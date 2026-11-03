"""Dynamic cost modifiers, data-driven from gamedata ability variables.

When a CardCreatedEvent trigger applies a permanent cost reduction whose size
depends on a CardCountAbilityVariable (e.g. Pterobot: "cost -1 for each Dwarf
and/or Robot you control"), the static leaf amount is 0 and the real value
comes from the ability's m_Variables tree:

    CardCountAbilityVariable  — count cards in m_CollectionFlags matching
                                m_CardFilter (controlled by the source)
    ExpressionAbilityVariable — "DwarfAndOrRobotYouControl * -1" → multiplier

We store the parsed formula on the card's game_cards.cost_mod_json and
evaluate it on demand, so the displayed cost and the charged cost stay
current as the board changes (matching the client's recalculated variables).
"""

import json
import re

from rules_port.card_view import CardView
from rules_port.targeting import ZONE_MAP, evaluate_card_filter


def formula_from_raw(raw_json):
    """Parse a CardModifier cardcost ability's m_Variables into a formula
    entry dict, or None when the modifier is static (no count variable)."""
    if not raw_json:
        return None
    try:
        rec = json.loads(raw_json)
    except Exception:
        return None
    if not isinstance(rec, dict):
        return None
    count_var = None
    for var in rec.get("m_Variables") or []:
        if str(var.get("_t", "")).split(".")[-1] == "CardCountAbilityVariable":
            count_var = var
            break
    if not count_var:
        return None
    count_name = count_var.get("m_Name", "")
    expr_text = None
    for var in rec.get("m_Variables") or []:
        if str(var.get("_t", "")).split(".")[-1] == "ExpressionAbilityVariable":
            et = var.get("m_ExpressionText") or ""
            if count_name and count_name in et:
                expr_text = et
                break
    mult = 1
    if expr_text:
        flat = expr_text.replace(" ", "")
        m = re.search(r'([A-Za-z_]\w*)\s*\*\s*(-?\d+)', flat)
        if m and m.group(1) == count_name:
            mult = int(m.group(2))
    return {
        "zones": [z for z in
                  (count_var.get("m_CollectionFlags") or "").split("|") if z],
        "filter": count_var.get("m_CardFilter") or {},
        "multiplier": mult,
    }


def _cards_in_zones(db, session_id, owner_uid, zones):
    from pvp_db import db_condition_cards_in_zones
    rows = db_condition_cards_in_zones(
        session_id, zones, user_id=owner_uid, conn=db)
    return [CardView.from_zone_row(r).as_shape("cost_filter") for r in rows]


def cost_mod_delta(db, session_id, card_uid, cost_mod_json):
    """Evaluate the card's stored formula entries against the current board."""
    try:
        entries = json.loads(cost_mod_json or "[]")
    except Exception:
        return 0
    if not entries:
        return 0
    from pvp_db import db_card_owner_id
    owner = db_card_owner_id(session_id, int(card_uid), conn=db) or 0
    total = 0
    for entry in entries:
        zones = [ZONE_MAP.get(z, z.lower()) for z in entry.get("zones") or []]
        if not zones:
            continue
        count = 0
        for card in _cards_in_zones(db, session_id, owner, zones):
            card["src_owner_id"] = owner
            if evaluate_card_filter(card, entry.get("filter") or {},
                                    int(card_uid)):
                count += 1
        total += count * int(entry.get("multiplier", 1))
    return total


