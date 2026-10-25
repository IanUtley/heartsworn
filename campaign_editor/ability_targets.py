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

import json
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
}

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
        if rest is None:
            return False
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
        return False
    return True


def read_properties(card_filter: dict[str, Any]) -> Properties | None:
    """The properties a card filter states, or None if it has a part not modelled here."""
    parts = _parts(card_filter)
    if parts is None:
        return None
    properties: Properties = {}
    for part in parts:
        if not _read_part(part, properties):
            return None
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
    for flags in properties.get("keywords") or []:
        parts.append(_filter("HasAllAttributeFlags", m_CardAttributeFlags=flags))
    for flag, key in FLAGS.items():
        if properties.get(key):
            parts.append(_with_defaults(flag, {}))
    if properties.get("name"):
        parts.append(_with_defaults("IsCardName", {"m_CardName": properties["name"]}))
    if properties.get("colour"):
        parts.append(_with_defaults("IsColor", {"m_ColorFlags": properties["colour"]}))
    if properties.get("faction"):
        parts.append(_filter("InFaction", m_Faction=properties["faction"]))
    for inner in properties.get("without") or []:
        parts.append(_filter("NotCardFilter", m_TargetFilter=build_filter(inner)))
    if properties.get("either"):
        parts.append(_filter("OrCardFilter",
                             m_TargetFilters=[build_filter(option) for option in properties["either"]]))
    if not parts:
        raise ValueError("a target needs at least one property")
    if len(parts) == 1:
        return parts[0]
    return {"_t": FILTERS + "AndCardFilter", "m_TargetFilters": parts}
