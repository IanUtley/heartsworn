# Ability e087f393: <b>Deathcry</b> - Deal 3 damage to all opposing champions and troops.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: Deal3DamageToAllOpposingChampionsAndTroops
DEAL_DAMAGE_ALL_OPPOSING_CHAMPIONS = CardModifierAbilityEffectTemplate(
    game_text='Deal 3 damage to all opposing champions and troops.',
    name='Deal3DamageToAllOpposingChampionsAndTroops',
    template_id=Ref('db6baa54-2f85-4428-2e83-62f90fbf285a'),
    modifier=DamageModifier(input_value=EffectInputVariable(input_variable_name='3')),
)

# Records name: BdeathcrybMDeal3DamageToAllOpposingChampionsAndTroops
DEATHCRY_DEAL_DAMAGE_ALL_OPPOSING = AbilityTemplate(
    ability_target_template_ids=[Ref('d5956664-ea0d-3514-c498-625ace395643')],
    ability_template_id=Ref('e087f393-4dd8-1b1a-c703-1f01b7a57e78'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='<b>Deathcry</b> - Deal 3 damage to all opposing champions and troops.',
    name='BdeathcrybMDeal3DamageToAllOpposingChampionsAndTroops',
    put_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_deck_target2=Ref('00000000-0000-0000-0000-000000000000'),
    put_into_hand_target=Ref('00000000-0000-0000-0000-000000000000'),
    reveal_target=Ref('00000000-0000-0000-0000-000000000000'),
    sacrifice_target=Resource('00000000-0000-0000-0000-000000000000'),
    serialized_tac={'data': 'AgDN0BHXAQAAAAAAAAA='},
    shuffle_into_deck_target=Ref('00000000-0000-0000-0000-000000000000'),
    trigger_collection_flags='Champions|Warzone',
    trigger_condition=AndTriggerCondition(conditions=[TriggerCardIsAbilitySource(), TriggerCardEnteredZone(destination_collection='Discard', source_collection='Warzone')]),
    trigger_event_type={'m_InternalType': 'Game.Shared.Mechanics.CardEnteredZoneEvent'},
    uses_previous_state=1,
    variables=[AbilityConstant(default_value=3, name='3')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("db6baa54-2f85-4428-2e83-62f90fbf285a"))],
)
