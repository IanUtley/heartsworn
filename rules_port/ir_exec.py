"""Execute an ability's IR against a battle.

The ability is read from its stored Records object, as typed objects from
``gamedata.mechanics_objects``. The interpreter evaluates the trigger and its condition, binds the
ability's variables, resolves each clause's targets, and runs the effects in order. Each effect
runs through the live primitive that applies it (``deal_damage``, ``change_counter``), so the battle
state changes as it does on the live path.

Card filters are evaluated over the battle's cards: champions (from the battle state's champion map)
and the session's cards in ``game_cards``. A construct the interpreter does not handle raises
``NotImplementedError``: an ability it cannot run is reported, never half-run.
"""

from __future__ import annotations

import functools
import json
import os
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from gamedata import DEFAULT_RECORD_STORE as STORE
from gamedata.mechanics import CLASSES
from gamedata.mechanics_fields import current_fields
from gamedata.mechanics_objects import MechanicsObject, from_record
from gamedata.records import RecordObject

# A CounterModifier's operation, as the live counter primitive names it.
COUNTER_OPERATIONS = {"Add": "add", "Remove": "remove", "Set": "set"}

_COMPARISONS = {
    "GreaterThanOrEqual": lambda a, b: a >= b,
    "GreaterThan": lambda a, b: a > b,
    "LessThanOrEqual": lambda a, b: a <= b,
    "LessThan": lambda a, b: a < b,
    "Equal": lambda a, b: a == b,
}


def _plain(value: Any) -> Any:
    if isinstance(value, RecordObject):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


# Card files loaded for the interpreter: (store class, GUID) -> (type name, plain Records value). They
# take precedence over the Records, so a card's own root and effect templates run from the card.
_CARD_RECORDS: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}

# The GUID field each store class is keyed by, and the store class a card object belongs to.
_KEY_FIELD = {"AbilityTemplate": "m_AbilityTemplateId", "AbilityEffectTemplate": "m_TemplateId",
              "AbilityTargetTemplate": "m_TemplateId"}


def register_card(names: dict[str, Any]) -> None:
    """Make a loaded card's root and effect and target templates the ones the interpreter runs.

    ``names`` is what ``ir_cards.load.load_card`` returns: the card's typed objects by name."""
    from gamedata.mechanics_base import Typed
    for value in names.values():
        if not isinstance(value, Typed):
            continue
        store_class = _store_class_of(value)
        if store_class is None:
            continue
        key_value = getattr(value, _KEY_FIELD[store_class], None)
        guid = _guid(key_value)
        if guid:
            _CARD_RECORDS[(store_class, guid)] = (value.type_name, value.to_record())


def clear_cards() -> None:
    _CARD_RECORDS.clear()


def _store_class_of(value: Any) -> str | None:
    """Which Records class a typed card object is stored as, or None when it is not one of them."""
    name = _short(value.type_name)
    if name == "AbilityTemplate":
        return "AbilityTemplate"
    if name.endswith("EffectTemplate"):
        return "AbilityEffectTemplate"
    if name.endswith("TargetTemplate"):
        return "AbilityTargetTemplate"
    return None


@functools.lru_cache(maxsize=None)
def _stored_record(store_class: str, guid: str) -> tuple[str, dict[str, Any]]:
    for record in STORE.load(store_class):
        if record.guid.lower() == guid:
            return record.type_name, _plain(record.to_dict(include_metadata=True))
    raise KeyError(f"no {store_class} {guid}")


def _stored(store_class: str, guid: str) -> tuple[str, dict[str, Any]]:
    """A template's class and plain value (with the ``_t`` and ``_v`` the Records keep): a loaded card's
    own template when one is registered, else the Records'."""
    guid = str(guid).lower()
    return _CARD_RECORDS.get((store_class, guid)) or _stored_record(store_class, guid)


def _load(store_class: str, guid: str) -> MechanicsObject:
    return from_record(_stored(store_class, guid)[1])


def _short(type_name: Any) -> str:
    return str(type_name or "").rsplit(".", 1)[-1]


