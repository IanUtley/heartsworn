"""Write a stored ability as surface text: ``ability("text", ...)`` with each non-default field by name.

    ABILITY = ability(
        "At the start of your turn, this deals damage to each opposing champion.",
        id="1e469b99-e701-fe5d-ca1a-ca1aaa6f5a0f",
        trigger_event_type=TurnStartedEventType,
        ability_effect_list=[Clause(effect_template_id=Ref("21bcbba6-661e-951b-0ac4-4a11681bebdf"))],
    )

A field that holds its client default is left out, and the constructor fills it back in. A clause leaves out
its own defaults the same way, and an empty reference is written as ``NoRef()``. ``ir_python.loads`` reads
the text back to the same Records.

Each effect the ability uses is written once, before the ability, under a name taken from its game text, and
each clause refers to it by that name:

    DEAL_DAMAGE = CardModifierAbilityEffectTemplate(game_text='deal damage.', ...)
    ABILITY = ability('…', ability_effect_list=[Clause(effect_template_id=DEAL_DAMAGE)])
"""

from __future__ import annotations

import re
from typing import Any

from gamedata import DEFAULT_RECORD_STORE as STORE
from gamedata import ir_python as P
from gamedata import ir_registry
from gamedata import mechanics_types as T
from gamedata.mechanics_base import Typed, _argument_names, _default_of, _record_value
from gamedata.mechanics_fields import ir_name
from gamedata import ir_surface as surface
from gamedata.ir_surface import EMPTY_REF_FIELDS, SHARED_EFFECTS, THIS

ZERO_GUID = "00000000-0000-0000-0000-000000000000"
THIS_GUID = THIS.guid
EVENT_PREFIX = "Game.Shared.Mechanics."
# A field the header writes as the ability's text or ID, not as a keyword.
HEADER = {"m_GameText", "m_AbilityTemplateId"}


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def stored_ability(guid: str) -> dict[str, Any]:
    """The stored ability record with its type tags and fields, as plain JSON."""
    for record in STORE.load("AbilityTemplate"):
        if record.guid.lower() == guid.lower():
            return _plain(record.to_dict(include_metadata=True))
    raise KeyError(f"no AbilityTemplate {guid}")


def _value(value: Any) -> str:
    """A field value as text: an empty reference as ``NoRef()``, anything else as the IR text writes it."""
    if isinstance(value, dict) and set(value) == {"m_Guid"} and str(value["m_Guid"]).lower() == ZERO_GUID:
        return "NoRef()"
    return shorten(P._literal(value))


# Words that carry no meaning in a fragment's name.
_NAME_STOPWORDS = {
    "a", "an", "the", "of", "to", "this", "that", "from", "on", "in", "at", "by", "for", "is", "are",
    "and", "or", "if", "your", "you", "each", "equal", "number", "under", "control", "controlled", "with",
}


def _fragment_name(record: dict[str, Any], used: set[str]) -> str:
    """A name from an effect's game text: its first words in capitals, without markup or filler words, unique
    within the ability."""
    plain = re.sub(r"<[^>]+>|\[[^\]]*\]|\([^)]*\)|#[A-Za-z0-9_]+#", " ", str(record.get("m_GameText") or ""))
    words = [word.upper() for word in re.findall(r"[A-Za-z]+", plain) if word.lower() not in _NAME_STOPWORDS][:4]
    name = "_".join(words) or "EFFECT"
    unique, count = name, 2
    while unique in used:
        unique = f"{name}_{count}"
        count += 1
    used.add(unique)
    return unique


def _effect_fragments(clauses: list[dict[str, Any]]) -> tuple[dict[str, str], list[str]]:
    """Each distinct effect the clauses use, as ``{guid: name}`` and the ``NAME = ...`` lines that bind them.
    An effect whose class the IR does not know stays a GUID reference."""
    names: dict[str, str] = {}
    lines: list[str] = []
    used: set[str] = set()
    for clause in clauses:
        guid = str(clause["m_EffectTemplateId"]["m_Guid"]).lower()
        if guid in names or guid == ZERO_GUID:
            continue
        shared = {guid_: name for name, guid_ in SHARED_EFFECTS.items()}
        if guid in shared:
            names[guid] = shared[guid]
            continue
        record = effect_record(guid)
        if getattr(T, _kind(record), None) is None:
            continue
        name = _fragment_name(record, used)
        names[guid] = name
        lines.append(f"{name} = {_fragment_value(record)}")
    return names, lines


