# Ability 0e5ff7c1: <b>Deploy</b> - Add a rocket counter to this for each artifact you control.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: AddARocketCounterToThisForEachArtifactYouControl
ADD_ROCKET_COUNTER_ARTIFACT = CardModifierAbilityEffectTemplate(
    game_text='Add a rocket counter to this for each artifact you control.',
    name='AddARocketCounterToThisForEachArtifactYouControl',
    template_id=Ref('15027a45-a204-f678-1fcb-974c4c50749c'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='AForEachArtifactYouControl'), card_counter_template_id=Ref('20385d18-e052-23a8-ea3a-4138425bdf08')),
)

# Records name: BdeploybMAddARocketCounterToThisForEachArtifactYouControl
DEPLOY_ADD_ROCKET_COUNTER_ARTIFACT = AbilityTemplate(
    ability_target_template_ids=[Ref('190a4d8c-7c2c-10d0-6429-99c5aeb0791f')],
    ability_template_id=Ref('0e5ff7c1-6b86-0f2a-c621-d1d836db4482'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='<b>Deploy</b> - Add a rocket counter to this for each artifact you control.',
    name='BdeploybMAddARocketCounterToThisForEachArtifactYouControl',
    put_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_deck_target2=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_hand_target=Ref('00000000-0000-0000-0000-000000000000'),
    reveal_target=Ref('00000000-0000-0000-0000-000000000000'),
    sacrifice_target=Resource('00000000-0000-0000-0000-000000000000'),
    serialized_tac={'data': 'AgCIOnpQAQAAAAAAAAA='},
    shuffle_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    trigger_collection_flags='Champions|Warzone',
    trigger_condition=AndTriggerCondition(conditions=[TriggerCardIsAbilitySource(), TriggerCardEnteredZone(destination_collection='Warzone')]),
    trigger_event_type={'m_InternalType': 'Game.Shared.Mechanics.CardEnteredZoneEvent'},
    variables=[CardCountAbilityVariable(name='AForEachArtifactYouControl', card_filter=AndCardFilter(target_filters=[IsArtifact(), IsControlledBy()]), collection_flags='Warzone', dont_recalculate=0, player_filter='MultiplePlayers')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("15027a45-a204-f678-1fcb-974c4c50749c"))],
)
