# Ability 595e4907: <b>Deploy</b> - Add a warp counter to this.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: AddAWarpCounterToThis
ADD_WARP_COUNTER = CardModifierAbilityEffectTemplate(
    game_text='Add a warp counter to this.',
    name='AddAWarpCounterToThis',
    template_id=Ref('da672dd6-1e80-f2cf-f05e-255b3f9fe56e'),
    modifier=CounterModifier(input_value=EffectInputVariable(input_variable_name='A'), card_counter_template_id=Ref('a2c9b894-b8d0-3099-0bba-9620e08abb59')),
)

# Records name: BdeploybMAddAWarpCounterToThis
DEPLOY_ADD_WARP_COUNTER = AbilityTemplate(
    ability_target_template_ids=[Ref('190a4d8c-7c2c-10d0-6429-99c5aeb0791f')],
    ability_template_id=Ref('595e4907-44ba-722e-0a92-4681d17b7f46'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='<b>Deploy</b> - Add a warp counter to this.',
    name='BdeploybMAddAWarpCounterToThis',
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
    variables=[AbilityConstant(default_value=1, name='A')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("da672dd6-1e80-f2cf-f05e-255b3f9fe56e"))],
)
