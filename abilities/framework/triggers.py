"""Generic card-trigger resolution (Deploy, Inspire, Deathcry, attacks, blocks).

A card's abilities live in ``game_cards.card_abilities`` (ability GUIDs, synced
from the template) and ``card_abilities_meta.trigger_event_type`` names the
game event that fires them.  When that event happens, every applicable card
(and the triggering card) with a matching trigger fires, and each ability's
BOM is resolved through the leaf executors in ``bom.py``.

Supported trigger events:
    AsEntersPlayEvent     — Inspire: another troop with cost >= this troop's
                            cost enters play under the same controller
    CardEnteredZoneEvent  — Deploy (self enters play) and Deathcry (self dies)
    CardAttackedEvent     — "when this attacks"
    CardBlockedEvent      — "when this blocks"
    CardInspiredEvent     — "when this inspires a troop"
"""

import json
import random

import game_engine
from gamedata import DEFAULT_RECORD_STORE, ability_graph

from ._shared import (
    _log,
    owner_uid,
)


_RECORD_STORE = DEFAULT_RECORD_STORE


def _card_uses_variable(db, session_id, card_uid, variable_type):
    """Whether a live card has a typed ability variable.

    This follows the current card ability list, so transformed cards and
    Mimic-created copies are evaluated from their instance metadata rather
    than from a card-name special case.
    """
    from pvp_db import db_card_ability_payload
    payload = db_card_ability_payload(session_id, int(card_uid), conn=db)
    if not payload:
        return False
    try:
        ability_guids = json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    for ability_guid in ability_guids or []:
        graph = ability_graph(_RECORD_STORE, str(ability_guid).lower())
        if graph is None:
            continue
        for variable in graph.variables:
            if variable.short_type == variable_type:
                return True
    return False


def _refresh_variable_cards(db, handler, game, session, pl_t, ai_t,
                            bstate, variable_type):
    """Re-send warzone cards whose continuous stats use a changed variable."""
    if not hasattr(handler, "_card_full_data"):
        return
    handler._current_bstate = bstate
    from pvp_db import db_warzone_display_rows
    rows = db_warzone_display_rows(session.session_id, conn=db)
    for card_uid, template_guid, user_id, card_state, card_type in rows:
        if not _card_uses_variable(db, session.session_id, card_uid,
                                   variable_type):
            continue
        scid = game_engine.SessionCardId(game_engine.UID(int(card_uid)))
        try:
            # SessionCardId is a wire wrapper without value equality. Remove
            # an older definition for this UID before rebuilding it, otherwise
            # repeated refreshes leave stale CardDefs in the same event batch.
            for old_scid in list(game.card_defs):
                if int(old_scid.uid.uid64) == int(card_uid):
                    del game.card_defs[old_scid]
            handler._card_full_data(game, scid, template_guid)
            cdef = game.card_defs.get(scid)
            game.push_card_updated(
                scid, owner_uid(user_id, pl_t, ai_t, bstate),
                game_engine.ECardCollections.Warzone,
                game_engine.card_type_from_db(card_type),
                template_id=template_guid,
                attributes=cdef.attributes if cdef else 0,
                state=int(card_state or 0))
        except Exception as exc:
            _log(f"    Static refresh failed for {variable_type} card "
                 f"{card_uid}: {exc}")


def _card_ability_guids(db, session_id, card_uid):
    """Ability GUIDs currently on a specific card instance."""
    from pvp_db import db_card_ability_payload
    payload = db_card_ability_payload(session_id, int(card_uid), conn=db)
    if not payload:
        return []
    try:
        return [g.lower() for g in json.loads(payload)]
    except (ValueError, TypeError):
        return []


