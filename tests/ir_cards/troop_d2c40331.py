# Ability d2c40331: <b>Deathcry</b> - This deals 1 damage to each opposing champion and troop.
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

# Records name: BdeathcrybMThisDeals1DamageToEachOpposingChampionAndTroop
DEATHCRY_DEALS_DAMAGE_OPPOSING_CHAMPION = AbilityTemplate(
    ability_target_template_ids=[Ref('68534d77-74f9-80d3-137b-0d0af2416f66')],
    ability_template_id=Ref('d2c40331-d6b5-3021-e234-262f1baadd04'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='<b>Deathcry</b> - This deals 1 damage to each opposing champion and troop.',
    name='BdeathcrybMThisDeals1DamageToEachOpposingChampionAndTroop',
    put_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_deck_target2=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_hand_target=Ref('00000000-0000-0000-0000-000000000000'),
    reveal_target=Ref('00000000-0000-0000-0000-000000000000'),
    sacrifice_target=Resource('00000000-0000-0000-0000-000000000000'),
    serialized_tac={'data': 'AgDN0BHXAQAAAAAAAAA='},
    shuffle_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    trigger_collection_flags='Champions|Warzone',
    trigger_condition=AndTriggerCondition(conditions=[TriggerCardIsAbilitySource(), TriggerCardEnteredZone(destination_collection='Discard', source_collection='Warzone')]),
    trigger_event_type={'m_InternalType': 'Game.Shared.Mechanics.CardEnteredZoneEvent'},
    uses_previous_state=1,
    variables=[AbilityConstant(default_value=1, name='1')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("23cf7d4a-eb05-9634-378f-062c291479b7"))],
)
