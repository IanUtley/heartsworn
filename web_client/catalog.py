"""Static card, champion and ability descriptions for the browser.

The Unity client ships these definitions in its gamedata; the browser asks
for the ones it needs by GUID. Numbers come from the server's seeded
``card_templates`` projection, display text and art numbers from the same
Records snapshot the engine reads.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

import db as dbmod
from gamedata import DEFAULT_RECORD_STORE, ability_graph

ROOT = Path(__file__).resolve().parents[1]
ART_OUT = Path(__file__).resolve().parent / "generated" / "art"
# Card art the campaign editor already decoded from the client install.
EDITOR_ART = ROOT / "campaign_editor" / "generated" / "cards"
SHARDS = ["Colorless", "Blood", "Ruby", "Sapphire", "Wild", "Diamond"]

_cache: dict[tuple[str, str], dict | None] = {}
_cache_lock = threading.Lock()

# Sets legal in Standard, and the core-commons sets that count as them
# (BuiltInResources.Format_Standard / CoreCommonsMap in the client). A card
# with no Standard version is drawn with the client's Immortal cost gem.
STANDARD_SETS = (
    "ecdbc188-5750-48ef-acac-05e2bcbcc46f", "fbbac856-2264-4d31-97b0-0d8a646b9597",
    "326602fa-e183-4dfe-8300-55cc0c7c4ce8", "9a824393-cd11-4273-a05e-41e35eb50dbe",
    "54f14f51-2afe-4a26-be28-d251b06a9cc4", "c4bd8bd9-b944-44bd-abf7-cd73dc7758c7",
    "150175a2-8efd-494a-97d6-89bda52be886")


def _thresholds(threshold_json):
    try:
        values = json.loads(threshold_json or "{}").get("values") or []
    except (TypeError, ValueError, AttributeError):
        values = []
    return {SHARDS[i]: int(v) for i, v in enumerate(values)
            if i < len(SHARDS) and int(v or 0) > 0}


def _record(section, guid):
    try:
        return DEFAULT_RECORD_STORE.get(section, guid)
    except Exception:
        return None


def _set_number(set_guid) -> int:
    """The set's number, which picks the SetIconNN badge on the card face."""
    record = _record("CardSetTemplate", str(set_guid or "").lower())
    try:
        return int(record.field("m_SetNo", 0) or 0) if record is not None else 0
    except (TypeError, ValueError):
        return 0


def card(guid: str) -> dict | None:
    guid = str(guid).lower()
    standard = ",".join("?" * len(STANDARD_SETS))
    row = dbmod._db.execute(
        "SELECT name, cost, attack, defense, card_type, rarity, "
        "threshold_json, subtype, socket_count, is_pve, "
        "current_resources_granted, max_resources_granted, "
        "variable_cost, variable_cost_double, set_guid, "
        f"EXISTS(SELECT 1 FROM card_templates o WHERE o.name = t.name "
        f"AND LOWER(o.set_guid) IN ({standard})) "
        "FROM card_templates t WHERE LOWER(guid)=?",
        (*STANDARD_SETS, guid)).fetchone()
    record = _record("CardTemplate", guid)
    if row is None and record is None:
        return champion(guid)
    out = {"guid": guid, "kind": "card"}
    if row is not None:
        (name, cost, attack, defense, card_type, rarity, thresholds, sub,
         sockets, pve, res_now, res_max, var_cost, var_double, set_guid,
         standard_legal) = row
        out.update(name=name, cost=int(cost or 0), attack=int(attack or 0),
                   defense=int(defense or 0), type=str(card_type or ""),
                   rarity=str(rarity or ""), subtype=str(sub or ""),
                   thresholds=_thresholds(thresholds),
                   sockets=int(sockets or 0), pve=bool(pve),
                   resources=[int(res_now or 0), int(res_max or 0)],
                   variable="XX" if var_double else ("X" if var_cost else ""))
        out["set"] = _set_number(set_guid)
        # CardTemplate.IsImmortal: not PvE, no Standard version, not a basic shard.
        basic = "Resource" in out["type"] and "Standard" in out["subtype"]
        out["immortal"] = not pve and not standard_legal and not basic
    if record is not None:
        out.setdefault("name", str(record.field("m_Name", "")))
        out.setdefault("type", str(record.field("m_CardType", "")))
        # The gamedata names the card-play clip by file; the clip's name in the
        # client bundle is that file's stem (Aud_FX_Card_Play_...).
        spawn = str(record.field("m_SoundEventOnSpawn", "") or "").replace("\\", "/")
        # The card face needs every type flag (an artifact troop is framed as an
        # artifact) and the faction badge; the projection keeps one type only.
        out.update(types=[t for t in str(record.field("m_CardType", "") or "").split("|") if t],
                   faction=str(record.field("m_Faction", "") or "None"),
                   unique=bool(record.field("m_Unique", 0)),
                   unlimited=bool(record.field("m_Unlimited", 0)))
        out.update(text=str(record.field("m_GameText", "") or ""),
                   flavor=str(record.field("m_FlavorText", "") or ""),
                   art=str(record.field("m_ArtNumber", "") or ""),
                   artist=str(record.field("m_ArtistName", "") or ""),
                   sound=Path(spawn).stem if spawn else "")
        if "thresholds" not in out:
            out["thresholds"] = {}
            for entry in record.field("m_Threshold") or ():
                color = str(entry.get("m_ColorFlags") or "")
                if color in SHARDS:
                    out["thresholds"][color] = int(
                        entry.get("m_ThresholdColorRequirement") or 0)
    return out


