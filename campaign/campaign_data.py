"""Campaign data files: the table shapes and the validation the editor and campaign_content use.

``campaign/campaign_data/*.json`` holds the editor's rows for the campaign content
tables (encounter scenes, node and quest conversations, quest templates, the map
edges, rewards). ``campaign_content`` builds those tables in memory from Records and
these files; nothing is written to ``hconnect.db``. A file row updates the Records
row with the same key, or adds a new row (``origin='data'``); a node conversation
that names a node is an editor placement, which shadows the seeded placements of the
same conversation.

File format::

    {"schema_version": 1,
     "tables": {"encounter_scenes": [{"guid": "...", "rewards_json": {...}}],
                "quest_conversations": [{...}]}}

``card_map.json`` and ``campaign_node_edges.json`` have their own formats (see
``campaign_content``). ``card_templates.json`` and ``ability_records.json`` are the
card editor's shadow overlays (see ``campaign_editor/cards.py`` and ``card_abilities.py``).
"""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
ORIGIN_SEED = "seed"
ORIGIN_DATA = "data"
DATA_DIR = Path(__file__).resolve().parent / "campaign_data"
# Encounter-deck card GUIDs that Records never defines -> the CardTemplate GUID
# the editor shows for them (campaign_editor/import_card_map.py writes this).
CARD_MAP_PATH = DATA_DIR / "card_map.json"
# Card templates the editor has created or edited. A row whose GUID is a Records
# card replaces that card whole; any other row is a new card.
CARD_TEMPLATES_PATH = DATA_DIR / "card_templates.json"

_CARD_OVERLAY_LOCK = threading.Lock()
_CARD_OVERLAY_CACHE: tuple[int, dict[str, dict[str, Any]]] | None = None   # (mtime_ns, rows)


def load_card_overlay() -> dict[str, dict[str, Any]]:
    """The card overlay as ``{lower-case GUID: row}``, a fresh copy each call.

    Re-read when the file changes, so an editor save is seen without a restart.
    """
    global _CARD_OVERLAY_CACHE
    try:
        mtime = CARD_TEMPLATES_PATH.stat().st_mtime_ns
    except FileNotFoundError:
        return {}
    with _CARD_OVERLAY_LOCK:
        if _CARD_OVERLAY_CACHE is None or _CARD_OVERLAY_CACHE[0] != mtime:
            data = json.loads(CARD_TEMPLATES_PATH.read_text(encoding="utf-8"))
            rows = {}
            for row in data.get("cards") or []:
                guid = str(row["guid"]).lower()
                rows[guid] = dict(row, guid=guid)
            _CARD_OVERLAY_CACHE = (mtime, rows)
        return copy.deepcopy(_CARD_OVERLAY_CACHE[1])


def write_card_overlay(rows: dict[str, dict[str, Any]]) -> None:
    """Replace the card overlay file with ``rows`` (sorted by GUID), atomically."""
    global _CARD_OVERLAY_CACHE
    body = {"schema_version": SCHEMA_VERSION,
            "cards": [rows[guid] for guid in sorted(rows)]}
    with _CARD_OVERLAY_LOCK:
        CARD_TEMPLATES_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = CARD_TEMPLATES_PATH.with_name(CARD_TEMPLATES_PATH.name + ".tmp")
        temporary.write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n",
                             encoding="utf-8")
        os.replace(temporary, CARD_TEMPLATES_PATH)
        _CARD_OVERLAY_CACHE = None


# Records card packs the store sells: one row per listed pack, with its price and tab.
# Records keeps the pack's set and type; the listing holds only what the store shows.
STORE_LISTINGS_PATH = DATA_DIR / "store_listings.json"
_STORE_LISTINGS_LOCK = threading.Lock()


