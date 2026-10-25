# Ability a103b5d9: <b>Deploy</b> - Add two gift counters to this.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: AAAddTwoGiftCountersToThis
ADD_TWO_GIFT_COUNTERS = CardModifierAbilityEffectTemplate(
    game_text='Add two gift counters to this.',
    name='AAAddTwoGiftCountersToThis',
    template_id=Ref('eb01ebca-2559-ea28-cb70-de8a99409ee9'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='AATwo'), card_counter_template_id=Ref('cb5fa09b-f96b-4e2e-2bbc-e5da82b63751')),
)

# Records name: AABdeploybMAddTwoGiftCountersToThis
DEPLOY_ADD_TWO_GIFT_COUNTERS = AbilityTemplate(
    ability_target_template_ids=[Ref('296bf150-baef-8de3-6a78-067e187e99eb')],
    ability_template_id=Ref('a103b5d9-b232-1036-00c8-8949b02e2dc3'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='<b>Deploy</b> - Add two gift counters to this.',
    name='AABdeploybMAddTwoGiftCountersToThis',
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
    variables=[AbilityConstant(default_value=2, name='AATwo')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("eb01ebca-2559-ea28-cb70-de8a99409ee9"))],
)