def champion(guid: str) -> dict | None:
    record = _record("ChampionTemplate", str(guid).lower())
    if record is None:
        return None
    return {"guid": str(guid).lower(), "kind": "champion",
            "name": str(record.field("m_Name", "") or ""),
            "type": "Champion",
            "subtype": str(record.field("m_SubType", "") or ""),
            "race": str(record.field("m_Race", "") or ""),
            "class": str(record.field("m_Class", "") or ""),
            "health": int(record.field("m_StartingHealth", 0) or 0),
            "text": str(record.field("m_GameText", "") or ""),
            "art": str(record.field("m_ArtNumber", "") or "")}


def ability(guid: str) -> dict | None:
    guid = str(guid).lower()
    record = _record("AbilityTemplate", guid)
    if record is None:
        return None
    out = {"guid": guid, "kind": "ability",
           "name": str(record.field("m_Name", "") or ""),
           "text": str(record.field("m_GameText", "") or ""),
           "manual": bool(getattr(record, "manual", False))}
    try:
        graph = ability_graph(DEFAULT_RECORD_STORE, guid)
        costs = graph.costs.as_dict() if graph is not None else {}
    except Exception:
        costs = {}
    out["costs"] = {key: value for key, value in costs.items()
                    if key in ("activation", "charge_points", "spell_points",
                               "life", "is_charge_power", "is_spell_power")}
    return out


_LOOKUPS = {"cards": card, "abilities": ability}


def describe(request: dict) -> dict:
    """``{"cards": [...], "abilities": [...]}`` -> descriptions by GUID."""
    out = {}
    for section, lookup in _LOOKUPS.items():
        found = {}
        for guid in (request.get(section) or ())[:500]:
            key = (section, str(guid).lower())
            with _cache_lock:
                cached = _cache.get(key, False)
            if cached is False:
                cached = lookup(key[1])
                with _cache_lock:
                    _cache[key] = cached
            if cached is not None:
                found[key[1]] = cached
        out[section] = found
    return out


# ----------------------------------------------------------------------
# Card art from a local client install (optional)
# ----------------------------------------------------------------------

_art_locations = None
_art_lock = threading.Lock()


def art_path(number: str) -> Path | None:
    """Return one card's art, decoding it from the client install if needed.

    Art the campaign editor already extracted is served first. Otherwise it is
    decoded from the client install on first request, using the campaign
    editor's HARC reader. The install is ``HEX_CLIENT_ROOT``
    (or ``/client-data``, the container mount); without one the browser falls
    back to its drawn card frames.
    """
    global _art_locations
    if not re.fullmatch(r"a\d{7}", number or ""):
        return None
    target = ART_OUT / f"{number}.jpg"
    if target.is_file():
        return target
    editor_copy = EDITOR_ART / f"{number}.jpg"
    if editor_copy.is_file():
        return editor_copy
    client = Path(os.environ.get("HEX_CLIENT_ROOT") or "/client-data")
    if not (client / "AssetBundles").is_dir():
        return None
    try:
        import sys
        extractors = str(ROOT / "campaign" / "AssetExtraction")
        if extractors not in sys.path:
            sys.path.insert(0, extractors)
        import extract_card_art
    except Exception:
        return None
    with _art_lock:
        if _art_locations is None:
            _art_locations = extract_card_art.harc_locations(client)
    return extract_card_art.extract_card(_art_locations, number, size=512,
                                         out=ART_OUT)