def _guid(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("m_Guid") or "").lower()
    return ""


def _field(obj: Any, name: str) -> Any:
    """An attribute of a typed object, or a key of a plain one."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _candidates(declared: str, version_table: list[dict[str, int]] | None) -> list[str]:
    """The classes in a stored type table that derive from a field's declared type."""
    found = []
    for entry in version_table or ():
        for name in entry:
            chain = []
            current = name
            while current in CLASSES and current not in chain:
                chain.append(current)
                current = CLASSES[current]["base"]
            if declared in chain and name != declared and name not in found:
                found.append(name)
    return found


def _typed(value: Any, declared: str, table: list[dict[str, int]] | None = None) -> Any:
    """A nested value as a typed object. A tagged object is read as stored. An untagged one takes
    the candidate class (from its declared field type) whose fields cover its keys, looking in its
    own type table and then the enclosing object's. Anything else is returned as it is."""
    if isinstance(value, MechanicsObject) or not isinstance(value, dict):
        return value
    if isinstance(value.get("_t"), str):
        return from_record(value)
    keys = {key for key in value if key not in ("_v", "_meta")}
    for name in _candidates(declared, value.get("_v")) + _candidates(declared, table):
        if keys <= set(current_fields(name)):
            return from_record({**value, "_t": f"Game.Shared.Mechanics.{name}"})
    return value


@dataclass
class Card:
    """One card in the battle: where it is, whose it is, and its counters (guid -> count)."""

    uid: int
    owner: int
    zone: str
    is_champion: bool
    card_type: str
    counters: dict[str, int]


@dataclass
class Scope:
    """What a run reads and changes: the battle context, the source card and its owner, the
    ability's variables (bound before its first effect), and the enclosing type table."""

    context: Any
    source_uid: int
    owner_uid: int
    variables: dict[str, int] = field(default_factory=dict)
    table: list[dict[str, int]] | None = None
    event: dict[str, Any] = field(default_factory=dict)   # player, card, from, to


def _champion_owners(bstate: dict[str, Any]) -> dict[int, int]:
    """Champion UID -> owner id, from the battle state's champion map (owner id -> champion UID)."""
    return {int(uid): int(owner) for owner, uid in (bstate.get("champ_map") or {}).items()}


def _battle_cards(scope: Scope) -> list[Card]:
    """Every card of the battle: the champions, then the session's other cards."""
    context = scope.context
    bstate = context.bstate
    cards: list[Card] = []
    counters = bstate.get("champion_counters") or {}
    for uid, owner in _champion_owners(bstate).items():
        cards.append(Card(uid=uid, owner=owner, zone="champions", is_champion=True,
                          card_type="Champion",
                          counters={k: int(v) for k, v in (counters.get(str(uid)) or {}).items()}))
    rows = context.db.execute(
        "SELECT card_uid, user_id, location, is_champion, card_type, permanent_buffs "
        "FROM game_cards WHERE session_id=?", (context.session.session_id,)).fetchall()
    for card_uid, user_id, location, is_champion, card_type, buffs_json in rows:
        buffs = json.loads(buffs_json or "{}")
        names = buffs.get("counter_guids") or {}
        values = buffs.get("counters") or {}
        cards.append(Card(uid=int(card_uid), owner=int(user_id),
                          zone=str(location or "").lower(), is_champion=bool(is_champion),
                          card_type=str(card_type or ""),
                          counters={str(names[name]).lower(): int(count)
                                    for name, count in values.items() if name in names}))
    return cards