def _guid_of(value: Any) -> str | None:
    return str(value["m_Guid"]).lower() if isinstance(value, dict) and "m_Guid" in value else None


def _input_of(value: Any) -> str | None:
    """The name of an effect input read from a variable, or None."""
    return str(value["m_InputVariableName"]) if isinstance(value, dict) and "m_InputVariableName" in value else None


def _kind(record: dict[str, Any]) -> str:
    return str(record.get("_t") or "").rsplit(".", 1)[-1]


def _modifier_kind(record: dict[str, Any]) -> str:
    return str((record.get("m_Modifier") or {}).get("_t") or "").rsplit(".", 1)[-1]


# Each surface helper with the keywords it takes from a stored record (None when the record is not its kind).
SURFACE_HELPERS: tuple[tuple[str, Any], ...] = (
    ("summon", lambda r: {"card": _guid_of(r.get("m_CardTemplateId")), "zone": r.get("m_CardCollection"),
                          "amount": r.get("m_Amount"), "amount_input": _input_of(r.get("m_AmountField"))}
     if _kind(r) == "SummonTokenTroopAbilityEffectTemplate" else None),
    ("activate", lambda r: {"ability": _guid_of(r.get("m_AbilityToInvoke"))}
     if _kind(r) == "ActivateAbilityEffectTemplate" else None),
    ("grant", lambda r: {"ability": _guid_of(r.get("m_GrantedAbilityTemplateId"))}
     if _kind(r) == "GrantAbilityEffectTemplate" else None),
    ("move_card", lambda r: {"zone": r.get("m_DestinationCollection")}
     if _kind(r) == "MoveCardToZoneEffectTemplate" else None),
    ("transform", lambda r: {"card": _guid_of(r.get("m_CardTemplateId"))}
     if _kind(r) == "TransformCardAbilityEffectTemplate" else None),
    ("add_counter", lambda r: {"counter": _guid_of(r.get("m_Modifier", {}).get("m_CardCounterTemplateId")),
                               "amount": _input_of(r.get("m_Modifier", {}).get("m_InputValue"))}
     if _kind(r) == "CardModifierAbilityEffectTemplate" and _modifier_kind(r) == "CounterModifier"
     else None),
    ("deal_damage", lambda r: {"amount": ((r.get("m_Modifier") or {}).get("m_InputValue") or {}).get(
        "m_InputVariableName")} if _kind(r) == "CardModifierAbilityEffectTemplate"
     and _modifier_kind(r) == "DamageModifier" else None),
)


def _same_record(built: dict[str, Any], stored: dict[str, Any]) -> bool:
    return {k: v for k, v in _plain(built).items() if k != "_v"} == {k: v for k, v in stored.items() if k != "_v"}


def _fragment_value(record: dict[str, Any]) -> str:
    """An effect's fragment value: the surface helper that rebuilds it exactly, else its constructor form."""
    import gamedata.ir_surface as surface
    text = record.get("m_GameText") or ""
    guid = _guid_of(record.get("m_TemplateId"))
    for helper, keywords_of in SURFACE_HELPERS:
        keywords = keywords_of(record)
        if keywords is None or guid is None or any(value is None for value in keywords.values()):
            continue
        keywords["id"] = guid
        if record.get("m_Name") != surface._derived_name(text):
            keywords["name"] = record.get("m_Name")
        try:
            built = getattr(surface, helper)(text, **keywords)
        except (ValueError, TypeError, KeyError):
            continue
        if _same_record(built.to_record(), record):
            shown = [repr(text), *[f"{key}={value!r}" for key, value in keywords.items()]]
            return f"{helper}({', '.join(shown)})"
    cls = getattr(T, _kind(record), None)
    return shorten(P.dumps(cls.from_record(record)))


