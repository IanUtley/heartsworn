"""Offline trainer for hexai capture shards.

    python3 -m hexai.train --data capture/ --out models/angel.json
    python3 -m hexai.train --data capture/ --out models/angel.json \
        --backend torch --device cuda

The default backend needs only numpy.  ``--backend torch`` uses the optional
``hexai.train_torch`` implementation, which batches every decision into dense
padded tensors and can run on CUDA (``--device auto`` picks the GPU when torch
sees one).  Both backends train the same factorised model and export the same
``model.json``.

The model is a factorised sparse policy/value network (see ``hexai.model``).
Training is deliberately small-scale: a few thousand games is enough to learn
deck-local priors, and the exported JSON runs in the server process without
numpy.

Policy targets:
    greedy   softmax over the heuristic candidate scores (imitation)
    chosen   one-hot on the action the heuristic actually took
    outcome  one-hot weighted by the game result (REINFORCE-style)

Value targets are the seat's final result (+1 win, -1 loss, 0 draw/unfinished),
so the value head learns to read the same features the policy uses.
"""

from __future__ import annotations

import argparse
import glob
import gzip
import json
import math
import os
import random
import sys

import numpy as np

from . import candidates as candidate_mod
from .model import Model


def _open_text(path):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "rt", encoding="utf-8")


def read_shards(paths):
    games: dict[str, dict] = {}
    for path in paths:
        try:
            handle = _open_text(path)
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                game_id = str(record.get("game"))
                game = games.setdefault(game_id, {
                    "id": game_id, "pids": [], "decks": {}, "decisions": [],
                    "winner": None, "turns": None, "health": {},
                })
                kind = record.get("type")
                if kind == "game":
                    game["pids"] = [int(pid) for pid in
                                    record.get("pids") or ()]
                    game["decks"] = record.get("decks") or {}
                    game["seed"] = record.get("seed")
                elif kind == "decision":
                    game["decisions"].append(record)
                elif kind == "end":
                    game["winner"] = record.get("winner")
                    game["turns"] = record.get("turns")
                    game["health"] = record.get("health") or {}
                    if record.get("pids"):
                        game["pids"] = [int(pid) for pid in record["pids"]]
    return games


def shard_paths(paths):
    out = []
    for path in paths:
        if os.path.isdir(path):
            out.extend(sorted(glob.glob(os.path.join(path, "*.jsonl"))))
            out.extend(sorted(glob.glob(os.path.join(path, "*.jsonl.gz"))))
        else:
            out.append(path)
    return out


def result_for(game, seat):
    winner = game.get("winner")
    if winner is None:
        return 0.0
    return 1.0 if int(winner) == int(seat) else -1.0


def _state_ids(record):
    seen = set()
    out = []
    for fid in record.get("features") or ():
        fid = int(fid)
        if fid not in seen:
            seen.add(fid)
            out.append(fid)
    return out


def build_samples(games, *, target="greedy", min_games=1):
    samples = []
    skipped = {"no_candidates": 0, "chosen_missing": 0, "unfinished": 0}
    if len(games) < max(1, int(min_games)):
        return [], skipped
    for game in games.values():
        if len(game["decisions"]) == 0:
            continue
        finished = game.get("winner") is not None
        if not finished:
            skipped["unfinished"] += 1
        for record in game["decisions"]:
            state_ids = _state_ids(record)
            cands = record.get("candidates") or ()
            if len(cands) < 2:
                skipped["no_candidates"] += 1
                continue
            chosen = str(record.get("chosen"))
            # "keep" candidates carry the heuristic action; match on key.
            keys = [str(c.get("key")) for c in cands]
            if chosen not in keys:
                skipped["chosen_missing"] += 1
                continue
            chosen_index = keys.index(chosen)
            seat = int(record.get("seat"))
            outcome = result_for(game, seat)
            action_ids = [list(map(int, c.get("action_ids") or ()))
                          for c in cands]
            scores = [float(c.get("score", 0.0)) for c in cands]
            if target == "greedy":
                if max(scores, default=0.0) <= 0.0:
                    policy = [0.0] * len(cands)
                    policy[chosen_index] = 1.0
                else:
                    policy = candidate_mod.normalise_scores(
                        [{"score": score} for score in scores])
                sample_weight = 1.0
            elif target == "outcome":
                policy = [0.0] * len(cands)
                policy[chosen_index] = 1.0
                sample_weight = outcome
            else:  # chosen
                policy = [0.0] * len(cands)
                policy[chosen_index] = 1.0
                sample_weight = 1.0
            if sample_weight == 0.0:
                continue
            samples.append({
                "game": game["id"],
                "state": state_ids,
                "actions": action_ids,
                "policy": policy,
                "weight": float(sample_weight),
                "value": outcome,
                "finished": finished,
                "kind": record.get("kind"),
            })
    return samples, skipped


