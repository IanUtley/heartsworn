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
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Iterator

from gamedata import DEFAULT_RECORD_STORE as STORE
from gamedata.mechanics import CLASSES
from gamedata.mechanics_fields import current_fields
from gamedata.mechanics_objects import MechanicsObject, from_record
from gamedata.records import RecordObject
from rules_port import ir_exec_conditions as bridge
from rules_port.ir_modifiers import GROUP_G_MODIFIERS, modifier_gaps
from rules_port.ir_exec_zones import ZONE_EFFECTS, run_zone_effect, zone_effect_gaps
from rules_port import ir_variables
from rules_port.ir_event_effects import NATIVE_LEAF_EFFECTS, coverage_of as event_effect_coverage, run_native_leaf
from rules_port.ir_exec_costs import unsupported_costs

from . import ir_triggers

# A CounterModifier's operation, as the live counter primitive names it.
COUNTER_OPERATIONS = {"Add": "add", "Remove": "remove", "Set": "set"}

# C# EComparisons. An operator outside this table is refused by coverage.
_COMPARISONS = {
    "GreaterThanOrEqual": lambda a, b: a >= b,
    "GreaterThan": lambda a, b: a > b,
    "LessThanOrEqual": lambda a, b: a <= b,
    "LessThan": lambda a, b: a < b,
    "Equals": lambda a, b: a == b,
    "NotEqual": lambda a, b: a != b,
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
              "AbilityTargetTemplate": "m_TemplateId", "AbilityEffectConditionTemplate": "m_TemplateId"}


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
    if name == "AbilityEffectConditionTemplate":
        return "AbilityEffectConditionTemplate"
    return None


@functools.lru_cache(maxsize=None)
def _stored_record(store_class: str, guid: str) -> tuple[str, dict[str, Any]]:
    record = STORE.get(store_class, guid)   # the store's index: a lookup, not a scan of every record
    if record is None:
        raise KeyError(f"no {store_class} {guid}")
    return record.type_name, _plain(record.to_dict(include_metadata=True))


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
    # The card's current subtype line ("Human Cleric"); None when it is not known.
    subtype: str | None = None
    # The card's template name; None for a synthetic champion, whose name the battle does not carry.
    name: str | None = None
    # The card's position in its zone (the deck's top is the lowest).
    position: int = 0


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
    chosen: dict[int, int | None] = field(default_factory=dict)   # target index -> the card the player chose
    # The test each of the running clause's targets must pass (its RequiresTargetPassesFilterCondition), or None.
    gate: Any = None
    applied: dict[int, bool] = field(default_factory=dict)   # effect position -> whether it applied (C# applied_effects)


class NeedsChoice(Exception):
    """The ability reached a target the player chooses. Its continuation (JSON-serialisable) resumes it with
    ``resume_ability`` once the player answers; ``candidates`` are the cards the answer may name."""

    def __init__(self, continuation: dict[str, Any], candidates: list[int], optional: bool, revealed: bool = False):
        super().__init__(f"choice of target {continuation['target_index']} for {continuation['ability_guid']}")
        self.continuation = continuation
        self.candidates = candidates
        self.optional = optional
        # A choice among the cards a reveal showed (the client's revealed-card picker), not among the battle's cards.
        self.revealed = revealed


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
                          counters={k: int(v) for k, v in (counters.get(str(uid)) or {}).items()},
                          subtype=_champion_subtype(context, owner)))
    rows = context.db.execute(
        "SELECT card_uid, user_id, location, is_champion, card_type, permanent_buffs, template_guid, "
        "(SELECT name FROM card_templates WHERE guid = game_cards.template_guid), position "
        "FROM game_cards WHERE session_id=?", (context.session.session_id,)).fetchall()
    from pvp_db import db_template_subtype
    for card_uid, user_id, location, is_champion, card_type, buffs_json, template, template_name, position in rows:
        buffs = json.loads(buffs_json or "{}")
        names = buffs.get("counter_guids") or {}
        values = buffs.get("counters") or {}
        # The current subtype: a SubTypeModifier's value in the card's buffs, else the template's.
        subtype = buffs.get("subtype")
        if subtype is None:
            subtype = db_template_subtype(template, conn=context.db) if template else ""
        cards.append(Card(uid=int(card_uid), owner=int(user_id),
                          zone=str(location or "").lower(), is_champion=bool(is_champion),
                          card_type=str(card_type or ""),
                          counters={str(names[name]).lower(): int(count)
                                    for name, count in values.items() if name in names},
                          subtype=str(subtype or ""), name=str(template_name or ""),
                          position=int(position or 0)))
    return cards


def _champion_subtype(context: Any, owner: int) -> str | None:
    """A champion's subtype line from its template, by its controller: the AI's (owner 0) or the player's
    (the profile's id). None when the battle does not name that champion's template (a PvP opponent)."""
    handler = context.handler
    if int(owner) == 0:
        guid = getattr(handler, "_ai_champ_guid", None)
    else:
        profile = getattr(handler, "user_profile", None) or {}
        guid = (getattr(handler, "_player_champ_guid", None)
                if int(profile.get("id", -1) or -1) == int(owner) else None)
    if not guid:
        return None
    from rules_port.targeting import _champion_subtype as template_subtype
    return template_subtype(str(guid).lower())


def _name_contains(filter_: MechanicsObject, card: Card) -> bool:
    """C# NameContainsFilter.IsMatch: the template name matches the pattern, ignoring case (an empty pattern
    matches every card). The keyword and subtype inclusions are refused by coverage."""
    import re
    pattern = str(filter_.m_ContainsString or "")
    if not pattern:
        return True
    if card.name is None:
        raise NotImplementedError("the name of a champion")
    return re.search(pattern, card.name, re.IGNORECASE) is not None


def _is_subtype(wanted: str, card: Card) -> bool:
    """C# IsSubType.IsMatch: "*" matches any card; otherwise Card.HasSubType on its subtype line. A
    multi-word value is a substring match; a single word matches a whole word, ignoring case."""
    if not wanted:
        return False
    if wanted == "*":
        return True
    if card.subtype is None:
        raise NotImplementedError("subtype of a champion the battle does not name")
    current = card.subtype
    if not current:
        return False
    if " " in wanted:
        return wanted in current
    return wanted.lower() in {word for word in current.lower().split(" ")}


def _has_card_type(card: Card, wanted: str) -> bool:
    """Whether a card carries a card type. A card's type is a flag list ("Troop|Quick"), so a Quick troop is a troop."""
    return wanted in {part.strip() for part in str(card.card_type or "").split("|")}


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
        from rules_port.ir_exec_parity import int_attr_value
        actual = int_attr_value(scope.context, card.uid, scope.owner_uid, typed.m_Attribute)
        return _COMPARISONS[typed.m_ComparisonOp](actual, int(typed.m_Value))
    if cls == "IsTroop":
        return _has_card_type(card, "Troop") and not card.is_champion
    if cls == "IsHero":
        return card.is_champion
    if cls == "IsResource":
        return _has_card_type(card, "Resource")
    if cls == "NotCardFilter":
        return not _filter_holds(typed.m_TargetFilter, card, scope)
    if cls == "IsType":
        return card.card_type in {part.strip() for part in str(typed.m_CardType).split("|")}
    if cls == "InZone":
        zones = {part.strip().lower() for part in typed.m_Collection.split("|")}
        return card.zone in zones
    if cls == "TopNOfDeck":
        return True   # the selection is the candidate list's (_card_candidates)
    if cls == "NameContainsFilter":
        return _name_contains(typed, card)
    if cls == "IsAbilitySource":
        return card.uid == scope.source_uid
    if cls == "IsSubType":
        return _is_subtype(str(typed.m_SubType or ""), card)
    if bridge.filter_accepted(cls):
        return bridge.filter_holds(typed, card.uid, scope)
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
    if cls in bridge.BRIDGED_CONDITIONS:
        return bridge.condition_holds(condition, scope)
    if bridge.filter_accepted(cls):
        return bridge.trigger_card_holds(condition, scope)
    if cls in ir_triggers.CONDITIONS:
        return ir_triggers.condition_holds(cls, condition, scope, _condition_holds)
    if getattr(condition, "m_CardCollection", None) is not None:
        return _count_holds(condition, scope)
    raise NotImplementedError(f"trigger condition {cls}")


