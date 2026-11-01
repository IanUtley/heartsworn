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
SHIELD_AND_SUBTYPE_MODIFIERS = {"DamageShieldModifier", "SubTypeModifier"}
GROUP_G_MODIFIERS = KEYWORD_MODIFIERS | PROTECTION_MODIFIERS | SHIELD_AND_SUBTYPE_MODIFIERS
# The clause durations this module runs. Others are refused by coverage().
DURATIONS = {"Permanent", "EndOfTurn"}
# Keyword and int attribute grants also run the turn-boundary durations their native leaves already expire.
_TURN_BOUNDARY_DURATIONS = {"Permanent", "EndOfTurn", "BeginningOfOwnersTurn", "AfterCardsReadyOnPlayersTurn"}
# A linked clause (triggered, player-activated, or an invoked child's) also takes the while-in-play lifetime: the
# grant is applied to its targets at resolution and recorded in the linked ledger, which expires it with its source.
_LINKED_KEYWORD_DURATIONS = _TURN_BOUNDARY_DURATIONS | {"WhileCardInPlay"}
# An int attribute also takes the lifetimes its native leaf records and expires: damage (UntilDamaged), the opponent's
# next turn, and while its source stays on top of its deck (the sweep checks the top card at each event).
_INT_ATTR_DURATIONS = _TURN_BOUNDARY_DURATIONS | {"BeginningOfOpponentsTurn", "UntilDamaged", "WhileCardOnTopOfDeck"}
_LINKED_INT_ATTR_DURATIONS = _INT_ATTR_DURATIONS | {"WhileCardInPlay"}
DURATIONS_BY_KIND = {"AttributeModifier": _TURN_BOUNDARY_DURATIONS, "IntAttrModifier": _INT_ATTR_DURATIONS}
LINKED_DURATIONS_BY_KIND = {"AttributeModifier": _LINKED_KEYWORD_DURATIONS,
                            "IntAttrModifier": _LINKED_INT_ATTR_DURATIONS}
# A keyword is granted with Add; Remove is not ported yet. An int attribute is set or added.
ATTRIBUTE_OPERATIONS = {"Add"}
INT_ATTR_OPERATIONS = {"Set", "Add"}
# A subtype is set, added or removed by the native subtype leaf. A damage shield's lifetime is its own flags
# (one shot, or lasts indefinitely), so its clause is Instant; a subtype persists on the card, so Instant or Permanent.
SUBTYPE_OPERATIONS = {"Set", "Add", "Remove"}
SHIELD_DURATIONS = {"Instant"}
SUBTYPE_DURATIONS = {"Instant", "Permanent"}
INPUT_KINDS = {"EffectConstant", "EffectInputVariable"}


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


def modifier_gaps(modifier: Any, duration: str, *, linked: bool = False) -> list[str]:
    """Why a group G modifier cannot run in the interpreter, or [] when it can.

    ``modifier`` is a raw Records dict (static coverage) or a typed object (runtime); both answer
    the same field reads, so coverage and the runner agree on what runs. ``linked`` says the clause
    belongs to a triggered, player-activated or invoked ability, which applies its grant to targets at
    resolution and records it in the linked ledger instead of being projected by the static scan.
    """
    kind = _kind(modifier)
    if kind == "DamageShieldModifier":
        return _damage_shield_gaps(modifier, duration)
    if kind == "SubTypeModifier":
        return _subtype_gaps(modifier, duration)
    durations = (LINKED_DURATIONS_BY_KIND if linked else DURATIONS_BY_KIND).get(kind, DURATIONS)
    if duration not in durations:
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
        # C# checks m_Double before the operation: a doubled attribute doubles whatever the operation says.
        if not _get(modifier, "m_Double") and operation not in INT_ATTR_OPERATIONS:
            gaps.append(f"int attribute operation {operation}")
        value_field = _get(modifier, "m_ValueField")
        if value_field is not None and _kind(value_field) != "EffectInputVariable":
            gaps.append(f"int attribute value {_kind(value_field)}")
        if not _get(modifier, "m_Attribute"):
            gaps.append("int attribute without a name")
        return gaps
    return []


def _damage_shield_gaps(modifier: Any, duration: str) -> list[str]:
    gaps = []
    if duration not in SHIELD_DURATIONS:
        gaps.append(f"DamageShieldModifier duration {duration}")
    # A shield with neither flag is not one-shot: it lasts until its amount is used up or the turn ends (C#
    # DamageShield.Apply and Card.ClearEndOfTurnDamageShields), which the native shield store already does.
    if _kind(_get(modifier, "m_InputValue")) not in INPUT_KINDS:
        gaps.append("damage shield amount")
    return gaps


def _subtype_gaps(modifier: Any, duration: str) -> list[str]:
    gaps = []
    if duration not in SUBTYPE_DURATIONS:
        gaps.append(f"SubTypeModifier duration {duration}")
    if _short(_get(modifier, "m_Operation")) not in SUBTYPE_OPERATIONS:
        gaps.append(f"subtype operation {_short(_get(modifier, 'm_Operation'))}")
    if not _get(modifier, "m_Subtype"):
        gaps.append("subtype without a name")
    return gaps


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
                "amount": amount, "duration": duration, "double": bool(modifier.m_Double)})
        return
    if kind == "DamageShieldModifier":
        # The native shield leaf reads the shield's own flags; the amount is its input (a constant at most).
        from rules_port.ir_exec import _input_value
        params = {"oneshot": bool(modifier.m_OneShot), "lastsindefinitely": bool(modifier.m_LastsIndefinitely),
                  "onlycombatdamage": bool(modifier.m_OnlyCombatDamage),
                  "onlypreventfromdamagedealer": bool(modifier.m_OnlyPreventFromDamageDealer),
                  "damagedealeradditionaltarget": int(modifier.m_DamageDealerAdditionalTarget or 0)}
        amount = _input_value(modifier, scope)
        for target in targets:
            context.damage_shield(int(target), amount, params)
        return
    if kind == "SubTypeModifier":
        # The native subtype leaf sets, adds or removes the subtype the card carries.
        metadata = {"subtype": modifier.m_Subtype, "operation": modifier.m_Operation}
        for target in targets:
            context.subtype_modifier(int(target), metadata)
        return
    # Protection modifiers persist as typed rules on the card, through the leaf the native path uses.
    from rules_port.metadata import modifier_metadata
    metadata = modifier_metadata(effect_guid) or {}
    param = {**metadata, "duration": duration}
    for target in targets:
        context.rule_modifier(int(target), param, metadata)
