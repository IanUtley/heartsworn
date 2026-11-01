"""Client game-rules abilities appended to every champion card.

``Session.AddGameRulesAbilitiesToCardTemplate`` gives each champion card
template (and only champion templates: the callers are the champion,
mercenary and champion-transform paths) the ``BuiltInResources`` keyword
abilities parsed in ``Parser.cs`` (lines ~220-266).  Unlike Tunneling/Surface
these are ordinary authored ``AbilityTemplate`` Records rows, so the server
attaches them to the champion's trigger holders and lets the native trigger
engine resolve their authored conditions, targets and effects:

* Burning ``82598d69`` - "At the end of your turn, if you are Burning,
  #IGNORE_CHAIN# deal damage to this equal to the number of burning counters
  on this. Remove a burning counter from this."
* Dazed removal ``fa620e3b`` - "At the end of your turn, if you are Dazed,
  #IGNORE_CHAIN# remove a dazed counter from this."
* Vulnerable removal ``1c938dd8`` - "At the end of your turn, #IGNORE_CHAIN#
  remove a vulnerable counter from your Vulnerable troops in all zones or
  your Vulnerable champion."
* SoulCursed ``e2464b7a`` - "When a troop you control with Soulcursed dies,
  sacrifice each troop you control with Soulcursed."

The two continuous built-ins are projected here as typed rules:

* Dazed ``16ffaa33`` - "While you are Dazed, non-resource cards in your hand
  have cost +1."  (:func:`dazed_cost_increase`)
* Vulnerable ``79c61378`` - "Your Vulnerable champion or your Vulnerable
  troops take double damage."  (:func:`vulnerable_damage_factor`)

Stealth's three built-ins live in :mod:`rules_port.stealth`; Tunneling has
its own lifecycle service.  Neither is listed here, so they cannot resolve
twice.
"""

from __future__ import annotations

import json


BURNING_COUNTER_GUID = "3932ad3a-9807-de2a-e1b9-a8b50e81def9"
DAZED_COUNTER_GUID = "9c98dace-4cac-0fda-8242-f7ef2c1d3cf0"
VULNERABLE_COUNTER_GUID = "b01d4e3c-bae6-c55f-ff4e-db0513d74484"

BURNING_ABILITY_GUID = "82598d69-0fc3-4fb6-9a55-b03fb8b3362a"
DAZED_ABILITY_GUID = "16ffaa33-6b56-4e77-a9b9-c5fd8db4420a"
DAZED_REMOVAL_ABILITY_GUID = "fa620e3b-5a6c-4c96-9491-973105a6f3e1"
VULNERABLE_ABILITY_GUID = "79c61378-3764-430e-ae8b-efebe258701d"
VULNERABLE_REMOVAL_ABILITY_GUID = "1c938dd8-fb9b-4a99-8dd5-c97b906b2e10"
SOULCURSED_ABILITY_GUID = "e2464b7a-15f1-46a2-8b9b-74e03f313043"

# Event-triggered built-ins resolved by the native trigger engine from their
# Records graphs.  Every champion carries them; the authored
# TriggerPlayerControlsAbilitySource condition limits each to its controller.
CHAMPION_TRIGGER_ABILITY_GUIDS = (
    BURNING_ABILITY_GUID,
    DAZED_REMOVAL_ABILITY_GUID,
    VULNERABLE_REMOVAL_ABILITY_GUID,
    SOULCURSED_ABILITY_GUID,
)


_TRIGGER_EVENTS = {
    BURNING_ABILITY_GUID: "TurnEndedEvent",
    DAZED_REMOVAL_ABILITY_GUID: "TurnEndedEvent",
    VULNERABLE_REMOVAL_ABILITY_GUID: "TurnEndedEvent",
    SOULCURSED_ABILITY_GUID: "CardEnteredZoneEvent",
}


def champion_trigger_abilities(db, session_id, battle_state, owner,
                               champion_uid, event_type):
    """Return the built-in trigger GUIDs one champion listens with.

    Only the built-ins whose authored trigger event is ``event_type`` are
    returned, so other events pay nothing.  Vulnerable removal is the one
    built-in without an authored "if you are ..." trigger condition and with
    a zero-minimum target; the client would activate (and flash) it on every
    end of turn with nothing to remove, so it is offered only while its
    controller actually has a Vulnerable champion or troop.
    """
    event_name = str(event_type or "").rsplit(".", 1)[-1]
    result = []
    for guid, wanted in _TRIGGER_EVENTS.items():
        if wanted != event_name:
            continue
        if (guid == VULNERABLE_REMOVAL_ABILITY_GUID and
                not owner_has_counter(db, session_id, battle_state, owner,
                                      champion_uid, VULNERABLE_COUNTER_GUID)):
            continue
        result.append(guid)
    return result