def _ability_condition_holds(condition: Any, scope: Scope) -> bool:
    """An ability's own condition (``m_AbilityCondition``): none, or the controller's thresholds."""
    if condition is None:
        return True
    typed = _typed(condition, "AbilityCondition", scope.table)
    if isinstance(typed, MechanicsObject) and _short(typed.type_name) in bridge.BRIDGED_CONDITIONS and \
            _short(typed.type_name) != "AbilityControllerHasThresholdAbilityCondition":
        return bridge.condition_holds(typed, scope)
    if not isinstance(typed, MechanicsObject) or \
            _short(typed.type_name) != "AbilityControllerHasThresholdAbilityCondition":
        raise NotImplementedError(f"ability condition {_short(getattr(typed, 'type_name', ''))}")
    import game_engine
    from rules_port.conditions import _participant_thresholds
    flag = int(game_engine.SHARD_TO_FLAG.get(str(typed.m_ColorFlags).lower(), 0) or 0)
    thresholds = _participant_thresholds(scope.context.bstate, int(scope.owner_uid))
    available = int(thresholds.get(flag, thresholds.get(str(flag), 0)) or 0)
    return _COMPARISONS[typed.m_ComparisonOp](available, int(typed.m_RequiredQuantity))


def _card_owner(scope: Scope, uid: int) -> int | None:
    """The owner of a battle card, None when the battle has no such card."""
    card = _card_by_uid(scope, uid)
    return card.owner if card else None


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
SUPPORTED_VARIABLES = {"AbilityConstant", "AbilityVariable", "CounterVariable", "CardCountAbilityVariable"} \
    | ir_variables.VARIABLE_KINDS


def _clause_names(clause: Any) -> set[str]:
    """The names a clause's effect and condition refer to, as the Records store them (a quoted string)."""
    record = clause.to_record() if isinstance(clause, MechanicsObject) else clause
    texts = [json.dumps(record, default=str)]
    effect = _stored("AbilityEffectTemplate", _guid(_field(clause, "m_EffectTemplateId")))[1]
    condition = _guid(_field(clause, "m_ConditionId"))
    texts.append(json.dumps(effect, default=str))
    if condition and condition != "00000000-0000-0000-0000-000000000000":
        texts.append(json.dumps(_stored("AbilityEffectConditionTemplate", condition)[1], default=str))
    return set(re.findall(r'"([^"]+)"', "".join(texts)))


def _bind_variables(root: MechanicsObject, scope: Scope, wanted: set[str] | None = None) -> None:
    """Each ability variable: a constant's default, the total of a counter or card-count variable, a card property
    or an expression over the others (bound last, once the variables it names are). ``wanted`` is the names the
    clause about to run refers to; None binds every expression."""
    expressions = {}
    for variable in root.m_Variables:
        kind = _short(variable.type_name)
        if kind == "CounterVariable":
            counter = _guid(_field(variable, "m_CardCounterTemplateId"))
            scope.variables[variable.m_Name] = _counter_variable(variable, counter, scope)
        elif kind == "CardCountAbilityVariable":
            scope.variables[variable.m_Name] = _counter_variable(variable, None, scope)
        elif kind == "AbilityConstant":
            scope.variables[variable.m_Name] = int(variable.m_DefaultValue or 0)
        elif kind == "AbilityVariable":
            # Set by an effect (RandomizeVariable): keep the value it gave, else start at zero.
            scope.variables.setdefault(variable.m_Name, 0)
        elif kind == ir_variables.CARD_PROPERTY_VARIABLE:
            scope.variables[variable.m_Name] = ir_variables.card_property_value(scope, variable)
        elif kind == ir_variables.EXPRESSION_VARIABLE:
            expressions[variable.m_Name] = variable
        else:
            raise NotImplementedError(f"variable {kind}")
    ir_variables.bind_expressions(expressions, scope.variables, wanted)


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
    """The cards a clause's target index picks, minus those the running clause's condition on its targets refuses."""
    targets = _resolved_targets(root, index, scope)
    if scope.gate is None:
        return targets
    return [uid for uid in targets if scope.gate(uid)]


def _resolved_targets(root: MechanicsObject, index: int, scope: Scope) -> list[int]:
    """The cards a clause's target index picks: the ability's source, or every card an automatic
    target's filter passes."""
    template = _load("AbilityTargetTemplate", _guid(root.m_AbilityTargetTemplateIds[index]))
    kind = _short(template.type_name)
    if kind == "AbilitySourceCardTargetTemplate":
        return [scope.source_uid]
    if kind == "AbilityTargetTemplate" and template.m_IsAutoTarget:
        return _card_candidates(template, scope)
    if kind == "SourceRevealedTargetTemplate":
        return _revealed_candidates(template, index, scope)
    if kind in ("AbilityTargetTemplate", "SourceRevealedTargetTemplate") and not template.m_IsAutoTarget \
            and index in scope.chosen:
        # A chosen target the player has answered: the card, or nothing when the player chose none.
        chosen = scope.chosen[index]
        return [] if chosen is None else [int(chosen)]
    if kind == "PlayerTargetTemplate":
        # An automatic player target: the controller ("You") or the one opponent. Its champion stands for
        # the player, as the live resolver's player target does for damage and the summoned tokens' owner.
        player = str(template.m_PlayerFilter or "")
        owners = sorted({owner for owner in _champion_owners(scope.context.bstate).values()})
        if player == "Self":
            return [_player_champion(scope, int(scope.owner_uid))]
        if player == "SingleOpponent":
            others = [owner for owner in owners if owner != int(scope.owner_uid)]
            if len(others) != 1:
                raise NotImplementedError("the opponent is not a single player")
            return [_player_champion(scope, others[0])]
        raise NotImplementedError(f"player target {player}")
    raise NotImplementedError(f"target {kind}")


# The player filters an automatic card target honours (C# EPlayerCardTargets). Anything else is refused.
TARGET_PLAYER_FILTERS = {"Self", "MultipleOpponents", "SingleOpponent", "MultiplePlayers"}


def _top_n_of_deck(card_filter: Any) -> MechanicsObject | None:
    """The TopNOfDeck node of a target's card filter (at its top level or in an AND), if it has one."""
    typed = card_filter if isinstance(card_filter, MechanicsObject) else None
    if typed is None:
        return None
    if _short(typed.type_name) == "TopNOfDeck":
        return typed
    if _short(typed.type_name) == "AndCardFilter":
        for part in typed.m_TargetFilters:
            found = _top_n_of_deck(part)
            if found is not None:
                return found
    return None


