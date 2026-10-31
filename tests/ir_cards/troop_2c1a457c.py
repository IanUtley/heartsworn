# Ability 2c1a457c: At the start of your turn, sacrifice this. Then this deals 1 damage to each troop.
#
# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by
# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses
# reference them by GUID.

# Records name: sacrifice
SACRIFICE_TROOP = SacrificeCardAbilityEffectTemplate(
    game_text='sacrifice a troop.',
    name='sacrifice',
    template_id=Ref('351f1326-c9ff-7858-cdab-c50f25e601c6'),
)
# Records name: ThisDeals1DamageToEachTroop
DEALS_DAMAGE_TROOP = CardModifierAbilityEffectTemplate(
    game_text='this deals 1 damage to each troop.',
    name='ThisDeals1DamageToEachTroop',
    template_id=Ref('29dc6ad0-97f1-f4dc-e2ca-75b5ffceb67d'),
    modifier=DamageModifier(input_value=EffectInputVariable(input_variable_name='1')),
)

# Records name: AtTheStartOfYourTurnSacrificeThisThenThisDeals1DamageToEachTroop
START_TURN_SACRIFICE_THEN_DEALS = AbilityTemplate(
    ability_target_template_ids=[Ref('1d967ad5-c4ce-474f-1cac-b91ce7ea2074'), Ref('9c566d93-5fad-e85b-5150-c29be35ee7f5')],
    ability_template_id=Ref('2c1a457c-997a-6ed5-ca15-3920830c37bc'),
    discard_target=Ref('00000000-0000-0000-0000-000000000000'),
    exhaust_target=Ref('00000000-0000-0000-0000-000000000000'),
    game_text='At the start of your turn, sacrifice this. Then this deals 1 damage to each troop.',
    name='AtTheStartOfYourTurnSacrificeThisThenThisDeals1DamageToEachTroop',
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
    variables=[AbilityConstant(default_value=1, name='1')],
    void_target=Ref('00000000-0000-0000-0000-000000000000'),
    _deprecated={'m_AICustomAbilityEvaluator': []},
    ability_effect_list=[Clause(effect_template_id=Ref("351f1326-c9ff-7858-cdab-c50f25e601c6")), Clause(effect_template_id=Ref("29dc6ad0-97f1-f4dc-e2ca-75b5ffceb67d"), target_template_index=1, effect_instance_id=1, effect_group_id=2)],
)
