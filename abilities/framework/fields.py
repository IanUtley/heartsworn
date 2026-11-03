"""Runtime evaluation of the client's AbilityField/EffectField values.

The client does not read effect quantities from localized text.  It evaluates
typed fields such as ``EffectInputVariable`` and ``EffectOutputVariable``
against the current ``AbilityInstance``.  This module is the small server-side
equivalent used by effect executors; the current Records snapshot is the only
rules-data source.
"""

import json
from typing import Any
from typing import cast

from gamedata import DEFAULT_RECORD_STORE
# The implementations live in rules_port.bom_fields (the single owner); these
# names remain importable from here for the framework callers.
from rules_port.bom_fields import (  # noqa: F401
    _last_type, _load_effect_templates, effect_template,
    _load_ability_templates, _raw_ability, ability_record,
    ability_variables, _field_name, field_variable_name, resolve_field,
    effect_template_value, counter_template_name)


_EFFECT_TEMPLATES = None
_ABILITY_TEMPLATES = None
_RECORD_STORE = DEFAULT_RECORD_STORE


def effect_field(db, bstate, effect_guid, field_name, default=0):
    """Evaluate a field on the currently resolving effect template."""
    template = effect_template(effect_guid)
    if not template:
        return int(default or 0)
    ability_guid = (bstate or {}).get("resolving_ability")
    variables = dict(ability_variables(db, ability_guid))
    variables.update((bstate or {}).get("ability_variables") or {})
    outputs = (bstate or {}).get("effect_outputs") or {}
    # CardIntegerVariable is persistent per card instance.  Populate the
    # resolver cache from the authoritative row on every field evaluation so
    # a later activation sees a value set by an earlier one.
    source_uid = (bstate or {}).get("resolving_source_uid")
    if source_uid is not None:
        try:
            from pvp_db import db_card_permanent_buffs
            value = db_card_permanent_buffs(
                (bstate or {}).get("session_id", 0), int(source_uid), conn=db)
            if value:
                data = json.loads(value or "{}")
                values = data.get("card_integer_variables") or {}
                if values:
                    bstate["card_integer_variables"] = values
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            pass
    field = template.get(field_name)
    value = resolve_field(field, variables, outputs, bstate, default)
    # Numeric fields in the extracted effect template often contain an
    # EffectInputVariable.  Its default is intentionally zero for dynamic
    # values (for example "draw a card for each Blessing in your hand").
    # Resolve the named variable through the same metadata variable evaluator
    # used by the statics and CardModifier leaves.
    variable_name = field_variable_name(field)
    ability_guid = (bstate or {}).get("resolving_ability")
    if variable_name and ability_guid:
        try:
            from .statics import ability_variable_value
            resolved = ability_variable_value(
                db, (bstate or {}).get("session_id", 0), bstate,
                ability_guid, variable_name,
                (bstate or {}).get("resolving_owner_id", 0),
                (bstate or {}).get("resolving_source_uid"),
                stat_prop=field_name)
            if resolved is not None:
                return int(resolved)
        except (AttributeError, TypeError, ValueError):
            pass
    return value


def modifier_metadata(effect_guid):
    """Return rule fields from a CardModifier's typed child template.

    The normalized BOM ``param`` is intentionally compact for compatibility,
    but the extracted effect record contains the authoritative modifier class
    and fields such as attribute flags, counter GUID, operation, and dynamic
    input value.  This adapter keeps leaves independent of localized text.
    """
    modifier = (effect_template(effect_guid) or {}).get("m_Modifier")
    if not isinstance(modifier, dict):
        return {}
    kind = _last_type(modifier.get("_t"))
    properties = {
        "AttackModifier": "attack",
        "DefenseModifier": "defense",
        "DamageModifier": "damage",
        "HealHeroModifier": "healhero",
        # Life loss is distinct from damage. A replacement such as Hardshell
        # Ambush must not replace the health loss caused by its own trigger.
        "LoseLifeModifier": "loselife",
        "SetHeroHealthModifier": "setherohealth",
        "CardCostModifier": "cardcost",
        "ChargePointsModifier": "chargepoints",
        "SpellPointsModifier": "spellpoints",
        "CurrentResourceModifier": "currentresource",
        "TotalResourceModifier": "totalresource",
        "ThresholdModifier": "threshold",
        "CardThresholdModifier": "cardthreshold",
        "AttributeModifier": "attribute",
        "IntAttrModifier": "intattr",
        "CounterModifier": "counter",
        "DamageMultiplierModifier": "damagemultiplier",
        "DamageImmunityModifier": "damageimmunity",
        "DamageShieldModifier": "damageshield",
        "BlockImmunityModifier": "blockimmunity",
        "BlockImmunityExceptionModifier": "blockimmunityexception",
        "BlockRestrictionModifier": "blockrestriction",
        "TargetingImmunityModifier": "targetingimmunity",
        "AttackImmunityModifier": "attackimmunity",
        "SubTypeModifier": "subtype",
    }
    out: dict[str, Any] = {"property": properties.get(kind, "")}
    for key in ("m_AttributeFlags", "m_Attribute", "m_Operation",
                "m_Value", "m_ThresholdColor", "m_Shard", "m_Subtype",
                "m_CardFilter", "m_CopySourceCard", "m_SetThresholds",
                "m_RemoveAllCounters", "m_RemoveHalfRoundedUp",
                "m_ReplaceExistingValue", "m_IsCombatDamage",
                "m_CombatDamageOnly", "m_NonCombatDamageOnly",
                "m_OnlyPreventFromDamageDealer",
                "m_DamageDealerAdditionalTarget", "m_OneShot",
                "m_LastsIndefinitely"):
        if key in modifier:
            out[key[2:].lower()] = modifier[key]
    counter = modifier.get("m_CardCounterTemplateId")
    if isinstance(counter, dict) and counter.get("m_Guid"):
        out["counter_template_guid"] = str(counter["m_Guid"]).lower()
    input_value = modifier.get("m_InputValue")
    if isinstance(input_value, dict):
        out["input_variable"] = field_variable_name(input_value)
        out["input_value"] = resolve_field(input_value, default=0)
    # IntAttrModifier stores its dynamic operand in m_ValueField, whereas
    # numeric modifiers generally use m_InputValue.  Both are typed
    # EffectInputVariable fields in Records and must reach the leaf resolver.
    value_field = modifier.get("m_ValueField")
    if isinstance(value_field, dict):
        out["input_variable"] = field_variable_name(value_field)
        out["input_value"] = resolve_field(value_field, default=0)
    if "m_LoseHalfHealth" in modifier:
        out["lose_half_health"] = bool(modifier["m_LoseHalfHealth"])
    return out
