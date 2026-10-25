# Ability 4c0c8d08: <b>Deploy</b> - This deals 1 damage to each opposing champion for each different threshold you have.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: ThisDeals1DamageToEachOpposingChampionForEachDifferentThresholdYouHave
DEALS_DAMAGE_OPPOSING_CHAMPION_DIFFERENT = CardModifierAbilityEffectTemplate(
    game_text='This deals 1 damage to each opposing champion for each different threshold you have.',
    name='ThisDeals1DamageToEachOpposingChampionForEachDifferentThresholdYouHave',
    template_id=Ref('f3a99ce2-0ae1-7832-cc70-a0e00c130604'),
    modifier=DamageModifier(input_value=EffectInputVariable(input_variable_name='1ForEachDifferentThresholdYouHave')),
)

# Records name: BdeploybMThisDeals1DamageToEachOpposingChampionForEachDifferentThresholdYouHave
DEPLOY_DEALS_DAMAGE_OPPOSING_CHAMPION = AbilityTemplate(
    ability_target_template_ids=[Ref('c35dd13a-71e2-b244-847a-d887a0666210')],
    ability_template_id=Ref('4c0c8d08-9dd1-88bf-6acd-a79766c89558'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='<b>Deploy</b> - This deals 1 damage to each opposing champion for each different threshold you have.',
    name='BdeploybMThisDeals1DamageToEachOpposingChampionForEachDifferentThresholdYouHave',
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
    variables=[SourcePlayerThresholdAbilityVariable(name='1ForEachDifferentThresholdYouHave', count_uniques=1, dont_recalculate=0, threshold='Any')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("f3a99ce2-0ae1-7832-cc70-a0e00c130604"))],
)
