"""Tournament request handlers that answer with fixed response shapes.

Served through the services registry. Each handler builds its response from
the request's reqid and sends it on the connection.
"""

import threading

import gamemodes.tournament_server as tournament_server
from encoder import compress_gzip, encode_datawrapper, encode_objfmt_response
from db import log_req
import db
from gamemodes.tournament_engine import record_tournament_forfeit
from pvp_db import db_delete_game_session
from tournament_db import (db_tournament_by_id, db_tournament_match_session,
                           db_tournament_retire_player_run, db_tournament_status)


# Waiting rooms created by CreateWaitingRoom, keyed by room id, and the lock and
# counter that guard them. Owned here, where the tournament handlers use them.
_waiting_rooms = {}
_waiting_room_lock = threading.Lock()
_waiting_room_next_id = 1


def _next_waiting_room_id():
    global _waiting_room_next_id
    rid = _waiting_room_next_id
    _waiting_room_next_id += 1
    return rid


def _send(handler, target, instance, reqid, comp, session_id, conh, data_type,
          resp_inner, label, SERVICE_MAIL_UID):
    """Wrap a response body in a DataWrapper and send it on the connection."""
    resp_body = compress_gzip(resp_inner) if comp else resp_inner
    dw_bytes = encode_datawrapper(reqid | 1, data_type, resp_body, comp, session_id)
    handler.scnt += 1
    handler.send({
        "issuer": f"0.0.0.0.ServiceTournaments.{SERVICE_MAIL_UID}.ServicePlayer."
                  f"{handler.client_uid}.{reqid | 1}",
        "target": target, "instance": instance, "reqid": reqid | 1,
        "c": comp, "conh": conh, "sid": handler.sid,
    }, dw_bytes)
    if label:
        log_req(f"    Sent {label} response ({len(dw_bytes)}b)")


def handle_unsubscribe_description_listener(handler, target, instance, reqid, comp,
                                            session_id, conh, inner_obj, inner_bytes,
                                            *, SERVICE_MAIL_UID, log_req, data_type, **_kw):
    """Acknowledge an UnsubscribeDescriptionListener request."""
    log_req(">>> UnsubscribeDescriptionListener — ack")
    resp_inner = encode_objfmt_response(
        ["Game.Shared.Network.Tournaments.UnsubscribeDescriptionListenerResponseArgs"], [])
    _send(handler, target, instance, reqid, comp, session_id, conh, data_type,
          resp_inner, "UnsubscribeDescriptionListener", SERVICE_MAIL_UID)


def handle_get_tournament_info(handler, target, instance, reqid, comp, session_id,
                               conh, inner_obj, inner_bytes,
                               *, SERVICE_MAIL_UID, log_req, data_type, **_kw):
    """Answer GetTournamentInfo with the requested tournament id."""
    tournament_id = int(inner_obj.get("TournamentID", 0))
    log_req(f">>> GetTournamentInfo: tid={tournament_id}")
    resp_inner = encode_objfmt_response(
        ["Game.Client.Network.Tournaments.GetTournamentInfoResponse",
         "Game.Shared.Tournaments.TournamentInfo",
         "System.UInt64",
         "Game.Shared.Network.Tournaments.EGetTournamentInfoError",
         "System.String"],
        [("Results", "struct", ("Game.Shared.Tournaments.TournamentInfo", [
            ("TournamentID", "ulong", tournament_id)])),
         ("Error", "enum1", ("Game.Shared.Network.Tournaments.EGetTournamentInfoError", 0)),
         ("ErrorMessage", "string", "")])
    _send(handler, target, instance, reqid, comp, session_id, conh, data_type,
          resp_inner, "GetTournamentInfo", SERVICE_MAIL_UID)


def handle_leave_waiting_room(handler, target, instance, reqid, comp, session_id,
                              conh, inner_obj, inner_bytes,
                              *, SERVICE_MAIL_UID, log_req, data_type, **_kw):
    """Remove the player from any waiting room and acknowledge."""
    log_req(">>> LeaveWaitingRoom")
    player_uid = int(handler.client_reck_id) if handler.client_reck_id else 0
    tournament_server.leave_all(player_uid)
    resp_inner = encode_objfmt_response(
        ["Game.Shared.Network.Tournaments.LeaveWaitingRoomResponseArgs"], [])
    _send(handler, target, instance, reqid, comp, session_id, conh, data_type,
          resp_inner, "LeaveWaitingRoom", SERVICE_MAIL_UID)


