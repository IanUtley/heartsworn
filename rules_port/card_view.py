"""Typed card views for the RulesPort card filters.

The CardFilter evaluators read a card through a mapping. Several producers used to build those
mappings by hand, each with its own keys. :class:`CardView` is the one place that says what a card
holds: each field has a name and a type, and a view is built by one of the builders below from the
database row (or champion entry) that produced it.

A consumer receives a *shape*: the mapping with a fixed key set that the filters have always been
given. :data:`SHAPES` lists each shape's keys, and :meth:`CardView.as_shape` builds it from the
fields. The values are unchanged from the producers they replace; only the way they are built is
new. Several shapes differ in ``attack``, ``defense``, ``cost`` and the threshold and subtype
overrides. Those differences are per-consumer decisions recorded in ``docs/RULES_PORT_PARITY.md``,
and the builders keep each producer's current choice rather than deciding them here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Sequence

from abilities.framework.targeting import shards_from_threshold


def threshold_shards(value: Any) -> list[int]:
    """The ECardShards flags of a card's threshold record (``{"list": [...]}`` or a bare list)."""
    try:
        data = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    values = data if isinstance(data, list) else data.get("list", data.get("values", []))
    return [{0: 0, 1: 4, 2: 8, 3: 16, 4: 32, 5: 64}.get(int(item), 0)
            for item in (values or [])]


_condition_shards = threshold_shards


# The key set each consumer receives. Keys are CardView field names; the order is not significant.
_CARD_CORE = ("card_uid", "card_type", "location", "user_id", "owner_id", "controller_id",
              "template_guid", "state", "attack", "defense", "name", "cost", "subtype",
              "attributes", "int_attrs", "shards", "rarity", "socket_count", "gems",
              "card_abilities", "counters", "counter_guids", "tags", "parent_uid",
              "original_template_guid")
SHAPES: dict[str, tuple[str, ...]] = {
    # The static card filters (abilities/framework/statics.py).
    "static": ("card_uid", "card_type", "location", "user_id", "state", "attack", "defense",
               "name", "cost", "subtype", "shards", "attributes", "damaged_opponent_this_turn",
               "src_owner_side"),
    # The condition layer (rules_port/condition_context.py).
    "condition_card": ("card_uid", "card_type", "location", "user_id", "owner_id", "controller_id",
                       "state", "attack", "defense", "template_guid", "name",
                       "original_template_guid", "gems", "socket_count", "rarity", "cost",
                       "subtype", "shards", "attributes", "int_attrs", "card_abilities",
                       "counters", "counter_guids", "position", "tags",
                       "damaged_opponent_this_turn"),
    "condition_champion": ("card_uid", "card_type", "location", "user_id", "owner_id",
                           "controller_id", "attack", "defense", "name", "cost", "shards",
                           "attributes", "counters", "counter_guids", "int_attrs"),
    # A card projected from its full row (rules_port/targeting.py: _project_card). ``thresholds``
    # is present only once the effective threshold view has been applied.
    "projected": _CARD_CORE,
    # A card from the source-card query (rules_port/targeting.py: _source_card).
    "source": ("card_uid", "card_type", "location", "user_id", "owner_id", "controller_id",
               "template_guid", "state", "attack", "defense", "name", "cost", "subtype",
               "attributes", "shards", "rarity", "socket_count", "gems",
               "original_template_guid", "int_attrs", "card_integer_variables", "card_abilities",
               "parent_uid"),
    # The stand-in for the source when there is no card row (rules_port/targeting.py).
    "source_champion": ("card_uid", "location", "user_id", "owner_id", "controller_id",
                        "card_type", "attack", "defense"),
    # A card revealed to a filter (rules_port/targeting.py: revealed_target_uids).
    "revealed": ("card_uid", "card_type", "location", "user_id", "state", "attack", "defense",
                 "template_guid", "name", "cost", "subtype", "shards", "attributes", "gems",
                 "src_owner_side"),
    # A live champion as a target (rules_port/targeting.py: _special_target_card and the
    # legal-target fallback, which omits owner_id, attributes and int_attrs).
    "special_champion": ("card_uid", "card_type", "location", "user_id", "owner_id",
                         "controller_id", "name", "defense", "attack", "attributes", "int_attrs"),
    "legal_champion": ("card_uid", "card_type", "location", "user_id", "controller_id", "name",
                       "defense", "attack"),
    # A champion as a card candidate with its template identity (targeting.py:
    # _champion_filter_cards).
    # The stub a bury effect's card filter is evaluated against (rules_port/context.py: bury).
    "bury_source": ("card_uid", "user_id", "owner_id", "controller_id"),
    # A card a bury effect's filter considers (rules_port/context.py: bury).
    "bury_card": ("card_uid", "card_type", "location", "user_id", "state", "name", "cost",
                  "subtype", "attributes"),
    # A card counted by a card-count variable (rules_port/context.py: set_card_count_variable).
    "count_card": ("card_uid", "card_type", "location", "user_id", "state", "attack", "defense",
                   "name", "cost", "subtype"),
    # Combat-rule evaluation of a battlefield card (rules_port/combat_rules.py).
    "combat_rule": ("card_uid", "card_type", "location", "user_id", "owner_id", "controller_id",
                    "template_guid", "shards", "attack", "defense", "attributes", "rule_flags"),
    # A champion as a counter-bearing candidate (rules_port/static_rules.py: champion pool).
    "champion_stub": ("card_uid", "card_type", "location", "user_id", "owner_id", "controller_id",
                      "name", "attack", "defense", "cost", "attributes", "counters",
                      "counter_guids"),
    # A template as a token-creation candidate (rules_port/token_effects.py: template pool).
    "token_candidate": ("card_uid", "template_guid", "name", "card_type", "cost", "rarity",
                        "shards", "thresholds", "subtype", "socket_count", "attributes",
                        "user_id", "attack", "defense", "_card_template_candidate"),
    # A template in the fused-card pool (rules_port/token_effects.py: fuse candidates).
    "fuse_candidate": ("card_uid", "template_guid", "name", "card_type", "cost", "rarity",
                       "subtype", "attributes", "user_id", "shards"),
    # A card a random transform considers, in its current place (rules_port/transform_effects.py).
    "transform_card": ("card_uid", "template_guid", "name", "card_type", "location", "user_id",
                       "state", "cost", "rarity", "shards", "subtype", "attributes", "counters",
                       "counter_guids", "tags"),
    # A template a random transform may turn a card into (rules_port/transform_effects.py).
    "transform_candidate": ("card_uid", "template_guid", "name", "card_type", "cost", "rarity",
                            "shards", "subtype", "attributes", "location", "user_id"),
    # A setup card with a targeted encounter passive (application/card_state.py).
    "scene_card": ("card_uid", "card_type", "location", "shards", "name", "user_id", "subtype",
                   "attributes", "int_attrs", "src_owner_id"),
    "filter_champion": ("card_uid", "card_type", "location", "collection", "user_id", "owner_id",
                        "controller_id", "template_guid", "template_id", "name", "subtype",
                        "attack", "attributes", "defense", "counters", "counter_guids"),
}


