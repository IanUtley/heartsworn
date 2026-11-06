"""RulesPort-native Records effect operations.

The resolver still obtains effect ordering and targets from Records.  This
module is the first native leaf boundary: simple state transitions execute
through the typed EffectContext without entering the legacy BOM leaf.
"""

from __future__ import annotations

from .ir_modifiers import card_cost_modifier as _card_cost  # noqa: F401
from .ir_modifiers import heal_hero_modifier as _heal_hero  # noqa: F401
from .ir_modifiers import int_attr_modifier as _int_attribute  # noqa: F401



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


def dispatch(effect_type, context, effect=None):
    """Dispatch one Records effect through the native RulesPort leaf set."""
    native = {
        "NoOpEffectTemplate": lambda: "no-op",
        "DrawCardAbilityEffectTemplate": lambda: context.draw(
            1, owner=context.target_owner(default=None)),
        "ClearStoredAbilityEffectTemplate": context.clear_stored,
        "SetResponsiblePlayerAbilityEffectTemplate":
            context.set_responsible_player,
        "CopyAbilityVariableEffectTemplate": context.copy_ability_variable,
        "SetCardCountVariableEffectTemplate": context.set_card_count_variable,
        "SetCardIntegerVariableEffectTemplate":
            context.set_card_integer_variable,
        "SetConstantValueVariableEffectTemplate":
            context.set_constant_value_variable,
        "ReplenishResourcesAbilityEffectTemplate": context.replenish_resources,
        "RemoveCardFromCombatAbilityEffectTemplate":
            lambda: context.remove_from_combat(),
        "SwapHealthAbilityEffectTemplate": context.swap_health,
        "ExtraCombatsThisTurnAbilityEffectTemplate":
            lambda: "extra combats: obsolete",
        "DrawNCardsAbilityEffectTemplate": context.draw_effect,
        "PutTopOfDeckIntoHandAbilityEffectTemplate":
            context.put_top_into_hand,
        "BuryCardAbilityEffectTemplate": context.bury,
        "VoidCardAbilityEffectTemplate": context.void_card,
        "DiscardCardAbilityEffectTemplate": context.discard,
        "TunnelAbilityEffectTemplate": context.tunnel,
        "TargetPlayerTakesControlEffectTemplate":
            context.target_player_takes_control,
        "StealCardAbilityEffectTemplate": context.steal_card,
        "StealEffectsAbilityEffectTemplate": context.steal_effects,
        "CopyAbilityEffectTemplate": lambda: _copy_ability(context),
        "GrantAbilityEffectTemplate": lambda: grant_ability(context),
        "CreateAndCastSpellAbilityEffectTemplate":
            lambda: create_and_cast_spell(context),
        "DoubleChoiceAbilityEffectTemplate": lambda: context.double_choice(),
        "LoseGameAbilityEffectTemplate": context.lose_game,
        "RevertTransformedCardAbilityEffectTemplate":
            context.revert_transformed_card,
        "StoreTargetsAbilityEffectTemplate": context.store_target,
        "StoreNameAbilityEffectTemplate": context.store_name,
        "RememberKeywordPowersEffectTemplate": context.remember_keyword_powers,
        "RegisterTriggerAbilityEffectTemplate": context.register_trigger,
        "RevokeAbilityEffectTemplate": context.revoke_ability,
        "RevertPermanentModificationsAbilityEffectTemplate":
            context.revert_modifications,
        "GiveBonusTurnAbilityEffectTemplate": context.queue_bonus_turn,
        "SacrificeCardAbilityEffectTemplate": context.sacrifice,
        "TransformSelfAbilityEffectTemplate": context.transform_self,
        "TransformCardToTargetAbilityEffectTemplate":
            context.transform_card_to_target,
        "TransformCardIntoReplicaAbilityEffectTemplate":
            context.transform_replica,
        "TransformCardAtRandomAbilityEffectTemplate":
            context.transform_card_random,
        "TransformCardAbilityEffectTemplate": context.transform_card,
        "CreateTokenCopyAbilityEffectTemplate": context.create_token_copy,
        "CreateTokenMatchingTargetAbilityEffectTemplate":
            context.create_matching_token,
        "SummonTokenTroopAbilityEffectTemplate": context.summon_token,
        "SummonXTokenTroopsAbilityEffectTemplate": context.summon_x_tokens,
        "ConscriptAbilityEffectTemplate": context.conscript,
        "LoadPlayerDeckAbilityEffectTemplate": context.load_player_deck,
        "DestroyCardByDefenseAbilityEffectTemplate": context.destroy_by_defense,
        "DiscardOrSacrificeCardAbilityEffectTemplate":
            context.discard_or_sacrifice,
        "PlayerAttributeAbilityEffectTemplate": context.player_attribute,
        "ExchangeCardsAbilityEffectTemplate": context.exchange_cards,
        "MergeCardCollectionsAbilityEffectTemplate":
            context.merge_card_collections,
        "ZombiePlagueAbilityEffectTemplate": context.zombie_plague,
        "XarloxAbilityEffectTemplate": context.xarlox,
        "PlanCAbilityEffectTemplate": context.plan_c,
        "ShuffleCardCollectionAbilityEffectTemplate": context.shuffle_collection,
        "RevealCardsAbilityEffectTemplate": context.reveal_cards,
        "Battle2CardsAbilityEffectTemplate":
            lambda: context.battle_cards(effect),
        "CounterSpellAbilityEffectTemplate": context.counter_spell,
        "InterruptSpellAbilityEffectTemplate": context.counter_spell,
        "DestroyCardAbilityEffectTemplate": context.destroy,
        "FireEventEffectTemplate": context.fire_event,
        "TACAbilityEffectTemplate": context.tac,
        "VerdictAbilityEffectTemplate": context.verdict,
        "ReturnToHandAbilityEffectTemplate": context.return_to_hand,
        "MoveCardToZoneEffectTemplate": context.move_card_to_zone,
        "FinishMovingCardToWarzoneEffectTemplate":
            context.finish_moving_to_warzone,
        "FinishResolvingCardAbilityEffectTemplate":
            context.finish_resolving_card,
        "RandomizeVariableEffectTemplate": context.randomize_variable,
        "RandomizeVariableAbilityEffectTemplate": context.randomize_variable,
        "ConversationAbilityEffectTemplate": context.conversation,
        "ActivateTriggeredAbilityEffectTemplate": context.activate_triggered,
        "ActivateAbilityEffectTemplate": context.activate_ability,
        "ActivatePowerAbilityEffectTemplate": context.activate_ability,
        "PlayCardAbilityEffectTemplate": context.play_card,
        "BuiltInPlayCardAbilityEffectTemplate": context.play_card,
        "BlockEffectTemplate": context.block,
    }
    operation = native.get(effect_type)
    if operation is not None:
        return operation()
    if effect_type == "StoreListAttrAbilityEffectTemplate":
        return _store_list_attr(context)
    if effect_type == "AnimationTriggerEffectTemplate":
        return _animation_trigger(context)
    if effect_type == "LoseThresholdAbilityEffectTemplate":
        return _lose_threshold(context, effect)
    if effect_type == "CardModifierAbilityEffectTemplate":
        return _card_modifier(context, effect)
    if effect_type == "RepeatingAbilityEffectTemplate":
        return _repeating(context, effect)
    if effect_type == "UntapCardAbilityEffectTemplate":
        return _untap(context)
    if effect_type == "TapCardAbilityEffectTemplate":
        return _tap(context)
    return None


