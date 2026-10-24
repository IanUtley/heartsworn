"""Ability IR, effect-template layer.

An effect template is one ``AbilityEffectTemplate`` subclass (the ``...EffectTemplate``
classes under ``Game.Shared.Mechanics.Abilities``), and its ``m_Modifier`` is one of the
``Game.Shared.Mechanics.Modifiers`` classes. Each record becomes a ``Node`` that keeps
its Records class in ``record_type``, so ``to_record`` rebuilds it exactly. ``kind`` is
a readable alias derived from the class (``SummonTokenTroopAbilityEffectTemplate`` ->
``summon_token_troop``).

Field names use the same conversion as the filter layer (``m_`` dropped, snake_case,
checked to invert). Nested values dispatch by what they are: card filters to the filter
layer, ability variables to the variable layer, target templates to the target layer,
modifiers and nested effects to ``Node``, and anything else (serialized TACs, counter
types) to ``Raw``. TAC payloads are not decoded here yet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import ability_ir
import ability_ir_filters as irf

ABILITY_PREFIX = ability_ir.RECORD_PREFIX
MODIFIER_PREFIX = "Game.Shared.Mechanics.Modifiers."
EFFECT_SUFFIX = "EffectTemplate"
_EFFECT_STEMS = ("AbilityEffectTemplate", "EffectTemplate")


@dataclass(frozen=True)
class Raw:
    """A nested object the IR does not model, kept verbatim."""

    value: dict[str, Any]


@dataclass
class Node:
    """One effect template or modifier. ``record_type`` is the full Records ``_t``."""

    record_type: str
    fields: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return kind_of(self.record_type)


def kind_of(record_type: str) -> str:
    """The readable kind for a Records class: the class name, snake_cased, with the
    effect suffix dropped."""
    name = record_type.rsplit(".", 1)[-1]
    for stem in _EFFECT_STEMS:
        if name.endswith(stem) and name != stem:
            name = name[: -len(stem)]
            break
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()


def is_effect_type(record_type: str) -> bool:
    return record_type.startswith(ABILITY_PREFIX) and record_type.endswith(EFFECT_SUFFIX)


def _value_from_record(value: Any) -> Any:
    if isinstance(value, list):
        return [_value_from_record(item) for item in value]
    if not isinstance(value, dict):
        return value
    stored_type = str(value.get("_t") or "")
    if stored_type.startswith(irf.FILTER_PREFIX):
        return irf.filter_from_record(value)
    if stored_type.startswith(ability_ir.TARGET_PREFIX):
        return ability_ir.target_from_record(value)
    if stored_type.startswith(MODIFIER_PREFIX) or is_effect_type(stored_type):
        return node_from_record(value)
    if stored_type.startswith(ABILITY_PREFIX):
        return ability_ir.from_record(value)
    return Raw(value)


def _value_to_record(value: Any) -> Any:
    if isinstance(value, list):
        return [_value_to_record(item) for item in value]
    if isinstance(value, Node):
        return node_to_record(value)
    if isinstance(value, irf.Filter):
        return irf.filter_to_record(value)
    if isinstance(value, ability_ir.Var):
        return ability_ir.to_record(value)
    if isinstance(value, ability_ir.Target):
        return ability_ir.target_to_record(value)
    if isinstance(value, Raw):
        return value.value
    return value


def node_from_record(record: dict[str, Any]) -> Node:
    """Read one effect template or modifier record into a ``Node``."""
    record_type = str(record.get("_t") or "")
    if not (record_type.startswith(ABILITY_PREFIX) or record_type.startswith(MODIFIER_PREFIX)):
        raise ability_ir.UnknownVariable(f"not an effect or modifier: {record_type!r}")
    fields_: dict[str, Any] = {}
    for key, value in record.items():
        if key in ("_t", "_v"):
            continue
        if not key.startswith("m_"):
            raise ability_ir.UnknownVariable(f"{record_type} has a non-field key {key!r}")
        name = irf._snake(key)
        if irf._pascal(name) != key:
            raise ability_ir.UnknownVariable(f"{key!r} does not invert through {name!r}")
        fields_[name] = _value_from_record(value)
    meta = {"_v": record["_v"]} if "_v" in record else {}
    return Node(record_type=record_type, fields=fields_, meta=meta)


def node_to_record(node: Node) -> dict[str, Any]:
    """Rebuild the Records dict for a ``Node``."""
    record: dict[str, Any] = {"_t": node.record_type}
    for name, value in node.fields.items():
        record[irf._pascal(name)] = _value_to_record(value)
    record.update(node.meta)
    return record
