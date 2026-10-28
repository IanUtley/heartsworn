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
from gamedata.ir_surface import EMPTY_REF_FIELDS, THIS

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
    return P._literal(value)


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
    return P.dumps(cls.from_record(record))


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


def _field_value(key: str, value: Any, names: dict[str, str]) -> str:
    """A list of clauses is written clause by clause; a trigger event by its event name where one exists; a target
    list names this card as ``this``."""
    if key == "m_AbilityEffectList":
        return "[" + ", ".join(_clause_text(item, names) for item in value) + "]"
    if key == "m_AbilityTargetTemplateIds":
        return "[" + ", ".join("this" if _guid_of(item) == THIS_GUID else _value(item) for item in value) + "]"
    if key == "m_TriggerEventType" and isinstance(value, dict) and set(value) == {"m_InternalType"}:
        name = str(value["m_InternalType"])
        if name.startswith(EVENT_PREFIX) and name[len(EVENT_PREFIX):] in P.EVENT_NAMES:
            return name[len(EVENT_PREFIX):] + "Type"
    return _value(value)


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
        if key not in argument_of:
            deprecated[key] = value
            continue
        if _record_value(value) == _record_value(_default_of(T.AbilityTemplate, key)):
            continue
        if key in EMPTY_REF_FIELDS and _value(value) == "NoRef()":
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
