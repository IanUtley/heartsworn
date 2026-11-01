"""Parity helpers for the interpreter's abilities: the reads the native path makes that the interpreter must match."""
from typing import Any

from rules_port.damage_effects import _int_attr_values
from rules_port.statistics import tac_stat


def int_attr_value(context: Any, card_uid: int, owner: int, attribute: str) -> int:
    """The left side of an IntAttrFilter, read as the native filter reads it: a card's own attribute, a
    player's attribute on the player's champion ("You>..."), or a turn statistic ("CardStatsThisTurn>...")."""
    parts = [part for part in str(attribute or "").split(">") if part]
    uid = int(card_uid)
    key = str(attribute or "")
    if parts and parts[0].lower().startswith("you"):
        champions = context.bstate.get("champ_map") or {}
        uid = int(champions.get(int(owner), champions.get(str(int(owner)))))
        key = ">".join(parts[1:])
        parts = parts[1:]
    if len(parts) >= 2 and parts[0] == "CardStatsThisTurn":
        return int(tac_stat(context.bstate, "cards", uid, parts[0], ">".join(parts[1:]), default=0) or 0)
    return int(_int_attr_values(context, uid).get(key, 0))
