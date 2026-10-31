# Ability 65cec3c8: <b>Deathcry</b> - Add two burning counters to each opposing champion.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: AddTwoBurningCountersToEachOpposingChampion
ADD_TWO_BURNING_COUNTERS_OPPOSING = CardModifierAbilityEffectTemplate(
    game_text='add two burning counters to each opposing champion.',
    name='AddTwoBurningCountersToEachOpposingChampion',
    template_id=Ref('5f612bd5-854a-9e18-eed0-662cbcbe01ee'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='Two'), card_counter_template_id=Ref('3932ad3a-9807-de2a-e1b9-a8b50e81def9')),
)

# Records name: BdeathcrybMAddTwoBurningCountersToEachOpposingChampion
DEATHCRY_ADD_TWO_BURNING_COUNTERS = AbilityTemplate(
    ability_target_template_ids=[Ref('c35dd13a-71e2-b244-847a-d887a0666210')],
    ability_template_id=Ref('65cec3c8-c7f4-6148-8a6f-451d7b9d497a'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='<b>Deathcry</b> - Add two burning counters to each opposing champion.',
    name='BdeathcrybMAddTwoBurningCountersToEachOpposingChampion',
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
    variables=[AbilityConstant(default_value=2, name='Two')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("5f612bd5-854a-9e18-eed0-662cbcbe01ee"))],
)
