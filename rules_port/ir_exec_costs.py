"""Costs and optional abilities for the interpreter (rules_port/ir_exec.py).

Paying an activation's cost happens before the ability reaches the chain: the numeric cost (resources, charge points,
spell points, life and counter costs) is planned and taken in services/tournament_game.py, so the interpreter runs
only the effects that are left. An optional ability is accepted or declined by the player's opt-in before it is
resolved, and a declined one never reaches the interpreter.

A cost that names cards to sacrifice, discard or exhaust is paid the same way, and its cards reach the effects only
through the ability's list variables ("for each troop exhausted this way", the ExhaustedCards list). Those variables are
not in the interpreter's supported set, so an ability that reads them is refused by its variables, not here.

A variable activation cost stays refused: its X value is not passed to the interpreter, and the effects read it through
AbilityPropertyVariable (which is also refused), or through a card filter the interpreter does not model.
"""

from __future__ import annotations

from typing import Any

# Costs paid before the ability is put on the chain, so resolution never sees them.
PAID_BEFORE_CHAIN = ("m_ActivationCost", "m_ChargePointCost", "m_LifeCost", "m_SpellPointCost", "m_CounterCosts")

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
    return missing