def _filter_holds(card_filter: Any, card: Card, scope: Scope) -> bool:
    """Whether a card passes a card filter (a typed filter, or a plain HasCountersValue)."""
    if card_filter is None:
        return True
    if isinstance(card_filter, dict) and not isinstance(card_filter.get("_t"), str) and \
            "m_CounterType" in card_filter:
        return _has_counters(card, card_filter)
    typed = _typed(card_filter, "CardFilter", scope.table)
    cls = _short(typed.type_name) if isinstance(typed, MechanicsObject) else ""
    if cls == "AndCardFilter":
        return all(_filter_holds(part, card, scope) for part in typed.m_TargetFilters)
    if cls == "OrCardFilter":
        return any(_filter_holds(part, card, scope) for part in typed.m_TargetFilters)
    if cls == "IsControlledBy":
        if typed.m_TestAgainstActivePlayer:
            raise NotImplementedError("controlled by the active player")
        return card.owner == int(scope.owner_uid)
    if cls == "IsNotControlledBy":
        if typed.m_FailUncontrolledCards:
            raise NotImplementedError("uncontrolled cards that fail the filter")
        return card.owner != int(scope.owner_uid)
    if cls == "HasCountersValue":
        return _has_counters(card, typed.__dict__)
    if cls == "IntAttrFilter":
        from rules_port.damage_effects import _int_attr_values
        values = _int_attr_values(scope.context, card.uid)
        compare = _COMPARISONS[typed.m_ComparisonOp]
        return compare(int(values.get(typed.m_Attribute, 0)), int(typed.m_Value))
    if cls == "IsTroop":
        return card.card_type == "Troop" and not card.is_champion
    if cls == "IsHero":
        return card.is_champion
    if cls == "IsResource":
        return card.card_type == "Resource"
    if cls == "NotCardFilter":
        return not _filter_holds(typed.m_TargetFilter, card, scope)
    if cls == "IsType":
        return card.card_type in {part.strip() for part in str(typed.m_CardType).split("|")}
    if cls == "InZone":
        zones = {part.strip().lower() for part in typed.m_Collection.split("|")}
        return card.zone in zones
    if cls == "IsAbilitySource":
        return card.uid == scope.source_uid
    raise NotImplementedError(f"card filter {cls or type(typed).__name__}")


def _has_counters(card: Card, spec: dict[str, Any]) -> bool:
    guid = _guid(spec.get("m_CounterType")) or str(spec.get("m_CounterType") or "").lower()
    compare = _COMPARISONS[spec["m_ComparisonOp"]]
    return compare(card.counters.get(guid, 0), int(spec.get("m_Amount", 1)))


def _zones_of(flags: Any) -> set[str]:
    """The battle zones an ECardCollections flag string names ("Warzone|Champions" -> {"warzone", ...})."""
    return {part.strip().lower() for part in str(flags or "").split("|") if part.strip() and part.strip() != "None"}


def _condition_holds(condition: Any, scope: Scope) -> bool:
    """A trigger condition: its parts all hold (``AndTriggerCondition``), and each part is checked."""
    if condition is None:
        return True
    condition = _typed(condition, "TriggerCondition", scope.table)
    if not isinstance(condition, MechanicsObject):
        raise NotImplementedError("untyped trigger condition")
    cls = _short(condition.type_name)
    if cls == "AndTriggerCondition":
        return all(_condition_holds(part, scope) for part in condition.m_Conditions)
    if cls in ("TriggerCondition", "TriggerSource"):
        return True
    if cls == "TriggerCardIsAbilitySource":
        # The card that moved is the ability's own source.
        return scope.event.get("card") is not None and int(scope.event["card"]) == scope.source_uid
    if cls == "TriggerPlayerControlsAbilitySource":
        # The event's player controls the ability's source.
        return int(scope.event.get("player", -1)) == int(scope.owner_uid)
    if cls == "TriggerCardEnteredZone":
        # A collection of "None" places no restriction on that side of the move.
        for wanted, actual in ((condition.m_SourceCollection, scope.event.get("from")),
                               (condition.m_DestinationCollection, scope.event.get("to"))):
            if str(wanted).lower() not in ("none", "") and str(wanted).lower() != str(actual or "").lower():
                return False
        mine = int(scope.event.get("player", -1)) == int(scope.owner_uid)
        return (not condition.m_Your or mine) and (not condition.m_Opposing or not mine)
    if cls == "TriggerCardMatchesFilter":
        card = _card_by_uid(scope, scope.event.get("card"))
        return card is not None and _filter_holds(condition.m_CardFilter, card, scope)
    if getattr(condition, "m_CardCollection", None) is not None:
        return _count_holds(condition, scope)
    raise NotImplementedError(f"trigger condition {cls}")


