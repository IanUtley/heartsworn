# Ability a97b58f0: <b>Deploy</b> - Add a party counter to this for each Fae you control.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: AddAPartyCounterToThisForEachFaeYouControl
ADD_PARTY_COUNTER_FAE = CardModifierAbilityEffectTemplate(
    game_text='Add a party counter to this for each Fae you control.',
    name='AddAPartyCounterToThisForEachFaeYouControl',
    template_id=Ref('c3181297-e06b-b43c-613a-02ac0bcf7825'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='AForEachFaeYouControl'), card_counter_template_id=Ref('5d477d5e-5196-5f39-6e9f-d29b2e779ade')),
)

# Records name: BdeploybMAddAPartyCounterToThisForEachFaeYouControl
DEPLOY_ADD_PARTY_COUNTER_FAE = AbilityTemplate(
    ability_target_template_ids=[Ref('190a4d8c-7c2c-10d0-6429-99c5aeb0791f')],
    ability_template_id=Ref('a97b58f0-2750-d549-bb9f-55e270bbb721'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='<b>Deploy</b> - Add a party counter to this for each Fae you control.',
    name='BdeploybMAddAPartyCounterToThisForEachFaeYouControl',
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
    variables=[CardCountAbilityVariable(name='AForEachFaeYouControl', card_filter=AndCardFilter(target_filters=[IsSubType(sub_type='Fae'), IsControlledBy()]), collection_flags='Warzone', dont_recalculate=0, player_filter='MultiplePlayers')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("c3181297-e06b-b43c-613a-02ac0bcf7825"))],
)
