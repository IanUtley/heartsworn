# Ability 34729a26: At the start of your turn, deal 1 damage to each opposing champion.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: Deal1DamageToEachOpposingChampion
DEAL_DAMAGE_OPPOSING_CHAMPION = CardModifierAbilityEffectTemplate(
    game_text='deal 1 damage to each opposing champion.',
    name='Deal1DamageToEachOpposingChampion',
    template_id=Ref('42866e4e-8dc7-c01f-7c32-2dec75527f4e'),
    modifier=DamageModifier(input_value=EffectInputVariable(input_variable_name='1')),
)

# Records name: AtTheStartOfYourTurnDeal1DamageToEachOpposingChampion
START_TURN_DEAL_DAMAGE_OPPOSING = AbilityTemplate(
    ability_target_template_ids=[Ref('c35dd13a-71e2-b244-847a-d887a0666210')],
    ability_template_id=Ref('34729a26-bb5c-8048-513f-c87a17a37850'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='At the start of your turn, deal 1 damage to each opposing champion.',
    name='AtTheStartOfYourTurnDeal1DamageToEachOpposingChampion',
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
    ability_effect_list=[Clause(effect_template_id=Ref("42866e4e-8dc7-c01f-7c32-2dec75527f4e"))],
)
