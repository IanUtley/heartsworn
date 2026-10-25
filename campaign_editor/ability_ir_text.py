"""The ability IR as code: a document that prints and parses back to the same Records.

An ability is written as declarations, then its root::

    variable Two = 2
    variable RandomNumber = variable.variable(_base={"m_DefaultValue": -1})
    target T0 = target.card(...)
    condition C0 = condition.ability_effect_condition_template(...)
    effect E0 = effect.activate(...)
    root.ability_template(
      ability_effect_list=[
        clause(effect=E0, target_template_index=0, condition=C0, ...),
      ],
      ability_target_template_ids=[T0],
      ...
    )

A variable is declared with its Records name. A constant is ``variable Two = 2``. Targets, effects and
conditions are declared once each with a name (``T0``, ``E0``, ``C0``, in order of first use), and the
root refers to them by name. Inside an effect, ``$name`` is an input, ``@name`` an ability variable, and
``effect_constant(value=N)`` a constant. Every other object is a call: ``layer.kind(field=value, ...)``.
A value is a string, an integer, ``null``, a list, a plain object, or one of those calls; ``ref("guid")``,
``none``, ``tac({...})``, ``event("class")``, ``raw(value)``. ``_meta=`` and ``_base=`` keywords carry a
stored version tag and a variable's base values.

``ability_code`` writes an ability from the Records by GUID; ``loads_ability`` reads the text back into the
Records it stands for. ``dumps`` and ``loads`` are the same pair for one IR object.
"""

from __future__ import annotations

import functools
import json
import re
from typing import Any

import ability_ir
import ability_ir_clauses as irc
import ability_ir_conditions as cond
import ability_ir_effects as ef
import ability_ir_filters as irf
import ability_ir_root as rt
import ability_ir_vocab
import card_abilities
from ability_ir import Raw, Tac, TriggerEvent
from ability_ir_effects import Node

ROOT_CLASS = "AbilityTemplate"
ROOT_RECORD = "Reckoning.Game.AbilityTemplate"
MODIFIER_PREFIX = ef.MODIFIER_PREFIX
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
DECLARATIONS = ("variable", "target", "effect", "condition")


class TextError(ValueError):
    """Text the IR syntax does not accept, or an object it cannot print."""


def _node_layer(record_type: str) -> str:
    short = record_type.rsplit(".", 1)[-1]
    if short == ROOT_CLASS:
        return "root"
    if record_type.startswith(MODIFIER_PREFIX):
        return "modifier"
    if ef.is_effect_type(record_type):
        return "effect"
    return "condition"


@functools.lru_cache(maxsize=1)
def _tables() -> dict[tuple[str, str], str]:
    """``(layer, kind) -> Records class`` for every name the code prints for a call."""
    to_class: dict[tuple[str, str], str] = {}
    for node in ability_ir_vocab._nodes():
        record_type = node["class"]
        if record_type.startswith(ability_ir.TARGET_PREFIX) or record_type.startswith(irf.FILTER_PREFIX):
            continue
        if record_type.startswith(ability_ir.RECORD_PREFIX) and \
                record_type.rsplit(".", 1)[-1] in ability_ir._KIND_BY_CLASS:
            continue
        key = (_node_layer(record_type), ef.kind_of(record_type))
        if key in to_class and to_class[key] != record_type:
            raise TextError(f"two classes share the name {key}")
        to_class[key] = record_type
    return to_class


def _name_of(layer: str, record_type: str) -> str:
    for (lay, kind), cls in _tables().items():
        if lay == layer and cls == record_type:
            return f"{lay}.{kind}"
    raise TextError(f"no printed name for {record_type} in layer {layer}")


def _name_text(name: str) -> str:
    return name if IDENTIFIER.match(name) else json.dumps(name)


def _call(name: str, args: list[tuple[str | None, str]]) -> str:
    if not args:
        return f"{name}()"
    parts = [f"{key}={text}" if key else text for key, text in args]
    if sum(len(part) for part in parts) <= 60 and not any("\n" in part for part in parts):
        return f"{name}({', '.join(parts)})"
    return f"{name}(\n" + "".join(f"  {_indent(part)},\n" for part in parts) + ")"


