"""Ability IR as JSON, for the editor.

Every IR object becomes a JSON object with one tag key, so ``from_json`` knows what it
is without guessing: ``$node``, ``$clause``, ``$ref``, ``$noref``, ``$filter``, ``$var``,
``$target``, ``$tac``, ``$raw`` or ``$dict``. Plain values (numbers, strings, booleans, null, lists) pass
through unchanged. A ``$raw`` object is the stored Records object, kept verbatim.

``from_json(to_json(x)) == x`` for every ability root, which the tests check, so the
editor can send the tree out and get back exactly the record it started from.
"""

from __future__ import annotations

from typing import Any

import ability_ir
import ability_ir_clauses as irc
import ability_ir_filters as irf
from ability_ir_effects import Node, Raw


def _meta_to_json(meta: dict[str, Any]) -> dict[str, Any]:
    return {key: {"$raw": value} for key, value in meta.items()}


def _meta_from_json(meta: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in meta.items():
        decoded = from_json(value)
        out[key] = decoded.value if isinstance(decoded, Raw) else decoded
    return out


def _fields_to_json(fields: dict[str, Any]) -> dict[str, Any]:
    return {name: to_json(value) for name, value in fields.items()}


def _fields_from_json(fields: dict[str, Any]) -> dict[str, Any]:
    return {name: from_json(value) for name, value in fields.items()}


def to_json(value: Any) -> Any:
    """The JSON form of an IR value."""
    if isinstance(value, list):
        return [to_json(item) for item in value]
    if isinstance(value, Node):
        return {"$node": value.record_type, "fields": _fields_to_json(value.fields),
                "meta": _meta_to_json(value.meta)}
    if isinstance(value, irc.Clause):
        return {"$clause": _fields_to_json(value.fields)}
    if isinstance(value, irc.NoRef):
        return {"$noref": True, **({"$type": value.record_type} if value.record_type else {})}
    if isinstance(value, irc.Ref):
        return {"$ref": value.guid, **({"$type": value.record_type} if value.record_type else {})}
    if isinstance(value, irf.Filter):
        return {"$filter": value.kind, "fields": _fields_to_json(value.fields)}
    if isinstance(value, ability_ir.Var):
        return {"$var": value.kind, "base": {k: to_json(v) for k, v in value.base.items()},
                "fields": _fields_to_json(value.fields)}
    if isinstance(value, ability_ir.Target):
        return {"$target": value.kind, "fields": _fields_to_json(value.fields),
                "meta": _meta_to_json(value.meta)}
    if isinstance(value, ability_ir.Tac):
        return {"$tac": value.attributes, **({"$type": value.type_tag} if value.type_tag else {})}
    if isinstance(value, Raw):
        return {"$raw": value.value}
    if isinstance(value, dict):   # a plain dict a clause or variable stores as it is
        return {"$dict": {k: to_json(v) for k, v in value.items()}}
    return value


def from_json(value: Any) -> Any:
    """The IR value for its JSON form. Raises on a tag it does not know."""
    if isinstance(value, list):
        return [from_json(item) for item in value]
    if not isinstance(value, dict):
        return value
    if "$node" in value:
        return Node(record_type=value["$node"], fields=_fields_from_json(value["fields"]),
                    meta=_meta_from_json(value.get("meta", {})))
    if "$clause" in value:
        return irc.Clause(fields=_fields_from_json(value["$clause"]))
    if "$noref" in value:
        return irc.NoRef(value.get("$type"))
    if "$ref" in value:
        return irc.Ref(value["$ref"], value.get("$type"))
    if "$filter" in value:
        return irf.Filter(kind=value["$filter"], fields=_fields_from_json(value["fields"]))
    if "$var" in value:
        return ability_ir.Var(kind=value["$var"],
                              base={k: from_json(v) for k, v in value["base"].items()},
                              fields=_fields_from_json(value["fields"]))
    if "$target" in value:
        return ability_ir.Target(kind=value["$target"], fields=_fields_from_json(value["fields"]),
                                 meta=_meta_from_json(value.get("meta", {})))
    if "$tac" in value:
        return ability_ir.Tac(value["$tac"], value.get("$type", ""))
    if "$raw" in value:
        return Raw(value["$raw"])
    if "$dict" in value:
        return {k: from_json(v) for k, v in value["$dict"].items()}
    raise ValueError(f"unknown IR tag in {sorted(value)[:3]}")
