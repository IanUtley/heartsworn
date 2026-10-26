"""Costs and optional abilities for the interpreter (rules_port/ir_exec.py).

Paying an activation's cost happens before the ability reaches the chain: the numeric cost (resources, charge points,
spell points, life and counter costs) is planned and taken in services/tournament_game.py, so the interpreter runs
only the effects that are left. An optional ability is accepted or declined by the player's opt-in before it is
resolved, and a declined one never reaches the interpreter.

Two costs do reach the interpreter and stay refused. A variable activation cost's X value is not passed to it. A cost
that names cards to sacrifice, discard or exhaust has its selection in the native cost_target_map
(rules_port/resolution.py), which the effects may count ("for each troop exhausted this way"). Both abilities stay on
the native path.
"""

from __future__ import annotations

from typing import Any

# Costs paid before the ability is put on the chain, so resolution never sees them.
PAID_BEFORE_CHAIN = ("m_ActivationCost", "m_ChargePointCost", "m_LifeCost", "m_SpellPointCost", "m_CounterCosts")

# Cost fields that name the cards the player selects to pay with. A ResourceId with the zero GUID names nothing.
COST_TARGET_FIELDS = ("m_SacrificeTarget", "m_DiscardTarget", "m_DiscardTargets", "m_ExhaustTarget", "m_ExhaustTargets")

_NO_GUID = "00000000-0000-0000-0000-000000000000"


def _is_set(value: Any) -> bool:
    """Whether a cost field holds anything: a number, a non-empty list, or a ResourceId naming a card."""
    if isinstance(value, dict):
        return str(value.get("m_Guid") or _NO_GUID).lower() != _NO_GUID
    if isinstance(value, list):
        return any(_is_set(item) for item in value)
    return value not in (None, 0, "", False, "0")


def unsupported_costs(raw: dict[str, Any]) -> list[str]:
    """The cost constructs of an AbilityTemplate that the interpreter cannot resolve (empty when it can)."""
    missing: list[str] = []
    if _is_set(raw.get("m_VariableActivationCost")):
        missing.append("cost or optional (m_VariableActivationCost)")
    for field_name in COST_TARGET_FIELDS:
        if _is_set(raw.get(field_name)):
            missing.append(f"cost or optional ({field_name})")
    return missing