def _copy_ability(context):
    from .ir_event_effects import _copy_ability as _impl
    return _impl(context)


def grant_ability(context):
    from .ir_event_effects import grant_ability as _impl
    return _impl(context)


def create_and_cast_spell(context):
    from .ir_event_effects import create_and_cast_spell as _impl
    return _impl(context)


def _store_list_attr(context):
    from .ir_event_effects import _store_list_attr as _impl
    return _impl(context)


def _animation_trigger(context):
    from .ir_event_effects import _animation_trigger as _impl
    return _impl(context)


def _repeating(context, effect):
    from .ir_exec_resources import _repeating as _impl
    return _impl(context, effect)


def _lose_threshold(context, effect):
    from .ir_exec_resources import _lose_threshold as _impl
    return _impl(context, effect)


def _resource_modifier(context, effect):
    from .ir_exec_resources import _resource_modifier as _impl
    return _impl(context, effect)


def _card_modifier(context, effect):
    from .ir_modifiers import _card_modifier as _impl
    return _impl(context, effect)


def _card_tag(context, target, param):
    from .ir_modifiers import _card_tag as _impl
    return _impl(context, target, param)


def _capture_tamed_card(context, target, buffs):
    from .ir_modifiers import _capture_tamed_card as _impl
    return _impl(context, target, buffs)


def _targets(context):
    from .ir_modifiers import _targets as _impl
    return _impl(context)


def _each_untap_targets(context):
    from .ir_modifiers import _each_untap_targets as _impl
    return _impl(context)


def _untap(context, targets=None):
    from .ir_modifiers import _untap as _impl
    return _impl(context, targets)


def _tap(context, targets=None):
    from .ir_modifiers import _tap as _impl
    return _impl(context, targets)
