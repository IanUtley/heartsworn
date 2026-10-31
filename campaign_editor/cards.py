"""Card templates for the campaign editor: browse, create, edit, set assignment and frames.

Three sources make up the card list:

* ``records``: the base ``card_templates`` rows (seeded from Records), untouched.
* ``edited``: a Records card with a row in ``campaign/campaign_data/card_templates.json``.
  That row replaces the Records card whole (a shadow). Reverting deletes it.
* ``custom``: a card in ``custom_sets/CardTemplates.json`` (e.g. Dominaria United).
  Edits write that file, and never Records or the overlay.
* ``new``: a card created in the editor, stored in the overlay.

Card sets are the ``CardSetTemplate`` records in Records plus those in
``custom_sets/CardSetTemplate.json``. Records is never written.

``frame_for`` applies the web client's card frame rules (``BuildFrame`` in
web_client/static/board3d.js) to a card, so the editor shows the same frame
sprites and badges the client draws.

A card's ability links (``abilities``, with any ``ability_overrides``) are validated
and saved with the card; the ability records themselves are ``card_abilities``'.

A card's keywords (``keywords``: ``ECardAttributes`` member names, the CardTemplate's
``m_AttributeFlags``) and integer attributes (``int_attributes``: ``{IntAttrs name:
value}``, such as Lethal, kept in the CardTemplate's ``m_SerializedTAC``) are read and
checked by ``card_attributes``. Its implementation stage (``impl_stage``: the client's
ECardImplStage, CardTemplate ``m_ImplStage``) says whether it is finished: only Done cards
go into a set's ``.incdata`` (``incdata.py``). A custom card stores these in its m_ fields;
an overlay row stores them only where they differ from the Records card.
``list_cards`` returns each card's face fields and frame, so the page can paint
the list with the client's painter.

Nothing here writes ``hconnect.db`` or Records.
"""

from __future__ import annotations

import json
import re
import struct
import threading
import uuid
from pathlib import Path
from typing import Any

from paths import RECORDS, REPO  # noqa: E402  (also sets sys.path)
import card_abilities as abilities  # noqa: E402
import card_attributes  # noqa: E402
import card_text  # noqa: E402
import client_schema  # noqa: E402
import campaign_data  # noqa: E402
from catalog import SHARD_NAMES, _card_thresholds  # noqa: E402

RARITIES = ("Common", "Uncommon", "Rare", "Epic", "Legendary", "Land", "Promo")
# The card types Records uses (CardTemplate m_CardType).
CARD_TYPES = ("Troop", "Troop|Artifact", "Troop|Quick", "BasicAction", "QuickAction",
              "Constant", "Constant|Quick", "Artifact", "Choice", "Resource", "Bane", "Mod")
# Threshold colours a card can require (SHARD_NAMES without Colorless).
THRESHOLD_SHARDS = tuple(SHARD_NAMES[1:])
# The order the client stacks threshold bars in (board3d.js THRESHOLD_ORDER).
FRAME_THRESHOLD_ORDER = ("Blood", "Diamond", "Ruby", "Sapphire", "Wild")
# The rarity discs the client has (board3d.js drawSetRarity).
FRAME_RARITIES = ("Common", "Uncommon", "Rare", "Legendary")
FACTION_SPRITE = {"Aria": "Faction_Ardent", "Underworld": "Faction_Underworld"}
# The card fields ``card_attributes`` checks, and the implementation stage; a save
# without one keeps the card's own.
TRAIT_FIELDS = ("keywords", "int_attributes", "impl_stage")
# CardTemplate m_ImplStage, the client's ECardImplStage. The client loads no card at
# NeedsScripting or earlier (TemplateManager.AddCardTemplate) and lets a player own only
# a Done card (CardTemplate.IsOwnable); every Records card is Done. A card without one
# is at the client default, the enum's first member.
IMPL_ENUM = "ECardImplStage"
IMPL_DONE = "Done"

_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ZERO_GUID = "00000000-0000-0000-0000-000000000000"
_BASE_COLUMNS = ("guid", "set_guid", "name", "rarity", "cost", "attack", "defense",
                 "card_type", "subtype", "threshold_json", "abilities_json", "variable_cost",
                 "socket_count", "is_pve")
_BASE_SQL = "SELECT " + ", ".join(_BASE_COLUMNS) + " FROM card_templates"
_RECORDS_SETS = RECORDS / "CardSetTemplate.jsonl"
_RECORDS_CARDS = RECORDS / "CardTemplate.jsonl"
_RECORDS_ITEMS = RECORDS / "InventoryItemData.jsonl"
CUSTOM_DIR = REPO / "custom_sets"
CUSTOM_CARDS_PATH = CUSTOM_DIR / "CardTemplates.json"
CUSTOM_SETS_PATH = CUSTOM_DIR / "CardSetTemplate.json"
# Temporarily hidden from the editor: custom sets and their cards are left out of the set list,
# the card list and the store listing. Set this to True to show them again.
SHOW_CUSTOM_SETS = False

_WRITE_LOCK = threading.Lock()
_CACHE_LOCK = threading.Lock()
# (mtime_ns, value) per source file; re-read when the file changes.
_CACHE: dict[str, tuple[int, Any]] = {}


def _cached(key: str, path: Path, load) -> Any:
    try:
        mtime = path.stat().st_mtime_ns
    except FileNotFoundError:
        return load(None)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is None or hit[0] != mtime:
            hit = (mtime, load(path))
            _CACHE[key] = hit
        return hit[1]


