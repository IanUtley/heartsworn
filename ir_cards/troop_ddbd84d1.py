# Ability ddbd84d1: <b>Deploy</b> - Secretly add a roar counter to this for each Dinosaur in your hand.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: AddARoarCounterToThisForEachDinosaurInYourHand
ADD_ROAR_COUNTER_DINOSAUR_HAND = CardModifierAbilityEffectTemplate(
    game_text='add a roar counter to this for each Dinosaur in your hand.',
    name='AddARoarCounterToThisForEachDinosaurInYourHand',
    template_id=Ref('80670585-fd5e-d092-410b-6363f22ac95e'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='AForEachDinosaurInYourHand'), card_counter_template_id=Ref('b056a29b-b013-1915-86d0-fe1cab4f168b')),
)

# Records name: BdeploybMSecretlyAddARoarCounterToThisForEachDinosaurInYourHand
DEPLOY_SECRETLY_ADD_ROAR_COUNTER = AbilityTemplate(
    ability_target_template_ids=[Ref('190a4d8c-7c2c-10d0-6429-99c5aeb0791f')],
    ability_template_id=Ref('ddbd84d1-2610-0ee9-5666-dd223d4dd12c'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='<b>Deploy</b> - Secretly add a roar counter to this for each Dinosaur in your hand.',
    name='BdeploybMSecretlyAddARoarCounterToThisForEachDinosaurInYourHand',
    put_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_deck_target2=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_hand_target=Ref('00000000-0000-0000-0000-000000000000'),
    reveal_target=Ref('00000000-0000-0000-0000-000000000000'),
    sacrifice_target=Resource('00000000-0000-0000-0000-000000000000'),
    serialized_tac={'data': 'AgCIOnpQAQAAAKxHaR4BAAAAAAAAAA=='},
    shuffle_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    trigger_collection_flags='Champions|Warzone',
    trigger_condition=AndTriggerCondition(conditions=[TriggerCardIsAbilitySource(), TriggerCardEnteredZone(destination_collection='Warzone')]),
    trigger_event_type={'m_InternalType': 'Game.Shared.Mechanics.CardEnteredZoneEvent'},
    variables=[CardCountAbilityVariable(name='AForEachDinosaurInYourHand', card_filter=AndCardFilter(target_filters=[IsSubType(sub_type='Dinosaur'), IsControlledBy(), InZone(collection='Hand')]), collection_flags='Deck|Hand|Champions|Warzone|Discard|Void|CastSpells|Underground|Choosing', dont_recalculate=0, player_filter='MultiplePlayers')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("80670585-fd5e-d092-410b-6363f22ac95e"))],
)