def owner_has_counter(db, session_id, battle_state, owner, champion_uid,
                      counter_guid):
    """Whether a player's champion or any card they control has the counter."""
    if champion_counter(battle_state, champion_uid, counter_guid) > 0:
        return True
    from pvp_db import db_owner_card_uids_with_counters
    for (uid,) in db_owner_card_uids_with_counters(
            session_id, int(owner or 0), conn=db):
        if card_counter(db, session_id, uid, counter_guid) > 0:
            return True
    return False


def champion_counter(battle_state, champion_uid, counter_guid):
    """Return one counter value persisted on a synthetic champion."""
    if champion_uid is None:
        return 0
    try:
        key = str(int(getattr(champion_uid, "uid64", champion_uid)))
    except (TypeError, ValueError):
        return 0
    values = ((battle_state or {}).get("champion_counters") or {}).get(key, {})
    if not isinstance(values, dict):
        return 0
    try:
        return int(values.get(str(counter_guid).lower(), 0) or 0)
    except (TypeError, ValueError):
        return 0


def card_counter(db, session_id, card_uid, counter_guid):
    """Return one counter value persisted on an ordinary ``game_cards`` row.

    Card counters are keyed by their lowercase name with a name->GUID map
    beside them (``counter_effects.change_counter``); match on the template
    identity first and fall back to the counter template's own name.
    """
    from pvp_db import db_card_mutation_field
    try:
        data = json.loads(db_card_mutation_field(
            session_id, int(card_uid), "permanent_buffs", conn=db) or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0
    if not isinstance(data, dict):
        return 0
    counters = data.get("counters") or {}
    guids = data.get("counter_guids") or {}
    wanted = str(counter_guid).lower()
    name = _counter_name(wanted)
    total = 0
    for key, value in (counters.items() if isinstance(counters, dict) else ()):
        mapped = str((guids or {}).get(key) or "").lower()
        if mapped == wanted or (not mapped and name and
                                str(key).lower() == name):
            try:
                total += int(value or 0)
            except (TypeError, ValueError):
                continue
    return total


def _counter_name(counter_guid):
    try:
        from gamedata import DEFAULT_RECORD_STORE
        record = DEFAULT_RECORD_STORE.get("CardCounterTemplate", counter_guid)
        return str(record.field("m_Name", "") or "").lower() if record else ""
    except (AttributeError, TypeError, ValueError):
        return ""


def dazed_cost_increase(handler, battle_state, owner_id, card_type):
    """Built-in Dazed: +1 cost for a non-resource hand card while Dazed.

    The authored ability is a ``WhileCardInPlay`` cost modifier whose
    constant ``P1`` is 1; the condition is the controller's champion holding
    one or more Dazed counters.  Returns the additive cost delta.
    """
    if "resource" in str(card_type or "").lower():
        return 0
    from .runtime_helpers import champion_uid_for_owner
    champion = champion_uid_for_owner(handler, battle_state, owner_id)
    if champion_counter(battle_state, champion, DAZED_COUNTER_GUID) <= 0:
        return 0
    return _ability_constant(DAZED_ABILITY_GUID, "P1", 1)


def _ability_constant(ability_guid, name, default):
    try:
        from gamedata import DEFAULT_RECORD_STORE
        record = DEFAULT_RECORD_STORE.get("AbilityTemplate", ability_guid)
        for variable in (record.field("m_Variables", []) or []) if record else ():
            get = (variable.field if hasattr(variable, "field") else
                   variable.get)
            if str(get("m_Name", "")) == name:
                return int(get("m_DefaultValue", default) or default)
    except (AttributeError, TypeError, ValueError):
        pass
    return int(default)


def vulnerable_damage_factor(context, target, is_champion):
    """Built-in Vulnerable: a Vulnerable champion or troop takes double damage.

    The target filter is "your Vulnerable champion or your Vulnerable troops":
    the champion carrying the ability multiplies damage to itself and to any
    troop its controller controls, provided that card has a Vulnerable
    counter.  Both champions carry the ability, so the controller is always
    covered.
    """
    from . import ir_exec
    if VULNERABLE_ABILITY_GUID in ir_exec.ENABLED:
        value = ir_exec.static_int_set(context, target, "DamageReceivedMultiplier")
        return 1 if value is None else value
    if is_champion:
        count = champion_counter(context.bstate, target, VULNERABLE_COUNTER_GUID)
    else:
        count = card_counter(context.db, context.session.session_id, target,
                             VULNERABLE_COUNTER_GUID)
    return 2 if count > 0 else 1


__all__ = [
    "BURNING_ABILITY_GUID", "BURNING_COUNTER_GUID",
    "CHAMPION_TRIGGER_ABILITY_GUIDS", "DAZED_ABILITY_GUID",
    "DAZED_COUNTER_GUID", "DAZED_REMOVAL_ABILITY_GUID",
    "SOULCURSED_ABILITY_GUID", "VULNERABLE_ABILITY_GUID",
    "VULNERABLE_COUNTER_GUID", "VULNERABLE_REMOVAL_ABILITY_GUID",
    "card_counter", "champion_counter", "dazed_cost_increase",
    "vulnerable_damage_factor",
]
