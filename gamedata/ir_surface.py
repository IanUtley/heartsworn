"""The Python surface for ability cards: plain names that build the generated mechanics classes.

A card reads like its rule, not like the Records:

    dazed = counter("Dazed")
    you_are_dazed = count(champions, having=dazed) >= 1
    remove_dazed = effect("remove a dazed counter from this.", remove_counter(this, dazed, amount=1))
    dazed_removal = ability(
        "At the end of your turn, if you are <b>Dazed</b>, remove a dazed counter from this.",
        on=turn_end,
        when=and_(player_controls_source, you_are_dazed),
        does=[remove_dazed],
    )

Names are looked up in the Records, not typed:

* ``counter("Dazed")`` finds the counter template by its name;
* ``library_target("…")`` finds a target record by its game text (markup ignored);
* ``effect(text, …)``, ``sacrifice(…)`` and ``ability(text, …)`` find the stored record with that game text.
  A stored record keeps its name and ID, and every field the card does not set. A text with no stored record
  gets a new ID, derived from the text, so it is stable.

A text that matches more than one record is refused; the card must then say which one (``id=``).

Everything here builds typed objects (gamedata/mechanics_types.py). gamedata/ir_python.py loads card text
and lets only these names through.
"""

from __future__ import annotations

import operator
import re
import uuid
from typing import Any

from gamedata import DEFAULT_RECORD_STORE as STORE
from gamedata import mechanics_types as T
from gamedata.mechanics_base import Typed, _typed_value
from gamedata.mechanics_fields import ir_name

ZERO_GUID = "00000000-0000-0000-0000-000000000000"
_NAMESPACE = uuid.UUID("6f0a9d2e-3c1b-4f7a-9d5e-2b8c4e1f0a73")
_TAGS = re.compile(r"<[^>]+>")


def _ref(guid: str) -> dict[str, Any]:
    return {"m_Guid": guid}


def _plain_text(text: Any) -> str:
    """Game text without markup, in lower case, for matching."""
    return re.sub(r"\s+", " ", _TAGS.sub("", str(text))).strip().lower()


class Counter:
    """A card counter, found in the Records by its name."""

    def __init__(self, guid: str, name: str) -> None:
        self.guid = guid
        self.name = name

    def reference(self) -> dict[str, Any]:
        return _ref(self.guid)

    def resource(self) -> dict[str, Any]:
        return {"_t": "Game.Shared.ResourceId", "m_Guid": self.guid}


def counter(name: str) -> Counter:
    """The card counter template whose Records name is ``name`` (``counter("Dazed")``)."""
    matches = [record for record in STORE.load("CardCounterTemplate")
               if str(record.field("m_Name", "")).lower() == name.lower()]
    if len(matches) != 1:
        raise ValueError(f"{len(matches)} counters named {name} in the Records")
    return Counter(matches[0].guid.lower(), name)


class Target:
    """A target a clause picks: this card, or a library target found by its game text."""

    def __init__(self, guid: str, text: str) -> None:
        self.guid = guid
        self.text = text


THIS = Target("190a4d8c-7c2c-10d0-6429-99c5aeb0791f", "this")


def library_target(text: str) -> Target:
    """The library target whose game text is ``text`` (markup ignored)."""
    wanted = _plain_text(text)
    found = [record for record in STORE.load("AbilityTargetTemplate")
             if _plain_text(record.field("m_GameText", "")) == wanted]
    if len(found) != 1:
        raise ValueError(f"{len(found)} targets read {text!r}")
    return Target(found[0].guid.lower(), text)


class Zone:
    """A collection of cards, by the name cards use for it."""

    def __init__(self, collection: str) -> None:
        self.collection = collection


class CardCount:
    """Cards in a collection that have a counter. Compared with ``>=`` (and the like), it is a condition."""

    _NAMES = {operator.ge: "GreaterThanOrEqual", operator.gt: "GreaterThan", operator.le: "LessThanOrEqual",
              operator.lt: "LessThan", operator.eq: "Equal"}

    def __init__(self, collection: str, counter_: Counter) -> None:
        self.collection = collection
        self.counter = counter_

    def compare(self, op: str, amount: int) -> Typed:
        return T.RequiresCardsControlled(
            card_collection=self.collection,
            card_filter=T.HasCountersValue(amount=1, comparison_op="GreaterThanOrEqual",
                                           counter_type=self.counter.resource()),
            required_quantity=int(amount),
            comparison_op=self._NAMES[op],
        )


