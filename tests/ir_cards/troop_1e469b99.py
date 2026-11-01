# Ability 1e469b99: At the start of your turn, this deals damage to each opposing champion for each constant you control.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: ThisDealsDamageToEachOpposingChampionForEachConstantYouControl
DEALS_DAMAGE_OPPOSING_CHAMPION_CONSTANT = CardModifierAbilityEffectTemplate(
    game_text='this deals damage to each opposing champion for each constant you control.',
    name='ThisDealsDamageToEachOpposingChampionForEachConstantYouControl',
    template_id=Ref('21bcbba6-661e-951b-0ac4-4a11681bebdf'),
    modifier=DamageModifier(input_value=EffectInputVariable(input_variable_name='ForEachConstantYouControl')),
)

# Records name: AtTheStartOfYourTurnThisDealsDamageToEachOpposingChampionForEachConstantYouControl
START_TURN_DEALS_DAMAGE_OPPOSING = AbilityTemplate(
    ability_target_template_ids=[Ref('c35dd13a-71e2-b244-847a-d887a0666210')],
    ability_template_id=Ref('1e469b99-e701-fe5d-ca1a-ca1aaa6f5a0f'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='At the start of your turn, this deals damage to each opposing champion for each constant you control.',
    name='AtTheStartOfYourTurnThisDealsDamageToEachOpposingChampionForEachConstantYouControl',
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
    variables=[CardCountAbilityVariable(name='ForEachConstantYouControl', card_filter=AndCardFilter(target_filters=[IsType(card_type='Constant'), IsControlledBy()]), collection_flags='Warzone', dont_recalculate=0, player_filter='MultiplePlayers')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("21bcbba6-661e-951b-0ac4-4a11681bebdf"))],
)
