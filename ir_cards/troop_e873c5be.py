# Ability e873c5be: At the end of your turn, this deals 3 damage to each opposing champion.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: ThisDeals3DamageToEachOpposingChampion
DEALS_DAMAGE_OPPOSING_CHAMPION = CardModifierAbilityEffectTemplate(
    game_text='This deals 3 damage to each opposing champion.',
    name='ThisDeals3DamageToEachOpposingChampion',
    template_id=Ref('1f5415e5-064f-a43e-86e6-99c718425c2e'),
    modifier=DamageModifier(input_value=EffectInputVariable(input_variable_name='3')),
)

# Records name: AtTheEndOfYourTurnThisDeals3DamageToEachOpposingChampion
END_TURN_DEALS_DAMAGE_OPPOSING = AbilityTemplate(
    ability_target_template_ids=[Ref('c35dd13a-71e2-b244-847a-d887a0666210')],
    ability_template_id=Ref('e873c5be-75e8-fe2b-016c-e04336ad7b8a'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='At the end of your turn, this deals 3 damage to each opposing champion.',
    name='AtTheEndOfYourTurnThisDeals3DamageToEachOpposingChampion',
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
    variables=[AbilityConstant(default_value=3, name='3')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("1f5415e5-064f-a43e-86e6-99c718425c2e"))],
)
