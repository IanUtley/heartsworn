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
``campaign_content``).
"""

from __future__ import annotations

import json
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
    # campaign/campaign_data/encounter_scenes.json and are inserted/updated at start.
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
