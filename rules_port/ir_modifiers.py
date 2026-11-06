"""Keyword, attribute and protection modifiers for the IR interpreter (gap area G).

The interpreter reads a CardModifier's typed fields and hands them to the same leaves the native path
uses, so card state is written one way on both paths. Only the Permanent and EndOfTurn durations run
here; lasting durations belong to area H.
"""

from __future__ import annotations

import json
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
# A damage multiplier is a typed rule on the card: it lasts for the turn, or until the start of the owner's next turn
# (the rule is tagged with that owner and the boundary pass removes it; see effect_lifetimes.expire_owner_turn_rules).
_RULE_DURATIONS = {"Permanent", "EndOfTurn", "BeginningOfOwnersTurn"}
DURATIONS_BY_KIND = {"AttributeModifier": _TURN_BOUNDARY_DURATIONS, "IntAttrModifier": _INT_ATTR_DURATIONS,
                     "DamageMultiplierModifier": _RULE_DURATIONS}
LINKED_DURATIONS_BY_KIND = {"AttributeModifier": _LINKED_KEYWORD_DURATIONS,
                            "IntAttrModifier": _LINKED_INT_ATTR_DURATIONS,
                            "DamageMultiplierModifier": _RULE_DURATIONS}
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
        amount = _amount(modifier, scope)
        for target in targets:
            int_attr_modifier(context, int(target), {
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


# The three modifier operations below are the bodies the native CardModifier dispatch (rules_port.effects)
# delegates to; each lives with the IR operation it implements.


def heal_hero_modifier(context, target, param):
    owner = context.target_owner(
        target, default=context.bstate.get("resolving_owner_id", 0))
    amount = context.modifier_value(param, param, "healhero")
    if not amount:
        amount = int(param.get("amount") or 0)
    return context.gain_health(int(owner or 0), amount)


def card_cost_modifier(context, target, param):
    if target is None:
        target = context.bstate.get("resolving_source_uid")
    if target is None:
        return "cardcost: no target"
    from pvp_db import (db_add_card_cost_modifier, db_card_zone_details,
                        db_card_mutation_field, db_set_card_mutation_field)
    amount = int(param.get("amount") or 0)
    if not amount:
        amount = context.modifier_value(param, param, "cardcost")
    replace = bool(param.get("replaceexistingvalue"))
    # A replace modifier can legitimately assign 0 ("... become cost [(0)]"),
    # so only an additive modifier falls back to the game-text operand.
    if not amount and not replace:
        # Extracted CardModifier rows can carry amount 0 with the operand only
        # in the localized game text (e.g. "cost -[(1)]") or in an ability
        # variable whose raw record is unavailable.  Recover the signed delta
        # from the text so the effect still applies.
        import re
        match = re.search(r"cost\s*([+-])\s*\[?\(?\s*(\d+)",
                          str(param.get("text") or ""), re.IGNORECASE)
        if match:
            value = int(match.group(2))
            amount = -value if match.group(1) == "-" else value
    if not amount and not replace:
        return "cardcost: no change"
    assigned_cost = int(amount)
    if replace:
        # C# ``CardCostModifier.Apply`` assigns ``card.ResourceCost = value``
        # when ReplaceExistingValue is set (Raucous Revelry: "your cards with
        # Rowdy in all zones become cost 1").  This port stores additive
        # deltas, so convert the assignment into the delta that makes the
        # card's current effective cost equal the authored value.
        from rules_port.static_rules import effective_cost
        current = int(effective_cost(
            context.db, context.session.session_id, context.bstate,
            int(target)) or 0)
        amount = assigned_cost - current
        if not amount:
            return f"cost already {assigned_cost} on {hex(int(target))}"
    duration = str(param.get("duration") or context.effect_duration)
    if duration == "UntilItLeavesYourHand":
        try:
            buffs = json.loads(db_card_mutation_field(
                context.session.session_id, int(target), "temporary_buffs",
                conn=context.db) or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            buffs = {}
        if not isinstance(buffs, dict):
            buffs = {}
        mods = buffs.setdefault("temporary_cost_modifiers", [])
        mods.append({"delta": int(amount), "duration": duration,
                     "source_uid": context.bstate.get("resolving_source_uid"),
                     "target_uid": int(target)})
        db_set_card_mutation_field(
            context.session.session_id, int(target), "temporary_buffs",
            json.dumps(buffs, separators=(",", ":"), sort_keys=True),
            conn=context.db)
    else:
        db_add_card_cost_modifier(
            context.session.session_id, int(target), amount, conn=context.db)
        if duration in ("BeginningOfOwnersTurn", "AfterCardsReadyOnPlayersTurn",
                        "WhileCardInPlay", "EndOfTurn"):
            # "Cost [(1)] until the start of your next turn" (Thk'tatcha) is
            # reversed at the owner's boundary instead of staying forever; a
            # while-in-play change is reversed when its source is gone; "this
            # turn" is reversed at the end of the turn (C# disables the
            # effect instance in RemoveEndOfTurnAbilities).
            from .effect_lifetimes import record_temporary_stat
            resolving_owner = int(
                context.bstate.get("resolving_owner_id", 0) or 0)
            expiration_owner = (
                resolving_owner if duration == "BeginningOfOwnersTurn"
                else int(context.target_owner(
                    int(target), default=resolving_owner) or 0))
            from pvp_db import db_card_location
            record_temporary_stat(
                context.bstate, int(target), 0, 0, duration=duration,
                owner_id=expiration_owner, cost=int(amount),
                location=db_card_location(
                    context.session.session_id, int(target), conn=context.db),
                source_uid=context.bstate.get("resolving_source_uid"))
    context.db.commit()
    row = db_card_zone_details(
        context.session.session_id, int(target), conn=context.db)
    if row:
        context._push_modifier_card(int(target))
    if replace:
        return f"cost -> {assigned_cost} on {hex(int(target))}"
    return f"cost {amount:+} on {hex(int(target))}"


def int_attr_modifier(context, target, param):
    if target is None:
        return "intattr: no target"
    attr = str(param.get("attribute") or "")
    if not attr:
        return "intattr: missing attribute"
    amount = int(param.get("amount") or 0)
    if not amount:
        amount = context.modifier_value(param, param, "intattr")
    operation = str(param.get("operation") or "set").lower()
    double = bool(param.get("double") or param.get("doubled") or False)
    duration = str(param.get("duration") or context.effect_duration)

    def apply_operation(old):
        # C# IntAttrModifier.Apply checks m_Double before m_Operation, then
        # Add/Remove/Set; Remove deletes the attribute regardless of value and
        # any other operation logs an error and does nothing.
        if double:
            return old + old
        if operation in ("add", "increment"):
            return old + amount
        if operation in ("remove", "removeall", "clear"):
            return 0
        if operation == "subtract":
            return old - amount
        if operation == "set":
            return amount
        return None
    temporary_durations = {
        "EndOfTurn", "EndOfNextTurn", "BeginningOfOwnersTurn",
        "BeginningOfOpponentsTurn", "AfterCardsReadyOnPlayersTurn",
        "UntilDamaged", "UntilItLeavesYourHand",
        # A while-in-play write is a recorded grant: it lives in the temporary payload and the linked ledger
        # expires it with the source (the sweep at each event and turn boundary).
        "WhileCardInPlay",
        # Likewise a write for as long as its source stays on top of its deck (the sweep checks the top card).
        "WhileCardOnTopOfDeck",
    }
    resolving_owner = int(context.bstate.get("resolving_owner_id", 0) or 0)
    if duration == "BeginningOfOwnersTurn":
        expiration_owner = resolving_owner
    elif duration == "BeginningOfOpponentsTurn":
        if context.bstate.get("pvp"):
            expiration_owner = next((int(pid) for pid in
                context.bstate.get("pids", ())
                if int(pid) != resolving_owner), 0)
        else:
            profile = getattr(context.handler, "user_profile", None) or {}
            player_owner = int(profile.get("id", 0) or 0)
            expiration_owner = (0 if resolving_owner == player_owner
                                else player_owner)
    elif duration == "AfterCardsReadyOnPlayersTurn":
        expiration_owner = int(context.target_owner(
            target, default=resolving_owner) or resolving_owner)
    else:
        expiration_owner = None
    champion_owner = context._champion_owner(target)
    if champion_owner is not None:
        uid_key = str(int(target))
        all_attrs = context.bstate.setdefault("champion_int_attrs", {})
        attrs = all_attrs.setdefault(uid_key, {})
        previous = attrs.get(attr)
        value = apply_operation(int(previous or 0))
        if value is None:
            return f"intattr: unsupported operation {operation} on {attr}"
        if value:
            attrs[attr] = value
        else:
            attrs.pop(attr, None)
        if duration in temporary_durations:
            from .effect_lifetimes import record_temporary_intattr
            record_temporary_intattr(
                context.bstate, int(target), attr, previous, attrs.get(attr),
                champion=True, duration=duration,
                owner_id=resolving_owner,
                target_owner_id=champion_owner,
                source_uid=context.bstate.get("resolving_source_uid"),
                expiration_owner_id=expiration_owner)
        context._push_champion_intattrs(
            champion_owner, int(target),
            cleared=() if value else (attr,))
        context.emit_int_attribute_gained(target, attr, previous, value)
        return f"intattr {attr}={value} champion={hex(int(target))}"

    from pvp_db import db_card_mutation_field, db_set_card_mutation_field
    temporary = duration in {
        "EndOfTurn", "EndOfNextTurn", "BeginningOfOwnersTurn",
        "BeginningOfOpponentsTurn",
        "AfterCardsReadyOnPlayersTurn", "UntilDamaged",
        "UntilItLeavesYourHand", "WhileCardInPlay", "WhileCardOnTopOfDeck"}
    column = "temporary_buffs" if temporary else "permanent_buffs"
    raw = db_card_mutation_field(
        context.session.session_id, int(target), column, conn=context.db)
    try:
        buffs = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        buffs = {}
    attrs = buffs.setdefault("int_attrs", {})
    old = int(attrs.get(attr, 0) or 0)
    # A persisted Tunneling IntAttr is the card's current absolute value
    # (rules_port.tunneling.tunneling_value), so a modifier on a card that
    # has none yet applies to the printed CardTemplate value: Tectonic
    # Megahulk's "Tunneling -1 for each Robot" is 15 -> 14, not 0 -> -1.
    # Keep an explicit 0 as well, or the threshold would revert to print.
    keep_zero = False
    if attr.lower() == "tunneling" and not temporary:
        from pvp_db import db_card_basic
        from .tunneling import tunneling_value
        basic = db_card_basic(
            context.session.session_id, int(target), conn=context.db)
        printed = tunneling_value(context.db, basic[0], {}) if basic else 0
        if attr not in attrs:
            old = printed
        keep_zero = printed > 0 and operation not in (
            "remove", "removeall", "clear")
    value = apply_operation(old)
    if value is None:
        return f"intattr: unsupported operation {operation} on {attr}"
    if value or keep_zero:
        attrs[attr] = value
    else:
        attrs.pop(attr, None)
    tamed = attr.lower() == "tamed" and value > 0
    if tamed:
        # Tamed and Untamed are mutually exclusive.
        attrs.pop("Untamed", None)
    db_set_card_mutation_field(
        context.session.session_id, int(target), column,
        json.dumps(buffs), conn=context.db)
    context.db.commit()
    from .effects import _capture_tamed_card  # the capture leaf stays with the Taming Sphere's effect in effects
    if tamed and _capture_tamed_card(context, int(target), buffs):
        return f"intattr {attr}={value} target={hex(int(target))} (captured)"
    if duration in temporary_durations:
        from .effect_lifetimes import record_temporary_intattr
        record_temporary_intattr(
            context.bstate, int(target), attr, old if old else None,
            attrs.get(attr), duration=duration,
            owner_id=resolving_owner,
            target_owner_id=context.target_owner(int(target), default=None),
            source_uid=context.bstate.get("resolving_source_uid"),
            expiration_owner_id=expiration_owner)
    context._push_modifier_card(int(target), int_attrs=attrs)
    context.emit_int_attribute_gained(target, attr, old, value)
    return f"intattr {attr}={value} target={hex(int(target))}"