def handle_pooled_messaging_flush(handler, target, instance, reqid, comp, session_id,
                                  conh, inner_obj, inner_bytes,
                                  *, SERVICE_MAIL_UID, log_req, data_type, **_kw):
    """Acknowledge a pooled-messaging flush request."""
    log_req(">>> PooledMsg reqflush — acking")
    resp_inner = encode_objfmt_response(
        ["Game.Shared.PooledMessaging.PooledMessagingRequestsInterface+Response"], [])
    _send(handler, target, instance, reqid, comp, session_id, conh, data_type,
          resp_inner, None, SERVICE_MAIL_UID)


def handle_ladder_tier_rewards_list(handler, target, instance, reqid, comp, session_id,
                                    conh, inner_obj, inner_bytes,
                                    *, SERVICE_MAIL_UID, log_req, data_type, **_kw):
    """Answer LadderTierRewardsList with no rewards."""
    log_req(">>> Respond LadderTierRewardsList — empty")
    resp_inner = encode_objfmt_response(
        ["Game.Shared.Tournaments.Messages.Tournament+LadderTierRewardsList+Response"],
        [("tier_rewards", "coll", ("System.Collections.Generic.Dictionary`2#System.UInt32!Game.Shared.Tournaments.TournamentRewardInfo", 0))])
    _send(handler, target, instance, reqid, comp, session_id, conh, data_type,
          resp_inner, "LadderTierRewardsList", SERVICE_MAIL_UID)


def handle_ladder_ranking_list(handler, target, instance, reqid, comp, session_id,
                               conh, inner_obj, inner_bytes,
                               *, SERVICE_MAIL_UID, log_req, data_type, **_kw):
    """Answer LadderRankingList with empty rankings."""
    log_req(">>> Respond LadderRankingList — empty")
    resp_inner = encode_objfmt_response(
        ["Game.Shared.Tournaments.Messages.Tournament+LadderRankingList+Response"],
        [("ConstructedLadderRanking", "coll", ("System.Collections.Generic.List`1#System.String", 0)),
         ("LimitedLadderRanking", "coll", ("System.Collections.Generic.List`1#System.String", 0))])
    _send(handler, target, instance, reqid, comp, session_id, conh, data_type,
          resp_inner, "LadderRankingList", SERVICE_MAIL_UID)


def handle_ladder_record(handler, target, instance, reqid, comp, session_id,
                         conh, inner_obj, inner_bytes,
                         *, SERVICE_MAIL_UID, log_req, data_type, **_kw):
    """Answer LadderRecord with an empty ladder."""
    log_req(">>> Respond LadderRecord — empty ladder")
    resp_fields = [
        ("constructedIronStars", "int", 0), ("constructedStars", "int", 0),
        ("constructedMaxStars", "int", 0), ("constructedStreak", "int", 0),
        ("CosmicRank", "int", 0), ("limitedStars", "int", 0),
        ("limitedMaxStars", "int", 0), ("limitedStreak", "int", 0),
        ("limitedCosmicRank", "int", 0),
        ("limitedSleeves", "coll", ("System.Collections.Generic.List`1#Game.Shared.ResourceId", 0)),
        ("constructedSleeves", "coll", ("System.Collections.Generic.List`1#Game.Shared.ResourceId", 0)),
        ("includesDebugData", "bool", False), ("MMR", "int", 0), ("LimitedMMR", "int", 0)]
    resp_inner = encode_objfmt_response(
        ["Game.Shared.Tournaments.Messages.Tournament+LadderRecord+Response"], resp_fields)
    _send(handler, target, instance, reqid, comp, session_id, conh, data_type,
          resp_inner, "LadderRecord", SERVICE_MAIL_UID)


