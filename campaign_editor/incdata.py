"""Build a client ``.incdata`` file for one card set: its finished cards and their abilities.

The client loads every ``Data/*.incdata`` at start-up on top of ``Data/gamedata``
(``TemplateManager.CheckForNewData`` -> ``IncrementalLoad`` -> ``LoadAbilitiesFromBall``),
and the server can push the same bytes as ``ProfileMainData``. The file is gzip of UTF-8
text: sections ``$$$---$$$\\n<Type>\\n``, each holding records separated by ``$$--$$``.
A record is TDF JSON as in ``hex-private/Records``: ``"_v"`` (the class version of each
class in it, which ``DefineBits`` reads fields by), ``"_t"`` and the ``m_`` fields. A
record with a GUID the client already has replaces it (``TemplateManager.AddTemplate``).

What a set's file holds:

* a custom set (``custom_sets/``): its CardSetTemplate and every card in it whose
  implementation stage is Done;
* a Records set: the cards of it the editor changed (overlay rows) that are Done;
* for either: the ability records those cards use that Records lacks or the editor
  changed (the ability overlay), with the effects, conditions, targets and further
  abilities they name.

A card not Done is left out: the client would not load it (``AddCardTemplate`` skips
NeedsScripting and earlier) or let a player own it (``CardTemplate.IsOwnable``).

A Records card is written as its Records record with the editor's changes; a custom card
from the client's field defaults (``client_schema``) with its own fields, and the portrait
window its art bundle was composed for (``build_custom_art_bundles.LAYOUT``) as
``m_DefaultLayout``. Nothing is written to disk here.
"""

from __future__ import annotations

import gzip
import json
import re
import threading
from collections import Counter, defaultdict
from typing import Any

from paths import RECORDS, REPO  # noqa: E402  (also sets sys.path)
import campaign_data  # noqa: E402
import card_abilities as abilities  # noqa: E402
import card_attributes  # noqa: E402
import card_text  # noqa: E402
import cards  # noqa: E402
import client_schema  # noqa: E402

SECTION = "$$$---$$$"
RECORD = "$$--$$"
CARD_TYPE = "Reckoning.Game.CardTemplate"
SET_TYPE = "Reckoning.Game.CardSetTemplate"
PACK_TYPE = "Reckoning.Game.InventoryCardPack"
ZERO = "00000000-0000-0000-0000-000000000000"
# The portrait window of a custom card's art (AssetExtraction/build_custom_art_bundles.py).
LAYOUT = {"m_PortraitLeft": 0.07, "m_PortraitRight": 0.92,
          "m_PortraitTop": 0.0138689652, "m_PortraitBottom": 0.74339366}
# Ability record fields that name another ability record, by section.
LINKS = {"AbilityEffectTemplate": ("m_GrantedAbilityTemplateId", "m_AbilityToInvoke")}

_LOCK = threading.Lock()
_VERSIONS: dict[str, Any] = {}


# ---------------------------------------------------------------- class versions

def _versions() -> dict[str, Any]:
    """``{"class": {short type: version}, "root": {short type: {class: version}}}`` from
    every Records record's ``_v`` (one version per class across Records): ``root`` is the
    table most records of that type carry."""
    with _LOCK:
        if _VERSIONS:
            return _VERSIONS
        by_class: dict[str, int] = {}
        by_root: dict[str, Counter] = defaultdict(Counter)
        pattern = re.compile(r'"_v"\s*:\s*(\[[^\]]*\])\s*,\s*"_t"\s*:\s*"([\w.]+)"')
        for path in sorted(RECORDS.glob("*.jsonl")):
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        text = json.loads(line)
                    except ValueError:
                        continue
                    found = pattern.search(text) if isinstance(text, str) else None
                    if not found:
                        continue
                    root = found.group(2).rsplit(".", 1)[-1]
                    table = []
                    for entry in json.loads(found.group(1)):
                        for name, version in entry.items():
                            by_class[name] = int(version)
                            table.append((name, int(version)))
                    by_root[root][tuple(table)] += 1
        _VERSIONS.update({"class": by_class,
                          "root": {root: dict(found.most_common(1)[0][0]) for root, found in by_root.items()}})
        return _VERSIONS


def _types_in(value: Any, out: set[str]) -> set[str]:
    if isinstance(value, dict):
        if value.get("_t"):
            out.add(str(value["_t"]).rsplit(".", 1)[-1])
        for item in value.values():
            _types_in(item, out)
    elif isinstance(value, list):
        for item in value:
            _types_in(item, out)
    return out


def tdf(record: dict[str, Any]) -> str:
    """One record as TDF JSON text: ``_v``, ``_t``, then its fields. ``_v`` is the record's
    own (a Records record) or the table most Records records of its type carry, with every
    other class the record holds added."""
    versions = _versions()
    root = str(record["_t"]).rsplit(".", 1)[-1]
    own = {name: version for entry in record.get("_v") or [] for name, version in entry.items()}
    table = own or dict(versions["root"].get(root) or {})
    for name in sorted(_types_in(record, set())):
        if name not in table and name in versions["class"]:
            table[name] = versions["class"][name]
    body = {"_v": [{name: version} for name, version in table.items()], "_t": record["_t"]}
    body.update((key, value) for key, value in record.items() if key not in ("_t", "_v"))
    return json.dumps(body, indent=1, ensure_ascii=False)