def split_games(games, holdout=0.1, seed=1):
    ids = [gid for gid, game in games.items() if game["decisions"]]
    rng = random.Random(seed)
    rng.shuffle(ids)
    cut = max(1, int(len(ids) * holdout)) if len(ids) > 1 else 0
    test_ids = set(ids[:cut])
    train_ids = set(ids[cut:])
    return ({gid: games[gid] for gid in train_ids},
            {gid: games[gid] for gid in test_ids})


def build_vocab(samples):
    feature_ids = set()
    action_ids = set()
    for sample in samples:
        feature_ids.update(sample["state"])
        for ids in sample["actions"]:
            action_ids.update(ids)
    return sorted(feature_ids), sorted(action_ids)


class Trainer:
    def __init__(self, vocab, dim=64, seed=7):
        rng = np.random.default_rng(seed)
        self.vocab = np.asarray(vocab, dtype=np.int64)
        self.index = {int(fid): row for row, fid in enumerate(self.vocab)}
        self.dim = int(dim)
        scale = 1.0 / math.sqrt(self.dim)
        self.E = rng.normal(0.0, scale, size=(len(vocab), self.dim))
        self.value_w = np.zeros(self.dim, dtype=np.float64)
        self.value_b = 0.0

    def rows(self, ids):
        return [self.index[int(fid)] for fid in ids if int(fid) in self.index]

    def forward(self, sample):
        rows = self.rows(sample["state"])
        if not rows:
            return None
        state = self.E[rows].mean(axis=0)
        scale = 1.0 / math.sqrt(self.dim)
        action_mats = []
        logits = []
        for ids in sample["actions"]:
            action_rows = self.rows(ids)
            vec = (self.E[action_rows].mean(axis=0) if action_rows
                   else np.zeros(self.dim))
            action_mats.append((action_rows, vec))
            logits.append(float(state @ vec) * scale)
        logits = np.asarray(logits)
        probs = np.exp(logits - logits.max())
        probs /= probs.sum()
        value = math.tanh(float(state @ self.value_w) + self.value_b)
        return state, rows, action_mats, probs, value

    def step(self, batch, *, lr, l2, value_weight):
        grad_E = np.zeros_like(self.E)
        grad_w = np.zeros_like(self.value_w)
        grad_b = 0.0
        policy_loss = 0.0
        value_loss = 0.0
        count = 0
        scale = 1.0 / math.sqrt(self.dim)
        for sample in batch:
            out = self.forward(sample)
            if out is None:
                continue
            state, state_rows, action_mats, probs, value = out
            target = np.asarray(sample["policy"], dtype=np.float64)
            weight = float(sample["weight"])
            if target.shape != probs.shape:
                continue
            policy_loss += float(-np.sum(
                target * np.log(np.clip(probs, 1e-9, 1.0))))
            dlogits = (probs - target) * weight
            ds = np.zeros(self.dim)
            for (action_rows, vec), dlogit in zip(action_mats, dlogits):
                if dlogit == 0.0:
                    continue
                ds += dlogit * vec * scale
                if action_rows:
                    grad_E[action_rows] += (dlogit * state * scale
                                            / len(action_rows))
            if sample["finished"]:
                dv = (value - float(sample["value"]))
                value_loss += dv * dv
                dpre = dv * (1.0 - value * value) * value_weight
                grad_w += dpre * state
                grad_b += dpre
                ds += dpre * self.value_w
            if state_rows:
                grad_E[state_rows] += ds / len(state_rows)
            count += 1
        if count == 0:
            return None
        # L2 on touched rows only: keeps untouched (unseen) rows at zero.
        touched = np.any(grad_E != 0.0, axis=1)
        grad_E[touched] += l2 * self.E[touched]
        norm = math.sqrt(float((grad_E ** 2).sum() + (grad_w ** 2).sum() +
                               grad_b ** 2))
        if norm > 5.0:
            shrink = 5.0 / norm
            grad_E *= shrink
            grad_w *= shrink
            grad_b *= shrink
        return (policy_loss / count, value_loss / count,
                grad_E, grad_w, grad_b)


