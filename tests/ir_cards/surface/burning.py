# Burning (champion built-in): at the end of your turn, if you are Burning, deal damage to this equal to the
# number of burning counters on this, then remove a burning counter from this.

burning = counter("Burning")

you_are_burning = count(champions, having=burning) >= 1

deal_burning_damage = effect(
    "deal damage to this equal to the number of burning counters on this.",
    damage(this, amount=counter_value(this, burning)),
)

remove_burning = effect("Remove a burning counter from this.", remove_counter(this, burning, amount=1))

burning_end_of_turn = ability(
    "At the end of your turn, if you are <b>Burning</b>, deal damage to this equal to the number of burning counters on this. Remove a burning counter from this.",
    on=turn_end,
    when=and_(player_controls_source, you_are_burning),
    does=[deal_burning_damage, remove_burning],
)
