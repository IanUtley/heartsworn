# Ability 0be28435: <b>Deploy</b> - Add a junk counter to it for each artifact in your crypt.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: AddAJunkCounterToItForEachArtifactInYourCrypt
ADD_JUNK_COUNTER_IT_ARTIFACT = CardModifierAbilityEffectTemplate(
    game_text='Add a junk counter to it for each artifact in your crypt.',
    name='AddAJunkCounterToItForEachArtifactInYourCrypt',
    template_id=Ref('c7689865-73d6-a9c9-2bf5-1e0d0f8d6328'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='AForEachArtifactInYourCrypt'), card_counter_template_id=Ref('5277d4db-47bf-bc50-9c87-8c13139c8aec')),
)

# Records name: BdeploybMAddAJunkCounterToItForEachArtifactInYourCrypt
DEPLOY_ADD_JUNK_COUNTER_IT = AbilityTemplate(
    ability_target_template_ids=[Ref('3a3ce6ce-550f-ffd6-e32f-2bdbe372a552')],
    ability_template_id=Ref('0be28435-7b30-22aa-b9d2-4778dd9f75db'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='<b>Deploy</b> - Add a junk counter to it for each artifact in your crypt.',
    name='BdeploybMAddAJunkCounterToItForEachArtifactInYourCrypt',
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
    variables=[CardCountAbilityVariable(name='AForEachArtifactInYourCrypt', card_filter=AndCardFilter(target_filters=[IsArtifact(), IsControlledBy(), InZone(collection='Discard')]), collection_flags='Deck|Hand|Champions|Warzone|Discard|Void|CastSpells|Underground|Choosing', dont_recalculate=0, player_filter='MultiplePlayers')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("c7689865-73d6-a9c9-2bf5-1e0d0f8d6328"))],
)