def load_store_listings() -> list[dict[str, Any]]:
    """The listed packs, each ``{pack_guid, name, short_desc, price, currency, store_tab}``."""
    with _STORE_LISTINGS_LOCK:
        try:
            data = json.loads(STORE_LISTINGS_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
    return [dict(row, pack_guid=str(row["pack_guid"]).lower()) for row in data.get("packs") or []]


def write_store_listings(rows: list[dict[str, Any]]) -> None:
    """Replace the store listing file with ``rows`` (sorted by pack GUID), atomically."""
    body = {"schema_version": SCHEMA_VERSION,
            "packs": sorted(rows, key=lambda row: row["pack_guid"])}
    with _STORE_LISTINGS_LOCK:
        STORE_LISTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = STORE_LISTINGS_PATH.with_name(STORE_LISTINGS_PATH.name + ".tmp")
        temporary.write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, STORE_LISTINGS_PATH)


# Card packs of custom sets: InventoryCardPack records the client loads from the set's
# .incdata. Each carries its pack artwork GUID and store artwork GUID (editor-owned).
CUSTOM_PACKS_PATH = DATA_DIR.parents[1] / "custom_sets" / "InventoryCardPacks.json"
_CUSTOM_PACKS_LOCK = threading.Lock()


def load_custom_packs() -> list[dict[str, Any]]:
    """The custom set card packs, as records (a fresh copy each call)."""
    with _CUSTOM_PACKS_LOCK:
        try:
            return json.loads(CUSTOM_PACKS_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []


def write_custom_packs(records: list[dict[str, Any]]) -> None:
    """Replace the custom pack file with ``records``, atomically."""
    with _CUSTOM_PACKS_LOCK:
        CUSTOM_PACKS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = CUSTOM_PACKS_PATH.with_name(CUSTOM_PACKS_PATH.name + ".tmp")
        temporary.write_text(json.dumps(records, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, CUSTOM_PACKS_PATH)


# Ability records the card editor has created or edited, by Records section
# (AbilityTemplate, AbilityEffectTemplate, AbilityEffectConditionTemplate,
# AbilityTargetTemplate). A record whose GUID is in Records replaces that record
# whole; any other record is new. Records keep their ``_t`` and ``m_`` fields.
ABILITY_RECORDS_PATH = DATA_DIR / "ability_records.json"
ABILITY_SECTIONS = ("AbilityTemplate", "AbilityEffectTemplate",
                    "AbilityEffectConditionTemplate", "AbilityTargetTemplate")

_ABILITY_OVERLAY_LOCK = threading.Lock()
_ABILITY_OVERLAY_CACHE: tuple[int, dict[str, dict[str, dict[str, Any]]]] | None = None


def ability_record_guid(section: str, record: dict[str, Any]) -> str:
    """A record's own GUID: ``m_AbilityTemplateId`` for an ability, else ``m_TemplateId``."""
    field = "m_AbilityTemplateId" if section == "AbilityTemplate" else "m_TemplateId"
    value = record.get(field)
    return str(value.get("m_Guid") or "").lower() if isinstance(value, dict) else ""


def load_ability_overlay() -> dict[str, dict[str, dict[str, Any]]]:
    """The ability overlay as ``{section: {lower-case GUID: record}}``, a fresh copy.

    Every section is present (possibly empty). Re-read when the file changes.
    """
    global _ABILITY_OVERLAY_CACHE
    try:
        mtime = ABILITY_RECORDS_PATH.stat().st_mtime_ns
    except FileNotFoundError:
        return {section: {} for section in ABILITY_SECTIONS}
    with _ABILITY_OVERLAY_LOCK:
        if _ABILITY_OVERLAY_CACHE is None or _ABILITY_OVERLAY_CACHE[0] != mtime:
            data = json.loads(ABILITY_RECORDS_PATH.read_text(encoding="utf-8"))
            stored = data.get("sections") or {}
            sections = {section: {ability_record_guid(section, record): record
                                  for record in stored.get(section) or []}
                        for section in ABILITY_SECTIONS}
            _ABILITY_OVERLAY_CACHE = (mtime, sections)
        return copy.deepcopy(_ABILITY_OVERLAY_CACHE[1])


def write_ability_overlay(sections: dict[str, dict[str, dict[str, Any]]]) -> None:
    """Replace the ability overlay file with ``sections`` (each sorted by GUID), atomically."""
    global _ABILITY_OVERLAY_CACHE
    body = {"schema_version": SCHEMA_VERSION,
            "sections": {section: [rows[guid] for guid in sorted(rows)]
                         for section in ABILITY_SECTIONS
                         for rows in [sections.get(section) or {}] if rows}}
    with _ABILITY_OVERLAY_LOCK:
        ABILITY_RECORDS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = ABILITY_RECORDS_PATH.with_name(ABILITY_RECORDS_PATH.name + ".tmp")
        temporary.write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n",
                             encoding="utf-8")
        os.replace(temporary, ABILITY_RECORDS_PATH)
        _ABILITY_OVERLAY_CACHE = None


_CARD_MAP_LOCK = threading.Lock()
_CARD_MAP_CACHE: tuple[int, dict[str, str]] | None = None   # (mtime_ns, map)


def load_card_map() -> dict[str, str]:
    """The card map as ``{unresolved GUID: CardTemplate GUID}``, lower-cased.

    Re-read when the file changes, so an editor save takes effect on the next
    battle without a restart. Returns a copy; callers may keep it.
    """
    global _CARD_MAP_CACHE
    try:
        mtime = CARD_MAP_PATH.stat().st_mtime_ns
    except FileNotFoundError:
        return {}
    with _CARD_MAP_LOCK:
        if _CARD_MAP_CACHE is None or _CARD_MAP_CACHE[0] != mtime:
            data = json.loads(CARD_MAP_PATH.read_text(encoding="utf-8"))
            mapping = {str(key).lower(): str(value).lower()
                       for key, value in (data.get("cards") or {}).items()}
            _CARD_MAP_CACHE = (mtime, mapping)
        return dict(_CARD_MAP_CACHE[1])

# table -> natural key, whether new rows may be inserted, writable columns.
# encounter_scenes is owned by the editor (full definitions in
# encounter_scenes.json); quest_templates is still update-only: the editor
# tunes seeded quests but does not invent them.
TABLES: dict[str, dict[str, Any]] = {
    # The campaign editor owns every encounter scene: full definitions live in
    # campaign/campaign_data/encounter_scenes.json (campaign_content reads them).
    "encounter_scenes": {
        "key": ("guid",), "insert": True,
        "columns": {"name", "title", "gameboard", "ai_deck_guid", "ai_champion_guid",
                    "mods_json", "rewards_json", "actions_json",
                    "ai_deck_personality", "player_champion_guid",
                    "player_deck_guid"},
        "file_columns": {"ai_deck_personality_override", "scene_tags"},
        # The JSON keeps the precomputed default separately from an optional
        # authored override; SQLite stores their effective value.
        "skip_blank": {"ai_deck_personality"}},
    "quest_templates": {
        "key": ("script_name",), "insert": False,
        "columns": {"title", "objectives_json", "campaign_group", "enabled",
                    "start_hook", "rewards_json"}},
    "campaign_node_conversations": {
        "key": ("campaign_template", "conversation_guid"), "insert": True,
        "columns": {"node_id", "conversation_name", "trigger_json",
                    "priority", "enabled"}},
    "quest_conversations": {
        "key": ("quest_script", "conversation_guid", "campaign_template"),
        "insert": True,
        "columns": {"node_id", "npc", "role", "faction", "conversation_name",
                    "start_hook", "conditions_json", "priority", "enabled"}},
    "conversation_rewards": {
        "key": ("conversation_guid",), "insert": True,
        "columns": {"reward_json", "one_time", "enabled"}},
}


def validate(data: dict[str, Any]) -> list[str]:
    """Problems that would make a data file unsafe to apply."""
    problems = []
    if data.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version must be {SCHEMA_VERSION}")
    for table, rows in (data.get("tables") or {}).items():
        spec = TABLES.get(table)
        if spec is None:
            problems.append(f"{table}: not an editable campaign table")
            continue
        if not isinstance(rows, list):
            problems.append(f"{table}: rows must be a list")
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                problems.append(f"{table}[{index}]: row must be an object")
                continue
            missing = [k for k in spec["key"] if not row.get(k)]
            if missing:
                problems.append(f"{table}[{index}]: missing key {missing}")
            extra = (set(row) - set(spec["key"]) - spec["columns"]
                     - spec.get("file_columns", set()))
            if extra:
                problems.append(f"{table}[{index}]: not editable {sorted(extra)}")
    return problems
