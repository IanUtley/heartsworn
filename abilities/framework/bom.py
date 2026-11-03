"""Card-state projection and target-controller helpers.

These are what remains of the old BOM leaf-executor module: RulesPort now
resolves every effect natively and only these two helpers are still called.
"""

import game_engine

from ._shared import owner_uid


def _push_card_state(game, session, db, handler, pl_t, ai_t, uid, new_state,
                     bstate=None):
    """Push a CardUpdated in the card's authoritative current collection."""
    from ._shared import card_collection_for_location
    from pvp_db import db_card_zone_details
    details = db_card_zone_details(session.session_id, int(uid), conn=db)
    trow = (details[0], details[2], details[3]) if details else None
    if not trow:
        return
    scid = game_engine.SessionCardId(game_engine.UID(int(uid)))
    _tpl, ct, _n, cost, atk, def_, _g = handler._card_full_data(game, scid, trow[0])
    # In Practice, user_id=0 is the AI and any non-zero id is the human.  PvP
    # has two real non-zero player ids, so use the PvP-aware owner mapping or
    # a CardUpdated for an opponent's damaged troop is rendered under the
    # caster's controller and appears to change sides.  Damage normally
    # supplies bstate; the UID check covers older leaf callers that do not.
    if ((bstate and bstate.get("pvp")) or
            (int(pl_t.uid64) & 0xff) == 244 and
            (int(ai_t.uid64) & 0xff) == 244):
        owner = game_engine.UID.make(244, int(trow[1]))
    else:
        owner = owner_uid(trow[1], pl_t, ai_t, bstate)
    game.push_card_updated(scid, owner, card_collection_for_location(trow[2]), ct,
                           template_id=trow[0], attack=atk, defense=def_,
                           cost=cost, state=new_state,
                           nulling=(trow[2] == "deck"))


def _controller_id_for_target(db, session, handler, bstate, target_uid):
    """Return the DB player id that controls a card or champion target."""
    from pvp_db import db_card_owner_id
    card_owner = db_card_owner_id(
        session.session_id, int(target_uid), conn=db)
    if card_owner is not None:
        return int(card_owner)
    if (bstate or {}).get("pvp"):
        for pid, cuid in ((bstate or {}).get("champ_map") or {}).items():
            try:
                if int(cuid) == int(target_uid):
                    return int(pid)
            except (TypeError, ValueError):
                continue
        # Headless PvP fixtures can have the handler's champion SessionCardIds
        # before the live session has populated champ_map.  Resolve ownership
        # from the fixture's player order in that narrow fallback case.
        pids = (bstate or {}).get("pids") or []
        if len(pids) >= 2:
            player_champ = getattr(handler, "_player_champ_scid", None)
            ai_champ = getattr(handler, "_ai_champ_scid", None)
            if player_champ is not None and int(
                    player_champ.uid.uid64) == int(target_uid):
                return int(pids[0])
            if ai_champ is not None and int(ai_champ.uid.uid64) == int(
                    target_uid):
                return int(pids[1])
    for attr, owner in (("_player_champ_scid", handler.user_profile["id"]
                         if handler.user_profile else 0),
                        ("_ai_champ_scid", 0)):
        champ = getattr(handler, attr, None)
        if champ is not None and int(champ.uid.uid64) == int(target_uid):
            return int(owner)
    return None


# ---------------------------------------------------------------------------
#  BOM helpers
# ---------------------------------------------------------------------------


