"""Trigger events and trigger conditions the interpreter runs (gap area C).

rules_port/ir_exec.py asks this module three things: whether an event's trigger type is one the interpreter runs
(``TRIGGER_EVENTS``), which fields that event adds to the interpreter's scope (``event_fields``), and whether one of
its trigger conditions holds (``condition_holds``). Anything not listed here stays refused, so the native resolver
keeps those abilities.
"""
from __future__ import annotations

from typing import Any, Callable

# Trigger types the interpreter runs. CardEnteredZoneEvent, TurnStartedEvent and TurnEndedEvent are in ir_exec.
TRIGGER_EVENTS = {
    "CardDealtDamageEvent", "CardAttackedEvent", "CardCastEvent", "CardDrawnEvent",
    "CardDiscardedEvent", "CardCreatedEvent", "AsEntersPlayEvent", "CardGainedIntAttrEvent",
    "GameStartedEvent", "PreGameEvent", "CardReadiedEvent", "CardBlockedEvent", "CardSacrificedEvent",
    # The three events that carry nothing but a source card and its player, so the interpreter needs no more than the
    # dispatch already gives it. CardExitedZoneEvent also carries the two collections (its own ``from``/``to``).
    "CardExitedZoneEvent", "GainChargeEvent", "ChampionHealedEvent", "CardTransformsEvent",
    "CardActivatedEvent", "CardBattledEvent", "FateweavedEvent",
}

# Trigger condition classes this module evaluates.
CONDITIONS = {"NotTriggerCondition", "TriggerPlayerControlsTarget", "TriggerEventIsCombatDamage",
              "OrTriggerCondition", "TriggerEventIntAttribute"}


def event_fields(event_type: str, trigger_source_uid: Any, target_player_uid: Any,
                 data: dict[str, Any]) -> dict[str, Any]:
    """The fields a live event adds to the interpreter's scope.

    ``card`` is the trigger source (the native ``ConditionContext.trigger_uid``), ``target_player`` is the event's
    TargetPlayerId (the damaged card's controller for a damage event), ``event_type`` is the event's class name, and
    ``int_attribute`` is the attribute a CardGainedIntAttrEvent reports. ``player`` is set by ``ir_exec.live_event``
    from the event's own SourcePlayerId, not from here: this function also serves coverage and the synthetic events."""
    return {"event_type": event_type, "card": trigger_source_uid, "target_player": target_player_uid,
            "int_attribute": data.get("event_int_attribute")}


def condition_holds(cls: str, condition: Any, scope: Any, holds: Callable[[Any, Any], bool]) -> bool:
    """Whether a trigger condition of class ``cls`` holds for ``scope``. ``holds`` evaluates a nested condition, so a
    ``NotTriggerCondition`` checks its child the way the interpreter checks any trigger condition."""
    if cls == "NotTriggerCondition":
        child = getattr(condition, "m_Condition", None)
        if child is None:
            raise NotImplementedError("NotTriggerCondition without a condition")
        return not holds(child, scope)
    if cls == "OrTriggerCondition":
        children = list(getattr(condition, "m_Conditions", None) or [])
        if not children:
            raise NotImplementedError("OrTriggerCondition without a condition")
        return any(holds(child, scope) for child in children)
    if cls == "TriggerPlayerControlsTarget":
        # The ability's source controller is the event's TargetPlayerId (C# TriggerPlayerControlsTarget.IsValid).
        target = scope.event.get("target_player")
        if target is None:
            # The event carries no target player here; refuse it rather than guess a side (the native path decides).
            raise NotImplementedError("the event names no target player")
        return int(target) == int(scope.owner_uid)
    if cls == "TriggerEventIntAttribute":
        # C# TriggerEventIntAttribute: the event is an attribute gain, and it names this attribute.
        return (scope.event.get("event_type") == "CardGainedIntAttrEvent" and
                str(getattr(condition, "m_Attribute", "") or "") == str(scope.event.get("int_attribute") or ""))
    if cls == "TriggerEventIsCombatDamage":
        # A damage event is combat damage when it is CardDealtDamageEvent (the native check, for the events here).
        return scope.event.get("event_type") == "CardDealtDamageEvent"
    raise NotImplementedError(f"trigger condition {cls}")
