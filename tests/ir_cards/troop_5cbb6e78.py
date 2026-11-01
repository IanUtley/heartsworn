# Ability 5cbb6e78: At the start of your turn, add a seed counter to this.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: AddASeedCounterToThis
ADD_SEED_COUNTER = CardModifierAbilityEffectTemplate(
    game_text='add a seed counter to this.',
    name='AddASeedCounterToThis',
    template_id=Ref('095e63b7-3ee7-dbb7-07a3-d9dac6f9a4ee'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='A'), card_counter_template_id=Ref('d5d41a79-8ee3-9443-ff83-544f139a98be')),
)

# Records name: AtTheStartOfYourTurnAddASeedCounterToThis
START_TURN_ADD_SEED_COUNTER = AbilityTemplate(
    ability_target_template_ids=[Ref('190a4d8c-7c2c-10d0-6429-99c5aeb0791f')],
    ability_template_id=Ref('5cbb6e78-ad03-12f4-abb4-ee1d16a6b341'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='At the start of your turn, add a seed counter to this.',
    name='AtTheStartOfYourTurnAddASeedCounterToThis',
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
    variables=[AbilityConstant(default_value=1, name='A')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("095e63b7-3ee7-dbb7-07a3-d9dac6f9a4ee"))],
)
