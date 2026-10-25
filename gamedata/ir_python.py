"""The ability IR as Python text: constructor calls of the generated mechanics classes.

``dumps(obj)`` writes a typed object as one expression: ``ClassName(snake_field=value, ...)``. A field
that holds its client default is left out, and the constructor fills it back in. A stored object whose
``_t`` differs from its class's type name keeps it as ``_t="..."``; removed or renamed fields a record
still carries are written as ``_deprecated={...}``.

``loads(text)`` reads that text back. It does not run it: the text is parsed with ``ast``, and only
these are allowed: the generated class names as calls, keyword arguments, literal numbers, strings,
booleans, ``None``, lists, tuples and dicts, and names bound earlier in the same text. Anything else
raises ``ValueError``, so text from an editor can be loaded safely.
"""

from __future__ import annotations

import ast
import operator
from typing import Any

from gamedata import ir_registry, ir_surface, mechanics_types
from gamedata.mechanics_base import Typed, _argument_names, _default_of, _record_names, _record_value

_RESERVED = ("_t", "_deprecated")
_ZERO_GUID = "00000000-0000-0000-0000-000000000000"


def _ref(guid: str) -> dict[str, Any]:
    """A reference to a library record by GUID (the Records' ``{"m_Guid": ...}``)."""
    return {"m_Guid": str(guid).lower()}


def _resource(guid: str) -> dict[str, Any]:
    """A counter or shard reference as a typed ResourceId, where the Records tag it."""
    return {"_t": "Game.Shared.ResourceId", "m_Guid": str(guid).lower()}


def _clause(**fields: Any) -> dict[str, Any]:
    """One effect-list entry. Its fields the text leaves out take the Records defaults. An effect
    template may be given as the object itself; the clause then uses its template id."""
    if isinstance(fields.get("effect_template_id"), Typed):
        fields["effect_template_id"] = fields["effect_template_id"].to_record()["m_TemplateId"]
    record = {
        "m_EffectTemplateId": None, "m_TargetTemplateIndex": 0, "m_EffectInstanceId": 0,
        "m_EffectDuration": "Instant", "m_OutputVariables": {}, "m_ContingentEffectInstanceId": -1,
        "m_IsOptional": 0, "m_ConditionId": _ref(_ZERO_GUID), "m_EffectGroupId": 1,
        "m_VfxProjectileType": "None", "m_VfxEventType": "None",
        "m_VfxTargetSelectionType": "Targetable", "m_RecalculateTargets": "True",
        "m_Layer": 0, "m_SecondaryTargetIndex": -1,
    }
    for name, value in fields.items():
        key = "m_" + "".join(part[:1].upper() + part[1:] for part in name.split("_"))
        if key not in record:
            raise ValueError(f"Clause has no field {name}")
        record[key] = value
    if record["m_EffectTemplateId"] is None:
        raise ValueError("Clause needs effect_template_id")
    return record


# The helpers the IR text may call besides the generated classes: each returns a Records value.
def _named(name: str) -> dict[str, Any]:
    """A template registered under a name (by an earlier card or line): the same Ref as its GUID."""
    return ir_registry.ref_of(name)


HELPERS: dict[str, Any] = {"Ref": _ref, "Resource": _resource, "Clause": _clause, "Named": _named}
# The surface's factories (counter, count, effect, ability, ...) are helpers too.
HELPERS.update(ir_surface.FACTORIES)


# Trigger events a card can name: <Event>Type is the trigger's type, with no internal names to write.
EVENT_NAMES = (
    "TurnStartedEvent", "TurnEndedEvent", "CardEnteredZoneEvent", "CardExitedZoneEvent",
    "CardDealtDamageEvent", "CardAttackedEvent", "CardCastEvent", "CardDrawnEvent", "CardCreatedEvent",
    "CardReadiedEvent", "CardDestroyedEvent", "CardSacrificedEvent", "DamagePreventedEvent",
    "GameStartedEvent", "PreGameEvent",
)

# Library targets a card can name without a GUID.
TARGETS = {"THIS_CARD_TARGET": "190a4d8c-7c2c-10d0-6429-99c5aeb0791f"}


