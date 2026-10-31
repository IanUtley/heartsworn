# Ability 937f111f: At end of your turn, sacrifice this.
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

# Records name: AtEndOfYourTurnSacrificeThis
END_TURN_SACRIFICE = AbilityTemplate(
    ability_target_template_ids=[Ref('1d967ad5-c4ce-474f-1cac-b91ce7ea2074')],
    ability_template_id=Ref('937f111f-1bda-46c4-4df6-6a9aa72bcfe5'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='At end of your turn, sacrifice this.',
    name='AtEndOfYourTurnSacrificeThis',
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
