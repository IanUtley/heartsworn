# Ability b4fe311a: <b>Deploy</b> - Add an #SELF# counter to this.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: AddAnSelfCounterToThis
ADD_COUNTER = CardModifierAbilityEffectTemplate(
    game_text='Add an #SELF# counter to this.',
    name='AddAnSelfCounterToThis',
    template_id=Ref('09fcc482-40cf-e465-e6e0-6b0f995e4f91'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='An'), card_counter_template_id=Ref('54bce52a-126b-7c61-4950-a2c88475ab4f')),
)

# Records name: BdeploybMAddAnSelfCounterToThis
DEPLOY_ADD_COUNTER = AbilityTemplate(
    ability_target_template_ids=[Ref('190a4d8c-7c2c-10d0-6429-99c5aeb0791f')],
    ability_template_id=Ref('b4fe311a-1a24-6d7b-bb69-acf2eaf9a51b'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='<b>Deploy</b> - Add an #SELF# counter to this.',
    name='BdeploybMAddAnSelfCounterToThis',
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
    variables=[AbilityConstant(default_value=1, name='An')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("09fcc482-40cf-e465-e6e0-6b0f995e4f91"))],
)
