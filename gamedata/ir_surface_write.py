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

Stage one: every effect is written inside its clause by its GUID. Named effect fragments come later.
"""

from __future__ import annotations

from typing import Any

from gamedata import DEFAULT_RECORD_STORE as STORE
from gamedata import ir_python as P
from gamedata import ir_registry
from gamedata import mechanics_types as T
from gamedata.mechanics_base import Typed, _argument_names, _default_of, _record_value
from gamedata.mechanics_fields import ir_name
from gamedata.ir_surface import EMPTY_REF_FIELDS

ZERO_GUID = "00000000-0000-0000-0000-000000000000"
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


def _clause_text(clause: dict[str, Any]) -> str:
    """One effect-list entry as ``Clause(...)``, leaving out the fields its own defaults already give."""
    defaults = P._clause(effect_template_id=clause["m_EffectTemplateId"])
    arguments = []
    for key, value in clause.items():
        if key in defaults and defaults[key] == value and key != "m_EffectTemplateId":
            continue
        if key == "m_EffectTemplateId":
            arguments.append(f"effect_template_id={_value(value)}")
        else:
            arguments.append(f"{ir_name(key)}={_value(value)}")
    return "Clause(" + ", ".join(arguments) + ")"


def _field_value(key: str, value: Any) -> str:
    """A list of clauses is written clause by clause; a trigger event by its event name where one exists."""
    if key == "m_AbilityEffectList":
        return "[" + ", ".join(_clause_text(item) for item in value) + "]"
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
        arguments.append(f"{argument_of[key]}={_field_value(key, value)}")
    if deprecated:
        arguments.append("_deprecated={" + ", ".join(
            f"{key!r}: {_value(value)}" for key, value in deprecated.items()) + "}")
    lines = [f"{name} = ability(", *(f"    {item}," for item in header + arguments), ")"]
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
