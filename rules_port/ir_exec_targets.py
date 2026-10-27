"""Stored, triggered and follow-up targets for the IR interpreter (gap area B).

A target can name cards that an earlier effect of the same ability produced: the cards it stored, moved,
drew, buried, voided or created, the cards a previous effect's target resolved to, or the card that
triggered the ability. The StoreTargets effect stores targets for those later effects to read.

Each one is answered by the live resolver's own enumerator or store leaf, so the interpreter and the
native path read the same bookkeeping. ``ir_exec`` calls into this module and this module reaches back
into ``ir_exec`` only inside functions, so the two import cleanly.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

# The target templates this module enumerates. The first four read the lists an effect recorded, and the
# StoredTargets list, for the source card or the ability.
LIST_TARGET_KINDS = {
    "SourceDrawnTargetTemplate", "SourceBuriedTargetTemplate", "SourceStoredTargetTemplate",
    "AbilityCreatedTargetTemplate", "VoidedTargetTemplate",
}
# Targets that follow an earlier target or effect of the same ability.
FOLLOW_UP_KINDS = LIST_TARGET_KINDS | {
    "AbilityTriggerCardTargetTemplate", "SecondaryTargetTemplate", "MatchSecondaryTargetTemplate",
}


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """An attribute of a typed object, or a key of a plain one."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _short(type_name: Any) -> str:
    return str(type_name or "").rsplit(".", 1)[-1]


def _guid(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("m_Guid") or "").lower()
    return str(value or "").lower()


def referenced_instances(root: Any) -> set[int]:
    """The effect instance ids a clause of the ability names as its secondary target (m_SecondaryTargetIndex)."""
    found = set()
    for clause in root.m_AbilityEffectList:
        wanted = int(_get(clause, "m_SecondaryTargetIndex", -1) or -1)
        if wanted >= 0:
            found.add(wanted)
    return found


# The templates whose cards the live resolver validates or limits by a count, and which take a constant count here.
COUNTED_KINDS = {"SecondaryTargetTemplate", "MatchSecondaryTargetTemplate"}


def _type_of(value: Any) -> str:
    """The short class name of a stored count or a typed object, or "" for a plain value."""
    if isinstance(value, dict):
        return _short(value.get("_t"))
    return _short(getattr(value, "type_name", ""))


def constant_count(template: Any, name: str) -> int:
    """A constant target count (``TargetConstant``'s value), 0 when the template sets none."""
    value = _get(template, name)
    if value is None or _type_of(value) != "TargetConstant":
        return 0
    return int(_get(value, "m_Value", 0) or 0)


# The trigger selectors a trigger card target reads: the event's source card and its target card.
TRIGGER_SELECTORS = {"TriggerSource", "TriggerTarget"}


def follow_up_gaps(template: Any, kind: str) -> list[str]:
    """The constructs of a follow-up target the interpreter does not run, for ``coverage`` (``template`` is the
    stored value). The trigger target (the card the event's other side names) is not wired. A chosen secondary or
    match target is a pick the player makes, which the interpreter does not prompt for. A list target is limited
    by a count only where the live resolver limits it, so a count is refused on the others."""
    gaps = []
    if kind == "AbilityTriggerCardTargetTemplate" and \
            _short(template.get("m_TriggerSelector") or "TriggerSource") not in TRIGGER_SELECTORS:
        gaps.append(f"trigger selector {_short(template.get('m_TriggerSelector'))}")
    if kind in COUNTED_KINDS and not template.get("m_IsAutoTarget"):
        gaps.append("chosen follow-up target")
    for count in ("m_MaxTargetCount", "m_MinTargetCount"):
        if template.get(count) in (None, 0, "", [], {}, "0"):
            continue
        if kind not in COUNTED_KINDS or _type_of(template.get(count)) != "TargetConstant":
            gaps.append(f"target {count[2:]}")
    return gaps


def _ability_shim(root_guid: str, scope: Any) -> SimpleNamespace:
    """The ability fields the live enumerators read, from the interpreter's scope."""
    return SimpleNamespace(ability_template_id=root_guid, source_uid=int(scope.source_uid),
                           responsible_player_id=int(scope.owner_uid), ordered_effects=(),
                           activation=SimpleNamespace(target_map={}, variables=dict(scope.variables)))