def count(zone: Zone, *, having: Counter) -> CardCount:
    return CardCount(zone.collection, having)


class CounterValue:
    """The number of a counter on this card: a variable an effect reads."""

    def __init__(self, counter_: Counter) -> None:
        self.counter = counter_
        words = re.findall(r"[A-Za-z]+", counter_.name)
        self.name = "EqualToTheNumberOf" + "".join(word[:1].upper() + word[1:] for word in words) + "CountersOnThis"

    def variable(self) -> Typed:
        return T.CounterVariable(
            name=self.name,
            card_counter_template_id=self.counter.reference(),
            card_filter=T.IsAbilitySource(),
            collection_flags="Deck|Hand|Champions|Warzone|Discard|Void|CastSpells|Underground|Choosing",
            player_filter="MultiplePlayers",
        )


def counter_value(target: Target, counter_: Counter) -> CounterValue:
    """The number of ``counter_`` on this card (``target`` must be this card)."""
    if target is not THIS:
        raise ValueError("counter_value counts the counters on this card")
    return CounterValue(counter_)


class Body:
    """What an effect does, before it is named: its modifier, the variables it reads, and its target."""

    def __init__(self, modifier: Typed, variables: list[Typed], target: Target) -> None:
        self.modifier = modifier
        self.variables = variables
        self.target = target


def damage(target: Target, *, amount: CounterValue) -> Body:
    """Deal damage, equal to a counter value, to ``target``."""
    return Body(
        modifier=T.DamageModifier(input_value=T.EffectInputVariable(input_variable_name=amount.name)),
        variables=[amount.variable()],
        target=target,
    )


def remove_counter(target: Target, counter_: Counter, *, amount: int = 1) -> Body:
    """Remove ``amount`` of a counter from ``target``."""
    return Body(
        modifier=T.CounterModifier(
            input_value=T.EffectInputVariable(input_variable_name="A"),
            card_counter_template_id=counter_.reference(),
            operation="Remove",
        ),
        variables=[T.AbilityConstant(name="A", default_value=int(amount))],
        target=target,
    )


def _single_record(store_class: str, text: str, id_: str | None) -> Any:
    found = [record for record in STORE.load(store_class) if str(record.field("m_GameText", "")) == text]
    if id_ is None and len(found) > 1:
        raise ValueError(f"{len(found)} records read {text!r}: give id= to choose one")
    return found[0] if found and id_ is None else None


def _derived_name(text: str) -> str:
    words = re.findall(r"[A-Za-z]+", _TAGS.sub(" ", text))
    return "".join(word[:1].upper() + word[1:] for word in words)


