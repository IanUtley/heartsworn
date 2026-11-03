"""Ability records for the card editor: read, check and save a card's abilities.

A card names its root abilities in ``m_CardAbilities``. A root is an ``AbilityTemplate``
record, and it refers to its children by GUID and by index, as the client does:

* ``m_AbilityEffectList``: the ordered effect entries (``AbilityEffectTargetMapping``).
  Each names an ``AbilityEffectTemplate`` (``m_EffectTemplateId``), the target it acts on
  (``m_TargetTemplateIndex`` and ``m_SecondaryTargetIndex``, indexes into the root's
  target list), an optional ``AbilityEffectConditionTemplate`` (``m_ConditionId``), and
  the entry it waits for (``m_ContingentEffectInstanceId``, another entry's
  ``m_EffectInstanceId``).
* ``m_AbilityTargetTemplateIds``: the ``AbilityTargetTemplate`` GUIDs those indexes use.
  Cost targets (``m_SacrificeTarget``, ``m_DiscardTargets``, ...) name targets directly.

Records is never written. Edits go to ``campaign/campaign_data/ability_records.json``
(``campaign_data.load_ability_overlay``): a record with a Records GUID replaces that
record whole, and any other record is new.

``validate`` checks a record by the C# class its ``_t`` names (``client_schema``): field
types, enum members, GUID references (which Records section a field points at is read
from Records itself), the root's indexes, and reference cycles.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import threading
import uuid
from typing import Any

from paths import RECORDS, REPO  # noqa: E402  (also sets sys.path)
import campaign_data  # noqa: E402
import client_schema  # noqa: E402
from gamedata.records import deserialize_line  # noqa: E402

SECTIONS = campaign_data.ABILITY_SECTIONS
MAPPING_TYPE = "Reckoning.Game.AbilityEffectTargetMapping"
# The client base class every record of a section derives from.
SECTION_BASE = {"AbilityTemplate": "AbilityTemplate",
                "AbilityEffectTemplate": "AbilityEffectTemplate",
                "AbilityEffectConditionTemplate": "AbilityEffectConditionTemplate",
                "AbilityTargetTemplate": "AbilityTargetTemplate"}
ZERO_GUID = "00000000-0000-0000-0000-000000000000"
# Root fields that are costs or use limits: never negative.
# Effect fields that resolve another ability at once (ActivateAbilityEffectTemplate).
INVOKE_FIELDS = ("m_AbilityToInvoke",)
NON_NEGATIVE = ("m_ActivationCost", "m_ChargePointCost", "m_SpellPointCost", "m_LifeCost",
                "m_UsesPerTurn", "m_UsesPerGame", "m_Cooldown", "m_VariableActivationCostMinimum")

_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_OWN_ID = re.compile(r'"(?:m_AbilityTemplateId|m_TemplateId|m_Id)"\s*:\s*\{\s*'
                     r'(?:"_t"\s*:\s*"[^"]*"\s*,\s*)?"m_Guid"\s*:\s*"([0-9a-fA-F-]{36})"')
_NAME = re.compile(r'"m_Name"\s*:\s*"((?:[^"\\]|\\.)*)"')
_TYPE = re.compile(r'"_t"\s*:\s*"([^"]+)"')

_WRITE_LOCK = threading.Lock()
_INDEX_LOCK = threading.Lock()
_INDEX: dict[str, tuple[int, dict[str, dict[str, Any]]]] = {}   # section -> (mtime, index)
_FACTS_LOCK = threading.Lock()
_FACTS: dict[str, Any] = {}


class Invalid(ValueError):
    """A save the checks rejected; ``problems`` lists every error and warning."""

    def __init__(self, problems: list[dict[str, Any]]):
        self.problems = problems
        errors = [p for p in problems if p["level"] == "error"]
        lines = [f"{p['name'] or p['guid']}: {p['path'] or 'record'}: {p['text']}" for p in errors[:5]]
        more = f" (and {len(errors) - 5} more)" if len(errors) > 5 else ""
        super().__init__("; ".join(lines) + more)


# ---------------------------------------------------------------- Records

def _records_index(section: str) -> dict[str, dict[str, Any]]:
    """``{GUID: {offset, length, name, type}}`` for one Records file, re-read when it changes."""
    path = RECORDS / f"{section}.jsonl"
    try:
        mtime = path.stat().st_mtime_ns
    except FileNotFoundError:
        return {}
    with _INDEX_LOCK:
        hit = _INDEX.get(section)
        if hit and hit[0] == mtime:
            return hit[1]
        index, offset = {}, 0
        with path.open("rb") as stream:
            for line in stream:
                text = json.loads(line) if line.strip() else ""
                own = _OWN_ID.search(text) if isinstance(text, str) else None
                if own:
                    name, kind = _NAME.search(text), _TYPE.search(text)
                    index[own.group(1).lower()] = {
                        "offset": offset, "length": len(line),
                        "name": json.loads(f'"{name.group(1)}"') if name else "",
                        "type": kind.group(1) if kind else ""}
                offset += len(line)
        _INDEX[section] = (mtime, index)
        return index


def _records_record(section: str, guid: str) -> dict[str, Any] | None:
    """The Records version of a record as plain JSON (``_t``, ``_v`` and ``m_`` fields)."""
    entry = _records_index(section).get(guid)
    if not entry:
        return None
    with (RECORDS / f"{section}.jsonl").open("rb") as stream:
        stream.seek(entry["offset"])
        line = stream.read(entry["length"]).decode("utf-8")
    return deserialize_line(line).to_dict()


def _iter_records(section: str):
    """Every record of a Records file as plain JSON, in file order."""
    with (RECORDS / f"{section}.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            text = json.loads(line) if line.strip() else ""
            if isinstance(text, str) and '"_t"' in text:
                yield deserialize_line(line).to_dict()


def _records_facts() -> dict[str, Any]:
    """Facts read once from Records: where each GUID field points, and the trigger events.

    ``references`` is ``{field: {section, strict}}``: a field whose Records GUIDs all lie
    in one section points at that section; ``strict`` when every one of them resolves.
    """
    with _FACTS_LOCK:
        if _FACTS:
            return _FACTS
        owner = {guid: section for section in SECTIONS + ("CardTemplate",)
                 for guid in _records_index(section)}
        hits: dict[str, dict[str, int]] = {}
        events: set[str] = set()

        def walk(value: Any, key: str) -> None:
            if isinstance(value, dict):
                guid = value.get("m_Guid")
                if isinstance(guid, str) and guid.lower() != ZERO_GUID and key:
                    counts = hits.setdefault(key, {})
                    where = owner.get(guid.lower(), "?")
                    counts[where] = counts.get(where, 0) + 1
                if key == "m_TriggerEventType" and value.get("m_InternalType"):
                    events.add(str(value["m_InternalType"]))
                for name, item in value.items():
                    walk(item, name)
            elif isinstance(value, list):
                for item in value:
                    walk(item, key)

        for section in SECTIONS:
            for record in _iter_records(section):
                walk(record, "")
        references = {}
        for field, counts in hits.items():
            sections = [s for s in counts if s != "?"]
            if len(sections) == 1 and field not in ("m_AbilityTemplateId", "m_TemplateId"):
                references[field] = {"section": sections[0], "strict": "?" not in counts}
        _FACTS.update(references=references, trigger_events=sorted(events))
        return _FACTS


def _engine_effects() -> set[str]:
    """The effect types the game server executes (rules_port/coverage.py), by short name.

    Loaded from the file itself: importing the rules_port package would load the engine.
    """
    path = REPO / "rules_port" / "coverage.py"
    spec = importlib.util.spec_from_file_location("_editor_rules_coverage", path)
    if spec is None or spec.loader is None:
        return set()
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.NATIVE_EFFECTS) | set(module.STRUCTURAL_EFFECTS)


_ENGINE_EFFECTS: set[str] | None = None


def engine_effects() -> set[str]:
    global _ENGINE_EFFECTS
    with _FACTS_LOCK:
        if _ENGINE_EFFECTS is None:
            _ENGINE_EFFECTS = _engine_effects()
        return _ENGINE_EFFECTS


# ---------------------------------------------------------------- lookups

def _section(value: Any) -> str:
    section = str(value or "")
    if section not in SECTIONS:
        raise ValueError(f"unknown ability section {section!r}")
    return section


def _guid(value: Any) -> str:
    guid = str(value or "").strip().lower()
    if not _GUID.match(guid):
        raise ValueError(f"{value!r} is not a GUID")
    return guid


def lookup(section: str, guid: str, overlay: dict | None = None) -> tuple[dict[str, Any] | None, str]:
    """``(record, source)``: source is ``records``, ``edited`` (overlay shadows Records) or ``new``."""
    overlay = campaign_data.load_ability_overlay() if overlay is None else overlay
    if guid in overlay[section]:
        source = "edited" if guid in _records_index(section) else "new"
        return overlay[section][guid], source
    record = _records_record(section, guid)
    return record, "records" if record else ""


def exists(section: str, guid: str, overlay: dict | None = None) -> bool:
    if section == "CardTemplate":
        import cards   # here, not at the top: cards imports this module
        return guid in _records_index("CardTemplate") or guid in cards.all_card_guids()
    overlay = campaign_data.load_ability_overlay() if overlay is None else overlay
    return guid in overlay[section] or guid in _records_index(section)


def name_of(section: str, guid: str, overlay: dict | None = None) -> str:
    overlay = campaign_data.load_ability_overlay() if overlay is None else overlay
    if guid in overlay.get(section, {}):
        return str(overlay[section][guid].get("m_Name") or "")
    return _records_index(section).get(guid, {}).get("name", "")


def search(section: str, query: str = "", limit: int = 40) -> list[dict[str, Any]]:
    """Records and overlay records whose name, GUID or type contains ``query``."""
    section = _section(section)
    needle = (query or "").strip().lower()
    overlay = campaign_data.load_ability_overlay()[section]
    rows = {guid: {"guid": guid, "name": entry["name"], "type": entry["type"], "source": "records"}
            for guid, entry in _records_index(section).items()}
    for guid, record in overlay.items():
        rows[guid] = {"guid": guid, "name": str(record.get("m_Name") or ""),
                      "type": str(record.get("_t") or ""),
                      "source": "edited" if guid in rows else "new"}
    found = [row for row in rows.values()
             if not needle or needle in row["name"].lower() or row["guid"].startswith(needle)
             or needle in row["type"].lower()]
    found.sort(key=lambda row: (row["source"] == "records", row["name"].lower()))
    return [dict(row, type=row["type"].rsplit(".", 1)[-1]) for row in found[:limit]]


def schema_for(type_name: str) -> list[dict[str, Any]]:
    """The fields of a client class as the form shows them, in declaration order."""
    return [dict(client_schema.describe(spec["type"]), name=name, cs_type=spec["type"],
                 default=client_schema.default_value(spec))
            for name, spec in client_schema.fields(type_name).items()]


def options() -> dict[str, Any]:
    """The choices the ability form offers: record types per section and trigger events."""
    engine = engine_effects()
    effect_types = client_schema.concrete_types("AbilityEffectTemplate")
    return {
        "types": {"AbilityTemplate": ["Reckoning.Game.AbilityTemplate"],
                  "AbilityEffectTemplate": effect_types,
                  "AbilityEffectConditionTemplate":
                      ["Game.Shared.Mechanics.Abilities.AbilityEffectConditionTemplate"],
                  "AbilityTargetTemplate": client_schema.concrete_types("AbilityTargetTemplate")},
        "engine_effects": sorted(t for t in effect_types if t.rsplit(".", 1)[-1] in engine),
        "trigger_events": _records_facts()["trigger_events"],
        "mapping_schema": schema_for(MAPPING_TYPE),
    }


def _record_payload(section: str, guid: str, overlay: dict) -> dict[str, Any] | None:
    record, source = lookup(section, guid, overlay)
    if record is None:
        return None
    return {"section": section, "guid": guid, "record": record, "source": source,
            "schema": schema_for(str(record.get("_t") or "")),
            "can_revert": source == "edited"}


def _children(root: dict[str, Any]) -> list[tuple[str, str]]:
    """``(section, GUID)`` of every record a root refers to directly."""
    references = _records_facts()["references"]
    found: list[tuple[str, str]] = []

    def walk(value: Any, key: str) -> None:
        if isinstance(value, dict):
            guid = str(value.get("m_Guid") or "").lower()
            target = references.get(key, {}).get("section")
            # A granted or invoked ability is one level down, so its text and effects can be shown.
            granted = key in ("m_GrantedAbilityTemplateId", "m_AbilityToInvoke") and target == "AbilityTemplate"
            if guid and guid != ZERO_GUID and target in SECTIONS and (target != "AbilityTemplate" or granted):
                found.append((target, guid))
            for name, item in value.items():
                walk(item, name)
        elif isinstance(value, list):
            for item in value:
                walk(item, key)

    walk(root, "")
    return list(dict.fromkeys(found))


def get_ability(guid: str) -> dict[str, Any]:
    """A root ability with its children's records, their names, and the form's options."""
    guid = _guid(guid)
    overlay = campaign_data.load_ability_overlay()
    root = _record_payload("AbilityTemplate", guid, overlay)
    if root is None:
        raise LookupError(f"no ability {guid}")
    children = {}
    for section, child in _children(root["record"]):
        payload = _record_payload(section, child, overlay)
        if payload:
            children[child] = payload
    root["children"] = children
    import ability_summary  # noqa: E402  (it reads this module, so it is imported here)
    root["summary"] = ability_summary.summary(root["record"])
    root["graph"] = ability_summary.graph(root["record"])
    root["engine_effects"] = sorted(engine_effects())
    return root


def get_record(section: str, guid: str) -> dict[str, Any]:
    payload = _record_payload(_section(section), _guid(guid), campaign_data.load_ability_overlay())
    if payload is None:
        raise LookupError(f"no {section} {guid}")
    return payload


def new_record(section: str, type_name: str) -> dict[str, Any]:
    """A blank record of a client type, with the client's field defaults and a new GUID."""
    section = _section(section)
    if type_name not in options()["types"][section]:
        raise ValueError(f"{type_name or '(no type)'} is not a {section} type the client has")
    record: dict[str, Any] = {"_t": type_name}
    for field in schema_for(type_name):
        record[field["name"]] = copy.deepcopy(field["default"])
    guid = str(uuid.uuid4())
    record["m_AbilityTemplateId" if section == "AbilityTemplate" else "m_TemplateId"] = {"m_Guid": guid}
    record["m_Name"] = f"New {type_name.rsplit('.', 1)[-1]}"
    return {"section": section, "guid": guid, "record": record, "source": "unsaved",
            "schema": schema_for(type_name), "can_revert": False}


