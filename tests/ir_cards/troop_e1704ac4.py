# Ability e1704ac4: <b>Deploy</b> - Add two energy counters to this.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: AddTwoEnergyCountersToThis
ADD_TWO_ENERGY_COUNTERS = CardModifierAbilityEffectTemplate(
    game_text='Add two energy counters to this.',
    name='AddTwoEnergyCountersToThis',
    template_id=Ref('5d75ebc8-64f5-9cd7-1cab-86e9600547c2'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='Two'), card_counter_template_id=Ref('5b184a30-bc4c-4f6f-cde7-cf7d51750097')),
)

# Records name: BdeploybMAddTwoEnergyCountersToThis
DEPLOY_ADD_TWO_ENERGY_COUNTERS = AbilityTemplate(
    ability_target_template_ids=[Ref('190a4d8c-7c2c-10d0-6429-99c5aeb0791f')],
    ability_template_id=Ref('e1704ac4-5f28-896f-3bdf-8bc4dd704a0c'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='<b>Deploy</b> - Add two energy counters to this.',
    name='BdeploybMAddTwoEnergyCountersToThis',
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
    variables=[AbilityConstant(default_value=2, name='Two')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("5d75ebc8-64f5-9cd7-1cab-86e9600547c2"))],
)