def _split_top(text: str) -> list[str]:
    """The comma-separated parts of a call's arguments or a list's items, leaving brackets and quotes whole."""
    parts, depth, quote, start = [], 0, None, 0
    for index, char in enumerate(text):
        if quote:
            if char == "\\":
                continue
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    tail = text[start:].strip()
    return [part for part in [*parts, tail] if part]


def _close_of(text: str, open_index: int) -> int:
    """The index of the bracket that closes the one at ``open_index``, skipping quoted text."""
    depth, quote, index = 0, None, open_index
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise ValueError("unbalanced brackets in " + text)


def _keywords(args: list[str]) -> dict[str, str] | None:
    """The arguments as ``{name: value text}``, or None when one is not a keyword."""
    found = {}
    for arg in args:
        match = re.fullmatch(r"([a-z_]\w*)=(.*)", arg, re.DOTALL)
        if match is None:
            return None
        found[match.group(1)] = match.group(2)
    return found


def _only(args: list[str], keys: set[str]) -> dict[str, str] | None:
    """The keyword arguments when they are exactly ``keys`` (the rest of a call's fields are its defaults)."""
    found = _keywords(args)
    return found if found is not None and set(found) == keys else None


def _kind_of(args: list[str], key: str, helper: str) -> str | None:
    """``helper(value)`` for a call whose only field is ``key``."""
    found = _only(args, {key})
    return f"{helper}({found[key]})" if found else None


def _class_of(text: str) -> type | None:
    """The generated class a constructor call in ``text`` names, when it starts with one."""
    match = re.match(r"([A-Z]\w*)\(", text)
    cls = getattr(T, match.group(1), None) if match else None
    return cls if isinstance(cls, type) else None


def _is(text: str, base: type) -> bool:
    cls = _class_of(text)
    return cls is not None and issubclass(cls, base)


def _not_of(args: list[str], key: str, base: type) -> str | None:
    """``not_(x)`` when ``not_`` would pick the class the call names: x is a ``base`` condition, and not a trigger
    or ability condition when ``base`` is the card filter's."""
    found = _only(args, {key})
    if not found or not _is(found[key], base):
        return None
    return f"not_({found[key]})"


def _or_of(args: list[str], key: str, base: type) -> str | None:
    """``or_(a, b)`` when ``or_`` would pick the class the call names: every item is a ``base`` condition."""
    found = _only(args, {key})
    if not found or not (found[key].startswith("[") and found[key].endswith("]")):
        return None
    items = _split_top(found[key][1:-1])
    if not items or not all(_is(item, base) for item in items):
        return None
    return f"or_({', '.join(items)})"


def _card_not_of(args: list[str]) -> str | None:
    """``not_(x)`` for a card filter: x is neither a trigger nor an ability condition."""
    found = _only(args, {"target_filter"})
    if not found or _is(found["target_filter"], T.TriggerCondition) or _is(found["target_filter"], T.AbilityCondition):
        return None
    return f"not_({found['target_filter']})"


def _card_or_of(args: list[str]) -> str | None:
    """``or_(a, b)`` for card filters: the items are neither all trigger conditions nor all ability conditions."""
    found = _only(args, {"target_filters"})
    if not found or not (found["target_filters"].startswith("[") and found["target_filters"].endswith("]")):
        return None
    items = _split_top(found["target_filters"][1:-1])
    if not items or all(_is(item, T.TriggerCondition) for item in items) \
            or all(_is(item, T.AbilityCondition) for item in items):
        return None
    return f"or_({', '.join(items)})"


def _variable_of(value: str) -> str | None:
    """The name of a variable a modifier reads: ``'P3'`` for ``EffectInputVariable(input_variable_name='P3')``."""
    match = re.fullmatch(r"EffectInputVariable\(input_variable_name=('[^']*')\)", value)
    return match.group(1) if match else None


def _modifier_of(args: list[str], helper: str) -> str | None:
    found = _only(args, {"input_value"})
    name = _variable_of(found["input_value"]) if found else None
    return f"{helper}({name})" if name else None


def _cost_of(args: list[str]) -> str | None:
    found = _keywords(args)
    if not found or "casting_cost" not in found or not set(found) <= {"casting_cost", "comparison_op"}:
        return None
    return _with_comparison(f"cost_is({found['casting_cost']}", found.get("comparison_op"))


