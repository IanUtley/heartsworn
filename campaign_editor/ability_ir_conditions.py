"""Ability IR, condition layer.

A condition is one of three things in the Records:

* an ability or trigger condition (``Abilities.Conditions``, ``Triggers.Conditions``, and
  the And/Or/Not combinators), which appears inside an ability root, an effect, or a
  target;
* an ``AbilityEffectConditionTemplate`` record, a named wrapper around one condition.

Both are ``Node`` objects with their Records class kept in ``record_type``, so the
round trip is exact. Field names use the filter layer's conversion. Nested values
dispatch by what they are: conditions to ``Node``, card filters to the filter layer,
ability variables to the variable layer, target templates to the target layer,
GUID wrappers to ``Ref``/``NoRef``, and anything else (serialized TACs, counter
types) to ``Raw``. Trigger selectors such as ``TriggerSource`` stay as stored strings.
"""

from __future__ import annotations

from typing import Any

import ability_ir
import ability_ir_clauses as irc
import ability_ir_filters as irf
from ability_ir_effects import Node, Raw

CONDITION_NAMESPACE = "Game.Shared.Mechanics."
WRAPPER_TYPE = "Game.Shared.Mechanics.Abilities.AbilityEffectConditionTemplate"


def is_condition_type(record_type: str) -> bool:
    """A condition class (ability or trigger), or the effect-condition wrapper."""
    if record_type == WRAPPER_TYPE:
        return True
    return record_type.startswith(CONDITION_NAMESPACE) and (
        ".Conditions." in record_type or record_type.endswith("Condition"))


def value_from_record(value: Any) -> Any:
    """Convert a stored value that may appear inside a condition."""
    if isinstance(value, list):
        return [value_from_record(item) for item in value]
    if not isinstance(value, dict):
        return value
    if irc._is_guid_wrapper(value):
        return irc._value_from_record(value)
    record_type = str(value.get("_t") or "")
    if record_type.startswith(irf.FILTER_PREFIX):
        return irf.filter_from_record(value)
    if record_type.startswith(ability_ir.TARGET_PREFIX):
        return ability_ir.target_from_record(value)
    if record_type.startswith(ability_ir.RECORD_PREFIX) and \
            record_type.rsplit(".", 1)[-1] in ability_ir._KIND_BY_CLASS:
        return ability_ir.from_record(value)
    if is_condition_type(record_type):
        return node_from_record(value)
    return Raw(value)


def value_to_record(value: Any) -> Any:
    """Rebuild a stored value from its IR form."""
    if isinstance(value, list):
        return [value_to_record(item) for item in value]
    if isinstance(value, Node):
        return node_to_record(value)
    if isinstance(value, irf.Filter):
        return irf.filter_to_record(value)
    if isinstance(value, ability_ir.Var):
        return ability_ir.to_record(value)
    if isinstance(value, ability_ir.Target):
        return ability_ir.target_to_record(value)
    if isinstance(value, (irc.Ref, irc.NoRef)):
        return irc._value_to_record(value)
    if isinstance(value, Raw):
        return value.value
    return value


def node_from_record(record: dict[str, Any]) -> Node:
    """Read one condition (or wrapper) record into a ``Node``."""
    record_type = str(record.get("_t") or "")
    if not is_condition_type(record_type):
        raise ability_ir.UnknownVariable(f"not a condition: {record_type!r}")
    fields_: dict[str, Any] = {}
    for key, value in record.items():
        if key in ("_t", "_v"):
            continue
        if not key.startswith("m_"):
            raise ability_ir.UnknownVariable(f"{record_type} has a non-field key {key!r}")
        name = irf._snake(key)
        if irf._pascal(name) != key:
            raise ability_ir.UnknownVariable(f"{key!r} does not invert through {name!r}")
        fields_[name] = value_from_record(value)
    meta = {"_v": record["_v"]} if "_v" in record else {}
    return Node(record_type=record_type, fields=fields_, meta=meta)


def node_to_record(node: Node) -> dict[str, Any]:
    """Rebuild the Records dict for a condition node."""
    record: dict[str, Any] = {"_t": node.record_type}
    for name, value in node.fields.items():
        record[irf._pascal(name)] = value_to_record(value)
    record.update(node.meta)
    return record
