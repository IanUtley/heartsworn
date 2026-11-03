"""Campaign content rows, built from Records and the editor's JSON files.

These tables used to live in ``hconnect.db``; now they are built in memory:

- the Records seed (``AssetExtraction/gamedata_seed.py``) gives the base rows;
- ``campaign/campaign_data/*.json`` overlays them (``campaign_data.py`` rules:
  a file row updates the seed row with the same key, or inserts a new
  ``origin='data'`` row; a row naming a node is an editor placement);
- ``campaign/campaign_data/card_map.json`` (read by ``deck_cards``) points the
  card GUIDs Records never defines at a CardTemplate.

Rows are plain dicts keyed by column name. The build is cached and rebuilt when
any campaign data file changes; a lock guards it, and callers get the same
list objects, which they must not mutate.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import campaign_data

CAMPAIGN_TABLES = ("encounter_scenes", "encounter_deck_cards",
                   "campaign_node_conversations", "quest_conversations",
                   "quest_templates", "conversation_rewards",
                   "campaign_node_edges")

_LOCK = threading.Lock()
_CACHE: tuple[tuple, dict[str, list[dict[str, Any]]], dict[str, Any]] | None = None
# Data files that are not campaign_data overlays (their own formats).
_OTHER_FILES = ("card_map.json", "campaign_node_edges.json")
_EDGE_COLUMNS = ("campaign_template", "from_node", "to_node", "path_name")


def _edge_rows() -> list[dict[str, Any]]:
    """The map's path graph (``campaign_node_edges.json``), both directions as stored."""
    path = Path(campaign_data.DATA_DIR) / "campaign_node_edges.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [{c: row[c] for c in _EDGE_COLUMNS} for row in data.get("edges") or []]


def _seed_rows() -> dict[str, list[dict[str, Any]]]:
    """The Records seed for the scene/deck/conversation/quest tables."""
    from AssetExtraction import gamedata_seed
    tables = gamedata_seed.extract()["tables"]
    rows: dict[str, list[dict[str, Any]]] = {}
    for table in ("encounter_scenes", "encounter_deck_cards",
                  "campaign_node_conversations", "quest_templates",
                  "quest_conversations"):
        columns = gamedata_seed.TABLE_COLUMNS[table]
        seeded = [dict(zip(columns, row)) for row in tables.get(table, [])]
        for row in seeded:
            if table == "encounter_scenes":
                for column, default in _DEFAULTS.items():
                    row.setdefault(column, default)
            for column in ("mods_json", "rewards_json"):
                if column in row:
                    row[column] = row[column] or _DEFAULTS[column]
            if table in ("encounter_scenes", "campaign_node_conversations",
                         "quest_conversations"):
                row.setdefault("origin", campaign_data.ORIGIN_SEED)
            if table == "quest_templates":
                row.setdefault("rewards_json", "{}")
        rows[table] = seeded
    return rows


# Seed-time corrections the database used to get from static.ensure_schema.
# The last link table is the same map static.py holds until its DDL is removed.
QUEST_OBJECTIVE_ENCOUNTER_LINKS = {
    ("az01_q_cross_the_river", "Step1"): "ab77df1e-5f13-471b-80e7-b7b4824ca280",
}
_RETIRED_START_HOOKS = ("az1_tamed_start", "az1_find_horwich_sea_start",
                        "az1_find_cave_in_start", "az1_find_ambling_mesa_start",
                        "az1_cross_zodiac_start", "az1_cross_the_river_start",
                        "az1_smoldering_dead_start")
_OBJECTIVE_TEXT_FIXES = {
    # Smoldering Dead's first objective names a dungeon template; link its scene.
    "q_smoldering_dead": (
        '"dungeon": "a8d2ad0e-306e-4d07-9cd1-e4d7c82cdf1c", "id": "Step1"',
        '"dungeon": "a8d2ad0e-306e-4d07-9cd1-e4d7c82cdf1c", '
        '"encounter": "1d91ee6d-dd5b-4aaf-af58-7ace873b1a80", "id": "Step1"'),
    # Cross Zodiac River's final conversation was stored as Warren's champion GUID.
    "az01_q_cross_the_river_part2": (
        "de519b67-1e3c-4fd1-96a1-909d2b6b3c67",
        "08b9b8ab-2100-4f8d-87b0-18369eb4ecb4"),
}


