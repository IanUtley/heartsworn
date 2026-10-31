# Ability 3d8b8a9b: At the start of your turn, remove an energy counter from this.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: RemoveAnEnergyCounterFromThis
REMOVE_ENERGY_COUNTER = CardModifierAbilityEffectTemplate(
    game_text='remove an energy counter from this.',
    name='RemoveAnEnergyCounterFromThis',
    template_id=Ref('7ca9a3be-7916-3576-b4cd-7a31bdc3bae2'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='An'), card_counter_template_id=Ref('5b184a30-bc4c-4f6f-cde7-cf7d51750097'), operation='Remove'),
)

# Records name: AtTheStartOfYourTurnRemoveAnEnergyCounterFromThis
START_TURN_REMOVE_ENERGY_COUNTER = AbilityTemplate(
    ability_target_template_ids=[Ref('190a4d8c-7c2c-10d0-6429-99c5aeb0791f')],
    ability_template_id=Ref('3d8b8a9b-496e-6325-3c8a-453136517a1a'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='At the start of your turn, remove an energy counter from this.',
    name='AtTheStartOfYourTurnRemoveAnEnergyCounterFromThis',
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
    variables=[AbilityConstant(default_value=1, name='An')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("7ca9a3be-7916-3576-b4cd-7a31bdc3bae2"))],
)