def _indent(text: str) -> str:
    return text.replace("\n", "\n  ")


def _fields_args(fields: dict[str, Any], names: dict[str, str] | None,
                 meta: dict[str, Any] | None = None, base: dict[str, Any] | None = None):
    args: list[tuple[str | None, str]] = [(key, dumps(value, names)) for key, value in fields.items()]
    if base:
        args.append(("_base", dumps(base, names)))
    if meta:
        args.append(("_meta", dumps(meta, names)))
    return args


def dumps(value: Any, names: dict[str, str] | None = None) -> str:
    """The text form of an IR value. ``names`` maps a GUID to the name the document declares for it."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, tuple):
        return f"tuple({dumps(list(value), names)})"
    if isinstance(value, list):
        items = [dumps(item, names) for item in value]
        if any("\n" in item for item in items) or sum(len(item) for item in items) > 60:
            return "[\n" + "".join(f"  {_indent(item)},\n" for item in items) + "]"
        return "[" + ", ".join(items) + "]"
    if isinstance(value, dict):
        items = [f"{json.dumps(key)}: {dumps(item, names)}" for key, item in value.items()]
        if any("\n" in item for item in items):
            return "{\n" + "".join(f"  {_indent(item)},\n" for item in items) + "}"
        return "{" + ", ".join(items) + "}"
    if isinstance(value, irc.NoRef):
        return "none" if value.record_type is None else f'none(type="{value.record_type}")'
    if isinstance(value, irc.Ref):
        if names and value.guid.lower() in names and value.record_type is None:
            return names[value.guid.lower()]
        if value.record_type is None:
            return f"ref({json.dumps(value.guid)})"
        return f"ref({json.dumps(value.guid)}, type={json.dumps(value.record_type)})"
    if isinstance(value, Tac):
        suffix = f", type={json.dumps(value.type_tag)}" if value.type_tag else ""
        return f"tac({dumps(value.attributes)}{suffix})"
    if isinstance(value, TriggerEvent):
        return f"event({json.dumps(value.class_name)})"
    if isinstance(value, Raw):
        return f"raw({dumps(value.value)})"
    if isinstance(value, irc.Clause):
        return _call("clause", _fields_args(value.fields, names))
    if isinstance(value, Node):
        return _call(_name_of(_node_layer(value.record_type), value.record_type),
                     _fields_args(value.fields, names, value.meta))
    if isinstance(value, irf.Filter):
        return _call(f"filter.{value.kind}", _fields_args(value.fields, names))
    if isinstance(value, ability_ir.Target):
        return _call(f"target.{value.kind}", _fields_args(value.fields, names, value.meta))
    if isinstance(value, ability_ir.Var):
        return _var_text(value, names)
    raise TextError(f"cannot print {type(value).__name__}")


def _var_text(var: ability_ir.Var, names: dict[str, str] | None) -> str:
    if var.kind in ("input_ref", "effect_variable") and set(var.fields) == {"name"}:
        sigil = "$" if var.kind == "input_ref" else "@"
        return sigil + _name_text(str(var.fields["name"]))
    if var.kind == "effect_constant":
        return _call("effect_constant", _fields_args(var.fields, names))
    return _call(f"variable.{var.kind}", _fields_args(var.fields, names, None, var.base))


# ---- reading ----------------------------------------------------------------------------------

_TOKEN = re.compile(r'\s*(?:(?P<str>"(?:[^"\\]|\\.)*")|(?P<num>-?\d+)|(?P<var>[$@](?:[A-Za-z_]\w*|"(?:[^"\\]|\\.)*"))'
                    r'|(?P<id>[A-Za-z_][A-Za-z0-9_.]*)|(?P<punct>[()\[\]{},:=]))')


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    text = text.rstrip()
    while pos < len(text):
        found = _TOKEN.match(text, pos)
        if not found:
            raise TextError(f"unexpected text at {pos}: {text[pos:pos + 20]!r}")
        pos = found.end()
        kind = found.lastgroup or ""
        tokens.append((kind, found.group(kind)))
    return tokens


def _build(name: str, positional: list[Any], keywords: dict[str, Any]) -> Any:
    if name == "ref":
        return irc.Ref(positional[0], keywords.get("type"))
    if name == "none":
        return irc.NoRef(keywords.get("type"))
    if name == "tac":
        return Tac(positional[0], keywords.get("type", ""))
    if name == "event":
        return TriggerEvent(positional[0])
    if name == "raw":
        return Raw(positional[0])
    if name == "clause":
        return irc.Clause(fields=dict(keywords))
    if name == "effect_constant":
        return ability_ir.Var(kind="effect_constant", base={}, fields=dict(keywords))
    if name == "tuple":
        return tuple(positional[0])
    if "." not in name:
        raise TextError(f"unknown call {name!r}")
    layer, kind = name.split(".", 1)
    meta = keywords.pop("_meta", None) or {}
    base = keywords.pop("_base", None) or {}
    fields = dict(keywords)
    if layer == "filter":
        return irf.Filter(kind=kind, fields=fields)
    if layer == "target":
        return ability_ir.Target(kind=kind, fields=fields, meta=meta)
    if layer == "variable":
        return ability_ir.Var(kind=kind, base=base, fields=fields)
    to_class = _tables()
    if (layer, kind) not in to_class:
        raise TextError(f"no {layer} kind {kind!r}")
    return Node(record_type=to_class[(layer, kind)], fields=fields, meta=meta)


class _Reader:
    def __init__(self, text: str) -> None:
        self.tokens = _tokenize(text)
        self.pos = 0
        self.names: dict[str, str] = {}   # declared name -> GUID

    def peek(self, offset: int = 0) -> tuple[str, str]:
        index = self.pos + offset
        return self.tokens[index] if index < len(self.tokens) else ("end", "")

    def take(self, value: str | None = None) -> tuple[str, str]:
        token = self.peek()
        if token[0] == "end":
            raise TextError("unexpected end of text")
        if value is not None and token[1] != value:
            raise TextError(f"expected {value!r}, found {token[1]!r}")
        self.pos += 1
        return token

    def value(self) -> Any:
        kind, text = self.peek()
        if kind == "str":
            self.take()
            return json.loads(text)
        if kind == "num":
            self.take()
            return int(text)
        if kind == "var":
            self.take()
            name = json.loads(text[1:]) if text[1:].startswith('"') else text[1:]
            return ability_ir.Var(kind="input_ref" if text[0] == "$" else "effect_variable",
                                  base={}, fields={"name": name})
        if text == "[":
            self.take()
            items = []
            while self.peek()[1] != "]":
                items.append(self.value())
                if self.peek()[1] == ",":
                    self.take()
            self.take("]")
            return items
        if text == "{":
            self.take()
            out: dict[str, Any] = {}
            while self.peek()[1] != "}":
                key = self.take()[1]
                if not key.startswith('"'):
                    raise TextError(f"an object key must be a string, found {key!r}")
                self.take(":")
                out[json.loads(key)] = self.value()
                if self.peek()[1] == ",":
                    self.take()
            self.take("}")
            return out
        if kind == "id":
            if self.peek(1)[1] == "(":
                return self.call()
            self.take()
            if text == "null":
                return None
            if text == "none":
                return irc.NoRef()
            if text in self.names:
                return irc.Ref(self.names[text], None)
            raise TextError(f"{text!r} is not declared")
        raise TextError(f"unexpected {text!r}")

    def call(self) -> Any:
        name = self.take()[1]
        self.take("(")
        positional: list[Any] = []
        keywords: dict[str, Any] = {}
        while self.peek()[1] != ")":
            if self.peek()[0] == "id" and self.peek(1)[1] == "=":
                key = self.take()[1]
                self.take("=")
                keywords[key] = self.value()
            else:
                positional.append(self.value())
            if self.peek()[1] == ",":
                self.take()
        self.take(")")
        return _build(name, positional, keywords)


def loads(text: str) -> Any:
    """The IR object for a text form of one IR object (no declarations). ``TextError`` otherwise."""
    reader = _Reader(text)
    value = reader.value()
    if reader.peek()[0] != "end":
        raise TextError(f"text after the object: {reader.peek()[1]!r}")
    return value


def _declared_name(reader: _Reader) -> str:
    kind, text = reader.take()
    if kind == "str":
        return json.loads(text)
    if kind != "id" or "." in text:
        raise TextError(f"a declaration needs a name, found {text!r}")
    return text


def loads_ability(text: str) -> dict[str, Any]:
    """The Records an ability's code stands for: the root, and every effect, target and condition it declares.
    Returns ``{"root": record, "records": [record, ...]}``; the root is first in ``records``."""
    reader = _Reader(text)
    variables: list[ability_ir.Var] = []
    bodies: list[tuple[str, Any]] = []
    while reader.peek()[0] == "id" and reader.peek()[1] in DECLARATIONS and reader.peek(2)[1] == "=":
        keyword = reader.take()[1]
        name = _declared_name(reader)
        reader.take("=")
        value = reader.value()
        if keyword == "variable":
            if isinstance(value, int):
                value = ability_ir.Var(kind="constant", base={"m_DefaultValue": value}, fields={})
            if not isinstance(value, ability_ir.Var):
                raise TextError(f"variable {name} must be a variable")
            value.base = {"m_Name": name, **value.base}
            variables.append(value)
            continue
        if keyword == "target":
            if not isinstance(value, ability_ir.Target):
                raise TextError(f"target {name} must be a target object")
            guid = _guid_of(value.fields.get("id"))
        else:
            if not isinstance(value, Node):
                raise TextError(f"{keyword} {name} must be a {keyword} object")
            guid = _guid_of(value.fields.get("template_id"))
        if name in reader.names:
            raise TextError(f"{name} is declared twice")
        reader.names[name] = guid
        bodies.append((keyword, value))
    if reader.peek()[0] == "end":
        raise TextError("no root declared")
    root = reader.value()
    if reader.peek()[0] != "end":
        raise TextError(f"text after the root: {reader.peek()[1]!r}")
    if not isinstance(root, Node) or root.record_type != ROOT_RECORD:
        raise TextError("the ability must end with one root.ability_template(...) call")
    root.fields["variables"] = variables
    records = [rt.node_to_record(root)]
    for keyword, value in bodies:
        if keyword == "target":
            records.append(ability_ir.target_to_record(value))
        elif keyword == "effect":
            records.append(ef.node_to_record(value))
        else:
            records.append(cond.node_to_record(value))
    return {"root": records[0], "records": records}


def _guid_of(value: Any) -> str:
    """The GUID a declaration's template field holds: a Ref, or a plain ``{"m_Guid": ...}`` object."""
    guid = value.get("m_Guid") if isinstance(value, dict) else getattr(value, "guid", None)
    if not guid:
        raise TextError("a declaration has no template GUID")
    return str(guid)


