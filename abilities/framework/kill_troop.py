"""Kill a troop (Dead state, move to graveyard, resolve Deathcry)."""

import game_engine as _ge


def kill_troop(game, session, db, handler, pl_t, ai_t, card_uid, bstate=None,
               cause="effect", defer_deathcry=False, deferred=None):
    """Kill a troop: Dead state, move to the owner's graveyard, push events,
    and resolve any Deathcry trigger.

    ``cause`` is "damage" | "effect" | "state" | "sacrifice".  Immortal troops
    survive "damage" and "effect" deaths but still die to "state" and "sacrifice".

    ``defer_deathcry`` (with ``deferred``, a list) skips the Deathcry resolution
    here and appends ``(card_uid, template_guid, owner_id)`` instead so the
    caller (combat) can resolve all Deathcries after the full damage assignment.
    """
    from ._shared import _log, owner_uid

    from pvp_db import db_card_death_info, db_kill_card_to_discard
    row = db_card_death_info(session.session_id, int(card_uid), conn=db)
    if not row:
        return
    tpl_guid, owner_id, attrs = row[0], row[1], (row[2] or 0)
    if attrs & _ge.ECardAttributes.Immortal and cause in ("damage", "effect"):
        _log(f"    Immortal {hex(card_uid)} survives {cause} death")
        return
    # Replacement: "If a card would enter a crypt..." (Booby Trap, Frost
    # Wizard) — a resolved trigger voids/replaces the card instead of the
    # normal move to the graveyard.
    if bstate is not None:
        from .triggers import resolve_triggers
        if resolve_triggers(db, handler, game, session, pl_t, ai_t, bstate,
                            "CardWouldEnterZoneEvent", int(card_uid),
                            source_owner_uid=owner_id):
            _log(f"    CardWouldEnterZone replaced death of {hex(card_uid)}")
            return
    db_kill_card_to_discard(
        session.session_id, int(card_uid),
        _ge.ECardStates.CameOutThisTurn | _ge.ECardStates.Tapped |
        _ge.ECardStates.Attacking | _ge.ECardStates.HasAttacked |
        _ge.ECardStates.Blocking | _ge.ECardStates.HasBlocked |
        _ge.ECardStates.Damaged,
        _ge.ECardStates.Dead, conn=db)
    import game_engine
    scid = game_engine.SessionCardId(game_engine.UID(int(card_uid)))
    _tpl, ct, _n, _c, atk, def_, _g = handler._card_full_data(game, scid, tpl_guid)
    owner = owner_uid(owner_id, pl_t, ai_t, bstate)
    game.push_card_moved(scid, owner, _ge.ECardCollections.Discard,
                         _ge.ECardLocations.Top, 0)
    game.push_card_updated(scid, owner, _ge.ECardCollections.Discard, ct,
                           attack=atk, defense=def_, template_id=tpl_guid)
    _log(f"    Killed {hex(card_uid)} ({cause})")

    # A card leaving the warzone fires its "when this leaves play" triggers
    # (e.g. Solitary Exile: "put each card voided by it into play").
    if bstate is not None:
        from .triggers import resolve_triggers
        resolve_triggers(db, handler, game, session, pl_t, ai_t, bstate,
                         "CardExitedZoneEvent", int(card_uid),
                         source_owner_uid=owner_id)
        # The card entered the crypt — "when a card enters an opposing crypt"
        # triggers (e.g. Incantation of Fear) fire here.
        resolve_triggers(db, handler, game, session, pl_t, ai_t, bstate,
                         "CardEnteredZoneEvent", int(card_uid),
                         source_owner_uid=owner_id,
                         event_source_collection="warzone",
                         event_destination_collection="discard",
                         event_previous_state=_ge.ECardStates.Dead)
        if cause == "sacrifice":
            resolve_triggers(db, handler, game, session, pl_t, ai_t, bstate,
                             "CardSacrificedEvent", int(card_uid),
                             source_owner_uid=owner_id)

    if defer_deathcry and deferred is not None:
        deferred.append((int(card_uid), tpl_guid, owner_id))
    else:
        from .deathcry import resolve_deathcry
        resolve_deathcry(game, session, db, handler, pl_t, ai_t, card_uid,
                         tpl_guid, bstate)


