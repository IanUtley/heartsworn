# Soulcursed (champion built-in): when a troop you control with Soulcursed dies, sacrifice each troop you
# control with Soulcursed.

each_soulcursed_troop_you_control = library_target("each troop you control with Soulcursed you control")

sacrifice_each_soulcursed_troop = sacrifice(each_soulcursed_troop_you_control)

soulcursed = ability(
    "When a troop you control with <b>Soulcursed</b> dies, sacrifice each troop you control with <b>Soulcursed</b>.",
    on=card_entered_zone,
    when=and_(
        player_controls_source,
        card_matches(troop_with("Soulcursed")),
        card_enters(from_zone=warzone, to_zone=discard),
    ),
    does=[sacrifice_each_soulcursed_troop],
)
