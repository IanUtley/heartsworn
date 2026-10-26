"""Target counts and random picks for the interpreter (gap area A: chosen, player and random targets).

A target's count is its m_MaxTargetCount field: unset (no limit), a constant, or an ability variable. A random
automatic target takes its cards from the battle's RNG, the same draw the live resolver makes, so both paths
pick the same cards from the same random sequence.
"""
from __future__ import annotations

from typing import Any


def target_count(field: Any, variables: dict[str, int]) -> int:
    """A target's count: 0 when the field is unset (no limit, C# int.MaxValue), a constant, or the bound value of an
    ability variable (0 while that variable is unbound)."""
    if field is None:
        return 0
    kind = str(getattr(field, "type_name", "") or "").rsplit(".", 1)[-1]
    if kind == "TargetConstant":
        return max(0, int(field.m_Value or 0))
    if kind == "TargetVariable":
        return max(0, int(variables.get(str(field.m_AbilityVariableName or ""), 0) or 0))
    raise NotImplementedError(f"target count {kind or 'untyped'}")


def capped(candidates: list[int], count: int) -> list[int]:
    """The first ``count`` candidates of an automatic target (C# GetAutoTargets truncates to the maximum). A count of
    0 is no limit."""
    return candidates[:count] if count > 0 else candidates


def random_picks(candidates: list[int], count: int, bstate: dict[str, Any]) -> list[int]:
    """The cards of a random automatic target: up to ``count`` of the candidates, drawn with the session RNG as the
    live resolver draws them (AbilityTargetTemplate.FilterRandomTargets)."""
    from rules_port import resolution
    return [int(uid) for uid in resolution._random_target_sample(candidates, count, bstate)]
