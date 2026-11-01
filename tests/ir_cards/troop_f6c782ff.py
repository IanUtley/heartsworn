# Ability f6c782ff: When a troop dies, add a soul counter to this.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: AddASoulCounterToThis
ADD_SOUL_COUNTER = CardModifierAbilityEffectTemplate(
    game_text='add a soul counter to this.',
    name='AddASoulCounterToThis',
    template_id=Ref('470a0055-9e6f-e379-f479-160b023c81b7'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='A'), card_counter_template_id=Ref('74fbeaac-9c56-e0ea-2785-572cf30dee18')),
)

# Records name: WhenATroopDiesAddASoulCounterToThis
WHEN_TROOP_DIES_ADD_SOUL = AbilityTemplate(
    ability_target_template_ids=[Ref('190a4d8c-7c2c-10d0-6429-99c5aeb0791f')],
    ability_template_id=Ref('f6c782ff-09e2-9bce-5efc-c303290b6446'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='When a troop dies, add a soul counter to this.',
    name='WhenATroopDiesAddASoulCounterToThis',
    put_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_deck_target2=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_hand_target=Ref('00000000-0000-0000-0000-000000000000'),
    reveal_target=Ref('00000000-0000-0000-0000-000000000000'),
    sacrifice_target=Resource('00000000-0000-0000-0000-000000000000'),
    serialized_tac={'data': 'AgAAAAAA'},
    shuffle_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    trigger_collection_flags='Champions|Warzone',
    trigger_condition=AndTriggerCondition(conditions=[TriggerCardMatchesFilter(card_filter=IsTroop()), TriggerCardEnteredZone(destination_collection='Discard', source_collection='Warzone')]),
    trigger_event_type={'m_InternalType': 'Game.Shared.Mechanics.CardEnteredZoneEvent'},
    uses_previous_state=1,
    variables=[AbilityConstant(default_value=1, name='A')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("470a0055-9e6f-e379-f479-160b023c81b7"))],
)
