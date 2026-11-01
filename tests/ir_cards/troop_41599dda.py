# Ability 41599dda: At the start of your turn, if there are less than 20 energy counters on this, add an energy counters to this. If there are less than 20 energy counters on this, add an energy counters to this. If there are less than 20 energy counters on this, add an energy counters to this. If there are less than 20 energy counters on this, add an energy counters to this.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: AddAnEnergyCountersToThis
ADD_ENERGY_COUNTERS = CardModifierAbilityEffectTemplate(
    game_text='add an energy counters to this.',
    name='AddAnEnergyCountersToThis',
    template_id=Ref('ebc4a42a-45b0-046d-714c-97a558631e48'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='An'), card_counter_template_id=Ref('5b184a30-bc4c-4f6f-cde7-cf7d51750097')),
)
# Records name: AddAnEnergyCountersToThis
ADD_ENERGY_COUNTERS_2 = CardModifierAbilityEffectTemplate(
    game_text='add an energy counters to this.',
    name='AddAnEnergyCountersToThis',
    template_id=Ref('ebc4a42a-45b0-046d-714c-97a558631e48'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='An'), card_counter_template_id=Ref('5b184a30-bc4c-4f6f-cde7-cf7d51750097')),
)
# Records name: AddAnEnergyCountersToThis
ADD_ENERGY_COUNTERS_3 = CardModifierAbilityEffectTemplate(
    game_text='add an energy counters to this.',
    name='AddAnEnergyCountersToThis',
    template_id=Ref('ebc4a42a-45b0-046d-714c-97a558631e48'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='An'), card_counter_template_id=Ref('5b184a30-bc4c-4f6f-cde7-cf7d51750097')),
)
# Records name: AddAnEnergyCountersToThis
ADD_ENERGY_COUNTERS_4 = CardModifierAbilityEffectTemplate(
    game_text='add an energy counters to this.',
    name='AddAnEnergyCountersToThis',
    template_id=Ref('ebc4a42a-45b0-046d-714c-97a558631e48'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='An'), card_counter_template_id=Ref('5b184a30-bc4c-4f6f-cde7-cf7d51750097')),
)

# Records name: AtTheStartOfYourTurnIfThereAreLessThan20EnergyCountersOnThisAddAnEnergyCountersToThisIfThereAreLessThan20EnergyCountersOnThisAddAnEnergyCountersToThisIfThereAreLessThan20EnergyCountersOnThisAddAnEnergyCountersToThisIfThereAreLessThan20EnergyCountersOnThisAddAnEnergyCountersToThis
START_TURN_THERE_LESS_THAN = AbilityTemplate(
    ability_target_template_ids=[Ref('190a4d8c-7c2c-10d0-6429-99c5aeb0791f'), Ref('190a4d8c-7c2c-10d0-6429-99c5aeb0791f'), Ref('190a4d8c-7c2c-10d0-6429-99c5aeb0791f'), Ref('190a4d8c-7c2c-10d0-6429-99c5aeb0791f')],
    ability_template_id=Ref('41599dda-3ef8-d696-2e31-1e9ec8d8e163'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='At the start of your turn, if there are less than 20 energy counters on this, add an energy counters to this. If there are less than 20 energy counters on this, add an energy counters to this. If there are less than 20 energy counters on this, add an energy counters to this. If there are less than 20 energy counters on this, add an energy counters to this.',
    name='AtTheStartOfYourTurnIfThereAreLessThan20EnergyCountersOnThisAddAnEnergyCountersToThisIfThereAreLessThan20EnergyCountersOnThisAddAnEnergyCountersToThisIfThereAreLessThan20EnergyCountersOnThisAddAnEnergyCountersToThisIfThereAreLessThan20EnergyCountersOnThisAddAnEnergyCountersToThis',
    put_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_deck_target2=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_hand_target=Ref('00000000-0000-0000-0000-000000000000'),
    reveal_target=Ref('00000000-0000-0000-0000-000000000000'),
    sacrifice_target=Resource('00000000-0000-0000-0000-000000000000'),
    serialized_tac={'data': 'AgAAAAAA'},
    shuffle_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    trigger_collection_flags='Champions|Warzone',
    trigger_condition=TriggerPlayerControlsAbilitySource(),
    trigger_event_type={'m_InternalType': 'Game.Shared.Mechanics.TurnStartedEvent'},
    variables=[AbilityConstant(default_value=1, name='An'), AbilityConstant(default_value=1, name='An'), AbilityConstant(default_value=1, name='An'), AbilityConstant(default_value=1, name='An')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("ebc4a42a-45b0-046d-714c-97a558631e48"), condition_id=Ref("40b7c743-28a3-13db-4f02-a60c65b85c08")), Clause(effect_template_id=Ref("ebc4a42a-45b0-046d-714c-97a558631e48"), target_template_index=1, effect_instance_id=1, condition_id=Ref("40b7c743-28a3-13db-4f02-a60c65b85c08"), effect_group_id=2), Clause(effect_template_id=Ref("ebc4a42a-45b0-046d-714c-97a558631e48"), target_template_index=2, effect_instance_id=2, condition_id=Ref("40b7c743-28a3-13db-4f02-a60c65b85c08"), effect_group_id=3), Clause(effect_template_id=Ref("ebc4a42a-45b0-046d-714c-97a558631e48"), target_template_index=3, effect_instance_id=3, condition_id=Ref("40b7c743-28a3-13db-4f02-a60c65b85c08"), effect_group_id=4)],
)
