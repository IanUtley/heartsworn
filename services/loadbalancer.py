"""ServiceLoadBalancer session requests, served through the services registry."""

import campaign
import game_engine
from application.commands import JoinSessionCommand, StartEncounterCommand, StartSessionCommand
from domain.uids import UID_TYPE, make_uid
from binascii import unhexlify
import struct
from encoder import compress_gzip, encode_datawrapper, encode_objfmt_response
from gamemodes.fra_arena import _fra_arena_session_state_data, _fra_arena_setup_values


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


def handle_start_encounter(handler, target, instance, reqid, comp, session_id,
                         conh, inner_obj, inner_bytes, *, SERVICE_MAIL_UID,
                         log_req, data_type):
    """Create an encounter session and answer with its StartEncounter packet."""
    log_req(">>> Respond StartEncounter -> creating session")
    player_uid = make_uid(UID_TYPE["ServicePlayer"], int(handler.client_reck_id))
    session_name = inner_obj.get("SessionName", "tutorial")
    encounter_data_raw = inner_obj.get("EncounterData", {})
    session = handler._application.execute(StartEncounterCommand(
    session_name=session_name,
    encounter_data=encounter_data_raw,
    player_uid=player_uid,
)).value
    sess_id = session.session_id
    server_id = session.server_id

    # Preserve the encounter scene in the SessionState returned by
    # StartEncounter.  The campaign notification already carries it,
    # but the fixed client replaces its battle context with this
    # response and otherwise receives a null EncounterData object;
    # that makes UIBattle fall back to an invalid (zero) EncounterDeck
    # and can leave the scene on the starfield.
    response_encounter_data = ("class", "Game.Shared.SessionStateEncounterData")
    fra_setup = _fra_arena_setup_values(encounter_data_raw)
    if str(session_name).startswith("camp_"):
        scene_guid = campaign._last_encounter_scene.get(str(session_name))
        if scene_guid:
            response_encounter_data = ("struct", (
                "Game.Shared.SessionStateEncounterData", [
                    ("SceneTemplateId", "struct", (
                        "Game.Shared.ResourceId", [
                            ("m_Guid", "guid", str(scene_guid))])),
                    ("SessionFlags", "enum1", (
                        "Game.Shared.ESessionFlags",
                        game_engine.ESessionFlags.IsEncounter |
                        game_engine.ESessionFlags.IsPvE)),
                ]))
    elif fra_setup is not None:
        # The client replaces its arena battle context with this
        # StartEncounter response. Preserve the arena IDs for the
        # client-side challenge UI and reconnect path; live battle
        # mods are loaded internally by the authoritative setup below.
        # Do NOT advertise IsPvE/IsPvEArena on this transient session:
        # GameClient.OnStartEncounter and GameClient.OnReadyForGameSetup
        # each call UIBattle.OnSessionCreated, which appends its
        # challenge-event handler on every PvE session. The
        # ReadyForGameSetup session below carries the live flags and
        # registers that handler once; adding them here registered it
        # twice, so one conversation objective event produced two
        # identical rows in the battle challenge panel.
        response_encounter_data = _fra_arena_session_state_data(
        fra_setup, flags=0)

    resp_inner = encode_objfmt_response(
    ["Game.Shared.Network.LoadBalancer.StartEncounterResponseArgs",
     "Game.Shared.UID", "System.Boolean", "Game.Shared.SessionState",
     "Game.Shared.UID", "System.String",
     "System.Int32", "System.Int32",
         "Game.Shared.SessionStateEncounterData", "System.Boolean",
         "Game.Shared.ResourceId", "System.Guid", "Game.Shared.ESessionFlags", "System.Int32",
     "Game.Shared.UID", "System.UInt64"],
    [("RoutingPlayerId", "uid", player_uid),
     ("Success", "bool", True),
     ("SessionState", "struct", ("Game.Shared.SessionState", [
         ("SessionId", "uid", sess_id),
         ("SessionName", "string", session_name),
         ("MinimumPlayerCount", "int", 2),
         ("MaximumPlayerCount", "int", 2),
         ("EncounterData", response_encounter_data[0], response_encounter_data[1]),
         ("JoinInsteadOfReconnect", "bool", False),
     ])),
     ("SessionID", "uid", sess_id),
     ("ServerID", "uid", server_id)]
)
    resp_body = compress_gzip(resp_inner) if comp else resp_inner
    resp_reqid = reqid | 1
    dw_bytes = encode_datawrapper(resp_reqid, data_type, resp_body, comp, session_id)
    issuer_str = f"0.0.0.0.ServiceLoadBalancer.{SERVICE_MAIL_UID}.ServicePlayer.{handler.client_uid}.{resp_reqid}"
    handler.scnt += 1
    handler.send({
        "issuer": issuer_str,
        "target": target,
        "instance": instance,
        "reqid": resp_reqid,
        "c": comp,
        "conh": conh,
        "sid": handler.sid,
    }, dw_bytes)
    log_req(f"    Sent StartEncounter session={session_name} sid={session_id} ({len(dw_bytes)}b)")