def _card_candidates(template: MechanicsObject, scope: Scope) -> list[int]:
    """The cards an AbilityTargetTemplate's zones, player filter and card filter pass: the automatic targets,
    and the candidates a chosen target is answered from. A TopNOfDeck filter keeps the first cards of the deck (by
    position) of each player the player filter admits, in deck order."""
    zones = _zones_of(template.m_CollectionFlags)
    player = str(template.m_PlayerFilter or "MultiplePlayers")
    top = _top_n_of_deck(template.m_CardFilter)
    cards = [card for card in _battle_cards(scope)
             if (not zones or card.zone in zones)
             and _player_passes(player, card.owner, scope)
             and _filter_holds(template.m_CardFilter, card, scope)]
    if top is None:
        return [card.uid for card in cards]
    if top.m_CountFromBottom or top.m_TopHalfOfDeck:
        raise NotImplementedError("a top-of-deck filter counted from the bottom or the half")
    chosen: list[int] = []
    for owner in sorted({card.owner for card in cards}):
        deck = sorted((card for card in cards if card.owner == owner and card.zone == "deck"),
                      key=lambda card: card.position)
        chosen.extend(card.uid for card in deck[:int(top.m_Amount or 0)])
    return chosen


def _revealed_candidates(template: MechanicsObject, index: int, scope: Scope) -> list[int]:
    """The cards a SourceRevealedTargetTemplate names: the cards the source's reveal showed, in the template's zones,
    that pass its filter. A later target of the same ability does not name a card an earlier target took (the
    "remaining cards" of an effect are the revealed cards the first target did not take)."""
    revealed = [int(uid) for uid in (scope.context.bstate.get("revealed_cards") or [])]
    zones = _zones_of(template.m_CollectionFlags)
    taken = {int(value) for position, value in scope.chosen.items()
             if position != index and value is not None}
    cards = {card.uid: card for card in _battle_cards(scope)}
    return [uid for uid in revealed
            if uid in cards and uid not in taken
            and (not zones or cards[uid].zone in zones)
            and _filter_holds(template.m_CardFilter, cards[uid], scope)]


def _needs_choice(root: MechanicsObject, index: int) -> bool:
    """Whether a clause's target is a card the player picks (a non-automatic card target)."""
    template = _load("AbilityTargetTemplate", _guid(root.m_AbilityTargetTemplateIds[index]))
    return _short(template.type_name) in ("AbilityTargetTemplate", "SourceRevealedTargetTemplate") \
        and not template.m_IsAutoTarget


def _choice_candidates(root: MechanicsObject, index: int, scope: Scope) -> tuple[list[int], bool]:
    """The cards a chosen target may name, and whether the player may choose none (its m_Optional)."""
    template = _load("AbilityTargetTemplate", _guid(root.m_AbilityTargetTemplateIds[index]))
    if _short(template.type_name) == "SourceRevealedTargetTemplate":
        return _revealed_candidates(template, index, scope), bool(template.m_Optional)
    return _card_candidates(template, scope), bool(template.m_Optional)


def _player_passes(player_filter: str, owner: int, scope: Scope) -> bool:
    """Whether a card controlled by ``owner`` is in a target's player filter, relative to the ability's controller."""
    controller = int(scope.owner_uid)
    if player_filter == "Self":
        return owner == controller
    if player_filter in ("MultipleOpponents", "SingleOpponent"):
        return owner != controller
    if player_filter == "MultiplePlayers":
        return True
    raise NotImplementedError(f"target player filter {player_filter}")


def _player_champion(scope: Scope, owner: int) -> int:
    """The champion UID of a player in the battle."""
    for uid, champion_owner in _champion_owners(scope.context.bstate).items():
        if champion_owner == int(owner):
            return uid
    raise NotImplementedError(f"player {owner} has no champion in the battle")


def _card_abilities(template_guid: str) -> list[str]:
    """The ability GUIDs a card template carries (its m_CardAbilities)."""
    from gamedata import DEFAULT_RECORD_STORE as STORE
    record = STORE.get("CardTemplate", template_guid.lower())
    if record is None:
        raise NotImplementedError(f"token template {template_guid} is missing from the Records")
    raw = record.to_dict(include_metadata=True)
    return [str(_guid(item.get("m_CardAbilityId")) or "") for item in raw.get("m_CardAbilities") or []]


def _summon_tokens(root: MechanicsObject, clause: Any, effect_raw: dict[str, Any], scope: Scope) -> None:
    """C# SummonTokenTroop: the live token primitive creates the tokens for the target's controller. Its
    fields come from this effect's stored template and its numbers from the ability's bound variables."""
    from rules_port import token_effects
    context = scope.context
    # The live resolver creates the controller's tokens first, then the others'.
    targets = sorted(_targets(root, int(_field(clause, "m_TargetTemplateIndex") or 0), scope),
                     key=lambda uid: (_card_owner(scope, uid) != int(scope.owner_uid), uid))
    previous_template = context.effect_template_override
    context.effect_template_override = effect_raw
    try:
        with _bridge_caster(context.bstate, owner=scope.owner_uid, variables=scope.variables):
            for target in targets:
                token_effects.summon_token(context, target=int(target), owner=int(scope.owner_uid))
    finally:
        context.effect_template_override = previous_template


def _target_owners(root: MechanicsObject, index: int, scope: Scope) -> list[int]:
    """The players a clause's target belongs to: the controller ("You"), the one opponent, or the owner of each card
    target (a heal lands on the owner's champion; an invoke runs for each owner). A player's id is the live
    resolver's owner id (the AI is 0)."""
    template = _load("AbilityTargetTemplate", _guid(root.m_AbilityTargetTemplateIds[index]))
    if _short(template.type_name) != "PlayerTargetTemplate":
        return [owner for owner in (_card_owner(scope, uid) for uid in _targets(root, index, scope)) if owner is not None]
    player = str(template.m_PlayerFilter or "")
    if player == "Self":
        return [int(scope.owner_uid)]
    if player == "SingleOpponent":
        owners = set(_champion_owners(scope.context.bstate).values())
        others = sorted(owner for owner in owners if owner != int(scope.owner_uid))
        if len(others) == 1:
            return others
        if not others and int(scope.owner_uid) != 0:
            return [0]  # PvE: the opponent is the AI, owner 0
        raise NotImplementedError("the opponent is not a single player")
    raise NotImplementedError(f"player target {player}")


def _is_sacrifice(clause: Any) -> bool:
    """Whether a clause's effect is a sacrifice."""
    effect_type, _raw = _stored("AbilityEffectTemplate", _guid(_field(clause, "m_EffectTemplateId")))
    return _short(effect_type) == "SacrificeCardAbilityEffectTemplate"


