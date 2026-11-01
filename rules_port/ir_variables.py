"""Ability variables the interpreter computes from an expression or from the source card's own stat (C#
ExpressionAbilityVariable and CardPropertyVariable). ``ir_exec._bind_variables`` calls ``bind_expressions`` and
``card_property_value``; ``ir_exec.coverage`` calls ``expression_gaps`` and ``card_property_gaps``.

An expression is the Records' formula text: integer literals, ``+ - * /``, parentheses, unary signs and the names of
the ability's other variables. It is worked out in floating point and truncated, as the native resolver does
(``static_rules._expression_value``), and a division by zero, or a text that does not parse, takes the default."""

from __future__ import annotations

import ast
from typing import Any, Callable

CARD_PROPERTY_VARIABLE = "CardPropertyVariable"
EXPRESSION_VARIABLE = "ExpressionAbilityVariable"
ABILITY_PROPERTY_VARIABLE = "AbilityPropertyVariable"
HIGHEST_CARD_VARIABLE = "HighestCardAbilityVariable"
THRESHOLD_VARIABLE = "SourcePlayerThresholdAbilityVariable"
VARIABLE_KINDS = {CARD_PROPERTY_VARIABLE, EXPRESSION_VARIABLE, ABILITY_PROPERTY_VARIABLE, HIGHEST_CARD_VARIABLE,
                  THRESHOLD_VARIABLE}

# The card properties the native resolver reads for the source card (static_rules._card_property_value).
CARD_PROPERTIES = {"CurrentAttackValue", "CurrentDefenseValue", "ResourceCostTrue"}

# A name an expression may use without an ability variable of that name: the source's escalation count.
ESCALATION_NAME = "ESC"
# The source card's inspire count, as the native resolver names it (C# AbilityInstance.CheckStaticIdentifiers "INS").
INSPIRE_NAME = "INS"
# The names an expression may use without declaring them (bound from the source card, not from the ability).
STATIC_NAMES = {ESCALATION_NAME, INSPIRE_NAME}

# C# int.MinValue: what a highest-card variable gives when no card passes its filter.
_NO_CARD = -2147483648

_OPERATORS = (ast.Add, ast.Sub, ast.Mult, ast.Div)


def _parse(text: str) -> ast.expr | None:
    """The expression's syntax tree, or None when the text is empty or is not a Python arithmetic expression."""
    if not text or not text.strip():
        return None
    try:
        return ast.parse(text.strip(), mode="eval").body
    except SyntaxError:
        return None


def _names(tree: ast.expr) -> set[str]:
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def _supported(tree: ast.expr) -> bool:
    """Whether every node of the tree is an integer literal, a name, a sign or one of the four operators."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            if type(node.value) is not int:
                return False
        elif isinstance(node, ast.BinOp):
            if not isinstance(node.op, _OPERATORS):
                return False
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.UAdd, ast.USub)):
                return False
        elif not isinstance(node, (ast.Name, ast.expr_context, ast.operator, ast.unaryop)):
            return False
    return True


def expression_gaps(expressions: dict[str, str], declared: set[str]) -> list[str]:
    """What stops the expression variables of an ability from running: a text outside the supported arithmetic, a
    name that no variable of the ability declares, or a name that leads back to its own variable."""
    gaps: list[str] = []
    graph: dict[str, set[str]] = {}
    for name, text in expressions.items():
        if not (text or "").strip():
            continue
        tree = _parse(text)
        if tree is None or not _supported(tree):
            gaps.append(f"expression {text}")
            continue
        for used in _names(tree):
            if used not in declared and used not in STATIC_NAMES:
                gaps.append(f"expression name {used}")
        graph[name] = _names(tree) & set(expressions)
    for name in graph:
        if _leads_back(name, graph):
            gaps.append(f"expression cycle on {name}")
    return list(dict.fromkeys(gaps))


def _leads_back(start: str, graph: dict[str, set[str]]) -> bool:
    """Whether the variable ``start`` depends, through other expression variables, on itself."""
    stack = list(graph.get(start, ()))
    seen: set[str] = set()
    while stack:
        name = stack.pop()
        if name == start:
            return True
        if name not in seen:
            seen.add(name)
            stack.extend(graph.get(name, ()))
    return False


def card_property_gaps(variable: dict[str, Any]) -> list[str]:
    """What stops a CardPropertyVariable from running: a property the native resolver does not read for a card,
    or a value the player is asked for."""
    gaps = []
    if str(variable.get("m_Property") or "") not in CARD_PROPERTIES:
        gaps.append(f"card property {variable.get('m_Property')}")
    if variable.get("m_RequiresPlayerInput") or variable.get("m_RequiresExplicitSet"):
        gaps.append("card property the player sets")
    return gaps


def card_property_value(scope: Any, variable: Any) -> int:
    """The source card's stat or cost now, as the native resolver reads it (C# CardPropertyVariable.GetValue)."""
    from rules_port.static_rules import _card_property_value

    context = scope.context
    value = _card_property_value(context.db, context.session.session_id, context.bstate,
                                 scope.source_uid, str(variable.m_Property or ""))
    return int(value or 0)


