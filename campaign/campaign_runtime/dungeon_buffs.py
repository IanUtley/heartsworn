"""Stat bonuses that last until the end of a dungeon run, as a continuous effect on the opposing troops.

A PvE battle inside a dungeon records each "until the end of the dungeon" stat bonus that its opposing troops take.
The bonus is kept in two places: the battle state (so the continuous static scan adds it to every opposing troop on
the warzone, including troops that enter later, for both combat and the client's display) and the champion's
campaign-wide progress (so the next battle of the same run starts with it). Leaving the dungeon for an area, or a new
run starting, clears it (the zone code does that on the campaign part it stores).
"""

from __future__ import annotations

from typing import Any

import pve_db
from campaign_runtime import progress

# The key of the bonus list in the campaign-wide progress and in the battle state.
BUFFS_KEY = "dungeon_opposing_buffs"
DUNGEON = "DUNGEON"
TROOP = "Troop"
WARZONE = "warzone"


def campaign_champion(db, camp_id: int) -> tuple[int, str] | None:
    """``(champion_id, campaign_type)`` for a campaign row, or None."""
    row = pve_db.db_campaign_protocol_row(int(camp_id), conn=db)
    if not row:
        return None
    return int(row[0] or 0), str(row[2] or "").upper()


def camp_id_from_session(session_name: str) -> int | None:
    """The campaign id a battle session belongs to (``camp_<id>_...``), or None."""
    name = str(session_name or "")
    if not name.startswith("camp_"):
        return None
    try:
        return int(name[5:].split("_", 1)[0])
    except (TypeError, ValueError):
        return None


def _items(bonus_list) -> list[dict[str, Any]]:
    return [item for item in (bonus_list or ()) if isinstance(item, dict)]


def record(db, champion_id: int, key: str, attack: int, defense: int) -> bool:
    """Add one bonus to the champion's dungeon run. A key already recorded is not added twice."""
    with progress.champion_lock(champion_id):
        loaded = progress.load(db, champion_id)
        if loaded is None:
            return False
        campaign, zones = loaded
        buffs = campaign.setdefault(BUFFS_KEY, [])
        if any(item.get("key") == key for item in buffs):
            return False
        buffs.append({"key": key, "attack": int(attack or 0), "defense": int(defense or 0)})
        progress.save(db, champion_id, campaign, zones)
        db.commit()
        return True


def campaign_bonuses(db, champion_id: int) -> list[dict[str, Any]]:
    """The bonuses the champion's dungeon run carries (empty before the first save)."""
    with progress.champion_lock(champion_id):
        loaded = progress.load(db, champion_id)
    return _items(loaded[0].get(BUFFS_KEY)) if loaded else []


def carry_into_battle(db, camp_id: int, battle_state: dict[str, Any]) -> int:
    """Put the run's bonuses into a new battle's state. Returns how many it carries (0 outside a dungeon)."""
    info = campaign_champion(db, camp_id)
    if info is None or info[1] != DUNGEON:
        return 0
    carried = campaign_bonuses(db, info[0])
    if carried:
        battle_state[BUFFS_KEY] = carried
    return len(carried)


def battle_bonus(battle_state) -> tuple[int, int]:
    """The attack and defense the run's bonuses give each opposing troop in this battle."""
    if not isinstance(battle_state, dict) or battle_state.get("pvp"):
        return 0, 0
    items = _items(battle_state.get(BUFFS_KEY))
    return (sum(int(item.get("attack", 0) or 0) for item in items),
            sum(int(item.get("defense", 0) or 0) for item in items))


def record_opposing_bonus(context, target_uid: int, attack: int, defense: int, duration: str) -> bool | None:
    """Record an ``UntilEndOfDungeon`` stat bonus on an opposing troop of a dungeon battle.

    Returns True when the bonus is now carried by the run (the continuous scan adds it, so the native leaf must not
    also write it to the card), or None when the bonus does not apply here (another duration, another battle, or a
    target on the source's own side). The key names the battle, the source card and its ability, so the several
    targets of one resolution record it once.
    """
    if duration != "UntilEndOfDungeon" or not (attack or defense):
        return None
    session = context.session
    camp_id = camp_id_from_session(getattr(session, "session_name", ""))
    if camp_id is None:
        return None
    info = campaign_champion(context.db, camp_id)
    if info is None or info[1] != DUNGEON:
        return None
    from pvp_db import db_card_source_info
    target = db_card_source_info(session.session_id, int(target_uid), conn=context.db)
    if not target:
        return None
    # PvE: a nonzero owner is the player, zero is the AI. The bonus is for the other side from its source.
    target_is_player = bool(int(target[3] or 0))
    source_is_player = bool(int(context.bstate.get("resolving_owner_id", 0) or 0))
    if target_is_player == source_is_player:
        return None
    key = ":".join(str(part) for part in (
        session.session_id, context.bstate.get("resolving_source_uid"),
        context.bstate.get("resolving_ability"), attack, defense))
    carried = context.bstate.setdefault(BUFFS_KEY, [])
    if not any(item.get("key") == key for item in _items(carried)):
        carried.append({"key": key, "attack": int(attack or 0), "defense": int(defense or 0)})
    record(context.db, info[0], key, attack, defense)
    return True


def is_opposing_troop_on_warzone(db, session_id: int, card_uid: int, owner_id, location) -> bool:
    """Whether a card is an AI-side troop on the warzone (the side and zone the run's bonuses apply to)."""
    if int(owner_id or 0) or str(location or "").lower() != WARZONE:
        return False
    from pvp_db import db_card_source_info
    row = db_card_source_info(session_id, int(card_uid), conn=db)
    return bool(row) and row[1] == TROOP
