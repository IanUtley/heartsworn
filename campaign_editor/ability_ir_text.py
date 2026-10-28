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

A clause that invokes a child ability is a child link, ``link(...)``. A link with bindings
(``bindings=[C1I1 = $I1]``) is compiled when the document is read: the child is inlined where the inliner can
take it, and the load fails with the reasons where it cannot. A link without bindings stays the client's
``ActivateAbility`` clause.

``ability_code`` writes an ability from the Records by GUID; ``loads_ability`` reads the text back into the
Records it stands for. ``dumps`` and ``loads`` are the same pair for one IR object.
"""

from __future__ import annotations

import copy
import functools
import json
import re
from dataclasses import dataclass
from typing import Any

import ability_ir
import ability_ir_clauses as irc
import ability_ir_conditions as cond
import ability_ir_effects as ef
import ability_ir_filters as irf
import ability_ir_link as link
import ability_ir_root as rt
import ability_ir_vocab
import card_abilities
from ability_ir import Raw, Tac, TriggerEvent
from ability_ir_effects import Node
from gamedata import mechanics_fields as mf  # noqa: E402  (also sets sys.path)
from gamedata.mechanics import CLASSES  # noqa: E402

ROOT_CLASS = "AbilityTemplate"
ACTIVATE_RECORD = "ActivateAbilityEffectTemplate"
ROOT_RECORD = "Reckoning.Game.AbilityTemplate"
MODIFIER_PREFIX = ef.MODIFIER_PREFIX
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
DECLARATIONS = ("variable", "target", "effect", "condition")


class TextError(ValueError):
    """Text the IR syntax does not accept, or an object it cannot print."""


@dataclass
class ClauseLink:
    """An ``ActivateAbility`` clause written as a child link: ``link(...)``. Its ``bindings`` are the
    declared inputs (``C1I1 = $I1``); a link with none is the client's own clause."""

    clause: irc.Clause
    bindings: tuple[link.Binding, ...] = ()


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


def _class_name(record_type: str) -> str:
    return record_type.rsplit(".", 1)[-1]