def _ability_condition_holds(condition: Any, scope: Scope) -> bool:
    """An ability's own condition (``m_AbilityCondition``): none, or the controller's thresholds."""
    if condition is None:
        return True
    typed = _typed(condition, "AbilityCondition", scope.table)
    if not isinstance(typed, MechanicsObject) or \
            _short(typed.type_name) != "AbilityControllerHasThresholdAbilityCondition":
        raise NotImplementedError(f"ability condition {_short(getattr(typed, 'type_name', ''))}")
    import game_engine
    from rules_port.conditions import _participant_thresholds
    flag = int(game_engine.SHARD_TO_FLAG.get(str(typed.m_ColorFlags).lower(), 0) or 0)
    thresholds = _participant_thresholds(scope.context.bstate, int(scope.owner_uid))
    available = int(thresholds.get(flag, thresholds.get(str(flag), 0)) or 0)
    return _COMPARISONS[typed.m_ComparisonOp](available, int(typed.m_RequiredQuantity))


def _card_by_uid(scope: Scope, uid: Any) -> Card | None:
    if uid is None:
        return None
    return next((card for card in _battle_cards(scope) if card.uid == int(uid)), None)


def _count_holds(condition: Any, scope: Scope) -> bool:
    """A count of the cards in a collection that pass the condition's filter, compared with the
    required quantity. ``Self`` is the ability's owner."""
    if condition.m_PlayerFilter != "Self":
        raise NotImplementedError(f"count for {condition.m_PlayerFilter}")
    collection = str(condition.m_CardCollection).lower()
    matching = [card for card in _battle_cards(scope)
                if card.owner == int(scope.owner_uid)
                and (card.zone == collection or (collection == "champions" and card.is_champion))
                and _filter_holds(condition.m_CardFilter, card, scope)]
    return _COMPARISONS[condition.m_ComparisonOp](len(matching), int(condition.m_RequiredQuantity))


COUNTER_VARIABLE_PLAYERS = ("Self", "MultipleOpponents", "MultiplePlayers")


def _counter_variable(variable: Any, counter: str | None, scope: Scope) -> int:
    """C# CounterVariable.GetValue (``counter`` given) and CardCountAbilityVariable.GetValue (``counter``
    None): over the cards that pass the player filter (the ability's controller, its opponents, or
    everyone), sit in one of the collection flags' zones, and pass the card filter, the counter each card
    carries, or one per card. The totals are summed, or the highest is taken when the variable says so."""
    player_filter = str(_field(variable, "m_PlayerFilter") or "Self")
    if player_filter not in COUNTER_VARIABLE_PLAYERS:
        raise NotImplementedError(f"counter variable for {player_filter}")
    zones = _zones_of(_field(variable, "m_CollectionFlags"))
    if not zones:
        raise NotImplementedError("counter variable without collection flags")
    values = []
    for card in _battle_cards(scope):
        if player_filter == "Self" and card.owner != int(scope.owner_uid):
            continue
        if player_filter == "MultipleOpponents" and card.owner == int(scope.owner_uid):
            continue
        if card.zone not in zones or not _filter_holds(_field(variable, "m_CardFilter"), card, scope):
            continue
        values.append(card.counters.get(counter, 0) if counter else 1)
    if _field(variable, "m_UseHighestValue"):
        return max(values, default=0)
    return sum(values)


# The ability variables the interpreter binds (coverage refuses an ability with any other kind).
SUPPORTED_VARIABLES = {"AbilityConstant", "CounterVariable", "CardCountAbilityVariable"}


def _bind_variables(root: MechanicsObject, scope: Scope) -> None:
    """Each ability variable: a constant's default, or the total of a counter or card-count variable."""
    for variable in root.m_Variables:
        kind = _short(variable.type_name)
        if kind == "CounterVariable":
            counter = _guid(_field(variable, "m_CardCounterTemplateId"))
            scope.variables[variable.m_Name] = _counter_variable(variable, counter, scope)
        elif kind == "CardCountAbilityVariable":
            scope.variables[variable.m_Name] = _counter_variable(variable, None, scope)
        elif kind == "AbilityConstant":
            scope.variables[variable.m_Name] = int(variable.m_DefaultValue or 0)
        else:
            raise NotImplementedError(f"variable {kind}")