# ---- from the Records -------------------------------------------------------------------------

def _lookup(section: str, guid: str) -> dict[str, Any]:
    record, _ = card_abilities.lookup(section, guid)
    if record is None:
        raise TextError(f"no {section} {guid}")
    return record


def ability_code(guid: str) -> str:
    """The code for an ability: its variables, targets, effects and conditions declared, then its root."""
    root_record = _lookup(ROOT_CLASS, guid)
    root = rt.node_from_record(root_record)
    names: dict[str, str] = {}
    targets: list[tuple[str, Any]] = []
    effects: list[tuple[str, Node]] = []
    conditions: list[tuple[str, Node]] = []

    def name_for(prefix: str, key: str) -> str:
        if key not in names:
            names[key] = f"{prefix}{sum(1 for v in names.values() if v.startswith(prefix))}"
        return names[key]

    for target_ref in root.fields.get("ability_target_template_ids") or []:
        key = target_ref.guid.lower()
        if key not in names:
            name_for("T", key)
            targets.append((names[key], ability_ir.target_from_record(_lookup("AbilityTargetTemplate", key))))
    for clause in root.fields.get("ability_effect_list") or []:
        effect_ref = clause.fields.get("effect_template_id")
        key = effect_ref.guid.lower()
        if key not in names:
            name_for("E", key)
            effects.append((names[key], ef.node_from_record(_lookup("AbilityEffectTemplate", key))))
        condition_ref = clause.fields.get("condition_id")
        if isinstance(condition_ref, irc.Ref):
            key = condition_ref.guid.lower()
            if key not in names:
                name_for("C", key)
                conditions.append((names[key], cond.node_from_record(_lookup("AbilityEffectConditionTemplate", key))))
    lines = []
    for var in root.fields.get("variables") or []:
        if var.kind == "constant" and set(var.base) == {"m_Name", "m_DefaultValue"} and not var.fields:
            value = dumps(var.base["m_DefaultValue"])
        else:
            value = _var_text(ability_ir.Var(kind=var.kind, base={k: v for k, v in var.base.items() if k != "m_Name"},
                                             fields=var.fields), names)
        lines.append(f"variable {_name_text(str(var.base.get('m_Name')))} = {value}")
    # A declaration's body writes its own references in full (its template GUID is its own name's
    # target, which the reader has not seen yet); only the root refers to declarations by name.
    for name, target in targets:
        lines.append(f"target {name} = {dumps(target)}")
    for name, node in effects:
        lines.append(f"effect {name} = {dumps(node)}")
    for name, node in conditions:
        lines.append(f"condition {name} = {dumps(node)}")
    root_fields = {key: value for key, value in root.fields.items() if key != "variables"}
    lines.append(dumps(Node(record_type=root.record_type, fields=root_fields, meta=root.meta), names))
    return "\n".join(lines) + "\n"