def _apply_fixups(rows: dict[str, list[dict[str, Any]]]) -> None:
    """The corrections static.ensure_schema applied to the seeded rows."""
    for row in rows["quest_conversations"]:
        if row.get("faction") in ("", None):
            npc = str(row.get("npc") or "").lower()
            if npc == "belarius":
                row["faction"] = "Ardent"
            elif npc == "takumi":
                row["faction"] = "Underworld"
    for table in ("quest_templates", "quest_conversations"):
        for row in rows[table]:
            if row.get("start_hook") in _RETIRED_START_HOOKS:
                row["start_hook"] = ""
    for row in rows["quest_templates"]:
        text_fix = _OBJECTIVE_TEXT_FIXES.get(row.get("script_name"))
        if text_fix:
            row["objectives_json"] = str(row.get("objectives_json") or "").replace(*text_fix)
        objective_link = next((
            guid for (script, _id), guid in QUEST_OBJECTIVE_ENCOUNTER_LINKS.items()
            if script == row.get("script_name")), None)
        if objective_link is None:
            continue
        try:
            objectives = json.loads(row.get("objectives_json") or "[]")
        except (TypeError, ValueError):
            continue
        changed = False
        for objective in objectives if isinstance(objectives, list) else []:
            if (isinstance(objective, dict) and not objective.get("encounter")
                    and str(objective.get("id") or "") == "Step1"
                    and str(objective.get("type") or "").lower() in ("encounter", "dungeon")):
                objective["encounter"] = objective_link
                changed = True
        if changed:
            row["objectives_json"] = json.dumps(objectives, sort_keys=True)


def _arena_deck_rows() -> list[dict[str, Any]]:
    """Frost Ring Arena decks: the DeckTemplate records named ``Arena_...``.

    Same rule as AssetExtraction/populate_fra_encounters.py: a card's copies are
    aggregated per deck, and gem slots run one list per copy.
    """
    from AssetExtraction import gamedata_seed as seed
    from AssetExtraction.gamedata_seed import guid, int_value, json_text
    data = seed.load_records_text(seed.configured_records_path())
    rows: list[dict[str, Any]] = []
    for _, record in seed.records(data, "DeckTemplate")[0]:
        deck = guid(record.get("m_Id")).lower()
        name = str(record.get("m_DeckName") or "")
        if not deck or not name.startswith("Arena_") or not guid(record.get("m_ChampionId")):
            continue
        agg: dict[str, list[Any]] = {}
        for entry in record.get("m_DeckResources") or []:
            if not isinstance(entry, dict):
                continue
            card = guid(entry.get("m_idTemplate")).lower()
            quantity = int_value(entry.get("m_Count"), 1)
            gems = entry.get("m_GemTypesNewList") or []
            slot = agg.setdefault(card, [0, [], False])
            if gems:
                if not slot[2]:
                    slot[1] = [[] for _ in range(slot[0])]
                slot[1].extend(gems)
                slot[2] = True
            elif slot[2]:
                slot[1].extend([[] for _ in range(quantity)])
            slot[0] += quantity
        for card, (quantity, gem_slots, _has) in sorted(agg.items()):
            rows.append({"deck_guid": deck, "card_guid": card, "quantity": quantity,
                         "gem_types_new_list_json": json_text(gem_slots, [])})
    return rows


def _lower_key(row: dict[str, Any], keys: tuple[str, ...]) -> tuple:
    """A row's natural key, GUIDs compared case-insensitively."""
    return tuple(str(row.get(k) or "").lower() for k in keys)


def _overlay(rows: dict[str, list[dict[str, Any]]], data: dict[str, Any]) -> None:
    """Apply one campaign data file's rows, as ``campaign_data.apply_data`` does."""
    for table, file_rows in (data.get("tables") or {}).items():
        spec = campaign_data.TABLES[table]
        bucket = rows.setdefault(table, [])
        for row in file_rows:
            placed = table == "campaign_node_conversations" and bool(row.get("node_id"))
            key_names = spec["key"] + (("node_id",) if placed else ())
            wanted = _lower_key(row, key_names)
            values = {c: v for c, v in row.items()
                      if c in spec["columns"]
                      and not (c in spec.get("skip_blank", ()) and v in ("", None))}
            if table == "encounter_scenes":
                personality = (str(row.get("ai_deck_personality_override") or "").strip()
                               or str(row.get("ai_deck_personality") or "").strip())
                # A blank personality is not written (campaign_data skip_blank).
                if personality:
                    values["ai_deck_personality"] = personality
            values = {c: _stored(c, v) for c, v in values.items()}
            matches = [r for r in bucket if _lower_key(r, key_names) == wanted]
            if matches:
                for target in matches:
                    target.update(values)
            elif spec["insert"]:
                new = {c: row.get(c) for c in spec["key"]}
                if placed:
                    new["node_id"] = row["node_id"]
                if table == "campaign_node_conversations":
                    # A placement names only its conversation: take the name
                    # from the conversation's own row, and enable it.
                    same = next((r for r in bucket if _lower_key(
                        r, ("campaign_template", "conversation_guid")) == _lower_key(
                        row, ("campaign_template", "conversation_guid"))), None)
                    new["enabled"] = 1
                    new["conversation_name"] = same.get("conversation_name") if same else None
                new.update(values)
                new["origin"] = campaign_data.ORIGIN_DATA
                bucket.append(new)


def _stored(column: str, value: Any) -> Any:
    """A value as the database stored it: ``_json`` columns as sorted JSON text."""
    if column.endswith("_json") and not isinstance(value, str):
        return json.dumps(value, sort_keys=True)
    if isinstance(value, bool):
        return int(value)
    return value


