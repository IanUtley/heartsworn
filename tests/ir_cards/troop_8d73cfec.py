# Ability 8d73cfec: At the start of your turn, add an egg counter to this.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: AddAnEggCounterToThis
ADD_EGG_COUNTER = CardModifierAbilityEffectTemplate(
    game_text='add an egg counter to this.',
    name='AddAnEggCounterToThis',
    template_id=Ref('f2363541-84ee-1aaa-3931-5faffe43ca6c'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='An'), card_counter_template_id=Ref('b55ea635-a2a9-ba00-4af2-0a097b2566ee')),
)

# Records name: AtTheStartOfYourTurnAddAnEggCounterToThis
START_TURN_ADD_EGG_COUNTER = AbilityTemplate(
    ability_target_template_ids=[Ref('190a4d8c-7c2c-10d0-6429-99c5aeb0791f')],
    ability_template_id=Ref('8d73cfec-48a0-ba3d-0c7a-631d3095849e'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='At the start of your turn, add an egg counter to this.',
    name='AtTheStartOfYourTurnAddAnEggCounterToThis',
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
    ability_effect_list=[Clause(effect_template_id=Ref("f2363541-84ee-1aaa-3931-5faffe43ca6c"))],
)
