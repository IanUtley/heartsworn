"""Field defaults and type tables for the serialized mechanics objects.

``gamedata.mechanics.CLASSES`` (generated from the client source) gives each class its version,
base class and current fields with their client defaults. The Records store these objects with
their stored representation, which differs from the C# defaults in two ways: a bool is ``0`` or
``1``, and every declared current field is present on every stored object. So a field whose value
equals its stored default can be left out of the text, and the loader puts it back.

The ``_v`` table on a stored object lists the serialized types it contains: the classes of the
object and of its nested objects, each with its version, in first-seen order.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Iterable

from .mechanics import CLASSES

# A serialized TAC payload (``m_SerializedTAC``) is typed in ``_v`` as this class.
TAC_CLASS = "SerializableTAC"
TAC_VERSION = 1


def stored_value(default: Any) -> Any:
    """A client default in the form the Records store it: a bool is 0 or 1."""
    if isinstance(default, bool):
        return int(default)
    return default


def ir_name(key: str) -> str:
    """The IR's name for a Records field: ``m_GameText`` is ``game_text``. Matches the IR's own
    conversion (``ability_ir_filters._snake``)."""
    stem = key[2:] if key.startswith("m_") else key
    stem = stem.replace("_", "__")
    stem = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", stem)
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", stem).lower()


@lru_cache(maxsize=None)
def _chain(cls: str) -> tuple[str, ...]:
    """The class and its base classes, nearest first, while they are known."""
    names: list[str] = []
    current = cls
    while current and current in CLASSES and current not in names:
        names.append(current)
        current = CLASSES[current]["base"]
    return tuple(names) if names else (cls,)


@lru_cache(maxsize=None)
def current_fields(cls: str) -> dict[str, Any]:
    """Each current field of a class, including inherited ones: name -> stored default.

    The nearest declaration wins, and fields are listed base-first in declaration order (the
    order the client serializes them in)."""
    collected: dict[str, Any] = {}
    for name in reversed(_chain(cls)):
        if name not in CLASSES:
            continue
        for field_name, (_type, default) in CLASSES[name]["fields"].items():
            collected[ir_name(field_name)] = stored_value(default)
    return collected


def is_default(cls: str, field_name: str, value: Any) -> bool:
    """Whether a stored field of this class holds its default (and so can be left out)."""
    fields = current_fields(cls)
    if field_name not in fields:
        return False
    default = fields[field_name]
    return type(value) is type(default) and value == default




def version_table(pairs: Iterable[tuple[str, int | None]]) -> list[dict[str, int]]:
    """The ``_v`` list for a sequence of ``(type name, version)`` pairs in encounter order.

    Each type adds its base classes (the classes it inherits from that are serialized), then
    every class is listed once, at its first appearance."""
    table: list[dict[str, int]] = []
    seen: set[str] = set()
    for name, version in pairs:
        for cls in _chain(name):
            if cls in CLASSES:
                cls_version = CLASSES[cls]["version"]
            else:
                cls_version = version if cls == name else None
            if cls_version is None or cls in seen:
                continue
            seen.add(cls)
            table.append({cls: cls_version})
    return table