def _run_clause(root: MechanicsObject, clause: Any, scope: Scope) -> None:
    from rules_port import ir_exec_resources
    effect_guid = _guid(_field(clause, "m_EffectTemplateId"))
    effect_type, effect_raw = _stored("AbilityEffectTemplate", effect_guid)
    if _short(effect_type) in ZONE_EFFECTS:
        run_zone_effect(_short(effect_type), root, clause, effect_raw, scope)
        return
    if _short(effect_type) == "SacrificeCardAbilityEffectTemplate":
        from rules_port.death_effects import kill_troop
        index = int(_field(clause, "m_TargetTemplateIndex") or 0)
        # A sacrifice of other cards never takes the source as its own payment (the native candidates leave it out);
        # "sacrifice this" is the source itself.
        this_card = _short(_load("AbilityTargetTemplate", _guid(root.m_AbilityTargetTemplateIds[index])).type_name) \
            == "AbilitySourceCardTargetTemplate"
        for target in _targets(root, index, scope):
            if this_card or int(target) != int(scope.source_uid):
                kill_troop(scope.context, target, cause="sacrifice")
        return
    if _short(effect_type) == "SummonTokenTroopAbilityEffectTemplate":
        _summon_tokens(root, clause, effect_raw, scope)
        return
    if _short(effect_type) in ir_exec_resources.EFFECTS:
        ir_exec_resources.run_effect(_short(effect_type), effect_raw, root, clause, scope)
        return
    if _short(effect_type) in NATIVE_LEAF_EFFECTS:
        # TAC and event-firing leaves run on the native path, once per target as the resolver runs them.
        run_native_leaf(scope.context, _short(effect_type), effect_raw,
                        _targets(root, int(_field(clause, "m_TargetTemplateIndex") or 0), scope))
        return
    if _short(effect_type) == "SetCardIntegerVariableEffectTemplate":
        # The native leaf reads the ability's variables and writes its new value to the battle state, so the
        # variables are published for it and the value it wrote is read back into the interpreter's scope.
        name = str(effect_raw.get("m_VariableName") or "")
        with _bridge_caster(scope.context.bstate, variables=scope.variables):
            run_native_leaf(scope.context, _short(effect_type), effect_raw,
                            _targets(root, int(_field(clause, "m_TargetTemplateIndex") or 0), scope))
            written = scope.context.bstate.get("ability_variables", {})
            if name in written:
                scope.variables[name] = int(written[name])
        return
    from rules_port.ir_exec_combat import COMBAT_EFFECTS, run_clause as run_combat_clause
    if _short(effect_type) in COMBAT_EFFECTS:
        run_combat_clause(_short(effect_type), root, clause, effect_raw, scope)
        return
    if _short(effect_type) != "CardModifierAbilityEffectTemplate":
        raise NotImplementedError(f"effect {_short(effect_type)}")
    modifier = _typed(effect_raw["m_Modifier"], "Modifier", effect_raw.get("_v"))
    if not isinstance(modifier, MechanicsObject):
        raise NotImplementedError(f"effect {effect_guid} has an untyped modifier")
    kind = _short(modifier.type_name)
    index = int(_field(clause, "m_TargetTemplateIndex") or 0)
    if kind in ir_exec_resources.MODIFIERS:
        ir_exec_resources.run_modifier(kind, modifier, root, clause, scope)
        return
    if kind in ("AttackModifier", "DefenseModifier"):
        # The shared stat primitive, with the live path's choice of buffs (EndOfTurn is this turn only).
        from rules_port.stat_effects import apply_stat_mod
        amount = _input_value(modifier, scope)
        this_turn = str(_field(clause, "m_EffectDuration") or "") == "EndOfTurn"
        for target in _targets(root, index, scope):
            attack, defense = (amount, 0) if kind == "AttackModifier" else (0, amount)
            apply_stat_mod(scope.context, target, attack, defense, this_turn=this_turn)
        return
    if kind in GROUP_G_MODIFIERS:
        from rules_port.ir_modifiers import run_modifier
        duration = str(_field(clause, "m_EffectDuration") or "")
        run_modifier(modifier, effect_guid, duration, _targets(root, index, scope), scope)
        return
    if kind == "HealHeroModifier":
        # A health gain for each player it lands on, through the live gain path (which refuses while
        # CantGainHealth applies).
        amount = _input_value(modifier, scope)
        for owner in _target_owners(root, index, scope):
            scope.context.gain_health(int(owner), amount)
        return
    # Damage is dealt to the cards in last-in-first-out order: the client pushes the matching cards on a stack and
    # pops them (a Deathcry's troops are hit before the champion that is listed first).
    targets = _targets(root, index, scope)
    if kind == "DamageModifier":
        targets = list(reversed(targets))
    for target in targets:
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


def engine_runs(guid: str, source_uid: Any, owner_uid: Any, answers: dict[int, Any] | None = None) -> bool:
    """Whether the interpreter resolves this ability in place of the native resolver: the engine is on, the ability
    has a source card and an owner, and no construct of it is uncovered.

    A card the player picks is allowed when the player's answer for it is given (``answers``, keyed by target index),
    and the abilities it invokes pick nothing. The native prompt asks the player and its answer comes back in the
    chain item; without an answer the ability stays on the native path."""
    if not (ENGINE_ON and source_uid is not None and owner_uid is not None) or coverage(guid):
        return False
    root = _load("AbilityTemplate", guid)
    own = [index for index in range(len(root.m_AbilityTargetTemplateIds or [])) if _needs_choice(root, index)]
    if any(index not in (answers or {}) for index in own):
        return False
    return not _invoked_non_revealed_choice(root, set())


def _invoked_non_revealed_choice(root: MechanicsObject, seen: set[str]) -> bool:
    """Whether an invoked ability (at any depth) picks a card other than from a reveal. A reveal's pick is shown
    through the revealed-card picker, which the engine wires; any other pick is not."""
    for clause in root.m_AbilityEffectList:
        effect_type, effect_raw = _stored("AbilityEffectTemplate", _guid(_field(clause, "m_EffectTemplateId")))
        if _short(effect_type) != "ActivateAbilityEffectTemplate":
            continue
        child = _guid(effect_raw.get("m_AbilityToInvoke"))
        if child in seen:
            continue
        child_root = _load("AbilityTemplate", child)
        for index in range(len(child_root.m_AbilityTargetTemplateIds or [])):
            if _needs_choice(child_root, index):
                template = _load("AbilityTargetTemplate", _guid(child_root.m_AbilityTargetTemplateIds[index]))
                if _short(template.type_name) != "SourceRevealedTargetTemplate" and \
                        "deck" not in _zones_of(template.m_CollectionFlags):
                    # Only a pick from the deck has its picker wired (the deck-search prompt); any other pick stays
                    # on the native path.
                    return True
        if _invoked_non_revealed_choice(child_root, seen | {child}):
            return True
    return False


def _pauses_for_choice(guid: str, seen: set[str]) -> bool:
    """Whether an ability, or an ability it invokes, has a target the player picks."""
    if guid in seen:
        return False
    seen = seen | {guid}
    root = _load("AbilityTemplate", guid)
    if any(_needs_choice(root, index) for index in range(len(root.m_AbilityTargetTemplateIds or []))):
        return True
    for clause in root.m_AbilityEffectList:
        effect_type, effect_raw = _stored("AbilityEffectTemplate", _guid(_field(clause, "m_EffectTemplateId")))
        if _short(effect_type) == "ActivateAbilityEffectTemplate" and \
                _pauses_for_choice(_guid(effect_raw.get("m_AbilityToInvoke")), seen):
            return True
    return False


def live_event(event_type: str, owner_uid: int, data: dict[str, Any], trigger_source_uid: Any,
               target_player_uid: Any = None) -> dict[str, Any]:
    """The interpreter's event for a live trigger: its player, its card and target player, and for a zone move the
    card and zones the live dispatch recorded (``event_source_collection`` and ``event_destination_collection``)."""
    event: dict[str, Any] = {"player": owner_uid, **ir_triggers.event_fields(event_type, trigger_source_uid,
                                                                             target_player_uid)}
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
    stored = raw.get("m_Condition") or {}
    if _short(stored.get("_t")) == "RequiresTargetPassesFilterCondition":
        # Tested against each target as the clause picks it (scope.gate), not before the clause runs.
        if scope.gate is None:
            raise NotImplementedError("target condition outside its clause")
        return True
    if _short(stored.get("_t")) == "NotContingentAbilityCondition":
        # C# NotContingentAbilityCondition: holds when the earlier effect at m_EffectIndex did not apply.
        if stored.get("m_EffectIndex") is None:
            return True
        return not scope.applied.get(int(stored["m_EffectIndex"]), False)
    if _short(stored.get("_t")) == "AbilityVariableCondition":
        # C# AbilityVariableCondition: the named variable compared with a constant.
        name = str(stored.get("m_Lhs") or "")
        if name not in scope.variables:
            raise KeyError(f"variable {name} is not bound")
        return _COMPARISONS[stored["m_ComparisonOp"]](scope.variables[name], _variable_or_number(stored, scope))
    if _short(stored.get("_t")) in bridge.BRIDGED_CONDITIONS:
        return bridge.condition_holds(stored, scope)
    condition = _typed(raw.get("m_Condition"), "AbilityEffectCondition", raw.get("_v"))
    if not isinstance(condition, MechanicsObject) or not hasattr(condition, "m_CardCollection"):
        raise NotImplementedError(f"effect condition {guid}")
    return _count_holds(condition, scope)