def code_for(guid: str) -> str:
    """Kept for the editor's load route: the code for an ability by GUID."""
    return ability_code(guid)


def record_for(text: str) -> dict[str, Any]:
    """The Records dict for the root of a code document (kept for the editor's compile route)."""
    return loads_ability(text)["root"]


# ---- for the editor --------------------------------------------------------------------------

VALUE_CALLS = [
    ("ref", 'ref("guid", type="Records class") a reference to a Records object'),
    ("none", "none(type=...) no object (the null GUID)"),
    ("tac", 'tac({"Attribute": 1}, type=...) a serialized TAC'),
    ("event", 'event("Records class") a trigger event'),
    ("raw", "raw(value) an object the IR keeps verbatim"),
    ("clause", "clause(...) one entry of an ability's effect list"),
    ("effect_constant", "effect_constant(value=N) a constant inside an effect"),
]
DECLARATION_NAMES = [
    ("variable", "variable NAME = value  declare an ability variable (or a constant, a bare integer)"),
    ("target", "target T0 = target.kind(...)  declare a target"),
    ("effect", "effect E0 = effect.kind(...)  declare an effect"),
    ("condition", "condition C0 = condition.kind(...)  declare an effect condition"),
]


def names() -> list[dict[str, Any]]:
    """Every name the code uses, with its class and fields, and the value and declaration keywords."""
    fields_by_class = {node["class"]: [item["name"] for item in node["fields"]]
                       for node in ability_ir_vocab._nodes()}
    out: list[dict[str, Any]] = []
    for (layer, kind), record_type in sorted(_tables().items()):
        out.append({"name": f"{layer}.{kind}", "layer": layer, "class": record_type,
                    "fields": fields_by_class.get(record_type, [])})
    for name, meaning in VALUE_CALLS:
        out.append({"name": name, "layer": "value", "class": "", "fields": [], "meaning": meaning})
    for name, meaning in DECLARATION_NAMES:
        out.append({"name": name, "layer": "declaration", "class": "", "fields": [], "meaning": meaning})
    return out
