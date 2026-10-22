"""Runtime policy agent.

Opt in with ``HEXAI_MODEL=/path/to/model.json``.  The agent only reranks the
main-phase candidate list; every other decision stays on the existing heuristic,
which keeps the blast radius small while a model is being trained.

Knobs (environment):
    HEXAI_MODEL          model path; unset disables the agent
    HEXAI_TEMPERATURE    softmax temperature for sampling (default 1.0)
    HEXAI_EXPLORE        probability of a uniformly random candidate (default 0)
    HEXAI_HEURISTIC_MIX  weight of the normalised evaluator score added to the
                         network logit (default 0.35); 0 = pure network
    HEXAI_SAMPLE         1 = sample from the distribution, 0 = argmax
"""

from __future__ import annotations

import os
import random
import threading

from . import candidates as candidate_mod
from . import features
from . import model as model_mod

_LOCK = threading.Lock()
_AGENT = None


def _float_env(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


class Agent:
    def __init__(self, model, *, heuristic_mix=0.35, temperature=1.0,
                 explore=0.0, sample=False, rng=None):
        self.model = model
        self.heuristic_mix = float(heuristic_mix)
        self.temperature = float(temperature)
        self.explore = max(0.0, min(1.0, float(explore)))
        self.sample = bool(sample)
        self.rng = rng or random.Random()

    def rank(self, feature_ids, candidates):
        state_vec = self.model.state_vector(feature_ids)
        logits = self.model.action_logits(state_vec, candidates)
        if self.heuristic_mix > 0 and candidates:
            priors = candidate_mod.normalise_scores(candidates)
            logits = [logit + self.heuristic_mix * prior * 4.0
                      for logit, prior in zip(logits, priors)]
        return logits, state_vec

    def choose(self, feature_ids, candidates):
        if not candidates:
            return None
        if self.explore > 0 and self.rng.random() < self.explore:
            return self.rng.choice(candidates)
        logits, _ = self.rank(feature_ids, candidates)
        probs = self.model.probabilities(logits, self.temperature)
        if self.sample:
            roll = self.rng.random()
            total = 0.0
            for candidate, prob in zip(candidates, probs):
                total += prob
                if roll <= total:
                    return candidate
            return candidates[-1]
        best = max(range(len(probs)), key=lambda index: probs[index])
        return candidates[best]

    def value(self, feature_ids):
        return self.model.score_state(feature_ids)

    # -- integration -------------------------------------------------------
    def choose_main(self, evaluator, state, seat_pid, opponent_pid,
                    greedy_decision, *, pre_combat=True, session=None,
                    candidates=None):
        """Return a replacement decision dict, or None to keep the heuristic."""
        if candidates is None:
            candidates = candidate_mod.main_phase_candidates(
                evaluator, pre_combat=pre_combat,
                greedy_decision=greedy_decision)
        if len(candidates) <= 1:
            return None
        feature_ids = features.encode_state(
            evaluator, state, seat_pid, opponent_pid, session=session)
        chosen = self.choose(feature_ids, candidates)
        if chosen is None or chosen.get("kind") == "keep":
            return None
        decision = candidate_mod.candidate_to_decision(chosen)
        greedy = candidate_mod.greedy_key(greedy_decision)
        if decision["kind"] == "pass" and greedy == "pass":
            return None
        if decision["kind"] == "card" and greedy == chosen.get("key"):
            return None
        return decision


def get_agent():
    """Return the configured agent, or None when the model is absent."""
    global _AGENT
    path = os.environ.get("HEXAI_MODEL")
    if not path:
        return None
    with _LOCK:
        resolved = os.path.abspath(path)
        if _AGENT is not None and _AGENT.model.meta.get("_path") == resolved:
            return _AGENT
        model = model_mod.load_cached(path)
        if model is None:
            return None
        meta = dict(model.meta)
        meta["_path"] = resolved
        model.meta = meta
        _AGENT = Agent(
            model,
            heuristic_mix=_float_env("HEXAI_HEURISTIC_MIX", 0.35),
            temperature=_float_env("HEXAI_TEMPERATURE", 1.0),
            explore=_float_env("HEXAI_EXPLORE", 0.0),
            sample=os.environ.get("HEXAI_SAMPLE", "") == "1")
        return _AGENT


def reset():
    """Drop the cached agent (tests and model hot-swap)."""
    global _AGENT
    with _LOCK:
        _AGENT = None