# ---------------------------------------------------------------- checks

class _Checker:
    """Collects the problems of one record; ``known`` answers whether a GUID exists."""

    def __init__(self, section: str, record: dict[str, Any], known):
        self.section = section
        self.guid = campaign_data.ability_record_guid(section, record)
        self.name = str(record.get("m_Name") or "")
        self.known = known
        self.problems: list[dict[str, Any]] = []

    def add(self, path: str, text: str, level: str = "error") -> None:
        self.problems.append({"section": self.section, "guid": self.guid, "name": self.name,
                              "path": path, "text": text, "level": level})

    # -- by C# type

    def object(self, value: dict[str, Any], declared: str, path: str) -> None:
        type_name = str(value.get("_t") or "")
        if type_name:
            if not client_schema.known_type(type_name):
                self.add(path + "_t", f"the client has no type {type_name}")
                return
            base = declared.split("<")[0]
            if base and client_schema.known_type(base) and not client_schema.is_a(type_name, base):
                self.add(path + "_t", f"{type_name.rsplit('.', 1)[-1]} is not a {base}")
        constrained = re.fullmatch(r"ConstrainedType<(\w+)>", declared)
        if constrained:
            self.constrained(value, constrained.group(1), path)
            return
        spec = client_schema.fields(type_name or declared)
        for key, item in value.items():
            if key in ("_t", "_v"):
                continue
            if key not in spec:
                if spec:
                    self.add(path + key, "the client class has no such field", "warning")
                continue
            self.value(item, spec[key]["type"], key, path + key)

    def constrained(self, value: dict[str, Any], base: str, path: str) -> None:
        inner = str(value.get("m_InternalType") or "")
        if inner and not client_schema.known_type(inner):
            self.add(path + "m_InternalType", f"the client has no type {inner}")
        elif inner and not client_schema.is_a(inner, base):
            self.add(path + "m_InternalType", f"{inner.rsplit('.', 1)[-1]} is not a {base}")

    def value(self, item: Any, cs_type: str, key: str, path: str) -> None:
        kind = client_schema.describe(cs_type)
        check = getattr(self, "kind_" + kind["kind"])
        check(item, kind, cs_type, key, path)

    def kind_bool(self, item, kind, cs_type, key, path):
        if item not in (0, 1) or isinstance(item, float):
            self.add(path, "must be 0 or 1")

    def kind_int(self, item, kind, cs_type, key, path):
        if isinstance(item, bool) or not isinstance(item, int):
            self.add(path, "must be a whole number")
        elif not client_schema.INT32[0] <= item <= client_schema.INT32[1]:
            self.add(path, "is out of the client's 32-bit range")

    def kind_number(self, item, kind, cs_type, key, path):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            self.add(path, "must be a number")

    def kind_text(self, item, kind, cs_type, key, path):
        if item is not None and not isinstance(item, str):
            self.add(path, "must be text")

    def kind_enum(self, item, kind, cs_type, key, path):
        if item not in kind["values"]:
            self.add(path, f"{item!r} is not a {cs_type} (one of {', '.join(kind['values'][:8])}...)")

    def kind_flags(self, item, kind, cs_type, key, path):
        parts = [part.strip() for part in str(item or "").split("|")]
        unknown = [part for part in parts if part not in kind["values"]]
        if not isinstance(item, str) or unknown:
            self.add(path, f"{', '.join(unknown) or item!r} not in {cs_type}")

    def kind_guid(self, item, kind, cs_type, key, path):
        guid = str(item.get("m_Guid") or "").lower() if isinstance(item, dict) else ""
        if not _GUID.match(guid):
            self.add(path, "must be {m_Guid: GUID}")
        elif guid != ZERO_GUID:
            self.reference(key, guid, path)

    def kind_type_name(self, item, kind, cs_type, key, path):
        if item and not client_schema.known_type(str(item)):
            self.add(path, f"the client has no type {item}")

    def kind_guid_list(self, item, kind, cs_type, key, path):
        if item is None:
            return
        if not isinstance(item, list):
            self.add(path, "must be a list of {m_Guid: GUID}")
            return
        for index, entry in enumerate(item):
            self.kind_guid(entry, kind, "ResourceId", key, f"{path}.{index}")

    def kind_json(self, item, kind, cs_type, key, path):
        if item is None:
            return
        if isinstance(item, dict) and not cs_type.startswith("Dictionary<"):
            self.object(item, cs_type, path + ".")
        elif isinstance(item, dict):
            self.dictionary(item, cs_type, path)
        elif isinstance(item, list) and kind.get("item"):
            for index, entry in enumerate(item):
                if isinstance(entry, dict):
                    self.object(entry, kind["item"], f"{path}.{index}.")
                else:
                    self.value(entry, kind["item"], key, f"{path}.{index}")
        elif not isinstance(item, (dict, list)):
            self.add(path, f"must be an object or list ({cs_type})")

    def dictionary(self, item: dict, cs_type: str, path: str) -> None:
        keys = re.fullmatch(r"Dictionary<(\w+),\s*[\w<>]+>", cs_type)
        found = client_schema.enum(keys.group(1)) if keys else None
        for name in item if found else ():
            if name not in found["values"]:
                self.add(f"{path}.{name}", f"{name!r} is not a {keys.group(1)}")

    def reference(self, key: str, guid: str, path: str) -> None:
        target = _records_facts()["references"].get(key)
        if target and not self.known(target["section"], guid):
            level = "error" if target["strict"] else "warning"
            self.add(path, f"no {target['section']} {guid} in Records, the overlay or the custom set",
                     level)

    # -- by section

    def ability(self, record: dict[str, Any]) -> None:
        """The root's own rules: costs, entry ids, target indexes and contingencies."""
        for field in NON_NEGATIVE:
            if isinstance(record.get(field), int) and record[field] < 0:
                self.add(field, "must not be negative")
        targets = record.get("m_AbilityTargetTemplateIds") or []
        entries = record.get("m_AbilityEffectList") or []
        if not isinstance(entries, list):
            return
        ids: dict[int, int] = {}
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                self.add(f"m_AbilityEffectList.{index}", "must be an effect entry")
                continue
            instance = entry.get("m_EffectInstanceId")
            if instance in ids:
                self.add(f"m_AbilityEffectList.{index}.m_EffectInstanceId",
                         f"entry {ids[instance]} already uses id {instance}")
            ids.setdefault(instance, index)
            self.target_index(entry, index, len(targets))
            if not str((entry.get("m_EffectTemplateId") or {}).get("m_Guid") or "").strip("0-"):
                self.add(f"m_AbilityEffectList.{index}.m_EffectTemplateId", "choose an effect")
        for index, entry in enumerate(entries):
            for field in ("m_SecondaryTargetIndex", "m_ContingentEffectInstanceId"):
                self.entry_id(entry, field, index, ids)
        self.contingencies(entries)

    def target_index(self, entry: dict, index: int, count: int) -> None:
        """``m_TargetTemplateIndex`` indexes the root's target list (AbilityInstance.cs)."""
        value = entry.get("m_TargetTemplateIndex")
        if isinstance(value, int) and not isinstance(value, bool) and not 0 <= value < max(count, 1):
            self.add(f"m_AbilityEffectList.{index}.m_TargetTemplateIndex",
                     f"target {value} does not exist (the ability has {count} target(s))")

    def entry_id(self, entry: Any, field: str, index: int, ids: dict[int, int]) -> None:
        """The secondary target and the contingency name another entry's
        ``m_EffectInstanceId``; a negative value means none (AbilityInstance.cs)."""
        value = entry.get(field, -1) if isinstance(entry, dict) else -1
        if isinstance(value, int) and value >= 0 and value not in ids:
            self.add(f"m_AbilityEffectList.{index}.{field}", f"no entry has instance id {value}")

    def contingencies(self, entries: list) -> None:
        """Following the contingent ids from any entry never loops."""
        ids = {entry.get("m_EffectInstanceId"): index for index, entry in enumerate(entries)
               if isinstance(entry, dict)}
        waits = {}
        for entry in entries:
            wanted = entry.get("m_ContingentEffectInstanceId", -1) if isinstance(entry, dict) else -1
            if isinstance(wanted, int) and wanted >= 0:
                waits[entry.get("m_EffectInstanceId")] = wanted
        for start in waits:
            seen, current = set(), start
            while current in waits and current not in seen:
                seen.add(current)
                current = waits[current]
            if current == start:
                self.add(f"m_AbilityEffectList.{ids.get(start, 0)}.m_ContingentEffectInstanceId",
                         "the contingent entries wait for each other in a loop")