def ability_matches_keyword(db, ability_guid, keyword):
    """Match a current ability against ActivateTriggered's typed keyword.

    The client stores keyword flags in the ability TAC for Deathcry and uses
    the event type for Momentum powers.  The current Records contract is the
    only rules-data source.
    """
    key = str(keyword or "").lower()
    if key.endswith("ies"):
        key = key[:-3] + "y"
    elif key.endswith("s"):
        key = key[:-1]
    graph = ability_graph(_RECORD_STORE, str(ability_guid).lower())
    if graph is None:
        return False
    if key == "deathcry":
        try:
            from .tac import _tac_attr_hash, decode_tac
            tac = graph.source.field("m_SerializedTAC")
            data = (tac.field("data", "") if hasattr(tac, "field")
                    else tac.get("data", "") if isinstance(tac, dict)
                    else "")
            if data and _tac_attr_hash("Deathcry") in decode_tac(data):
                return True
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        return False
    if key == "momentum":
        return "CardInspiredEvent" in graph.trigger_event_type
    return False


def _ability_is_secret(graph):
    """Read the client's ability-level Secret TAC flag.

    ``secretly`` is parsed by the client into ``AbilityTemplate.tac`` rather
    than into the localized game text or a CardModifier field. The extracted
    Records retain that serialized TAC, so use the same flag here instead of
    recognizing a card name or a phrase in card text.
    """
    try:
        from .tac import _tac_attr_hash, decode_tac
        serialized = graph.source.field("m_SerializedTAC")
        data = (serialized.field("data", "")
                if hasattr(serialized, "field") else
                serialized.get("data", "")
                if isinstance(serialized, dict) else "")
        return bool(data and _tac_attr_hash("Secret") in decode_tac(data))
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return False


def manually_trigger_abilities(db, handler, game, session, pl_t, ai_t,
                               bstate, target_uid, keyword):
    """Resolve all matching triggered powers currently on one card.

    This is the server counterpart of Session.ManuallyTriggerAbilities.  It
    deliberately invokes only the card's current ability list, so temporary
    grants and transformed-card abilities participate naturally.
    """
    ags = _card_ability_guids(db, session.session_id, target_uid)
    if not ags:
        return ""
    from pvp_db import db_card_owner_id
    owner_id = int(db_card_owner_id(
        session.session_id, int(target_uid), conn=db) or 0)
    port_session = getattr(session, "_rules_port_session", None)
    if (port_session is not None or
            bstate.get("_rules_port_attached")):
        from rules_port.resolution import resolve_port_ability
        results = []
        for ag in ags:
            if not ability_matches_keyword(db, ag, keyword):
                continue
            results.append(resolve_port_ability(
                handler, game, session, db, pl_t, ai_t, bstate, ag,
                int(target_uid), owner_id))
        return "; ".join(str(result) for result in results if result)
    from .resolution import resolve_ability
    results = []
    for ag in ags:
        if not ability_matches_keyword(db, ag, keyword):
            continue
        results.append(resolve_ability(
            handler, game, session, db, pl_t, ai_t, bstate, ag,
            int(target_uid), owner_id, {}))
    return "; ".join(str(result) for result in results if result)
def _ai_battle_target(db, session, source_uid, ability_guid, candidates):
    """Choose an AI battle target from the data-defined legal candidates.

    The client AI's useful preference for a battle deploy is a low-health
    flier first, then a random troop that can be killed by the source.  Keep
    the candidate set metadata-driven and only apply that tactical ordering
    when the ability actually contains a Battle2Cards effect.
    """
    if not candidates:
        return None
    from pvp_db import (db_ability_has_effect, db_card_combat_identity,
                        db_battle_target_stats)
    effect = db_ability_has_effect(ability_guid, conn=db)
    if not effect:
        return candidates[0]
    source = db_card_combat_identity(session.session_id, int(source_uid), conn=db)
    damage = max(0, int(source[3] or 0)) if source else 0
    if damage <= 0:
        return random.choice(candidates)
    rows = db_battle_target_stats(session.session_id, candidates, conn=db)
    by_uid = {int(uid): (int(attrs or 0), int(health or 0))
              for uid, attrs, health in rows}
    fliers = [uid for uid in candidates
              if (by_uid.get(int(uid), (0, 0))[0]
                  & game_engine.ECardAttributes.Flight)
              and 0 < by_uid.get(int(uid), (0, 0))[1] < damage]
    if fliers:
        return random.choice(fliers)
    killable = [uid for uid in candidates
                if 0 < by_uid.get(int(uid), (0, 0))[1] <= damage]
    return random.choice(killable or candidates)