def _variable_or_number(stored: dict[str, Any], scope: Scope) -> int:
    """The right-hand side of a variable condition: a number, or the value of a bound variable."""
    rhs = str(stored.get("m_Rhs") or "0").strip()
    if rhs.lstrip("-").isdigit():
        return int(rhs)
    if rhs not in scope.variables:
        raise KeyError(f"variable {rhs} is not bound")
    return scope.variables[rhs]


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
                owner_uid: int, event: dict[str, Any] | None = None,
                answers: dict[int, int | None] | None = None) -> bool:
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
    if event_type is not None and _short(_field(trigger, "m_InternalType")) != event_type:
        return False
    event = {"player": owner_uid, **(event or {})}
    scope = Scope(context=context, source_uid=int(source_uid), owner_uid=int(owner_uid),
                  table=root.version_table, event=event)
    # The trigger fires only while its source is in one of the zones its collection flags name (C#
    # AbilityTemplate.TriggerCollectionFlags).
    zones = _zones_of(_field(root, "m_TriggerCollectionFlags"))
    source = _card_by_uid(scope, source_uid)
    # With m_UsesPreviousState the flags are tested against the zone the source was in before the event
    # (C# PassesCollectionFlagRequirements), which for the card that moved is the event's "from" zone.
    zone = source.zone if source else None
    if _field(root, "m_UsesPreviousState") and event.get("card") is not None \
            and int(event["card"]) == int(source_uid) and event.get("from"):
        zone = str(event["from"]).lower()
    if event_type is not None and zones and zone not in zones:
        return False
    # An activation's own threshold is checked when the player activates (C# CanActivateAbilityBase), not again when
    # it resolves, so only a triggered ability re-checks it here.
    if event_type is not None and not _ability_condition_holds(root.m_AbilityCondition, scope):
        return False
    if not _condition_holds(root.m_TriggerCondition, scope):
        return False
    scope.chosen = {int(k): (None if v is None else int(v)) for k, v in (answers or {}).items()}
    return _run_effects(guid, root, scope, start=0)


def _run_effects(guid: str, root: MechanicsObject, scope: Scope, start: int) -> bool:
    """Run the ability's effects from clause ``start``. A clause whose target the player picks either takes its
    one forced candidate, takes nothing, or pauses with NeedsChoice before it runs."""
    bstate = scope.context.bstate
    with _bridge_caster(bstate, owner=scope.owner_uid, source=scope.source_uid):
        for position in range(start, len(root.m_AbilityEffectList)):
            clause = root.m_AbilityEffectList[position]
            # Counts are read when the effect runs, after the effects before it (C# evaluates a variable lazily).
            _bind_variables(root, scope, _clause_names(clause))
            # A contingent effect runs only when the effect it names applied (C# ContingentEffectInstanceId).
            if not _contingency_holds(root, clause, scope):
                scope.applied[position] = False
                continue
            # An effect runs only when its own condition holds (its m_ConditionId, as C# checks it). A condition
            # on the effect's targets is tested as each target is picked (scope.gate).
            scope.gate = _target_gate(clause, scope)
            if not _clause_condition_holds(clause, scope):
                scope.applied[position] = False
                continue
            index = int(_field(clause, "m_TargetTemplateIndex") or 0)
            if index not in scope.chosen and _needs_choice(root, index):
                candidates, optional = _choice_candidates(root, index, scope)
                if _is_sacrifice(clause):
                    candidates = [uid for uid in candidates if int(uid) != int(scope.source_uid)]
                if len(candidates) == 1 and not optional:
                    # The live resolver does not prompt for a single forced candidate (its single_forced rule).
                    scope.chosen[index] = candidates[0]
                elif not candidates:
                    scope.chosen[index] = None
                else:
                    continuation = {"ability_guid": guid, "event": dict(scope.event),
                                    "source_uid": int(scope.source_uid), "owner_uid": int(scope.owner_uid),
                                    "position": position, "target_index": index,
                                    "candidates": candidates, "optional": optional,
                                    "chosen": {str(k): v for k, v in scope.chosen.items()},
                                    "variables": dict(scope.variables),
                                    "applied": {str(k): v for k, v in scope.applied.items()}}
                    template = _load("AbilityTargetTemplate", _guid(root.m_AbilityTargetTemplateIds[index]))
                    revealed = _short(template.type_name) == "SourceRevealedTargetTemplate"
                    raise NeedsChoice(continuation, candidates, optional, revealed=revealed)
            effect_type, effect_raw = _stored("AbilityEffectTemplate", _guid(_field(clause, "m_EffectTemplateId")))
            # A chosen revealed target that finds no card ends the ability: the client disables that effect and
            # runs nothing after it (Oakhenge's remaining cards are not moved, and the random generator is not drawn).
            # An automatic revealed target that finds nothing only disables its own effect.
            if _short(effect_type) in ("MoveCardToZoneEffectTemplate", "RevealCardsAbilityEffectTemplate",
                                       "PlayCardAbilityEffectTemplate") and \
                    _revealed_target_empty(root, int(_field(clause, "m_TargetTemplateIndex") or 0), scope):
                break
            if _short(effect_type) == "PlayCardAbilityEffectTemplate":
                from rules_port.ir_exec_play import play_for_free
                play_for_free(scope.context, _targets(root, index, scope), effect_raw)
                continue
            # The effect applies from here on: a later NotContingent condition reads it (C# applied_effects).
            scope.applied[position] = True
            if _short(effect_type) == "ActivateAbilityEffectTemplate":
                _invoke(guid, position, effect_raw, scope, _target_owners(root, index, scope))
                continue
            if _short(effect_type) == "RandomizeVariableEffectTemplate":
                _randomize_variable(effect_raw, scope)
                continue
            if _short(effect_type) == "MoveCardToZoneEffectTemplate":
                _move_cards(root, clause, effect_raw, scope)
                continue
            if _short(effect_type) == "RevealCardsAbilityEffectTemplate":
                _reveal_cards(root, clause, effect_raw, scope)
                continue
            _run_clause(root, clause, scope)
    scope.gate = None
    return True


def _contingency_holds(root: MechanicsObject, clause: Any, scope: Scope) -> bool:
    """Whether the effect the clause is contingent on applied. A clause with no contingency, or one naming an
    effect the ability does not have, is not held back."""
    wanted = _field(clause, "m_ContingentEffectInstanceId")
    if wanted is None or int(wanted) == -1:
        return True
    for position, other in enumerate(root.m_AbilityEffectList):
        if int(_field(other, "m_EffectInstanceId") or -1) == int(wanted):
            return bool(scope.applied.get(position, False))
    return True


def _target_gate(clause: Any, scope: Scope):
    """The per-target test of a clause whose effect condition is RequiresTargetPassesFilterCondition, else None."""
    guid = _guid(_field(clause, "m_ConditionId"))
    if not guid or guid == "00000000-0000-0000-0000-000000000000":
        return None
    _cls, raw = _stored("AbilityEffectConditionTemplate", guid)
    stored = raw.get("m_Condition") or {}
    if _short(stored.get("_t")) != "RequiresTargetPassesFilterCondition":
        return None
    return bridge.target_gate(stored, scope)


