"""Interpreter leaves for the TAC, event-firing and bookkeeping effects (gap area N).

``FireEventEffectTemplate`` and ``TACAbilityEffectTemplate`` run through the native RulesPort leaves
(``rules_port.effects.dispatch``), so the interpreter does what the resolver does: the same event is fired and the
same TAC operation runs. The resolver calls a card-scoped leaf once per target, and once with no target when there
is none. This module does the same, and restores the target aliases the leaves read once it is done.
"""

from __future__ import annotations

from typing import Any

# The effect templates this module runs through the native leaves. The bookkeeping ones remember a name, a keyword's
# powers or a registered trigger on the battle state, and the keyword one activates the target's triggered ability.
NATIVE_LEAF_EFFECTS = frozenset({
    "FireEventEffectTemplate", "TACAbilityEffectTemplate",
    "StoreNameAbilityEffectTemplate", "RememberKeywordPowersEffectTemplate",
    "RegisterTriggerAbilityEffectTemplate", "ActivateTriggeredAbilityEffectTemplate",
    # Copies the played spell and resolves the copy's authored abilities (rules_port.effects.create_and_cast_spell).
    "CreateAndCastSpellAbilityEffectTemplate",
    # Presentation only: pushes the client animation for a talent or power (rules_port.effects._animation_trigger).
    "AnimationTriggerEffectTemplate",
    # Records one typed entry in a named list of the ability (a shard pick's ChosenShards; rules_port.effects).
    "StoreListAttrAbilityEffectTemplate",
    # Loads the authored deck template for the controller and shuffles it (rules_port.deck_effects.load_player_deck).
    "LoadPlayerDeckAbilityEffectTemplate",
    # Queues a copy of the activated ability (the one in battle state's card_activated_item), rules_port.effects._copy_ability.
    "CopyAbilityEffectTemplate",
    # Offers the choice cards for an AI (chosen at once) or a player (the interpreter pauses for the player's answer,
    # see rules_port.ir_exec._pause_for_double_choice). rules_port.choice_effects.double_choice.
    "DoubleChoiceAbilityEffectTemplate",
    # Finishes a card's move into the warzone (the card's entry bookkeeping; rules_port.effects dispatch).
    "FinishMovingCardToWarzoneEffectTemplate",
    # The card-play template's two effects: play the card (the native play machinery queues it on the chain) and finish
    # its resolution (rules_port.effects.play_card, and the finish leaf).
    "BuiltInPlayCardAbilityEffectTemplate", "FinishResolvingCardAbilityEffectTemplate",
})

# The TAC operations the native leaf (EffectContext.tac) implements. A TAC operation outside this set is refused by
# coverage, so its ability is never half-run.
TAC_FUNCTIONS = frozenset({
    "AppendToList", "CycleCardArt", "DepleteResources", "Escalate", "ForgetAllCards",
    "GainTargetsThresholds", "GainTargetsProvidedThresholds", "MoveInDeck", "RemoveFromStoredTargetsAbility",
    "ReplaceCardInCollection", "RevokeBaseAbilities", "ShiftAbility", "Tame",
})

# The battle-state aliases a target is published under for the leaves (the resolver sets and clears the same keys).
_TARGET_KEYS = ("resolving_target_uid", "player_mod_target", "player_spell_target", "grant_target")


def tac_function_of(effect: Any) -> str:
    """The TAC operation a TACAbilityEffectTemplate names, read from its serialized TAC (or "" when it has none)."""
    from rules_port.tac import tac_function
    serialized = effect.get("m_SerializedTAC") if isinstance(effect, dict) else None
    if isinstance(serialized, dict):
        serialized = serialized.get("data")
    return tac_function(serialized or "")


def run_native_leaf(context: Any, effect_type: str, effect_raw: dict[str, Any], targets: list[int]) -> None:
    """Run one effect template through its native leaf, once per target (or once with no target)."""
    from rules_port.effects import dispatch
    bstate = context.bstate
    previous = {key: bstate.get(key) for key in _TARGET_KEYS}
    try:
        for target in targets or [None]:
            if target is None:
                for key in _TARGET_KEYS:
                    bstate.pop(key, None)
                child = context.nested_effect_context(effect_raw, "", ())
            else:
                for key in _TARGET_KEYS:
                    bstate[key] = int(target)
                child = context.nested_effect_context(effect_raw, "", (int(target),))
            result = dispatch(effect_type, child)
            if result is None:
                raise RuntimeError(f"no native leaf for {effect_type}")
    finally:
        for key, value in previous.items():
            if value is None:
                bstate.pop(key, None)
            else:
                bstate[key] = value


def coverage_of(kind: str, effect: dict[str, Any]) -> list[str]:
    """The constructs of one of these effect templates (``kind``, its short class name) the interpreter does not run:
    empty when it runs all of them."""
    if kind == "TACAbilityEffectTemplate":
        function = tac_function_of(effect)
        if function not in TAC_FUNCTIONS:
            return [f"TAC {function or 'untyped'}"]
    return []

