# Ability 4639acad: At the start of your turn, this deals damage to each opposing champion for each constant you control.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: AAThisDealsDamageToEachOpposingChampionForEachConstantYouControl
DEALS_DAMAGE_OPPOSING_CHAMPION_CONSTANT = CardModifierAbilityEffectTemplate(
    game_text='this deals damage to each opposing champion for each constant you control.',
    name='AAThisDealsDamageToEachOpposingChampionForEachConstantYouControl',
    template_id=Ref('c33e5a46-97fb-3c35-47a4-b2b14ec18a89'),
    modifier=DamageModifier(input_value=EffectInputVariable(input_variable_name='AAForEachConstantYouControl')),
)

# Records name: AAAtTheStartOfYourTurnThisDealsDamageToEachOpposingChampionForEachConstantYouControl
START_TURN_DEALS_DAMAGE_OPPOSING = AbilityTemplate(
    ability_target_template_ids=[Ref('4d7134a2-2b9c-1596-fc2a-511acb92354e')],
    ability_template_id=Ref('4639acad-889f-9d77-ba70-8f24a181ee3e'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='At the start of your turn, this deals damage to each opposing champion for each constant you control.',
    name='AAAtTheStartOfYourTurnThisDealsDamageToEachOpposingChampionForEachConstantYouControl',
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
    variables=[CardCountAbilityVariable(name='AAForEachConstantYouControl', card_filter=AndCardFilter(target_filters=[IsType(card_type='Constant'), IsControlledBy()]), collection_flags='Warzone', dont_recalculate=0, player_filter='MultiplePlayers')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("c33e5a46-97fb-3c35-47a4-b2b14ec18a89"))],
)
