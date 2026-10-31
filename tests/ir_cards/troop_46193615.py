# Ability 46193615: When a troop you control dies, add a cinder counter to this.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: AddACinderCounterToThis
ADD_CINDER_COUNTER = CardModifierAbilityEffectTemplate(
    game_text='add a cinder counter to this.',
    name='AddACinderCounterToThis',
    template_id=Ref('a3144aa5-7f07-6835-1c14-afd47a4d1f51'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='A'), card_counter_template_id=Ref('99741b58-5a0b-7bac-d9be-8184bceecebe')),
)

# Records name: WhenATroopYouControlDiesAddACinderCounterToThis
WHEN_TROOP_DIES_ADD_CINDER = AbilityTemplate(
    ability_target_template_ids=[Ref('190a4d8c-7c2c-10d0-6429-99c5aeb0791f')],
    ability_template_id=Ref('46193615-a256-d53a-5675-936b256d2a35'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='When a troop you control dies, add a cinder counter to this.',
    name='WhenATroopYouControlDiesAddACinderCounterToThis',
    put_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_deck_target2=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_hand_target=Ref('00000000-0000-0000-0000-000000000000'),
    reveal_target=Ref('00000000-0000-0000-0000-000000000000'),
    sacrifice_target=Resource('00000000-0000-0000-0000-000000000000'),
    serialized_tac={'data': 'AgAAAAAA'},
    shuffle_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    trigger_collection_flags='Champions|Warzone',
    trigger_condition=AndTriggerCondition(conditions=[TriggerPlayerControlsAbilitySource(), TriggerCardMatchesFilter(card_filter=IsTroop()), TriggerCardEnteredZone(destination_collection='Discard', source_collection='Warzone')]),
    trigger_event_type={'m_InternalType': 'Game.Shared.Mechanics.CardEnteredZoneEvent'},
    uses_previous_state=1,
    variables=[AbilityConstant(default_value=1, name='A')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("a3144aa5-7f07-6835-1c14-afd47a4d1f51"))],
)