def _reveal_cards(root: MechanicsObject, clause: Any, effect_raw: dict[str, Any], scope: Scope) -> None:
    """C# RevealCardsAbilityEffectTemplate: the targets are shown through the live reveal leaf
    (rules_port.reveal_effects.reveal_cards), which sets the revealed cards the ability's later targets name."""
    from rules_port.reveal_effects import reveal_cards
    context = scope.context
    targets = _targets(root, int(_field(clause, "m_TargetTemplateIndex") or 0), scope)
    # No card to show: nothing is revealed. The live leaf would otherwise fall back to a zone-and-count reveal.
    if not targets:
        scope.context.bstate["revealed_cards"] = []
        return
    previous_template, previous_targets = context.effect_template_override, context.effect_targets
    context.effect_template_override = effect_raw
    context.effect_targets = tuple(int(target) for target in targets)
    try:
        with _bridge_caster(context.bstate, owner=scope.owner_uid):
            reveal_cards(context)
    finally:
        context.effect_template_override = previous_template
        context.effect_targets = previous_targets


@contextmanager
def _bridge_caster(bstate: dict[str, Any], *, owner: Any = None, source: Any = None,
                   variables: dict[str, Any] | None = None) -> Iterator[None]:
    """The one place the interpreter writes the caster, source and variables into the battle state. The shared
    resolver leaves read those from the battle state, so they are set here for the duration of one interpreted call
    and restored after it. Every target the interpreter acts on is passed explicitly, not read from this state."""
    saved = {key: bstate.get(key) for key in ("resolving_owner_id", "resolving_source_uid", "ability_variables")}
    try:
        if owner is not None:
            bstate["resolving_owner_id"] = int(owner)
        if source is not None:
            bstate["resolving_source_uid"] = int(source)
        if variables is not None:
            bstate["ability_variables"] = dict(variables)
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                bstate.pop(key, None)
            else:
                bstate[key] = value


def _move_cards(root: MechanicsObject, clause: Any, effect_raw: dict[str, Any], scope: Scope) -> None:
    """C# MoveCardToZoneEffectTemplate: each target moves to the authored zone, through the live move leaf
    (rules_port.context move_card_to_zone), with the target passed explicitly and the caster bridged for the leaf."""
    context = scope.context
    targets = _targets(root, int(_field(clause, "m_TargetTemplateIndex") or 0), scope)
    previous_template = context.effect_template_override
    context.effect_template_override = effect_raw
    try:
        with _bridge_caster(context.bstate, owner=scope.owner_uid):
            for target in targets:
                context.move_card_to_zone(target=int(target))
    finally:
        context.effect_template_override = previous_template


def _randomize_variable(effect_raw: dict[str, Any], scope: Scope) -> None:
    """C# RandomizeVariableEffectTemplate: a number from the minimum to the maximum, inclusive, drawn from the
    session RNG (rules_port.context.randomize_variable), kept in the ability's variables under the name."""
    from rules_port.runtime_helpers import rng_below
    minimum, maximum = int(effect_raw.get("m_MinValue") or 0), int(effect_raw.get("m_MaxValue") or 0)
    if minimum > maximum:
        minimum, maximum = maximum, minimum
    span = max(0, maximum - minimum) + 1
    scope.variables[str(effect_raw.get("m_VariableName") or "RandomNumber")] = \
        minimum + int(rng_below(scope.context.bstate, span)) % span


def _revealed_target_empty(root: MechanicsObject, index: int, scope: Scope) -> bool:
    """Whether a clause's revealed target is a chosen one that finds no card.""" 
    template = _load("AbilityTargetTemplate", _guid(root.m_AbilityTargetTemplateIds[index]))
    return _short(template.type_name) == "SourceRevealedTargetTemplate" and not template.m_IsAutoTarget \
        and not _revealed_candidates(template, index, scope)


def _invoke(parent_guid: str, position: int, effect_raw: dict[str, Any], scope: Scope, owners: list[int]) -> None:
    """C# ActivateAbilityEffectTemplate: run the invoked ability once for each owner the invoke's target names (the
    controller of "You invoke", each opposing champion of "Each opposing champion invokes"), as a child of the same
    source and event. Its own trigger is not checked. A choice the child pauses for is kept with this invoke as the
    outer frame of its continuation, so resuming the child then carries on from the invoking clause."""
    child_guid = _guid(effect_raw.get("m_AbilityToInvoke"))
    child_root = _load("AbilityTemplate", child_guid)
    for owner in owners:
        child_scope = Scope(context=scope.context, source_uid=int(scope.source_uid), owner_uid=int(owner),
                            table=child_root.version_table, event=dict(scope.event))
        try:
            _run_effects(child_guid, child_root, child_scope, start=0)
        except NeedsChoice as pause:
            frame = {"ability_guid": parent_guid, "event": dict(scope.event), "source_uid": int(scope.source_uid),
                     "owner_uid": int(scope.owner_uid), "position": position,
                     "chosen": {str(k): v for k, v in scope.chosen.items()},
                     "variables": dict(scope.variables),
                     "applied": {str(k): v for k, v in scope.applied.items()}}
            _append_chain(pause.continuation, frame)
            raise


def _append_chain(continuation: dict[str, Any], outer: dict[str, Any] | None) -> None:
    """Attach ``outer`` (the invoking frames) after the last frame of ``continuation``."""
    node = continuation
    while node.get("parent") is not None:
        node = node["parent"]
    node["parent"] = outer


def _frame_scope(frame: dict[str, Any], context: Any) -> tuple[MechanicsObject, Scope]:
    """The root and scope of one frame of a paused ability, as it was when the frame paused."""
    root = _load("AbilityTemplate", frame["ability_guid"])
    scope = Scope(context=context, source_uid=int(frame["source_uid"]), owner_uid=int(frame["owner_uid"]),
                  table=root.version_table, event=dict(frame["event"]))
    scope.chosen = {int(k): (None if v is None else int(v)) for k, v in frame["chosen"].items()}
    # The variables the frame had bound (a random branch's number, say) are part of its state, so the resumed effects
    # see the same values the paused run did.
    scope.variables = dict(frame.get("variables") or {})
    scope.applied = {int(k): bool(v) for k, v in (frame.get("applied") or {}).items()}
    return root, scope


def resume_ability(continuation: dict[str, Any], answer: int | None, *, context: Any) -> bool:
    """Resume a paused ability with the player's answer: the card chosen, or None for choosing none. The answer
    must be one of the candidates the pause offered (or None when the choice is optional). The invoking frames
    then resume after their invoke clauses, outermost last."""
    index = int(continuation["target_index"])
    candidates = [int(uid) for uid in continuation["candidates"]]
    if answer is None:
        if not continuation["optional"]:
            raise ValueError("this choice is not optional")
    elif int(answer) not in candidates:
        raise ValueError(f"{answer} is not a candidate of this choice")
    root, scope = _frame_scope(continuation, context)
    scope.chosen[index] = None if answer is None else int(answer)
    outer = continuation.get("parent")
    try:
        _run_effects(continuation["ability_guid"], root, scope, start=int(continuation["position"]))
    except NeedsChoice as pause:
        _append_chain(pause.continuation, outer)
        raise
    frame = outer
    while frame is not None:
        frame_root, frame_scope = _frame_scope(frame, context)
        nxt = frame.get("parent")
        try:
            _run_effects(frame["ability_guid"], frame_root, frame_scope, start=int(frame["position"]) + 1)
        except NeedsChoice as pause:
            _append_chain(pause.continuation, nxt)
            raise
        frame = nxt
    return True


