"""ServiceLoadBalancer session requests, served through the services registry."""

from application.commands import StartSessionCommand
from domain.uids import UID_TYPE, make_uid
from encoder import compress_gzip, encode_datawrapper, encode_objfmt_response


def handle_start_session(handler, target, instance, reqid, comp, session_id,
                         conh, inner_obj, inner_bytes, *, SERVICE_MAIL_UID,
                         log_req, data_type):
    """Create a session for the player and answer with its StartSession packet."""
    log_req(f">>> StartSession (dt={data_type}) -> creating session")
    player_uid = make_uid(UID_TYPE["ServicePlayer"], int(handler.client_reck_id))
    session_name = inner_obj.get("SessionName", "session")
    session = handler._application.execute(StartSessionCommand(
        session_name=session_name,
        player_uid=player_uid,
    )).value
    sess_id = session.session_id

    resp_inner = encode_objfmt_response(
        ["Game.Shared.Network.LoadBalancer.StartSessionResponseArgs",
         "Game.Shared.UID", "System.Boolean", "Game.Shared.SessionState",
         "Game.Shared.UID", "System.String",
         "System.Int32", "System.Int32",
         "Game.Shared.SessionStateEncounterData", "System.Boolean"],
        [("RoutingPlayerId", "uid", player_uid),
         ("Success", "bool", True),
         ("SessionState", "struct", ("Game.Shared.SessionState", [
             ("SessionId", "uid", sess_id),
             ("SessionName", "string", session_name),
             ("MinimumPlayerCount", "int", 2),
             ("MaximumPlayerCount", "int", 2),
             ("EncounterData", "class", "Game.Shared.SessionStateEncounterData"),
             ("JoinInsteadOfReconnect", "bool", False),
         ]))]
    )
    resp_body = compress_gzip(resp_inner) if comp else resp_inner
    resp_reqid = reqid | 1
    dw_bytes = encode_datawrapper(resp_reqid, data_type, resp_body, comp, session_id)
    issuer_str = (f"0.0.0.0.ServiceLoadBalancer.{SERVICE_MAIL_UID}"
                  f".ServicePlayer.{handler.client_uid}.{resp_reqid}")
    handler.scnt += 1
    handler.send({
        "issuer": issuer_str, "target": target, "instance": instance,
        "reqid": resp_reqid, "c": comp, "conh": conh, "sid": handler.sid,
    }, dw_bytes)
    log_req(f"    Sent StartSession sid={sess_id} ({len(dw_bytes)}b)")
