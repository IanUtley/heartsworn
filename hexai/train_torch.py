"""Optional PyTorch trainer (CUDA-capable) for hexai samples.

The numpy trainer in ``hexai.train`` is the default because it needs no extra
install and is fast enough for a few thousand games.  This backend exists for
two cases:

* a large replay buffer (the 3080/3070 is then idle-time-free because batches
  are dense tensors, not a per-sample Python loop);
* growing the model later (a transformer or wider embedding) without rewriting
  the capture format.

It trains the same factorised model and exports the same ``model.json``.  Use::

    python3 -m hexai.train --backend torch --device cuda ...

Batches are padded once for the whole dataset: state ids ``(N,S)`` and
candidate action ids ``(N,C,A)`` with masks, so each step is three kernels
(embedding, masked mean, einsum) instead of a Python loop.  The same padded
tensors are sliced per step, which also removes the repeated host-to-device copy
that made a naive CUDA loop slower than numpy on small models.
"""

from __future__ import annotations

import math

import numpy as np


def _require_torch():
    try:
        import torch  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depends on the venv
        raise SystemExit(
            "torch is not installed; install it in a training venv or use "
            "--backend numpy") from exc
    return __import__("torch")


def _make_batch(samples, torch, device="cpu"):
    size = len(samples)
    max_state = max(1, max(len(s["state"]) for s in samples))
    max_cand = max(1, max(len(s["actions"]) for s in samples))
    max_act = max(1, max((len(ids) for s in samples for ids in s["actions"]),
                         default=1))
    state_idx = np.zeros((size, max_state), dtype=np.int64)
    state_mask = np.zeros((size, max_state), dtype=np.float32)
    action_idx = np.zeros((size, max_cand, max_act), dtype=np.int64)
    action_mask = np.zeros((size, max_cand, max_act), dtype=np.float32)
    cand_mask = np.zeros((size, max_cand), dtype=bool)
    target = np.zeros((size, max_cand), dtype=np.float32)
    weight = np.ones((size,), dtype=np.float32)
    value = np.zeros((size,), dtype=np.float32)
    finished = np.zeros((size,), dtype=np.float32)
    for row, sample in enumerate(samples):
        for col, fid in enumerate(sample["state"]):
            state_idx[row, col] = int(fid)
            state_mask[row, col] = 1.0
        for c_index, ids in enumerate(sample["actions"]):
            cand_mask[row, c_index] = True
            for a_index, fid in enumerate(ids):
                action_idx[row, c_index, a_index] = int(fid)
                action_mask[row, c_index, a_index] = 1.0
            target[row, c_index] = float(sample["policy"][c_index])
        weight[row] = float(sample["weight"])
        value[row] = float(sample["value"])
        finished[row] = 1.0 if sample["finished"] else 0.0

    def tensor(array, dtype=None):
        if dtype is None:
            dtype = torch.bool if array.dtype == bool else torch.float32
        return torch.as_tensor(array, dtype=dtype, device=device)

    return {
        "state_idx": tensor(state_idx, torch.long),
        "state_mask": tensor(state_mask),
        "action_idx": tensor(action_idx, torch.long),
        "action_mask": tensor(action_mask),
        "cand_mask": tensor(cand_mask),
        "target": tensor(target),
        "weight": tensor(weight),
        "value": tensor(value),
        "finished": tensor(finished),
    }


def _move_batch(batch, device):
    if device.type == "cpu":
        return batch
    return {key: tensor.to(device) for key, tensor in batch.items()}


def _slice_batch(batch, index):
    return {key: tensor[index] for key, tensor in batch.items()}


def _forward(model, batch, torch):
    emb = model["embedding"]
    state = emb(batch["state_idx"]) * batch["state_mask"].unsqueeze(-1)
    state = state.sum(1) / batch["state_mask"].sum(
        1, keepdim=True).clamp(min=1.0)
    actions = emb(batch["action_idx"]) * batch["action_mask"].unsqueeze(-1)
    actions = actions.sum(2) / batch["action_mask"].sum(
        2, keepdim=True).clamp(min=1.0)
    scale = 1.0 / math.sqrt(model["dim"])
    logits = (state.unsqueeze(1) * actions).sum(-1) * scale
    logits = logits.masked_fill(~batch["cand_mask"], -1e9)
    value = torch.tanh(state @ model["value_w"] + model["value_b"])
    return logits, value


def _loss(model, batch, torch, value_weight):
    logits, value = _forward(model, batch, torch)
    logp = torch.log_softmax(logits, dim=1)
    policy = -(batch["target"] * logp).sum(dim=1)
    policy = (policy * batch["weight"]).mean()
    active = batch["finished"].sum().clamp(min=1.0)
    value_loss = (((value - batch["value"]) ** 2) * batch["finished"]).sum()
    value_loss = value_loss / active
    return policy + value_weight * value_loss, policy, value_loss


