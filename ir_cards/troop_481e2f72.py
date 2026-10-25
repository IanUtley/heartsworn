# Ability 481e2f72: [BLOOD][BLOOD][BLOOD]: At the start of your turn, this deals 1 damage to each opposing champion.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: ThisDeals1DamageToEachOpposingChampion
DEALS_DAMAGE_OPPOSING_CHAMPION = CardModifierAbilityEffectTemplate(
    game_text='this deals 1 damage to each opposing champion.',
    name='ThisDeals1DamageToEachOpposingChampion',
    template_id=Ref('9787fe47-08c1-9b8c-5184-2fd72f0f2a90'),
    modifier=DamageModifier(input_value=EffectInputVariable(input_variable_name='1')),
)

# Records name: BloodBloodBloodAtTheStartOfYourTurnThisDeals1DamageToEachOpposingChampion
START_TURN_DEALS_DAMAGE_OPPOSING = AbilityTemplate(
    ability_condition=AbilityControllerHasThresholdAbilityCondition(color_flags='Blood', comparison_op='GreaterThanOrEqual', required_quantity=3),
    ability_target_template_ids=[Ref('c35dd13a-71e2-b244-847a-d887a0666210')],
    ability_template_id=Ref('481e2f72-90f9-f104-9d5a-55d1468ae4c7'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='[BLOOD][BLOOD][BLOOD]: At the start of your turn, this deals 1 damage to each opposing champion.',
    name='BloodBloodBloodAtTheStartOfYourTurnThisDeals1DamageToEachOpposingChampion',
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
    ability_effect_list=[Clause(effect_template_id=Ref("9787fe47-08c1-9b8c-5184-2fd72f0f2a90"))],
)