def _apply_health_gain(game, bstate, pl_t, ai_t, amount, source_owner_uid,
                       db=None, handler=None, session=None):
    """Gain *amount* champion health for the ability source's controller.

    ``source_owner_uid`` is the DB user_id of the ability source (0 = AI,
    non-zero = the human player).  Updates the authoritative battle-state
    health (``bstate``) plus the transient ``Game``, and pushes the
    ChampionHealthChanged event to the correct champion.  Any warzone card
    whose trigger is ChampionHealedEvent ("When you gain health, ...", e.g.
    Righteous Paladin, Incantation of Righteousness) then fires.
    """
    health_key = "player_health" if source_owner_uid else "ai_health"
    if (bstate or {}).get("pvp"):
        health_key = (bstate or {}).get("pvp_health_map", {}).get(
            int(source_owner_uid or 0), health_key)
    cur = bstate.get(health_key, getattr(game, health_key, 20))
    # Emberspire Witch: "Champions can't gain health." — while she is in play,
    # no champion gains health (continuous static flag from the statics layer).
    if db is not None and handler is not None and session is not None:
        try:
            from .statics import global_flags, health_gain_bonus
            if "cant_gain_health" in global_flags(
                    db, session.session_id, bstate):
                return "prevented: champions can't gain health"
            amount += health_gain_bonus(
                db, session.session_id, bstate, source_owner_uid)
        except Exception:
            pass
    new_val = cur + amount
    bstate[health_key] = new_val
    setattr(game, health_key, new_val)
    ev = game_engine.ChampionHealthChangedSessionEventArgs()
    ev.player_id = owner_uid(source_owner_uid, pl_t, ai_t, bstate)
    ev.old_damage_value = cur
    ev.new_damage_value = new_val
    game._push(ev)
    # Fire "when you gain health" triggers for the healed player's cards.
    if db is not None and handler is not None and session is not None:
        if not (bstate or {}).get("_resolving_healed_event"):
            bstate["_resolving_healed_event"] = True
            try:
                resolve_triggers(db, handler, game, session, pl_t, ai_t,
                                 bstate, "ChampionHealedEvent", None,
                                 source_owner_uid=source_owner_uid)
            finally:
                bstate.pop("_resolving_healed_event", None)
    return f"health +{amount} -> {new_val}"


def _resolve_ability_bom(db, handler, game, session, pl_t, ai_t, bstate,
                         ability_guid, source_uid, game_text, target_uid=None,
                         source_owner_uid=None, trigger_target_uid=None,
                         effect_groups=None):
    """Resolve one current-Records ability through the shared interpreter.

    Trigger dispatch and card-play resolution use the same client-style
    AbilityInstance interpreter.  This compatibility-shaped entry point is
    retained for callers that still use its historical name, but it has no
    alternate flat/BOM implementation.
    """
    from gamedata import ability_graph
    from gamedata.play_plan import ActivationData

    ability_guid = str(ability_guid).lower()
    graph = ability_graph(_RECORD_STORE, ability_guid)
    if graph is None:
        raise RuntimeError(
            f"ability {ability_guid} is missing from current Records")

    target_map = {}
    if target_uid is not None:
        explicit_indexes = [
            index for index, target in enumerate(graph.targets)
            if target.requires_input and target.explicit
        ]
        indexes = explicit_indexes or ([0] if graph.targets else [])
        target_map = {
            index: (int(target_uid),) for index in indexes
        }

    previous = {
        key: bstate.get(key)
        for key in ("resolving_target_uid", "resolving_trigger_target_uid",
                    "player_spell_target", "player_mod_target",
                    "grant_target")
    }
    bstate["resolving_target_uid"] = target_uid
    bstate["resolving_trigger_target_uid"] = (
        target_uid if trigger_target_uid is None else trigger_target_uid)
    bstate["resolving_ability"] = ability_guid
    bstate["resolving_owner_id"] = source_owner_uid or 0
    bstate["resolving_source_uid"] = source_uid
    bstate["grant_target"] = (target_uid if target_uid is not None
                               else source_uid)
    bstate.pop("player_spell_target", None)
    bstate.pop("player_mod_target", None)
    try:
        if bstate.get("_rules_port_native_effect"):
            # A trigger raised while a native effect is resolving must remain
            # inside the RulesPort lifecycle.  This preserves nested ability
            # identity/continuations and prevents the legacy resolver from
            # becoming an invisible second rules engine.
            from rules_port.resolution import resolve_port_ability
            return resolve_port_ability(
                handler, game, session, db, pl_t, ai_t, bstate,
                ability_guid, source_uid, source_owner_uid or 0,
                target_map=target_map)
        from .resolution import resolve_ability
        return resolve_ability(
            handler, game, session, db, pl_t, ai_t, bstate,
            ability_guid, source_uid, source_owner_uid or 0,
            target_map=target_map,
            activation_data=ActivationData.from_values(target_map=target_map),
            effect_groups=effect_groups)
    finally:
        for key, value in previous.items():
            if value is None:
                bstate.pop(key, None)
            else:
                bstate[key] = value