def _stable_guid(kind: str, text: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{kind}:{text}"))


def _fill(obj: Typed, record: Any, values: dict[str, Any]) -> Typed:
    """Give an object its stored record's order and fields. The card's own values (by record name) win;
    every other stored field is kept as stored."""
    if record is not None:
        stored = record.to_dict(include_metadata=True)
        stored.pop("_v", None)
        order = [key for key in stored if key != "_t"]
        for key in order:
            if key not in values:
                setattr(obj, key, _typed_value(stored[key]))
        obj._record_order = order
        obj._stored_type = record.type_name
    for key, value in values.items():
        setattr(obj, key, value)
        if getattr(obj, "_record_order", None) is not None and key not in obj._record_order:
            obj._record_order.append(key)
    return obj


def effect(text: str, body: Body, *, id: str | None = None) -> Typed:
    """The effect template named by its rule text. The stored record with that text is reused."""
    record = _single_record("AbilityEffectTemplate", text, id)
    guid = (id or (record.guid.lower() if record else _stable_guid("effect", text))).lower()
    name = str(record.field("m_Name", "")) if record else _derived_name(text)
    values = {"m_Name": name, "m_TemplateId": _ref(guid), "m_GameText": text, "m_Modifier": body.modifier}
    obj = T.CardModifierAbilityEffectTemplate(name=name, template_id=_ref(guid), game_text=text,
                                              modifier=body.modifier)
    obj = _fill(obj, record, values)
    obj._surface_variables = body.variables
    obj._surface_target = body.target
    return obj


def sacrifice(target: Target) -> Typed:
    """Sacrifice each card ``target`` picks. The stored "sacrifice a troop." effect is reused."""
    text = "sacrifice a troop."
    record = _single_record("AbilityEffectTemplate", text, None)
    if record is None:
        raise ValueError(f"no stored effect reads {text!r}")
    name = str(record.field("m_Name", ""))
    values = {"m_Name": name, "m_TemplateId": _ref(record.guid.lower()), "m_GameText": text}
    obj = T.SacrificeCardAbilityEffectTemplate(name=name, template_id=_ref(record.guid.lower()), game_text=text)
    obj = _fill(obj, record, values)
    obj._surface_variables = []
    obj._surface_target = target
    return obj


def ability(text: str, *, on: dict[str, str], when: Typed, does: list[Typed], id: str | None = None,
            zones: str = "Champions|Warzone") -> Typed:
    """The ability named by its rule text: its trigger, its condition, and the effects it runs in order.
    Each effect's clause picks that effect's target."""
    from gamedata.ir_python import _clause
    record = _single_record("AbilityTemplate", text, id)
    guid = (id or (record.guid.lower() if record else _stable_guid("ability", text))).lower()
    name = str(record.field("m_Name", "")) if record else _derived_name(text)
    variables: list[Typed] = []
    targets: list[dict[str, str]] = []
    clauses: list[dict[str, Any]] = []
    for index, item in enumerate(does):
        for variable in item._surface_variables:
            if variable.m_Name not in [existing.m_Name for existing in variables]:
                variables.append(variable)
        targets.append(_ref(item._surface_target.guid))
        clauses.append(_clause(effect_template_id=item, target_template_index=index,
                               effect_instance_id=index, effect_group_id=index + 1))
    values = {
        "m_AbilityTemplateId": _ref(guid), "m_Name": name, "m_GameText": text,
        "m_TriggerEventType": dict(on), "m_TriggerCondition": when,
        "m_TriggerCollectionFlags": zones, "m_Variables": variables,
        "m_AbilityTargetTemplateIds": targets, "m_AbilityEffectList": clauses,
    }
    obj = T.AbilityTemplate(**{ir_name(key): value for key, value in values.items()})
    if record is None:
        # A new ability: the boilerplate the Records give an ability with no special rules.
        obj.ignores_chain = 1
        obj.serialized_tac = {"data": "AgAAAAAA"}
        for field in ("m_DiscardTarget", "m_ExhaustTarget", "m_PutIntoDeckTarget", "m_PutIntoDeckTarget2",
                      "m_PutIntoHandTarget", "m_RevealTarget", "m_SacrificeTarget",
                      "m_ShuffleIntoDeckTarget", "m_VoidTarget"):
            setattr(obj, field, _ref(ZERO_GUID))
        obj.m_AICustomAbilityEvaluator = []
        obj._record_order = [*T.AbilityTemplate.fields, "m_AICustomAbilityEvaluator"]
        return obj
    return _fill(obj, record, values)


def card_enters(*, from_zone: Zone, to_zone: Zone) -> Typed:
    """A card moves from one collection to another (a trigger condition)."""
    return T.TriggerCardEnteredZone(source_collection=from_zone.collection,
                                    destination_collection=to_zone.collection)


def card_matches(card_filter: Typed) -> Typed:
    """The card that moved matches a filter (a trigger condition)."""
    return T.TriggerCardMatchesFilter(card_filter=card_filter)


def troop_with(attribute: str) -> Typed:
    """A troop with an integer attribute of at least 1 (``troop_with("Soulcursed")``)."""
    return T.AndCardFilter(target_filters=[
        T.IsTroop(),
        T.IntAttrFilter(attribute=attribute, comparison_op="GreaterThanOrEqual", value=1),
    ])


def and_(*conditions: Typed) -> Typed:
    return T.AndTriggerCondition(conditions=list(conditions))


# Names a card can use: constants and factories.
CONSTANTS: dict[str, Any] = {
    "champions": Zone("Champions"),
    "warzone": Zone("Warzone"),
    "discard": Zone("Discard"),
    "this": THIS,
    "turn_end": {"m_InternalType": "Game.Shared.Mechanics.TurnEndedEvent"},
    "turn_start": {"m_InternalType": "Game.Shared.Mechanics.TurnStartedEvent"},
    "card_entered_zone": {"m_InternalType": "Game.Shared.Mechanics.CardEnteredZoneEvent"},
    "player_controls_source": T.TriggerPlayerControlsAbilitySource(),
}
FACTORIES: dict[str, Any] = {
    "counter": counter,
    "count": count,
    "counter_value": counter_value,
    "damage": damage,
    "remove_counter": remove_counter,
    "library_target": library_target,
    "effect": effect,
    "sacrifice": sacrifice,
    "ability": ability,
    "card_enters": card_enters,
    "card_matches": card_matches,
    "troop_with": troop_with,
    "and_": and_,
}
