"""The interpreter's counter-spell effect: C# CounterSpellAbilityEffectTemplate.

Each target is countered through the live counter leaf (rules_port.context counter_spell). The leaf reads its target
from the battle state, so the target is set for the one call and restored after it. The leaf itself refuses a card
that is no longer on the chain, or one that cannot be interrupted.
"""

from __future__ import annotations

from typing import Any


def counter_targets(context: Any, targets: list[int], effect_raw: dict[str, Any]) -> None:
    """Counter each target card that is still on the chain, in order, through the live counter leaf."""
    bstate = context.bstate
    previous_template = context.effect_template_override
    previous_target = bstate.get("resolving_target_uid")
    context.effect_template_override = effect_raw
    try:
        for target in targets:
            bstate["resolving_target_uid"] = int(target)
            context.counter_spell()
    finally:
        context.effect_template_override = previous_template
        if previous_target is None:
            bstate.pop("resolving_target_uid", None)
        else:
            bstate["resolving_target_uid"] = previous_target