def check(section: str, record: Any, known) -> list[dict[str, Any]]:
    """The problems of one record. ``known(section, guid)`` says whether a GUID exists."""
    section = _section(section)
    if not isinstance(record, dict):
        return [{"section": section, "guid": "", "name": "", "path": "",
                 "text": "a record must be an object", "level": "error"}]
    checker = _Checker(section, record, known)
    type_name = str(record.get("_t") or "")
    if not _GUID.match(checker.guid):
        checker.add("m_AbilityTemplateId" if section == "AbilityTemplate" else "m_TemplateId",
                    "the record needs a GUID")
    if not client_schema.is_a(type_name, SECTION_BASE[section]):
        checker.add("_t", f"{type_name or '(none)'} is not a client {SECTION_BASE[section]}")
        return checker.problems
    if section != "AbilityTargetTemplate" and not checker.name.strip():
        checker.add("m_Name", "needs a name")
    checker.object(record, type_name, "")
    if section == "AbilityTemplate":
        checker.ability(record)
    if section == "AbilityEffectTemplate" and type_name.rsplit(".", 1)[-1] not in engine_effects():
        checker.add("_t", "the game server has no executor for this effect type", "warning")
    return checker.problems


def _ability_cycle(guid: str, lookup_record) -> list[str]:
    """A chain of abilities that invoke each other back to ``guid`` (an endless
    resolution), or []. Granting an ability is not a loop: Records abilities often
    grant themselves ("this retains this power")."""
    invokes = INVOKE_FIELDS

    def next_abilities(ability_guid: str) -> list[str]:
        record = lookup_record("AbilityTemplate", ability_guid)
        out = []
        for entry in (record or {}).get("m_AbilityEffectList") or []:
            effect_guid = str((entry.get("m_EffectTemplateId") or {}).get("m_Guid") or "").lower()
            effect = lookup_record("AbilityEffectTemplate", effect_guid) or {}
            for field in invokes:
                value = effect.get(field)
                if isinstance(value, dict) and value.get("m_Guid"):
                    out.append(str(value["m_Guid"]).lower())
        return out

    stack, seen = [(guid, [guid])], set()
    while stack:
        current, trail = stack.pop()
        for following in next_abilities(current):
            if following == guid:
                return trail + [following]
            if following not in seen:
                seen.add(following)
                stack.append((following, trail + [following]))
    return []


