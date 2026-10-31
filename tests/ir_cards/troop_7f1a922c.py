# Ability 7f1a922c: <b>Deploy</b> - Add a gift counter to this.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: AAAddAGiftCounterToThis
ADD_GIFT_COUNTER = CardModifierAbilityEffectTemplate(
    game_text='Add a gift counter to this.',
    name='AAAddAGiftCounterToThis',
    template_id=Ref('dd517c01-e931-a955-113e-faf4ce162c72'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='AAA'), card_counter_template_id=Ref('cb5fa09b-f96b-4e2e-2bbc-e5da82b63751')),
)

# Records name: AABdeploybMAddAGiftCounterToThis
DEPLOY_ADD_GIFT_COUNTER = AbilityTemplate(
    ability_target_template_ids=[Ref('296bf150-baef-8de3-6a78-067e187e99eb')],
    ability_template_id=Ref('7f1a922c-8e43-b2e8-019e-0aaa6f185e46'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='<b>Deploy</b> - Add a gift counter to this.',
    name='AABdeploybMAddAGiftCounterToThis',
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
    variables=[AbilityConstant(default_value=1, name='AAA')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("dd517c01-e931-a955-113e-faf4ce162c72"))],
)