def _resolve_counter_spell(db, handler, game, session, pl_t, ai_t, bstate,
                           eg, param, game_text):
    """CounterSpell leaf — interrupts a target card on the chain."""
    target_uid = (bstate or {}).get("player_spell_target")
    if target_uid is None:
        return "counter: no target on chain"
    from pvp_db import db_card_owner_id, db_card_chain_info
    owner = db_card_owner_id(
        session.session_id, int(target_uid), conn=db) or 0
    from ._shared import owner_uid
    owner_sid = owner_uid(owner, pl_t, ai_t, bstate)
    from pvp_db import db_discard_card
    db_discard_card(session.session_id, int(target_uid), connection=db)
    scid = game_engine.SessionCardId(game_engine.UID(int(target_uid)))
    # Remove the countered card's own item from underneath Countermagic on the
    # chain, otherwise the next pass can resolve the already-countered card.
    stack = (bstate or {}).get("stack")
    if isinstance(stack, list):
        stack[:] = [item for item in stack
                    if int(item.get("source_uid") or 0) != int(target_uid)]
    # A full discard update keeps the client's cached representation out of the
    # hand/chain after the authoritative zone move.
    tpl_row = db_card_chain_info(
        session.session_id, int(target_uid), conn=db)
    card_type = "Troop"
    if tpl_row:
        _tpl, card_type, _name, _cost, _atk, _def, _gems = \
            handler._card_full_data(game, scid, tpl_row[0])
    game.push_card_moved(scid, owner_sid, game_engine.ECardCollections.Discard,
                         game_engine.ECardLocations.Top, 0)
    if tpl_row:
        game.push_card_updated(
            scid, owner_sid, game_engine.ECardCollections.Discard,
            game_engine.card_type_from_db(card_type), template_id=tpl_row[0])
    _log(f"    Countered {hex(int(target_uid))}")
    return f"countered {hex(int(target_uid))}"


def _trigger_collection_allows(trigger_flags, card_location):
    """Mirror the client's Card.PassesCollectionFlagRequirements: a trigger
    only fires while its source card is in one of the ability's
    m_TriggerCollectionFlags zones.  Missing/None flags mean unrestricted;
    unknown card locations are allowed (the client allows None/Simulacrum).
    """
    if not trigger_flags:
        return True
    flags = str(trigger_flags)
    if not flags or flags.strip().lower() in ("none", ""):
        return True
    allowed = {s.strip().lower() for s in flags.split("|") if s.strip()}
    loc = str(card_location or "").lower()
    if not loc:
        return True
    return loc in allowed


