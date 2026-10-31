# Ability d6899580: <b>Deploy</b> - Add a destiny counter to this.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: AddADestinyCounterToThis
ADD_DESTINY_COUNTER = CardModifierAbilityEffectTemplate(
    game_text='Add a destiny counter to this.',
    name='AddADestinyCounterToThis',
    template_id=Ref('7ef50ed6-d929-63b3-1555-703009bbbb06'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='A'), card_counter_template_id=Ref('85e921da-b9ae-234f-0d73-e25b0a28e42a')),
)

# Records name: BdeploybMAddADestinyCounterToThis
DEPLOY_ADD_DESTINY_COUNTER = AbilityTemplate(
    ability_target_template_ids=[Ref('190a4d8c-7c2c-10d0-6429-99c5aeb0791f')],
    ability_template_id=Ref('d6899580-c73d-a29e-f97c-cfa86f07e762'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='<b>Deploy</b> - Add a destiny counter to this.',
    name='BdeploybMAddADestinyCounterToThis',
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
    variables=[AbilityConstant(default_value=1, name='A')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("7ef50ed6-d929-63b3-1555-703009bbbb06"))],
)