def _attribute_of(args: list[str]) -> str | None:
    found = _keywords(args)
    if not found or not {"attribute", "value"} <= set(found) or not set(found) <= {"attribute", "value", "comparison_op"}:
        return None
    return _with_comparison(f"attribute_is({found['attribute']}, {found['value']}", found.get("comparison_op"))


def _attribute_mod_of(args: list[str]) -> str | None:
    found = _keywords(args)
    if not found or not {"attribute", "operation"} <= set(found):
        return None
    if set(found) == {"attribute", "operation", "value"}:
        return f"attribute_mod({found['attribute']}, {found['operation']}, {found['value']})"
    name = _variable_of(found["value_field"]) if set(found) == {"attribute", "operation", "value_field"} else None
    if name is None:
        return None
    return f"attribute_mod({found['attribute']}, {found['operation']}, variable={name})"


def _threshold_of(args: list[str]) -> str | None:
    found = _keywords(args)
    if not found or not {"color_flags", "required_quantity"} <= set(found) \
            or not set(found) <= {"color_flags", "required_quantity", "comparison_op"}:
        return None
    return _with_comparison(f"threshold({found['color_flags']}, {found['required_quantity']}",
                            found.get("comparison_op"))


def _with_comparison(opening: str, comparison: str | None) -> str:
    """The helper call with its comparison, left out when it is the helpers' default."""
    if comparison is None or comparison == "'GreaterThanOrEqual'":
        return opening + ")"
    return f"{opening}, comparison={comparison})"


def _troop_of(args: list[str]) -> str | None:
    found = _only(args, {"target_filters"})
    if not found or not found["target_filters"].startswith("[IsTroop(), attribute_is("):
        return None
    items = _split_top(found["target_filters"][1:-1])
    match = re.fullmatch(r"attribute_is\(('[^']*'), 1\)", items[1]) if len(items) == 2 else None
    return f"troop_with({match.group(1)})" if match else None


# Constructor names the short helpers replace, each with the function that writes its call (or None when the
# call would not rebuild the same record). A round trip checks each one.
_SHORT_FORMS: dict[str, Any] = {
    "NotTriggerCondition": lambda args: _not_of(args, "condition", T.TriggerCondition),
    "NotAbilityCondition": lambda args: _not_of(args, "condition", T.AbilityCondition),
    "NotCardFilter": _card_not_of,
    "OrTriggerCondition": lambda args: _or_of(args, "conditions", T.TriggerCondition),
    "OrAbilityCondition": lambda args: _or_of(args, "conditions", T.AbilityCondition),
    "OrCardFilter": _card_or_of,
    "DefenseModifier": lambda args: _modifier_of(args, "def_mod"),
    "AttackModifier": lambda args: _modifier_of(args, "atk_mod"),
    "HealHeroModifier": lambda args: _modifier_of(args, "heal"),
    "CardCostModifier": lambda args: _modifier_of(args, "cost_mod"),
    "HasSourceCastingCostFilter": _cost_of,
    "IntAttrFilter": _attribute_of,
    "IntAttrModifier": _attribute_mod_of,
    "AbilityControllerHasThresholdAbilityCondition": _threshold_of,
    "AndCardFilter": _troop_of,
    "TriggerCardMatchesFilter": lambda args: _kind_of(args, "card_filter", "card_matches"),
}
_CALL_START = re.compile(r"(?<![\w.])([A-Z][A-Za-z0-9_]*)\(")


def shorten(text: str) -> str:
    """``text`` with each constructor call that has a short helper written as that helper. Nested calls are
    shortened first, so the outer call sees their short forms."""
    out, position = [], 0
    while True:
        match = _CALL_START.search(text, position)
        if match is None:
            break
        name = match.group(1)
        if name not in _SHORT_FORMS:
            out.append(text[position:match.end()])
            position = match.end()
            continue
        close = _close_of(text, match.end() - 1)
        args = _split_top(shorten(text[match.end():close]))
        short = _SHORT_FORMS[name](args)
        out.append(text[position:match.start()])
        if short is None:
            out.append(f"{name}({', '.join(args)})")
        else:
            out.append(short)
        position = close + 1
    out.append(text[position:])
    return "".join(out)