def ability_property_gaps(variable: dict[str, Any]) -> list[str]:
    """What stops an AbilityPropertyVariable from running: a property other than the X cost, or a player's choice."""
    gaps = []
    if str(variable.get("m_Property") or "") != "AbilityResourceXCost":
        gaps.append(f"ability property {variable.get('m_Property')}")
    if variable.get("m_RequiresPlayerInput") or variable.get("m_RequiresExplicitSet"):
        gaps.append("ability property the player sets")
    return gaps


def ability_property_value(scope: Any) -> int:
    """The X cost paid for the spell or ability now (C# AbilityPropertyVariable with AbilityResourceXCost)."""
    return int(scope.context.bstate.get("x_cost", 0) or 0)


def highest_card_gaps(variable: dict[str, Any]) -> list[str]:
    """What stops a HighestCardAbilityVariable from running: a property the native resolver does not read for a card,
    or a value the player is asked for."""
    gaps = []
    if str(variable.get("m_Property") or "") not in CARD_PROPERTIES:
        gaps.append(f"highest card property {variable.get('m_Property')}")
    if variable.get("m_RequiresPlayerInput") or variable.get("m_RequiresExplicitSet"):
        gaps.append("highest card the player sets")
    return gaps


def highest_card_value(scope: Any, variable: Any) -> int:
    """The highest stat or cost among the cards its zones and filter pass, from the player the variable names
    (native ``static_rules._highest_card_variable``); the default when the variable names no cards to read."""
    from rules_port.static_rules import _variable_card_candidates, effective_cost, effective_stats

    context = scope.context
    session_id = context.session.session_id
    cards = _variable_card_candidates(context.db, session_id, context.bstate, scope.source_uid, scope.owner_uid,
                                      variable.to_record(), self_owner="source")
    if cards is None:
        return int(variable.m_DefaultValue or 0)
    if not cards:
        return _NO_CARD
    prop = str(variable.m_Property or "").lower()
    if prop in {"currentattackvalue", "attack", "cardattack"}:
        values = [effective_stats(context.db, session_id, context.bstate, int(card["card_uid"]))[0] for card in cards]
    elif prop in {"currentdefensevalue", "defense", "carddefense"}:
        values = [effective_stats(context.db, session_id, context.bstate, int(card["card_uid"]))[1] for card in cards]
    else:
        values = [effective_cost(context.db, session_id, context.bstate, int(card["card_uid"])) for card in cards]
    return int(max(values))


def threshold_gaps(variable: dict[str, Any]) -> list[str]:
    """What stops a SourcePlayerThresholdAbilityVariable from running: a value the player is asked for."""
    if variable.get("m_RequiresPlayerInput") or variable.get("m_RequiresExplicitSet"):
        return ["threshold the player sets"]
    return []