def resolve_triggers(db, handler, game, session, pl_t, ai_t, bstate,
                     event_type, source_uid, source_owner_uid=None,
                     extra_target=None, zones=None,
                     event_source_collection=None,
                     event_destination_collection=None,
                     event_previous_state=None, event_int_attribute=None,
                     event_tac=None):
    """Fire every card ability whose trigger_event_type == event_type.

    ``source_uid`` is the card that entered / attacked / blocked / died.
    For ``CardCastEvent``, it is the casting champion and ``extra_target`` is
    the card being cast. ``source_owner_uid`` is the event player's DB id
    (0 = AI).
    Returns a log string.
    """
    bstate = bstate or {}
    from rules_port.triggers import NativeTriggerBackend, TriggerEvent

    # RulesPort owns the live trigger lifecycle. Projection and mode adapters
    # still call this historical function name, so every event goes through the
    # typed dispatcher, including recursive native events.
    return NativeTriggerBackend()(
        db=db, handler=handler, game=game, session=session,
        player_uid=pl_t, ai_uid=ai_t, battle_state=bstate,
        event=TriggerEvent(
            str(event_type).rsplit(".", 1)[-1], source_uid,
            source_owner_uid, extra_target,
            data={"zones": zones,
                  "event_source_collection": event_source_collection,
                  "event_destination_collection": event_destination_collection,
                  "event_previous_state": event_previous_state,
                  "event_int_attribute": event_int_attribute,
                  "event_tac": dict(event_tac or {})}))



def resolve_cards_attacked(db, handler, game, session, pl_t, ai_t, bstate,
                           source_uid, source_owner_uid, attacker_uids):
    """Emit the metadata ``CardsAttackedEvent`` once for an attack group.

    The client raises this champion-scoped event after all attackers are
    declared, not once per card.  Its TAC payload carries the attack count;
    this is what cards such as Diligent Counselor test.  Attack declaration
    has separate PvE/PvP and human/AI entry points, so keep the lifecycle
    boundary here and deduplicate reconnect/replay of the same declaration.
    """
    uids = sorted({int(uid) for uid in (attacker_uids or [])})
    if not uids:
        return ""
    try:
        turn = int((bstate or {}).get("turn_number", 0) or 0)
    except (TypeError, ValueError):
        turn = 0
    marker = "%d:%d:%s" % (
        turn, int(source_owner_uid or 0), ",".join(str(uid) for uid in uids))
    if (bstate or {}).get("_cards_attacked_event_marker") == marker:
        return ""
    bstate["_cards_attacked_event_marker"] = marker
    from .tac import _tac_attr_hash
    return resolve_triggers(
        db, handler, game, session, pl_t, ai_t, bstate,
        "CardsAttackedEvent", int(source_uid),
        source_owner_uid=int(source_owner_uid or 0),
        event_tac={_tac_attr_hash("NumAttackers"): len(uids)})


def resolve_card_battled(db, handler, game, session, pl_t, ai_t, bstate,
                         source_uid, source_owner_uid, target_uid,
                         target_owner_uid):
    """Raise one directional metadata event for a card-to-card battle."""
    if source_uid is None or target_uid is None:
        return ""
    return resolve_triggers(
        db, handler, game, session, pl_t, ai_t, bstate,
        "CardBattledEvent", int(source_uid),
        source_owner_uid=int(source_owner_uid or 0),
        extra_target=int(target_uid))


def resolve_turn_phase_triggers(db, handler, game, session, pl_t, ai_t,
                                bstate, phase, owner_id):
    """Dispatch one metadata-defined TurnPhaseEvent on phase entry.

    The original client enqueues this event from TurnPhaseState.OnEntry. The
    server has several phase drivers (PvE human, PvE AI, and PvP), so keep the
    once-per-phase guard here instead of duplicating it in each driver.
    """
    try:
        phase = int(phase)
        owner_id = int(owner_id or 0)
    except (TypeError, ValueError):
        return ""
    marker = (int(bstate.get("turn_number", 0) or 0), phase, owner_id)
    if bstate.get("_last_turn_phase_event") == marker:
        return ""
    bstate["_last_turn_phase_event"] = marker
    if bstate.get("pvp"):
        # Tournament PvP owns packet projection, but phase-entry trigger
        # discovery/resolution must use the same native RulesPort backend as
        # Practice and the AI path.
        from rules_port.triggers import dispatch_native_trigger
        return dispatch_native_trigger(
            db=db, handler=handler, game=game, session=session,
            player_uid=pl_t, ai_uid=ai_t, battle_state=bstate,
            event_type="TurnPhaseEvent", source_card_id=None,
            source_player_id=owner_id, data={"phase": phase})
    return resolve_triggers(
        db, handler, game, session, pl_t, ai_t, bstate,
        "TurnPhaseEvent", None, source_owner_uid=owner_id)


