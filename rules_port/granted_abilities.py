"""Granted abilities for the interpreter: GrantAbility, RevokeAbility and RevertPermanentModifications.

Each effect runs the live leaf the native path runs for it (``rules_port.effects.grant_ability``, and the
``EffectContext`` revoke and revert methods), once per target as the native resolver does. The leaf reads the
authored template and its explicit target from the context, so the interpreter sets those for one call and
restores them after. The grant's lifetime (``m_EffectDuration``) is recorded and expired by
``rules_port.effect_lifetimes``, which the live path uses too.
"""

from __future__ import annotations

from typing import Any

ZERO_GUID = "0" * 36

# The durations a granted ability can run with. ``effect_lifetimes`` expires each of them on the live path.
GRANT_DURATIONS = frozenset({"Instant", "Permanent", "EndOfTurn", "BeginningOfOwnersTurn", "WhileCardInPlay"})

# Grants that take their abilities from sockets, payments, remembered powers or a random pick. The live leaf
# computes those from the battle, which the interpreter does not model yet, so such an effect is refused.
UNSUPPORTED_POWER_FLAGS = (
    "m_RandomInspirePower", "m_RandomChampionChargePower",
    "m_AllSocketedPowersOfSource", "m_AllSocketedPowersOfTarget", "m_AllSocketedPowersOfMyMaster",
    "m_AllPaymentPowersOfSource", "m_AllPaymentPowersOfTarget", "m_AllRememberedPowers",
)

GRANTED_EFFECTS = frozenset({
    "GrantAbilityEffectTemplate",
    "RevokeAbilityEffectTemplate",
    "RevertPermanentModificationsAbilityEffectTemplate",
})


def _typed(guid: Any) -> str:
    """A template guid as the live leaves read it, or '' when the effect names none."""
    if isinstance(guid, dict):
        guid = guid.get("m_Guid")
    text = str(guid or "").strip().lower()
    return "" if text in ("", ZERO_GUID) else text


def gaps(kind: str, effect: dict[str, Any], duration: str) -> list[str]:
    """The constructs in one granted-ability effect the interpreter does not run (empty when it runs it)."""
    if kind == "GrantAbilityEffectTemplate":
        missing = []
        if duration not in GRANT_DURATIONS:
            missing.append(f"grant duration {duration}")
        if not _typed(effect.get("m_GrantedAbilityTemplateId")):
            missing.append("grant without a typed ability")
        if any(effect.get(flag) for flag in UNSUPPORTED_POWER_FLAGS):
            missing.append("grant of powers from sockets, payments, remembered or random sources")
        return missing
    if kind == "RevokeAbilityEffectTemplate":
        return [] if _typed(effect.get("m_RevokedAbilityTemplateId")) else ["revoke without a typed ability"]
    if kind == "RevertPermanentModificationsAbilityEffectTemplate":
        return []
    return [f"effect {kind}"]


# The battle-state aliases a leaf reads its target from before the resolving target. The interpreter passes
# its targets explicitly, so these are cleared for one call and restored after it.
_TARGET_ALIASES = ("grant_target", "player_spell_target", "player_mod_target", "resolving_target_uid")


def run(kind: str, effect: dict[str, Any], duration: str, targets: list[int], context: Any) -> None:
    """Apply one granted-ability effect to each target through the live leaf. The caller has bridged the
    caster and source into the battle state."""
    bstate = context.bstate
    saved_context = (context.effect_template_override, context.effect_duration_override, context.param)
    saved_bstate = {key: bstate.pop(key, None) for key in _TARGET_ALIASES}
    try:
        context.effect_template_override = effect
        context.effect_duration_override = duration
        if kind == "GrantAbilityEffectTemplate":
            from rules_port.effects import grant_ability
            context.param = _typed(effect.get("m_GrantedAbilityTemplateId"))
        for target in targets:
            bstate["resolving_target_uid"] = int(target)
            if kind == "GrantAbilityEffectTemplate":
                grant_ability(context)
            elif kind == "RevokeAbilityEffectTemplate":
                context.revoke_ability()
            else:
                context.revert_modifications()
    finally:
        context.effect_template_override, context.effect_duration_override, context.param = saved_context
        for key in _TARGET_ALIASES:
            bstate.pop(key, None)
            if saved_bstate[key] is not None:
                bstate[key] = saved_bstate[key]
