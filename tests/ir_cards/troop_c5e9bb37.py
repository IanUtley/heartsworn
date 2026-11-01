# Ability c5e9bb37: When a troop you control dies, add an ammo counter to this.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: AddAnAmmoCounterToThis
ADD_AMMO_COUNTER = CardModifierAbilityEffectTemplate(
    game_text='add an ammo counter to this.',
    name='AddAnAmmoCounterToThis',
    template_id=Ref('e0fd0568-87ac-199c-ae37-1fbd52591ce8'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='An'), card_counter_template_id=Ref('7eaa6452-c658-03d0-8e94-ff6947e176c9')),
)

# Records name: WhenATroopYouControlDiesAddAnAmmoCounterToThis
WHEN_TROOP_DIES_ADD_AMMO = AbilityTemplate(
    ability_target_template_ids=[Ref('190a4d8c-7c2c-10d0-6429-99c5aeb0791f')],
    ability_template_id=Ref('c5e9bb37-672b-8736-414a-84a564a61944'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='When a troop you control dies, add an ammo counter to this.',
    name='WhenATroopYouControlDiesAddAnAmmoCounterToThis',
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
    variables=[AbilityConstant(default_value=1, name='An')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("e0fd0568-87ac-199c-ae37-1fbd52591ce8"))],
)