# ---------------------------------------------------------------- Records sets

def _load_records_sets(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    sets = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            text = json.loads(line) if line.strip() else None
        except ValueError:
            continue
        if not isinstance(text, str) or '"_t"' not in text:
            continue
        guid = re.search(r'"m_Id"\s*:\s*\{\s*"m_Guid"\s*:\s*"([^"]+)"', text)
        name = re.search(r'"m_Name"\s*:\s*"([^"]*)"', text)
        bundle = re.search(r'"m_BundleName"\s*:\s*"([^"]*)"', text)
        number = re.search(r'"m_SetNo"\s*:\s*(\d+)', text)
        if guid and name:
            sets.append({"guid": guid.group(1).lower(), "name": name.group(1),
                         "bundle": bundle.group(1) if bundle else "",
                         "set_no": int(number.group(1)) if number else 0,
                         "source": "records", "file": "hex-private/Records/CardSetTemplate.jsonl"})
    return sets


def _load_custom_sets(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    return [{"guid": str(entry["m_Id"]["m_Guid"]).lower(), "name": entry.get("m_Name", ""),
             "bundle": entry.get("m_BundleName", ""), "set_no": int(entry.get("m_SetNo") or 0),
             "source": "custom", "file": "custom_sets/CardSetTemplate.json"}
            for entry in json.loads(path.read_text(encoding="utf-8"))]


def card_sets() -> list[dict[str, Any]]:
    """Every card set as ``{guid, name, bundle, set_no, source}``: Records first, then custom."""
    records = _cached("records-sets", _RECORDS_SETS, _load_records_sets)
    custom = _cached("custom-sets", CUSTOM_SETS_PATH, _load_custom_sets) if SHOW_CUSTOM_SETS else []
    return [dict(entry) for entry in records + custom]


def _set_names() -> dict[str, str]:
    return {entry["guid"]: entry["name"] for entry in card_sets()}


def _set_number(set_guid: str) -> int:
    for entry in card_sets():
        if entry["guid"] == set_guid:
            return entry["set_no"]
    return 0


def _set_guid(value: Any) -> str:
    """A set GUID that Records or the custom set file defines, or "" for no set."""
    guid = str(value or "").strip().lower()
    if not guid:
        return ""
    if not _GUID.match(guid):
        raise ValueError("a card set must be a GUID")
    if guid not in _set_names():
        raise ValueError("unknown card set")
    return guid


# ---------------------------------------------------------------- Records extras

def _load_records_equipment(path: Path | None) -> list[dict[str, str]]:
    """``[{guid, name}]`` for every Records equipment item, by name."""
    items = []
    if path is None:
        return items
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            text = json.loads(line) if line.strip() else None
        except ValueError:
            continue
        if not isinstance(text, str) or not re.search(r'"_t"\s*:\s*"[^"]*InventoryEquipmentData"', text):
            continue
        guid = re.search(r'"m_Id"\s*:\s*\{\s*"m_Guid"\s*:\s*"([^"]+)"', text)
        name = re.search(r'"m_Name"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        if guid and name:
            items.append({"guid": guid.group(1).lower(),
                          "name": json.loads(f'"{name.group(1)}"').strip()})
    return sorted(items, key=lambda item: item["name"].lower())


def _equipment_items() -> list[dict[str, str]]:
    return _cached("records-equipment", _RECORDS_ITEMS, _load_records_equipment)


def _load_records_extras(path: Path | None) -> dict[str, dict[str, Any]]:
    """``{card GUID: faction, text, flavor, unique, art}`` for every Records CardTemplate.

    ``equipment_slots`` lists the equipment item each slot requires ("" for an empty
    slot); ``equipment_modified`` marks the equipped alternate of a base card.
    """
    extras: dict[str, dict[str, Any]] = {}
    if path is None:
        return extras

    def field(text: str, name: str, default: str = "") -> str:
        found = re.search(rf'"{name}"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        return json.loads(f'"{found.group(1)}"') if found else default

    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            text = json.loads(line) if line.strip() else None
        except ValueError:
            continue
        if not isinstance(text, str) or '"_t"' not in text:
            continue
        guid = re.search(r'"m_Id"\s*:\s*\{\s*"m_Guid"\s*:\s*"([^"]+)"', text)
        if not guid:
            continue
        unique = re.search(r'"m_Unique"\s*:\s*(\d+)', text)
        rage = re.search(r'"m_RageValue"\s*:\s*(\d+)', text)
        modified = re.search(r'"m_EquipmentModifiedCard"\s*:\s*(\d+)', text)
        slots = re.search(r'"m_EquipmentSlots"\s*:\s*\[(.*?)\]', text, re.S)
        slot_guids = re.findall(r'"m_Guid"\s*:\s*"([^"]+)"', slots.group(1)) if slots else []
        extras[guid.group(1).lower()] = {
            "equipment_modified": bool(int(modified.group(1))) if modified else False,
            "equipment_slots": ["" if g.lower() == ZERO_GUID else g.lower() for g in slot_guids],
            "faction": field(text, "m_Faction", "None") or "None",
            "text": field(text, "m_GameText"),
            "rage": int(rage.group(1)) if rage else 0,
            "flavor": field(text, "m_FlavorText"),
            "unique": bool(int(unique.group(1))) if unique else False,
            "art": field(text, "m_ArtNumber"),
            "impl_stage": field(text, "m_ImplStage", IMPL_DONE),
        }
    return extras


# ---------------------------------------------------------------- custom cards

def _load_custom_cards(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _custom_list() -> list[dict[str, Any]]:
    """The custom CardTemplate records as a fresh copy, safe to edit before writing."""
    return json.loads(json.dumps(_cached("custom-cards", CUSTOM_CARDS_PATH, _load_custom_cards)))


def _from_custom(raw: dict[str, Any]) -> dict[str, Any]:
    """A custom CardTemplate record (m_ fields) in the editor's card shape."""
    return {
        "guid": str(raw["m_Id"]["m_Guid"]).lower(),
        "name": raw.get("m_Name", ""),
        "set_guid": str(raw.get("m_SetId", {}).get("m_Guid", "")).lower(),
        "rarity": raw.get("m_CardRarity", ""),
        "cost": int(raw.get("m_ResourceCost") or 0),
        "attack": int(raw.get("m_BaseAttackValue") or 0),
        "defense": int(raw.get("m_BaseDefenseValue") or 0),
        "card_type": raw.get("m_CardType", ""),
        "subtype": raw.get("m_CardSubtype", ""),
        "threshold": [{"shard": t["m_ColorFlags"], "count": int(t["m_ThresholdColorRequirement"])}
                      for t in raw.get("m_Threshold") or []],
        "variable_cost": bool(raw.get("m_VariableCost")),
        "abilities": [a["m_CardAbilityId"]["m_Guid"] for a in raw.get("m_CardAbilities") or []],
        "ability_overrides": {str(a["m_CardAbilityId"]["m_Guid"]).lower():
                              (a.get("m_CardAbilityOverrides") or {}).get("m_OverrideValues") or []
                              for a in raw.get("m_CardAbilities") or []},
        "faction": raw.get("m_Faction", "None") or "None",
        "pve": False,
        "socket_count": int(raw.get("m_SocketCount") or 0),
        "sockets": card_text.socket_types(raw.get("m_GameText", "") or ""),
        "text": _built_text(raw.get("m_Name", ""), raw.get("m_GameText", "") or "", _custom_traits(raw),
                            int(raw.get("m_RageValue") or 0), card_text.socket_types(raw.get("m_GameText", "") or ""),
                            [a["m_CardAbilityId"]["m_Guid"] for a in raw.get("m_CardAbilities") or []]),
        "flavor": raw.get("m_FlavorText", "") or "",
        "unique": bool(raw.get("m_Unique")),
        "art_url": _custom_art_url(raw.get("m_CardImagePath", "")),
        **_custom_traits(raw),
    }


def _custom_traits(raw: dict[str, Any]) -> dict[str, Any]:
    """A custom record's keywords and integer attributes; a value the client could not
    read shows as none, so the list still opens."""
    try:
        flags = card_attributes.parse_flags(raw.get("m_AttributeFlags"))
    except ValueError:
        flags = []
    try:
        ints = card_attributes.tac_ints((raw.get("m_SerializedTAC") or {}).get("data"))
    except (ValueError, TypeError, IndexError, struct.error):
        ints = {}
    return {"keywords": flags, "int_attributes": dict(sorted(ints.items())),
            "impl_stage": str(raw.get("m_ImplStage") or _default_stage())}


def _apply_traits(raw: dict[str, Any], traits: dict[str, Any]) -> None:
    """Write checked keywords and integer attributes into a custom record's m_ fields;
    the TAC's other entries stay."""
    if "keywords" in traits:
        raw["m_AttributeFlags"] = card_attributes.flags_text(traits["keywords"])
    if "impl_stage" in traits:
        raw["m_ImplStage"] = traits["impl_stage"]
    if "int_attributes" in traits:
        data = (raw.get("m_SerializedTAC") or {}).get("data")
        if traits["int_attributes"] or card_attributes.decode_tac(data):
            raw["m_SerializedTAC"] = {"data": card_attributes.with_ints(data, traits["int_attributes"])}
        else:
            raw.pop("m_SerializedTAC", None)


def _custom_art_url(path: str) -> str:
    """A custom card's art, served from custom_sets/ (``/custom/<path under it>``)."""
    relative = str(path or "").replace("\\", "/")
    if relative.startswith("custom_sets/"):
        return "/custom/" + relative[len("custom_sets/"):]
    return ""


def _apply_to_custom(raw: dict[str, Any], fields: dict[str, Any]) -> None:
    """Write the editable fields back into a custom record, keeping its other m_ fields."""
    raw["m_Name"] = fields["name"]
    raw["m_SetId"] = {"m_Guid": fields["set_guid"]}
    raw["m_CardRarity"] = fields["rarity"]
    raw["m_ResourceCost"] = fields["cost"]
    raw["m_BaseAttackValue"] = fields["attack"]
    raw["m_BaseDefenseValue"] = fields["defense"]
    raw["m_CardType"] = fields["card_type"]
    raw["m_CardSubtype"] = fields["subtype"]
    raw["m_Threshold"] = [{"m_ColorFlags": t["shard"], "m_ThresholdColorRequirement": t["count"]}
                          for t in fields["threshold"]]
    raw["m_VariableCost"] = 1 if fields["variable_cost"] else 0
    raw["m_GameText"] = card_text.with_sockets(raw.get("m_GameText", "") or "", fields["sockets"])
    raw["m_SocketCount"] = len(fields["sockets"])
    if fields["flavor"]:
        # An empty flavour writes nothing, so the card's own flavour stays.
        raw["m_FlavorText"] = fields["flavor"]


def _card_abilities(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ability links in the CardTemplate ``m_CardAbilities`` shape."""
    return [{"m_CardAbilityId": {"m_Guid": link["guid"]},
             "m_CardAbilityOverrides": {"m_OverrideValues": link["overrides"]}} for link in links]


def _write_custom(records: list[dict[str, Any]]) -> None:
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    temporary = CUSTOM_CARDS_PATH.with_name(CUSTOM_CARDS_PATH.name + ".tmp")
    temporary.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(CUSTOM_CARDS_PATH)
    with _CACHE_LOCK:
        _CACHE.pop("custom-cards", None)


def _custom_cards() -> dict[str, dict[str, Any]]:
    """``{guid: raw custom record}`` (a fresh copy each call)."""
    records = _cached("custom-cards", CUSTOM_CARDS_PATH, _load_custom_cards)
    return {str(r["m_Id"]["m_Guid"]).lower(): json.loads(json.dumps(r)) for r in records}


# ---------------------------------------------------------------- reading

def _guid(value: Any) -> str:
    guid = str(value or "").strip().lower()
    if not _GUID.match(guid):
        raise ValueError("card GUIDs must be GUIDs")
    return guid


def _record(row: dict[str, Any]) -> dict[str, Any]:
    """A base card row in the editor's card shape."""
    return {
        "guid": str(row["guid"]).lower(),
        "name": row["name"] or "",
        "set_guid": (row["set_guid"] or "").lower(),
        "rarity": row["rarity"] or "",
        "cost": int(row["cost"] or 0),
        "attack": int(row["attack"] or 0),
        "defense": int(row["defense"] or 0),
        "card_type": row["card_type"] or "",
        "subtype": row["subtype"] or "",
        "threshold": _card_thresholds(row["threshold_json"]),
        "variable_cost": bool(int(row["variable_cost"] or 0)),
        "abilities": json.loads(row["abilities_json"] or "[]"),
        "socket_count": int(row["socket_count"] or 0),
        "pve": bool(int(row["is_pve"] or 0)),
        "faction": "None",
    }


def _records_extras() -> dict[str, dict[str, Any]]:
    return _cached("records-extras", _RECORDS_CARDS, _load_records_extras)


def _base_cards(db) -> dict[str, dict[str, Any]]:
    rows = db.execute(_BASE_SQL).fetchall()
    records = (_record(dict(zip(_BASE_COLUMNS, row))) for row in rows)
    cards = {}
    for record in records:
        record["faction"] = _records_extras().get(record["guid"], {}).get("faction", "None")
        cards[record["guid"]] = record
    return cards


def _base_card(db, guid: str) -> dict[str, Any] | None:
    row = db.execute(_BASE_SQL + " WHERE LOWER(guid)=?", (guid,)).fetchone()
    if not row:
        return None
    record = _record(dict(zip(_BASE_COLUMNS, row)))
    record["faction"] = _records_extras().get(guid, {}).get("faction", "None")
    return record


def _merged(db) -> dict[str, tuple[dict[str, Any], str]]:
    """Every card as ``{guid: (record, source)}``.

    source is ``records`` (untouched), ``edited`` (an overlay row shadows a base
    card), ``custom`` (a custom_sets card) or ``new`` (an overlay row with no base card).
    """
    base = _base_cards(db)
    overlay = campaign_data.load_card_overlay()
    custom = _custom_cards() if SHOW_CUSTOM_SETS else {}
    cards = {}
    for guid, record in base.items():
        cards[guid] = (overlay[guid], "edited") if guid in overlay else (record, "records")
    for guid, row in overlay.items():
        if guid not in base and guid not in custom:
            cards[guid] = (row, "new")
    for guid, raw in custom.items():
        cards[guid] = (_from_custom(raw), "custom")
    return cards


def frame_for(record: dict[str, Any], set_number: int = 0) -> dict[str, Any]:
    """The client's card frame for a card, by the rules of board3d.js ``BuildFrame``.

    Several shards, or a champion, frame as Multi. Otherwise an artifact frames as
    Artifact, and a card with one shard frames as that shard, and a colourless card
    as Shardless. A Resource card has no frame: the client paints it frameless.
    """
    types = [t for t in (record.get("card_type") or "").split("|") if t]
    shards = list(dict.fromkeys(t["shard"] for t in record.get("threshold") or []
                                if t.get("shard") != "Colorless"))
    rarity = record.get("rarity") or "Common"
    pve = bool(record.get("pve"))
    faction = record.get("faction") or "None"
    if "Resource" in types:
        return {"frame": None, "layout": "resource", "rarity_sprite": _rarity_sprite(rarity)}
    if "Champion" in types or len(shards) > 1:
        frame = "Multi"
    elif "Artifact" in types:
        frame = "Artifact"
    else:
        frame = shards[0] if shards else "Shardless"
    counts = {t["shard"]: t["count"] for t in record.get("threshold") or []}
    thresholds = [{"sprite": f"Threshold_{shard}", "count": counts[shard]}
                  for shard in FRAME_THRESHOLD_ORDER if counts.get(shard)]
    return {
        "frame": frame,
        "set_number": max(1, set_number),
        "layout": "troop" if ("Troop" in types or "Champion" in types) else "spell",
        "frame_sprites": [f"{frame}_{i}" for i in range(1, 5)] + [f"{frame}_Text"],
        "title_bar": "TitleBarPVE_1" if pve else "TitleBar",
        "threshold_sprites": thresholds,
        "faction_sprite": FACTION_SPRITE.get(faction, "Faction_Neutral"),
        "rarity_sprite": _rarity_sprite(rarity),
        "set_sprite": "Chapter01" if pve else f"SetIcon{max(1, set_number):02d}",
        "pve": pve,
        "sockets": min(int(record.get("socket_count") or 0), 3),
    }


def preview_frame(raw: Any, faction: str = "None", pve: bool = False) -> dict[str, Any]:
    """The frame for unsaved card fields, so the form can show it while they change."""
    fields = clean_fields(raw)
    record = dict(fields, faction=faction or "None", pve=bool(pve),
                  socket_count=_whole(raw.get("socket_count"), "sockets"))
    return frame_for(record, _set_number(fields["set_guid"]))


def _rarity_sprite(rarity: str) -> str:
    """The rarity disc the client draws; rarities it has no disc for show a dimmed Common."""
    known = rarity if rarity in FRAME_RARITIES else "Common"
    return f"Rarity_{known}"


def options() -> dict[str, Any]:
    """The choices the editor offers for a card's fields."""
    return {
        "rarities": list(RARITIES),
        "card_types": list(CARD_TYPES),
        "shards": list(THRESHOLD_SHARDS),
        "sets": card_sets(),
        # From the client sources: ECardAttributes with its ToolName display names, and
        # the IntAttrs names Records cards carry (with how many cards carry each).
        "keywords": card_attributes.keywords(),
        "int_attributes": card_attributes.int_attributes(),
        "impl_stages": impl_stages(),
        "equipment": _equipment_items(),
    }


def list_cards(db, query: str = "", limit: int = 200, set_guid: str | None = None) -> dict[str, Any]:
    """Cards whose name or GUID contains ``query``, by name, at most ``limit``.

    ``set_guid`` limits the list to one set; ``"none"`` lists the cards without a set.
    """
    needle = (query or "").strip().lower()
    set_names = _set_names()
    set_numbers = {entry["guid"]: entry["set_no"] for entry in card_sets()}
    merged = _merged(db)
    cards = []
    total = 0
    for guid, (record, source) in sorted(merged.items(), key=lambda item: item[1][0]["name"].lower()):
        if needle and needle not in record["name"].lower() and not guid.startswith(needle):
            continue
        if set_guid == "none" and record["set_guid"]:
            continue
        if set_guid and set_guid != "none" and record["set_guid"] != set_guid.lower():
            continue
        total += 1
        if len(cards) >= limit:
            continue
        cards.append(dict(_face_fields(guid, record), source=source,
                          set_name=set_names.get(record["set_guid"], ""),
                          frame=frame_for(record, set_numbers.get(record["set_guid"], 0))))
    return {"cards": cards, "total": total, "options": options()}


# The card fields the page paints a card face from (cards.js faceFromFields).
FACE_FIELDS = ("guid", "name", "set_guid", "rarity", "cost", "attack", "defense", "card_type",
               "subtype", "threshold", "variable_cost", "faction", "pve", "socket_count",
               "text", "flavor", "unique", "art_url")


def _face_fields(guid: str, record: dict[str, Any]) -> dict[str, Any]:
    """A card's face fields: its record, with Records text, flavour and art where it has none."""
    full = dict(_display_extras(guid, record), **record)
    return {name: full.get(name, "") for name in FACE_FIELDS}


def _display_extras(guid: str, record: dict[str, Any]) -> dict[str, Any]:
    """Text, flavour, unique flag and art for a card that the editor's record lacks."""
    if "art_url" in record:
        return {}
    extra = _records_extras().get(guid, {})
    art = extra.get("art", "")
    traits = _records_traits(guid)
    sockets = record["sockets"] if "sockets" in record else card_text.socket_types(extra.get("text", ""))
    return {
        "faction": extra.get("faction", "None"),
        "sockets": sockets,
        "text": _built_text(record.get("name") or extra.get("name", ""), extra.get("text", ""), traits,
                            extra.get("rage", 0), sockets, record.get("abilities") or []),
        "flavor": extra.get("flavor", ""),
        "unique": extra.get("unique", False),
        "art_url": f"/generated/cards/{art}.jpg" if re.fullmatch(r"a\d{7}", art) else "",
        **traits,   # keywords, integer attributes, implementation stage
    }


def impl_stages() -> list[dict[str, Any]]:
    """``[{name, label}]``: the client's ECardImplStage members, in order."""
    return [{"name": m["name"], "label": m["label"] or m["name"]}
            for m in client_schema.enum_members(IMPL_ENUM)]


def _default_stage() -> str:
    stages = impl_stages()
    return stages[0]["name"] if stages else ""


def _records_traits(guid: str) -> dict[str, Any]:
    """A card's traits as Records holds them (none, and the default stage, for a card
    Records lacks)."""
    extra = _records_extras().get(guid)
    return dict(card_attributes.records_card(guid),
                impl_stage=extra.get("impl_stage", IMPL_DONE) if extra else _default_stage())


def _ability_records(guids: list[Any], overlay: dict[str, Any]) -> list[dict[str, Any]]:
    """A card's ability records in its order, each ``{text, hidden}``: the m_GameText and
    the HiddenAbility attribute the client's card text reads."""
    found_all = []
    for guid in guids or []:
        found, _ = abilities.lookup("AbilityTemplate", str(guid).lower(), overlay)
        found = found or {}
        try:
            hidden = bool(card_attributes.tac_ints((found.get("m_SerializedTAC") or {}).get("data")).get("HiddenAbility"))
        except (ValueError, TypeError, IndexError, struct.error):
            hidden = False
        found_all.append({"text": str(found.get("m_GameText") or ""), "hidden": hidden})
    return found_all


def _built_text(name: str, own_text: str, traits: dict[str, Any], rage: int, sockets: list[str],
                guids: list[Any]) -> str:
    """The card's text as the client prints it (``card_text``), from its own text, traits,
    sockets and ability records."""
    overlay = campaign_data.load_ability_overlay()
    card = {"name": name, "own_text": own_text, "impl_stage": traits.get("impl_stage", IMPL_DONE),
            "keywords": traits.get("keywords") or [], "int_attributes": traits.get("int_attributes") or {},
            "rage": rage, "socket_types": sockets}
    return card_text.build(card, _ability_records(guids, overlay))["text"]


def preview_text(db, guid: str, fields: dict[str, Any]) -> dict[str, Any]:
    """The card text the client would print for the card as the form has it: the saved card,
    with the form's keywords, integer attributes, sockets and stage in place of the saved ones.
    A new card (no GUID) has no abilities or own text, only what the form gives."""
    guid = _guid(guid) if guid else ""
    name = str(fields.get("name") or "")
    if guid and guid in _merged(db):
        record, _source = _merged(db)[guid]
        full = dict(_display_extras(guid, record), **record)
        custom = _custom_cards().get(guid)
        if custom is not None:
            own_text = custom.get("m_GameText", "") or ""
        else:
            own_text = _records_extras().get(guid, {}).get("text", "")
        rage = int(full.get("rage") or _records_extras().get(guid, {}).get("rage", 0) or 0)
        abilities_guids = full.get("abilities") or []
        base = {"keywords": full.get("keywords") or [], "int_attributes": full.get("int_attributes") or {},
                "impl_stage": full.get("impl_stage") or IMPL_DONE}
        sockets = full.get("sockets") or []
    else:
        own_text, rage, abilities_guids, sockets = "", 0, [], []
        base = {"keywords": [], "int_attributes": {}, "impl_stage": IMPL_DONE}
    traits = dict(base)
    if "keywords" in fields:
        traits["keywords"] = list(fields.get("keywords") or [])
    if "int_attributes" in fields:
        traits["int_attributes"] = dict(fields.get("int_attributes") or {})
    if fields.get("impl_stage"):
        traits["impl_stage"] = fields["impl_stage"]
    if "sockets" in fields:
        sockets = _sockets(fields.get("sockets"))
    overlay = campaign_data.load_ability_overlay()
    text = card_text.build({"name": name, "impl_stage": traits["impl_stage"], "own_text": own_text,
                            "keywords": traits["keywords"], "int_attributes": traits["int_attributes"],
                            "rage": rage, "socket_types": sockets},
                           _ability_records(abilities_guids, overlay))
    return {"text": text["text"], "unresolved": text["unresolved"]}


def get_card(db, guid: str) -> dict[str, Any]:
    guid = _guid(guid)
    merged = _merged(db)
    if guid not in merged:
        raise LookupError("no such card")
    record, source = merged[guid]
    set_guid = record["set_guid"]
    record = dict(_display_extras(guid, record), **record)
    extra = _records_extras().get(guid, {})
    return {
        "card": dict(record),
        "ability_links": _ability_links_view(record),
        "source": source,
        "set_name": _set_names().get(set_guid, ""),
        "can_revert": source == "edited",
        "frame": frame_for(record, _set_number(set_guid)),
        # The equipped alternate of a base card lists the item each slot requires.
        "equipment_modified": bool(extra.get("equipment_modified")),
        "equipment_slots": list(extra.get("equipment_slots", [])),
        "options": options(),
    }


def _ability_links_view(record: dict[str, Any]) -> list[dict[str, Any]]:
    """The card's root abilities with their names and where each record comes from."""
    overrides = record.get("ability_overrides") or {}
    links = []
    for guid in record.get("abilities") or []:
        guid = str(guid).lower()
        found, source = abilities.lookup("AbilityTemplate", guid)
        links.append({"guid": guid, "name": str((found or {}).get("m_Name") or ""),
                      "source": source or "missing", "overrides": overrides.get(guid, [])})
    return links


# ---------------------------------------------------------------- writing

def _whole(value: Any, label: str) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a whole number")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a whole number") from None
    if not 0 <= number <= 99:
        raise ValueError(f"{label} must be between 0 and 99")
    return number


def _thresholds(raw: Any) -> list[dict[str, Any]]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError("threshold must be a list")
    out, seen = [], set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("each threshold must be {shard, count}")
        shard = str(entry.get("shard") or "")
        if shard not in THRESHOLD_SHARDS:
            raise ValueError(f"unknown threshold colour {shard!r}")
        if shard in seen:
            raise ValueError(f"{shard} is listed twice in the threshold")
        count = _whole(entry.get("count"), "threshold count")
        if count < 1:
            raise ValueError("a threshold needs at least 1")
        seen.add(shard)
        out.append({"shard": shard, "count": count})
    return out


def clean_fields(raw: Any) -> dict[str, Any]:
    """Validate the editable card fields; raises ValueError with a readable message."""
    if not isinstance(raw, dict):
        raise ValueError("card fields must be an object")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("a card needs a name")
    rarity = str(raw.get("rarity") or "")
    if rarity not in RARITIES:
        raise ValueError(f"unknown rarity {rarity!r}")
    card_type = str(raw.get("card_type") or "")
    if card_type not in CARD_TYPES:
        raise ValueError(f"unknown card type {card_type!r}")
    return {
        "name": name,
        "set_guid": _set_guid(raw.get("set_guid")),
        "rarity": rarity,
        "cost": _whole(raw.get("cost"), "cost"),
        "attack": _whole(raw.get("attack"), "attack"),
        "defense": _whole(raw.get("defense"), "defense"),
        "card_type": card_type,
        "subtype": str(raw.get("subtype") or "").strip(),
        "threshold": _thresholds(raw.get("threshold")),
        "variable_cost": bool(raw.get("variable_cost")),
        "flavor": _flavor(raw.get("flavor")),
        "sockets": _sockets(raw.get("sockets")),
    }


def _sockets(raw: Any) -> list[str]:
    """A card's sockets in order: ``Major``/``Minor`` names (any case), at most six."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("sockets must be a list")
    if len(raw) > 6:
        raise ValueError("a card has at most six sockets")
    out = []
    for entry in raw:
        kind = str(entry or "").strip().capitalize()
        if kind not in card_text.SOCKET_TYPES:
            raise ValueError(f"a socket is Major or Minor, not {entry!r}")
        out.append(kind)
    return out


def clean_traits(raw: Any) -> dict[str, Any]:
    """The card's keywords and integer attributes that ``raw`` gives, checked against
    the client's enum and attribute names (a field ``raw`` lacks is left out)."""
    if not isinstance(raw, dict):
        raise ValueError("card fields must be an object")
    out: dict[str, Any] = {}
    if "keywords" in raw:
        out["keywords"] = card_attributes.clean_keywords(raw["keywords"])
    if "int_attributes" in raw:
        out["int_attributes"] = card_attributes.clean_int_attributes(raw["int_attributes"])
    if "impl_stage" in raw:
        stage = str(raw["impl_stage"] or "")
        if stage not in {m["name"] for m in impl_stages()}:
            raise ValueError(f"{stage!r} is not an implementation stage (an {IMPL_ENUM} member)")
        out["impl_stage"] = stage
    return out


def _overlay_traits(guid: str, traits: dict[str, Any], prior: dict[str, Any] | None) -> dict[str, Any]:
    """The traits an overlay row keeps: the new value, else the row's own, and only
    where it differs from the Records card (which shows otherwise)."""
    records = _records_traits(guid)
    out = {}
    for field in TRAIT_FIELDS:
        value = traits[field] if field in traits else (prior or {}).get(field)
        if value is not None and value != records[field]:
            out[field] = value
    return out


def _flavor(value: Any) -> str:
    """Flavour text drawn at the foot of the card; "" leaves the card's own flavour."""
    text = str(value or "").strip()
    if len(text) > 500:
        raise ValueError("flavour text must be at most 500 characters")
    return text


def _overlay_fields(guid: str, fields: dict[str, Any]) -> dict[str, Any]:
    """The fields an overlay row keeps: ``flavor`` only when it is set and differs from
    the Records flavour, so an empty or unchanged value lets the Records flavour show."""
    row = dict(fields)
    if not row["flavor"] or row["flavor"] == _records_extras().get(guid, {}).get("flavor", ""):
        row.pop("flavor")
    return row


def _ability_links(raw: Any) -> list[dict[str, Any]]:
    """Validate a card's ability links: ``[{guid, overrides}]`` (or bare GUIDs).

    Each GUID must be an AbilityTemplate in Records or the ability overlay, once.
    """
    if not isinstance(raw, list):
        raise ValueError("abilities must be a list")
    links, seen = [], set()
    for entry in raw:
        entry = entry if isinstance(entry, dict) else {"guid": entry}
        guid = str(entry.get("guid") or "").strip().lower()
        if not _GUID.match(guid):
            raise ValueError(f"ability {guid or '(blank)'} is not a GUID")
        if guid in seen:
            raise ValueError(f"ability {guid} is linked twice")
        if not abilities.exists("AbilityTemplate", guid):
            raise ValueError(f"no ability {guid} in Records or the ability overlay")
        overrides = entry.get("overrides") or []
        if not isinstance(overrides, list):
            raise ValueError(f"ability {guid}: overrides must be a list")
        seen.add(guid)
        links.append({"guid": guid, "overrides": overrides})
    return links


def _overlay_abilities(links: list[dict[str, Any]]) -> dict[str, Any]:
    """Ability links as overlay row fields: the GUIDs, and any non-empty overrides."""
    overrides = {link["guid"]: link["overrides"] for link in links if link["overrides"]}
    return {"abilities": [link["guid"] for link in links],
            **({"ability_overrides": overrides} if overrides else {})}


def save_card(db, guid: str, raw: Any) -> dict[str, Any]:
    """Create a card (``guid`` blank), edit a custom card, or shadow/update a Records card.

    ``raw["abilities"]`` (optional) replaces the card's ability links; without it they stay.
    ``raw["keywords"]`` and ``raw["int_attributes"]`` (optional) likewise.
    """
    fields = clean_fields(raw)
    traits = clean_traits(raw)
    links = _ability_links(raw["abilities"]) if "abilities" in raw else None
    with _WRITE_LOCK:
        existing_guid = bool(guid)
        if guid:
            guid = _guid(guid)
            if guid in _custom_cards():
                records = _custom_list()
                for entry in records:
                    if str(entry["m_Id"]["m_Guid"]).lower() == guid:
                        _apply_to_custom(entry, fields)
                        _apply_traits(entry, traits)
                        if links is not None:
                            entry["m_CardAbilities"] = _card_abilities(links)
                _write_custom(records)
                return get_card(db, guid)
        overlay = campaign_data.load_card_overlay()
        prior_row = None
        if guid:
            base = _base_card(db, guid)
            if base is None and guid not in overlay:
                raise LookupError("no such card")
            prior = prior_row = overlay.get(guid) or base
            kept = {"abilities": prior["abilities"]}
            if prior.get("ability_overrides"):
                kept["ability_overrides"] = prior["ability_overrides"]
        else:
            guid = str(uuid.uuid4())
            kept = {"abilities": []}
        linked = _overlay_abilities(links) if links is not None else kept
        if not existing_guid and _is_custom_set(fields["set_guid"]):
            # A card added to a custom set lives in that set's file, with the set's other cards.
            records = _custom_list()
            record = _custom_record(guid, dict(fields, ability_links=links or []))
            _apply_traits(record, traits)
            records.append(record)
            _write_custom(records)
        else:
            overlay[guid] = dict(_overlay_fields(guid, fields), guid=guid, **linked,
                                 **_overlay_traits(guid, traits, prior_row))
            campaign_data.write_card_overlay(overlay)
    return get_card(db, guid)


def _is_custom_set(set_guid: str) -> bool:
    return any(entry["guid"] == set_guid and entry["source"] == "custom" for entry in card_sets())


def _custom_record(guid: str, fields: dict[str, Any]) -> dict[str, Any]:
    """A new custom CardTemplate record in the shape custom_sets/CardTemplates.json uses."""
    return {
        "m_Id": {"m_Guid": guid},
        "m_SetId": {"m_Guid": fields["set_guid"]},
        "m_DesignerCardId": "",
        "m_Name": fields["name"],
        "m_CardNumber": 0,
        "m_CardType": fields["card_type"],
        "m_CardSubtype": fields["subtype"],
        "m_CardRarity": fields["rarity"],
        "m_ResourceCost": fields["cost"],
        "m_VariableCost": 1 if fields["variable_cost"] else 0,
        "m_Threshold": [{"m_ColorFlags": t["shard"], "m_ThresholdColorRequirement": t["count"]}
                        for t in fields["threshold"]],
        "m_BaseAttackValue": fields["attack"],
        "m_BaseDefenseValue": fields["defense"],
        "m_Unique": 0,
        "m_AttributeFlags": card_attributes.NO_FLAGS,
        "m_Faction": "None",
        "m_GameText": card_text.with_sockets("", fields["sockets"]),
        "m_FlavorText": fields["flavor"],
        "m_ArtistName": "",
        "m_CardImagePath": "",
        "m_CardAbilities": _card_abilities(fields.get("ability_links") or []),
        "m_SocketCount": len(fields["sockets"]),
    }


def revert_card(db, guid: str) -> dict[str, Any]:
    """Drop the edits to a Records card so the Records version shows again."""
    guid = _guid(guid)
    with _WRITE_LOCK:
        if guid in _custom_cards():
            raise ValueError("a custom card has no Records version to revert to")
        overlay = campaign_data.load_card_overlay()
        if guid not in overlay:
            raise LookupError("this card has no edits")
        if _base_card(db, guid) is None:
            raise ValueError("a new card has no Records version to revert to")
        del overlay[guid]
        campaign_data.write_card_overlay(overlay)
    return get_card(db, guid)


def assign_to_set(db, guids: Any, set_guid: Any) -> dict[str, Any]:
    """Set ``set_guid`` on each card (blank clears it). Custom cards are written to their file."""
    if not isinstance(guids, list) or not guids:
        raise ValueError("choose at least one card")
    target = _set_guid(set_guid)
    wanted = [_guid(guid) for guid in guids]
    with _WRITE_LOCK:
        custom = _custom_cards()
        overlay = campaign_data.load_card_overlay()
        base = _base_cards(db)
        custom_records = _custom_list()
        custom_changed = False
        for guid in wanted:
            if guid in custom:
                for entry in custom_records:
                    if str(entry["m_Id"]["m_Guid"]).lower() == guid:
                        entry["m_SetId"] = {"m_Guid": target}
                        custom_changed = True
                continue
            if guid in overlay:
                row = overlay[guid]
            elif guid in base:
                row = base[guid]
            else:
                raise LookupError(f"no such card: {guid}")
            overlay[guid] = dict(row, set_guid=target)
        if custom_changed:
            _write_custom(custom_records)
        campaign_data.write_card_overlay(overlay)
    return {"assigned": len(wanted), "set_guid": target, "set_name": _set_names().get(target, "")}


def all_card_guids() -> set[str]:
    """GUIDs of the cards the overlay and the custom set define (Records cards aside)."""
    return set(campaign_data.load_card_overlay()) | set(_custom_cards())
