"""Historical trigger entry points that forward to ``rules_port.triggers``.

Projection and mode adapters still call ``resolve_triggers`` /
``resolve_enters_play_triggers`` by these names; every event is dispatched
by the native RulesPort trigger backend.
"""

import json

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