def handle_create_waiting_room(handler, target, instance, reqid, comp, session_id,
                               conh, inner_obj, inner_bytes,
                               *, SERVICE_MAIL_UID, log_req, data_type, **_kw):
    """Create a waiting room for the requested format and answer with an empty reply."""
    fmt = inner_obj.get("format", "constructed")
    room_id = _next_waiting_room_id()
    with _waiting_room_lock:
        _waiting_rooms[room_id] = {
            "id": room_id, "style": inner_obj.get("style", "se"),
            "format": fmt, "playerCount": int(inner_obj.get("playerCount", 2)),
            "players": {}, "status": "waiting"}
    log_req(f">>> CreateWaitingRoom: id={room_id}")
    resp_inner = encode_objfmt_response(
        ["Game.Shared.Network.Tournaments.CreateWaitingRoomResponseArgs"], [])
    _send(handler, target, instance, reqid, comp, session_id, conh, data_type,
          resp_inner, "CreateWaitingRoom", SERVICE_MAIL_UID)


def handle_test_tournament_entry(handler, target, instance, reqid, comp, session_id,
                                 conh, inner_obj, inner_bytes,
                                 *, SERVICE_MAIL_UID, log_req, data_type, **_kw):
    """Say whether the player may enter the tournament: an open one, not closed."""
    tournament_id = int(inner_obj.get("TournamentID", 0) or 0)
    is_wr = bool(inner_obj.get("IsWaitingRoom", False))
    log_req(f">>> TestTournamentEntry: tid={tournament_id} wr={is_wr}")
    room = db_tournament_by_id(tournament_id)
    # Corinth is a persistent async catalog entry.  It remains in
    # ``started`` status while individual runs are matched, but its
    # registration is still open and has no entry fee.
    room_status = str(room.get("status", "")).lower() if room else ""
    room_style = str(room.get("style", "")).lower() if room else ""
    success = bool(
        room and room_status != "closed" and
        (room_status == "waiting" or
         room_style in {"async", "asynchronous"}))
    resp_inner = encode_objfmt_response(
        ["Game.Shared.Network.Tournaments.TestTournamentEntryResponseArgs"],
        [("success", "bool", success),
         ("qualifyingEntryGroups", "intlist", ("System.Collections.Generic.List`1#System.Int32", 0, [])),
         ("Error", "enum1", ("Game.Shared.Network.Tournaments.ETestTournamentEntryError", 0)),
         ("ErrorMessage", "string", "")])
    _send(handler, target, instance, reqid, comp, session_id, conh, data_type,
          resp_inner, "TestTournamentEntry", SERVICE_MAIL_UID)


def handle_leave_tournament(handler, target, instance, reqid, comp, session_id,
                            conh, inner_obj, inner_bytes,
                            *, SERVICE_MAIL_UID, log_req, data_type, **_kw):
    """Leave a tournament: record a forfeit or retire the run, then leave the waiting rooms."""
    log_req(">>> LeaveTournament")
    player_uid = int(handler.client_reck_id) if handler.client_reck_id else 0
    tournament_id = int(inner_obj.get("TournamentID", 0) or 0)
    active_tournament_session = db_tournament_match_session(
        tournament_id, player_uid, conn=db._db) if tournament_id else None
    forfeit_recorded = bool(
        tournament_id and record_tournament_forfeit(
            tournament_id, player_uid, handler))
    if forfeit_recorded:
        log_req(f"    Recorded tournament forfeit: tid={tournament_id} "
                f"loser={player_uid}")
        status = db_tournament_status(tournament_id, conn=db._db)
        if (status and str(status).lower() == "complete"
                and active_tournament_session):
            db_delete_game_session(active_tournament_session[0])
            log_req(f"    Tournament complete: cleaned PvP session "
                    f"{active_tournament_session[0]}")
    # record_tournament_forfeit already applies the async run-limit
    # retirement and deckbuilder transition.  Applying a generic
    # withdrawal afterwards would turn the returned player back into
    # a withdrawn signup and leave the client in the lobby.
    if tournament_id and not forfeit_recorded:
        db_tournament_retire_player_run(tournament_id, player_uid, conn=db._db)
    tournament_server.leave_all(player_uid)
    resp_inner = encode_objfmt_response(
        ["Game.Shared.Network.Tournaments.LeaveTournamentResponseArgs",
         "System.UInt64"],
        [("success", "bool", True),
         ("TournamentID", "ulong", tournament_id)])
    _send(handler, target, instance, reqid, comp, session_id, conh, data_type,
          resp_inner, "LeaveTournament", SERVICE_MAIL_UID)