def _adam(params, grads, state, lr, beta1=0.9, beta2=0.999, eps=1e-8):
    step, m, v = state
    step += 1
    m = beta1 * m + (1 - beta1) * grads
    v = beta2 * v + (1 - beta2) * (grads * grads)
    m_hat = m / (1 - beta1 ** step)
    v_hat = v / (1 - beta2 ** step)
    params -= lr * m_hat / (np.sqrt(v_hat) + eps)
    return params, (step, m, v)


def _adam_scalar(param, grad, state, lr, beta1=0.9, beta2=0.999, eps=1e-8):
    step, m, v = state
    step += 1
    m = beta1 * m + (1 - beta1) * grad
    v = beta2 * v + (1 - beta2) * (grad * grad)
    m_hat = m / (1 - beta1 ** step)
    v_hat = v / (1 - beta2 ** step)
    param -= lr * m_hat / (math.sqrt(v_hat) + eps)
    return param, (step, m, v)


def evaluate(model, samples):
    if not samples:
        return {}
    correct = 0
    value_error = 0.0
    value_count = 0
    for sample in samples:
        rows = [model.index[int(fid)] for fid in sample["state"]
                if int(fid) in model.index]
        if not rows:
            continue
        state = model.E[rows].mean(axis=0)
        scale = 1.0 / math.sqrt(model.dim)
        logits = []
        for ids in sample["actions"]:
            rows_a = [model.index[int(fid)] for fid in ids
                      if int(fid) in model.index]
            vec = model.E[rows_a].mean(axis=0) if rows_a else np.zeros(model.dim)
            logits.append(float(state @ vec) * scale)
        best = int(np.argmax(logits))
        chosen = int(np.argmax(sample["policy"]))
        correct += int(best == chosen)
        if sample["finished"]:
            value = math.tanh(float(state @ model.value_w) + model.value_b)
            value_error += (value - sample["value"]) ** 2
            value_count += 1
    return {
        "policy_accuracy": correct / max(1, len(samples)),
        "value_mse": value_error / max(1, value_count),
        "samples": len(samples),
    }


