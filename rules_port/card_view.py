"""Typed card views for the static card filters.

The filters evaluate a card against the CardFilter tree. Until now the card was a dict built in
several places, each with its own keys. :class:`CardView` is the one place that says what a
static-filter card holds: each field has a name and a type, and a view is built from the static
card query (:func:`db_static_card_rows`), its permanent-buffs override, or the champion entry.

:meth:`CardView.as_mapping` returns exactly the mapping the filters take today, so a converted
caller sees the same values. The field meanings are unchanged here; in particular ``attack`` is the
template's attack as the static query reads it. Differences between producers are a separate
question, recorded in ``docs/RULES_PORT_PARITY.md``, and this module does not decide them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Sequence

from abilities.framework.targeting import shards_from_threshold


@dataclass(frozen=True)
class CardView:
    """One card as the static card filters see it."""

    card_uid: int
    card_type: str          # the database text (e.g. "Troop"), as the filters have always received it
    location: str
    user_id: int | None
    state: int
    attack: int             # the template's attack (static query)
    defense: int            # the template's defense (static query)
    name: str
    cost: int
    subtype: str
    shards: list[int] = field(default_factory=list)    # ECardShards flags from the threshold
    attributes: int = 0
    src_owner_side: str = "ai"

    @classmethod
    def from_static_row(cls, row: Sequence[Any]) -> "CardView":
        """Build a view from one row of ``db_static_card_rows`` (or ``db_card_list_stat_row``)."""
        user_id = row[3]
        return cls(
            card_uid=int(row[0]),
            card_type=row[1],
            location=row[2],
            user_id=user_id,
            state=int(row[4] or 0),
            attack=int(row[5] or 0),
            defense=int(row[6] or 0),
            name=row[7] or "",
            cost=row[8] or 0,
            subtype=row[9] or "",
            shards=list(shards_from_threshold(row[10])),
            attributes=int(row[11] or 0) | int(row[12] or 0),
            src_owner_side="player" if (user_id or 0) else "ai",
        )

    @classmethod
    def champion(cls, side: str, user_id: int, health: int) -> "CardView":
        """The synthetic champion entry the static filters see for ``side`` ("ai" or "player")."""
        return cls(
            card_uid=-1 if side == "ai" else -2,
            card_type="Champion",
            location="warzone",
            user_id=user_id,
            state=0,
            attack=0,
            defense=health,
            name="Champion",
            cost=0,
            subtype="",
            shards=[],
            attributes=0,
            src_owner_side=side,
        )

    def with_permanent_buffs(self, buffs_json: Any) -> "CardView":
        """Apply the instance overrides a permanent-buffs record carries.

        ``CardThresholdModifier`` and ``SubTypeModifier`` change the card's current thresholds and
        subtype, not its template, so the static filters must see those values. A record that does
        not decode leaves the view unchanged, as it always has.
        """
        try:
            buffs = json.loads(buffs_json or "{}") if buffs_json else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return self
        if not isinstance(buffs, dict):
            return self
        changes: dict[str, Any] = {}
        if isinstance(buffs.get("subtype"), str):
            changes["subtype"] = buffs["subtype"]
        if isinstance(buffs.get("thresholds"), list):
            changes["shards"] = [int(value) for value in buffs["thresholds"]]
        return replace(self, **changes) if changes else self

    def as_mapping(self) -> dict[str, Any]:
        """The mapping the static card filters take (the shape they have always received)."""
        return {
            "card_uid": self.card_uid,
            "card_type": self.card_type,
            "location": self.location,
            "user_id": self.user_id,
            "state": self.state,
            "attack": self.attack,
            "defense": self.defense,
            "name": self.name,
            "cost": self.cost,
            "subtype": self.subtype,
            "shards": list(self.shards),
            "attributes": self.attributes,
            "damaged_opponent_this_turn": [],
            "src_owner_side": self.src_owner_side,
        }