def resolve_turn_ended_triggers(db, handler, game, session, pl_t, ai_t,
                                 bstate, owner_id):
    """Dispatch one ``TurnEndedEvent`` for the owning turn.

    EndTurn is a retryable checkpoint while its triggered chain waits for
    priority.  Calling the raw dispatcher on every retry re-discovers the
    same persistent triggers (notably Verdant Wyldeboar), pushing an
    unbounded number of identical chain items.  Mirror the phase-entry guard
    above with a turn/owner marker; a new turn gets a new turn number and can
    trigger normally again.
    """
    try:
        turn = int(bstate.get("turn_number", 0) or 0)
        owner_id = int(owner_id or 0)
    except (TypeError, ValueError):
        return ""
    marker = (turn, owner_id)
    if bstate.get("_last_turn_ended_event") == marker:
        return ""
    bstate["_last_turn_ended_event"] = marker
    return resolve_triggers(
        db, handler, game, session, pl_t, ai_t, bstate,
        "TurnEndedEvent", None, source_owner_uid=owner_id)


def resolve_gain_charge_triggers(db, handler, game, session, pl_t, ai_t,
                                 bstate, owner_id, *, amount=1):
    """Dispatch the data-defined event for a newly gained champion charge.

    Charge-point UI updates are not gameplay events by themselves.  Use the
    owning champion as the event source so triggers such as Reactor Bot's
    ``When you gain a charge`` can be discovered by the normal metadata-driven
    trigger dispatcher.
    """
    try:
        owner_id = int(owner_id or 0)
    except (TypeError, ValueError):
        owner_id = 0
    champ = (getattr(handler, "_ai_champ_scid", None)
             if owner_id == 0 else
             getattr(handler, "_player_champ_scid", None))
    if champ is None:
        return ""
    try:
        amount = max(0, int(amount or 0))
    except (TypeError, ValueError):
        amount = 0
    result = ""
    for _ in range(amount):
        result = resolve_triggers(
            db, handler, game, session, pl_t, ai_t, bstate,
            "GainChargeEvent", int(champ.uid.uid64), owner_id)
    return result


def resolve_gain_threshold_triggers(db, handler, game, session, pl_t, ai_t,
                                    bstate, owner_id, color=None):
    """Dispatch the metadata-defined event for a gained threshold."""
    try:
        owner_id = int(owner_id or 0)
    except (TypeError, ValueError):
        owner_id = 0
    champ = (getattr(handler, "_ai_champ_scid", None)
             if owner_id == 0 else
             getattr(handler, "_player_champ_scid", None))
    if champ is None:
        return ""
    old = bstate.get("gain_threshold_color")
    if color is not None:
        bstate["gain_threshold_color"] = int(color)
    try:
        return resolve_triggers(
            db, handler, game, session, pl_t, ai_t, bstate,
            "GainThresholdEvent", int(champ.uid.uid64), owner_id)
    finally:
        if old is None:
            bstate.pop("gain_threshold_color", None)
        else:
            bstate["gain_threshold_color"] = old