def prelude() -> dict[str, Any]:
    """The names a card file can use with no import line: every generated mechanics class, the helpers,
    the trigger types (``TurnStartedEventType``) and the library targets. Card text is checked against
    this set; nothing else is in scope."""
    names: dict[str, Any] = {name: value for name, value in vars(mechanics_types).items()
                             if isinstance(value, type) and issubclass(value, Typed) and value is not Typed}
    names.update(HELPERS)
    names.update(ir_surface.CONSTANTS)
    for event in EVENT_NAMES:
        names[f"{event}Type"] = {"m_InternalType": f"Game.Shared.Mechanics.{event}"}
    for name, guid in TARGETS.items():
        names[name] = _ref(guid)
    return names


def _class_for(name: str) -> type:
    cls = getattr(mechanics_types, name, None)
    if not (isinstance(cls, type) and issubclass(cls, Typed)):
        raise ValueError(f"{name} is not a generated mechanics class")
    return cls


def _literal(value: Any) -> str:
    if isinstance(value, Typed):
        return dumps(value)
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        # A GUID reference and a typed ResourceId are written with their helpers, which build the same value.
        if set(value) == {"m_Guid"}:
            return f"Ref({value['m_Guid']!r})"
        if set(value) == {"_t", "m_Guid"} and value["_t"] == "Game.Shared.ResourceId":
            return f"Resource({value['m_Guid']!r})"
        return "{" + ", ".join(f"{_literal(key)}: {_literal(item)}" for key, item in value.items()) + "}"
    raise ValueError(f"cannot write {type(value).__name__} as IR text")


def dumps(obj: Typed) -> str:
    """One typed object as a constructor expression, with its non-default fields."""
    cls = type(obj)
    arguments: list[str] = []
    stored = getattr(obj, "_stored_type", cls.type_name)
    if stored != cls.type_name:
        arguments.append(f"_t={stored!r}")
    for argument, record_name in _argument_names(cls).items():
        value = getattr(obj, record_name)
        if _record_value(value) == _record_value(_default_of(cls, record_name)):
            continue
        arguments.append(f"{argument}={_literal(value)}")
    deprecated = {name: getattr(obj, name) for name in getattr(obj, "_record_order", [])
                  if name not in _record_names(cls) and name not in ("_t", "_v") and hasattr(obj, name)}
    if deprecated:
        arguments.append("_deprecated={" + ", ".join(
            f"{name!r}: {_literal(value)}" for name, value in deprecated.items()) + "}")
    return f"{cls.__name__}({', '.join(arguments)})"


class _Checker(ast.NodeVisitor):
    """Accepts only the allowed syntax, and records which names the text binds."""

    def __init__(self) -> None:
        self.bound: set[str] = set()

    def generic_visit(self, node: ast.AST) -> None:
        allowed = (ast.Module, ast.Expr, ast.Assign, ast.Name, ast.Load, ast.Store, ast.Call, ast.keyword,
                   ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.UnaryOp, ast.USub, ast.UAdd,
                   ast.Compare, ast.GtE, ast.Gt, ast.LtE, ast.Lt, ast.Eq)
        if not isinstance(node, allowed):
            raise ValueError(f"{type(node).__name__} is not allowed in IR text")
        super().generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if not isinstance(node.func, ast.Name):
            raise ValueError("only generated classes can be called in IR text")
        if node.func.id not in HELPERS:
            _class_for(node.func.id)
            if node.args:
                raise ValueError(f"{node.func.id} takes keyword arguments only")
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.bound.add(node.id)
        elif node.id not in self.bound and node.id not in _PRELUDE:
            raise ValueError(f"{node.id} is not defined in the IR text")


