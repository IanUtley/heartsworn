# Ability 3491eb58: When a card enters a crypt from play, add a slag counter to this.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: AddASlagCounterToThis
ADD_SLAG_COUNTER = CardModifierAbilityEffectTemplate(
    game_text='add a slag counter to this.',
    name='AddASlagCounterToThis',
    template_id=Ref('d1836870-5e70-da5b-1948-6df11d398323'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='A'), card_counter_template_id=Ref('8e304937-e64d-21ea-377f-7e3f8e60a89e')),
)

# Records name: WhenACardEntersACryptFromPlayAddASlagCounterToThis
WHEN_CARD_ENTERS_CRYPT_PLAY = AbilityTemplate(
    ability_target_template_ids=[Ref('190a4d8c-7c2c-10d0-6429-99c5aeb0791f')],
    ability_template_id=Ref('3491eb58-77ca-385f-4646-1bb1f36af8ed'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='When a card enters a crypt from play, add a slag counter to this.',
    name='WhenACardEntersACryptFromPlayAddASlagCounterToThis',
    put_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_deck_target2=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_hand_target=Ref('00000000-0000-0000-0000-000000000000'),
    reveal_target=Ref('00000000-0000-0000-0000-000000000000'),
    sacrifice_target=Resource('00000000-0000-0000-0000-000000000000'),
    serialized_tac={'data': 'AgAAAAAA'},
    shuffle_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    trigger_collection_flags='Champions|Warzone',
    trigger_condition=AndTriggerCondition(conditions=[TriggerCardMatchesFilter(card_filter=IsType(card_type='Troop|BasicAction|Resource|Artifact|QuickAction|Constant|Bane|Choice')), TriggerCardEnteredZone(destination_collection='Discard', source_collection='Warzone')]),
    trigger_event_type={'m_InternalType': 'Game.Shared.Mechanics.CardEnteredZoneEvent'},
    variables=[AbilityConstant(default_value=1, name='A')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("d1836870-5e70-da5b-1948-6df11d398323"))],
)
