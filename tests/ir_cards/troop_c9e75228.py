# Ability c9e75228: <b>Deathcry</b> - This deals 6 damage to each troop. Add three burning counters to each opposing champion.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: ThisDeals6DamageToEachTroop
DEALS_DAMAGE_TROOP = CardModifierAbilityEffectTemplate(
    game_text='This deals 6 damage to each troop.',
    name='ThisDeals6DamageToEachTroop',
    template_id=Ref('6f8d1326-50e7-b809-c6eb-9770f1fdf75a'),
    modifier=DamageModifier(input_value=EffectInputVariable(input_variable_name='6')),
)
# Records name: AddThreeBurningCountersToEachOpposingChampion
ADD_THREE_BURNING_COUNTERS_OPPOSING = CardModifierAbilityEffectTemplate(
    game_text='Add three burning counters to each opposing champion.',
    name='AddThreeBurningCountersToEachOpposingChampion',
    template_id=Ref('9f0935c8-b3ec-364f-7760-56b12378e1be'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='Three'), card_counter_template_id=Ref('3932ad3a-9807-de2a-e1b9-a8b50e81def9')),
)

# Records name: BdeathcrybMThisDeals6DamageToEachTroopAddThreeBurningCountersToEachOpposingChampion
DEATHCRY_DEALS_DAMAGE_TROOP_ADD = AbilityTemplate(
    ability_target_template_ids=[Ref('9c566d93-5fad-e85b-5150-c29be35ee7f5'), Ref('c35dd13a-71e2-b244-847a-d887a0666210')],
    ability_template_id=Ref('c9e75228-0244-ab59-2cf4-e810f50a682e'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='<b>Deathcry</b> - This deals 6 damage to each troop. Add three burning counters to each opposing champion.',
    name='BdeathcrybMThisDeals6DamageToEachTroopAddThreeBurningCountersToEachOpposingChampion',
    put_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_deck_target2=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_hand_target=Ref('00000000-0000-0000-0000-000000000000'),
    reveal_target=Ref('00000000-0000-0000-0000-000000000000'),
    sacrifice_target=Resource('00000000-0000-0000-0000-000000000000'),
    serialized_tac={'data': 'AgDN0BHXAQAAAAAAAAA='},
    shuffle_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    trigger_collection_flags='Champions|Warzone',
    trigger_condition=AndTriggerCondition(conditions=[TriggerCardIsAbilitySource(), TriggerCardEnteredZone(destination_collection='Discard', source_collection='Warzone')]),
    trigger_event_type={'m_InternalType': 'Game.Shared.Mechanics.CardEnteredZoneEvent'},
    uses_previous_state=1,
    variables=[AbilityConstant(default_value=6, name='6'), AbilityConstant(default_value=3, name='Three')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("6f8d1326-50e7-b809-c6eb-9770f1fdf75a")), Clause(effect_template_id=Ref("9f0935c8-b3ec-364f-7760-56b12378e1be"), target_template_index=1, effect_instance_id=1, effect_group_id=2)],
)