def effect_record(guid: str) -> dict[str, Any]:
    """The stored effect template record with its type tags and fields, as plain JSON."""
    for record in STORE.load("AbilityEffectTemplate"):
        if record.guid.lower() == guid.lower():
            return _plain(record.to_dict(include_metadata=True))
    raise KeyError(f"no AbilityEffectTemplate {guid}")


def _clause_text(clause: dict[str, Any], names: dict[str, str]) -> str:
    """One effect-list entry as ``Clause(...)``, leaving out the fields its own defaults already give. The effect is
    its fragment's name when it has one."""
    defaults = P._clause(effect_template_id=clause["m_EffectTemplateId"])
    arguments = []
    for key, value in clause.items():
        if key in defaults and defaults[key] == value and key != "m_EffectTemplateId":
            continue
        if key == "m_EffectTemplateId":
            guid = str(value["m_Guid"]).lower()
            arguments.append(f"effect_template_id={names.get(guid) or _value(value)}")
        else:
            arguments.append(f"{ir_name(key)}={_value(value)}")
    return "Clause(" + ", ".join(arguments) + ")"


def _condition_text(value: Any) -> str | None:
    """A threshold condition as ``threshold(...)`` when that call rebuilds it exactly, else None."""
    if not isinstance(value, Typed) or type(value) is not T.AbilityControllerHasThresholdAbilityCondition:
        return None
    record = value.to_record()
    try:
        built = surface.threshold(record["m_ColorFlags"], record["m_RequiredQuantity"],
                                  comparison=record["m_ComparisonOp"])
    except (KeyError, TypeError, ValueError):
        return None
    if _same_record(built.to_record(), record):
        return (f"threshold({record['m_ColorFlags']!r}, {record['m_RequiredQuantity']!r}"
                f"{'' if record['m_ComparisonOp'] == 'GreaterThanOrEqual' else ', comparison=' + repr(record['m_ComparisonOp'])})")
    return None


def _field_value(key: str, value: Any, names: dict[str, str]) -> str:
    """A list of clauses is written clause by clause; a trigger event by its event name where one exists; a target
    list names this card as ``this``."""
    if key == "m_AbilityEffectList":
        return "[" + ", ".join(_clause_text(item, names) for item in value) + "]"
    if key == "m_AbilityCondition":
        call = _condition_text(value)
        if call is not None:
            return call
    if key == "m_AbilityTargetTemplateIds":
        return "[" + ", ".join("this" if _guid_of(item) == THIS_GUID else _value(item) for item in value) + "]"
    if key == "m_TriggerEventType" and isinstance(value, dict) and set(value) == {"m_InternalType"}:
        name = str(value["m_InternalType"])
        if name.startswith(EVENT_PREFIX) and name[len(EVENT_PREFIX):] in P.EVENT_NAMES:
            return name[len(EVENT_PREFIX):] + "Type"
    return _value(value)


def _variables_text(variables: list[Any], clauses: list[dict[str, Any]]) -> str | None:
    """Constants written as ``{name: value}`` when that rebuilds exactly the stored list, else None."""
    if not all(type(item) is T.AbilityConstant for item in variables):
        return None
    values: dict[str, Any] = {}
    for item in variables:
        record = item.to_record()
        if values.setdefault(record["m_Name"], record["m_DefaultValue"]) != record["m_DefaultValue"]:
            return None
    rebuilt = surface.expand_variables(values, clauses)
    if [_plain(item.to_record()) for item in rebuilt] != [_plain(item.to_record()) for item in variables]:
        return None
    return "{" + ", ".join(f"{key!r}: {value!r}" for key, value in values.items()) + "}"