# Defaults the table DDL gives a column that the seed does not set.
_DEFAULTS = {"mods_json": "[]", "rewards_json": "{}", "actions_json": "{}",
             "player_champion_guid": "", "player_deck_guid": "",
             "origin": campaign_data.ORIGIN_SEED}


def _data_signature() -> tuple:
    """What the build depends on: every campaign data file and the card map."""
    paths = sorted(Path(campaign_data.DATA_DIR).glob("*.json"))
    return tuple((p.name, p.stat().st_mtime_ns) for p in paths if p.exists())


def build() -> dict[str, list[dict[str, Any]]]:
    """All campaign content tables, rebuilt only when a data file changes."""
    global _CACHE
    signature = _data_signature()
    with _LOCK:
        if _CACHE is not None and _CACHE[0] == signature:
            return _CACHE[1]
        rows = _seed_rows()
        _apply_fixups(rows)
        for path in sorted(Path(campaign_data.DATA_DIR).glob("*.json")):
            if path.name in _OTHER_FILES:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if campaign_data.validate(data):
                continue   # a bad file is skipped whole, as campaign_data does
            _overlay(rows, data)
        rows["campaign_node_edges"] = _edge_rows()
        # Arena decks join the deck table; a scene's deck is never an Arena template.
        scene_decks = {str(r.get("deck_guid") or "").lower() for r in rows["encounter_deck_cards"]}
        rows["encounter_deck_cards"].extend(
            r for r in _arena_deck_rows() if r["deck_guid"] not in scene_decks)
        for table in CAMPAIGN_TABLES:
            rows.setdefault(table, [])
        decks: dict[str, list[dict[str, Any]]] = {}
        for row in rows["encounter_deck_cards"]:
            decks.setdefault(str(row.get("deck_guid") or "").lower(), []).append(row)
        scenes_by_guid = {str(row.get("guid") or "").lower(): row
                          for row in rows["encounter_scenes"]}
        # A seed conversation row is hidden when an editor placement names the same
        # conversation in the same template.
        placed = {(str(r.get("campaign_template")), str(r.get("conversation_guid") or "").lower())
                  for r in rows["campaign_node_conversations"] if r.get("origin") == campaign_data.ORIGIN_DATA}
        visible = [r for r in rows["campaign_node_conversations"]
                   if r.get("origin") == campaign_data.ORIGIN_DATA
                   or (str(r.get("campaign_template")), str(r.get("conversation_guid") or "").lower())
                   not in placed]
        _CACHE = (signature, rows, {"decks": decks, "scenes": scenes_by_guid, "visible": visible})
        return rows


def table(name: str) -> list[dict[str, Any]]:
    """One campaign content table's rows (see the module docstring)."""
    if name not in CAMPAIGN_TABLES:
        raise KeyError(name)
    return build()[name]


def deck_cards(deck_guid: str, *, mapped: bool = True) -> list[dict[str, Any]]:
    """An encounter deck's card rows.

    ``mapped`` (the default, for battles) swaps each unresolved GUID for its card
    map target; ``mapped=False`` keeps the GUID Records gave, for the editor's
    remap. Rows keep their order and count: callers index each row's gem slots.
    """
    build()
    with _LOCK:
        rows = _CACHE[2]["decks"].get(str(deck_guid or "").lower(), [])
    if not mapped:
        return [dict(row) for row in rows]
    card_map = campaign_data.load_card_map()
    return [{**row, "card_guid": card_map.get(str(row.get("card_guid") or "").lower(),
                                              row.get("card_guid"))}
            for row in rows]


def deck_guids() -> set[str]:
    """Every encounter deck GUID that has cards."""
    build()
    with _LOCK:
        return set(_CACHE[2]["decks"])


def conversation_reward(conversation_guid: str) -> tuple[str, bool, bool] | None:
    """``(reward_json, one_time, enabled)`` for a conversation, or None when it has no row."""
    guid = str(conversation_guid or "").lower()
    for row in table("conversation_rewards"):
        if str(row.get("conversation_guid") or "").lower() == guid:
            return (row.get("reward_json") or "{}", bool(row.get("one_time", True)),
                    bool(row.get("enabled", True)))
    return None


def scenes() -> list[dict[str, Any]]:
    """Every encounter scene (encounter_scenes), in build order."""
    return table("encounter_scenes")


def scene(scene_guid: str) -> dict[str, Any] | None:
    """One encounter scene by GUID, or None."""
    build()
    with _LOCK:
        return _CACHE[2]["scenes"].get(str(scene_guid or "").lower())


def node_conversations() -> list[dict[str, Any]]:
    """Visible campaign node conversations: editor placements, and seed rows they do not shadow."""
    build()
    with _LOCK:
        return list(_CACHE[2]["visible"])


def quest_conversations() -> list[dict[str, Any]]:
    """Every quest conversation link (quest_conversations), in build order."""
    return table("quest_conversations")


def quest_templates() -> list[dict[str, Any]]:
    """Every quest template (quest_templates), in build order."""
    return table("quest_templates")


def edges() -> list[dict[str, Any]]:
    """Every map path edge (campaign_node_edges), in file order."""
    return table("campaign_node_edges")
