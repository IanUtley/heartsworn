"""Vocabulary of the ability IR: the node kinds the Records use, and the fields each one has.

It is read from the Records themselves: every object with a ``_t`` class is a node, and its field
names are the IR's (snake_case, from the filter layer's conversion). So the vocabulary lists what the
Records actually use, with how often, and an editor can complete against it. Kinds follow the layers:
filters use their own kind names (``ability_ir_filters.FILTER_KINDS``); effects, conditions and targets
use the class name with its suffix dropped (``ability_ir_effects.kind_of``).
"""

from __future__ import annotations

import collections
import functools
from typing import Any

import ability_ir
import ability_ir_effects as ef
import ability_ir_filters as irf
import card_abilities as ca
import records_reader

# The Records sections the IR reads, and the layer each section's objects belong to.
SECTIONS = {
    "AbilityTemplate": "root",
    "AbilityEffectTemplate": "effect",
    "AbilityTargetTemplate": "target",
    "AbilityEffectConditionTemplate": "condition",
    "CardCounterTemplate": "counter",
    "CardTemplate": "card",
    "ChampionTemplate": "champion",
}

# The values the IR holds that are not Records objects.
VALUES = [
    {"kind": "ref", "meaning": "a GUID reference to a Records object (irc.Ref)"},
    {"kind": "no_ref", "meaning": "the null GUID: no object (irc.NoRef)"},
    {"kind": "tac", "meaning": "a serialized TAC: its attributes (ability_ir.Tac)"},
    {"kind": "trigger_event", "meaning": "a trigger's event class (ability_ir.TriggerEvent)"},
    {"kind": "card_ability", "meaning": "a card's or champion's ability entry (ability_ir_cards.CardAbility)"},
    {"kind": "raw", "meaning": "a stored object the IR does not interpret (kept verbatim)"},
]

def _short(record_type: str) -> str:
    return record_type.rsplit(".", 1)[-1]


def _kind(record_type: str, layer: str) -> str:
    short = _short(record_type)
    if layer == "filter":
        return next((kind for kind, cls in irf.FILTER_KINDS.items() if cls == short), short)
    if layer == "variable":
        return ability_ir._KIND_BY_CLASS.get(short, ef.kind_of(record_type))
    return ef.kind_of(record_type)


def _layer(record_type: str) -> str:
    if record_type.startswith(irf.FILTER_PREFIX):
        return "filter"
    if record_type.startswith(ability_ir.RECORD_PREFIX) and _short(record_type) in ability_ir._KIND_BY_CLASS:
        return "variable"
    if record_type.startswith(ability_ir.TARGET_PREFIX):
        return "target"
    if ef.is_effect_type(record_type):
        return "effect"
    return "node"


def _collect(value: Any, nodes: dict[str, dict[str, Any]], layer: str | None) -> None:
    """Count every typed object under ``value``. A section's top-level record takes the section's
    ``layer``; a nested object takes the layer its class belongs to."""
    if isinstance(value, dict):
        record_type = str(value.get("_t") or "")
        if record_type:
            node_layer = layer or _layer(record_type)
            entry = nodes.setdefault(record_type, {"layer": node_layer, "records": 0, "fields": collections.Counter()})
            entry["records"] += 1
            for key in value:
                if key.startswith("m_"):
                    entry["fields"][irf._snake(key)] += 1
        for item in value.values():
            _collect(item, nodes, None)
    elif isinstance(value, list):
        for item in value:
            _collect(item, nodes, None)


@functools.lru_cache(maxsize=1)
def _nodes() -> tuple[dict[str, Any], ...]:
    """The node classes the Records use, each with its layer, record count and field counts."""
    found: dict[str, dict[str, Any]] = {}
    for section, layer in SECTIONS.items():
        if section in ("AbilityTemplate", "AbilityEffectTemplate", "AbilityTargetTemplate",
                       "AbilityEffectConditionTemplate"):
            records = ca._iter_records(section)
        else:
            records = records_reader.records(str(ca.RECORDS / f"{section}.jsonl"))
        for record in records:
            _collect(record, found, layer)   # the top-level record takes the section's layer
    out = []
    for record_type, entry in sorted(found.items()):
        out.append({"class": record_type, "kind": _kind(record_type, entry["layer"]), "layer": entry["layer"],
                    "records": entry["records"],
                    "fields": [{"name": name, "uses": uses} for name, uses in entry["fields"].most_common()]})
    return tuple(out)


def vocab() -> dict[str, Any]:
    """Every node kind and its fields, the value kinds, and the layers."""
    return {"nodes": [dict(node) for node in _nodes()], "values": VALUES,
            "layers": sorted({node["layer"] for node in _nodes()})}


def completions(prefix: str) -> list[dict[str, str]]:
    """Node kinds and field names that contain ``prefix`` (case-insensitive), for an editor to offer.
    Names that start with the prefix come first, then the names that merely contain it."""
    needle = (prefix or "").strip().lower()
    matches: list[tuple[int, dict[str, str]]] = []

    def rank(name: str) -> int:
        return 0 if name.lower().startswith(needle) else 1

    for node in _nodes():
        if needle in node["kind"].lower() or needle in node["class"].lower():
            matches.append((rank(node["kind"]), {"text": node["kind"], "type": "node",
                                                 "class": node["class"], "layer": node["layer"]}))
        for field_ in node["fields"]:
            if needle and needle in field_["name"].lower():
                matches.append((rank(field_["name"]), {"text": field_["name"], "type": "field",
                                                       "kind": node["kind"], "class": node["class"]}))
    matches.sort(key=lambda item: item[0])   # stable: the order within each rank is kept
    return [item for _, item in matches][:200]