def validate(entries: Any) -> list[dict[str, Any]]:
    """Check records saved together (``[{section, record}]``) against Records, the overlay
    and each other: a new child may be saved with the root that uses it."""
    if not isinstance(entries, list) or not entries:
        raise ValueError("send at least one record")
    overlay = campaign_data.load_ability_overlay()
    pending = {section: {} for section in SECTIONS}
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("record"), dict):
            section = _section(entry.get("section"))
            pending[section][campaign_data.ability_record_guid(section, entry["record"])] = entry["record"]

    def known(section: str, guid: str) -> bool:
        return guid in pending.get(section, {}) or exists(section, guid, overlay)

    def lookup_record(section: str, guid: str):
        return pending[section].get(guid) or lookup(section, guid, overlay)[0]

    problems = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each entry must be {section, record}")
        problems += check(entry.get("section"), entry.get("record"), known)
    for guid in pending["AbilityTemplate"]:
        loop = _ability_cycle(guid, lookup_record)
        if loop:
            names = " -> ".join(name_of("AbilityTemplate", g, overlay) or g for g in loop)
            problems.append({"section": "AbilityTemplate", "guid": guid,
                             "name": name_of("AbilityTemplate", guid, overlay), "path": "m_AbilityEffectList",
                             "text": f"the ability invokes itself: {names}", "level": "error"})
    return problems