def export_model(trainer, meta):
    embedding = {}
    for row, fid in enumerate(trainer.vocab):
        if np.any(trainer.E[row] != 0.0):
            embedding[int(fid)] = [float(value) for value in trainer.E[row]]
    return Model.from_arrays(
        dim=trainer.dim,
        embedding=embedding,
        value_w=[float(value) for value in trainer.value_w],
        value_b=float(trainer.value_b),
        meta=meta)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", nargs="+", required=True,
                        help="capture directory or shard paths")
    parser.add_argument("--out", required=True, help="model.json to write")
    parser.add_argument("--dim", type=int, default=64)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch", type=int, default=256)
    parser.add_argument("--lr", type=float, default=2e-3)
    parser.add_argument("--l2", type=float, default=1e-5)
    parser.add_argument("--value-weight", type=float, default=0.5)
    parser.add_argument("--target", choices=("greedy", "chosen", "outcome"),
                        default="greedy")
    parser.add_argument("--holdout", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--min-games", type=int, default=1)
    parser.add_argument("--backend", choices=("numpy", "torch"),
                        default="numpy",
                        help="numpy (no dependencies) or torch (optional, "
                             "CUDA-capable)")
    parser.add_argument("--device", default="auto",
                        help="torch device: auto, cuda, cuda:0, cpu "
                             "(ignored by the numpy backend)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    paths = shard_paths(args.data)
    if not paths:
        print(f"no capture shards under {args.data}", file=sys.stderr)
        return 2
    games = read_shards(paths)
    train_games, test_games = split_games(games, args.holdout, args.seed)
    train_samples, skipped = build_samples(
        train_games, target=args.target, min_games=args.min_games)
    test_samples, _ = build_samples(test_games, target=args.target,
                                    min_games=0)
    if not train_samples:
        print(f"no usable decisions (skipped={skipped})", file=sys.stderr)
        return 2

    feature_vocab, action_vocab = build_vocab(train_samples)
    # The state and action encoders share one embedding table, so the model's
    # row set is the union.  Using only ``feature_vocab`` would drop every
    # action id from the index and leave all action vectors at zero.
    vocab = sorted(set(feature_vocab) | set(action_vocab))
    if args.backend == "torch":
        from types import SimpleNamespace

        from . import train_torch

        embedding, value_w, value_b, metrics = train_torch.train(
            train_samples, test_samples, vocab, dim=args.dim,
            epochs=args.epochs, batch=args.batch, lr=args.lr, l2=args.l2,
            value_weight=args.value_weight, device=args.device,
            seed=args.seed, quiet=args.quiet)
        trainer = SimpleNamespace(
            vocab=np.asarray(vocab, dtype=np.int64),
            dim=int(args.dim),
            E=np.asarray(embedding, dtype=np.float64),
            value_w=np.asarray(value_w, dtype=np.float64),
            value_b=float(value_b),
        )
    else:
        trainer = Trainer(vocab, dim=args.dim, seed=args.seed)
        rng = random.Random(args.seed)

        adam_E = (0, np.zeros_like(trainer.E), np.zeros_like(trainer.E))
        adam_w = (0, np.zeros_like(trainer.value_w),
                  np.zeros_like(trainer.value_w))
        adam_b = (0, 0.0, 0.0)
        best = None
        best_state = None
        for epoch in range(1, args.epochs + 1):
            order = list(range(len(train_samples)))
            rng.shuffle(order)
            losses = []
            for start in range(0, len(order), args.batch):
                batch = [train_samples[index]
                         for index in order[start:start + args.batch]]
                result = trainer.step(batch, lr=args.lr, l2=args.l2,
                                      value_weight=args.value_weight)
                if result is None:
                    continue
                policy_loss, value_loss, grad_E, grad_w, grad_b = result
                trainer.E, adam_E = _adam(trainer.E, grad_E, adam_E, args.lr)
                trainer.value_w, adam_w = _adam(
                    trainer.value_w, grad_w, adam_w, args.lr)
                trainer.value_b, adam_b = _adam_scalar(
                    trainer.value_b, grad_b, adam_b, args.lr)
                losses.append((policy_loss, value_loss))
            metrics = evaluate(trainer, test_samples)
            policy_mean = sum(p[0] for p in losses) / max(1, len(losses))
            value_mean = sum(p[1] for p in losses) / max(1, len(losses))
            if not args.quiet:
                print(f"epoch {epoch}: policy={policy_mean:.4f} "
                      f"value={value_mean:.4f} val={metrics}")
            score = metrics.get("value_mse", 1.0) + (1.0 - metrics.get(
                "policy_accuracy", 0.0))
            if best is None or score < best:
                best = score
                best_state = (trainer.E.copy(), trainer.value_w.copy(),
                              trainer.value_b)
        if best_state is not None:
            trainer.E, trainer.value_w, trainer.value_b = best_state
        metrics = evaluate(trainer, test_samples)

    meta = {
        "created": int(__import__("time").time()),
        "target": args.target,
        "dim": args.dim,
        "games": len(games),
        "train_samples": len(train_samples),
        "test_samples": len(test_samples),
        "skipped": skipped,
        "metrics": metrics,
        "backend": args.backend,
        "source": [os.path.abspath(path) for path in paths],
    }
    model = export_model(trainer, meta)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    model.save(args.out)
    metrics_path = os.path.splitext(args.out)[0] + ".metrics.json"
    with open(metrics_path, "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2)
    if not args.quiet:
        print(f"wrote {args.out} ({len(model.embedding)} rows, "
              f"dim={model.dim}) and {metrics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
