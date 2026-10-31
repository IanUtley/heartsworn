# Ability c1d65cbc: <b>Deathcry</b> - This deals 10 damage to each opposing champion and troop.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: ThisDeals10DamageToEachOpposingChampionAndTroop
DEALS_DAMAGE_OPPOSING_CHAMPION_TROOP = CardModifierAbilityEffectTemplate(
    game_text='This deals 10 damage to each opposing champion and troop.',
    name='ThisDeals10DamageToEachOpposingChampionAndTroop',
    template_id=Ref('56402ac8-8a51-6cea-690a-7c1fe6c3e688'),
    modifier=DamageModifier(input_value=EffectInputVariable(input_variable_name='10')),
)

# Records name: BdeathcrybMThisDeals10DamageToEachOpposingChampionAndTroop
DEATHCRY_DEALS_DAMAGE_OPPOSING_CHAMPION = AbilityTemplate(
    ability_target_template_ids=[Ref('68534d77-74f9-80d3-137b-0d0af2416f66')],
    ability_template_id=Ref('c1d65cbc-91b3-cb52-f939-976cea865ed5'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='<b>Deathcry</b> - This deals 10 damage to each opposing champion and troop.',
    name='BdeathcrybMThisDeals10DamageToEachOpposingChampionAndTroop',
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
    variables=[AbilityConstant(default_value=10, name='10')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("56402ac8-8a51-6cea-690a-7c1fe6c3e688"))],
)
