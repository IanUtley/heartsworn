"""Ability IR, variable layer.

Each Records ability variable becomes one ``Var``: a short IR kind, the shared
base fields exactly as stored, and the class-specific fields under the IR names
in docs/ABILITY_IR.md. ``to_record`` rebuilds the Records dict. Unknown keys
raise instead of being dropped, so a variable that round-trips has lost nothing.

Stored values are kept verbatim (filters stay as the nested Records objects);
normalising them belongs to a later layer.

Expression texts (``ExpressionAbilityVariable``) parse into a small tree.
Identifiers in an expression name either a variable of the same ability or one
of the static identifiers the client resolves from the source card
(``AbilityInstance.CheckStaticIdentifiers``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Union

RECORD_PREFIX = "Game.Shared.Mechanics.Abilities."

# Base fields every variable may carry. Presence varies by class, so a Var
# records only the keys that were stored.
BASE_KEYS = ("m_Name", "m_DefaultValue", "m_RequiresExplicitSet", "m_RequiresPlayerInput")

# Static identifiers, resolved by the client's AbilityInstance.CheckStaticIdentifiers:
#   ESC  -> the source card's EscalationCount
#   INS  -> the source card's InspireCount (a per-card counter that rises by one
#           each time an inspire triggers; Session.cs adds it, and clears it on
#           the card's specific-duration stats)
#   ABILITYFIRSTOPTION -> the ability's first option
STATIC_NAMES = frozenset({"ESC", "INS", "ABILITYFIRSTOPTION"})

# kind -> (Records class, {IR field: stored key}, implied fields not stored).
KINDS: dict[str, tuple[str, dict[str, str], dict[str, Any]]] = {
    "constant": ("AbilityConstant", {}, {}),
    "variable": ("AbilityVariable", {}, {}),
    "expression": ("ExpressionAbilityVariable", {
        "text": "m_ExpressionText", "dont_recalculate": "m_DontRecalculate",
        "calculated": "m_Calculated"}, {}),
    "card_count": ("CardCountAbilityVariable", {
        "player": "m_PlayerFilter", "filter": "m_CardFilter",
        "zones": "m_CollectionFlags",
        "different_races_for": "m_OnlyIncludeDifferentRacesForFaction",
        "dont_recalculate": "m_DontRecalculate"}, {}),
    "card_property": ("CardPropertyVariable", {"prop": "m_Property"}, {}),
    "list_count": ("CountListAttrAbilityVariable", {
        "list": "m_ListAttrName", "from_source": "m_PullFromSourceCard",
        "filter": "m_CardFilter"}, {}),
    "int_attr": ("IntAttrAbilityVariable", {
        "attr": "m_IntAttrName", "from_source": "m_PullFromSourceCard",
        "half_up": "m_HalfRoundedUp"}, {}),
    "list_sum": ("SumVariableInListAttrCardsAbilityVariable", {
        "list": "m_ListAttrName", "filter": "m_CardFilter",
        "prop": "m_Property"}, {}),
    "ability_property": ("AbilityPropertyVariable", {"prop": "m_Property"}, {}),
    "counter": ("CounterVariable", {
        "counter": "m_CardCounterTemplateId", "player": "m_PlayerFilter",
        "filter": "m_CardFilter", "zones": "m_CollectionFlags",
        "use_highest": "m_UseHighestValue"}, {}),
    "threshold": ("SourcePlayerThresholdAbilityVariable", {
        "colour": "m_Threshold", "unique": "m_CountUniques",
        "dont_recalculate": "m_DontRecalculate"}, {}),
    "card_integer": ("CardIntegerVariable", {}, {}),
    "highest_card": ("HighestCardAbilityVariable", {
        "player": "m_PlayerFilter", "zones": "m_CollectionFlags",
        "filter": "m_CardFilter", "prop": "m_Property"}, {}),
    "event_damage": ("TriggerEventDamageProperty", {}, {}),
    "health": ("SourcePlayerHealthVariable", {}, {}),
    "trigger_source": ("TriggerSourcePropertyVariable", {"prop": "m_Property"}, {}),
    "card_sum": ("CardSumAbilityVariable", {
        "player": "m_PlayerFilter", "zones": "m_CollectionFlags",
        "filter": "m_CardFilter", "prop": "m_Property",
        "attribute": "m_Attribute"}, {}),
    "trigger_target": ("TriggerTargetPropertyVariable", {"prop": "m_Property"}, {}),
    "shards": ("SourcePlayerShardAbilityVariable", {
        "zone": "m_Zone", "different_thresholds": "m_DifferentThresholds"}, {}),
    # The client class reads one fixed player stat; the IR names it.
    "player_stat": ("SourcePlayerBriarLegionVariable", {},
                    {"attr": "BriarLegionsPlayedThisGame"}),
    "resources": ("SourcePlayerResourceAbilityVariable", {
        "temporary": "m_LookUpTemporaryResources"}, {}),
    "charges": ("SourcePlayerChargeVariable", {}, {}),
}

_KIND_BY_CLASS = {cls: kind for kind, (cls, _, _) in KINDS.items()}


class UnknownVariable(ValueError):
    """A Records variable whose class or fields the IR does not describe."""


@dataclass
class Var:
    """One ability variable in IR form."""

    kind: str
    base: dict[str, Any] = field(default_factory=dict)
    fields: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str | None:
        return self.base.get("m_Name")


def from_record(record: dict[str, Any]) -> Var:
    """Read one Records variable into a ``Var``. Raises on anything unknown."""
    stored_type = str(record.get("_t") or "")
    if not stored_type.startswith(RECORD_PREFIX):
        raise UnknownVariable(f"not an ability variable: {stored_type!r}")
    cls = stored_type[len(RECORD_PREFIX):]
    kind = _KIND_BY_CLASS.get(cls)
    if kind is None:
        raise UnknownVariable(f"no IR kind for {cls}")
    _, mapping, _ = KINDS[kind]
    known = set(BASE_KEYS) | set(mapping.values()) | {"_t"}
    extra = sorted(k for k in record if k not in known)
    if extra:
        raise UnknownVariable(f"{cls} stores fields the IR does not map: {extra}")
    base = {key: record[key] for key in BASE_KEYS if key in record}
    fields_ = {ir: record[key] for ir, key in mapping.items() if key in record}
    return Var(kind=kind, base=base, fields=fields_)


def to_record(var: Var) -> dict[str, Any]:
    """Rebuild the Records dict for a ``Var``. Raises on fields the kind does not take."""
    if var.kind not in KINDS:
        raise UnknownVariable(f"unknown IR kind {var.kind!r}")
    cls, mapping, _ = KINDS[var.kind]
    extra = sorted(set(var.fields) - set(mapping))
    if extra:
        raise UnknownVariable(f"{var.kind} takes no field(s) {extra}")
    record: dict[str, Any] = {"_t": RECORD_PREFIX + cls}
    record.update(var.base)
    for ir, key in mapping.items():
        if ir in var.fields:
            record[key] = var.fields[ir]
    return record


# --- Expressions -------------------------------------------------------------

@dataclass(frozen=True)
class Num:
    value: int


@dataclass(frozen=True)
class Ref:
    name: str


@dataclass(frozen=True)
class Neg:
    operand: "Expr"


@dataclass(frozen=True)
class Bin:
    op: str
    left: "Expr"
    right: "Expr"


Expr = Union[Num, Ref, Neg, Bin]

_TOKEN = re.compile(r"\s*(?:(\d+)|([A-Za-z_][A-Za-z_0-9]*)|(\S))")
_PRECEDENCE = {"+": 1, "-": 1, "*": 2, "/": 2}


class ExpressionError(ValueError):
    """An expression text the grammar does not accept."""


def _tokens(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    text = text.rstrip()
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match:
            raise ExpressionError(f"cannot tokenise {text!r} at {pos}")
        number, ident, symbol = match.groups()
        if number is not None:
            out.append(("num", number))
        elif ident is not None:
            out.append(("ident", ident))
        else:
            if symbol not in "+-*/()":
                raise ExpressionError(f"unexpected {symbol!r} in {text!r}")
            out.append(("sym", symbol))
        pos = match.end()
    return out


def parse_expression(text: str) -> Expr:
    """Parse an ``ExpressionAbilityVariable`` text into an expression tree."""
    tokens = _tokens(text)
    index = 0

    def peek() -> tuple[str, str] | None:
        return tokens[index] if index < len(tokens) else None

    def take() -> tuple[str, str]:
        nonlocal index
        token = peek()
        if token is None:
            raise ExpressionError(f"unexpected end of {text!r}")
        index += 1
        return token

    def factor() -> Expr:
        kind, value = take()
        if kind == "num":
            return Num(int(value))
        if kind == "ident":
            return Ref(value)
        if (kind, value) == ("sym", "-"):
            nxt = peek()
            if nxt is not None and nxt[0] == "num":
                take()
                return Num(-int(nxt[1]))
            return Neg(factor())
        if (kind, value) == ("sym", "("):
            inner = expr(0)
            if take() != ("sym", ")"):
                raise ExpressionError(f"missing ')' in {text!r}")
            return inner
        raise ExpressionError(f"unexpected {value!r} in {text!r}")

    def expr(min_precedence: int) -> Expr:
        left = factor()
        while True:
            token = peek()
            if token is None or token[0] != "sym" or token[1] not in _PRECEDENCE:
                return left
            op = token[1]
            precedence = _PRECEDENCE[op]
            if precedence < min_precedence:
                return left
            take()
            right = expr(precedence + 1)
            left = Bin(op, left, right)

    tree = expr(0)
    if index != len(tokens):
        raise ExpressionError(f"trailing tokens in {text!r}")
    return tree


def emit_expression(tree: Expr) -> str:
    """Render an expression tree in the spacing Records uses (``a * -1``, ``10 - b``)."""
    if isinstance(tree, Num):
        return str(tree.value)
    if isinstance(tree, Ref):
        return tree.name
    if isinstance(tree, Neg):
        inner = emit_expression(tree.operand)
        return f"-{inner}" if isinstance(tree.operand, (Num, Ref)) else f"-({inner})"

    def side(child: Expr, parent_op: str, right: bool) -> str:
        text = emit_expression(child)
        if isinstance(child, Bin):
            child_p = _PRECEDENCE[child.op]
            parent_p = _PRECEDENCE[parent_op]
            if child_p < parent_p or (right and child_p == parent_p):
                return f"({text})"
        return text

    return f"{side(tree.left, tree.op, False)} {tree.op} {side(tree.right, tree.op, True)}"


def expression_refs(tree: Expr) -> set[str]:
    """Every identifier an expression reads."""
    if isinstance(tree, Ref):
        return {tree.name}
    if isinstance(tree, Neg):
        return expression_refs(tree.operand)
    if isinstance(tree, Bin):
        return expression_refs(tree.left) | expression_refs(tree.right)
    return set()


def unresolved_refs(variables: list[Var]) -> set[str]:
    """Identifiers in an ability's expressions that name neither a variable
    of the ability nor a static identifier."""
    declared = {var.name for var in variables if var.name}
    missing: set[str] = set()
    for var in variables:
        if var.kind != "expression":
            continue
        tree = parse_expression(str(var.fields.get("text") or ""))
        missing |= expression_refs(tree) - declared - STATIC_NAMES
    return missing


# --- Targets -----------------------------------------------------------------
#
# Each AbilityTargetTemplate record's class is its selection rule (the 15
# classes under Game.Shared.Mechanics.Abilities.TargetTemplates), so each class
# is an IR kind with its own constraint fields. Shared fields are listed in
# TARGET_BASE. ``_v`` (the serializer's type table) is carried in ``meta``.

TARGET_PREFIX = "Game.Shared.Mechanics.Abilities.TargetTemplates."

TARGET_BASE: dict[str, str] = {
    "m_TemplateId": "id", "m_Name": "name", "m_GameText": "text",
    "m_IsAutoTarget": "auto", "m_IsRandomTarget": "random",
    "m_Optional": "optional", "m_Explicit": "explicit",
    "m_AllowBestEffortMinimumTargetCount": "best_effort_minimum",
    "m_PlayerFilter": "player", "m_CollectionFlags": "zones",
    "m_CardFilter": "filter",
    "m_MinTargetCount": "min", "m_MaxTargetCount": "max",
    "m_MinimumTargetCount_DEPRECATED": "min_deprecated",
    "m_MaximumTargetCount_DEPRECATED": "max_deprecated",
}

# kind -> (Records class, {IR field: stored key} for the class's own fields).
TARGET_KINDS: dict[str, tuple[str, dict[str, str]]] = {
    "card": ("AbilityTargetTemplate", {}),
    "source_card": ("AbilitySourceCardTargetTemplate",
                    {"choice_override": "m_ChoiceOverride"}),
    "player": ("PlayerTargetTemplate", {}),
    "source_drawn": ("SourceDrawnTargetTemplate", {}),
    "match_secondary": ("MatchSecondaryTargetTemplate", {
        "same_cost": "m_SameCost", "same_owner": "m_SameOwner",
        "shares_race": "m_SharesRace", "same_name": "m_SameName",
        "doesnt_share_race": "m_DoesntShareRace",
        "cant_be_previous": "m_CantBePreviousTarget"}),
    "trigger_card": ("AbilityTriggerCardTargetTemplate",
                     {"selector": "m_TriggerSelector"}),
    "secondary": ("SecondaryTargetTemplate", {}),
    "source_revealed": ("SourceRevealedTargetTemplate",
                        {"ignore_acted_on": "m_IgnoreActedOn"}),
    "source_stored": ("SourceStoredTargetTemplate", {
        "in_ability": "m_StoredInAbility", "in_card": "m_StoredInCard"}),
    "created": ("AbilityCreatedTargetTemplate", {}),
    "source_buried": ("SourceBuriedTargetTemplate", {"list": "m_ListAttrName"}),
    "player_or_stuff": ("TargetsAPlayerOrHisStuff", {}),
    "voided": ("VoidedTargetTemplate", {"voided_by": "m_VoidedBy"}),
    "shared_name": ("SharedNameTargetTemplate", {}),
    "duplicate": ("DuplicateCardTargetTemplate", {}),
}

_TARGET_KIND_BY_CLASS = {cls: kind for kind, (cls, _) in TARGET_KINDS.items()}


@dataclass
class Target:
    """One target template in IR form.

    ``fields`` holds the IR fields that were stored. A count is ``None``,
    ``("const", n)`` or ``("var", ability_variable_name)``. ``meta`` holds
    serializer data (``_v``) that is carried verbatim.
    """

    kind: str
    fields: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


def _count_from_record(value: Any) -> Any:
    if value is None:
        return None
    stored_type = str(value.get("_t") or "")
    if stored_type == TARGET_PREFIX + "TargetConstant" and set(value) == {"_t", "m_Value"}:
        return ("const", value["m_Value"])
    if stored_type == TARGET_PREFIX + "TargetVariable" and set(value) == {"_t", "m_AbilityVariableName"}:
        return ("var", value["m_AbilityVariableName"])
    raise UnknownVariable(f"unknown count object {stored_type!r}")


def _count_to_record(count: Any) -> Any:
    if count is None:
        return None
    kind, value = count
    if kind == "const":
        return {"_t": TARGET_PREFIX + "TargetConstant", "m_Value": value}
    if kind == "var":
        return {"_t": TARGET_PREFIX + "TargetVariable", "m_AbilityVariableName": value}
    raise UnknownVariable(f"unknown count kind {kind!r}")


def target_from_record(record: dict[str, Any]) -> Target:
    """Read one target template record into a ``Target``. Raises on anything unknown."""
    stored_type = str(record.get("_t") or "")
    if not stored_type.startswith(TARGET_PREFIX):
        raise UnknownVariable(f"not a target template: {stored_type!r}")
    cls = stored_type[len(TARGET_PREFIX):]
    kind = _TARGET_KIND_BY_CLASS.get(cls)
    if kind is None:
        raise UnknownVariable(f"no IR kind for target class {cls}")
    own = TARGET_KINDS[kind][1]
    known = set(TARGET_BASE) | set(own.values()) | {"_t", "_v"}
    extra = sorted(k for k in record if k not in known)
    if extra:
        raise UnknownVariable(f"{cls} stores fields the IR does not map: {extra}")
    fields_: dict[str, Any] = {}
    for stored_key, ir in TARGET_BASE.items():
        if stored_key in record:
            value = record[stored_key]
            if ir in ("min", "max"):
                value = _count_from_record(value)
            fields_[ir] = value
    for ir, stored_key in own.items():
        if stored_key in record:
            fields_[ir] = record[stored_key]
    meta = {"_v": record["_v"]} if "_v" in record else {}
    return Target(kind=kind, fields=fields_, meta=meta)


def target_to_record(target: Target) -> dict[str, Any]:
    """Rebuild the Records dict for a ``Target``."""
    if target.kind not in TARGET_KINDS:
        raise UnknownVariable(f"unknown target kind {target.kind!r}")
    cls, own = TARGET_KINDS[target.kind]
    known_ir = set(TARGET_BASE.values()) | set(own)
    extra = sorted(set(target.fields) - known_ir)
    if extra:
        raise UnknownVariable(f"{target.kind} takes no field(s) {extra}")
    record: dict[str, Any] = {"_t": TARGET_PREFIX + cls}
    for stored_key, ir in TARGET_BASE.items():
        if ir in target.fields:
            value = target.fields[ir]
            if ir in ("min", "max"):
                value = _count_to_record(value)
            record[stored_key] = value
    for ir, stored_key in own.items():
        if ir in target.fields:
            record[stored_key] = target.fields[ir]
    record.update(target.meta)
    return record
