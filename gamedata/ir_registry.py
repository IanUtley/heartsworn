"""A registry of named GUIDs for IR text.

When card text binds a name to a template object (an ability, an effect or a target), the name and the
template's GUID are registered here. Any later text, from the same card or another, can refer to that
template by name with ``Named("NAME")``, which gives the same ``Ref`` a GUID literal would.

A name can be registered again only with the same GUID; a different GUID for a name already in use is
an error, so two cards cannot silently share a name.
"""

from __future__ import annotations

from typing import Any

_REGISTRY: dict[str, dict[str, str]] = {}


def register(name: str, guid: str, kind: str) -> None:
    guid = str(guid).lower()
    existing = _REGISTRY.get(name)
    if existing is not None and existing["guid"] != guid:
        raise ValueError(f"{name} is already registered for {existing['guid']}, not {guid}")
    _REGISTRY[name] = {"guid": guid, "kind": kind}


def guid_of(name: str) -> str:
    if name not in _REGISTRY:
        raise KeyError(f"no registered name {name}")
    return _REGISTRY[name]["guid"]


def ref_of(name: str) -> dict[str, Any]:
    """The Records reference for a registered name."""
    return {"m_Guid": guid_of(name)}


def names() -> dict[str, dict[str, str]]:
    return {name: dict(entry) for name, entry in _REGISTRY.items()}


def clear() -> None:
    _REGISTRY.clear()
