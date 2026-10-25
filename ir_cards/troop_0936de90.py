# Ability 0936de90: At the end of your turn, sacrifice this.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: sacrifice
SACRIFICE_TROOP = SacrificeCardAbilityEffectTemplate(
    game_text='sacrifice a troop.',
    name='sacrifice',
    template_id=Ref('351f1326-c9ff-7858-cdab-c50f25e601c6'),
)

# Records name: AAAtTheEndOfYourTurnSacrificeThis
END_TURN_SACRIFICE = AbilityTemplate(
    ability_target_template_ids=[Ref('e2fc01c6-ff89-1f52-c5b0-ac0be00bfe67')],
    ability_template_id=Ref('0936de90-563f-ede3-fc30-40cfd0b0a0b2'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='At the end of your turn, sacrifice this.',
    name='AAAtTheEndOfYourTurnSacrificeThis',
    put_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_deck_target2=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_hand_target=Ref('00000000-0000-0000-0000-000000000000'),
    reveal_target=Ref('00000000-0000-0000-0000-000000000000'),
    sacrifice_target=Resource('00000000-0000-0000-0000-000000000000'),
    serialized_tac={'data': 'AgAAAAAA'},
    shuffle_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    trigger_collection_flags='Champions|Warzone',
    trigger_condition=TriggerPlayerControlsAbilitySource(),
    trigger_event_type={'m_InternalType': 'Game.Shared.Mechanics.TurnEndedEvent'},
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("351f1326-c9ff-7858-cdab-c50f25e601c6"))],
)