def _input_value(modifier: MechanicsObject, scope: Scope) -> int:
    """A modifier's number: an input variable's bound value, or a constant."""
    value = modifier.m_InputValue
    kind = _short(value.type_name)
    if kind == "EffectConstant":
        return int(value.m_Value)
    if kind != "EffectInputVariable":
        raise NotImplementedError(f"input value {kind}")
    name = value.m_InputVariableName
    if name not in scope.variables:
        raise KeyError(f"variable {name} is not bound")
    return scope.variables[name]


def _targets(root: MechanicsObject, index: int, scope: Scope) -> list[int]:
    """The cards a clause's target index picks: the ability's source, or every card an automatic
    target's filter passes."""
    template = _load("AbilityTargetTemplate", _guid(root.m_AbilityTargetTemplateIds[index]))
    kind = _short(template.type_name)
    if kind == "AbilitySourceCardTargetTemplate":
        return [scope.source_uid]
    if kind == "AbilityTargetTemplate" and template.m_IsAutoTarget:
        zones = {part.strip().lower() for part in str(template.m_CollectionFlags or "").split("|") if part.strip()}
        return [card.uid for card in _battle_cards(scope)
                if (not zones or card.zone in zones)
                and _filter_holds(template.m_CardFilter, card, scope)]
    raise NotImplementedError(f"target {kind}")


def _run_clause(root: MechanicsObject, clause: Any, scope: Scope) -> None:
    effect_guid = _guid(_field(clause, "m_EffectTemplateId"))
    effect_type, effect_raw = _stored("AbilityEffectTemplate", effect_guid)
    if _short(effect_type) == "SacrificeCardAbilityEffectTemplate":
        from rules_port.death_effects import kill_troop
        for target in _targets(root, int(_field(clause, "m_TargetTemplateIndex") or 0), scope):
            kill_troop(scope.context, target, cause="sacrifice")
        return
    if _short(effect_type) != "CardModifierAbilityEffectTemplate":
        raise NotImplementedError(f"effect {_short(effect_type)}")
    modifier = _typed(effect_raw["m_Modifier"], "Modifier", effect_raw.get("_v"))
    if not isinstance(modifier, MechanicsObject):
        raise NotImplementedError(f"effect {effect_guid} has an untyped modifier")
    kind = _short(modifier.type_name)
    for target in _targets(root, int(_field(clause, "m_TargetTemplateIndex") or 0), scope):
        if kind == "DamageModifier":
            from rules_port.damage_effects import deal_damage
            deal_damage(scope.context, target, _input_value(modifier, scope))
        elif kind == "CounterModifier":
            from rules_port.bom_fields import counter_template_name
            from rules_port.counter_effects import change_counter
            if modifier.m_RemoveAllCounters or modifier.m_Operation not in COUNTER_OPERATIONS:
                raise NotImplementedError(f"counter operation {modifier.m_Operation}")
            counter = _guid(modifier.m_CardCounterTemplateId)
            amount = _input_value(modifier, scope)
            if modifier.m_RemoveHalfRoundedUp:
                # C# CounterModifier.Apply: the amount is half the target's count, rounded up.
                card = _card_by_uid(scope, target)
                current = card.counters.get(counter.lower(), 0) if card else 0
                amount = (current + 1) // 2
            change_counter(scope.context, target, counter_template_name(counter).lower() or "counter",
                           counter, amount, COUNTER_OPERATIONS[modifier.m_Operation])
        else:
            raise NotImplementedError(f"modifier {kind}")


@dataclass
class BattleView:
    """The part of a battle context the interpreter reads, for callers that hold only the database,
    the session and the battle state."""

    db: Any
    session_id: int
    bstate: dict[str, Any]
    handler: Any = None

    @property
    def session(self) -> Any:
        return SimpleNamespace(session_id=self.session_id)