def resolve_stack_trigger(handler, game, session, db, pl_t, ai_t, bstate, item):
    """Resolve a triggered ability that was pushed onto the chain."""
    ag = item.get("ability_guid", "")
    # Card-play stack items are drained by the battle engine.  Lightweight
    # trigger harnesses may pass those items through this historical helper;
    # they do not represent an AbilityTemplate and therefore have no ability
    # to interpret here.
    if not ag:
        return ""
    cu = item.get("source_uid")
    target_uid = item.get("target_uid")
    graph = ability_graph(_RECORD_STORE, str(ag).lower())
    if graph is None:
        raise RuntimeError(f"ability {str(ag).lower()} is missing from current Records")
    gtext = graph.game_text
    # The trigger may belong to a PLAYER card (e.g. Adamanthian Scrivener):
    # look up the source card's owner so the effect targets the right champion.
    src_owner = item.get("source_owner_uid")
    if src_owner is None:
        src_owner = 0
    if cu is not None:
        from pvp_db import db_card_owner_id
        orow = db_card_owner_id(session.session_id, int(cu), conn=db)
        if orow is not None:
            src_owner = orow
        elif src_owner == 0:
            # Champion sources are represented by handler-held SessionCardIds,
            # not game_cards rows. Preserve their controller for effects such
            # as Ridge Raiders' opposing-champion damage trigger.
            pchamp = getattr(handler, "_player_champ_scid", None)
            achamp = getattr(handler, "_ai_champ_scid", None)
            if pchamp is not None and int(pchamp.uid.uid64) == int(cu):
                src_owner = (handler.user_profile["id"]
                             if handler.user_profile else 0)
            elif achamp is not None and int(achamp.uid.uid64) == int(cu):
                src_owner = 0
    if item.get("activated_ability_guid"):
        bstate["card_activated_item"] = {
            "kind": "ability",
            "ability_guid": item.get("activated_ability_guid"),
            "source_uid": item.get("activated_source_uid"),
            "target_uid": item.get("activated_target_uid"),
        }
    old_src = bstate.get("resolving_source_uid")
    old_owner = bstate.get("resolving_owner_id")
    old_trigger_target = bstate.get("resolving_trigger_target_uid")
    bstate["resolving_source_uid"] = cu
    bstate["resolving_owner_id"] = src_owner
    bstate["resolving_trigger_target_uid"] = item.get(
        "trigger_target_uid", target_uid)
    if (getattr(session, "_rules_port_session", None) is not None or
            bstate.get("_rules_port_attached")):
        from rules_port.resolution import resolve_port_ability
        target_map = ({0: int(target_uid)} if target_uid is not None else {})
        result = resolve_port_ability(
            handler, game, session, db, pl_t, ai_t, bstate, ag, cu,
            src_owner, target_map=target_map,
            effect_groups=item.get("effect_groups"))
    else:
        result = _resolve_ability_bom(
            db, handler, game, session, pl_t, ai_t, bstate, ag, cu, gtext,
            target_uid=target_uid, source_owner_uid=src_owner,
            trigger_target_uid=item.get("trigger_target_uid"),
            effect_groups=item.get("effect_groups"))
    bstate["resolving_source_uid"] = old_src
    bstate["resolving_owner_id"] = old_owner
    if old_trigger_target is None:
        bstate.pop("resolving_trigger_target_uid", None)
    else:
        bstate["resolving_trigger_target_uid"] = old_trigger_target
    # Some legacy trigger continuations can lose the source target while
    # reattaching the RulesPort session.  If the authored graph explicitly
    # contains PutThisIntoYourDeck and the source is still in play, replay the
    # source-bound mutation (never a card-name special case).
    if cu is not None and any(
            e.concrete_type == "MoveCardToZoneEffectTemplate" and
            (str(e.name).lower() == "putthisintoyourdeck" or
             "deck" in str(e.name).lower())
            for e in graph.effects):
        from pvp_db import db_card_mutation_snapshot, db_move_card_to_deck
        row = db_card_mutation_snapshot(session.session_id, int(cu), conn=db)
        if row and str(row[2]).lower() == "warzone":
            from pvp_db import db_randomly_insert_deck_cards
            db_move_card_to_deck(session.session_id, int(cu), 0, conn=db)
            db.commit()
            db_randomly_insert_deck_cards(session.session_id, int(row[1] or 0),
                                          [int(cu)], connection=db)
            scid = game_engine.SessionCardId(game_engine.UID(int(cu)))
            owner = owner_uid(row[1], pl_t, ai_t, bstate)
            game.push_card_moved(scid, owner, game_engine.ECardCollections.Deck,
                                 game_engine.ECardLocations.Unknown, 0)
            game.push_card_updated(scid, owner, game_engine.ECardCollections.Deck,
                                   game_engine.card_type_from_db("Troop"),
                                   template_id=row[0], state=0, nulling=True)
    return result


