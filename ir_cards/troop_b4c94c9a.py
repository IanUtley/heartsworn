# Ability b4c94c9a: <b>Deploy</b> - This deals 1 damage to each opposing troop for each Wormoid you control.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: ThisDeals1DamageToEachOpposingTroopForEachWormoidYouControl
DEALS_DAMAGE_OPPOSING_TROOP_WORMOID = CardModifierAbilityEffectTemplate(
    game_text='This deals 1 damage to each opposing troop for each Wormoid you control.',
    name='ThisDeals1DamageToEachOpposingTroopForEachWormoidYouControl',
    template_id=Ref('8e7438e3-c599-d379-4f05-c8429eebbb6c'),
    modifier=DamageModifier(input_value=EffectInputVariable(input_variable_name='1ForEachWormoidYouControl')),
)

# Records name: BdeploybMThisDeals1DamageToEachOpposingTroopForEachWormoidYouControl
DEPLOY_DEALS_DAMAGE_OPPOSING_TROOP = AbilityTemplate(
    ability_target_template_ids=[Ref('ffd7b68e-1b75-ac43-6c2e-13f04ed388dc')],
    ability_template_id=Ref('b4c94c9a-0fe7-8376-0d14-b59d4c3351dc'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='<b>Deploy</b> - This deals 1 damage to each opposing troop for each Wormoid you control.',
    name='BdeploybMThisDeals1DamageToEachOpposingTroopForEachWormoidYouControl',
    put_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_deck_target2=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_hand_target=Ref('00000000-0000-0000-0000-000000000000'),
    reveal_target=Ref('00000000-0000-0000-0000-000000000000'),
    sacrifice_target=Resource('00000000-0000-0000-0000-000000000000'),
    serialized_tac={'data': 'AgCIOnpQAQAAAAAAAAA='},
    shuffle_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    trigger_collection_flags='Champions|Warzone',
    trigger_condition=AndTriggerCondition(conditions=[TriggerCardIsAbilitySource(), TriggerCardEnteredZone(destination_collection='Warzone')]),
    trigger_event_type={'m_InternalType': 'Game.Shared.Mechanics.CardEnteredZoneEvent'},
    variables=[CardCountAbilityVariable(name='1ForEachWormoidYouControl', card_filter=AndCardFilter(target_filters=[IsSubType(sub_type='Wormoid'), IsControlledBy()]), collection_flags='Warzone', dont_recalculate=0, player_filter='MultiplePlayers')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("8e7438e3-c599-d379-4f05-c8429eebbb6c"))],
)