# Abilities whose static effects the live paths take from the interpreter. Empty by default: the
# live engine computes those effects itself until an ability is opted in.
ENABLED: set[str] = set()

# The engine switch. When on, the live chain resolver (rules_port/resolution.py resolve_port_trigger) runs
# each ability the interpreter covers in place of the native resolver; the others keep the native path.
# HEX_IR_ENGINE=1 turns it on at start-up, and set_engine() changes it while the server runs.
ENGINE_ON: bool = os.environ.get("HEX_IR_ENGINE", "") == "1"


def set_engine(on: bool) -> None:
    """Turn the interpreter engine on or off for the abilities resolved from now on."""
    global ENGINE_ON
    ENGINE_ON = bool(on)


def engine_runs(guid: str, source_uid: Any, owner_uid: Any) -> bool:
    """Whether the interpreter resolves this ability in place of the native resolver: the engine is on,
    the ability has a source card and an owner, and no construct of it is uncovered."""
    return ENGINE_ON and source_uid is not None and owner_uid is not None and not coverage(guid)


def live_event(event_type: str, owner_uid: int, data: dict[str, Any], trigger_source_uid: Any) -> dict[str, Any]:
    """The interpreter's event for a live trigger: its player, and for a zone move the card and zones
    the live dispatch recorded (``event_source_collection`` and ``event_destination_collection``)."""
    event: dict[str, Any] = {"player": owner_uid}
    if event_type == "CardEnteredZoneEvent":
        event["card"] = trigger_source_uid
        event["from"] = data.get("event_source_collection")
        event["to"] = data.get("event_destination_collection")
    return event


def _champion_scopes(context: Any) -> list[Scope]:
    """A scope for each champion in the battle, owned by its player."""
    return [Scope(context=context, source_uid=int(champion), owner_uid=int(owner))
            for owner, champion in (context.bstate.get("champ_map") or {}).items()]


def _clause_condition_holds(clause: Any, scope: Scope) -> bool:
    """An effect's own condition (``m_ConditionId``): none, or a count over the owner's cards."""
    guid = _guid(_field(clause, "m_ConditionId"))
    if not guid or guid == "00000000-0000-0000-0000-000000000000":
        return True
    _cls, raw = _stored("AbilityEffectConditionTemplate", guid)
    condition = _typed(raw.get("m_Condition"), "AbilityEffectCondition", raw.get("_v"))
    if not isinstance(condition, MechanicsObject) or not hasattr(condition, "m_CardCollection"):
        raise NotImplementedError(f"effect condition {guid}")
    return _count_holds(condition, scope)


def static_int_set(context: Any, target: int, attribute: str) -> int | None:
    """The value an enabled ability's static ``IntAttrModifier`` (operation Set) gives a card's
    ``attribute``, or None when no enabled ability sets it on that card."""
    value = None
    for scope in _champion_scopes(context):
        for guid in ENABLED:
            root = _load("AbilityTemplate", guid)
            for clause in root.m_AbilityEffectList:
                effect_type, effect_raw = _stored("AbilityEffectTemplate", _guid(_field(clause, "m_EffectTemplateId")))
                if _short(effect_type) != "CardModifierAbilityEffectTemplate":
                    continue
                modifier = _typed(effect_raw["m_Modifier"], "Modifier", effect_raw.get("_v"))
                if (not isinstance(modifier, MechanicsObject) or _short(modifier.type_name) != "IntAttrModifier"
                        or modifier.m_Attribute != attribute or modifier.m_Operation != "Set"):
                    continue
                index = int(_field(clause, "m_TargetTemplateIndex") or 0)
                if (_clause_condition_holds(clause, scope)
                        and int(target) in _targets(root, index, scope)):
                    value = int(modifier.m_Value)
    return value


