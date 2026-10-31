# Custom (not in the Records): at the start of your turn, this takes 2 damage.
#
# Card text needs no imports: the mechanics classes, Ref, Resource, Clause, the trigger types
# (TurnStartedEventType) and THIS_CARD_TARGET are in scope (gamedata/ir_python.py: prelude()).

# The card's own ids: the ability's and its effect's. Each is a record of its own.
ABILITY_ID = Ref("00000000-0000-0000-0000-00000000c0de")
EFFECT_ID = Ref("00000000-0000-0000-0000-00000000c0df")

TWO = EffectConstant(value=2)

DEAL_TWO = CardModifierAbilityEffectTemplate(
    name="CustomDealTwoToThis",
    template_id=EFFECT_ID,
    modifier=DamageModifier(input_value=TWO),
)

CUSTOM = AbilityTemplate(
    ability_template_id=ABILITY_ID,
    name="CustomAtTheStartOfYourTurnThisTakesTwoDamage",
    trigger_event_type=TurnStartedEventType,
    trigger_condition=TriggerPlayerControlsAbilitySource(),
    ability_target_template_ids=[THIS_CARD_TARGET],
    ability_effect_list=[Clause(effect_template_id=DEAL_TWO, target_template_index=0)],
)
