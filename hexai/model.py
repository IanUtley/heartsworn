"""Runtime policy/value model (pure Python, no numpy).

The model is a factorised sparse network:

    s = mean(embedding[f] for f in state features)
    a = mean(embedding[k] for k in action keys)
    logit(action | state) = s . a / sqrt(dim) + bias[action]
    value(state)          = tanh(v_w . s + v_b)

Mean pooling over a sparse feature bag matches MageZero's state encoder, and a
bilinear action score lets one model rank a variable-size candidate list (the
deck does not need a fixed action vocabulary).  Weights are stored as JSON so
the server process does not need numpy; training lives in ``hexai.train``.
"""

from __future__ import annotations

import json
import math
import os
import threading

_FORMAT = 1
_CACHE: dict[tuple[str, float], "Model"] = {}
_CACHE_LOCK = threading.Lock()


def _mean(rows, dim):
    if not rows:
        return [0.0] * dim
    total = [0.0] * dim
    for row in rows:
        for index, value in enumerate(row):
            total[index] += value
    scale = 1.0 / len(rows)
    return [value * scale for value in total]


class Model:
    def __init__(self, *, dim, embedding, value_w, value_b=0.0,
                 action_bias=None, meta=None):
        self.dim = int(dim)
        self.embedding = {int(key): list(vec) for key, vec in
                          (embedding or {}).items()}
        self.value_w = list(value_w)
        self.value_b = float(value_b or 0.0)
        self.action_bias = {str(key): float(value) for key, value in
                            (action_bias or {}).items()}
        self.meta = dict(meta or {})

    # -- tensors -----------------------------------------------------------
    def feature_row(self, feature_id):
        return self.embedding.get(int(feature_id))

    def state_vector(self, feature_ids):
        rows = [self.embedding[int(fid)] for fid in feature_ids
                if int(fid) in self.embedding]
        return _mean(rows, self.dim)

    def action_vector(self, action_ids):
        rows = [self.embedding[int(fid)] for fid in action_ids
                if int(fid) in self.embedding]
        return _mean(rows, self.dim)

    # -- inference ---------------------------------------------------------
    def action_logits(self, state_vec, candidates):
        scale = 1.0 / math.sqrt(max(1, self.dim))
        out = []
        for candidate in candidates:
            action_vec = candidate.get("_vector")
            if action_vec is None:
                action_vec = self.action_vector(
                    candidate.get("action_ids") or ())
                candidate["_vector"] = action_vec
            score = sum(a * b for a, b in zip(state_vec, action_vec)) * scale
            score += self.action_bias.get(str(candidate.get("key")), 0.0)
            out.append(score)
        return out

    def value(self, state_vec):
        raw = sum(w * s for w, s in zip(self.value_w, state_vec))
        raw += self.value_b
        return math.tanh(raw)

    def probabilities(self, logits, temperature=1.0):
        if not logits:
            return []
        temperature = max(1e-3, float(temperature))
        top = max(logits)
        exps = [math.exp((value - top) / temperature) for value in logits]
        total = sum(exps) or 1.0
        return [value / total for value in exps]

    def choose(self, feature_ids, candidates, *, temperature=1.0):
        """Return ``(index, probabilities, state_value)`` for a candidate list."""
        state_vec = self.state_vector(feature_ids)
        logits = self.action_logits(state_vec, candidates)
        probs = self.probabilities(logits, temperature)
        if not probs:
            return -1, [], self.value(state_vec)
        best = max(range(len(probs)), key=lambda index: probs[index])
        return best, probs, self.value(state_vec)

    def score_state(self, feature_ids):
        return self.value(self.state_vector(feature_ids))

    # -- persistence -------------------------------------------------------
    def to_dict(self):
        return {
            "format": _FORMAT,
            "dim": self.dim,
            "embedding": {str(key): [round(value, 7) for value in vec]
                          for key, vec in self.embedding.items()},
            "value_w": [round(value, 7) for value in self.value_w],
            "value_b": round(self.value_b, 7),
            "action_bias": {key: round(value, 7)
                            for key, value in self.action_bias.items()},
            "meta": self.meta,
        }

    def save(self, path):
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, separators=(",", ":"))
        os.replace(tmp, path)

    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if int(data.get("format", 0)) != _FORMAT:
            raise ValueError(f"unsupported HexAI model format: {path}")
        return cls(
            dim=data["dim"],
            embedding=data.get("embedding") or {},
            value_w=data.get("value_w") or [],
            value_b=data.get("value_b", 0.0),
            action_bias=data.get("action_bias") or {},
            meta=data.get("meta") or {})

    @classmethod
    def from_arrays(cls, *, dim, embedding, value_w, value_b=0.0,
                    action_bias=None, meta=None):
        return cls(dim=dim, embedding=embedding, value_w=value_w,
                   value_b=value_b, action_bias=action_bias, meta=meta)


def load_cached(path):
    """Load a model once per path+mtime (safe for a long-lived server)."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    key = (os.path.abspath(path), mtime)
    with _CACHE_LOCK:
        model = _CACHE.get(key)
        if model is None:
            model = Model.load(path)
            _CACHE.clear()
            _CACHE[key] = model
    return model
