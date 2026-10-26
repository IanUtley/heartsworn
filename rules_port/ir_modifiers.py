"""Keyword, attribute and protection modifiers for the IR interpreter (gap area G).

The interpreter reads a CardModifier's typed fields and hands them to the same leaves the native path
uses, so card state is written one way on both paths. Only the Permanent and EndOfTurn durations run
here; lasting durations belong to area H.
"""

from __future__ import annotations

from typing import Any, Iterable

from gamedata.mechanics_objects import MechanicsObject

# The CardModifier kinds this module runs, mapped to the native property the leaf is keyed by.
KEYWORD_MODIFIERS = {"AttributeModifier", "IntAttrModifier"}
PROTECTION_MODIFIERS = {
    "DamageMultiplierModifier", "DamageImmunityModifier", "BlockImmunityModifier",
    "BlockImmunityExceptionModifier", "BlockRestrictionModifier", "TargetingImmunityModifier",
    "AttackImmunityModifier",
}
GROUP_G_MODIFIERS = KEYWORD_MODIFIERS | PROTECTION_MODIFIERS
# The clause durations this module runs. Others are refused by coverage().
DURATIONS = {"Permanent", "EndOfTurn"}
# A keyword is granted with Add; Remove is not ported yet. An int attribute is set or added.
ATTRIBUTE_OPERATIONS = {"Add"}
INT_ATTR_OPERATIONS = {"Set", "Add"}


def _short(type_name: Any) -> str:
    return str(type_name or "").rsplit(".", 1)[-1]


def _get(obj: Any, name: str) -> Any:
    """A field of a raw Records dict (coverage) or of a typed mechanics object (runtime)."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _kind(obj: Any) -> str:
    if isinstance(obj, dict):
        return _short(obj.get("_t"))
    return _short(getattr(obj, "type_name", ""))


def modifier_gaps(modifier: Any, duration: str) -> list[str]:
    """Why a group G modifier cannot run in the interpreter, or [] when it can.

    ``modifier`` is a raw Records dict (static coverage) or a typed object (runtime); both answer
    the same field reads, so coverage and the runner agree on what runs.
    """
    kind = _kind(modifier)
    if duration not in DURATIONS:
        return [f"{kind} duration {duration}"]
    operation = _short(_get(modifier, "m_Operation"))
    if kind == "AttributeModifier":
        gaps = []
        if operation not in ATTRIBUTE_OPERATIONS:
            gaps.append(f"keyword operation {operation}")
        if not _flag_bits(_get(modifier, "m_AttributeFlags")):
            gaps.append("keyword without typed flags")
        return gaps
    if kind == "IntAttrModifier":
        gaps = []
        if operation not in INT_ATTR_OPERATIONS:
            gaps.append(f"int attribute operation {operation}")
        if _get(modifier, "m_Double"):
            gaps.append("int attribute doubled")
        value_field = _get(modifier, "m_ValueField")
        if value_field is not None and _kind(value_field) != "EffectInputVariable":
            gaps.append(f"int attribute value {_kind(value_field)}")
        if not _get(modifier, "m_Attribute"):
            gaps.append("int attribute without a name")
        return gaps
    return []


def _flag_bits(flags: Any) -> int:
    from rules_port.attribute_effects import attribute_bits_from_flags
    return attribute_bits_from_flags(flags if isinstance(flags, str) else "")


def _amount(modifier: MechanicsObject, scope: Any) -> int:
    """An int attribute's operand: its bound input variable, else the constant m_Value."""
    value_field = modifier.m_ValueField
    if isinstance(value_field, MechanicsObject) and _kind(value_field) == "EffectInputVariable":
        name = value_field.m_InputVariableName
        if name not in scope.variables:
            raise KeyError(f"variable {name} is not bound")
        return int(scope.variables[name])
    return int(modifier.m_Value or 0)


def run_modifier(modifier: MechanicsObject, effect_guid: str, duration: str,
                 targets: Iterable[int], scope: Any) -> None:
    """Apply one group G modifier to each target, through the native leaf that owns its state."""
    kind = _kind(modifier)
    context = scope.context
    owner = int(context.bstate.get("resolving_owner_id", 0) or 0)
    if kind == "AttributeModifier":
        from rules_port.attribute_effects import apply_attribute_grant
        for target in targets:
            apply_attribute_grant(context, int(target), {
                "attribute_flags": modifier.m_AttributeFlags, "operation": "Add",
                "duration": duration, "text": "", "source_owner_id": owner})
        return
    if kind == "IntAttrModifier":
        # The native int attribute leaf applies Set and Add, and writes the card's temporary or permanent buffs.
        from rules_port.effects import _int_attribute
        amount = _amount(modifier, scope)
        for target in targets:
            _int_attribute(context, int(target), {
                "attribute": modifier.m_Attribute, "operation": modifier.m_Operation,
                "amount": amount, "duration": duration})
        return
    # Protection modifiers persist as typed rules on the card, through the leaf the native path uses.
    from rules_port.metadata import modifier_metadata
    metadata = modifier_metadata(effect_guid) or {}
    param = {**metadata, "duration": duration}
    for target in targets:
        context.rule_modifier(int(target), param, metadata)
