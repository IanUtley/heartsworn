# Vulnerable removal (champion built-in): at the end of your turn, remove a vulnerable counter from your
# Vulnerable troops in all zones or your Vulnerable champion.

vulnerable = counter("Vulnerable")

your_vulnerable_troops_or_champion = library_target(
    "your Vulnerable troops in all zones or your Vulnerable champion")

remove_vulnerable = effect(
    "remove a vulnerable counter from your <b>Vulnerable</b> troops in all zones or your <b>Vulnerable</b> champion.",
    remove_counter(your_vulnerable_troops_or_champion, vulnerable, amount=1),
)

vulnerable_removal = ability(
    "At the end of your turn, remove a vulnerable counter from your <b>Vulnerable</b> troops in all zones or your <b>Vulnerable</b> champion.",
    on=turn_end,
    when=player_controls_source,
    does=[remove_vulnerable],
)
