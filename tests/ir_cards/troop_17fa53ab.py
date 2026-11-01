# Ability 17fa53ab: At the start of your turn, add a sculpture counter to this card for each Cleric you control.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: AddASculptureCounterToThisCardForEachClericYouControl
ADD_SCULPTURE_COUNTER_CARD_CLERIC = CardModifierAbilityEffectTemplate(
    game_text='add a sculpture counter to this card for each Cleric you control.',
    name='AddASculptureCounterToThisCardForEachClericYouControl',
    template_id=Ref('4bd252e0-79c2-69a5-545a-2378e2712849'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='AForEachClericYouControl'), card_counter_template_id=Ref('f4cbd789-8c55-32a3-4837-bbab12dec222')),
)

# Records name: AtTheStartOfYourTurnAddASculptureCounterToThisCardForEachClericYouControl
START_TURN_ADD_SCULPTURE_COUNTER = AbilityTemplate(
    ability_target_template_ids=[Ref('c8954b15-b383-f3b9-24d0-339a472899eb')],
    ability_template_id=Ref('17fa53ab-db15-0b8b-e0b8-4e234af97f2e'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='At the start of your turn, add a sculpture counter to this card for each Cleric you control.',
    name='AtTheStartOfYourTurnAddASculptureCounterToThisCardForEachClericYouControl',
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
    variables=[CardCountAbilityVariable(name='AForEachClericYouControl', card_filter=AndCardFilter(target_filters=[IsSubType(sub_type='Cleric'), IsControlledBy()]), collection_flags='Warzone', dont_recalculate=0, player_filter='MultiplePlayers')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("4bd252e0-79c2-69a5-545a-2378e2712849"))],
)
