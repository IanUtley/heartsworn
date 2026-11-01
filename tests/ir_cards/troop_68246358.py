# Ability 68246358: At the start of your turn, add a sculpture counter to this card for each Cleric you control.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: AAAddASculptureCounterToThisCardForEachClericYouControl
ADD_SCULPTURE_COUNTER_CARD_CLERIC = CardModifierAbilityEffectTemplate(
    game_text='add a sculpture counter to this card for each Cleric you control.',
    name='AAAddASculptureCounterToThisCardForEachClericYouControl',
    template_id=Ref('5baacc62-e50e-9287-e51e-f3855ce05a0b'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='AAAForEachClericYouControl'), card_counter_template_id=Ref('8563e30e-ed6a-f543-1f15-11164f4ba5bc')),
)

# Records name: AAAtTheStartOfYourTurnAddASculptureCounterToThisCardForEachClericYouControl
START_TURN_ADD_SCULPTURE_COUNTER = AbilityTemplate(
    ability_target_template_ids=[Ref('dfb7442a-77fe-b4fa-ed01-499c70d10c21')],
    ability_template_id=Ref('68246358-1ae4-b056-caa4-def8f85d1300'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='At the start of your turn, add a sculpture counter to this card for each Cleric you control.',
    name='AAAtTheStartOfYourTurnAddASculptureCounterToThisCardForEachClericYouControl',
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
    variables=[CardCountAbilityVariable(name='AAAForEachClericYouControl', card_filter=AndCardFilter(target_filters=[IsSubType(sub_type='Cleric'), IsControlledBy()]), collection_flags='Warzone', dont_recalculate=0, player_filter='MultiplePlayers')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("5baacc62-e50e-9287-e51e-f3855ce05a0b"))],
)
