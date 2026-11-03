"""Zone-move projection helper shared by the AI and effect code."""

import game_engine

from .._shared import owner_uid


def _push_card_in_zone(game, session, db, handler, pl_t, ai_t, bstate,
                       uid, location):
    from pvp_db import db_card_source_info
    source = db_card_source_info(session.session_id, int(uid), conn=db)
    row = (source[0], source[3]) if source else None
    if not row:
        return
    from .._shared import card_collection_for_location
    scid = game_engine.SessionCardId(game_engine.UID(int(uid)))
    _tpl, ct, _name, cost, atk, defense, _gem = handler._card_full_data(
        game, scid, row[0])
    owner = owner_uid(row[1], pl_t, ai_t, bstate)
    collection = card_collection_for_location(location)
    game.push_card_moved(scid, owner, collection,
                         game_engine.ECardLocations.Top, 0)
    game.push_card_updated(scid, owner, collection, ct, template_id=row[0],
                           cost=cost, attack=atk, defense=defense,
                           nulling=(str(location).lower() == "deck"))
    from .visibility import refresh_player_visibility
    refresh_player_visibility(
        db, session, handler, game, pl_t, ai_t, bstate)