# What the interpreter runs. A construct outside these sets is uncovered: its ability raises
# NotImplementedError when run, and the coverage report lists it.
SUPPORTED_TRIGGERS = {"TurnEndedEvent", "TurnStartedEvent", "CardEnteredZoneEvent"} | ir_triggers.TRIGGER_EVENTS
SUPPORTED_CONDITIONS = {
    "AndTriggerCondition", "TriggerCondition", "TriggerSource", "TriggerPlayerControlsAbilitySource",
    "TriggerCardEnteredZone", "TriggerCardMatchesFilter", "RequiresCardsControlled",
    "TriggerCardIsAbilitySource",
} | ir_triggers.CONDITIONS
SUPPORTED_FILTERS = {
    "AndCardFilter", "OrCardFilter", "NotCardFilter", "IsControlledBy", "IsNotControlledBy",
    "HasCountersValue", "IsTroop", "IsHero", "IsResource", "IsType", "IntAttrFilter", "InZone",
    "IsAbilitySource", "IsSubType", "NameContainsFilter", "TopNOfDeck",
}
SUPPORTED_MODIFIERS = {"DamageModifier", "CounterModifier", "HealHeroModifier", "AttackModifier", "DefenseModifier",
                       "CurrentResourceModifier", "TotalResourceModifier", "ChargePointsModifier", "ThresholdModifier",
                       "LoseLifeModifier", "SpellPointsModifier", "CardCostModifier"}
# The clause durations a stat modifier runs with: Permanent (the card's permanent buffs) and EndOfTurn (its
# temporary buffs), as the live stat path applies them. Other durations are refused.
STAT_DURATIONS = {"Permanent", "EndOfTurn"}
# The effect text the live stat path derives its operand from (equal to this troop's DEF, a voided troop's stat).
# An effect with such text is refused, not computed differently.
DERIVED_STAT_TEXT = ("troop's [def]", "voided troop's")
SUPPORTED_TARGETS = {"AbilitySourceCardTargetTemplate", "AbilityTargetTemplate", "PlayerTargetTemplate",
                     "SourceRevealedTargetTemplate"}
# The player targets run without a choice: "You", and the one opponent (its random pick is that one player).
SUPPORTED_PLAYER_TARGETS = {"Self", "SingleOpponent"}


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


# The destinations the live move leaf moves a card to (its deck reinsertion is not ported).
MOVE_DESTINATIONS = {"hand", "discard", "void", "warzone", "underground", "deck"}


_REGEX_CHARACTERS = set("\\.[](){}*+?|^$")


def _unsupported_name_filters(value: Any, out: list[str]) -> list[str]:
    """Every NameContainsFilter the interpreter cannot evaluate as C# does: its keyword or subtype inclusion, or a
    pattern that uses regular-expression syntax (only plain text is compared)."""
    if isinstance(value, dict):
        if _short(value.get("_t")) == "NameContainsFilter":
            if value.get("m_IncludeKeywords") or value.get("m_IncludeSubType"):
                out.append("name filter with keywords or subtype")
            if set(str(value.get("m_ContainsString") or "")) & _REGEX_CHARACTERS:
                out.append("name filter with a regular expression")
        for item in value.values():
            _unsupported_name_filters(item, out)
    elif isinstance(value, list):
        for item in value:
            _unsupported_name_filters(item, out)
    return out


def _comparison_ops(value: Any, out: list[str]) -> list[str]:
    """Every comparison operator a stored value names (its ``m_ComparisonOp`` fields), in order."""
    if isinstance(value, dict):
        if isinstance(value.get("m_ComparisonOp"), str):
            out.append(value["m_ComparisonOp"])
        for item in value.values():
            _comparison_ops(item, out)
    elif isinstance(value, list):
        for item in value:
            _comparison_ops(item, out)
    return out


# The abilities whose coverage is being checked, so an invocation cycle is refused rather than followed.
_COVERAGE_STACK: set[str] = set()


