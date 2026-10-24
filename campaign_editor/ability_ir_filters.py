"""Ability IR, card-filter layer.

A card filter is a tree: combinators (``all_of``, ``any_of``, ``not``) over leaf
tests such as ``troop`` or ``in_zone``. Each Records filter class is one IR kind
in ``FILTER_KINDS``.

Field names are the stored names with ``m_`` dropped and CamelCase turned into
snake_case (``m_TargetFilters`` -> ``target_filters``). The conversion is checked
to invert exactly, so no field can change name on the way back.

Nested values keep their shape. A filter becomes a ``Filter`` node, an ability
variable goes through the variable layer (``ability_ir.from_record``), and any
other object (serialized TACs, counter types) is kept as ``Raw``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import ability_ir

FILTER_PREFIX = "Game.Shared.Mechanics.Cards.Filters."

# kind -> Records class
FILTER_KINDS: dict[str, str] = {
    "all_of": "AndCardFilter",
    "any_of": "OrCardFilter",
    "not": "NotCardFilter",
    "troop": "IsTroop",
    "hero": "IsHero",
    "resource": "IsResource",
    "artifact": "IsArtifact",
    "tapped": "IsTapped",
    "attacking": "IsAttacking",
    "blocking": "IsBlocking",
    "blocking_filter": "BlockingFilter",
    "being_blocked_by": "BeingBlockedByFilter",
    "controlled_by": "IsControlledBy",
    "not_controlled_by": "IsNotControlledBy",
    "in_zone": "InZone",
    "in_collection": "InCollection",
    "type": "IsType",
    "subtype": "IsSubType",
    "is_source": "IsAbilitySource",
    "name_is": "IsCardName",
    "has_name": "HasName",
    "name_contains": "NameContainsFilter",
    "int_attr": "IntAttrFilter",
    "resource_cost": "HasResourceCost",
    "source_cost": "HasSourceResourceCost",
    "casting_cost_vs_source": "HasSourceCastingCostFilter",
    "casting_cost_vs_counters": "CompareCastingCostToSourceCountersFilter",
    "color": "IsColor",
    "faction": "InFaction",
    "top_of_deck": "TopNOfDeck",
    "socketed": "IsSocketed",
    "socketable": "IsSocketable",
    "attack_compare": "HasAttackValue",
    "defense_compare": "HasDefenseValue",
    "counters": "HasCountersValue",
    "has_attributes": "HasAllAttributeFlags",
    "tac": "TACFilter",
    "shares_shard_with_source": "HasASharedShardWithSourceFilter",
    "shares_class_with_source_champion": "HasASharedClassWithSourceChampionFilter",
    "shares_subtype_with_source": "HasASharedSubtypeWithSourceFilter",
    "shares_subtype_with_source_champion": "HasASharedSubtypeWithSourceChampionFilter",
    "shares_rarity_with_source": "HasASharedRarityWithSourceFilter",
    "source_type": "HasSourceTypeFilter",
    "played_this_turn": "IsPlayedThisTurn",
    "damaged_this_turn": "IsDamagedThisTurn",
    "damaged_opponent_this_turn": "DamagedOpponentThisTurn",
    "attacked_this_turn": "HasAttackedThisTurn",
    "transformed": "IsTranformed",
    "equipped": "IsEquippedCardFilter",
    "rarity": "IsRarity",
    "keyword": "HasKeywordAbility",
    "mercenary": "IsMercenaryFilter",
    "alternate_art": "IsAlternateArt",
    "stored_card": "IsStoredCardFilter",
    "child_of_source": "IsChildOfAbilitySource",
    "parent_of_source": "IsParentOfAbilitySourceFilter",
    "other_troops": "OtherTroops",
    "any_card": "AnyCard",
    "attack_is_highest": "CompareAttackToHighestFilter",
    "attack_is_lowest": "CompareAttackToLowestFilter",
    "cost_is_highest": "CompareResourceCostToHighestFilter",
    "cost_is_my_highest": "CompareResourceCostToMyHighestFilter",
    "health_is_highest": "CompareHealthToHighestFilter",
    "attack_and_defense_vs_source": "CompareAttackAndDefenseFilter",
    "players_who_control": "PlayersWhoControlMatchingFilter",
}

_KIND_BY_CLASS = {cls: kind for kind, cls in FILTER_KINDS.items()}


@dataclass(frozen=True)
class Raw:
    """A nested object that is neither a filter nor a variable, kept verbatim."""

    value: dict[str, Any]


@dataclass
class Filter:
    """One card filter. ``fields`` holds the stored fields under their IR names."""

    kind: str
    fields: dict[str, Any] = field(default_factory=dict)


def _snake(key: str) -> str:
    stem = key[2:] if key.startswith("m_") else key
    # A stored underscore becomes a double underscore, so the inverse can tell it
    # from the word breaks that snake_case adds.
    stem = stem.replace("_", "__")
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", stem).lower()


# Stored names that are acronyms, so the snake_case inverse can restore them.
ACRONYMS = frozenset({"ai", "tac", "deprecated"})


def _pascal(name: str) -> str:
    parts: list[str] = []
    for part in name.split("_"):
        if part == "":
            parts.append("_")  # the second half of a stored double underscore
        elif part in ACRONYMS:
            parts.append(part.upper())
        else:
            parts.append(part[:1].upper() + part[1:])
    return "m_" + "".join(parts)


def _value_from_record(value: Any) -> Any:
    if isinstance(value, list):
        return [_value_from_record(item) for item in value]
    if isinstance(value, dict):
        stored_type = str(value.get("_t") or "")
        if stored_type.startswith(FILTER_PREFIX):
            return filter_from_record(value)
        if stored_type.startswith(ability_ir.RECORD_PREFIX):
            return ability_ir.from_record(value)
        return Raw(value)
    return value


def _value_to_record(value: Any) -> Any:
    if isinstance(value, list):
        return [_value_to_record(item) for item in value]
    if isinstance(value, Filter):
        return filter_to_record(value)
    if isinstance(value, ability_ir.Var):
        return ability_ir.to_record(value)
    if isinstance(value, Raw):
        return value.value
    return value


def filter_from_record(record: dict[str, Any]) -> Filter:
    """Read one filter record into a ``Filter``. Raises on anything unknown."""
    stored_type = str(record.get("_t") or "")
    if not stored_type.startswith(FILTER_PREFIX):
        raise ability_ir.UnknownVariable(f"not a card filter: {stored_type!r}")
    cls = stored_type[len(FILTER_PREFIX):]
    kind = _KIND_BY_CLASS.get(cls)
    if kind is None:
        raise ability_ir.UnknownVariable(f"no IR kind for filter class {cls}")
    fields_: dict[str, Any] = {}
    for key, value in record.items():
        if key == "_t":
            continue
        if not key.startswith("m_"):
            raise ability_ir.UnknownVariable(f"{cls} has a non-field key {key!r}")
        name = _snake(key)
        if _pascal(name) != key:
            raise ability_ir.UnknownVariable(f"{key!r} does not invert through {name!r}")
        fields_[name] = _value_from_record(value)
    return Filter(kind=kind, fields=fields_)


def filter_to_record(node: Filter) -> dict[str, Any]:
    """Rebuild the Records dict for a ``Filter``."""
    if node.kind not in FILTER_KINDS:
        raise ability_ir.UnknownVariable(f"unknown filter kind {node.kind!r}")
    record: dict[str, Any] = {"_t": FILTER_PREFIX + FILTER_KINDS[node.kind]}
    for name, value in node.fields.items():
        record[_pascal(name)] = _value_to_record(value)
    return record