def static_cost_increase(context: Any, card_uid: int, owner: int) -> int:
    """The cost an enabled ability's static ``CardCostModifier`` adds to a card, for its owner."""
    total = 0
    for scope in _champion_scopes(context):
        if scope.owner_uid != int(owner):
            continue
        for guid in ENABLED:
            root = _load("AbilityTemplate", guid)
            _bind_variables(root, scope)
            for clause in root.m_AbilityEffectList:
                effect_type, effect_raw = _stored("AbilityEffectTemplate", _guid(_field(clause, "m_EffectTemplateId")))
                if _short(effect_type) != "CardModifierAbilityEffectTemplate":
                    continue
                modifier = _typed(effect_raw["m_Modifier"], "Modifier", effect_raw.get("_v"))
                if not isinstance(modifier, MechanicsObject) or _short(modifier.type_name) != "CardCostModifier":
                    continue
                index = int(_field(clause, "m_TargetTemplateIndex") or 0)
                if (_clause_condition_holds(clause, scope)
                        and int(card_uid) in _targets(root, index, scope)):
                    total += _input_value(modifier, scope)
    return total


def run_ability(guid: str, event_type: str, *, context: Any, source_uid: int,
                owner_uid: int, event: dict[str, Any] | None = None) -> bool:
    """Run one ability for an event. Returns whether its trigger fired.

    ``event`` names what happened: ``player`` (whose turn or whose card), and for a zone move the
    ``card`` and its ``from`` and ``to`` zones. A turn event needs only the ``player``."""
    # Refuse an ability with any construct the interpreter lacks before anything runs, so no ability
    # is ever half-run.
    missing = coverage(guid)
    if missing:
        raise NotImplementedError("; ".join(missing))
    root = _load("AbilityTemplate", guid)
    if _field(root, "m_Threshold"):
        raise NotImplementedError("ability threshold requirement")
    trigger = _field(root, "m_TriggerEventType")
    if _short(_field(trigger, "m_InternalType")) != event_type:
        return False
    event = {"player": owner_uid, **(event or {})}
    scope = Scope(context=context, source_uid=int(source_uid), owner_uid=int(owner_uid),
                  table=root.version_table, event=event)
    # The trigger fires only while its source is in one of the zones its collection flags name (C#
    # AbilityTemplate.TriggerCollectionFlags).
    zones = _zones_of(_field(root, "m_TriggerCollectionFlags"))
    source = _card_by_uid(scope, source_uid)
    if zones and (source is None or source.zone not in zones):
        return False
    if not _ability_condition_holds(root.m_AbilityCondition, scope):
        return False
    if not _condition_holds(root.m_TriggerCondition, scope):
        return False
    _bind_variables(root, scope)
    # Damage and other effects are dealt by the ability's source, as the live resolver records it.
    bstate = context.bstate
    previous = bstate.get("resolving_source_uid")
    bstate["resolving_source_uid"] = int(source_uid)
    try:
        for clause in root.m_AbilityEffectList:
            _run_clause(root, clause, scope)
    finally:
        if previous is None:
            bstate.pop("resolving_source_uid", None)
        else:
            bstate["resolving_source_uid"] = previous
    return True


# What the interpreter runs. A construct outside these sets is uncovered: its ability raises
# NotImplementedError when run, and the coverage report lists it.
SUPPORTED_TRIGGERS = {"TurnEndedEvent", "TurnStartedEvent", "CardEnteredZoneEvent"}
SUPPORTED_CONDITIONS = {
    "AndTriggerCondition", "TriggerCondition", "TriggerSource", "TriggerPlayerControlsAbilitySource",
    "TriggerCardEnteredZone", "TriggerCardMatchesFilter", "RequiresCardsControlled",
    "TriggerCardIsAbilitySource",
}
SUPPORTED_FILTERS = {
    "AndCardFilter", "OrCardFilter", "NotCardFilter", "IsControlledBy", "IsNotControlledBy",
    "HasCountersValue", "IsTroop", "IsHero", "IsResource", "IsType", "IntAttrFilter", "InZone",
    "IsAbilitySource",
}
SUPPORTED_MODIFIERS = {"DamageModifier", "CounterModifier"}
SUPPORTED_TARGETS = {"AbilitySourceCardTargetTemplate", "AbilityTargetTemplate"}


