"""Tournament request handlers that answer with fixed response shapes.

Served through the services registry. Each handler builds its response from
the request's reqid and sends it on the connection.
"""

import gamemodes.tournament_server as tournament_server
from encoder import compress_gzip, encode_datawrapper, encode_objfmt_response
from db import log_req


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