def resolve_enters_play_triggers(db, handler, game, session, pl_t, ai_t,
                                 bstate, entering_uid, entering_owner_id,
                                 entering_cost=None, extra_target=None):
    """Fire Deploy (self CardEnteredZone) + Inspire (other troops' AsEntersPlay)."""
    from db import log_req
    logs = []
    # The entering card's own Deploy trigger is evaluated after its zone
    # mutation.  Carry that authoritative destination into the collection
    # gate; relying on a stale pre-move projection suppresses Underground
    # triggers such as Minion of Yazukan's +1 ATK effect.
    from pvp_db import db_card_location, db_card_cost
    destination_row = db_card_location(
        session.session_id, int(entering_uid), conn=db)
    destination_collection = (str(destination_row).lower()
                              if destination_row
                              else "warzone")
    # Callers that move a permanent into play often do not have to carry the
    # cost separately (and tokens may legitimately cost zero).  Resolve it
    # from the entering card's template here so every game mode evaluates the
    # same data-defined Inspire condition.
    if entering_cost is None or int(entering_cost or 0) <= 0:
        crow = db_card_cost(session.session_id, int(entering_uid), conn=db)
        entering_cost = int(crow or 0)
    else:
        entering_cost = int(entering_cost)
    # SourcePlayerBriarLegionVariable is a typed card variable. The match-wide
    # event count is shared by both controllers, so a Briar Legion played by
    # either controller increases the value seen by every Briar.
    try:
        is_briar_counter_card = _card_uses_variable(
            db, session.session_id, entering_uid,
            "SourcePlayerBriarLegionVariable")
    except Exception as exc:
        is_briar_counter_card = False
        _log(f"    Briar Legion metadata lookup failed for {entering_uid}: "
             f"{exc}")
    if is_briar_counter_card:
        if "briar_legions_entered" not in bstate:
            bstate["briar_legions_entered"] = (
                int(bstate.get("player_briar_legions_entered", 0)) +
                int(bstate.get("ai_briar_legions_entered", 0)))
        bstate["briar_legions_entered"] = int(
            bstate.get("briar_legions_entered", 0)) + 1
        try:
            _refresh_variable_cards(
                db, handler, game, session, pl_t, ai_t, bstate,
                "SourcePlayerBriarLegionVariable")
        except Exception as exc:
            _log(f"    Briar Legion static refresh failed: {exc}")
    # Deploy: the entering card's own CardEnteredZone triggers
    logs.append(resolve_triggers(db, handler, game, session, pl_t, ai_t, bstate,
                                 "CardEnteredZoneEvent", entering_uid,
                                 entering_owner_id,
                                 extra_target=extra_target,
                                 event_destination_collection=destination_collection))
    # Deploy and Inspire are both the data-defined AsEntersPlay event.  The
    # old hand-written Inspire loop intentionally skipped the entering card,
    # which meant a self-trigger such as Honeycap's "as this enters play"
    # never ran and a 0/0 Honeycap immediately died to state-based effects.
    # Let the normal trigger-condition evaluator distinguish self triggers
    # from Inspire triggers (including cost/ownership filters) instead.
    logs.append(resolve_triggers(db, handler, game, session, pl_t, ai_t, bstate,
                                 "AsEntersPlayEvent", entering_uid,
                                 entering_owner_id,
                                 extra_target=entering_uid))
    # Hand/stack play and the tunneling Surface path both converge here. A
    # permission granted by an underground source (for example Subterranean
    # Spy) must be recalculated after the source changes zones, otherwise the
    # controller keeps seeing the opponent's hand after the Spy is played.
    from .effects.visibility import refresh_player_visibility
    refresh_player_visibility(
        db, session, handler, game, pl_t, ai_t, bstate)
    if logs:
        log_req("    Enters-play triggers: " + "; ".join(str(l) for l in logs if l))
    return "; ".join(str(l) for l in logs if l)