def coverage(guid: str) -> list[str]:
    """The constructs in an ability the interpreter does not run (empty when it runs them all)."""
    from rules_port import ir_exec_resources
    _cls, raw = _stored("AbilityTemplate", guid)
    missing: list[str] = []
    trigger = _short((raw.get("m_TriggerEventType") or {}).get("m_InternalType"))
    if trigger and trigger not in SUPPORTED_TRIGGERS:
        missing.append(f"trigger {trigger}")
    top_condition = _short((raw.get("m_TriggerCondition") or {}).get("_t"))
    if top_condition in SUPPORTED_FILTERS:
        # A card filter on its own as the trigger condition: there is no card of the event to test it against, and
        # the runtime does not evaluate it (Feralroot Archdruid's health-gained check).
        missing.append(f"trigger condition {top_condition}")
    if raw.get("m_Threshold"):
        missing.append("ability threshold")
    ability_condition = _short((raw.get("m_AbilityCondition") or {}).get("_t"))
    if ability_condition and ability_condition != "AbilityControllerHasThresholdAbilityCondition":
        missing.extend(bridge.unsupported(raw.get("m_AbilityCondition"), "ability condition"))
    for name in _tagged_classes(raw.get("m_TriggerCondition"), []):
        # A ResourceId is a value (a counter's identity), not a construct.
        if name not in SUPPORTED_CONDITIONS and name not in SUPPORTED_FILTERS and name != "ResourceId" \
                and name not in bridge.BRIDGED_CONDITIONS and not bridge.filter_accepted(name):
            missing.append(f"condition {name}")
    trigger_condition = _short((raw.get("m_TriggerCondition") or {}).get("_t"))
    if not trigger and trigger_condition in SUPPORTED_FILTERS:
        # A card filter standing as the whole condition of an ability with no trigger. The native activation does not
        # test it (the ability runs on the native path), so the interpreter refuses it rather than gate differently.
        missing.append(f"activation condition {trigger_condition}")
    for template_guid in (_guid(item) for item in raw.get("m_AbilityTargetTemplateIds") or []):
        template_type, template = _stored("AbilityTargetTemplate", template_guid)
        kind = _short(template_type)
        if kind not in SUPPORTED_TARGETS:
            missing.append(f"target {kind}")
        if kind == "PlayerTargetTemplate":
            player = str(template.get("m_PlayerFilter") or "")
            if not template.get("m_IsAutoTarget"):
                missing.append("chosen target")
            if player not in SUPPORTED_PLAYER_TARGETS:
                missing.append(f"player target {player}")
            continue
        if kind == "AbilityTargetTemplate" and str(template.get("m_PlayerFilter") or "") not in TARGET_PLAYER_FILTERS:
            missing.append(f"target player filter {template.get('m_PlayerFilter')}")
        if template.get("m_IsRandomTarget"):
            missing.append("random target")
        for name in _tagged_classes(template.get("m_CardFilter"), []):
            if name not in SUPPORTED_FILTERS and not bridge.filter_accepted(name):
                missing.append(f"filter {name}")
        # The interpreter picks one card for a target index. A target that takes several (or a count that varies)
        # is refused, as the native resolver takes them all ("two target" and "up to two target" cards).
        for bound in ("m_MinTargetCount", "m_MaxTargetCount"):
            count = template.get(bound)
            if isinstance(count, dict) and count.get("m_Value") is not None and int(count["m_Value"]) != 1 \
                    and not (bound == "m_MinTargetCount" and int(count["m_Value"]) == 0):
                missing.append(f"multi-card target ({bound} {count['m_Value']})")
            elif isinstance(count, dict) and count.get("m_Value") is None:
                missing.append(f"variable target count ({bound})")
    # Paid costs and optional abilities are settled before the ability reaches the interpreter (ir_exec_costs).
    missing.extend(unsupported_costs(raw))
    for operator in _comparison_ops(raw, []):
        if operator not in _COMPARISONS:
            missing.append(f"comparison {operator}")
    missing.extend(_unsupported_name_filters(raw, []))
    for clause in raw.get("m_AbilityEffectList") or []:
        condition_guid = _guid(clause.get("m_ConditionId"))
        if condition_guid and condition_guid != "00000000-0000-0000-0000-000000000000":
            _cls, condition = _stored("AbilityEffectConditionTemplate", condition_guid)
            stored = condition.get("m_Condition") or {}
            kind = _short(stored.get("_t"))
            if kind == "AbilityVariableCondition":
                declared = {str(v.get("m_Name") or "") for v in raw.get("m_Variables") or [] if isinstance(v, dict)}
                rhs = str(stored.get("m_Rhs") or "0").strip()
                if str(stored.get("m_Lhs") or "") not in declared:
                    missing.append(f"variable condition on {stored.get('m_Lhs')}")
                elif not rhs.lstrip("-").isdigit() and rhs not in declared:
                    missing.append(f"variable condition against {rhs}")
            elif kind == "RequiresTargetPassesFilterCondition":
                # Tested against each target (scope.gate), so only its filter has to run.
                missing.extend(bridge.unsupported(stored.get("m_Filter"), "effect condition"))
            elif kind not in ("RequiresCardsControlled", "NotContingentAbilityCondition"):
                missing.extend(bridge.unsupported(stored, "effect condition"))
                missing.extend(f"comparison {op}" for op in _comparison_ops(stored, []) if op not in _COMPARISONS)
    declared = {str(v.get("m_Name") or "") for v in raw.get("m_Variables") or [] if isinstance(v, dict)}
    expressions: dict[str, str] = {}
    for variable in raw.get("m_Variables") or []:
        kind = _short((variable or {}).get("_t")) if isinstance(variable, dict) else ""
        if kind not in SUPPORTED_VARIABLES:
            missing.append(f"variable {kind or 'untyped'}")
            continue
        if kind in ("AbilityConstant", "AbilityVariable"):
            continue
        if kind == ir_variables.CARD_PROPERTY_VARIABLE:
            missing.extend(ir_variables.card_property_gaps(variable))
            continue
        if kind == ir_variables.EXPRESSION_VARIABLE:
            expressions[str(variable.get("m_Name") or "")] = str(variable.get("m_ExpressionText") or "")
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
            if name not in SUPPORTED_FILTERS and not bridge.filter_accepted(name):
                missing.append(f"filter {name}")
    missing.extend(ir_variables.expression_gaps(expressions, declared))
    for clause in raw.get("m_AbilityEffectList") or []:
        effect_type, effect = _stored("AbilityEffectTemplate", _guid(clause.get("m_EffectTemplateId")))
        kind = _short(effect_type)
        if kind == "SummonTokenTroopAbilityEffectTemplate":
            # The summon itself is the live token primitive's (its random pick comes from the battle's RNG). A
            # token's entry triggers are queued on the battle stack, as on the live path, for the chain to resolve.
            continue
        if kind == "SacrificeCardAbilityEffectTemplate":
            continue
        if kind in ZONE_EFFECTS:
            missing.extend(zone_effect_gaps(kind, raw, clause, effect))
            continue
        if kind in NATIVE_LEAF_EFFECTS:
            missing.extend(event_effect_coverage(kind, effect))
            continue
        if kind == "SetCardIntegerVariableEffectTemplate":
            operation = str(effect.get("m_Operation") or "Set").rsplit(".", 1)[-1]
            if operation not in ("Add", "Remove", "Set"):
                missing.append(f"card integer operation {operation}")
            continue
        if kind == "MoveCardToZoneEffectTemplate":
            destination = _short(effect.get("m_DestinationCollection")).lower()
            if destination not in MOVE_DESTINATIONS:
                missing.append(f"move to {destination or 'no zone'}")
            if str(effect.get("m_AllCardsOfTargetInZone") or "None") not in ("None", ""):
                missing.append("move of every card in a zone")
            continue
        if kind == "PlayCardAbilityEffectTemplate":
            # The free play is the live play leaf's; a play that is only an activation is not modelled.
            if effect.get("m_JustActivate"):
                missing.append("play that only activates")
            continue
        if kind == "RevealCardsAbilityEffectTemplate":
            if str(effect.get("m_PlayerRevealTargets") or "Unknown") not in ("Unknown", "Everyone", "Self"):
                missing.append(f"reveal to {effect.get('m_PlayerRevealTargets')}")
            continue
        if kind == "RandomizeVariableEffectTemplate":
            if effect.get("m_SecondValue") or effect.get("m_MaxValueField"):
                missing.append("random variable with a second value or a dynamic bound")
            continue
        if kind == "ActivateAbilityEffectTemplate":
            if effect.get("m_RandomlyLuckyOrUnlucky"):
                missing.append("random invocation")
            child = _guid(effect.get("m_AbilityToInvoke"))
            if child in _COVERAGE_STACK:
                missing.append("recursive invocation")
            else:
                _COVERAGE_STACK.add(child)
                try:
                    missing.extend(f"invoked: {item}" for item in coverage(child))
                finally:
                    _COVERAGE_STACK.discard(child)
            continue
        if kind in ir_exec_resources.EFFECTS:
            missing.extend(ir_exec_resources.coverage_effect(kind, effect, clause, raw))
            continue
        from rules_port.ir_exec_combat import COMBAT_EFFECTS, coverage_missing as combat_missing
        if kind in COMBAT_EFFECTS:
            missing.extend(combat_missing(kind, clause, raw.get("m_AbilityEffectList") or []))
            continue
        if kind != "CardModifierAbilityEffectTemplate":
            missing.append(f"effect {kind}")
            continue
        modifier = _short((effect.get("m_Modifier") or {}).get("_t"))
        if modifier in ir_exec_resources.MODIFIERS:
            missing.extend(ir_exec_resources.coverage_modifier(modifier, effect, clause, raw))
            continue
        if modifier in ("AttackModifier", "DefenseModifier"):
            stat = effect.get("m_Modifier") or {}
            clause_duration = str(clause.get("m_EffectDuration") or "")
            if clause_duration not in STAT_DURATIONS:
                missing.append(f"stat duration {clause_duration}")
            if stat.get("m_ReplaceExistingValue"):
                missing.append("stat replaces the existing value")
            if str(stat.get("m_Property") or "Unknown") != "Unknown":
                missing.append(f"stat property {stat.get('m_Property')}")
            if _short((stat.get("m_InputValue") or {}).get("_t")) not in ("EffectInputVariable", "EffectConstant"):
                missing.append("stat without a typed amount")
            game_text = str(effect.get("m_GameText") or "").lower()
            if any(phrase in game_text for phrase in DERIVED_STAT_TEXT):
                missing.append("stat derived from another card")
        if modifier in GROUP_G_MODIFIERS:
            missing.extend(modifier_gaps(effect.get("m_Modifier") or {},
                                         str(clause.get("m_EffectDuration") or "")))
        elif modifier not in SUPPORTED_MODIFIERS:
            missing.append(f"modifier {modifier or '(untagged)'}")
        elif modifier == "CounterModifier" and (
                (effect.get("m_Modifier") or {}).get("m_Operation") not in COUNTER_OPERATIONS
                or (effect.get("m_Modifier") or {}).get("m_RemoveAllCounters")):
            missing.append(f"counter operation {(effect.get('m_Modifier') or {}).get('m_Operation')}")
    missing.extend(ir_exec_resources.coverage_invocation_cost(raw))
    return sorted(set(missing))
