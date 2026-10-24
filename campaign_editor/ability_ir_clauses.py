"""Ability IR, clause layer.

A clause is one entry of an ability's ``m_AbilityEffectList``: it applies one effect
template to one of the ability's targets, with a duration, an instance and group id,
and a condition. Clauses have no ``_t`` tag, so a ``Clause`` is just its fields.

Field names use the filter layer's conversion (``m_`` dropped, snake_case, checked to
invert). A GUID wrapper (``{"m_Guid": ...}``) becomes a ``Ref``, and the null GUID becomes
``NoRef`` so that "no condition" reads as nothing. Anything else is kept as stored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import ability_ir
import ability_ir_filters as irf

NULL_GUID = "00000000-0000-0000-0000-000000000000"


@dataclass(frozen=True)
class Ref:
    """A reference to a Records object by GUID."""

    guid: str


@dataclass(frozen=True)
class NoRef:
    """The null GUID: a reference to nothing (for example, no condition)."""


@dataclass
class Clause:
    """One effect application. ``fields`` holds the stored fields under IR names."""

    fields: dict[str, Any] = field(default_factory=dict)


def _is_guid_wrapper(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == {"m_Guid"}


def _value_from_record(value: Any) -> Any:
    if _is_guid_wrapper(value):
        guid = str(value["m_Guid"] or "")
        return NoRef() if guid in ("", NULL_GUID) else Ref(guid)
    return value


def _value_to_record(value: Any) -> Any:
    if isinstance(value, NoRef):
        return {"m_Guid": NULL_GUID}
    if isinstance(value, Ref):
        return {"m_Guid": value.guid}
    return value


def clause_from_record(record: dict[str, Any]) -> Clause:
    """Read one clause into a ``Clause``. Raises on a key that does not invert."""
    fields_: dict[str, Any] = {}
    for key, value in record.items():
        if not key.startswith("m_"):
            raise ability_ir.UnknownVariable(f"clause has a non-field key {key!r}")
        name = irf._snake(key)
        if irf._pascal(name) != key:
            raise ability_ir.UnknownVariable(f"{key!r} does not invert through {name!r}")
        fields_[name] = _value_from_record(value)
    return Clause(fields=fields_)


def clause_to_record(clause: Clause) -> dict[str, Any]:
    """Rebuild the stored clause dict."""
    return {irf._pascal(name): _value_to_record(value) for name, value in clause.fields.items()}
