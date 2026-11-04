"""ServiceTournaments requests (entry, reconnection, ladder records).

Moved out of hconnect_server.HCPHandler._handle_service_request_legacy.
"""

import gamemodes.tournament_server as tournament_server
from db import log_req
import db
from encoder import encode_datawrapper
from encoder import compress_gzip
from encoder import encode_objfmt_response
from domain.uids import SERVICE_MAIL_UID
from tournament_db import db_tournament_room_for_game
from gamemodes.tournament_engine import _is_corinth_room
from tournament_db import db_tournament_pool
from tournament_db import db_tournament_signup_by_player
from tournament_db import db_tournament_signup_set_async_state
from gamemodes.tournament_engine import CORINTH_SHARD_GUIDS
from gamemodes.tournament_engine import player_handler_lock
from tournament_db import db_tournament_match_session
from gamemodes.tournament_engine import player_handlers
from gamemodes.tournament_engine import push_tournament_room_data
from pvp_db import db_game_session_pids
from gamemodes.tournament_engine import player_decks
from tournament_db import db_tournament_signup_set_status
from tournament_db import db_seed_tournament_pool
from gamemodes.tournament_engine import _push_corinth_deck_construction
from tournament_db import db_tournament_signups_by_tournament
from domain.enums import ETournamentFormats
from pvp_db import db_delete_game_session
from tournament_db import db_tournament_player_run_live
from tournament_db import db_tournament_by_id
from gamemodes.tournament_engine import uid_instance
from gamemodes.tournament_engine import _encode_enter_tournament_error
from gamemodes.tournament_engine import _make_deck_data
from gamemodes.tournament_engine import start_waiting_room_game
from tournament_db import db_tournament_discard_match
from gamemodes.tournament_engine import try_start_corinth_match
from pvp_db import db_apply_tournament_main_deck
from gamemodes.tournament_engine import push_tournament_session_start
from tournament_db import db_tournament_pool_replace
from gamemodes.tournament_engine import record_tournament_forfeit
from tournament_db import db_tournament_status
from tournament_db import db_tournament_retire_player_run


