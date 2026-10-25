# Ability 6c1d547f: <b>Deploy</b> - This deals 1 damage to all opposing champions and troops for each card that inspired it.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: ThisDeals1DamageToAllOpposingChampionsAndTroopsForEachCardThatInspiredIt
DEALS_DAMAGE_ALL_OPPOSING_CHAMPIONS = CardModifierAbilityEffectTemplate(
    game_text='This deals 1 damage to all opposing champions and troops for each card that inspired it.',
    name='ThisDeals1DamageToAllOpposingChampionsAndTroopsForEachCardThatInspiredIt',
    template_id=Ref('637f0868-22f3-2a03-a043-9ff629f40151'),
    modifier=DamageModifier(input_value=EffectInputVariable(input_variable_name='1ForEachCardThatInspiredIt')),
)

# Records name: BdeploybMThisDeals1DamageToAllOpposingChampionsAndTroopsForEachCardThatInspiredIt
DEPLOY_DEALS_DAMAGE_ALL_OPPOSING = AbilityTemplate(
    ability_target_template_ids=[Ref('d5956664-ea0d-3514-c498-625ace395643')],
    ability_template_id=Ref('6c1d547f-064b-e0b5-e450-aa3609720bc5'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='<b>Deploy</b> - This deals 1 damage to all opposing champions and troops for each card that inspired it.',
    name='BdeploybMThisDeals1DamageToAllOpposingChampionsAndTroopsForEachCardThatInspiredIt',
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
    variables=[ExpressionAbilityVariable(name='1ForEachCardThatInspiredIt', expression_text='INS')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("637f0868-22f3-2a03-a043-9ff629f40151"))],
)