def _client_session_name(session):
    """The session name the client reads. Its reconnect dialog only recognises a campaign
    session as ``C:<campaign id>`` (``Utils.ParseCampaignSessionInfo``); any other name leaves
    the dialog with no effect. The server keeps its own ``camp_<id>`` name for lookups."""
    name = str(session.session_name or "")
    if name.startswith("camp_"):
        return f"C:{name.split('_', 1)[1]}"
    return name



def _campaign_encounter_struct(session):
    """The campaign battle's encounter identity, as the client reads it: the scene, the flags and the
    tournament id, from the encounter data stored when the battle began."""
    stored = session.encounter_data or {}
    scene = str((stored.get("SceneTemplateId") or {}).get("m_Guid")
                or "00000000-0000-0000-0000-000000000000")
    flags = stored.get("SessionFlags") or 0
    flags = int(flags.get("value__", 0) if isinstance(flags, dict) else flags)
    return ("Game.Shared.SessionStateEncounterData", [
        ("SceneTemplateId", "struct", (
            "Game.Shared.ResourceId", [("m_Guid", "guid", scene)])),
        ("SessionFlags", "enum1", ("Game.Shared.ESessionFlags", flags)),
        ("TournamentID", "ulong", 0),
    ])



def _campaign_resume_session(player_pid):
    """The player's live campaign battle, if one can be rejoined and they have not declined it."""
    import game_session as gs
    from services.tournament_game import campaign_session_reconnectable
    from rules_port.persistence import load_state as load_rules_state
    from services.reconnect import _reconnect_declined_for_pid
    session = gs.find_session_by_player(player_pid)
    if not campaign_session_reconnectable(session, player_pid):
        return None
    if _reconnect_declined_for_pid(load_rules_state(session) or {}, player_pid):
        return None
    return session



def handle_find_session(handler, target, instance, reqid, comp, session_id,
                      conh, inner_obj, inner_bytes, *, SERVICE_MAIL_UID,
                      log_req, data_type):
    """Answer FindSession with the player's resumable campaign battle, or none."""
    player_uid = make_uid(UID_TYPE["ServicePlayer"], int(handler.client_reck_id))
    resume = _campaign_resume_session(int(handler.client_reck_id))
    if resume is not None:
        # A live campaign battle: the client joins it, rather than starting a new encounter.
        log_req(f">>> Respond FindSession -> campaign {resume.session_name} (resume)")
        resp_inner = encode_objfmt_response(
        ["Game.Shared.Network.LoadBalancer.FindSessionResponseArgs",
         "Game.Shared.UID", "System.Boolean", "Game.Shared.SessionState",
         "Game.Shared.SessionStateEncounterData", "Game.Shared.ResourceId",
         "System.Guid", "Game.Shared.ESessionFlags", "System.UInt64",
         "System.Int32", "System.String", "System.Boolean"],
        [("RoutingPlayerId", "uid", player_uid),
         ("Success", "bool", True),
         ("SessionState", "struct", ("Game.Shared.SessionState", [
             ("SessionId", "uid", resume.session_id),
             ("SessionName", "string", _client_session_name(resume)),
             ("MinimumPlayerCount", "int", 2),
             ("MaximumPlayerCount", "int", 2),
             ("EncounterData", "struct", _campaign_encounter_struct(resume)),
             ("JoinInsteadOfReconnect", "bool", False),
         ]))]
    )
    else:
        log_req(">>> Respond FindSession -> no session")
        resp_inner = encode_objfmt_response(
        ["Game.Shared.Network.LoadBalancer.FindSessionResponseArgs",
         "Game.Shared.UID", "System.Boolean", "Game.Shared.SessionState"],
        [("RoutingPlayerId", "uid", player_uid),
         ("Success", "bool", False),
         ("SessionState", "class", "Game.Shared.SessionState")]
    )
    resp_body = compress_gzip(resp_inner) if comp else resp_inner
    resp_reqid = reqid | 1
    dw_bytes = encode_datawrapper(resp_reqid, data_type, resp_body, comp, session_id)
    issuer_str = f"0.0.0.0.ServiceLoadBalancer.{SERVICE_MAIL_UID}.ServicePlayer.{handler.client_uid}.{resp_reqid}"
    handler.scnt += 1
    handler.send({
        "issuer": issuer_str,
        "target": target,
        "instance": instance,
        "reqid": resp_reqid,
        "c": comp,
        "conh": conh,
        "sid": handler.sid,
    }, dw_bytes)
    log_req(f"    Sent FindSession response ({len(dw_bytes)}b)")


