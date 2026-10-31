# Ability 407e059b: <b>Deploy</b> - Add four energy counters to this.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: AddFourEnergyCountersToThis
ADD_FOUR_ENERGY_COUNTERS = CardModifierAbilityEffectTemplate(
    game_text='Add four energy counters to this.',
    name='AddFourEnergyCountersToThis',
    template_id=Ref('f656e807-01dc-0dbe-3af8-3b512f90024e'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='Four'), card_counter_template_id=Ref('5b184a30-bc4c-4f6f-cde7-cf7d51750097')),
)

# Records name: BdeploybMAddFourEnergyCountersToThis
DEPLOY_ADD_FOUR_ENERGY_COUNTERS = AbilityTemplate(
    ability_target_template_ids=[Ref('190a4d8c-7c2c-10d0-6429-99c5aeb0791f')],
    ability_template_id=Ref('407e059b-5ace-3226-18c9-8670df9b7b6d'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='<b>Deploy</b> - Add four energy counters to this.',
    name='BdeploybMAddFourEnergyCountersToThis',
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
    variables=[AbilityConstant(default_value=4, name='Four')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("f656e807-01dc-0dbe-3af8-3b512f90024e"))],
)