def threshold_value(scope: Any, variable: Any) -> int:
    """The shards the source player holds of the named threshold, counted as the native resolver counts them
    (``static_rules._source_player_threshold``)."""
    from rules_port.static_rules import _source_player_threshold

    return int(_source_player_threshold(scope.context.bstate, scope.owner_uid, variable.to_record()) or 0)


def uses_static(expressions: dict[str, Any], name: str) -> bool:
    """Whether any expression variable names the source card's ``name`` (ESC or INS)."""
    return any(name in _names(tree)
               for variable in expressions.values() if not _is_threshold(variable)
               for tree in [_parse(variable.m_ExpressionText or "")] if tree is not None)


def _is_threshold(variable: Any) -> bool:
    return str(getattr(variable, "type_name", "")).endswith(THRESHOLD_VARIABLE)


def evaluate(text: str, lookup: Callable[[str], int]) -> float:
    """The value of an expression, each name read through ``lookup``. Raises ArithmeticError or ValueError when the
    text cannot be worked out; the caller then takes the default."""
    tree = _parse(text)
    if tree is None:
        raise ValueError(f"no expression in {text!r}")
    return _value(tree, lookup)


def _value(node: ast.expr, lookup: Callable[[str], int]) -> float:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return lookup(node.id)
    if isinstance(node, ast.UnaryOp):
        operand = _value(node.operand, lookup)
        return operand if isinstance(node.op, ast.UAdd) else -operand
    if isinstance(node, ast.BinOp):
        left = _value(node.left, lookup)
        right = _value(node.right, lookup)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        return left / right
    raise ValueError(f"unsupported expression node {type(node).__name__}")


def inspire_value(scope: Any) -> int:
    """The source card's inspire count now, as the native resolver reads ``INS`` (its CardStatsWithSpecificDuration
    InspireCount)."""
    from rules_port.statistics import tac_stat

    context = scope.context
    value = tac_stat(context.bstate or {}, "cards", scope.source_uid,
                     "CardStatsWithSpecificDuration", "InspireCount", default=None)
    return int(value if value is not None else 0)


def escalation_value(scope: Any) -> int:
    """The source card's escalation count now, as the native resolver reads ``ESC``."""
    from rules_port.statistics import card_escalation_count

    context = scope.context
    return int(card_escalation_count(context.db, context.session.session_id, context.bstate, scope.source_uid))


def bind_expressions(variables: dict[str, Any], values: dict[str, int], wanted: set[str] | None = None,
                     threshold: Callable[[Any], int] | None = None) -> None:
    """Bind each expression variable (``variables``: name -> the typed variable) into ``values``. An expression can
    name another expression variable, so each is worked out when first read. A variable with Don't recalculate is
    worked out once, the first time a clause names it (``wanted``; None means every clause), and then keeps that
    value, as the native cache does. A threshold variable is worked out by ``threshold`` in the same way."""
    fresh: dict[str, int] = {}

    def value_of(name: str, stack: frozenset[str]) -> int:
        if name in fresh:
            return fresh[name]
        variable = variables.get(name)
        if variable is None:
            return values[name]
        if variable.m_DontRecalculate and name in values:
            return values[name]
        if name in stack:
            raise NotImplementedError(f"expression cycle on {name}")
        if _is_threshold(variable):
            fresh[name] = int(threshold(variable))
            return fresh[name]
        default = int(variable.m_DefaultValue or 0)
        try:
            computed = int(evaluate(variable.m_ExpressionText or "",
                                    lambda used: value_of(used, stack | {name})))
        except (ArithmeticError, ValueError):
            computed = default
        fresh[name] = computed
        return computed

    for name, variable in variables.items():
        if variable.m_DontRecalculate and (name in values or (wanted is not None and name not in wanted)):
            continue
        values[name] = value_of(name, frozenset())
