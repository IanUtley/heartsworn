"""Granted abilities for the interpreter: GrantAbility, RevokeAbility and RevertPermanentModifications.

Each effect runs the live leaf the native path runs for it (``rules_port.ir_event_effects.grant_ability``, and the
``EffectContext`` revoke and revert methods), once per target as the native resolver does. The leaf reads the
authored template and its explicit target from the context, so the interpreter sets those for one call and
restores them after. The grant's lifetime (``m_EffectDuration``) is recorded and expired by
``rules_port.effect_lifetimes``, which the live path uses too.
"""

from __future__ import annotations

import rules_port.leaves_abilities as _leaves_abilities
import rules_port.leaves_zones as _leaves_zones
from typing import Any

ZERO_GUID = "00000000-0000-0000-0000-000000000000"


def is_zero_guid(value: Any) -> bool:
    """Whether a Records guid reference names nothing. Records render the empty guid as the dashed
    ``00000000-0000-0000-0000-000000000000``, so comparing against ``"0" * 36`` never matched and an effect that
    names no template read as if it named the empty guid itself."""
    text = str(value or "").strip().lower().replace("-", "")
    return not text or text == "0" * 32


# The durations a granted ability can run with. ``effect_lifetimes`` expires each of them on the live path.
GRANT_DURATIONS = frozenset({"Instant", "Permanent", "EndOfTurn", "BeginningOfOwnersTurn", "WhileCardInPlay",
                             "BeginningOfOpponentsTurn"})

# The power sources a grant can take its abilities from (sockets, payments, remembered powers, a random pick).
# The live leaf reads each of them from the battle this ability is resolving in (``db_card_gem_ability_guids``,
# ``db_card_manual_ability_guids``, ``remembered_powers`` keyed on the resolving ability, and the Inspire /
# champion-charge pools for a random pick), so the interpreter runs them through that same leaf rather than
# recomputing them.
POWER_SOURCE_FLAGS = (
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
    return "" if is_zero_guid(guid) else str(guid or "").strip().lower()


def gaps(kind: str, effect: dict[str, Any], duration: str) -> list[str]:
    """The constructs in one granted-ability effect the interpreter does not run (empty when it runs it).

    A grant that takes its abilities from sockets, payments, remembered powers or a random pick is run: the live
    leaf resolves every one of those sources from the battle, including the once-per-activation random pick (the
    interpreter publishes the activation's ``resolving_ability_instance_id`` for it)."""
    if kind == "GrantAbilityEffectTemplate":
        missing = []
        if duration not in GRANT_DURATIONS:
            missing.append(f"grant duration {duration}")
        if not _typed(effect.get("m_GrantedAbilityTemplateId")) and \
                not any(effect.get(flag) for flag in POWER_SOURCE_FLAGS):
            # With neither a typed template nor a named power source, the leaf falls back to whatever the ability
            # remembered under its own guid, which is a source it cannot verify from the effect alone.
            missing.append("grant without a typed ability")
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
            from rules_port.ir_event_effects import grant_ability
            context.param = _typed(effect.get("m_GrantedAbilityTemplateId"))
        for target in targets:
            bstate["resolving_target_uid"] = int(target)
            if kind == "GrantAbilityEffectTemplate":
                grant_ability(context)
            elif kind == "RevokeAbilityEffectTemplate":
                _leaves_abilities.revoke_ability(context)
            else:
                _leaves_zones.revert_modifications(context)
    finally:
        context.effect_template_override, context.effect_duration_override, context.param = saved_context
        for key in _TARGET_ALIASES:
            bstate.pop(key, None)
            if saved_bstate[key] is not None:
                bstate[key] = saved_bstate[key]
