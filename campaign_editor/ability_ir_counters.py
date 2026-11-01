"""Card counter templates (``CardCounterTemplate``): the counters an ability adds, removes or tests.

Counter costs, counter modifiers, counter variables and ``SourceCardHasCounters`` conditions refer to
a counter by ``m_CardCounterTemplateId``. The counter's own record is read here, so a reference can be
followed to its name and prefab. Field names follow the filter layer's conversion, and the client
declares the same fields (``m_CardCounterId``, ``m_Name``, ``m_Description``, ``m_CounterPrefab``,
``m_Facedown``, ``m_Secret``, ``m_Hidden``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import ability_ir_clauses as irc
import ability_ir_filters as irf

RECORD_TYPE = "Reckoning.Game.CardCounterTemplate"


@dataclass
class CounterTemplate:
    """One counter definition. ``fields`` holds the record's values by snake_case name; a GUID is a
    ``Ref``. ``meta`` keeps the record's ``_v`` version tag."""

    fields: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


def from_record(record: dict[str, Any]) -> CounterTemplate:
    """Read one ``CardCounterTemplate`` record."""
    if str(record.get("_t") or "") != RECORD_TYPE:
        raise ValueError(f"not a counter template: {record.get('_t')!r}")
    fields_: dict[str, Any] = {}
    for key, value in record.items():
        if key in ("_t", "_v"):
            continue
        fields_[irf._snake(key)] = irc._value_from_record(value)
    meta = {"_v": record["_v"]} if "_v" in record else {}
    return CounterTemplate(fields=fields_, meta=meta)


def to_record(counter: CounterTemplate) -> dict[str, Any]:
    """The Records dict for a counter template (the inverse of ``from_record``)."""
    record: dict[str, Any] = {"_t": RECORD_TYPE}
    for name, value in counter.fields.items():
        record[irf._pascal(name)] = irc._value_to_record(value)
    record.update(counter.meta)
    return record
