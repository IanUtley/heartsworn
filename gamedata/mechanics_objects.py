"""Typed objects for the serialized mechanics records.

``from_record`` turns a Records object whose ``_t`` names a generated mechanics class (see
``gamedata.mechanics``) into an instance of a Python class built from that class: its fields are
attributes, named as the Records name them (``m_GameText``), and any field the record leaves out
takes its client default. ``to_record`` is the inverse. Nested objects are typed the same way, and
a value without a type tag stays a plain dict or list.

The round trip is exact: ``to_record(from_record(x)) == x`` for every stored object, with keys in
their stored order.
"""

from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any

from .mechanics import CLASSES
from .mechanics_fields import stored_value

# Attributes that hold the record's own bookkeeping, not a field.
_RESERVED = ("_t", "_v")


def _chain(cls: str) -> list[str]:
    names: list[str] = []
    current = cls
    while current and current in CLASSES and current not in names:
        names.append(current)
        current = CLASSES[current]["base"]
    return names


@lru_cache(maxsize=None)
def _defaults(cls: str) -> dict[str, Any]:
    """Every current field of a class (inherited ones too), name -> stored default."""
    collected: dict[str, Any] = {}
    # Base classes first, so a derived class's declaration wins.
    for name in reversed(_chain(cls)):
        for field_name, (_type, default) in CLASSES[name]["fields"].items():
            collected[field_name] = stored_value(default)
    return collected


class MechanicsObject:
    """One serialized mechanics object. ``type_name`` is its full Records ``_t``; ``class_name`` is
    the generated class it is an instance of. ``_order`` is the stored key order."""

    class_name: str = ""

    def __init__(self, type_name: str, values: dict[str, Any], version_table: Any = None):
        self.type_name = type_name
        self.version_table = version_table
        self._order = list(values)
        self._record_order = ["_t", *self._order]
        for key, value in values.items():
            setattr(self, key, value)

    # Fields are set from the Records dict at runtime (see __init__), so their names are not
    # known statically. Declaring the lookup as Any lets callers read a Records field typed.
    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)

    def present(self, name: str) -> bool:
        return name in self._order

    def to_record(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key in self._record_order:
            if key == "_t":
                out[key] = self.type_name
            elif key == "_v":
                out[key] = copy.deepcopy(self.version_table)
            else:
                out[key] = _to_value(getattr(self, key))
        return out

    def __repr__(self) -> str:
        return f"<{self.class_name} {len(self._order)} fields>"


@lru_cache(maxsize=None)
def _class_for(cls: str) -> type:
    """A Python class for one generated mechanics class, with its fields as attributes."""
    namespace = {"class_name": cls, "__module__": __name__}
    return type(cls, (MechanicsObject,), namespace)


def from_record(value: Any) -> Any:
    """The typed object for a Records value: an instance for a tagged mechanics object, a list or
    dict of typed values for containers, and the value itself otherwise."""
    if isinstance(value, list):
        return [from_record(item) for item in value]
    if not isinstance(value, dict):
        return value
    type_name = value.get("_t")
    if isinstance(type_name, str):
        cls = type_name.rsplit(".", 1)[-1]
        if cls in CLASSES:
            values = {key: from_record(item) for key, item in value.items() if key not in _RESERVED}
            # A class's own defaults fill its missing fields, after the stored ones.
            for key, default in _defaults(cls).items():
                values.setdefault(key, copy.deepcopy(default))
            obj = _class_for(cls)(type_name, {}, value.get("_v"))
            obj._order = [key for key in value if key not in _RESERVED]
            obj._record_order = list(value)
            for key, item in values.items():
                setattr(obj, key, item)
            return obj
    return {key: from_record(item) for key, item in value.items()}


def _to_value(value: Any) -> Any:
    if isinstance(value, MechanicsObject):
        return value.to_record()
    if isinstance(value, list):
        return [_to_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_value(item) for key, item in value.items()}
    return value