def pack(sections: dict[str, list[dict[str, Any]]]) -> bytes:
    """The ``.incdata`` bytes: gzip of the sections' TDF text."""
    text = "".join(f"{SECTION}\n{name}\n" + "".join(f"{RECORD}\n{tdf(record)}\n" for record in records)
                   for name, records in sections.items() if records)
    return gzip.compress(text.encode("utf-8"), mtime=0)


def unpack(data: bytes) -> dict[str, list[dict[str, Any]]]:
    """The sections of ``.incdata`` bytes, each record as JSON (for checks and tests)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for section in gzip.decompress(data).decode("utf-8").split(SECTION):
        parts = section.split(RECORD)
        name = parts[0].strip()
        if name:
            out[name] = [json.loads(part) for part in parts[1:] if part.strip()]
    return out


# ---------------------------------------------------------------- records

def _defaults(type_name: str) -> dict[str, Any]:
    """A record of a client class with every field at its client default."""
    out: dict[str, Any] = {"_t": type_name}
    for name, spec in client_schema.fields(type_name).items():
        out[name] = client_schema.default_value(spec)
    return out


def _guid_ref(guid: str) -> dict[str, str]:
    return {"m_Guid": guid or ZERO}


def _card_abilities(card: dict[str, Any]) -> list[dict[str, Any]]:
    overrides = card.get("ability_overrides") or {}
    return [{"m_CardAbilityId": _guid_ref(guid),
             "m_CardAbilityOverrides": {"m_OverrideValues": overrides.get(guid, [])}}
            for guid in card.get("abilities") or []]


def _apply_card(record: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    """The editor's card fields written into a CardTemplate record."""
    if "sockets" in card:
        # The sockets are tokens in the game text, and m_SocketCount counts them.
        record["m_GameText"] = card_text.with_sockets(record.get("m_GameText") or "", card["sockets"])
        record["m_SocketCount"] = len(card["sockets"])
    record.update({
        "m_Name": card["name"],
        "m_SetId": _guid_ref(card.get("set_guid") or ""),
        "m_CardRarity": card["rarity"],
        "m_ResourceCost": int(card.get("cost") or 0),
        "m_BaseAttackValue": int(card.get("attack") or 0),
        "m_BaseDefenseValue": int(card.get("defense") or 0),
        "m_CardType": card["card_type"],
        "m_CardSubtype": card.get("subtype") or "",
        "m_Threshold": [{"m_ColorFlags": t["shard"], "m_ThresholdColorRequirement": int(t["count"])}
                        for t in card.get("threshold") or []],
        "m_VariableCost": 1 if card.get("variable_cost") else 0,
        "m_CardAbilities": _card_abilities(card),
        "m_AttributeFlags": card_attributes.flags_text(card.get("keywords") or []),
        "m_ImplStage": card.get("impl_stage") or cards.IMPL_DONE,
    })
    if card.get("flavor"):
        record["m_FlavorText"] = card["flavor"]
    tac = (record.get("m_SerializedTAC") or {}).get("data") if isinstance(record.get("m_SerializedTAC"), dict) else None
    ints = card.get("int_attributes") or {}
    if ints or tac:
        record["m_SerializedTAC"] = {"data": card_attributes.with_ints(tac, ints)}
    return record