def _type_pairs(value: Any, out: list[tuple[str, int | None]]) -> list[tuple[str, int | None]]:
    """The ``(type, version)`` pairs of an IR value's serialized objects, in encounter order."""
    if isinstance(value, Node):
        cls = _class_name(value.record_type)
        out.append((cls, CLASSES[cls]["version"] if cls in CLASSES else None))
        for item in value.fields.values():
            _type_pairs(item, out)
    elif isinstance(value, Tac):
        out.append((mf.TAC_CLASS, mf.TAC_VERSION))
    elif isinstance(value, ability_ir.Target):
        # A target's own type table is stored with it; its types come from there.
        for entry in value.meta.get("_v") or ():
            out.extend((name, version) for name, version in entry.items())
    elif isinstance(value, irc.Clause):
        for item in value.fields.values():
            _type_pairs(item, out)
    elif isinstance(value, ClauseLink):
        _type_pairs(value.clause, out)
    elif isinstance(value, Raw):
        _type_pairs(value.value, out)
    elif isinstance(value, dict):
        for item in value.values():
            _type_pairs(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _type_pairs(item, out)
    return out


def _generated_meta(node: Node) -> list[dict[str, int]] | None:
    """The ``_v`` table a node's Records object carries, generated from its fields."""
    if _class_name(node.record_type) not in CLASSES:
        return None
    return mf.version_table(_type_pairs(node, []))


def _written_fields(node: Node) -> dict[str, Any]:
    """The node's fields without the ones that hold their client default: the loader puts them back."""
    cls = _class_name(node.record_type)
    if cls not in CLASSES:
        return node.fields
    return {key: value for key, value in node.fields.items() if not mf.is_default(cls, key, value)}


CLAUSE_CLASS = "AbilityEffectTargetMapping"


def _written_clause_fields(clause: irc.Clause) -> dict[str, Any]:
    """A clause's fields without the ones that hold their client default (its output variables,
    its VFX and the other fields Records never vary): the loader puts them back."""
    return {key: value for key, value in clause.fields.items() if not mf.is_default(CLAUSE_CLASS, key, value)}


def _target_keys(kind: str) -> dict[str, str]:
    """The stored key of each IR field of a target kind."""
    keys = {ir: key for key, ir in ability_ir.TARGET_BASE.items()}
    keys.update(ability_ir.TARGET_KINDS[kind][1])
    return keys


def _written_target_fields(target: ability_ir.Target) -> dict[str, Any]:
    """A target's fields without the ones that hold their client default: the loader puts them back."""
    cls = ability_ir.TARGET_KINDS[target.kind][0]
    keys = _target_keys(target.kind)
    out = {}
    for ir, value in target.fields.items():
        stored = keys.get(ir)
        stored_value = ability_ir._count_to_record(value) if ir in ("min", "max") else value
        if stored and mf.is_default(cls, mf.ir_name(stored), stored_value):
            continue
        out[ir] = value
    return out


def _with_defaults(cls: str, fields: dict[str, Any]) -> dict[str, Any]:
    """The fields with each missing field of the class set to its client default (copied, so the
    shared defaults are never changed)."""
    filled = dict(fields)
    for name, default in mf.current_fields(cls).items():
        if name not in filled:
            filled[name] = copy.deepcopy(default)
    return filled


def _with_target_defaults(kind: str, fields: dict[str, Any]) -> dict[str, Any]:
    """A target's fields with each missing field of its kind set to its client default."""
    cls = ability_ir.TARGET_KINDS[kind][0]
    current = mf.current_fields(cls)
    filled = dict(fields)
    for ir, stored in _target_keys(kind).items():
        name = mf.ir_name(stored)
        if ir in filled or name not in current:
            continue
        default = copy.deepcopy(current[name])
        filled[ir] = ability_ir._count_from_record(default) if ir in ("min", "max") else default
    return filled


def _written_meta(node: Node) -> dict[str, Any] | None:
    """The node's meta, without a ``_v`` the loader can generate exactly (None when there is
    nothing to write)."""
    if not node.meta:
        return {}
    generated = _generated_meta(node)
    if set(node.meta) == {"_v"} and generated is not None and generated == node.meta["_v"]:
        return None
    return node.meta


def _fields_args(fields: dict[str, Any], names: dict[str, str] | None,
                 meta: dict[str, Any] | None = None, base: dict[str, Any] | None = None):
    args: list[tuple[str | None, str]] = [(key, dumps(value, names)) for key, value in fields.items()]
    if base:
        args.append(("_base", dumps(base, names)))
    # ``_meta={}`` is written when the object stores no type table: the loader must not add one.
    if meta is not None:
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
        return _call("clause", _fields_args(_written_clause_fields(value), names))
    if isinstance(value, ClauseLink):
        args = _fields_args(_written_clause_fields(value.clause), names)
        if value.bindings:
            pairs = [f"{_name_text(b.child)} = ${_name_text(b.parent)}" for b in value.bindings]
            args.append(("bindings", "[" + ", ".join(pairs) + "]"))
        return _call("link", args)
    if isinstance(value, Node):
        return _call(_name_of(_node_layer(value.record_type), value.record_type),
                     _fields_args(_written_fields(value), names, _written_meta(value)))
    if isinstance(value, irf.Filter):
        return _call(f"filter.{value.kind}", _fields_args(value.fields, names))
    if isinstance(value, ability_ir.Target):
        return _call(f"target.{value.kind}", _fields_args(_written_target_fields(value), names, value.meta or None))
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
        return irc.Clause(fields=_with_defaults(CLAUSE_CLASS, dict(keywords)))
    if name == "effect_constant":
        return ability_ir.Var(kind="effect_constant", base={}, fields=dict(keywords))
    if name == "tuple":
        return tuple(positional[0])
    if name == "link":
        bindings = keywords.pop("bindings", None) or []
        return ClauseLink(clause=irc.Clause(fields=_with_defaults(CLAUSE_CLASS, dict(keywords))),
                          bindings=tuple(bindings))
    if "." not in name:
        raise TextError(f"unknown call {name!r}")
    layer, kind = name.split(".", 1)
    had_meta = "_meta" in keywords
    meta = keywords.pop("_meta", None) or {}
    base = keywords.pop("_base", None) or {}
    fields = dict(keywords)
    if layer == "filter":
        return irf.Filter(kind=kind, fields=fields)
    if layer == "target":
        return ability_ir.Target(kind=kind, fields=_with_target_defaults(kind, fields), meta=meta)
    if layer == "variable":
        return ability_ir.Var(kind=kind, base=base, fields=fields)
    to_class = _tables()
    if (layer, kind) not in to_class:
        raise TextError(f"no {layer} kind {kind!r}")
    record_type = to_class[(layer, kind)]
    if _class_name(record_type) in CLASSES:
        # The fields the text left out are the client defaults; a missing _meta is generated.
        fields = mf.fill_defaults(_class_name(record_type), fields)
        node = Node(record_type=record_type, fields=fields, meta=meta)
        if not had_meta:
            node.meta = {"_v": _generated_meta(node)}
        return node
    return Node(record_type=record_type, fields=fields, meta=meta)


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
                if self.peek()[0] == "id" and self.peek(1)[1] == "=":
                    items.append(self.binding())
                else:
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

    def binding(self) -> link.Binding:
        child = self.take()[1]
        self.take("=")
        value = self.value()
        if not (isinstance(value, ability_ir.Var) and value.kind == "input_ref"):
            raise TextError(f"a binding takes an input, as {child} = $name")
        return link.Binding(child=child, parent=str(value.fields["name"]))

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
    clauses = []
    bound: list[tuple[int, ClauseLink]] = []
    for position, item in enumerate(root.fields.get("ability_effect_list") or []):
        if isinstance(item, ClauseLink):
            clauses.append(item.clause)
            if item.bindings:
                bound.append((position, item))
        else:
            clauses.append(item)
    root.fields["ability_effect_list"] = clauses
    root_record = rt.node_to_record(root)
    effects = {}
    for keyword, value in bodies:
        if keyword == "effect":
            effects[str(_guid_of(value.fields.get("template_id"))).lower()] = ef.node_to_record(value)
    if bound:
        root_record = _compile_links(root_record, bound, effects)
    used = _referenced_guids(root_record)
    records = [root_record]
    for keyword, value in bodies:
        if keyword == "target":
            guid, record = _guid_of(value.fields.get("id")), ability_ir.target_to_record(value)
        elif keyword == "effect":
            guid, record = _guid_of(value.fields.get("template_id")), ef.node_to_record(value)
        else:
            guid, record = _guid_of(value.fields.get("template_id")), cond.node_to_record(value)
        if str(guid).lower() in used:   # a body the compiled root no longer uses is left out
            records.append(record)
    return {"root": root_record, "records": records}


def _referenced_guids(record: dict[str, Any]) -> set[str]:
    """Every GUID the root record refers to through its clauses and targets."""
    out = set()
    for clause in record.get("m_AbilityEffectList") or []:
        for field_ in ("m_EffectTemplateId", "m_ConditionId"):
            if isinstance(clause.get(field_), dict):
                out.add(str(clause[field_].get("m_Guid") or "").lower())
    for target in record.get("m_AbilityTargetTemplateIds") or []:
        out.add(str(target.get("m_Guid") or "").lower())
    return out


def _compile_links(root_record: dict[str, Any], bound: list[tuple[int, ClauseLink]],
                   effects: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The root with its bound child links compiled (inlined). ``TextError`` with the reasons when one is refused."""
    def effect_of(guid: str) -> dict[str, Any]:
        if guid in effects:
            return effects[guid]
        return _lookup("AbilityEffectTemplate", guid)

    def root_of(guid: str) -> dict[str, Any] | None:
        return card_abilities.lookup("AbilityTemplate", guid)[0]

    def target_of(guid: str) -> dict[str, Any] | None:
        return card_abilities.lookup("AbilityTargetTemplate", guid)[0]

    def condition_of(guid: str) -> dict[str, Any] | None:
        return card_abilities.lookup("AbilityEffectConditionTemplate", guid)[0]

    links = []
    for position, item in bound:
        clause = root_record["m_AbilityEffectList"][position]
        effect = effect_of(str(clause["m_EffectTemplateId"]["m_Guid"]).lower())
        child = str((effect.get("m_AbilityToInvoke") or {}).get("m_Guid") or "").lower()
        if not child:
            raise TextError(f"clause {position + 1} has no child ability to bind")
        links.append(link.ChildLink(
            clause=position, child=child, target=int(clause.get("m_TargetTemplateIndex") or 0),
            lucky=bool(effect.get("m_RandomlyLuckyOrUnlucky")),
            bindings=tuple(item.bindings), entry=clause))
    compiled, problems = link.compile_root(root_record, links, effect_of, root_of, target_of, condition_of)
    if problems or compiled is None:
        raise TextError("; ".join(problems) or "the child links cannot be compiled")
    return compiled


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
    # A clause that invokes a child ability is written as a child link, not as a plain clause.
    link_guids = {str(_guid_of(node.fields.get("template_id"))).lower() for _, node in effects
                  if node.record_type.endswith(ACTIVATE_RECORD)}
    clauses = []
    for clause in root.fields.get("ability_effect_list") or []:
        effect_ref = clause.fields.get("effect_template_id")
        is_link = isinstance(effect_ref, irc.Ref) and effect_ref.guid.lower() in link_guids
        clauses.append(ClauseLink(clause=clause) if is_link else clause)
    root_fields = {key: value for key, value in root.fields.items() if key != "variables"}
    root_fields["ability_effect_list"] = clauses
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
    ("link", "link(..., bindings=[C1I1 = $I1]) a child ability invoked by an entry of the effect list"),
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
