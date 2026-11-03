"""Typed card views for the RulesPort card filters.

The CardFilter evaluators read a card through a mapping. Several producers used to build those
mappings by hand, each with its own keys and its own meaning for them. :class:`CardView` is the one
place that says what a card holds. Each field has a name and a type, and a view is built by one of
the builders below from the database row that produced it.

Two mappings are kept, because the filters have always been given two shapes:

* :meth:`CardView.as_mapping` is the *static* shape the static card filters take
  (``abilities/framework/statics.py``). Its key set and values are unchanged.
* :meth:`CardView.as_condition_mapping` is the *condition* shape the condition layer takes
  (``rules_port/condition_context.py``): a card or, for a live champion, a champion entry.

``attack`` and ``defense`` always mean the value the producer chooses and the builder says so:
the static builder uses the template's value (``from_static_row``), the condition builder adds the
card's instance modifiers (``from_condition_row``). Whether each filter *should* see the template
or the current value is an open rules question, recorded in ``docs/RULES_PORT_PARITY.md``; the
builders keep today's choice rather than deciding it here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Sequence

from abilities.framework.targeting import shards_from_threshold
from rules_port.targeting import _shards as _condition_shards


@dataclass(frozen=True)
class CardView:
    """One card as a RulesPort filter sees it. See the module docstring for the two mappings."""

    card_uid: int
    card_type: str          # the database text (e.g. "Troop"); the filters parse it
    location: str
    user_id: int | None
    state: int
    attack: int             # effective attack, as the producing builder defines it
    defense: int
    name: str
    cost: int
    subtype: str
    shards: list[int] = field(default_factory=list)    # ECardShards flags from the threshold
    attributes: int = 0
    src_owner_side: str = "ai"
    damaged_opponent_this_turn: list = field(default_factory=list)

    # Condition-layer fields. Static views leave them at their defaults.
    kind: str = "card"      # "card", or "champion" for a live champion's condition entry
    owner_id: int | None = None
    controller_id: int | None = None
    template_guid: str = ""
    original_template_guid: str = ""
    gems: int = 0
    socket_count: int = 0
    rarity: str = ""
    int_attrs: dict = field(default_factory=dict)
    card_abilities: list = field(default_factory=list)
    counters: dict = field(default_factory=dict)
    counter_guids: dict = field(default_factory=dict)
    position: int = 0
    tags: dict = field(default_factory=dict)

    # --- builders -------------------------------------------------------------------------

    @classmethod
    def from_static_row(cls, row: Sequence[Any]) -> "CardView":
        """A view from one row of ``db_static_card_rows`` (or ``db_card_list_stat_row``).

        ``attack`` and ``defense`` are the template's values: the static query does not read the
        card's instance modifiers.
        """
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
    def from_condition_row(cls, row: Sequence[Any], *, buffs: dict, card_abilities: list,
                           counters: dict, counter_guids: dict, effective_cost: Any,
                           damaged_opponent_this_turn: list) -> "CardView":
        """A view from one row of ``db_condition_card_row``.

        ``attack`` and ``defense`` are the template's values plus the card's instance modifiers.
        The caller reads the card's buffs, abilities, counters and effective cost (they come from
        other tables); this builder only maps them.
        """
        user_id = row[3]
        return cls(
            card_uid=int(row[0]),
            card_type=row[1] or "",
            location=row[2] or "",
            user_id=user_id,
            state=int(row[4] or 0),
            attack=int(row[5] or 0) + int(row[14] or 0),
            defense=int(row[6] or 0) + int(row[15] or 0),
            name=row[8] or "",
            cost=int(effective_cost or 0),
            subtype=row[10] or "",
            shards=_condition_shards(row[11]),
            attributes=int(row[12] or 0) | int(row[13] or 0),
            src_owner_side="player" if (user_id or 0) else "ai",
            damaged_opponent_this_turn=list(damaged_opponent_this_turn),
            kind="card",
            owner_id=user_id,
            controller_id=user_id,
            template_guid=row[7] or "",
            original_template_guid=row[19] or "",
            # Socketed gems feed IsSocketed and shared-gem filters; the template's socket
            # count feeds IsSocketable; the rarity feeds IsRarity.
            gems=int(row[20] or 0) if len(row) > 20 else 0,
            socket_count=int(row[22] or 0) if len(row) > 22 else 0,
            rarity=row[21] if len(row) > 21 else "",
            int_attrs=buffs.get("int_attrs", {}),
            card_abilities=card_abilities,
            counters=counters,
            counter_guids=counter_guids,
            position=int(row[18] or 0),
            tags=buffs.get("tags", {}) or {},
        )

    @classmethod
    def from_condition_champion(cls, uid: int, owner: Any, name: str | None, health: Any,
                                counters: dict, counter_guids: dict,
                                int_attrs: dict) -> "CardView":
        """The condition entry for a live champion (a synthetic card with no game_cards row)."""
        return cls(
            card_uid=uid,
            card_type="Champion",
            location="champions",
            user_id=owner,
            state=0,
            attack=0,
            defense=int(health or 0),
            name=name or "Champion",
            cost=0,
            subtype="",
            shards=[],
            attributes=0,
            src_owner_side="player" if (owner or 0) else "ai",
            kind="champion",
            owner_id=owner,
            controller_id=owner,
            counters=counters,
            counter_guids=counter_guids,
            int_attrs=int_attrs,
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

    # --- overrides ------------------------------------------------------------------------

    def with_permanent_buffs(self, buffs_json: Any) -> "CardView":
        """Apply the instance overrides a permanent-buffs record carries (static path only).

        ``CardThresholdModifier`` and ``SubTypeModifier`` change the card's current thresholds and
        subtype, not its template, so the static filters must see those values. A record that does
        not decode leaves the view unchanged.
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

    # --- mappings -------------------------------------------------------------------------

    def as_mapping(self) -> dict[str, Any]:
        """The static card filters' mapping (the shape they have always received)."""
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

    def as_condition_mapping(self) -> dict[str, Any]:
        """The condition layer's mapping: a champion entry, or a card."""
        if self.kind == "champion":
            return {
                "card_uid": self.card_uid,
                "card_type": self.card_type,
                "location": self.location,
                "user_id": self.user_id,
                "owner_id": self.owner_id,
                "controller_id": self.controller_id,
                "attack": self.attack,
                "defense": self.defense,
                "name": self.name,
                "cost": self.cost,
                "shards": list(self.shards),
                "attributes": self.attributes,
                "counters": self.counters,
                "counter_guids": self.counter_guids,
                "int_attrs": self.int_attrs,
            }
        return {
            "card_uid": self.card_uid,
            "card_type": self.card_type,
            "location": self.location,
            "user_id": self.user_id,
            "owner_id": self.owner_id,
            "controller_id": self.controller_id,
            "state": self.state,
            "attack": self.attack,
            "defense": self.defense,
            "template_guid": self.template_guid,
            "name": self.name,
            "original_template_guid": self.original_template_guid,
            "gems": self.gems,
            "socket_count": self.socket_count,
            "rarity": self.rarity,
            "cost": self.cost,
            "subtype": self.subtype,
            "shards": list(self.shards),
            "attributes": self.attributes,
            "int_attrs": self.int_attrs,
            "card_abilities": self.card_abilities,
            "counters": self.counters,
            "counter_guids": self.counter_guids,
            "position": self.position,
            "tags": self.tags,
            "damaged_opponent_this_turn": list(self.damaged_opponent_this_turn),
        }
