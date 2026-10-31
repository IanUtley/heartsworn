# Ability f875adf6: At the start of your turn, this deals 1 damage to each opposing champion and troop.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: ThisDeals1DamageToEachOpposingChampionAndTroop
DEALS_DAMAGE_OPPOSING_CHAMPION_TROOP = CardModifierAbilityEffectTemplate(
    game_text='This deals 1 damage to each opposing champion and troop.',
    name='ThisDeals1DamageToEachOpposingChampionAndTroop',
    template_id=Ref('23cf7d4a-eb05-9634-378f-062c291479b7'),
    modifier=DamageModifier(input_value=EffectInputVariable(input_variable_name='1')),
)

# Records name: AtTheStartOfYourTurnThisDeals1DamageToEachOpposingChampionAndTroop
START_TURN_DEALS_DAMAGE_OPPOSING = AbilityTemplate(
    ability_target_template_ids=[Ref('68534d77-74f9-80d3-137b-0d0af2416f66')],
    ability_template_id=Ref('f875adf6-11e2-c6e4-a08f-9ef79d10b460'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='At the start of your turn, this deals 1 damage to each opposing champion and troop.',
    name='AtTheStartOfYourTurnThisDeals1DamageToEachOpposingChampionAndTroop',
    put_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_deck_target2=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_hand_target=Ref('00000000-0000-0000-0000-000000000000'),
    reveal_target=Ref('00000000-0000-0000-0000-000000000000'),
    sacrifice_target=Resource('00000000-0000-0000-0000-000000000000'),
    serialized_tac={'data': 'AgAAAAAA'},
    shuffle_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    trigger_collection_flags='Champions|Warzone',
    trigger_condition=TriggerPlayerControlsAbilitySource(),
    trigger_event_type={'m_InternalType': 'Game.Shared.Mechanics.TurnStartedEvent'},
    variables=[AbilityConstant(default_value=1, name='1')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("23cf7d4a-eb05-9634-378f-062c291479b7"))],
)
