"""Targets as properties: a target is a set of card properties built up in the editor.

A property is one card filter (a Records ``Cards.Filters`` object). A target's card filter
is the properties ANDed together, so a target reads as its properties:

    troop, subtype warlock, in the warzone, you control, cost 3 or more

``read_properties`` takes a card filter and returns its properties, or None when the
filter has a part this module does not model. ``build_filter`` does the reverse. A
target that is built this way is reused when the Records already hold one with the same
filter, as the DSL reuses phrases.
"""

from __future__ import annotations

import functools
import json
import re
from typing import Any

FILTERS = "Game.Shared.Mechanics.Cards.Filters."

# Comparison operators as the Records write them.
COMPARISONS = ("GreaterThanOrEqual", "LessThanOrEqual", "Equal", "GreaterThan", "LessThan", "NotEqual")

# Keywords: the ECardAttributes flags the Records test (HasAllAttributeFlags).
Properties = dict[str, Any]

# Fields the Records always store on these filters, with the value a plain property has.
# A filter's fields must be these plus the ones the property states, or it is not modelled.
DEFAULTS: dict[str, dict[str, Any]] = {
    "HasResourceCost": {"m_AddAttack": 0, "m_AddDefense": 0, "m_AddX": 0, "m_AddVariable": None,
                        "m_AddCardIntegerVariable": None, "m_AddSumListAttrName": None,
                        "m_AddSumProperty": None, "m_ResourceCostCardFilter": None},
    "HasAttackValue": {"m_CompareToAbilitySource": 0, "m_CompareToCardIntegerVariable": None,
                       "m_CompareToSourceControlledCardFilterCount": None, "m_CompareToStoredTarget": 0,
                       "m_CompareToTriggerSource": 0},
    "HasDefenseValue": {"m_CompareToAbilitySource": 0, "m_CompareToCardIntegerVariable": None,
                        "m_CompareToSourceControlledCardFilterCount": None, "m_CompareToStoredTarget": 0,
                        "m_CompareToTriggerSource": 0},
    "IsCardName": {"m_CompareToAbilitySource": 0, "m_CompareToTriggerSource": 0, "m_CompareToTriggerTarget": 0},
    "IsHero": {"m_MustBeAI": 0, "m_MustBeHuman": 0},
    "IsResource": {"m_IsBasicResource": 0, "m_IsNonStandardResource": 0, "m_ThresholdColorFlags": "Any"},
    "IsColor": {"m_IncludeResources": 0, "m_Prismatic": 0},
    "NameContainsFilter": {"m_IncludeKeywords": 0, "m_IncludeSubType": 0, "m_LanguageCode": "en"},
    "IntAttrFilter": {"m_CompareToCost": 0},
}

# Properties that can occur more than once in one target (the others occur once).
MULTI = {"comparisons", "keywords", "without", "attributes"}

# Plain flag filters: a property that is simply true (no fields of its own).
FLAGS = {"IsAbilitySource": "source", "IsArtifact": "artifact", "IsTapped": "tapped",
         "IsAttacking": "attacking", "IsHero": "hero", "IsResource": "resource"}


def _filter(name: str, **fields: Any) -> dict[str, Any]:
    return {"_t": FILTERS + name, **fields}


