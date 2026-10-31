# Ability c82703d3: At end of turn, remove all rocket counters from this.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: RemoveAllRocketCountersFromThis
REMOVE_ALL_ROCKET_COUNTERS = CardModifierAbilityEffectTemplate(
    game_text='remove all rocket counters from this.',
    name='RemoveAllRocketCountersFromThis',
    template_id=Ref('4d1347ad-da74-d27e-51f3-7351e8d12f9e'),
    modifier=CounterModifier(input_value=EffectConstant(), card_counter_template_id=Ref('20385d18-e052-23a8-ea3a-4138425bdf08'), operation='Set'),
)

# Records name: AtEndOfTurnRemoveAllRocketCountersFromThis
END_TURN_REMOVE_ALL_ROCKET = AbilityTemplate(
    ability_target_template_ids=[Ref('190a4d8c-7c2c-10d0-6429-99c5aeb0791f')],
    ability_template_id=Ref('c82703d3-ff43-48d0-763a-e071598785b0'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='At end of turn, remove all rocket counters from this.',
    name='AtEndOfTurnRemoveAllRocketCountersFromThis',
    put_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_deck_target2=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_hand_target=Ref('00000000-0000-0000-0000-000000000000'),
    reveal_target=Ref('00000000-0000-0000-0000-000000000000'),
    sacrifice_target=Resource('00000000-0000-0000-0000-000000000000'),
    serialized_tac={'data': 'AgAAAAAA'},
    shuffle_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    trigger_collection_flags='Champions|Warzone',
    trigger_event_type={'m_InternalType': 'Game.Shared.Mechanics.TurnEndedEvent'},
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("4d1347ad-da74-d27e-51f3-7351e8d12f9e"))],
)