def _custom_card_record(raw: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    """A custom card as a full CardTemplate: client defaults, its own m_ fields (those the
    client class has), the editor's fields, and the art bundle's portrait window."""
    record = _defaults(CARD_TYPE)
    known = client_schema.fields(CARD_TYPE)
    record.update({key: value for key, value in raw.items() if key in known})
    layout = dict(record.get("m_DefaultLayout") or {}, m_Id=_guid_ref(""), m_Name="",
                  m_PortraitRotation=0, m_PortraitCenterX=0.5, m_PortraitCenterY=0.5,
                  m_PortraitRadius=0.5, **LAYOUT)
    record["m_DefaultLayout"] = layout
    return _apply_card(record, card)


def _set_record(entry: dict[str, Any]) -> dict[str, Any]:
    record = _defaults(SET_TYPE)
    known = client_schema.fields(SET_TYPE)
    raw = {key: value for key, value in entry.items() if key in known}
    record.update(raw)
    return record


def _ability_records(guids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """The ability overlay records these abilities need, following every reference to an
    overlay record (Records' own records the client already has)."""
    overlay = campaign_data.load_ability_overlay()
    out: dict[str, dict[str, dict[str, Any]]] = {section: {} for section in campaign_data.ABILITY_SECTIONS}
    queue = [("AbilityTemplate", guid) for guid in guids]
    while queue:
        section, guid = queue.pop()
        guid = str(guid or "").lower()
        if not guid or guid == ZERO or guid in out[section]:
            continue
        record = overlay.get(section, {}).get(guid)
        if record is None:
            continue   # Records' own: the client has it
        out[section][guid] = record
        if section == "AbilityTemplate":
            for entry in record.get("m_AbilityEffectList") or []:
                queue.append(("AbilityEffectTemplate", (entry.get("m_EffectTemplateId") or {}).get("m_Guid")))
                queue.append(("AbilityEffectConditionTemplate", (entry.get("m_ConditionId") or {}).get("m_Guid")))
            for ref in record.get("m_AbilityTargetTemplateIds") or []:
                queue.append(("AbilityTargetTemplate", ref.get("m_Guid")))
            for field in ("m_SacrificeTarget", "m_VoidTarget", "m_ExhaustTarget"):
                queue.append(("AbilityTargetTemplate", (record.get(field) or {}).get("m_Guid")))
        for field in LINKS.get(section, ()):
            queue.append(("AbilityTemplate", (record.get(field) or {}).get("m_Guid")))
    return {section: list(records.values()) for section, records in out.items()}


def build(db, set_guid: str) -> dict[str, Any]:
    """``{data, filename, summary}`` for one set's ``.incdata`` (nothing is written)."""
    set_guid = str(set_guid or "").strip().lower()
    entry = next((s for s in cards.card_sets() if s["guid"] == set_guid), None)
    if entry is None:
        raise LookupError("choose a card set")
    custom = entry["source"] == "custom"
    merged = cards._merged(db)
    raw_custom = cards._custom_cards()
    chosen, skipped = [], Counter()
    for guid, (row, source) in sorted(merged.items()):
        if row.get("set_guid") != set_guid:
            continue
        if not custom and source not in ("edited", "new"):
            continue   # a Records set's file holds only what the editor changed
        card = dict(cards._display_extras(guid, row), **row)   # as get_card shows it
        if card.get("impl_stage") != cards.IMPL_DONE:
            skipped[card.get("impl_stage") or "(none)"] += 1
            continue
        chosen.append((guid, source, card))
    card_records = []
    for guid, source, card in chosen:
        if source == "custom":
            card_records.append(_custom_card_record(raw_custom[guid], card))
        elif source == "edited":
            card_records.append(_apply_card(abilities._records_record("CardTemplate", guid), card))
        else:   # an editor card in the overlay: built like a custom one
            card_records.append(_custom_card_record({"m_Id": _guid_ref(guid), "m_CardImagePath": ""}, card))
    linked = [guid for _, _, card in chosen for guid in card.get("abilities") or []]
    ability_sections = _ability_records(linked)
    sections: dict[str, list[dict[str, Any]]] = {}
    if custom:
        custom_set = next(s for s in _custom_sets() if str(s["m_Id"]["m_Guid"]).lower() == set_guid)
        sections["CardSetTemplate"] = [_set_record(custom_set)]
        sections["InventoryItemData"] = [_pack_record(raw) for raw in campaign_data.load_custom_packs()
                                         if str(raw["m_SetId"]["m_Guid"]).lower() == set_guid]
    for section in ("AbilityTargetTemplate", "AbilityEffectConditionTemplate",
                    "AbilityEffectTemplate", "AbilityTemplate"):
        sections[section] = ability_sections[section]
    sections["CardTemplate"] = card_records
    data = pack(sections)
    summary = {"set": entry["name"], "custom": custom, "cards": len(card_records),
               "skipped_not_done": dict(skipped),
               "records": {name: len(records) for name, records in sections.items() if records},
               "bytes": len(data)}
    name = re.sub(r"[^a-z0-9]+", "_", (entry.get("bundle") or entry["name"]).lower()).strip("_") or "set"
    return {"data": data, "filename": f"{name}.incdata", "summary": summary}


def _pack_record(raw: dict[str, Any]) -> dict[str, Any]:
    """A custom card pack as an InventoryCardPack: client defaults, its own m_ fields the
    client class has, and its artwork GUIDs as the paths the client reads: the store icon
    (``m_Icon``) and the pack artwork (``m_TexturePath``), named by GUID as card art is."""
    record = _defaults(PACK_TYPE)
    known = client_schema.fields(PACK_TYPE)
    record.update({key: value for key, value in raw.items() if key in known})
    record["m_Icon"] = f"custom_sets/store_art/{raw['m_StoreArtGuid']}.jpg"
    record["m_TexturePath"] = f"custom_sets/pack_art/{raw['m_PackArtGuid']}.jpg"
    return record


PUBLISHED_DIR = REPO / "campaign" / "incdata" / "published"


def publish(db, set_guid: str) -> dict[str, Any]:
    """Write a set's .incdata to ``campaign/incdata/published``, where the server sends it to each
    client at login (ProfileGenericUpdate). Returns the file and the build summary."""
    built = build(db, set_guid)
    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    target = PUBLISHED_DIR / built["filename"]
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_bytes(built["data"])
    temporary.replace(target)
    shown = target.relative_to(REPO) if REPO in target.parents else target
    return {"file": str(shown), "summary": built["summary"]}


def _custom_sets() -> list[dict[str, Any]]:
    path = cards.CUSTOM_SETS_PATH
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
