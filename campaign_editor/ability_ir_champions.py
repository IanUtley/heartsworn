"""Champion templates (``ChampionTemplate``): a champion's abilities, slots, starting values and presentation.

A champion lists its abilities in ``m_ChampionAbilities``, each a card-ability entry (a reference to an
ability root and the overrides), exactly as a card lists them. A charge power is one of those abilities:
an ability root with ``m_IsChargePower`` set, whose cost is ``m_ChargePointCost``. The champion's own
ability slots are references, and ``m_StartingCharges`` is the number of charges it starts with.
"""

from __future__ import annotations

from typing import Any

import ability_ir_cards as cards
import ability_ir_clauses as irc
import ability_ir_filters as irf
from ability_ir_cards import CardAbility, CardTemplate

RECORD_TYPE = "Reckoning.Game.ChampionTemplate"
ABILITIES_KEY = "m_ChampionAbilities"


def from_record(record: dict[str, Any]) -> CardTemplate:
    """Read one ``ChampionTemplate`` record. The result has the card node's shape."""
    if str(record.get("_t") or "") != RECORD_TYPE:
        raise ValueError(f"not a champion template: {record.get('_t')!r}")
    fields_: dict[str, Any] = {}
    for key, value in record.items():
        if key in ("_t", "_v"):
            continue
        if key == ABILITIES_KEY:
            fields_[irf._snake(key)] = [CardAbility(ability=irc._value_from_record(entry["m_CardAbilityId"]),
                                                    overrides=cards._from(entry["m_CardAbilityOverrides"]))
                                        for entry in value]
        else:
            fields_[irf._snake(key)] = cards._from(value)
    meta = {"_t": record["_t"], "_v": record["_v"]} if "_v" in record else {"_t": record["_t"]}
    return CardTemplate(fields=fields_, meta=meta)


def to_record(champion: CardTemplate) -> dict[str, Any]:
    """The Records dict for a champion template (the inverse of ``from_record``)."""
    return cards.to_record(champion)