def follow_up_targets(template: Any, template_guid: str, root_guid: str, scope: Any) -> list[int]:
    """The cards a follow-up target names, from the bookkeeping the live path keeps for them."""
    from rules_port import resolution
    from rules_port.ir_exec import _card_by_uid, _filter_holds
    context = scope.context
    bstate = context.bstate
    kind = _short(template.type_name)
    if kind == "AbilityTriggerCardTargetTemplate":
        # The card the event names for its selector: the trigger source (the card that moved, the native
        # resolving_trigger_source_uid), or the trigger target (the native resolving_trigger_target_uid). The card
        # filter must pass it.
        if _short(getattr(template, "m_TriggerSelector", None) or "TriggerSource") == "TriggerTarget":
            uid = context.bstate.get("resolving_trigger_target_uid")
        else:
            uid = scope.event.get("card")
        if uid is None:
            return []
        if template.m_CardFilter is not None:
            card = _card_by_uid(scope, uid)
            if card is None or not _filter_holds(template.m_CardFilter, card, scope):
                return []
        return [int(uid)]
    db = context.db
    session_id = context.session.session_id
    ability = _ability_shim(root_guid, scope)
    target_spec = SimpleNamespace(guid=template_guid)
    if kind in LIST_TARGET_KINDS:
        return [int(uid) for uid in resolution._list_target_values(
            bstate, ability, kind, scope.source_uid, target_spec, db, session_id)]
    # The contingent effect's targets are the ones the interpreter resolved for that effect instance.
    effect = {"secondary_target_index": int(_get(scope.clause, "m_SecondaryTargetIndex", -1) or -1)}
    if kind == "SecondaryTargetTemplate":
        from rules_port.targeting import filter_resolved_targets
        raw = (resolution._resolved_effect_target_values(
            ability, effect["secondary_target_index"], bstate, scope.resolved_by_instance)
            if effect["secondary_target_index"] >= 0 else ())
        return [int(uid) for uid in filter_resolved_targets(
            db, session_id, scope.owner_uid, template_guid, scope.source_uid, raw, bstate)]
    champions = getattr(context.handler, "_champion_targets", lambda: [])() or []
    values = resolution._match_secondary_values(
        db, session_id, ability, effect, target_spec, bstate, champions=champions,
        resolved_by_instance=scope.resolved_by_instance)
    # The live resolver's random sample, or its truncation to the maximum count, for an automatic match.
    maximum = constant_count(template, "m_MaxTargetCount")
    if template.m_IsRandomTarget:
        return [int(uid) for uid in resolution._random_target_sample(values, maximum, bstate)]
    return [int(uid) for uid in (values[:maximum] if maximum > 0 else values)]


def store_targets(scope: Any, root_guid: str, effect_raw: dict[str, Any], targets: list[int]) -> None:
    """C# StoreTargets: remember the clause's targets for the effects after it in this ability. The live store
    leaf does the work (on the ability instance, or on the source card for the card-scoped form), so the list
    a later target reads is the one the native path writes."""
    context = scope.context
    bstate = context.bstate
    previous_template = context.effect_template_override
    previous_ability = bstate.get("resolving_ability")
    context.effect_template_override = effect_raw
    # The live store leaf keys the list on the resolving ability, which is this ability.
    bstate["resolving_ability"] = root_guid
    try:
        for target in targets:
            context.store_target(target=int(target))
    finally:
        context.effect_template_override = previous_template
        if previous_ability is None:
            bstate.pop("resolving_ability", None)
        else:
            bstate["resolving_ability"] = previous_ability


def stored_card_uids(scope: Any) -> set[int]:
    """The cards IsStoredCardFilter passes: the ones stored for the source card or the ability."""
    from rules_port.filters import _stored_card_uids
    return {int(uid) for uid in _stored_card_uids(scope.context, scope.source_uid)}


def trigger_card_is_stored(scope: Any) -> bool:
    """TriggerCardIsStoredTargetOfAbilitySource: the card that moved was stored by some ability's StoreTargets."""
    uid = scope.event.get("card")
    if uid is None:
        return False
    stored = {int(value) for values in (scope.context.bstate.get("stored_targets") or {}).values()
              for value in values}
    return int(uid) in stored


def target_count(field: Any, variables: dict[str, int]) -> int:
    """A target's count: 0 when the field is unset (no limit, C# int.MaxValue), a constant, or the bound value of an
    ability variable (0 while that variable is unbound)."""
    if field is None:
        return 0
    kind = str(getattr(field, "type_name", "") or "").rsplit(".", 1)[-1]
    if kind == "TargetConstant":
        return max(0, int(field.m_Value or 0))
    if kind == "TargetVariable":
        return max(0, int(variables.get(str(field.m_AbilityVariableName or ""), 0) or 0))
    raise NotImplementedError(f"target count {kind or 'untyped'}")


def capped(candidates: list[int], count: int) -> list[int]:
    """The first ``count`` candidates of an automatic target (C# GetAutoTargets truncates to the maximum). A count of
    0 is no limit."""
    return candidates[:count] if count > 0 else candidates


def random_picks(candidates: list[int], count: int, bstate: dict[str, Any]) -> list[int]:
    """The cards of a random automatic target: up to ``count`` of the candidates, drawn with the session RNG as the
    live resolver draws them (AbilityTargetTemplate.FilterRandomTargets)."""
    from rules_port import resolution
    return [int(uid) for uid in resolution._random_target_sample(candidates, count, bstate)]
