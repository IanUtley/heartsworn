"""The interpreter's play-for-free effect: C# PlayCardAbilityEffectTemplate.

Each target is played for free through the live play leaf (rules_port.context play_card), with the target passed
explicitly: the leaf reads its target from the battle state, so that is set for the one call and restored after it.
A free play queues the card on the chain, exactly as the live path does, so the chain resolves it later.
"""

from __future__ import annotations

from typing import Any


def play_for_free(context: Any, targets: list[int], effect_raw: dict[str, Any]) -> None:
    """Play each target card for free, in order, through the live play leaf."""
    bstate = context.bstate
    previous_template = context.effect_template_override
    previous_target = bstate.get("resolving_target_uid")
    context.effect_template_override = effect_raw
    try:
        for target in targets:
            bstate["resolving_target_uid"] = int(target)
            context.play_card()
    finally:
        context.effect_template_override = previous_template
        if previous_target is None:
            bstate.pop("resolving_target_uid", None)
        else:
            bstate["resolving_target_uid"] = previous_target
