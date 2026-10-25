"""Ability IR, root layer.

An ability root is one ``AbilityTemplate`` record. It holds the ability's costs, its
options, its trigger and its conditions, and the lists that link to its targets,
variables and clauses. The root and every condition are ``Node`` objects, with the
Records class kept in ``record_type``, so the round trip is exact. Clauses go through
the clause layer, and the other layers handle their own values:

* card filters (``Cards.Filters``) to the filter layer,
* ability variables to the variable layer,
* target templates to the target layer,
* ability and trigger conditions (``Abilities.Conditions``, ``Triggers.Conditions``,
  and the And/Or combinators) to ``Node``,
* GUID wrappers (``ResourceId``) to ``Ref`` or ``NoRef``,
* anything else (serialized TACs, counter costs, AI hints) to ``Raw``.

Field names use the filter layer's conversion, which is checked to invert for every key
in the ability records.
"""

from __future__ import annotations

from typing import Any

import ability_ir
import ability_ir_clauses as irc
import ability_ir_conditions
import ability_ir_filters as irf
from ability_ir_effects import Node, Raw

ROOT_KEY_EFFECT_LIST = "m_AbilityEffectList"


def _value_from_record(key: str, value: Any) -> Any:
    if key == ROOT_KEY_EFFECT_LIST:
        return [irc.clause_from_record(item) for item in value]
    if isinstance(value, list):
        return [_value_from_record("", item) for item in value]
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
    if ability_ir_conditions.is_condition_type(record_type):
        return ability_ir_conditions.node_from_record(value)
    return ability_ir.tac_from_record(value) or Raw(value)


def node_from_record(record: dict[str, Any]) -> Node:
    """Read one root or condition record into a ``Node``."""
    record_type = str(record.get("_t") or "")
    if not record_type:
        raise ability_ir.UnknownVariable("record has no type tag")
    fields_: dict[str, Any] = {}
    for key, value in record.items():
        if key in ("_t", "_v"):
            continue
        if not key.startswith("m_"):
            raise ability_ir.UnknownVariable(f"{record_type} has a non-field key {key!r}")
        name = irf._snake(key)
        if irf._pascal(name) != key:
            raise ability_ir.UnknownVariable(f"{key!r} does not invert through {name!r}")
        fields_[name] = _value_from_record(key, value)
    meta = {"_v": record["_v"]} if "_v" in record else {}
    return Node(record_type=record_type, fields=fields_, meta=meta)


def _value_to_record(value: Any) -> Any:
    if isinstance(value, list):
        return [_value_to_record(item) for item in value]
    if isinstance(value, irc.Clause):
        return irc.clause_to_record(value)
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
    if isinstance(value, ability_ir.Tac):
        return ability_ir.tac_to_record(value)
    if isinstance(value, Raw):
        return value.value
    return value


def node_to_record(node: Node) -> dict[str, Any]:
    """Rebuild the Records dict for a ``Node``."""
    record: dict[str, Any] = {"_t": node.record_type}
    for name, value in node.fields.items():
        record[irf._pascal(name)] = _value_to_record(value)
    record.update(node.meta)
    return record
