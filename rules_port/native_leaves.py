"""Native leaf table: Records effect template -> the leaf that runs it.

The resolver (``resolution.NativeEffectBackend``) and the IR interpreter
(``ir_event_effects.run_native_leaf``) both route a Records effect through
:func:`dispatch`. Every entry calls a rules_port module function directly with the
:class:`~rules_port.context.EffectContext` as ``ctx``; no leaf body lives behind an
``EffectContext`` method.  The bodies sit in ``leaves_*`` modules (zones,
players, transforms, variables, modifiers, abilities) and in the IR modules that
already own them.
"""

from __future__ import annotations

import rules_port.leaves_abilities as _leaves_abilities
import rules_port.leaves_players as _leaves_players
from typing import Any, Callable

from rules_port import ir_event_effects as _ir_events
from rules_port import ir_exec_resources as _ir_resources
from rules_port import ir_modifiers as _ir_modifiers
from rules_port import leaves_abilities as _leaves_abilities
from rules_port import leaves_modifiers as _leaves_modifiers
from rules_port import leaves_players as _leaves_players
from rules_port import leaves_transforms as _leaves_transforms
from rules_port import leaves_variables as _leaves_variables
from rules_port import leaves_zones as _leaves_zones

# Effect templates whose C# Apply(AbilityEffectInstance) runs once for the
# mapping and handles any target set itself.  The resolver's per-target loop
# must not invoke them once per member card.
SELF_TARGETED_EFFECTS = frozenset({
    # "Look at the top five cards of your deck": re-running the reveal for
    # each of the five resolved cards revealed the same five cards five times
    # and queued one client Coverflow per copy.
    "RevealCardsAbilityEffectTemplate",
    # C# overrides Apply(AbilityEffectInstance) and applies its nested child
    # to the whole target instance once. Calling it once per target multiplies
    # the authored loop count by the target count.
    "RepeatingAbilityEffectTemplate",
    # RandomizeVariableEffectTemplate overrides CardAbilityEffectTemplate's
    # per-card Apply and writes one ability-instance variable. Re-running it
    # for every target made later AbilityVariableCondition branches use only
    # the final target's roll instead of the single authored roll.
    "RandomizeVariableEffectTemplate",
    "RandomizeVariableAbilityEffectTemplate",
})

_Leaf = Callable[[Any, Any], Any]

_ZONES: dict[str, str] = {
    "DiscardCardAbilityEffectTemplate": "discard",
    "DestroyCardAbilityEffectTemplate": "destroy",
    "VoidCardAbilityEffectTemplate": "void_card",
    "BuryCardAbilityEffectTemplate": "bury",
    "PutTopOfDeckIntoHandAbilityEffectTemplate": "put_top_into_hand",
    "TunnelAbilityEffectTemplate": "tunnel",
    "DestroyCardByDefenseAbilityEffectTemplate": "destroy_by_defense",
    "DiscardOrSacrificeCardAbilityEffectTemplate": "discard_or_sacrifice",
    "ReturnToHandAbilityEffectTemplate": "return_to_hand",
    "MoveCardToZoneEffectTemplate": "move_card_to_zone",
    "FinishMovingCardToWarzoneEffectTemplate": "finish_moving_to_warzone",
    "FinishResolvingCardAbilityEffectTemplate": "finish_resolving_card",
    "TargetPlayerTakesControlEffectTemplate": "target_player_takes_control",
    "StealCardAbilityEffectTemplate": "steal_card",
    "StealEffectsAbilityEffectTemplate": "steal_effects",
    "RevertTransformedCardAbilityEffectTemplate": "revert_transformed_card",
    "ExchangeCardsAbilityEffectTemplate": "exchange_cards",
    "MergeCardCollectionsAbilityEffectTemplate": "merge_card_collections",
    "ShuffleCardCollectionAbilityEffectTemplate": "shuffle_collection",
    "RevertPermanentModificationsAbilityEffectTemplate": "revert_modifications",
    "RemoveCardFromCombatAbilityEffectTemplate": "remove_from_combat",
    "SacrificeCardAbilityEffectTemplate": "sacrifice",
}

_PLAYERS: dict[str, str] = {
    "DrawNCardsAbilityEffectTemplate": "draw_effect",
    "LoseGameAbilityEffectTemplate": "lose_game",
    "ReplenishResourcesAbilityEffectTemplate": "replenish_resources",
    "SwapHealthAbilityEffectTemplate": "swap_health",
    "PlayerAttributeAbilityEffectTemplate": "player_attribute",
}

_TRANSFORMS: dict[str, str] = {
    "TransformSelfAbilityEffectTemplate": "transform_self",
    "TransformCardToTargetAbilityEffectTemplate": "transform_card_to_target",
    "TransformCardIntoReplicaAbilityEffectTemplate": "transform_replica",
    "TransformCardAtRandomAbilityEffectTemplate": "transform_card_random",
    "TransformCardAbilityEffectTemplate": "transform_card",
    "CreateTokenCopyAbilityEffectTemplate": "create_token_copy",
    "CreateTokenMatchingTargetAbilityEffectTemplate": "create_matching_token",
    "SummonTokenTroopAbilityEffectTemplate": "summon_token",
    "SummonXTokenTroopsAbilityEffectTemplate": "summon_x_tokens",
    "ConscriptAbilityEffectTemplate": "conscript",
    "LoadPlayerDeckAbilityEffectTemplate": "load_player_deck",
}

