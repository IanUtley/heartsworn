"""Legal-action enumeration shared by capture, training and the runtime agent.

Only the main phase has a full candidate list today.  Combat, champion powers
and triggered choices still flow through the existing heuristic; they are
captured as (state, chosen action, outcome) samples so the value head can learn
from them and a later policy phase can enumerate them.

A candidate is a plain dict so it can be serialised into a capture record or
executed by ``pvp_autoplay``:

    {"kind": "card", "card": CardInfo, "target_uids": [uid, ...],
     "x_cost": 0, "score": float, "key": "card:<template>",
     "action_ids": [int, ...]}

``kind == "keep"`` means "retain the heuristic decision" (champion power,
warzone ability, tunnel, resource sink or pass-as-decided).  The agent treats
it as the safe fallback.
"""

from __future__ import annotations

import math

from . import features


def _uid_templates(evaluator) -> dict[int, str]:
    out = {}
    for collection in (getattr(evaluator, "hand", ()),
                       getattr(evaluator, "ai_warzone", ()),
                       getattr(evaluator, "player_warzone", ())):
        for card in collection or ():
            try:
                out[int(card.card_uid)] = str(card.template_guid or "").lower()
            except (AttributeError, TypeError, ValueError):
                continue
    return out


def _target_templates(target_uids, uid_templates) -> list[str]:
    return [uid_templates[int(uid)] for uid in target_uids or ()
            if int(uid) in uid_templates]


def _main_candidate(evaluator, card, uid_templates, *, score) -> dict:
    target_uids = []
    try:
        target_uids = [int(uid) for uid in
                       evaluator.choose_action_targets(card) or ()]
    except Exception:
        target_uids = []
    if not target_uids:
        try:
            single = evaluator.choose_action_target(card)
            if single is not None:
                target_uids = [int(single)]
        except Exception:
            target_uids = []
    try:
        x_cost = int(evaluator.preferred_x_cost(card) or 0)
    except Exception:
        x_cost = 0
    template = str(getattr(card, "template_guid", "") or "").lower()
    action_ids = features.encode_action(
        "card", card, targets=target_uids,
        target_templates=_target_templates(target_uids, uid_templates))
    return {
        "kind": "card",
        "card": card,
        "target_uids": target_uids,
        "x_cost": x_cost,
        "score": float(score),
        "key": f"card:{template}",
        "action_ids": action_ids,
    }


def main_phase_candidates(evaluator, *, pre_combat=True,
                          greedy_decision=None) -> list[dict]:
    """Return the heuristic's legal main-phase options for one seat.

    The list always contains a fallback entry.  ``greedy_decision`` is the
    normal ``ai_choose_main_phase_card`` result; when it is a non-card action
    the fallback is a ``keep`` candidate so reranking never removes an
    authored champion/warzone option.
    """
    uid_templates = _uid_templates(evaluator)
    candidates: list[dict] = []
    by_key: dict[str, dict] = {}

    for card in list(getattr(evaluator, "hand", ()) or ()):
        try:
            if card.is_resource():
                # Resources are played by the dedicated resource step before
                # this decision; they are never a main-phase card choice.
                continue
            if evaluator.is_playable(card) != "True":
                continue
            if not evaluator.is_board_builder(card, pre_combat):
                continue
        except Exception:
            continue
        candidate = _main_candidate(
            evaluator, card, uid_templates,
            score=evaluator.get_card_value(card))
        existing = by_key.get(candidate["key"])
        if existing is not None:
            # Keep one copy per template; the hand's duplicates are
            # interchangeable for policy purposes.
            if candidate["score"] > existing["score"]:
                existing.update(candidate)
            continue
        by_key[candidate["key"]] = candidate
        candidates.append(candidate)

    greedy_kind = (greedy_decision or {}).get("kind")
    if greedy_kind == "card" and greedy_decision.get("card") is not None:
        card = greedy_decision["card"]
        key = f"card:{str(getattr(card, 'template_guid', '') or '').lower()}"
        if key not in by_key:
            candidate = _main_candidate(
                evaluator, card, uid_templates,
                score=evaluator.get_card_value(card))
            by_key[key] = candidate
            candidates.append(candidate)

    if greedy_kind in (None, "pass"):
        candidates.append({
            "kind": "pass", "score": 0.0, "key": "pass",
            "action_ids": features.encode_action("pass"),
        })
    elif greedy_kind != "card":
        candidates.append({
            "kind": "keep",
            "score": max((c["score"] for c in candidates), default=0.0) + 1.0,
            "key": f"keep:{greedy_kind}",
            "action_ids": features.encode_action(
                "keep", extra_keys=(str(greedy_kind),)),
        })
    return candidates


def candidate_record(candidate) -> dict:
    """Serialisable subset of a candidate (no CardInfo object)."""
    card = candidate.get("card")
    return {
        "key": candidate["key"],
        "kind": candidate["kind"],
        "score": round(float(candidate.get("score", 0.0)), 4),
        "action_ids": list(candidate.get("action_ids") or ()),
        "template_guid": (str(getattr(card, "template_guid", "") or "").lower()
                          if card is not None else None),
        "target_uids": list(candidate.get("target_uids") or ()),
        "x_cost": int(candidate.get("x_cost", 0) or 0),
    }


def greedy_key(decision) -> str:
    """Stable key for the heuristic's chosen action."""
    kind = (decision or {}).get("kind") or "pass"
    if kind == "card" and decision.get("card") is not None:
        return f"card:{str(getattr(decision['card'], 'template_guid', '') or '').lower()}"
    if kind == "champion_power":
        power = decision.get("power") or {}
        return f"power:{power.get('ability_guid') or ''}"
    if kind in ("warzone_ability", "resource_sink"):
        ability = decision.get("ability") or {}
        return f"ability:{ability.get('ability_guid') or ''}"
    if kind == "tunnel":
        ability = decision.get("ability") or {}
        return f"tunnel:{ability.get('ability_guid') or ''}"
    return kind


def candidate_to_decision(candidate) -> dict:
    """Turn a main-phase candidate into the structure the game loop executes."""
    if candidate["kind"] == "pass":
        return {"kind": "pass", "reason": "hexai_pass"}
    if candidate["kind"] == "keep":
        return {"kind": "keep", "reason": "hexai_keep_greedy"}
    card = candidate["card"]
    target_uids = [int(uid) for uid in candidate.get("target_uids") or ()]
    return {
        "kind": "card",
        "card": card,
        "target_uid": target_uids[0] if target_uids else None,
        "target_uids": target_uids,
        "x_cost": int(candidate.get("x_cost", 0) or 0),
        "reason": "hexai_model",
    }


def normalise_scores(candidates, temperature=1.0) -> list[float]:
    """Softmax over heuristic candidate scores, for supervised priors."""
    scores = [float(c.get("score", 0.0)) for c in candidates]
    if not scores:
        return []
    temperature = max(1e-3, float(temperature))
    top = max(scores)
    exps = [math.exp((score - top) / temperature) for score in scores]
    total = sum(exps) or 1.0
    return [value / total for value in exps]
