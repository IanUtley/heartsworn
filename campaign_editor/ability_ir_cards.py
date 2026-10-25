"""Card templates (``CardTemplate``): a card's abilities, cost targets and presentation, as IR.

A card lists its abilities in ``m_CardAbilities``: each entry is a reference to an ability root
(``m_CardAbilityId``) and the card's overrides of it (``m_CardAbilityOverrides``). The cost
targets and the card's own GUIDs are references too. Play and deckbuilding conditions are the
condition and filter nodes the ability layers already read. Presentation values (layouts, display
text, art paths) are kept as stored: plain values, with any GUID inside them typed as a ``Ref``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import ability_ir
import ability_ir_clauses as irc
import ability_ir_filters as irf
import ability_ir_root as rt

RECORD_TYPE = "Reckoning.Game.CardTemplate"
ABILITIES_KEY = "m_CardAbilities"


@dataclass
class CardAbility:
    """One ability a card has: the ability root it refers to and the card's overrides of it."""

    ability: Any            # an irc.Ref to the AbilityTemplate
    overrides: Any = field(default_factory=dict)


@dataclass
class CardTemplate:
    """One card. ``fields`` holds the record's values by snake_case name; ``meta`` keeps ``_t`` and ``_v``."""

    fields: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


def _from(value: Any) -> Any:
    """A stored value as IR: GUID wrappers are Refs, a TAC is a Tac, a typed object is read by the
    ability layers, and a plain object or list keeps its shape with its contents read the same way."""
    if isinstance(value, list):
        return [_from(item) for item in value]
    if not isinstance(value, dict):
        return value
    if irc._is_guid_wrapper(value):
        return irc._value_from_record(value)
    tac = ability_ir.tac_from_record(value)
    if tac is not None:
        return tac
    if str(value.get("_t") or ""):
        return rt._value_from_record("", value)
    return {key: _from(item) for key, item in value.items()}


def _to(value: Any) -> Any:
    if isinstance(value, list):
        return [_to(item) for item in value]
    if isinstance(value, CardAbility):
        return {"m_CardAbilityId": irc._value_to_record(value.ability),
                "m_CardAbilityOverrides": _to(value.overrides)}
    if isinstance(value, dict):
        return {key: _to(item) for key, item in value.items()}
    return rt._value_to_record(value)


def from_record(record: dict[str, Any]) -> CardTemplate:
    """Read one ``CardTemplate`` record."""
    if str(record.get("_t") or "") != RECORD_TYPE:
        raise ValueError(f"not a card template: {record.get('_t')!r}")
    fields_: dict[str, Any] = {}
    for key, value in record.items():
        if key in ("_t", "_v"):
            continue
        if key == ABILITIES_KEY:
            fields_[irf._snake(key)] = [CardAbility(ability=irc._value_from_record(entry["m_CardAbilityId"]),
                                                    overrides=_from(entry["m_CardAbilityOverrides"]))
                                        for entry in value]
        else:
            fields_[irf._snake(key)] = _from(value)
    meta = {"_t": record["_t"], "_v": record["_v"]} if "_v" in record else {"_t": record["_t"]}
    return CardTemplate(fields=fields_, meta=meta)


def to_record(card: CardTemplate) -> dict[str, Any]:
    """The Records dict for a card template (the inverse of ``from_record``)."""
    record: dict[str, Any] = {}
    for name, value in card.fields.items():
        record[irf._pascal(name)] = _to(value)
    record.update(card.meta)
    return record