# ServiceTournaments — TryReconnection (25021)
# ServiceTournaments — LadderRecord (70023)
# ServiceTournaments — TestTournamentEntry (25027)
# ServiceTournaments — EnterTournament (25029)
def handle_request_25039(handler, target, instance, data_type, reqid, comp, session_id,
                         conh, inner_obj, inner_bytes, raw_data_wrapper_bytes):
    import hconnect_server as _hc
    # GameEntrance is the async-gauntlet matchmaking boundary.  A
    # saved Corinth deck is applied to the session only when the
    # player starts searching; cancelling search leaves the pool.
    player_uid = int(handler.client_reck_id) if handler.client_reck_id else 0
    tournament_id = int(inner_obj.get("TournamentID", 0) or 0)
    searching = bool(inner_obj.get("searching", False))
    room = db_tournament_room_for_game(tournament_id, conn=db._db)
    pool = db_tournament_pool(tournament_id, player_uid, conn=db._db)
    main_ids = [card_uid for card_uid, _guid, location in pool
                if int(location) == 0]
    corinth_entry = _is_corinth_room(room or {})
    ok = bool(room and (int(room.get("format") or 0) &
                        int(ETournamentFormats.Iconoclast)))
    match = None
    if corinth_entry:
        if searching:
            active_session = None
            # A client can return to the deckbuilder after the
            # battle connection was lost and press Play Game again.
            # That player is already assigned to an unfinished match;
            # do not put them back into the queue (the matcher will
            # correctly exclude them) and do not leave searching=1
            # falsely displayed in the lobby.
            active_match = db_tournament_match_session(
            tournament_id, player_uid, conn=db._db)
            if active_match:
                other_pid = next(
                    (int(pid) for pid in db_game_session_pids(
                    int(active_match[0])) if int(pid) != player_uid),
                    0)
                other_signup = db_tournament_signup_by_player(
                tournament_id, other_pid, conn=db._db)
                other_searching = bool(
                    other_signup and other_signup.get("status") == "active"
                    and int(other_signup.get("searching", 0) or 0))
                if other_searching:
                    # Both players explicitly pressed Play Game after
                    # declining the stale reconnect. Discard only this
                    # unfinished assignment and its game state; it is
                    # not a win or loss in the async event.
                    db_tournament_discard_match(
                    tournament_id, active_match[0], conn=db._db)
                    db_delete_game_session(active_match[0], conn=db._db)
                    db._db.commit()
                    log_req(
                    f">>> GameEntrance: discarded stale Corinth "
                    f"match session={active_match[0]} for new search")
                    active_match = None
                else:
                    # Wait for the opponent's explicit new-search
                    # request; do not silently reconnect this client.
                    db_tournament_signup_set_async_state(
                    tournament_id, player_uid,
                    deck_ready=True, searching=True, conn=db._db)
                    db._db.commit()
                    ok = True
                    active_match = None
                    log_req(
                    f">>> GameEntrance: player {player_uid} "
                    f"requested new search; awaiting opponent")
            else:
                active_session = None
            if active_session:
                # The existing match has been re-announced above;
                # skip new-search setup for this request.
                pass
            else:
                ok = ok and len(main_ids) == 20
                if ok:
                    # Corinth is a persistent async event. A prior
                    # LeaveTournament marks the signup withdrawn, but
                    # an explicit Play Game/Find Player action is a
                    # new search request and must re-enter that queue.
                    signup = db_tournament_signup_by_player(
                    tournament_id, player_uid, conn=db._db)
                    if signup and signup.get("status") != "active":
                        db_tournament_signup_set_status(
                        tournament_id, player_uid, "active", conn=db._db)
                    db_tournament_signup_set_async_state(
                    tournament_id, player_uid,
                    deck_ready=True, searching=True,
                    conn=db._db)
                    db._db.commit()
                    try_start_corinth_match(
                    tournament_id, handler_override=handler)
        else:
            # Cancelling the search leaves the saved deck/pool
            # intact but removes this entrant from the match queue.
            db_tournament_signup_set_async_state(
            tournament_id, player_uid, searching=False, conn=db._db)
            db._db.commit()
        # Searching is a valid asynchronous state even when no
        # opponent is available yet; the match transition is pushed
        # later when the second ready player arrives.
    elif searching:
        ok = ok and len(main_ids) == 20
        match = (db_tournament_match_session(
        tournament_id, player_uid, conn=db._db)
                  if ok else None)
        ok = ok and bool(match)
        if ok:
            db_apply_tournament_main_deck(
            match[0], player_uid, main_ids, conn=db._db)
    log_req(f">>> GameEntrance (dt=25039) tid={tournament_id} "
        f"player={player_uid} searching={searching} ok={ok}")
    resp_inner = encode_objfmt_response(
    ["Game.Shared.Network.Tournaments.GameEntranceResponseArgs",
     "System.Boolean"], [("success", "bool", bool(ok))])
    resp_body = compress_gzip(resp_inner) if comp else resp_inner
    dw_bytes = encode_datawrapper(reqid | 1, data_type, resp_body,
                              comp, session_id)
    handler.scnt += 1
    handler.send({
        "issuer": f"0.0.0.0.ServiceTournaments.{SERVICE_MAIL_UID}.ServicePlayer.{handler.client_uid}.{reqid | 1}",
        "target": target, "instance": instance, "reqid": reqid | 1,
        "c": comp, "conh": conh, "sid": handler.sid,
    }, dw_bytes)
    # Corinth matchmaking is started inside try_start_corinth_match
    # once two ready players exist; that path pushes the session-start
    # events to both handlers.  Do not index ``match`` for the normal
    # one-player-waiting case.
    if ok and searching and match:
        push_tournament_session_start(
        handler, tournament_id, int(match[0]),
        f"tourney-{tournament_id}", 0, room)


LEGACY_HANDLERS = {
    25039: handle_request_25039,
}
