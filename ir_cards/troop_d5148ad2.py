# Ability d5148ad2: When a constant enters play under your control, this deals 2 damage to each opposing champion.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: ThisDeals2DamageToEachOpposingChampion
DEALS_DAMAGE_OPPOSING_CHAMPION = CardModifierAbilityEffectTemplate(
    game_text='this deals 2 damage to each opposing champion.',
    name='ThisDeals2DamageToEachOpposingChampion',
    template_id=Ref('afeb8d1a-b1ca-49f9-161f-0cc0b38f2b7a'),
    modifier=DamageModifier(input_value=EffectInputVariable(input_variable_name='2')),
)

# Records name: WhenAConstantEntersPlayUnderYourControlThisDeals2DamageToEachOpposingChampion
WHEN_CONSTANT_ENTERS_PLAY_DEALS = AbilityTemplate(
    ability_target_template_ids=[Ref('c35dd13a-71e2-b244-847a-d887a0666210')],
    ability_template_id=Ref('d5148ad2-48e1-fe09-5c82-b47b5de7f17d'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='When a constant enters play under your control, this deals 2 damage to each opposing champion.',
    name='WhenAConstantEntersPlayUnderYourControlThisDeals2DamageToEachOpposingChampion',
    put_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_deck_target2=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_hand_target=Ref('00000000-0000-0000-0000-000000000000'),
    reveal_target=Ref('00000000-0000-0000-0000-000000000000'),
    sacrifice_target=Resource('00000000-0000-0000-0000-000000000000'),
    serialized_tac={'data': 'AgAAAAAA'},
    shuffle_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    trigger_collection_flags='Champions|Warzone',
    trigger_condition=AndTriggerCondition(conditions=[TriggerPlayerControlsAbilitySource(), TriggerCardMatchesFilter(card_filter=IsType(card_type='Constant')), TriggerCardEnteredZone(destination_collection='Warzone')]),
    trigger_event_type={'m_InternalType': 'Game.Shared.Mechanics.CardEnteredZoneEvent'},
    variables=[AbilityConstant(default_value=2, name='2')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("afeb8d1a-b1ca-49f9-161f-0cc0b38f2b7a"))],
)