def _evaluate(model, batch, torch, chunk=1024):
    if batch is None:
        return {}
    total = int(batch["state_idx"].shape[0])
    if not total:
        return {}
    correct = 0
    value_error = 0.0
    value_count = 0
    with torch.no_grad():
        for start in range(0, total, chunk):
            index = torch.arange(
                start, min(start + chunk, total),
                device=batch["state_idx"].device)
            part = _slice_batch(batch, index)
            logits, value = _forward(model, part, torch)
            best = logits.argmax(dim=1)
            target = part["target"].argmax(dim=1)
            correct += int((best == target).sum().item())
            mask = part["finished"] > 0
            if int(mask.sum().item()):
                diff = (value - part["value"]) ** 2
                value_error += float(
                    (diff * part["finished"]).sum().item())
                value_count += int(mask.sum().item())
    return {
        "policy_accuracy": correct / max(1, total),
        "value_mse": value_error / max(1, value_count),
        "samples": total,
    }


def train(train_samples, test_samples, vocab, *, dim=64, epochs=8, batch=256,
          lr=2e-3, l2=1e-5, value_weight=0.5, device="auto", seed=7,
          quiet=False):
    """Train and return ``(embedding_rows, value_w, value_b, metrics)``.

    ``embedding_rows`` is a ``(len(vocab), dim)`` numpy array in the same row
    order as ``vocab`` so the caller can export the pure-Python model.
    """
    torch = _require_torch()
    if device in (None, "auto"):
        device = "cuda" if torch.cuda.is_available() else "cpu"
    device = torch.device(device)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise SystemExit("CUDA requested but torch.cuda.is_available() is False")

    torch.manual_seed(seed)
    index = {int(fid): row for row, fid in enumerate(vocab)}
    model = {
        "embedding": torch.nn.Embedding(len(vocab), dim).to(device),
        "value_w": torch.zeros(dim, device=device, requires_grad=True),
        "value_b": torch.zeros(1, device=device, requires_grad=True),
        "dim": dim,
    }
    with torch.no_grad():
        model["embedding"].weight.normal_(0.0, 1.0 / math.sqrt(dim))
    params = [model["embedding"].weight, model["value_w"], model["value_b"]]
    optimizer = torch.optim.Adam(params, lr=lr)

    # Map feature ids to embedding rows once; the model is trained on rows.
    for sample in train_samples:
        sample["state"] = [index[int(fid)] for fid in sample["state"]
                           if int(fid) in index]
        sample["actions"] = [[index[int(fid)] for fid in ids
                              if int(fid) in index]
                             for ids in sample["actions"]]
    for sample in test_samples:
        sample["state"] = [index[int(fid)] for fid in sample["state"]
                           if int(fid) in index]
        sample["actions"] = [[index[int(fid)] for fid in ids
                              if int(fid) in index]
                             for ids in sample["actions"]]
    train_samples = [sample for sample in train_samples if sample["state"]]
    test_samples = [sample for sample in test_samples if sample["state"]]

    train_batch = _move_batch(_make_batch(train_samples, torch), device)
    test_batch = None
    if test_samples:
        test_batch = _move_batch(_make_batch(test_samples, torch), device)

    total = len(train_samples)
    best = None
    best_state = None
    for epoch in range(1, epochs + 1):
        order = torch.randperm(total, device=device)
        totals = [0.0, 0.0, 0]
        for start in range(0, total, batch):
            index = order[start:start + batch]
            batch_t = _slice_batch(train_batch, index)
            loss, policy, value_loss = _loss(
                model, batch_t, torch, value_weight)
            if l2:
                loss = loss + l2 * (model["embedding"].weight ** 2).mean()
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(params, 5.0)
            optimizer.step()
            totals[0] += float(policy.item())
            totals[1] += float(value_loss.item())
            totals[2] += 1
        metrics = _evaluate(model, test_batch, torch)
        if not quiet:
            count = max(1, totals[2])
            print(f"epoch {epoch}: policy={totals[0] / count:.4f} "
                  f"value={totals[1] / count:.4f} val={metrics} "
                  f"device={device.type}")
        score = metrics.get("value_mse", 1.0) + (
            1.0 - metrics.get("policy_accuracy", 0.0))
        if best is None or score < best:
            best = score
            best_state = (
                model["embedding"].weight.detach().clone(),
                model["value_w"].detach().clone(),
                model["value_b"].detach().clone())
    if best_state is not None:
        model["embedding"].weight.data.copy_(best_state[0])
        model["value_w"].data.copy_(best_state[1])
        model["value_b"].data.copy_(best_state[2])

    rows = model["embedding"].weight.detach().cpu().numpy()
    value_w = model["value_w"].detach().cpu().numpy()
    value_b = float(model["value_b"].detach().cpu().item())
    metrics = _evaluate(model, test_batch, torch)
    return rows, value_w, value_b, metrics