def ability_text(guid: str, name: str = "ABILITY") -> str:
    """The surface text for one stored ability, bound to ``name``. ``ir_python.loads`` reads it back."""
    record = stored_ability(guid)
    root = T.AbilityTemplate.from_record(record)
    known = _argument_names(T.AbilityTemplate)
    argument_of = {record_name: argument for argument, record_name in known.items()}
    header = [repr(root.m_GameText)]
    names, fragments = _effect_fragments(record.get("m_AbilityEffectList") or [])
    arguments: list[str] = [f"id={root.m_AbilityTemplateId['m_Guid']!r}"]
    deprecated: dict[str, Any] = {}
    for key in record:
        if key in ("_t", "_v") or key in HEADER:
            continue
        value = getattr(root, key)
        if key in surface.BOILERPLATE:
            # Written only where the stored value differs from the boilerplate the explicit path fills in.
            if value != surface.BOILERPLATE[key]:
                deprecated[key] = value
            continue
        if key not in argument_of:
            deprecated[key] = value
            continue
        if _record_value(value) == _record_value(_default_of(T.AbilityTemplate, key)):
            continue
        if key in EMPTY_REF_FIELDS and _value(value) == "NoRef()":
            continue
        if key == "m_Variables" and (shorthand := _variables_text(value, record.get("m_AbilityEffectList") or [])):
            arguments.append(f"{argument_of[key]}={shorthand}")
            continue
        arguments.append(f"{argument_of[key]}={_field_value(key, value, names)}")
    if deprecated:
        arguments.append("_deprecated={" + ", ".join(
            f"{key!r}: {_value(value)}" for key, value in deprecated.items()) + "}")
    lines = [*fragments, f"{name} = ability(", *(f"    {item}," for item in header + arguments), ")"]
    return "\n".join(lines) + "\n"


def round_trip_problems(guid: str) -> list[str]:
    """Differences between the stored ability and the one its written text reads back to (empty when exact)."""
    text = ability_text(guid)
    ir_registry.clear()   # each ability binds the same name, so the registry is cleared between them
    back = P.loads(text, scope=None)["ABILITY"]
    if not isinstance(back, Typed):
        return ["the text did not read back to an ability"]
    stored = {key: value for key, value in stored_ability(guid).items() if key != "_v"}
    written = {key: value for key, value in _plain(back.to_record()).items() if key != "_v"}
    return [key for key in sorted(set(stored) | set(written)) if stored.get(key) != written.get(key)]


def record_for(text: str) -> dict[str, Any]:
    """The Records ability that surface text binds (one ``ability(...)`` call). Raises ValueError on text that
    is not valid, or that does not bind exactly one ability."""
    ir_registry.clear()   # a text is read on its own: its names are not held over from the last one read
    names = P.loads(text)
    found = [value for value in names.values() if isinstance(value, Typed) and type(value) is T.AbilityTemplate]
    if len(found) != 1:
        raise ValueError(f"the text must bind one ability, not {len(found)}")
    return found[0].to_record()


def _first_line(value: Any) -> str:
    return (getattr(value, "__doc__", None) or "").strip().split("\n")[0]


def surface_names() -> list[dict[str, Any]]:
    """Every name surface text can use, for completion: its layer, the keywords a call takes, and a meaning. A
    helper takes the keywords of its signature, and a generated class the fields of its constructor."""
    import inspect
    names: list[dict[str, Any]] = []
    for name, value in sorted(P.prelude().items()):
        if name.startswith("_"):
            continue
        if isinstance(value, type) and issubclass(value, Typed):
            layer, fields, meaning = "class", list(_argument_names(value)), _first_line(value)
        elif callable(value):
            layer, meaning = "helper", _first_line(value)
            fields = [param.name for param in inspect.signature(value).parameters.values()
                      if param.kind not in (param.VAR_POSITIONAL, param.VAR_KEYWORD)]
            if name == "Clause":
                fields = [ir_name(key) for key in P._clause(effect_template_id={"m_Guid": ZERO_GUID})
                          if key != "m_EffectTemplateId"]
            if name == "ability":
                fields += [argument for argument in _argument_names(T.AbilityTemplate) if argument not in fields]
        elif name.endswith("EventType"):
            layer, fields, meaning = "event", [], "a trigger event"
        else:
            layer, fields, meaning = "constant", [], ""
        names.append({"name": name, "layer": layer, "class": "", "fields": fields, "meaning": meaning})
    return names
