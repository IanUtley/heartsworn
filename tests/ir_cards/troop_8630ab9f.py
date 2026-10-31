# Ability 8630ab9f: At the start of your turn, this deals 1 damage to each opposing champion for each constant you control.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: ThisDeals1DamageToEachOpposingChampionForEachConstantYouControl
DEALS_DAMAGE_OPPOSING_CHAMPION_CONSTANT = CardModifierAbilityEffectTemplate(
    game_text='this deals 1 damage to each opposing champion for each constant you control.',
    name='ThisDeals1DamageToEachOpposingChampionForEachConstantYouControl',
    template_id=Ref('4de6ed10-453d-cf1c-ecfd-db4a9ba81774'),
    modifier=DamageModifier(input_value=EffectInputVariable(input_variable_name='1ForEachConstantYouControl')),
)

# Records name: AtTheStartOfYourTurnThisDeals1DamageToEachOpposingChampionForEachConstantYouControl
START_TURN_DEALS_DAMAGE_OPPOSING = AbilityTemplate(
    ability_target_template_ids=[Ref('c35dd13a-71e2-b244-847a-d887a0666210')],
    ability_template_id=Ref('8630ab9f-8a3c-2d38-296c-d4c711123a51'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='At the start of your turn, this deals 1 damage to each opposing champion for each constant you control.',
    name='AtTheStartOfYourTurnThisDeals1DamageToEachOpposingChampionForEachConstantYouControl',
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
    variables=[CardCountAbilityVariable(name='1ForEachConstantYouControl', card_filter=AndCardFilter(target_filters=[IsType(card_type='Constant'), IsControlledBy()]), collection_flags='Warzone', dont_recalculate=0, player_filter='MultiplePlayers')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("4de6ed10-453d-cf1c-ecfd-db4a9ba81774"))],
)