# ---------------------------------------------------------------- writing

def save_records(entries: Any) -> dict[str, Any]:
    """Validate and store records saved together. A record equal to its Records version
    drops its overlay row (the Records version shows again)."""
    with _WRITE_LOCK:
        problems = validate(entries)
        if any(problem["level"] == "error" for problem in problems):
            raise Invalid(problems)
        overlay = campaign_data.load_ability_overlay()
        saved = []
        for entry in entries:
            section = entry["section"]
            record = entry["record"]
            guid = campaign_data.ability_record_guid(section, record)
            if _records_record(section, guid) == record:
                overlay[section].pop(guid, None)
            else:
                overlay[section][guid] = record
            saved.append({"section": section, "guid": guid})
        campaign_data.write_ability_overlay(overlay)
    return {"saved": saved, "problems": problems}


def revert_record(section: str, guid: str) -> dict[str, Any]:
    """Drop the edits to a Records record so the Records version shows again."""
    section, guid = _section(section), _guid(guid)
    with _WRITE_LOCK:
        overlay = campaign_data.load_ability_overlay()
        if guid not in overlay[section]:
            raise LookupError("this record has no edits")
        if guid not in _records_index(section):
            raise ValueError("a new record has no Records version to revert to")
        del overlay[section][guid]
        campaign_data.write_ability_overlay(overlay)
    return get_record(section, guid)


def warm_caches() -> None:
    """Read the client sources and the Records facts ahead of the first ability request."""
    client_schema.fields(MAPPING_TYPE)
    _records_facts()
    engine_effects()