_VARIABLES: dict[str, str] = {
    "CopyAbilityVariableEffectTemplate": "copy_ability_variable",
    "SetCardCountVariableEffectTemplate": "set_card_count_variable",
    "SetCardIntegerVariableEffectTemplate": "set_card_integer_variable",
    "SetConstantValueVariableEffectTemplate": "set_constant_value_variable",
    "ClearStoredAbilityEffectTemplate": "clear_stored",
    "SetResponsiblePlayerAbilityEffectTemplate": "set_responsible_player",
    "StoreTargetsAbilityEffectTemplate": "store_target",
    "StoreNameAbilityEffectTemplate": "store_name",
    "RememberKeywordPowersEffectTemplate": "remember_keyword_powers",
    "RandomizeVariableEffectTemplate": "randomize_variable",
    "RandomizeVariableAbilityEffectTemplate": "randomize_variable",
}

_ABILITIES: dict[str, str] = {
    "RegisterTriggerAbilityEffectTemplate": "register_trigger",
    "RevokeAbilityEffectTemplate": "revoke_ability",
    "GiveBonusTurnAbilityEffectTemplate": "queue_bonus_turn",
    "DoubleChoiceAbilityEffectTemplate": "double_choice",
    "CounterSpellAbilityEffectTemplate": "counter_spell",
    "InterruptSpellAbilityEffectTemplate": "counter_spell",
    "FireEventEffectTemplate": "fire_event",
    "TACAbilityEffectTemplate": "tac",
    "VerdictAbilityEffectTemplate": "verdict",
    "ConversationAbilityEffectTemplate": "conversation",
    "ActivateTriggeredAbilityEffectTemplate": "activate_triggered",
    "ActivateAbilityEffectTemplate": "activate_ability",
    "ActivatePowerAbilityEffectTemplate": "activate_ability",
    "PlayCardAbilityEffectTemplate": "play_card",
    "BuiltInPlayCardAbilityEffectTemplate": "play_card",
    "BlockEffectTemplate": "block",
    "Battle2CardsAbilityEffectTemplate": "battle_cards",
    "ZombiePlagueAbilityEffectTemplate": "zombie_plague",
    "XarloxAbilityEffectTemplate": "xarlox",
    "PlanCAbilityEffectTemplate": "plan_c",
    "RevealCardsAbilityEffectTemplate": "reveal_cards",
}

_MODULE_FOR_NAME: dict[str, tuple[Any, str]] = {}
for _table, _module in (
        (_ZONES, _leaves_zones),
        (_PLAYERS, _leaves_players),
        (_TRANSFORMS, _leaves_transforms),
        (_VARIABLES, _leaves_variables),
        (_ABILITIES, _leaves_abilities)):
    for _effect, _leaf_name in _table.items():
        # Resolved at call time through the module, so a rebinding of the leaf is seen.
        _MODULE_FOR_NAME[_effect] = (_module, _leaf_name)


def _constant(value: str) -> _Leaf:
    return lambda ctx, effect: value


def _draw_one(ctx: Any, effect: Any) -> Any:
    return _leaves_players.draw(ctx, 1, owner=ctx.target_owner(default=None))


def _battle_cards(ctx: Any, effect: Any) -> Any:
    return _leaves_abilities.battle_cards(ctx, effect)


NATIVE_LEAVES: dict[str, _Leaf] = {
    "NoOpEffectTemplate": _constant("no-op"),
    "ExtraCombatsThisTurnAbilityEffectTemplate": _constant("extra combats: obsolete"),
    "DrawCardAbilityEffectTemplate": _draw_one,
    "CopyAbilityEffectTemplate": lambda ctx, effect: _ir_events._copy_ability(ctx),
    "GrantAbilityEffectTemplate": lambda ctx, effect: _ir_events.grant_ability(ctx),
    "CreateAndCastSpellAbilityEffectTemplate":
        lambda ctx, effect: _ir_events.create_and_cast_spell(ctx),
    "StoreListAttrAbilityEffectTemplate": lambda ctx, effect: _ir_events._store_list_attr(ctx),
    "AnimationTriggerEffectTemplate": lambda ctx, effect: _ir_events._animation_trigger(ctx),
    "LoseThresholdAbilityEffectTemplate":
        lambda ctx, effect: _ir_resources._lose_threshold(ctx, effect),
    "CardModifierAbilityEffectTemplate":
        lambda ctx, effect: _ir_modifiers._card_modifier(ctx, effect),
    "RepeatingAbilityEffectTemplate": lambda ctx, effect: _ir_resources._repeating(ctx, effect),
    "UntapCardAbilityEffectTemplate": lambda ctx, effect: _ir_modifiers._untap(ctx),
    "TapCardAbilityEffectTemplate": lambda ctx, effect: _ir_modifiers._tap(ctx),
}
for _effect, (_mod, _name) in _MODULE_FOR_NAME.items():
    NATIVE_LEAVES.setdefault(_effect, (lambda m, n: lambda ctx, effect: getattr(m, n)(ctx))(_mod, _name))
# Leaves that take the effect as well as the context.
NATIVE_LEAVES["Battle2CardsAbilityEffectTemplate"] = _battle_cards


def dispatch(effect_type, context, effect=None):
    """Run one Records effect through its native leaf; ``None`` when no leaf runs it."""
    leaf = NATIVE_LEAVES.get(effect_type)
    if leaf is None:
        return None
    return leaf(context, effect)
