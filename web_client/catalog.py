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

from db import _db
from gamedata import DEFAULT_RECORD_STORE, ability_graph

ROOT = Path(__file__).resolve().parents[1]
ART_OUT = Path(__file__).resolve().parent / "generated" / "art"
SHARDS = ["Colorless", "Blood", "Ruby", "Sapphire", "Wild", "Diamond"]

_cache: dict[tuple[str, str], dict | None] = {}
_cache_lock = threading.Lock()


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


def card(guid: str) -> dict | None:
    guid = str(guid).lower()
    row = _db.execute(
        "SELECT name, cost, attack, defense, card_type, rarity, "
        "threshold_json, subtype FROM card_templates WHERE LOWER(guid)=?",
        (guid,)).fetchone()
    record = _record("CardTemplate", guid)
    if row is None and record is None:
        return champion(guid)
    out = {"guid": guid, "kind": "card"}
    if row is not None:
        name, cost, attack, defense, card_type, rarity, thresholds, sub = row
        out.update(name=name, cost=int(cost or 0), attack=int(attack or 0),
                   defense=int(defense or 0), type=str(card_type or ""),
                   rarity=str(rarity or ""), subtype=str(sub or ""),
                   thresholds=_thresholds(thresholds))
    if record is not None:
        out.setdefault("name", str(record.field("m_Name", "")))
        out.setdefault("type", str(record.field("m_CardType", "")))
        out.update(text=str(record.field("m_GameText", "") or ""),
                   flavor=str(record.field("m_FlavorText", "") or ""),
                   art=str(record.field("m_ArtNumber", "") or ""),
                   artist=str(record.field("m_ArtistName", "") or ""))
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
    """Decode one card's art from the client install on first request.

    Uses the campaign editor's HARC reader. The install is ``HEX_CLIENT_ROOT``
    (or ``/client-data``, the container mount); without one the browser falls
    back to its drawn card frames.
    """
    global _art_locations
    if not re.fullmatch(r"a\d{7}", number or ""):
        return None
    target = ART_OUT / f"{number}.jpg"
    if target.is_file():
        return target
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