@dataclass(frozen=True)
class CardView:
    """One card as a RulesPort filter sees it. See the module docstring for the shapes."""

    card_uid: int
    card_type: str          # the database text (e.g. "Troop"); the filters parse it
    location: str
    user_id: int | None
    state: int
    attack: int             # the value the producing builder defines (see its docstring)
    defense: int
    name: str
    cost: int
    subtype: str
    shards: list[int] = field(default_factory=list)    # ECardShards flags from the threshold
    attributes: int = 0
    src_owner_side: str = "ai"
    damaged_opponent_this_turn: list = field(default_factory=list)

    # Fields used by the richer shapes. Shapes that do not list one never read it.
    kind: str = "card"      # "card", or "champion" for a live champion's condition entry
    owner_id: int | None = None
    controller_id: int | None = None
    template_guid: str = ""
    template_id: str | None = None
    original_template_guid: str = ""
    gems: int = 0
    socket_count: int = 0
    rarity: str = ""
    int_attrs: dict = field(default_factory=dict)
    card_abilities: list = field(default_factory=list)
    card_integer_variables: dict = field(default_factory=dict)
    counters: dict = field(default_factory=dict)
    counter_guids: dict = field(default_factory=dict)
    position: int = 0
    tags: dict = field(default_factory=dict)
    parent_uid: int | None = None
    collection: str | None = None
    thresholds: list[int] | None = None
    template_candidate: bool = False
    rule_flags: Any = None
    # A scene card's controller override; the shape lists it only when it is set.
    src_owner_id: int | None = None

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
    def from_projected_row(cls, row: Sequence[Any]) -> "CardView":
        """The base view of a card projected from its full row (``_project_card``).

        ``attack``/``defense`` are the template's values. :meth:`with_effective` applies the
        current values when the caller has a database session.
        """
        row_data = tuple(row)
        if len(row_data) >= 19:
            (uid, card_type, location, owner, template_guid, state, attack, defense,
             name, cost, subtype, threshold, abilities, buffs, rarity, sockets, gems,
             original_guid, card_attributes) = row_data[:19]
        else:
            (uid, card_type, location, owner, template_guid, state, attack, defense,
             name, cost, subtype, threshold, abilities, buffs, rarity, sockets, gems,
             original_guid) = row_data[:18]
            card_attributes = 0
        saved = _json_object(buffs)
        attrs: dict = saved["int_attrs"] if isinstance(saved.get("int_attrs"), dict) else {}
        return cls(
            card_uid=int(uid),
            card_type=card_type or "",
            location=location or "",
            user_id=int(owner or 0),
            owner_id=int(owner or 0),
            controller_id=int(owner or 0),
            template_guid=template_guid or "",
            state=int(state or 0),
            attack=int(attack or 0),
            defense=int(defense or 0),
            name=name or "",
            cost=int(cost or 0),
            subtype=saved.get("subtype", subtype or ""),
            attributes=int(saved.get("attributes", 0) or 0) | int(card_attributes or 0),
            int_attrs=attrs,
            shards=_condition_shards(threshold),
            rarity=rarity or "",
            socket_count=int(sockets or 0),
            gems=int(gems or 0),
            card_abilities=(json.loads(abilities or "[]")
                            if isinstance(abilities, str) else (abilities or [])),
            counters=saved.get("counters", {}),
            counter_guids=saved.get("counter_guids", {}),
            tags=saved.get("tags", {}) or {},
            parent_uid=int(saved.get("parent_uid", 0) or 0),
            original_template_guid=original_guid or "",
        )

    def with_effective(self, thresholds: list[int], subtype: str,
                       stats: Sequence[Any] | None) -> "CardView":
        """Apply the current threshold, subtype and stats of a card with a session.

        ``stats`` is ``(attack, defense, ...)`` from the effective-stats evaluation, or None when
        that evaluation is unavailable, in which case the template's values stand.
        """
        changes: dict[str, Any] = {"shards": thresholds, "subtype": subtype,
                                   "thresholds": thresholds}
        if stats is not None:
            changes["attack"], changes["defense"] = (int(value) for value in stats[:2])
        return replace(self, **changes)

    @classmethod
    def from_source_row(cls, row: Sequence[Any]) -> "CardView":
        """A view from one row of ``db_target_source_row`` (``_source_card``)."""
        (uid, card_type, location, owner, state, attack, defense,
         template_guid, name, cost, subtype, threshold, attributes,
         rarity, sockets, gems, original_guid, abilities, buffs) = row
        saved = _json_object(buffs)
        return cls(
            card_uid=int(uid),
            card_type=card_type or "",
            location=location or "",
            user_id=int(owner or 0),
            owner_id=int(owner or 0),
            controller_id=int(owner or 0),
            template_guid=template_guid or "",
            state=int(state or 0),
            attack=int(attack or 0),
            defense=int(defense or 0),
            name=name or "",
            cost=int(cost or 0),
            subtype=subtype or "",
            attributes=int(attributes or 0),
            shards=_condition_shards(threshold),
            rarity=rarity or "",
            socket_count=int(sockets or 0),
            gems=int(gems or 0),
            original_template_guid=original_guid or "",
            int_attrs=saved.get("int_attrs", {}) or {},
            card_integer_variables=saved.get("card_integer_variables", {}) or {},
            card_abilities=(json.loads(abilities or "[]")
                            if isinstance(abilities, str) else (abilities or [])),
            parent_uid=_parent_uid_of(buffs),
        )

    @classmethod
    def source_champion(cls, source_uid: Any, controller_uid: Any) -> "CardView":
        """The source stand-in when no card row exists: the controlling champion, with no stats."""
        return cls(
            card_uid=int(source_uid or 0),
            card_type="Champion",
            location="champions",
            user_id=int(controller_uid or 0),
            owner_id=int(controller_uid or 0),
            controller_id=int(controller_uid or 0),
            state=0,
            attack=0,
            defense=0,
            name="",
            cost=0,
            subtype="",
            kind="champion",
        )

    @classmethod
    def from_revealed_row(cls, row: Sequence[Any], owner_id: Any) -> "CardView":
        """A view from one row of ``db_condition_card_row`` as a revealed card (``revealed_target_uids``).

        Its ``attack`` and ``defense`` are the template's values, without the instance modifiers.
        """
        return cls(
            card_uid=int(row[0]),
            card_type=row[1] or "",
            location=row[2] or "",
            user_id=int(row[3] or 0),
            state=int(row[4] or 0),
            attack=int(row[5] or 0),
            defense=int(row[6] or 0),
            template_guid=row[7] or "",
            name=row[8] or "",
            cost=int(row[9] or 0),
            subtype=row[10] or "",
            shards=_condition_shards(row[11]),
            attributes=int(row[12] or 0) | int(row[13] or 0),
            gems=int(row[20] or 0) if len(row) > 20 else 0,
            src_owner_side="player" if int(owner_id or 0) else "ai",
        )

    @classmethod
    def special_champion(cls, uid: Any, owner: Any, name: str | None, health: Any) -> "CardView":
        """A live champion as a target (``_special_target_card``; the legal-target shape omits some keys)."""
        return cls(
            card_uid=int(uid),
            card_type="Champion",
            location="champions",
            user_id=owner,
            owner_id=owner,
            controller_id=owner,
            state=0,
            attack=0,
            defense=int(health or 0),
            name=name or "Champion",
            cost=0,
            subtype="",
            kind="champion",
        )

    @classmethod
    def filter_champion(cls, uid: int, owner: int, name: Any, guid: str, subtype: str,
                        attributes: int, defense: Any, counters: dict) -> "CardView":
        """A champion as a card candidate (``_champion_filter_cards``)."""
        return cls(
            card_uid=uid,
            card_type="Champion",
            location="champions",
            collection="champions",
            user_id=owner,
            owner_id=owner,
            controller_id=owner,
            template_guid=guid,
            template_id=guid,
            state=0,
            name=name,
            cost=0,
            subtype=subtype,
            attack=0,
            attributes=attributes,
            defense=defense,
            counters=counters,
            counter_guids={guid_: guid_ for guid_ in counters},
            kind="champion",
        )

    @classmethod
    def bury_source(cls, card_uid: Any, owner: Any) -> "CardView":
        """The stub a bury effect's filter is evaluated against: the source card and the deck owner."""
        return cls(
            card_uid=int(card_uid or 0),
            card_type="",
            location="",
            user_id=int(owner or 0),
            state=0,
            attack=0,
            defense=0,
            name="",
            cost=0,
            subtype="",
            owner_id=int(owner or 0),
            controller_id=int(owner or 0),
        )

    @classmethod
    def from_bury_row(cls, row: Sequence[Any]) -> "CardView":
        """A deck card a bury effect's filter considers, from one ``db_condition_card_row``.

        ``cost`` is the template's cost (the condition row's ``ct.cost``), not the effective cost.
        """
        return cls(
            card_uid=int(row[0]),
            card_type=row[1] or "",
            location=row[2] or "",
            user_id=int(row[3] or 0),
            state=int(row[4] or 0),
            attack=0,
            defense=0,
            name=row[8] or "",
            cost=int(row[9] or 0),
            subtype=row[10] or "",
            attributes=int(row[12] or 0) | int(row[13] or 0),
        )

    @classmethod
    def from_count_row(cls, row: Sequence[Any]) -> "CardView":
        """A card a card-count variable counts, from one ``db_cards_for_filter`` row.

        ``attack``, ``defense`` and ``cost`` are the template's values.
        """
        return cls(
            card_uid=int(row[0]),
            card_type=row[1] or "",
            location=row[2] or "",
            user_id=int(row[3] or 0),
            state=int(row[4] or 0),
            attack=int(row[5] or 0),
            defense=int(row[6] or 0),
            name=row[7] or "",
            cost=int(row[8] or 0),
            subtype=row[9] or "",
        )

    @classmethod
    def combat_rule(cls, card_uid: int, card_type: str, location: str, owner: Any,
                    template_guid: str, shards: list[int], attack: int, defense: int,
                    attributes: int, rule_flags: Any) -> "CardView":
        """A battlefield card as the combat rules see it (values as the producer computed them)."""
        return cls(card_uid=card_uid, card_type=card_type, location=location, user_id=owner,
                   owner_id=owner, controller_id=owner, template_guid=template_guid,
                   state=0, attack=attack, defense=defense, name="", cost=0, subtype="",
                   shards=shards, attributes=attributes, rule_flags=rule_flags)

    @classmethod
    def champion_stub(cls, card_uid: int, owner: Any, counters: dict) -> "CardView":
        """A champion in the counter-bearing pool; its counter GUIDs are the lower-cased keys."""
        return cls(card_uid=card_uid, card_type="Champion", location="champions", user_id=owner,
                   owner_id=owner, controller_id=owner, state=0, name="Champion", attack=0,
                   defense=0, cost=0, subtype="", attributes=0, counters=counters,
                   counter_guids={str(guid).lower(): str(guid).lower() for guid in counters},
                   kind="champion")

    @classmethod
    def token_candidate(cls, template_guid: str, name: str, card_type: str, cost: int,
                        rarity: str, shards: list[int], thresholds: Any, subtype: str,
                        socket_count: int, attributes: int, owner: Any, attack: int,
                        defense: int) -> "CardView":
        """A template as a token-creation candidate (``card_uid`` is 0: it is not a card yet)."""
        return cls(card_uid=0, card_type=card_type, location="", user_id=owner, state=0,
                   attack=attack, defense=defense, name=name, cost=cost, subtype=subtype,
                   shards=shards, attributes=attributes, template_guid=template_guid,
                   rarity=rarity, socket_count=socket_count, thresholds=thresholds,
                   template_candidate=True)

    @classmethod
    def fuse_candidate(cls, template_guid: str, name: Any, card_type: Any, cost: int,
                       rarity: Any, subtype: Any, owner: Any) -> "CardView":
        """A template in the fused-card pool (values as read from the template record)."""
        return cls(card_uid=0, card_type=card_type, location="", user_id=owner, state=0,
                   attack=0, defense=0, name=name, cost=cost, subtype=subtype, shards=[],
                   attributes=0, template_guid=template_guid, rarity=rarity)

    @classmethod
    def transform_card(cls, uid: int, row: Sequence[Any], saved: dict) -> "CardView":
        """A card a random transform considers (``card_record``): its row and parsed buffs."""
        return cls(card_uid=int(uid), template_guid=row[0], name=row[6] or "",
                   card_type=row[1] or "", location=row[2] or "", user_id=int(row[3] or 0),
                   state=int(row[4] or 0), attack=0, defense=0, cost=int(row[7] or 0),
                   rarity=row[8] or "", shards=shards_from_threshold(row[9]),
                   subtype=row[10] or "",
                   attributes=int(row[11] or 0) | int(row[12] or 0),
                   counters=saved.get("counters") or {},
                   counter_guids=saved.get("counter_guids") or {},
                   tags=saved.get("tags") or {})

    @classmethod
    def transform_candidate(cls, row: Sequence[Any], owner: Any) -> "CardView":
        """A template a random transform may become (candidate rows: guid, name, type, ...)."""
        return cls(card_uid=0, template_guid=row[0], name=row[1] or "", card_type=row[2] or "",
                   cost=int(row[3] or 0), rarity=row[4] or "",
                   shards=shards_from_threshold(row[5]), subtype=row[6] or "",
                   attributes=int(row[7] or 0), location="", user_id=owner, state=0,
                   attack=0, defense=0)

    @classmethod
    def scene_card(cls, card_uid: int, card_type: str, location: str, shards: list[int],
                   name: str, owner: Any, subtype: str, attributes: int,
                   int_attrs: dict) -> "CardView":
        """A setup card a targeted encounter passive filters on (values as the producer computed them)."""
        return cls(card_uid=card_uid, card_type=card_type, location=location, user_id=owner,
                   state=0, attack=0, defense=0, name=name, cost=0, subtype=subtype,
                   shards=shards, attributes=attributes, int_attrs=int_attrs)

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

    # --- shapes ---------------------------------------------------------------------------

    def as_shape(self, shape: str) -> dict[str, Any]:
        """The mapping for ``shape`` (a key of :data:`SHAPES`), built from this view's fields."""
        keys = SHAPES[shape]
        out: dict[str, Any] = {}
        for key in keys:
            value = getattr(self, _FIELD_OF_KEY.get(key, key))
            if key in ("shards", "damaged_opponent_this_turn"):
                value = list(value)
            out[key] = value
        if shape == "projected" and self.thresholds is not None:
            out["thresholds"] = list(self.thresholds)
        if shape == "scene_card" and self.src_owner_id is None:
            del out["src_owner_id"]
        return out

    def as_mapping(self) -> dict[str, Any]:
        """The static card filters' mapping (the ``static`` shape)."""
        return self.as_shape("static")

    def as_condition_mapping(self) -> dict[str, Any]:
        """The condition layer's mapping: a champion entry, or a card."""
        return self.as_shape("condition_champion" if self.kind == "champion"
                             else "condition_card")


# Shape keys that are not field names. The template-candidate marker is a key the filters read.
_FIELD_OF_KEY = {"_card_template_candidate": "template_candidate"}


def _json_object(raw: Any) -> dict:
    """A serialized JSON object, or an empty dict when it is missing or not an object."""
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _parent_uid_of(serialized_buffs: Any) -> int:
    """The ``parent_uid`` a permanent-buffs record carries (0 when absent)."""
    try:
        value = json.loads(serialized_buffs or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0
    return int(value.get("parent_uid", 0) or 0) if isinstance(value, dict) else 0