def handle_join_session(handler, target, instance, reqid, comp, session_id,
                        conh, inner_obj, inner_bytes, *, SERVICE_MAIL_UID,
                        log_req, data_type):
    """Join the named session and answer with its JoinSession packet."""
    log_req(">>> JoinSession (dt=22021)")
    player_uid = make_uid(UID_TYPE["ServicePlayer"], int(handler.client_reck_id))
    # JoinSession carries the client's phase-stop preferences.  Keep
    # them for the first battle checkpoint; previously only a later
    # SetTurnPhasesTransaction populated the RulesPort state, so the
    # initial practice turn silently fell back to server defaults
    # (including an opponent SecondMain stop).
    join_self_stops = handler._extract_enum_list(
        inner_bytes, "m_SelfTurnPhases", "m_OpponentTurnPhases")
    join_opp_stops = handler._extract_enum_list(
        inner_bytes, "m_OpponentTurnPhases", None)
    # A few older client serializers omit the member-name prefix;
    # retain compatibility without weakening the normal wire parse.
    if join_self_stops is None:
        join_self_stops = handler._extract_enum_list(
            inner_bytes, "SelfTurnPhases", "OpponentTurnPhases")
    if join_opp_stops is None:
        join_opp_stops = handler._extract_enum_list(
            inner_bytes, "OpponentTurnPhases", None)
    if join_self_stops is not None or join_opp_stops is not None:
        handler._pending_player_stops = (
            join_self_stops, join_opp_stops)
        if handler.user_profile and join_self_stops is not None:
            handler._save_player_stops(
                handler.user_profile["id"], join_self_stops,
                join_opp_stops if join_opp_stops is not None else [])
    # Extract SessionId UID from raw inner bytes
    session_id_val = 0
    if isinstance(inner_bytes, bytes) and b"SessionId" in inner_bytes:
        pos = inner_bytes.find(b"SessionId")
        if pos >= 0:
            rest = inner_bytes[pos:]
            try:
                # Skip to m_UID64, then 4 fields to hex value
                idx = rest.find(b"m_UID64")
                if idx >= 0:
                    rest2 = rest[idx + 7:]  # skip "m_UID64"
                    parts = rest2.split(b";", 5)
                    # parts: [sub_idx, sub_type, nprops, hex_value, ...]
                    if len(parts) >= 5:
                        hex_val = parts[4].decode("ascii")
                        session_id_val = struct.unpack("<Q", unhexlify(hex_val))[0]
            except:
                pass
    session = (handler._application.execute(JoinSessionCommand(
    session_id=session_id_val,
    player_uid=player_uid,
)).value if session_id_val else None)
    if session:
        resp_inner = encode_objfmt_response(
        ["Game.Shared.Network.LoadBalancer.JoinSessionResponseArgs",
         "Game.Shared.UID", "System.Boolean", "Game.Shared.SessionState",
         "System.Collections.Generic.List`1#Game.Shared.PlayerState",
         "Game.Shared.UID", "System.String",
         "System.Int32", "System.Int32",
         "Game.Shared.SessionStateEncounterData", "System.Boolean"],
        [("RoutingPlayerId", "uid", player_uid),
         ("Success", "bool", True),
         ("SessionState", "struct", ("Game.Shared.SessionState", [
             ("SessionId", "uid", session.session_id),
             ("SessionName", "string", session.session_name),
             ("MinimumPlayerCount", "int", 2),
             ("MaximumPlayerCount", "int", 2),
             ("EncounterData", "class", "Game.Shared.SessionStateEncounterData"),
             ("JoinInsteadOfReconnect", "bool", False),
         ])),
         ("SessionPlayers", "coll", ("System.Collections.Generic.List`1#Game.Shared.PlayerState",
             0, []))]
    )
        log_req(f"    JoinSession success, {len(session.players)} players")
    else:
        resp_inner = encode_objfmt_response(
        ["Game.Shared.Network.LoadBalancer.JoinSessionResponseArgs",
         "Game.Shared.UID", "System.Boolean"],
        [("RoutingPlayerId", "uid", player_uid),
         ("Success", "bool", False)]
    )
        log_req(f"    JoinSession failed: session not found")
    resp_body = compress_gzip(resp_inner) if comp else resp_inner
    resp_reqid = reqid | 1
    dw_bytes = encode_datawrapper(resp_reqid, data_type, resp_body, comp, session_id)
    issuer_str = f"0.0.0.0.ServiceLoadBalancer.{SERVICE_MAIL_UID}.ServicePlayer.{handler.client_uid}.{resp_reqid}"
    handler.scnt += 1
    handler.send({
        "issuer": issuer_str, "target": target, "instance": instance,
        "reqid": resp_reqid, "c": comp, "conh": conh, "sid": handler.sid,
    }, dw_bytes)
    log_req(f"    Sent JoinSession response ({len(dw_bytes)}b)")