def _tagged_classes(value: Any, out: list[str]) -> list[str]:
    """Every tagged class in a stored value, in order."""
    if isinstance(value, dict):
        if isinstance(value.get("_t"), str):
            out.append(_short(value["_t"]))
        for item in value.values():
            _tagged_classes(item, out)
    elif isinstance(value, list):
        for item in value:
            _tagged_classes(item, out)
    return out


def coverage(guid: str) -> list[str]:
    """The constructs in an ability the interpreter does not run (empty when it runs them all)."""
    _cls, raw = _stored("AbilityTemplate", guid)
    missing: list[str] = []
    trigger = _short((raw.get("m_TriggerEventType") or {}).get("m_InternalType"))
    if trigger and trigger not in SUPPORTED_TRIGGERS:
        missing.append(f"trigger {trigger}")
    if raw.get("m_Threshold"):
        missing.append("ability threshold")
    ability_condition = _short((raw.get("m_AbilityCondition") or {}).get("_t"))
    if ability_condition and ability_condition != "AbilityControllerHasThresholdAbilityCondition":
        missing.append(f"ability condition {ability_condition}")
    for name in _tagged_classes(raw.get("m_TriggerCondition"), []):
        # A ResourceId is a value (a counter's identity), not a construct.
        if name not in SUPPORTED_CONDITIONS and name not in SUPPORTED_FILTERS and name != "ResourceId":
            missing.append(f"condition {name}")
    for template_guid in (_guid(item) for item in raw.get("m_AbilityTargetTemplateIds") or []):
        template_type, template = _stored("AbilityTargetTemplate", template_guid)
        kind = _short(template_type)
        if kind not in SUPPORTED_TARGETS:
            missing.append(f"target {kind}")
        if kind == "AbilityTargetTemplate" and not template.get("m_IsAutoTarget"):
            missing.append("chosen target")
        if template.get("m_IsRandomTarget"):
            missing.append("random target")
        for name in _tagged_classes(template.get("m_CardFilter"), []):
            if name not in SUPPORTED_FILTERS:
                missing.append(f"filter {name}")
    for variable in raw.get("m_Variables") or []:
        kind = _short((variable or {}).get("_t")) if isinstance(variable, dict) else ""
        if kind not in SUPPORTED_VARIABLES:
            missing.append(f"variable {kind or 'untyped'}")
            continue
        if kind == "AbilityConstant":
            continue
        player = str(variable.get("m_PlayerFilter") or "Self")
        if player not in COUNTER_VARIABLE_PLAYERS:
            missing.append(f"{kind} for {player}")
        if not _zones_of(variable.get("m_CollectionFlags")):
            missing.append(f"{kind} without collection flags")
        if kind == "CardCountAbilityVariable" and str(variable.get("m_OnlyIncludeDifferentRacesForFaction") or
                                                      "Unknown") != "Unknown":
            missing.append("card count of different races")
        for name in _tagged_classes(variable.get("m_CardFilter"), []):
            if name not in SUPPORTED_FILTERS:
                missing.append(f"filter {name}")
    for clause in raw.get("m_AbilityEffectList") or []:
        effect_type, effect = _stored("AbilityEffectTemplate", _guid(clause.get("m_EffectTemplateId")))
        kind = _short(effect_type)
        if kind == "SacrificeCardAbilityEffectTemplate":
            continue
        if kind != "CardModifierAbilityEffectTemplate":
            missing.append(f"effect {kind}")
            continue
        modifier = _short((effect.get("m_Modifier") or {}).get("_t"))
        if modifier not in SUPPORTED_MODIFIERS:
            missing.append(f"modifier {modifier or '(untagged)'}")
        elif modifier == "CounterModifier" and (
                (effect.get("m_Modifier") or {}).get("m_Operation") not in COUNTER_OPERATIONS
                or (effect.get("m_Modifier") or {}).get("m_RemoveAllCounters")):
            missing.append(f"counter operation {(effect.get('m_Modifier') or {}).get('m_Operation')}")
    return sorted(set(missing))