def _value(node: ast.AST, names: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.operand, ast.Constant):
        return -node.operand.value if isinstance(node.op, ast.USub) else node.operand.value
    if isinstance(node, ast.Name):
        return names[node.id] if node.id in names else _PRELUDE[node.id]
    if isinstance(node, ast.Compare):
        # A comparison of a surface count with a number: ``count(...) >= 1``.
        if len(node.ops) != 1:
            raise ValueError("a comparison takes one operator")
        ops = {ast.GtE: operator.ge, ast.Gt: operator.gt, ast.LtE: operator.le, ast.Lt: operator.lt,
               ast.Eq: operator.eq}
        left = _value(node.left, names)
        if not hasattr(left, "compare"):
            raise ValueError("only a count can be compared in IR text")
        return left.compare(ops[type(node.ops[0])], _value(node.comparators[0], names))
    if isinstance(node, ast.List):
        return [_value(item, names) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_value(item, names) for item in node.elts)
    if isinstance(node, ast.Dict):
        return {_value(key, names): _value(item, names) for key, item in zip(node.keys, node.values)}
    if isinstance(node, ast.Call):
        arguments = {}
        for keyword in node.keywords:
            if keyword.arg is None:
                raise ValueError("** is not allowed in IR text")
            arguments[keyword.arg] = _value(keyword.value, names)
        if node.func.id == "Named":
            # A name bound earlier in this text, else a name another card registered (its qualified name).
            name = node.args[0].value
            if name in names and isinstance(names[name], Typed):
                return _ref_of_template(names[name])
            return ir_registry.ref_of(name)
        if node.func.id in HELPERS:
            return HELPERS[node.func.id](**arguments) if node.func.id == "Clause" else \
                HELPERS[node.func.id](*[_value(arg, names) for arg in node.args], **arguments)
        return _construct(_class_for(node.func.id), arguments)
    raise ValueError(f"{type(node).__name__} is not allowed in IR text")


def _construct(cls: type, arguments: dict[str, Any]) -> Typed:
    stored_type = arguments.pop("_t", None)
    deprecated = arguments.pop("_deprecated", {})
    known = _argument_names(cls)
    unknown = sorted(set(arguments) - set(known))
    if unknown:
        raise ValueError(f"{cls.__name__} has no field {', '.join(unknown)}")
    obj = cls(**arguments)
    for name, value in dict(deprecated).items():
        setattr(obj, name, value)
    if deprecated:
        # A deprecated field is part of the record, after the current fields.
        obj._record_order = [*_record_names(cls), *dict(deprecated)]
    if stored_type is not None:
        obj._stored_type = stored_type
    return obj


_PRELUDE: dict[str, Any] = {}


def _prelude_names() -> None:
    """The prelude is read once, so every name a card uses is known before it is checked."""
    if not _PRELUDE:
        _PRELUDE.update(prelude())


def _ref_of_template(value: Typed) -> dict[str, Any]:
    """The GUID reference of a template object: its ability id, or its effect or target template id."""
    record = value.to_record()
    for key in ("m_AbilityTemplateId", "m_TemplateId"):
        if isinstance(record.get(key), dict) and record[key].get("m_Guid"):
            return {"m_Guid": record[key]["m_Guid"]}
    raise ValueError(f"{type(value).__name__} has no template id to refer to")


def _register_template(name: str, value: Any, scope: str | None) -> None:
    """A template bound to a name is registered under that name (qualified by the card's scope, when there
    is one), with its GUID."""
    name = f"{scope}.{name}" if scope else name
    if not isinstance(value, Typed):
        return
    record = value.to_record()
    for key, kind in (("m_AbilityTemplateId", "ability"), ("m_TemplateId", "template")):
        guid = (record.get(key) or {}).get("m_Guid") if isinstance(record.get(key), dict) else None
        if guid:
            ir_registry.register(name, guid, kind)
            return


def loads(text: str, scope: str | None = None) -> dict[str, Any]:
    """The names the text binds, each a typed object or a literal. Raises ValueError on anything the
    IR text does not allow."""
    _prelude_names()
    tree = ast.parse(text, mode="exec")
    checker = _Checker()
    checker.visit(tree)
    names: dict[str, Any] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
                raise ValueError("an IR assignment binds one name")
            name = statement.targets[0].id
            names[name] = _value(statement.value, names)
            _register_template(name, names[name], scope)
        elif isinstance(statement, ast.Expr):
            _value(statement.value, names)
        else:
            raise ValueError(f"{type(statement).__name__} is not allowed in IR text")
    return names
