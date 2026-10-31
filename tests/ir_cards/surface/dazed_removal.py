# Dazed removal (champion built-in): at the end of your turn, if you are Dazed, remove a dazed counter
# from this.

dazed = counter("Dazed")

you_are_dazed = count(champions, having=dazed) >= 1

remove_dazed = effect("remove a dazed counter from this.", remove_counter(this, dazed, amount=1))

dazed_removal = ability(
    "At the end of your turn, if you are <b>Dazed</b>, remove a dazed counter from this.",
    on=turn_end,
    when=and_(player_controls_source, you_are_dazed),
    does=[remove_dazed],
)
