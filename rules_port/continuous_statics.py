"""Continuous stat clauses for the interpreter.

A stat modifier whose clause lasts WhileCardInPlay is a continuous effect. The native static path
(``rules_port.static_rules``) projects it from its source card while that card is in play: the projection re-reads the
source's current ability list on every read, so a transform, revert, talent change or zone change turns the clause off
without any cleanup step. The interpreter therefore does not apply it.

It accepts the clause only when the scan can actually project it: the ability is an authored static (no trigger, not
player-activated, asked about on its own rather than as an invoked child), and the modifier's leaf is one the scan
resolves (``static_rules.projects_continuous_modifier``). Gating on the duration alone would let a leaf the scan
ignores pass as covered and vanish silently. A triggered ability with such a clause is a linked effect instead
(``rules_port.effect_lifetimes``), and stays refused here.
"""

from __future__ import annotations

import json

# The clause durations the native static path owns.
STATIC_OWNED_DURATIONS = frozenset({"WhileCardInPlay"})


def is_static_owned(duration: str) -> bool:
    """Whether a clause's duration is projected by the native static path rather than run by the interpreter."""
    return duration in STATIC_OWNED_DURATIONS


def _manual(raw) -> bool:
    """Whether the ability is player-activated (C# ``m_Manual``): the scan does not project those."""
    record = raw if isinstance(raw, dict) else None
    if record is None:
        try:
            record = json.loads(raw or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return True
    return bool(record.get("m_Manual"))


def covered_as_static(duration: str, trigger: str, modifier=None, raw="", *, ability_guid: str = "",
                      effect_guid: str = "", invoked: bool = False) -> bool:
    """Whether the interpreter accepts a clause as projected on read rather than run.

    ``invoked`` says the clause belongs to an ability this one invokes: the scan projects only the abilities authored
    on a card, so an invoked child's clause is a resolved effect with a linked lifetime, not an aura. ``ability_guid``
    and ``effect_guid`` let the projector answer from its own snapshot (``static_rules.projects_clause``).
    """
    if not is_static_owned(duration) or trigger or invoked or modifier is None:
        return False
    if _manual(raw):
        return False
    from rules_port.static_rules import projects_clause, projects_continuous_modifier
    if not projects_continuous_modifier(modifier, raw):
        return False
    if ability_guid or effect_guid:
        return projects_clause(ability_guid, effect_guid, duration)
    return True
