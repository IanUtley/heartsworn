# Ability b2c3f85c: <b>Deploy</b> - This deals 1 damage to each opposing troop.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: ThisDeals1DamageToEachOpposingTroop
DEALS_DAMAGE_OPPOSING_TROOP = CardModifierAbilityEffectTemplate(
    game_text='this deals 1 damage to each opposing troop.',
    name='ThisDeals1DamageToEachOpposingTroop',
    template_id=Ref('09b57f29-fc2b-cf24-d1f7-18315a64abcd'),
    modifier=DamageModifier(input_value=EffectInputVariable(input_variable_name='1')),
)

# Records name: BdeploybMThisDeals1DamageToEachOpposingTroop
DEPLOY_DEALS_DAMAGE_OPPOSING_TROOP = AbilityTemplate(
    ability_target_template_ids=[Ref('ffd7b68e-1b75-ac43-6c2e-13f04ed388dc')],
    ability_template_id=Ref('b2c3f85c-c1fd-bb7a-879e-2e4f028eb63b'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='<b>Deploy</b> - This deals 1 damage to each opposing troop.',
    name='BdeploybMThisDeals1DamageToEachOpposingTroop',
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
    variables=[AbilityConstant(default_value=1, name='1')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("09b57f29-fc2b-cf24-d1f7-18315a64abcd"))],
)