def _parts(card_filter: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The parts of an AndCardFilter (or the filter itself, as one part)."""
    if not isinstance(card_filter, dict):
        return None
    if card_filter.get("_t") == FILTERS + "AndCardFilter" and set(card_filter) == {"_t", "m_TargetFilters"}:
        return list(card_filter["m_TargetFilters"])
    return [card_filter]


def _without_defaults(name: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    """The fields that are not defaults, or None if a default is missing from the filter."""
    defaults = DEFAULTS.get(name, {})
    if any(key in fields and fields[key] != value for key, value in defaults.items()):
        return None   # a default-field set to something else: not a plain property
    if any(key not in fields for key in defaults):
        return None
    return {key: value for key, value in fields.items() if key not in defaults}


def _read_part(part: dict[str, Any], properties: Properties) -> bool:
    """Record one part as a property. False when the part is not one this module models."""
    name = str(part.get("_t", "")).removeprefix(FILTERS)
    fields = {key: value for key, value in part.items() if key != "_t"}
    if name in DEFAULTS:
        rest = _without_defaults(name, fields)
        if rest is None:   # a default set to something else: not a plain property
            properties.setdefault("raw", []).append(part)
            return True
        fields = rest
    if name in FLAGS and not fields:
        properties[FLAGS[name]] = True
        return True
    if name == "NotCardFilter" and set(fields) == {"m_TargetFilter"}:
        inner = read_properties(fields["m_TargetFilter"])
        if inner is None:
            return False
        properties.setdefault("without", []).append(inner)
        return True
    if name == "OrCardFilter" and set(fields) == {"m_TargetFilters"}:
        options = [read_properties(option) for option in fields["m_TargetFilters"]]
        if any(option is None for option in options):
            return False
        properties["either"] = options
        return True
    if name == "IsCardName" and set(fields) == {"m_CardName"}:
        properties["name"] = fields["m_CardName"]
    elif name == "IsColor" and set(fields) == {"m_ColorFlags"}:
        properties["colour"] = fields["m_ColorFlags"]
    elif name == "InFaction" and set(fields) == {"m_Faction"}:
        properties["faction"] = fields["m_Faction"]
    elif name == "NameContainsFilter" and set(fields) == {"m_ContainsString"}:
        properties["name_contains"] = fields["m_ContainsString"]
    elif name == "IntAttrFilter" and set(fields) == {"m_Attribute", "m_ComparisonOp", "m_Value"}:
        properties.setdefault("attributes", []).append(
            {"attribute": fields["m_Attribute"], "op": fields["m_ComparisonOp"], "value": fields["m_Value"]})
    elif name == "IsTroop" and not fields:
        properties["type"] = "Troop"
    elif name == "IsType" and set(fields) == {"m_CardType"}:
        properties["type"] = fields["m_CardType"]
    elif name == "IsSubType" and set(fields) == {"m_SubType"}:
        properties["subtype"] = fields["m_SubType"]
    elif name == "InZone" and set(fields) == {"m_Collection"}:
        properties["zone"] = fields["m_Collection"]
    elif name == "IsControlledBy" and set(fields) == {"m_TestAgainstActivePlayer"}:
        properties["controller"] = "you"
    elif name == "IsNotControlledBy" and set(fields) == {"m_FailUncontrolledCards"}:
        properties["controller"] = "opponent"
    elif name == "HasAttackValue" and set(fields) == {"m_AttackValue", "m_ComparisonOp"}:
        properties.setdefault("comparisons", []).append(
            {"stat": "attack", "op": fields["m_ComparisonOp"], "value": fields["m_AttackValue"]})
    elif name == "HasDefenseValue" and set(fields) == {"m_DefenseValue", "m_ComparisonOp"}:
        properties.setdefault("comparisons", []).append(
            {"stat": "defense", "op": fields["m_ComparisonOp"], "value": fields["m_DefenseValue"]})
    elif name == "HasResourceCost" and set(fields) == {"m_ResourceCost", "m_ComparisonOp"}:
        properties.setdefault("comparisons", []).append(
            {"stat": "cost", "op": fields["m_ComparisonOp"], "value": fields["m_ResourceCost"]})
    elif name == "HasAllAttributeFlags" and set(fields) == {"m_CardAttributeFlags"}:
        properties.setdefault("keywords", []).append(fields["m_CardAttributeFlags"])
    else:
        # A part the model does not name is carried as it is, so the target still rebuilds
        # exactly; it cannot be edited as a property.
        properties.setdefault("raw", []).append(part)
    return True


def read_properties(card_filter: dict[str, Any]) -> Properties | None:
    """The properties a card filter states, or None if it has a part not modelled here."""
    parts = _parts(card_filter)
    if parts is None:
        return None
    properties: Properties = {}
    for part in parts:
        one: Properties = {}
        if not _read_part(part, one):
            return None
        for key, value in one.items():
            if key in MULTI:
                properties.setdefault(key, []).extend(value if key != "either" else [value])
            elif key == "raw":
                properties.setdefault("raw", []).extend(value)
            elif key in properties:   # a second value for a single property: carried as raw
                properties.setdefault("raw", []).append(part)
            else:
                properties[key] = value
    return properties


def canonical(card_filter: dict[str, Any]) -> str:
    """A filter's meaning as a string: the parts of AND and OR filters are sorted at every
    level, so two filters that state the same parts in another order compare equal."""
    def node(value: Any) -> Any:
        if isinstance(value, list):
            return sorted((node(item) for item in value), key=lambda x: json.dumps(x, sort_keys=True))
        if isinstance(value, dict):
            name = str(value.get("_t", "")).removeprefix(FILTERS)
            if name in ("AndCardFilter", "OrCardFilter"):
                return {"_t": name, "m_TargetFilters": node(value.get("m_TargetFilters", []))}
            return {key: node(item) for key, item in value.items()}
        return value
    return json.dumps(node(card_filter), sort_keys=True)


def _with_defaults(name: str, fields: dict[str, Any]) -> dict[str, Any]:
    return _filter(name, **{**DEFAULTS.get(name, {}), **fields})


def build_filter(properties: Properties) -> dict[str, Any]:
    """The card filter for a set of properties: each property is one filter, ANDed."""
    parts: list[dict[str, Any]] = []
    kind = properties.get("type")
    if kind == "Troop":
        parts.append(_filter("IsTroop"))
    elif kind:
        parts.append(_filter("IsType", m_CardType=kind))
    if properties.get("subtype"):
        parts.append(_filter("IsSubType", m_SubType=properties["subtype"]))
    if properties.get("zone"):
        parts.append(_filter("InZone", m_Collection=properties["zone"]))
    controller = properties.get("controller")
    if controller == "you":
        # The Records always store 0 here; "you control" lives in the target's player filter.
        parts.append(_filter("IsControlledBy", m_TestAgainstActivePlayer=0))
    elif controller == "opponent":
        parts.append(_filter("IsNotControlledBy", m_FailUncontrolledCards=0))
    for comparison in properties.get("comparisons") or []:
        name, field = {"attack": ("HasAttackValue", "m_AttackValue"),
                       "defense": ("HasDefenseValue", "m_DefenseValue"),
                       "cost": ("HasResourceCost", "m_ResourceCost")}[comparison["stat"]]
        parts.append(_with_defaults(name, {field: comparison["value"], "m_ComparisonOp": comparison["op"]}))
    parts.extend(
        (_filter("HasAllAttributeFlags", m_CardAttributeFlags=flags))
        for flags in (properties.get("keywords") or [])
    )
    parts.extend(_with_defaults(flag, {}) for flag, key in FLAGS.items() if properties.get(key))
    if properties.get("name"):
        parts.append(_with_defaults("IsCardName", {"m_CardName": properties["name"]}))
    if properties.get("colour"):
        parts.append(_with_defaults("IsColor", {"m_ColorFlags": properties["colour"]}))
    if properties.get("name_contains"):
        parts.append(_with_defaults("NameContainsFilter", {"m_ContainsString": properties["name_contains"]}))
    parts.extend(_with_defaults("IntAttrFilter", {"m_Attribute": attribute["attribute"],
                                                      "m_ComparisonOp": attribute["op"],
                                                      "m_Value": attribute["value"]}) for attribute in properties.get("attributes") or [])
    if properties.get("faction"):
        parts.append(_filter("InFaction", m_Faction=properties["faction"]))
    parts.extend((_filter("NotCardFilter", m_TargetFilter=build_filter(inner))) for inner in (properties.get("without") or []))
    if properties.get("either"):
        parts.append(_filter("OrCardFilter",
                             m_TargetFilters=[build_filter(option) for option in properties["either"]]))
    parts.extend((part) for part in (properties.get("raw") or []))
    if not parts:
        raise ValueError("a target needs at least one property")
    if len(parts) == 1:
        return parts[0]
    return {"_t": FILTERS + "AndCardFilter", "m_TargetFilters": parts}


# ---- target records ---------------------------------------------------------------

TARGET_CLASS = "Game.Shared.Mechanics.Abilities.TargetTemplates.AbilityTargetTemplate"
ALL_ZONES = "Deck|Hand|Champions|Warzone|Discard|Void|CastSpells|Underground|Choosing"
NULL_GUID = "00000000-0000-0000-0000-000000000000"


def _target_key(record: dict[str, Any]) -> tuple[str, str, str, int, int]:
    """What makes two target records the same target: filter, player, zones, auto, count."""
    count = lambda value: (value or {}).get("m_Value") if isinstance(value, dict) else None
    return (canonical(record["m_CardFilter"]), str(record.get("m_PlayerFilter")),
            str(record.get("m_CollectionFlags")), int(bool(record.get("m_IsAutoTarget"))),
            count(record.get("m_MinTargetCount")) or 0)


def target_record(name: str, properties: Properties) -> dict[str, Any]:
    """An AbilityTargetTemplate record for a set of properties. Nothing is stored; the
    caller saves it, or reuses ``find_target``'s match. A row with no count states none
    (Records' usual target); a zone property sets the zones the target searches."""
    constant = TARGET_CLASS.rsplit(".", 1)[0] + ".TargetConstant"
    quantity = properties.get("quantity") or QUANTIFIERS[DEFAULT_QUANTIFIER]
    low, high, player, best, automatic, explicit = quantity
    return {
        "_t": TARGET_CLASS,
        "m_TemplateId": {"m_Guid": NULL_GUID},
        "m_Name": name,
        "m_GameText": name,
        "m_CardFilter": build_filter(properties),
        "m_PlayerFilter": player,
        "m_CollectionFlags": properties.get("searched") or ALL_ZONES,
        "m_IsAutoTarget": automatic,
        "m_IsRandomTarget": 1 if (automatic and best) else 0,   # "a random", "two random"
        "m_Optional": 0,
        "m_Explicit": explicit,
        "m_AllowBestEffortMinimumTargetCount": best,
        "m_MinTargetCount": None if low is None else {"_t": constant, "m_Value": low},
        "m_MaxTargetCount": None if high is None else {"_t": constant, "m_Value": high},
        "m_MinimumTargetCount_DEPRECATED": None,
        "m_MaximumTargetCount_DEPRECATED": None,
    }


def find_target(record: dict[str, Any], records: list[dict[str, Any]]) -> str:
    """The GUID of a stored target with the same filter, player, zones, auto and count, or
    "" if there is none. Reuse it instead of storing a copy."""
    wanted = _target_key(record)
    for stored in records:
        if stored.get("_t") == TARGET_CLASS and _target_key(stored) == wanted:
            return str((stored.get("m_TemplateId") or {}).get("m_Guid") or "")
    return ""


# ---- the words a target row is written with ---------------------------------------------

ZONE_WORDS = {"Deck": "from your deck", "Hand": "in your hand", "Discard": "in a crypt",
              "Void": "in the void", "Underground": "underground", "Choosing": "in the choice zone"}
# Where a target searches (its collection flags), when it is not every zone: play is the warzone.
SEARCH_WORDS = {"Warzone": "in play"}
SEARCH_OF = {words: zone for zone, words in SEARCH_WORDS.items()}
ZONE_OF = {words: zone for zone, words in ZONE_WORDS.items()}
TYPE_WORDS = {"Troop": "troop", "BasicAction": "basic action", "QuickAction": "quick action",
              "BasicAction|QuickAction": "action", "Constant": "constant"}
TYPE_OF = {words: kind for kind, words in TYPE_WORDS.items()}
FLAG_WORDS = {"artifact": "artifact", "resource": "resource", "hero": "champion"}
# The type list the Records use for "any card": a target with it has no type of its own.
ALL_TYPES = "Troop|BasicAction|Resource|Artifact|QuickAction|Constant|Bane|Choice"


STAT_WORDS = {"attack", "defense", "cost"}
OPERATOR_WORDS = {"GreaterThanOrEqual": "or more", "LessThanOrEqual": "or less", "Equals": ""}
OPERATOR_OF = {words: op for op, words in OPERATOR_WORDS.items()}

# A row's quantifier: (min count, max count, player filter, best-effort minimum, automatic).
# None counts mean the Records state no count. "Automatic" targets are chosen by the game,
# the others by the player.
QUANTIFIERS: dict[str, tuple[int | None, int | None, str, int, int, int]] = {
    "each": (None, None, "MultiplePlayers", 0, 1, 0),
    "your": (None, None, "Self", 0, 1, 0),
    "target": (1, 1, "SinglePlayer", 0, 0, 1),
    "a": (1, 1, "MultiplePlayers", 0, 0, 0),
    "a random": (1, 1, "MultiplePlayers", 1, 1, 0),
    "a chosen": (1, 1, "MultiplePlayers", 1, 0, 0),
    "up to one": (0, 1, "MultiplePlayers", 0, 0, 1),
    "two": (2, 2, "MultiplePlayers", 1, 1, 0),
    "two chosen": (2, 2, "MultiplePlayers", 1, 0, 0),
    "three": (3, 3, "MultiplePlayers", 1, 1, 0),
    "three chosen": (3, 3, "MultiplePlayers", 1, 0, 0),
}
DEFAULT_QUANTIFIER = "each"


def quantity_of(record: dict[str, Any]) -> tuple[int | None, int | None, str, int, int, int] | None:
    """The quantifier a stored target states, or None when no quantifier word states it."""
    count = lambda value: (value or {}).get("m_Value") if isinstance(value, dict) else None
    key = (count(record.get("m_MinTargetCount")), count(record.get("m_MaxTargetCount")),
           record.get("m_PlayerFilter"), int(bool(record.get("m_AllowBestEffortMinimumTargetCount"))),
           int(bool(record.get("m_IsAutoTarget"))), int(bool(record.get("m_Explicit"))))
    for word, values in QUANTIFIERS.items():
        if values == key:
            return values
    return None


_KIND_PATTERN = r"troop|basic action|quick action|action|constant|artifact|resource|champion|card"
_SUBTYPE_FOLLOWS = (rf"(?:{_KIND_PATTERN})(?: |$)|you control|in play|from your deck|in your hand|"
                    r"in a crypt|in the void|underground|in the choice zone|with")
_TARGET_PHRASE = re.compile(
    r"^(?:(?P<quant>up to one|a random|a chosen|two chosen|three chosen|target|two|three|each|your|an?)(?: |$))?"
    r"(?:faction (?P<faction>[a-z]+) )?"
    r"(?P<opposing>opposing(?: |$))?"
    # A subtype is only a word before the kind it describes: "troop" is never a subtype.
    rf"(?:(?!(?:{_KIND_PATTERN})(?: |$))(?P<subtype>[a-z'-]+) (?={_SUBTYPE_FOLLOWS}))?"
    rf"(?P<kind>{_KIND_PATTERN})?"
    r"(?P<control> ?you control)?"
    r"(?: ?(?P<zone>in play|from your deck|in your hand|in a crypt|in the void|underground|in the choice zone))?"
    r"(?P<stats>(?: ?with (?:cost|attack|defense) \d+(?: or more| or less)?)*)$")
_STAT_PHRASE = re.compile(r" ?with (cost|attack|defense) (\d+)( or more| or less)?")


def render_phrase(properties: Properties) -> str:
    """A target row's words, e.g. "target warlock troop you control in play with cost 3 or more"."""
    quantity = properties.get("quantity") or QUANTIFIERS[DEFAULT_QUANTIFIER]
    word = next((w for w, v in QUANTIFIERS.items() if v == tuple(quantity)), None)
    if word is None:
        raise ValueError("this target's count or player has no words yet")
    if properties.get("raw") or properties.get("either") or properties.get("without") or \
            properties.get("attributes") or properties.get("keywords") or properties.get("name"):
        raise ValueError("this target has properties the words cannot show yet")
    words = [word]
    if properties.get("faction"):
        words.append(f"faction {str(properties['faction']).lower()}")
    if properties.get("controller") == "opponent":
        words.append("opposing")
    if properties.get("subtype"):
        words.append(str(properties["subtype"]).lower())
    kind = properties.get("type")
    flags = [name for name in FLAG_WORDS if properties.get(name)]
    if properties.get("colour") or (properties.get("type") and properties["type"] not in TYPE_WORDS
                                    and properties["type"] != ALL_TYPES):
        raise ValueError("this target has a colour or type the words cannot show yet")
    if any(properties.get(name) for name in ("tapped", "attacking", "source")):
        raise ValueError("this target has a state the words cannot show yet")
    if flags and (properties.get("type") is not None or kind):
        raise ValueError("a type and a card flag cannot both be shown in words yet")
    if kind in TYPE_WORDS:
        words.append(TYPE_WORDS[kind])
    elif flags:
        words.append(FLAG_WORDS[flags[0]])
    elif kind == ALL_TYPES:
        words.append("card")
    if properties.get("controller") == "you":
        words.append("you control")
    if properties.get("searched"):
        if properties["searched"] not in SEARCH_WORDS:
            raise ValueError("this search has no words yet")
        words.append(SEARCH_WORDS[properties["searched"]])
    if properties.get("zone"):
        if properties["zone"] not in ZONE_WORDS:
            raise ValueError("this zone has no words yet")
        words.append(ZONE_WORDS[properties["zone"]])
    for comparison in properties.get("comparisons") or []:
        tail = OPERATOR_WORDS[comparison["op"]]
        words.append(f"with {comparison['stat']} {comparison['value']}" + (f" {tail}" if tail else ""))
    return " ".join(word for word in words if word)


def parse_phrase(text: str, subtypes: dict[str, str] | None = None,
                 faction_names: dict[str, str] | None = None) -> Properties:
    """The properties a target row's words state. ``subtypes`` maps lower-case subtype words
    to the Records' spelling (the parser cannot know it otherwise)."""
    found = _TARGET_PHRASE.match(str(text or "").strip().lower())
    if not found:
        raise ValueError(f"not a target row: {text!r}")
    properties: Properties = {"quantity": list(QUANTIFIERS[found.group("quant") or DEFAULT_QUANTIFIER])}
    if found.group("faction"):
        properties["faction"] = (faction_names or {}).get(found.group("faction"), found.group("faction").title())
    if found.group("opposing"):
        properties["controller"] = "opponent"
    if found.group("control"):
        if properties.get("controller"):
            raise ValueError("a target cannot be both opposing and yours")
        properties["controller"] = "you"
    kind = found.group("kind")
    if kind == "card":
        properties["type"] = ALL_TYPES   # the Records' "any card" type list
    if kind in FLAG_WORDS.values():
        properties[next(name for name, words in FLAG_WORDS.items() if words == kind)] = True
    elif kind == "champion":
        properties["hero"] = True
    elif kind in TYPE_OF:
        properties["type"] = TYPE_OF[kind]
    elif kind == "action":
        properties["type"] = "BasicAction|QuickAction"
    if found.group("subtype"):
        word = found.group("subtype")
        properties["subtype"] = (subtypes or {}).get(word, word.title())
    if found.group("zone") in SEARCH_OF:
        properties["searched"] = SEARCH_OF[found.group("zone")]
    elif found.group("zone"):
        properties["zone"] = ZONE_OF[found.group("zone")]
    comparisons = []
    for stat, value, tail in _STAT_PHRASE.findall(found.group("stats") or ""):
        op = "LessThanOrEqual" if tail.strip() == "or less" else (
            "GreaterThanOrEqual" if tail.strip() == "or more" else "Equals")
        comparisons.append({"stat": stat, "op": op, "value": int(value)})
    if comparisons:
        properties["comparisons"] = comparisons
    return properties


# ---- rows in sentences --------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def vocabulary() -> tuple[dict[str, str], dict[str, str]]:
    """The Records' own spellings of subtypes and factions, keyed by lower-case word."""
    import card_abilities  # imported here: it reads the Records
    subtypes: dict[str, str] = {}
    factions: dict[str, str] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            kind = str(node.get("_t", ""))
            if kind.endswith(".IsSubType") and node.get("m_SubType"):
                subtypes.setdefault(str(node["m_SubType"]).lower(), str(node["m_SubType"]))
            if kind.endswith(".InFaction") and node.get("m_Faction"):
                factions.setdefault(str(node["m_Faction"]).lower(), str(node["m_Faction"]))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for record in card_abilities._iter_records("AbilityTargetTemplate"):
        walk(record.get("m_CardFilter"))
    return subtypes, factions


ROW_NAME = re.compile(r"^[A-Za-z]\d*$")


def parse_definitions(text: str) -> dict[str, Properties]:
    """``T0 = troop you control; T1 = opposing champion`` as {name: properties}."""
    subtypes, factions = vocabulary()
    rows: dict[str, Properties] = {}
    for item in str(text or "").split(";"):
        if not item.strip():
            continue
        name, equals, phrase = item.partition("=")
        if not equals or not ROW_NAME.match(name.strip()):
            raise ValueError(f"a target row is 'NAME = words', not {item.strip()!r}")
        rows[name.strip().upper()] = parse_phrase(phrase.strip(), subtypes, factions)
    return rows


def render_definitions(rows: dict[str, Properties]) -> str:
    """The inverse of ``parse_definitions``: 'T0 = troop you control; T1 = ...'."""
    return "; ".join(f"{name} = {render_phrase(props)}" for name, props in rows.items())
