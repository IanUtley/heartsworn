"""ServiceLoadBalancer session requests (find, start, join, ready, reconnect).

Moved out of hconnect_server.HCPHandler._handle_service_request_legacy.
"""

from binascii import hexlify
import io
import json
import random
import struct
from db import log_req
import game_engine
import db
from encoder import encode_datawrapper
from encoder import compress_gzip
from encoder import encode_objfmt_response
import campaign
from domain.uids import SERVICE_MAIL_UID
from encoder import encode_sync_event
from domain.uids import UID_TYPE
from domain.uids import SERVICE_GAME_SESSION_UID
from domain.uids import make_uid
from pvp_db import db_ability_effect_rows
from tournament_db import db_tournament_room_for_game
from gamemodes.fra_arena import _fra_arena_setup_values
from services.loadbalancer import _campaign_encounter_struct, _client_session_name
from pvp_db import db_deck_active_gems
from profile_db import db_insert_card_instance
from gamemodes.fra_arena import _fra_arena_session_state_data
from domain.constants import SERVICE_AI_UID_TYPE
from pvp_db import db_template_ability_guids
from pve_db import db_get_arena_state
from pvp_db import db_target_template_filter
from pvp_db import db_template_exists
from pvp_db import db_template_card_info
from gamemodes.tournament_engine import player_decks
from pvp_db import db_insert_game_cards
from pvp_db import db_ability_metadata
from services.reconnect import _reconnect_declined_for_pid
from gamemodes.tournament_engine import tournament_id_from_session_name
from gamemodes.tournament_engine import _tournament_session_flags
from profile_db import db_get_user
from pve_db import db_get_player_champion_guid
from pve_db import db_champion_template_data_exists
from pve_db import db_extended_champion_health
from pve_db import db_champion_talents_for_deck
from pve_db import db_champion_template_name
from pve_db import db_get_fra_challengers
from pvp_db import db_get_charge_power
from pve_db import db_encounter_deck_sleeve
from profile_db import db_deck_sleeve
from pve_db import db_talent_ability_rows
from application.practice_flow import _push_pve_game_started
from profile_db import db_deck_champion_name
from pvp_db import db_clear_session_cards
from pvp_db import db_deck_cards_json
from pvp_db import db_first_template_guid
from pvp_db import db_update_game_card_abilities
from pvp_db import db_gem_templates
from pvp_db import db_encounter_deck_cards
from pvp_db import db_scene_mods
from pvp_db import db_ability_has_effect


# ServiceLoadBalancer — TryReconnectionToDisconnectedGame (22013)
def handle_tryreconnectiontodisconnectedgame_22013(handler, target, instance, data_type, reqid, comp, session_id,
                                                   conh, inner_obj, inner_bytes, raw_data_wrapper_bytes):
    import hconnect_server as _hc
    reconnect_pid = int(handler.client_reck_id or 0)
    reconnect_session = _hc.game_session.find_session_by_player(reconnect_pid)
    reconnect_state = {}
    # The fixed client sends this same request once from the landing
    # page and again when UIArenaCampaignViewModel opens. Keep FRA
    # offers out of the landing-page prompt; the arena-screen probe is
    # the second distinct request on this authenticated connection.
    probe_ids = getattr(handler, "_try_reconnection_probe_ids", None)
    if probe_ids is None:
        probe_ids = handler._try_reconnection_probe_ids = set()
    try:
        probe_id = int(reqid)
    except (TypeError, ValueError):
        probe_id = len(probe_ids)
    if probe_id not in probe_ids:
        probe_ids.add(probe_id)
    fra_arena_screen_probe = len(probe_ids) > 1
    fra_setup = (_fra_arena_setup_values(
    getattr(reconnect_session, "encounter_data", None))
        if reconnect_session else None)
    saved_battle_state = (getattr(reconnect_session, "turn_order", None)
                          if reconnect_session else None)
    is_fra_checkpoint = False
    is_tournament_reconnect = bool(
        reconnect_session and
        str(getattr(reconnect_session, "session_name", "") or "")
        .startswith("tourney-"))
    reconnect_declined = False
    from services.tournament_game import campaign_session_reconnectable
    is_campaign_reconnect = bool(
        reconnect_session and
        campaign_session_reconnectable(reconnect_session, reconnect_pid))
    if (reconnect_session and
            getattr(reconnect_session, "state", "") != "ended" and
            (fra_setup or is_tournament_reconnect or is_campaign_reconnect)):
        try:
            from services.tournament_game import (
                fra_session_reconnectable, pvp_load_state,
                pvp_session_lock,
            )
            with pvp_session_lock(reconnect_session):
                if is_campaign_reconnect:
                    from rules_port.persistence import load_state as load_rules_state
                    reconnect_state = load_rules_state(reconnect_session) or {}
                elif fra_setup:
                    is_fra_checkpoint = fra_session_reconnectable(
                        reconnect_session, reconnect_pid)
                    saved_battle_state = getattr(
                        reconnect_session, "turn_order", None)
                    if is_fra_checkpoint:
                        from rules_port.persistence import load_state
                        reconnect_state = load_state(reconnect_session) or {}
                elif is_tournament_reconnect:
                    reconnect_state = pvp_load_state(reconnect_session) or {}
                reconnect_declined = _reconnect_declined_for_pid(
                reconnect_state, reconnect_pid)
        except Exception as exc:
            log_req(f"    Reconnect checkpoint lookup failed: {exc}")
            reconnect_state = {}
    is_fra_reconnect = bool(
        is_fra_checkpoint and fra_arena_screen_probe and
        isinstance(saved_battle_state, dict))
    if is_fra_checkpoint and not fra_arena_screen_probe:
        log_req("    FRA reconnect offer deferred until the arena screen")
    can_reconnect = bool(
        reconnect_session
        and getattr(reconnect_session, "state", "") != "ended"
        and (is_fra_reconnect or is_campaign_reconnect or
             (is_tournament_reconnect and reconnect_state.get("pvp"))))
    if can_reconnect and not reconnect_declined:
        session_uid = int(reconnect_session.session_id)
        tournament_id = 0
        if is_fra_reconnect:
            reconnect_flags = int(fra_setup["flags"])
            reconnect_encounter_data = \
                _fra_arena_session_state_data(fra_setup)[1]
        elif is_campaign_reconnect:
            reconnect_encounter_data = _campaign_encounter_struct(reconnect_session)
        else:
            try:
                tournament_id = tournament_id_from_session_name(
                reconnect_session.session_name)
            except (IndexError, ValueError):
                tournament_id = 0
            tournament_room = db_tournament_room_for_game(
            tournament_id, conn=db._db)
            reconnect_flags = _tournament_session_flags({
            "format": tournament_room.get("format", 0)
            if tournament_room else 0
        })
            reconnect_encounter_data = (
                "Game.Shared.SessionStateEncounterData", [
                    ("SceneTemplateId", "struct", (
                        "Game.Shared.ResourceId", [
                            ("m_Guid", "guid",
                             "00000000-0000-0000-0000-000000000000")])),
                    ("SessionFlags", "enum1", (
                        "Game.Shared.ESessionFlags", reconnect_flags)),
                    ("TournamentID", "ulong", tournament_id),
                ])
        deck_db_id = int(player_decks.get(reconnect_pid, 0) or 0)
        if is_fra_reconnect:
            try:
                arena_state = db_get_arena_state(
                int((handler.user_profile or {}).get("id") or 0),
                initialize=False, conn=db._db)
                deck_db_id = handler._resolve_fra_deck_id(
                    arena_state.get("deck_id", 0))
            except Exception as exc:
                log_req(f"    FRA reconnect deck lookup failed: {exc}")
        deck_uid = _hc.encoder.make_uid(244, deck_db_id) if deck_db_id else 0
        resp_inner = encode_objfmt_response(
        ["Game.Client.Network.LoadBalancer.TryReconnectionToDisconnectedGameResponse",
         "Game.Shared.SessionState", "Game.Shared.UID",
         "Game.Shared.SessionStateEncounterData",
         "Game.Shared.ResourceId", "System.Guid",
         "Game.Shared.ESessionFlags", "System.UInt64",
         "System.Int32", "System.String", "System.Boolean",
         "Game.Shared.Network.LoadBalancer.ETryReconnectionToDisconnectedGameError",
         "System.String"],
        [("SessionState", "struct", ("Game.Shared.SessionState", [
            ("SessionId", "uid", session_uid),
            ("SessionName", "string", _client_session_name(reconnect_session)),
            ("MinimumPlayerCount", "int", 2),
            ("MaximumPlayerCount", "int", 2),
            # The client dereferences EncounterData before it
            # enters Battle and UIBattle later reads the flags.
            # An empty `class` field decodes as null on the fixed
            # client, so encode the saved tournament/FRA identity;
            # the reconnect snapshot restores the actual board.
            ("EncounterData", "struct", reconnect_encounter_data),
            ("JoinInsteadOfReconnect", "bool", False),
        ])),
         ("DeckID", "uid", deck_uid),
         ("Error", "enum1", (
             "Game.Shared.Network.LoadBalancer.ETryReconnectionToDisconnectedGameError", 0)),
         ("ErrorMessage", "string", "")])
        log_req(f">>> Respond TryReconnectionToDisconnectedGame -> "
            f"{reconnect_session.session_name} "
            f"({'FRA' if is_fra_reconnect else 'campaign' if is_campaign_reconnect else 'tournament'})")
    else:
        if can_reconnect and reconnect_declined:
            log_req(
            f">>> Respond TryReconnectionToDisconnectedGame -> "
            f"pid {reconnect_pid} previously declined this session")
        else:
            log_req(">>> Respond TryReconnectionToDisconnectedGame -> no game")
        resp_inner = encode_objfmt_response(
        ["Game.Client.Network.LoadBalancer.TryReconnectionToDisconnectedGameResponse",
         "Game.Shared.UID",
         "Game.Shared.Network.LoadBalancer.ETryReconnectionToDisconnectedGameError",
         "System.String"],
        [("DeckID", "uid", 0),
         ("Error", "enum1", (
             "Game.Shared.Network.LoadBalancer.ETryReconnectionToDisconnectedGameError", 0)),
         ("ErrorMessage", "string", "")])
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
    log_req(f"    Sent TryReconnection response ({len(dw_bytes)}b)")


# Armies of Myth (AZ1 Node 66): the AI starts with the campaign's army level,
# the counter the conversation sets from the difficulty picked (0-6).
ARMY_OF_MYTH_SCENE = "0b1d5244-e9f6-46ec-bb06-73c36801f5ef"
ARMY_LEVEL_COUNTER = "fight_army_at_level"


def _army_of_myth_ai_resources(session):
    """The AI's starting resources for the Army of Myth battle, else None."""
    import hconnect_server as _hc
    from campaign_runtime import progress
    session_name = str(session.session_name or "")
    scene = campaign._last_encounter_scene.get(session_name)
    if str(scene or "").lower() != ARMY_OF_MYTH_SCENE:
        return None
    identity = _hc.db_campaign_identity_state(
        int(session_name.split("_")[-1] or 0), conn=db._db)
    if not identity:
        return 0
    loaded = progress.load(db._db, int(identity[0]))
    counters = (loaded[0] if loaded else {}).get("counters") or {}
    return int(counters.get(ARMY_LEVEL_COUNTER, 0) or 0)


# ServiceLoadBalancer — ReadyToStartGame (22031)
def handle_readytostartgame_22031(handler, target, instance, data_type, reqid, comp, session_id,
                                  conh, inner_obj, inner_bytes, raw_data_wrapper_bytes):
    import hconnect_server as _hc
    player_uid = make_uid(UID_TYPE["ServicePlayer"], int(handler.client_reck_id))
    log_req(f"    Player UID: reck={handler.client_reck_id}, uid={player_uid:#x}")
    session = _hc.game_session.find_session_by_player(player_uid)
    if not session:
        log_req("    No session found for tutorial game!")
        return

    # A client reconnect can reach the game setup before re-sending
    # auth. Campaign ownership is persisted, so restore the profile
    # instead of allowing setup to crash on self.user_profile["id"].
    if not handler.user_profile and (session.session_name or "").startswith("camp_"):
        try:
            camp_id = int(session.session_name.split("_", 1)[1])
        except (TypeError, ValueError, IndexError):
            camp_id = 0
        if camp_id:
            profile_user_id = _hc.db_campaign_owner_user_id(camp_id, conn=db._db)
            if profile_user_id:
                restored_profile = db_get_user(profile_user_id)
                if restored_profile is not None:
                    handler.user_profile = restored_profile
                if handler.user_profile:
                    handler._set_client_identity_from_profile()
                    log_req(
                    f"    Restored campaign profile: "
                    f"user={handler.user_profile['id']}")

    # A live campaign battle being rejoined: restore its saved board instead of the new-game setup.
    from services.tournament_game import campaign_session_reconnectable, pvp_session_lock
    campaign_reconnect = campaign_session_reconnectable(session, int(handler.client_reck_id))
    if campaign_reconnect:
        reconnect_pid = int(handler.client_reck_id)
        try:
            with pvp_session_lock(session):
                restored = handler._push_fra_reconnect_snapshot(session, reconnect_pid)
            if not restored:
                log_req(f"    Campaign reconnect snapshot could not restore pid {reconnect_pid}")
        except Exception as exc:
            import traceback
            log_req(f"    Campaign reconnect snapshot failed for {reconnect_pid}: "
                    f"{exc}\n{traceback.format_exc()}")

    # PvP/tournament sessions and restored campaign battles: skip PvE encounter setup
    if campaign_reconnect or (session.session_name and (session.session_name.startswith("pvp-") or session.session_name.startswith("tourney-"))):
        log_req("    PvP/tourney/campaign reconnect — ReadyToStartGame ack only")
        rts_resp = encode_objfmt_response(
        ["Game.Client.Network.LoadBalancer.ReadyToStartGameResponse",
         "System.Boolean"],
        [("IsReady", "bool", True)]
    )
        rts_body = compress_gzip(rts_resp) if comp else rts_resp
        rts_dw = encode_datawrapper(reqid | 1, 22031, rts_body, comp and 1 or 0, "00000000-0000-0000-0000-000000000000")
        handler.send({
            "issuer": f"0.0.0.0.ServiceLoadBalancer.{SERVICE_MAIL_UID}.ServicePlayer.{handler.client_uid}.{reqid | 1}",
            "target": target, "instance": instance,
            "reqid": reqid | 1, "c": comp, "conh": conh, "sid": handler.sid,
        }, rts_dw)
        return
    gs_instance = str(session.server_id)

    # Establish the coin-toss winner before any setup response exposes
    # the turn order. The tutorial keeps its historical deterministic
    # player-first behavior; FRA and completed tutorial battles toss
    # normally. If the AI wins, it makes the subsequent Play/Draw
    # choice automatically.
    is_campaign_session = (session.session_name or "").startswith("camp_")
    is_tutorial_session = False
    handler._pve_player_cannot_choose_play_first = False
    if is_campaign_session:
        try:
            camp_id = int((session.session_name or "camp_0").split("_")[-1] or 0)
        except (TypeError, ValueError):
            camp_id = 0
        camp_identity = (_hc.db_campaign_identity_state(camp_id, conn=db._db)
                         if camp_id else None)
        if camp_identity:
            try:
                is_tutorial_session = not bool(
                    json.loads(camp_identity[1] or "{}").get("TutorialDone", False))
            except (TypeError, ValueError):
                is_tutorial_session = True
        if camp_id:
            handler._pve_player_cannot_choose_play_first = \
                campaign.player_cannot_choose_play_first(db._db, camp_id)
            if handler._pve_player_cannot_choose_play_first:
                log_req("    Campaign talent restriction: player cannot choose Play first")
    coin_winner_is_player = (
        True if is_tutorial_session else bool(random.getrandbits(1)))
    handler._pve_coin_winner_is_player = coin_winner_is_player

    # Respond to 22031
    rts_resp = encode_objfmt_response(
    ["Game.Client.Network.LoadBalancer.ReadyToStartGameResponse",
     "System.Boolean"],
    [("IsReady", "bool", True)]
)
    rts_body = compress_gzip(rts_resp) if comp else rts_resp
    rts_dw = encode_datawrapper(reqid | 1, 22031, rts_body, comp and 1 or 0, "00000000-0000-0000-0000-000000000000")
    handler.send({
        "issuer": f"0.0.0.0.ServiceLoadBalancer.{SERVICE_MAIL_UID}.ServicePlayer.{handler.client_uid}.{reqid | 1}",
        "target": target, "instance": instance,
        "reqid": reqid | 1, "c": comp, "conh": conh, "sid": handler.sid,
    }, rts_dw)
    log_req(f"    Sent ReadyToStartGame response ({len(rts_dw)}b)")

    # Push ReadyForGameSetup (22027) with Opponent + TurnOrder - manual encode
    rfg_types = ["Game.Client.Network.LoadBalancer.ReadyForGameSetupResponse",
                 "Game.Shared.UID", "Game.Shared.ResourceId", "System.Guid",
                 "Game.Shared.SessionState", "System.Int32", "System.String",
                 "System.Collections.Generic.List`1#Game.Shared.PlayerState",
                 "Game.Shared.PlayerState", "System.UInt64",
                 "System.Collections.Generic.List`1#Game.Shared.UID",
                 "Game.Shared.Network.LoadBalancer.EReadyForGameSetupError"]
    def rfg_ft(t):
        if t not in rfg_types: rfg_types.append(t)
        return rfg_types.index(t)
    rfg_buf = io.BytesIO(); rfg_sz = []; rfg_w = lambda s: rfg_buf.write(s.encode("utf-8"))
    rfg_sp = lambda: rfg_buf.write(b";")
    rfg_sz.append(0)
    rfg_w(""); rfg_sp(); rfg_w("0"); rfg_sp(); rfg_w(str(rfg_ft(rfg_types[0]))); rfg_sp(); rfg_w("9"); rfg_sp()
    # Fields in alphabetical order: DeckId, DeckTemplateId, OpponentsInfo, SessionState, TurnOrder, seedW, seedZ, Error, ErrorMessage
    pl_uid_val = (int(handler.client_reck_id) << 8) | 244  # ServicePlayer type
    ai_uid_val = (1000 << 8) | SERVICE_AI_UID_TYPE  # ServiceAI
    
    # DeckId (UID.Invalid — client keeps its selected deck)
    fd = rfg_buf.tell(); rfg_sz.append(0)
    rfg_w("DeckId"); rfg_sp(); rfg_w(str(len(rfg_sz)-1)); rfg_sp()
    rfg_w(str(rfg_ft("Game.Shared.UID"))); rfg_sp(); rfg_w("1"); rfg_sp()
    fd2 = rfg_buf.tell(); rfg_sz.append(0)
    rfg_w("m_UID64"); rfg_sp(); rfg_w(str(len(rfg_sz)-1)); rfg_sp(); rfg_w(str(rfg_ft("System.UInt64"))); rfg_sp(); rfg_w("0"); rfg_sp()
    rfg_w(hexlify(struct.pack("<Q", pl_uid_val)).decode("ascii")); rfg_sp()
    rfg_sz[-1] = rfg_buf.tell()-fd2; rfg_sz[-2] = rfg_buf.tell()-fd
    
    # DeckTemplateId (ResourceId)
    fdt = rfg_buf.tell(); rfg_sz.append(0)
    rfg_w("DeckTemplateId"); rfg_sp(); rfg_w(str(len(rfg_sz)-1)); rfg_sp()
    rfg_w(str(rfg_ft("Game.Shared.ResourceId"))); rfg_sp(); rfg_w("1"); rfg_sp()
    fdt2 = rfg_buf.tell(); rfg_sz.append(0)
    rfg_w("m_Guid"); rfg_sp(); rfg_w(str(len(rfg_sz)-1)); rfg_sp(); rfg_w(str(rfg_ft("System.Guid"))); rfg_sp(); rfg_w("0"); rfg_sp()
    rfg_w("36"); rfg_sp(); rfg_buf.write(b"00000000-0000-0000-0000-000000000000")
    rfg_sz[-1] = rfg_buf.tell()-fdt2; rfg_sz[-2] = rfg_buf.tell()-fdt
    
    # OpponentsInfo (List<PlayerState> with 1 AI opponent)
    foi = rfg_buf.tell(); rfg_sz.append(0); foi_idx = len(rfg_sz)-1
    rfg_w("OpponentsInfo"); rfg_sp(); rfg_w(str(foi_idx)); rfg_sp()
    rfg_w(str(rfg_ft("System.Collections.Generic.List`1#Game.Shared.PlayerState"))); rfg_sp(); rfg_w("0"); rfg_sp()
    rfg_w("1"); rfg_sp()  # count=1
    # PlayerState element: PlayerId(UID), PlayerPosition(int), PlayerName(string)
    fps = rfg_buf.tell(); rfg_sz.append(0); fps_idx = len(rfg_sz)-1
    rfg_w("0"); rfg_sp(); rfg_w(str(fps_idx)); rfg_sp(); rfg_w(str(rfg_ft("Game.Shared.PlayerState"))); rfg_sp(); rfg_w("3"); rfg_sp()
    # PlayerId as UID
    fpi = rfg_buf.tell(); rfg_sz.append(0)
    rfg_w("PlayerId"); rfg_sp(); rfg_w(str(len(rfg_sz)-1)); rfg_sp()
    rfg_w(str(rfg_ft("Game.Shared.UID"))); rfg_sp(); rfg_w("1"); rfg_sp()
    fpi2 = rfg_buf.tell(); rfg_sz.append(0)
    rfg_w("m_UID64"); rfg_sp(); rfg_w(str(len(rfg_sz)-1)); rfg_sp(); rfg_w(str(rfg_ft("System.UInt64"))); rfg_sp(); rfg_w("0"); rfg_sp()
    rfg_w(hexlify(struct.pack("<Q", ai_uid_val)).decode("ascii")); rfg_sp()
    rfg_sz[-1] = rfg_buf.tell()-fpi2; rfg_sz[-2] = rfg_buf.tell()-fpi
    # PlayerPosition (int)
    fpp = rfg_buf.tell(); rfg_sz.append(0)
    rfg_w("PlayerPosition"); rfg_sp(); rfg_w(str(len(rfg_sz)-1)); rfg_sp()
    rfg_w(str(rfg_ft("System.Int32"))); rfg_sp(); rfg_w("0"); rfg_sp()
    rfg_w(hexlify(struct.pack("<i", 1)).decode("ascii")); rfg_sp()
    rfg_sz[-1] = rfg_buf.tell()-fpp
    # PlayerName (string)
    fpn = rfg_buf.tell(); rfg_sz.append(0)
    rfg_w("PlayerName"); rfg_sp(); rfg_w(str(len(rfg_sz)-1)); rfg_sp()
    rfg_w(str(rfg_ft("System.String"))); rfg_sp(); rfg_w("0"); rfg_sp()
    enc_name = b"AI Opponent"; rfg_w(str(len(enc_name))); rfg_sp(); rfg_buf.write(enc_name)
    rfg_sz[-1] = rfg_buf.tell()-fpn
    rfg_sz[fps_idx] = rfg_buf.tell()-fps
    rfg_sz[foi_idx] = rfg_buf.tell()-foi
    
    rfg_fra_setup = _fra_arena_setup_values(session.encounter_data)
    if rfg_fra_setup is not None:
        log_req("    ReadyForGameSetup FRA data: "
            f"flags={rfg_fra_setup['flags']} "
            f"arena={rfg_fra_setup['arena_instance']} "
            f"owner={rfg_fra_setup['arena_owner']}")

    # SessionState
    fss = rfg_buf.tell(); rfg_sz.append(0); fss_idx = len(rfg_sz)-1
    rfg_w("SessionState"); rfg_sp(); rfg_w(str(fss_idx)); rfg_sp()
    rfg_w(str(rfg_ft("Game.Shared.SessionState"))); rfg_sp(); rfg_w("6"); rfg_sp()
    # The client rebuilds SessionClient from this setup response.
    # Preserve FRA identity for challenge UI and reconnect handling.
    if rfg_fra_setup is None:
        f_ed = rfg_buf.tell(); rfg_sz.append(0); ed_idx = len(rfg_sz)-1
        rfg_w("EncounterData"); rfg_sp(); rfg_w(str(ed_idx)); rfg_sp()
        rfg_w(str(rfg_ft("Game.Shared.SessionStateEncounterData"))); rfg_sp(); rfg_w("0"); rfg_sp()
        rfg_sz[ed_idx] = rfg_buf.tell() - f_ed
    else:
        f_ed = rfg_buf.tell(); rfg_sz.append(0); ed_idx = len(rfg_sz)-1
        rfg_w("EncounterData"); rfg_sp(); rfg_w(str(ed_idx)); rfg_sp()
        rfg_w(str(rfg_ft("Game.Shared.SessionStateEncounterData"))); rfg_sp(); rfg_w("4"); rfg_sp()

        f_scene = rfg_buf.tell(); rfg_sz.append(0); scene_idx = len(rfg_sz)-1
        rfg_w("SceneTemplateId"); rfg_sp(); rfg_w(str(scene_idx)); rfg_sp()
        rfg_w(str(rfg_ft("Game.Shared.ResourceId"))); rfg_sp(); rfg_w("1"); rfg_sp()
        f_scene_guid = rfg_buf.tell(); rfg_sz.append(0); scene_guid_idx = len(rfg_sz)-1
        rfg_w("m_Guid"); rfg_sp(); rfg_w(str(scene_guid_idx)); rfg_sp()
        rfg_w(str(rfg_ft("System.Guid"))); rfg_sp(); rfg_w("0"); rfg_sp()
        rfg_w("36"); rfg_sp(); rfg_buf.write(b"00000000-0000-0000-0000-000000000000")
        rfg_sz[scene_guid_idx] = rfg_buf.tell() - f_scene_guid
        rfg_sz[scene_idx] = rfg_buf.tell() - f_scene

        f_flags = rfg_buf.tell(); rfg_sz.append(0); flags_idx = len(rfg_sz)-1
        rfg_w("SessionFlags"); rfg_sp(); rfg_w(str(flags_idx)); rfg_sp()
        rfg_w(str(rfg_ft("Game.Shared.ESessionFlags"))); rfg_sp(); rfg_w("1"); rfg_sp()
        f_flags_value = rfg_buf.tell(); rfg_sz.append(0); flags_value_idx = len(rfg_sz)-1
        rfg_w("value__"); rfg_sp(); rfg_w(str(flags_value_idx)); rfg_sp()
        rfg_w(str(rfg_ft("System.Int32"))); rfg_sp(); rfg_w("0"); rfg_sp()
        rfg_w(hexlify(struct.pack("<i", rfg_fra_setup["flags"])).decode("ascii")); rfg_sp()
        rfg_sz[flags_value_idx] = rfg_buf.tell() - f_flags_value
        rfg_sz[flags_idx] = rfg_buf.tell() - f_flags

        for field_name, value in (("ArenaInstance", rfg_fra_setup["arena_instance"]),
                                  ("ArenaOwner", rfg_fra_setup["arena_owner"])):
            f_arena = rfg_buf.tell(); rfg_sz.append(0); arena_idx = len(rfg_sz)-1
            rfg_w(field_name); rfg_sp(); rfg_w(str(arena_idx)); rfg_sp()
            rfg_w(str(rfg_ft("System.UInt64"))); rfg_sp(); rfg_w("0"); rfg_sp()
            rfg_w(hexlify(struct.pack("<Q", value)).decode("ascii")); rfg_sp()
            rfg_sz[arena_idx] = rfg_buf.tell() - f_arena
        rfg_sz[ed_idx] = rfg_buf.tell() - f_ed
    # JoinInsteadOfReconnect
    rfg_w("JoinInsteadOfReconnect"); rfg_sp(); rfg_w(str(len(rfg_sz))); rfg_sp()
    rfg_w(str(rfg_ft("System.Boolean"))); rfg_sp(); rfg_w("0"); rfg_sp()
    rfg_w("0"); rfg_sz.append(0)
    # MaxPlayerCount
    rfg_w("MaximumPlayerCount"); rfg_sp(); rfg_w(str(len(rfg_sz))); rfg_sp()
    rfg_w(str(rfg_ft("System.Int32"))); rfg_sp(); rfg_w("0"); rfg_sp()
    rfg_w(hexlify(struct.pack("<i", 2)).decode("ascii")); rfg_sp()
    rfg_sz.append(0)
    # MinPlayerCount
    rfg_w("MinimumPlayerCount"); rfg_sp(); rfg_w(str(len(rfg_sz))); rfg_sp()
    rfg_w(str(rfg_ft("System.Int32"))); rfg_sp(); rfg_w("0"); rfg_sp()
    rfg_w(hexlify(struct.pack("<i", 1)).decode("ascii")); rfg_sp()
    rfg_sz.append(0)
    # SessionId
    rfg_w("SessionId"); rfg_sp(); rfg_w(str(len(rfg_sz))); rfg_sp()
    rfg_w(str(rfg_ft("Game.Shared.UID"))); rfg_sp(); rfg_w("1"); rfg_sp()
    rfg_w("m_UID64"); rfg_sp(); rfg_w(str(len(rfg_sz))); rfg_sp()
    rfg_w(str(rfg_ft("System.UInt64"))); rfg_sp(); rfg_w("0"); rfg_sp()
    rfg_w(hexlify(struct.pack("<Q", session.session_id)).decode("ascii")); rfg_sp()
    rfg_sz.append(0); rfg_sz.append(0)
    # SessionName
    rfg_w("SessionName"); rfg_sp(); rfg_w(str(len(rfg_sz))); rfg_sp()
    rfg_w(str(rfg_ft("System.String"))); rfg_sp(); rfg_w("0"); rfg_sp()
    enc_sname = session.session_name.encode("utf-8")
    rfg_w(str(len(enc_sname))); rfg_sp(); rfg_buf.write(enc_sname)
    rfg_sz.append(0)
    rfg_sz[fss_idx] = rfg_buf.tell()-fss
    
    # TurnOrder (List<UID> with 2 players)
    fto = rfg_buf.tell(); rfg_sz.append(0); fto_idx = len(rfg_sz)-1
    rfg_w("TurnOrder"); rfg_sp(); rfg_w(str(fto_idx)); rfg_sp()
    rfg_w(str(rfg_ft("System.Collections.Generic.List`1#Game.Shared.UID"))); rfg_sp(); rfg_w("0"); rfg_sp()
    rfg_w("2"); rfg_sp()  # count=2
    turn_order_uids = ([pl_uid_val, ai_uid_val]
                       if coin_winner_is_player
                       else [ai_uid_val, pl_uid_val])
    for ti, t_uid_val in enumerate(turn_order_uids):
        ft = rfg_buf.tell(); rfg_sz.append(0); ft_idx = len(rfg_sz)-1
        rfg_w(str(ti)); rfg_sp(); rfg_w(str(ft_idx)); rfg_sp()
        rfg_w(str(rfg_ft("Game.Shared.UID"))); rfg_sp(); rfg_w("1"); rfg_sp()
        ft2 = rfg_buf.tell(); rfg_sz.append(0)
        rfg_w("m_UID64"); rfg_sp(); rfg_w(str(len(rfg_sz)-1)); rfg_sp()
        rfg_w(str(rfg_ft("System.UInt64"))); rfg_sp(); rfg_w("0"); rfg_sp()
        rfg_w(hexlify(struct.pack("<Q", t_uid_val)).decode("ascii")); rfg_sp()
        rfg_sz[-1] = rfg_buf.tell()-ft2; rfg_sz[ft_idx] = rfg_buf.tell()-ft
    rfg_sz[fto_idx] = rfg_buf.tell()-fto
    
    # seedW
    rfg_w("seedW"); rfg_sp(); rfg_w(str(len(rfg_sz))); rfg_sp()
    rfg_w(str(rfg_ft("System.UInt64"))); rfg_sp(); rfg_w("0"); rfg_sp()
    rfg_w(hexlify(struct.pack("<Q", 0x12345678)).decode("ascii")); rfg_sp()
    rfg_sz.append(0)
    # seedZ
    rfg_w("seedZ"); rfg_sp(); rfg_w(str(len(rfg_sz))); rfg_sp()
    rfg_w(str(rfg_ft("System.UInt64"))); rfg_sp(); rfg_w("0"); rfg_sp()
    rfg_w(hexlify(struct.pack("<Q", 0xABCDEF01)).decode("ascii")); rfg_sp()
    rfg_sz.append(0)
    # Error
    rfg_w("Error"); rfg_sp(); rfg_w(str(len(rfg_sz))); rfg_sp()
    rfg_w(str(rfg_ft("Game.Shared.Network.LoadBalancer.EReadyForGameSetupError"))); rfg_sp(); rfg_w("1"); rfg_sp()
    rfg_w("value__"); rfg_sp(); rfg_w(str(len(rfg_sz))); rfg_sp()
    rfg_w(str(rfg_ft("System.Int32"))); rfg_sp(); rfg_w("0"); rfg_sp()
    rfg_w(hexlify(struct.pack("<i", 0)).decode("ascii")); rfg_sp()
    rfg_sz.append(0); rfg_sz.append(0)
    # ErrorMessage
    rfg_w("ErrorMessage"); rfg_sp(); rfg_w(str(len(rfg_sz))); rfg_sp()
    rfg_w(str(rfg_ft("System.String"))); rfg_sp(); rfg_w("0"); rfg_sp()
    rfg_w("0"); rfg_sp()
    rfg_sz.append(0)
    
    rfg_sz[0] = rfg_buf.tell()
    rfg_w(";".join(rfg_types)); rfg_buf.write(b"\n")
    for i, s in enumerate(rfg_sz):
        if i > 0: rfg_sp()
        rfg_w(str(s))
    rfg_resp = rfg_buf.getvalue()
    rfg_body = compress_gzip(rfg_resp) if comp else rfg_resp
    rfg_dw = encode_datawrapper(0, 22027, rfg_body, comp and 1 or 0, "00000000-0000-0000-0000-000000000000")
    handler.scnt += 1
    handler.send({
        "issuer": f"0.0.0.0.ServiceLoadBalancer.{SERVICE_MAIL_UID}.ServicePlayer.{handler.client_uid}.{handler.scnt}",
        "target": "ServiceLoadBalancer", "instance": "Shared",
        "reqid": 0, "c": 0, "conh": 0, "sid": handler.sid,
    }, rfg_dw)
    log_req(f"    Pushed ReadyForGameSetup (22027, {len(rfg_dw)}b)")

    # GameStarted must follow ReadyForGameSetup. The client initializes
    # its session/player/card state from 22027 before accepting the
    # 3053 event; sending these in the opposite order causes the
    # battle scene to stall after joining the encounter.
    gs_inner = _hc.encoder.encode_game_started_response(
        int(player_uid),
        [int(player_uid), game_engine.UID.make(game_engine.SERVICE_AI_UID_TYPE, 1000).uid64],
        seed_w=0xABCDEF0102030405,
        seed_z=0x1234567890ABCDEF,
        success=True)
    gs_body = compress_gzip(gs_inner)
    gs_dw = encode_datawrapper(0, 3053, gs_body, 1, "00000000-0000-0000-0000-000000000000")
    handler.scnt += 1
    handler.send({
        "issuer": f"0.0.0.0.ServiceGameSession.{SERVICE_GAME_SESSION_UID}.{session.session_id}.{handler.scnt}",
        "target": "ServiceGameSession", "instance": gs_instance,
        "reqid": 0, "c": 0, "conh": 0, "sid": handler.sid,
    }, gs_dw)
    log_req(f"    Pushed GameStarted (3053, {len(gs_dw)}b)")

    # Push 3055 game events AFTER 22027
    pl_uid_t = game_engine.UID.make(244, int(handler.client_reck_id))
    ai_uid_t = game_engine.UID.make(game_engine.SERVICE_AI_UID_TYPE, 1000)
    pl_uid_val = (int(handler.client_reck_id) << 8) | 244
    ai_uid_val = (1000 << 8) | SERVICE_AI_UID_TYPE
    sess_tutorial = game_engine.UID(session.session_id)
    player_name = handler.user_profile["name"] if handler.user_profile else "TestPlayer"
    log_req(f"    SESS: raw={session.session_id:#x}, tut={sess_tutorial.uid64:#x}")

    # Game mode: campaign (training/dungeon), Practice mirror, or FRA.
    # Campaign sessions are named "camp_<CampID>" by the campaign
    # 'start' handler, so we can distinguish them thread-safely from
    # the DB-backed session row.
    game = game_engine.Game(sess_tutorial.uid64, pl_uid_t, ai_uid_t)
    handler._scene_global_ability_guids = []
    handler._scene_targeted_ability_guids = []
    handler._scene_targeted_ability_owners = {}
    handler._champion_granted_ability_guids = {}
    handler._campaign_fortune_guid = None
    handler._campaign_fortune_ability_guid = None
    handler._campaign_fortune_starting_hand_bonus = 0
    player_champ_id = game._new_card_id()
    ai_champ_id = game._new_card_id()
    player_talents_json = "[]"

    is_campaign = (session.session_name or "").startswith("camp_")
    is_practice = (session.session_name or "").startswith("Session-")
    mode_name = ("CAMPAIGN" if is_campaign else
                 "PRACTICE" if is_practice else "FRA")
    log_req(f"    Game mode: {mode_name} (session={session.session_name})")
    fra_mod_setup = None
    if not is_campaign and not is_practice:
        from services.arena import get_battle_modifications
        fra_mod_setup = get_battle_modifications(
            int(handler.user_profile["id"]))
        log_req(
        "    FRA encounter setup: "
        f"challenge={(fra_mod_setup.get('challenge') or {}).get('challenge_name', 'none')}, "
        f"mods={len(fra_mod_setup.get('modifications') or [])}")

    campaign_encounter_conversation_guid = None
    campaign_mod_choice_guid = None
    campaign_player_deck_json = None
    campaign_extra_deck_cards = []
    if is_campaign:
        camp_id = int((session.session_name or "camp_0").split("_")[-1] or 0)
        cfg = campaign.resolve_battle_config(
            handler, db._db, camp_id, session.session_name)
        campaign_encounter_conversation_guid = cfg.get(
            "encounter_mod_conversation_guid")
        campaign_mod_choice_guid = cfg.get("encounter_mod_choice_guid")
        scene_guid = cfg["scene_guid"]
        ai_deck_guid = cfg["ai_deck_guid"]
        ai_champ_guid = cfg["ai_champ_guid"]
        ai_name = cfg["ai_name"]
        ai_charge_power = cfg["ai_charge_power"]
        ai_personality = cfg["ai_personality"]
        ai_deck_personality = cfg.get("ai_deck_personality")
        deck_db_id = cfg["deck_db_id"]
        player_champ_name = cfg["player_champ_name"]
        cfg["race_num"], cfg["cls_num"], cfg["gnd_num"]  # required keys, as before
        race_name, cls_name = cfg["race_name"], cfg["cls_name"]
        cfg["gnd_name"]  # required key, as before
        player_talents_json = cfg["player_talents_json"]
        player_starting_health = cfg["player_starting_health"]
        player_champ_guid = cfg["player_champ_guid"]
        campaign_player_deck_json = cfg.get("player_deck_cards_json")
        # Temporary dungeon cards (a Chapel reward pick) join the deck for
        # this run only.
        campaign_extra_deck_cards = cfg.get("extra_deck_cards") or []
        # A mercenary fights with its saved party deck (template GUIDs,
        # like a starter deck); deck equipment does not apply to it.
        mercenary_deck_cards = cfg.get("mercenary_deck_cards")
        if mercenary_deck_cards:
            import json as _merc_json
            campaign_player_deck_json = _merc_json.dumps(mercenary_deck_cards)
        handler._campaign_fortune_guid = cfg.get("fortune_guid")
        handler._campaign_fortune_ability_guid = cfg.get(
            "fortune_ability_guid")
        handler._campaign_fortune_starting_hand_bonus = int(
            cfg.get("fortune_starting_hand_bonus", 0) or 0)
        log_req(f"    Campaign battle: player_deck={deck_db_id} player={player_champ_name} ai={ai_name} ai_deck={ai_deck_guid} scene={scene_guid} player_champ={player_champ_guid}"
                + (f" mercenary_deck={len(mercenary_deck_cards)} cards" if mercenary_deck_cards else ""))
    else:
        # Get player champion from the selected deck. Practice uses
        # this same deck as the AI's mirror; FRA uses it only as the
        # player's deck and obtains an authored challenger below.
        arena = db_get_arena_state(handler.user_profile["id"])
        deck_db_id = handler._resolve_fra_deck_id(arena["deck_id"])
        player_champ_guid = db_get_player_champion_guid(deck_db_id) or "1d462ffb-0744-4996-804c-ba61b2c5c2f1"
        if db_champion_template_data_exists(player_champ_guid, conn=db._db):
            player_starting_health = handler._champion_health_by_guid(player_champ_guid)
        else:
            ext_health = db_extended_champion_health(player_champ_guid, conn=db._db)
            player_starting_health = ext_health if ext_health is not None else 20
        # FRA uses the selected PvP deck rather than a campaign row,
        # but the champion record still owns the selected talent list.
        # Carry it into the same PreGame path used by campaign games.
        _talent_row = db_champion_talents_for_deck(
        handler.user_profile["id"], deck_db_id, conn=db._db)
        if _talent_row and _talent_row[0]:
            player_talents_json = _talent_row[0]
        log_req(f"    {mode_name} battle: deck={deck_db_id} "
            f"(arena={arena['deck_id']}) champ={player_champ_guid} "
            f"health={player_starting_health}")

        if is_practice:
            # Practice is a true mirror match: use the champion
            # selected by the player's resolved deck on both sides.
            # The AI still receives its own SessionCardId/owner UID,
            # but its template, health, charge power, and abilities
            # must come from the same champion definition.
            ai_champ_guid = player_champ_guid
            _mirror_name = db_champion_template_name(ai_champ_guid, conn=db._db)
            ai_name = (_mirror_name if _mirror_name
                       else "AI Opponent")
            ai_deck_guid = None
            ai_deck_personality = "Default"
        else:
            # Get AI champion from the current FRA challenger.
            challengers = db_get_fra_challengers(handler.user_profile["id"])
            cidx = arena["challenger_index"]
            if cidx < len(challengers):
                ai_champ_guid = challengers[cidx]["champion_guid"]
                ai_name = challengers[cidx]["name"]
                ai_deck_guid = challengers[cidx]["deck"]
                ai_deck_personality = (
                    challengers[cidx].get("ai_deck_personality")
                    or "Default")
            else:
                ai_champ_guid = "f8f86969-2e47-4901-8c9e-7fbf8d859e22"
                ai_name = "Angel of Dawn"
                ai_deck_guid = None
                ai_deck_personality = "Default"
        ai_charge_power = db_get_charge_power(ai_champ_guid)

        # FRA encounters have no authored combat attitude. Their
        # static deck strategy is copied onto the saved challenger.
        ai_personality = None

    # Configure the client-style attitude and deck strategy once at
    # the battle boundary. Campaign attitude is the fallback when an
    # encounter deck has no explicit strategy.
    from campaign_runtime.records import encounter_deck_sleeve
    ai_deck_sleeve_guid = (
        db_encounter_deck_sleeve(ai_deck_guid, conn=db._db)
        or encounter_deck_sleeve(ai_deck_guid)) if ai_deck_guid else None
    player_deck_sleeve_guid = (
        db_deck_sleeve(deck_db_id, handler.user_profile["id"], conn=db._db)
        if deck_db_id else None)
    import ai as _ai
    if ai_deck_guid:
        log_req(
        f"    AI deck strategy: deck={ai_deck_guid} "
        f"profile={ai_deck_personality or 'Default'}")
    _ai.configure_personality(
        handler,
        deck_personality=ai_deck_personality,
        campaign_personality=ai_personality,
    )
    
    # Player champion abilities come from the champion's talents
    # (charge powers, spell powers, other passive abilities selected
    # during champion creation).  Each talent is a CardTemplate with
    # an m_CardAbilityId that points to the actual AbilityTemplate —
    # it's the ability GUID, not the talent GUID, that the client's
    # champion card expects in CardUpdated.abilities.
    pl_abilities = []
    try:
        import json as _tal_json
        talent_guids = _tal_json.loads(player_talents_json) if player_talents_json else []
        handler._player_talent_guids = [str(value).lower()
                                     for value in talent_guids]
        for tg in talent_guids:
            # All abilities granted by this talent (one-to-many).
            ab_rows = db_talent_ability_rows(tg, conn=db._db)
            if ab_rows:
                pl_abilities.extend(game_engine.ResourceId.from_str(r[0]) for r in ab_rows)
            else:
                # Passive talent with no ability (e.g. Efficient) — skip.
                pass
    except Exception:
        handler._player_talent_guids = []
        pass
    handler._ai_talent_guids = []
    # The champion's signature charge power comes from gamedata
    # champion_abilities (e.g. Dimmid's "[DIAMOND][DIAMOND]: [BASIC] [2]
    # Target troop gets Lifedrain this turn") — include it even when it
    # isn't granted via a talent.
    from pvp_db import db_get_champion_ability_guids as _db_champ_ab
    for ag in _db_champ_ab(player_champ_guid):
        rid = game_engine.ResourceId.from_str(ag)
        if rid not in pl_abilities:
            pl_abilities.append(rid)

    # Campaign class data supplies the base opening hand. Talent
    # modifiers are resolved from the extracted talent ability/BOM
    # metadata so dungeon-only effects do not leak into campaigns.
    handler._campaign_starting_hand_size = 7
    handler._campaign_max_hand_size = 10
    handler._campaign_starting_hand_effects = []
    if is_campaign:
        hand_cfg = campaign.resolve_opening_hand_config(
            db._db, session, handler.user_profile["id"], race_name, cls_name,
            pl_abilities)
        handler._campaign_starting_hand_size = max(
            0, int(hand_cfg["starting_hand_size"]) +
            handler._campaign_fortune_starting_hand_bonus)
        handler._campaign_max_hand_size = max(
            0, int(hand_cfg["maximum_hand_size"]))
        handler._campaign_starting_hand_effects = list(
            hand_cfg["starting_hand_effects"])
        log_req(
        f"    Campaign opening hand: {handler._campaign_starting_hand_size}, "
        f"maximum: {handler._campaign_max_hand_size}, "
        f"hand effects: {handler._campaign_starting_hand_effects}")

    game.card_defs[player_champ_id] = game_engine.CardDef("Player",
        game_engine.ECardTypes.Champion, 0, player_starting_health, player_starting_health, [], pl_abilities)
    ai_starting_health = handler._champion_health_by_guid(ai_champ_guid)
    from pvp_db import db_get_champion_ability_guids as _db_champ_ab_ai
    ai_abilities = [game_engine.ResourceId.from_str(g)
                    for g in _db_champ_ab_ai(ai_champ_guid)]
    game.card_defs[ai_champ_id] = game_engine.CardDef(ai_name,
        game_engine.ECardTypes.Champion, 0, ai_starting_health, ai_starting_health, [], ai_abilities)
    game.player_champion_card_id = player_champ_id
    handler._player_champ_scid = player_champ_id
    handler._ai_champ_scid = ai_champ_id
    handler._player_champ_guid = player_champ_guid
    handler._ai_champ_guid = ai_champ_guid
    handler._player_champ_abilities = list(pl_abilities)  # ResourceId list
    handler._ai_champ_ability_guids = [str(a.guid) for a in ai_abilities]
    # Persist starting health for the DB battle state (combat damage + reconnect).
    handler._player_starting_health = player_starting_health
    handler._ai_starting_health = ai_starting_health
    handler._player_starting_charges = 0
    handler._ai_starting_charges = 0
    game.ai_champion_card_id = ai_champ_id
    # Carry the real starting health into the Game so the initial
    # PlayerUpdated reports it (a fresh Game defaults to 20 — Iddi is 10).
    game.player_health = player_starting_health
    game.ai_health = ai_starting_health
    # Attach the authoritative RulesPort before any deck/setup cards
    # are materialized.  CardCreated, hidden setup, and PreGame
    # triggers must use the same native discovery/resolution path as
    # turn-time gameplay; attaching only when the first transaction
    # arrives leaves setup as a second rules engine.
    setup_bstate = {
        "stack": [], "ability_lists": {}, "pvp": False,
        "turn_player": "player", "phase_idx": 0,
        "player_health": int(game.player_health),
        "ai_health": int(game.ai_health),
        "player_resources": 0, "ai_resources": 0,
        "player_threshold": {}, "ai_threshold": {},
        "champ_map": {
            int(handler.user_profile.get("id", 0)):
                int(player_champ_id.uid.uid64),
            0: int(ai_champ_id.uid.uid64),
        },
        # Champion template GUIDs by owner: continuous champion
        # passives (Construct Foreman's "Champions have no maximum
        # hand size") have no ``game_cards`` row, so static rule
        # evaluation resolves their authored abilities from here.
        "champ_guid_map": {
            str(int(handler.user_profile.get("id", 0))): player_champ_guid,
            "0": ai_champ_guid,
        },
        "talent_guids_by_owner": {
            str(int(handler.user_profile.get("id", 0))):
                list(getattr(handler, "_player_talent_guids", ())),
            "0": list(getattr(handler, "_ai_talent_guids", ())),
        },
        # Deck sleeves must survive every fresh per-packet Game; the
        # client resets a player's cards to the default Hex sleeve when
        # a PlayerUpdated omits the field.
        "player_deck_sleeve_id": player_deck_sleeve_guid or "",
        "ai_deck_sleeve_id": ai_deck_sleeve_guid or "",
    }
    handler._player_deck_sleeve_guid = player_deck_sleeve_guid
    handler._ai_deck_sleeve_guid = ai_deck_sleeve_guid
    handler._maybe_attach_rules_port(session, game, setup_bstate)
    def _drain_setup_stack(state):
        """Resolve setup-time native triggers without a UI window."""
        import hconnect_server as _hc
        guard = 0
        while state.get("stack") and guard < 256:
            guard += 1
            from rules_port.resolution import resolve_port_trigger
            resolve_port_trigger(
                handler, game, session, db._db, pl_uid_t, ai_uid_t,
                state, state["stack"].pop())
        if state.get("stack"):
            raise RuntimeError("setup RulesPort trigger stack did not drain")
    game.max_hand_size = handler._max_hand_size(session)
    # GameStarted.ChampionNames drives the large pre-battle/coin-toss
    # banner in UIBattle.  Campaigns must identify the player's
    # champion there, not their account, just as ChampionCardPlayed
    # does for the in-battle HUD.
    if is_campaign:
        player_name = player_champ_name if player_champ_name else "Player"
    # Keep GameStarted's order identical to the setup response: the
    # first entry is the coin-toss winner, not necessarily the player
    # who eventually plays first.
    _push_pve_game_started(
    game, player_name=player_name, ai_name=ai_name,
    player_champion_guid=player_champ_guid,
    ai_champion_guid=ai_champ_guid,
    player_sleeve_guid=player_deck_sleeve_guid,
    ai_sleeve_guid=ai_deck_sleeve_guid,
    coin_winner_is_player=coin_winner_is_player)
    game.push_player_updated(
        pl_uid_t, deck_sleeve_id=player_deck_sleeve_guid,
        champ_id=getattr(handler, "_player_champ_scid", None))
    game.push_player_updated(
        ai_uid_t, deck_sleeve_id=ai_deck_sleeve_guid,
        champ_id=getattr(handler, "_ai_champ_scid", None))
    # A champion's static intattrs (Armor 3 from its own ability) are on the
    # portrait from the start; they are not stored, so they are computed here.
    from rules_port.static_rules import champion_static_intattrs
    player_owner = int(handler.user_profile["id"]) if handler.user_profile else 0
    start_state = {
        "champ_map": {"0": int(ai_champ_id.uid.uid64),
                      str(player_owner): int(player_champ_id.uid.uid64)},
        "champ_guid_map": {"0": ai_champ_guid, str(player_owner): player_champ_guid}}
    player_static = champion_static_intattrs(
        db._db, session.session_id, start_state, player_owner)
    ai_static = champion_static_intattrs(db._db, session.session_id, start_state, 0)
    game.push_card_updated(player_champ_id, pl_uid_t, game_engine.ECardCollections.None_,
                           game_engine.ECardTypes.Champion, attack=0, defense=player_starting_health,
                           template_id=player_champ_guid,
                           **({"int_attrs": player_static} if player_static else {}))
    game.push_card_updated(ai_champ_id, ai_uid_t, game_engine.ECardCollections.None_,
                           game_engine.ECardTypes.Champion, attack=0, defense=ai_starting_health,
                           template_id=ai_champ_guid,
                           **({"int_attrs": ai_static} if ai_static else {}))

    # HUD portrait name: use the champion's name (e.g. "Morgana") for
    # the player, and the trainer NPC name for the AI (e.g. "Iddi").
    # For campaign, player_champ_name was resolved via the campaign ->
    # champion -> deck chain; for FRA, look up via the deck's champion.
    if not is_campaign:
        player_name = handler.user_profile["name"] if handler.user_profile else "TestPlayer"
        if deck_db_id:
            deck_champion_name = db_deck_champion_name(deck_db_id, conn=db._db)
            if deck_champion_name:
                player_name = deck_champion_name
    log_req(f"    Battle HUD names: player={player_name} ai={ai_name}")
    # The champion card views are placed by ChampionCardPlayed, and
    # that event only lands here, inside the game-start burst, while
    # Unity is still entering the battle scene.  Keep the exact
    # arguments so the board projection can re-announce them once the
    # client has proved it is in the battle UI.
    handler._player_champ_board = (
        player_champ_id, False, player_name, player_champ_guid)
    handler._ai_champ_board = (
        ai_champ_id, True, ai_name, ai_champ_guid)
    game.push_champion_card_played(pl_uid_t, False, player_name, player_champ_id)
    game.push_champion_card_played(ai_uid_t, True, ai_name, ai_champ_id)
    
    # Populate game_cards from player's deck in DB
    import json as _json
    db_clear_session_cards(session.session_id)
    deck_rows = None
    if campaign_player_deck_json:
        # The scene fixes the player's deck: its encounter-deck cards.
        deck_rows = (campaign_player_deck_json,)
    elif deck_db_id and handler.user_profile:
        deck_cards_json = db_deck_cards_json(
        deck_db_id, handler.user_profile["id"], conn=db._db)
        deck_rows = (deck_cards_json,) if deck_cards_json is not None else None
    player_deck_cards = []
    player_card_tpl_ids = []
    player_resolved_tpl_ids = []
    # Resolve the active socket map before materializing rows so gem
    # abilities can be included in the first card snapshot.
    import json as _gemj
    deck_gem_abilities = {}
    deck_gem_types = {}
    if deck_db_id:
        active_gems_json = db_deck_active_gems(
        deck_db_id, conn=db._db)
        if active_gems_json:
            try:
                active_gems = _gemj.loads(active_gems_json) or {}
                deck_gem_types = {
                    str(key): int(value or 0)
                    for key, value in active_gems.items()
                }
                deck_gem_abilities = handler._resolve_gem_abilities(
                    active_gems)
            except Exception:
                deck_gem_abilities = {}
    player_setup_cache = {}
    player_ref_cache = {}
    player_card_data = []
    player_insert_rows = []
    # Equipped items replace their cards with the equipment-modified
    # templates the client data provides for each combination.  Only the
    # player's own saved deck carries equipment: a scene-fixed or mercenary
    # deck does not.
    from profile_db import db_deck_equipment
    from services.equipment import equipped_variant
    deck_equipment = (db_deck_equipment(deck_db_id, conn=db._db)
                      if deck_db_id and not campaign_player_deck_json else [])
    if deck_equipment:
        log_req(f"    Deck equipment: {deck_equipment}")
    if deck_rows and deck_rows[0]:
        card_ids = _json.loads(deck_rows[0]) + campaign_extra_deck_cards
        random.shuffle(card_ids)
        for pos, card_ref in enumerate(card_ids):  # Push full deck
            cid = game._new_card_id()
            # card_ref is either a template GUID (campaign starter
            # deck) or an instance id (FRA deck). Resolve to GUID.
            if isinstance(card_ref, str) and "-" in card_ref:
                card_tpl_id = card_ref
            else:
                card_tpl_id = card_ref
            player_card_tpl_ids.append(card_tpl_id)
            card_uid = cid.uid.to_uint64()
            ref_key = (("guid", str(card_tpl_id).lower())
                       if isinstance(card_tpl_id, str)
                       else ("instance", card_tpl_id))
            if ref_key not in player_ref_cache:
                resolved = handler._resolve_card_ref(
                    card_tpl_id, handler.user_profile["id"])
                variant = equipped_variant(resolved[0], deck_equipment)
                if variant:
                    upgraded = handler._resolve_card_ref(
                        variant, handler.user_profile["id"])
                    if upgraded[0]:
                        log_req(f"    Equipped {resolved[2]}: "
                                f"{resolved[0]} -> {variant}")
                        resolved = upgraded
                player_ref_cache[ref_key] = resolved
            _tpl, ctype, _n, _c, _a, _d = player_ref_cache[ref_key]
            player_resolved_tpl_ids.append(_tpl)
            setup_data = handler._battle_setup_template_data(
                _tpl, player_setup_cache)
            if setup_data:
                abilities_json, effective_attrs, _ags, _threshold = setup_data
            else:
                abilities_json, effective_attrs = "[]", 0
            player_insert_rows.append(
                (handler.user_profile["id"], session.session_id, card_uid,
                 card_tpl_id, ctype or "Unknown", _tpl, 'deck', pos,
                 handler.user_profile["id"], _tpl, abilities_json,
                 effective_attrs, "{}",
                 deck_gem_types.get(str(card_tpl_id), 0)))
            player_card_data.append({
                "row": ((_tpl, ctype, _n, _c, _a, _d)
                        if _tpl else None),
                "setup": setup_data,
            })
            player_deck_cards.append(cid)
    if player_insert_rows:
        db_insert_game_cards(player_insert_rows, conn=db._db)
    db._db.commit()
    # Derive client definitions from the same prepared metadata used
    # for insertion; only socketed-gem ability merges need a later
    # update.
    player_ability_updates = []
    for i, cid in enumerate(player_deck_cards):
        # No opening hands yet — like PvP, both decks stay face-down
        # through PreGame/PickGoesFirst; each player draws 7 only after
        # the Play/Draw pick (see the 3029 ChoosePlay/ChooseDraw
        # handler).
        col = game_engine.ECardCollections.Deck
        instance_id = player_card_tpl_ids[i]
        # Look up card template GUID and type. The campaign starter
        # deck stores template GUIDs directly; FRA decks store
        # instance ids (resolve via card_instances).
        tpl_guid = "00000000-0000-0000-0000-000000000000"
        ct = game_engine.ECardTypes.Troop
        ctype_name = "Troop"
        cost, atk, def_ = 0, 0, 0
        shards = []
        card_data = player_card_data[i]
        row = card_data["row"]
        setup_data = card_data["setup"]
        if row:
            tpl_guid = row[0]
            ctype_name = row[1]
            ct = game_engine.card_type_from_db(ctype_name)
            cost, atk, def_ = row[3] or 0, row[4] or 0, row[5] or 0
            # Fetch threshold data
            import json as _json2
            _base_ab_json, _base_attrs, _base_ags, trow_val = (
                setup_data if setup_data else ("[]", 0, [], None))
            if trow_val:
                try:
                    td = _json2.loads(trow_val)
                    shard_flags = {0:0, 1:4, 2:8, 3:16, 4:32, 5:64}
                    raw_list = td.get('list', [])
                    shards = [shard_flags.get(s, s) for s in raw_list]
                except: pass
            # Fetch abilities from the setup cache.
            ab_guids = list(_base_ags)
            # Gem-granted abilities (baked into the deck at save time)
            # join the card's ability list from the very first push.
            for _gem_ag in (deck_gem_abilities.get(str(instance_id)) or []):
                if str(_gem_ag).lower() not in [str(g).lower() for g in ab_guids]:
                    ab_guids.append(str(_gem_ag).lower())
            abilities = [game_engine.ResourceId.from_str(g) for g in ab_guids]
            # Fetch static/effective attributes from the setup cache.
            attributes = int(_base_attrs or 0)
            if len(ab_guids) != len(_base_ags):
                player_ability_updates.append(
                    (_json2.dumps(ab_guids), session.session_id,
                     cid.uid.to_uint64()))
        else:
            # Missing instance — insert a placeholder instance
            fallback = db_first_template_guid(conn=db._db)
            if fallback:
                tpl_guid = fallback
                db_insert_card_instance(
                handler.user_profile["id"], instance_id, tpl_guid,
                conn=db._db)
        # Effective attributes = static template attributes + any
        # granted by the card's own abilities (passives), so attribute
        # icons show in hand from the first CardUpdated.
        cdef_attrs = game_engine.ECardAttributes.Unknown
        if row:
            cdef_attrs = attributes
        game.card_defs[cid] = game_engine.CardDef(row[2] if row else "Card", ct, cost, atk, def_, shards, abilities, cdef_attrs)
        # Deck cards: minimal update so the client knows they exist.
        # Both players' decks stay face-down (nulling=True) — you
        # cannot inspect your own deck mid-game. The client shows the
        # deck sleeve and skips the examiner for Null cards.
        game.push_card_updated(cid, pl_uid_t, game_engine.ECardCollections.Deck, ct,
                              nulling=True)
    if player_ability_updates:
        db_update_game_card_abilities(player_ability_updates, conn=db._db)
    if player_deck_cards:
        game.push_deck_created_with_cards(
            pl_uid_t, player_deck_cards,
            sleeve_guid=player_deck_sleeve_guid)
        # Client-side workaround: a nulled deck card that carries
        # socketed-gem abilities (e.g. Shamed Gladiator's Rage gem) can
        # render as a ghost in the warzone at game start.  Re-assert
        # the gem-bearing deck cards into the deck during PreGame so
        # the client's zone placement matches the authoritative DB.
        if deck_gem_abilities:
            for i, cid in enumerate(player_deck_cards):
                if str(player_card_tpl_ids[i]) not in deck_gem_abilities:
                    continue
                # cid is already a SessionCardId (game._new_card_id());
                # wrapping it again made the CardMoved's session card
                # id serialize a SessionCardId as its UID and crashed
                # make_network_packet during PreGame.
                c_scid = cid
                game.push_card_moved(
                    c_scid, pl_uid_t, game_engine.ECardCollections.Deck,
                    game_engine.ECardLocations.Top, 0)
    
    # AI deck (shuffled). For campaign battles use the encounter's
    # real AI deck from encounter_deck_cards; otherwise a simple deck.
    ai_deck_cards = []
    import json as _aj
    ai_card_specs = []
    scene_ai_ability_guids = []
    scene_mod_card_guids = set()
    if is_practice:
        # Practice is explicitly a mirror match. Use the resolved
        # player deck templates, preserving one AI card per player
        # card and the current socketed-gem abilities.
        active_gems = {}
        if deck_db_id:
            active_gems_json = db_deck_active_gems(
            deck_db_id, conn=db._db)
            if active_gems_json:
                try:
                    active_gems = _aj.loads(active_gems_json) or {}
                except (TypeError, ValueError):
                    active_gems = {}
        for idx, cg in enumerate(player_resolved_tpl_ids):
            if not cg:
                continue
            gem_key = str(player_card_tpl_ids[idx])
            gem_type = int(active_gems.get(gem_key, 0) or 0)
            gem_ability_guids = deck_gem_abilities.get(gem_key) or []
            ai_card_specs.append((cg, gem_type, gem_ability_guids))
    elif not ai_deck_guid:
        # Practice sessions do not provide an encounter deck GUID.
        # Use a validated extracted starter deck so the opponent has
        # real card templates, resources, abilities, and a deck that
        # can also be inspected by !zones. The Human list is a
        # neutral fallback; encounter-specific decks still take
        # precedence below.
        fallback_deck = _hc._STARTER_DECKS.get("Human") or {}
        for card_guid, quantity in fallback_deck.get("cards", []):
            for _ in range(max(0, int(quantity or 0))):
                if db_template_exists(card_guid, conn=db._db):
                    ai_card_specs.append((card_guid, 0, []))
    if ai_deck_guid or ai_card_specs:
        gem_types_by_name = {
            str(name): (int(gem_type or 0),
                        _aj.loads(abilities_json or "[]")
                        if abilities_json else [])
            for name, gem_type, abilities_json in db_gem_templates(
                conn=db._db)
        }
        if ai_deck_guid:
            rows = db_encounter_deck_cards(ai_deck_guid, conn=db._db)
            for cg, q, gem_json in rows:
                try:
                    gem_slots = _aj.loads(gem_json or "[]")
                except (TypeError, ValueError):
                    gem_slots = []
                for copy_index in range(int(q or 0)):
                    gem_type = 0
                    gem_ability_guids = []
                    socket_names = (gem_slots[copy_index]
                                    if copy_index < len(gem_slots)
                                    else [])
                    if not isinstance(socket_names, list):
                        socket_names = [socket_names]
                    for socket_name in socket_names:
                        gem_data = gem_types_by_name.get(str(socket_name))
                        if not gem_data:
                            continue
                        if not gem_type:
                            gem_type = gem_data[0]
                        for gem_guid in gem_data[1] or []:
                            gem_guid = str(gem_guid).lower()
                            if gem_guid not in gem_ability_guids:
                                gem_ability_guids.append(gem_guid)
                    ai_card_specs.append(
                        (cg, gem_type, gem_ability_guids))
        # EncounterScene player modifications are authored metadata,
        # not card text.  Add cards targeted at the AI (for example
        # Taming Dire Toad's Taming Sphere) before the opening hand
        # is dealt so their GameStarted abilities can resolve.
        if is_campaign and scene_guid:
            # All players use the shared authored scene modifiers.
            scene_mods_json = db_scene_mods(scene_guid, conn=db._db)
            try:
                scene_mods = _aj.loads(scene_mods_json or "[]")
            except (TypeError, ValueError):
                scene_mods = []
            # A "choice" mod (card-selector battle) applies only the
            # card the player picked; unpicked options are dropped.
            import campaign_rules as _campaign_rules
            scene_mods = _campaign_rules.chosen_scene_mods(
                scene_mods, campaign_mod_choice_guid)
            # Targeted entries with a round are not placed now; they are
            # applied when their side reaches that turn (see
            # rules_port.scene_round_mods).
            from rules_port.scene_round_mods import (
                PENDING_KEY as _ROUND_KEY, split_round_mods)
            scene_mods, _round_pending = split_round_mods(scene_mods)
            setup_bstate[_ROUND_KEY] = _round_pending
            # A card attached to a concrete encounter player is a
            # hidden battleboard controller, not a scene-wide grant.
            # The scene metadata can also repeat that card in a
            # descriptive modifier entry, so collect the targeted
            # GUIDs before interpreting untargeted scene abilities.
            targeted_scene_mod_guids = {
                str(item.get("guid")).lower()
                for mod in scene_mods if mod.get("target")
                for item in mod.get("mods", [])
                if item.get("guid")
            }
            scene_setup_card_guids = set()

            def _has_game_started_grant(card_guid):
                """Whether a scene card has a setup-time ability.

                Untargeted encounter setup cards are authored as
                scene modifiers, but their ``You`` and ``opposing
                champion`` ownership is evaluated from the encounter
                side that owns the hidden card.  Keep those cards in
                the AI mod zone so the normal metadata/BOM resolver
                applies the opposing-champion target correctly.  The
                authored ability can either grant a GameStarted
                ability or be a direct non-manual BOM (such as Early
                Sprouts' resource grant).
                """
                import hconnect_server as _hc
                card_abilities_json = db_template_ability_guids(
                card_guid, conn=db._db)
                try:
                    ability_guids = _aj.loads(
                        card_abilities_json or "[]")
                except (TypeError, ValueError):
                    ability_guids = []
                for ability_guid in ability_guids:
                    meta = db_ability_metadata(ability_guid, conn=db._db)
                    effect_exists = db_ability_has_effect(
                    ability_guid, conn=db._db)
                    if (meta and effect_exists and not meta[0] and
                            ("GameStartedEvent" in (meta[1] or "") or
                             not (meta[1] or ""))):
                        return True
                    for _eg, effect_type, raw_param in db_ability_effect_rows(
                        ability_guid, conn=db._db):
                        if effect_type != "GrantAbilityEffectTemplate":
                            continue
                        try:
                            grant_data = _aj.loads(raw_param or "{}")
                        except (TypeError, ValueError):
                            grant_data = None
                        granted_guid = (
                            grant_data.get("ability_guid") or
                            grant_data.get("abilityId")
                            if isinstance(grant_data, dict)
                            else str(raw_param or "").strip())
                        if not granted_guid:
                            continue
                        trigger_row = db_ability_metadata(
                        granted_guid, conn=db._db)
                        if (trigger_row and
                                "GameStartedEvent" in (trigger_row[1] or "")):
                            return True
                return False

            for mod in scene_mods:
                for item in mod.get("mods", []):
                    mg = item.get("guid")
                    mg_key = str(mg or "").lower()
                    # A scene-level card with a start-of-game grant is
                    # an encounter setup card.  Cockatwice's Taming
                    # modifier is the canonical example: the hidden AI
                    # source grants the sphere to its opposing player.
                    # If the authored data already contains an
                    # explicit AI copy (Wild Cub), use that copy once.
                    if (mg and not mod.get("target") and
                            mg_key not in targeted_scene_mod_guids and
                            _has_game_started_grant(mg)):
                        ai_card_specs.append((mg, 0, []))
                        scene_mod_card_guids.add(mg_key)
                        scene_setup_card_guids.add(mg_key)
                    if mg and mod.get("target") == "AIPlayer":
                        ai_card_specs.append((mg, 0, []))
                        scene_mod_card_guids.add(mg_key)
                        # Resolve controller-only grants (for example
                        # the Untamed Dire Toad passive) and apply them
                        # later to matching visible troops.
                        _mcard_json = db_template_ability_guids(
                        mg, conn=db._db)
                        try:
                            _mags = _aj.loads(_mcard_json or "[]")
                        except (TypeError, ValueError):
                            _mags = []
                        for _mag in _mags:
                            for _meg, _met, _mep in db_ability_effect_rows(
                                _mag, conn=db._db):
                                if _met != "GrantAbilityEffectTemplate":
                                    continue
                                _granted = str(_mep or "").lower()
                                if not _granted:
                                    continue
                                _gtids_json = _hc.db_ability_target_template_ids(
                                    _granted, conn=db._db)
                                try:
                                    _gtids = _aj.loads(_gtids_json or "[]")
                                except (TypeError, ValueError):
                                    _gtids = []
                                for _gtid in _gtids:
                                    _filter_json = db_target_template_filter(
                                    _gtid, conn=db._db)
                                    if (_filter_json or "{}").strip() not in ("", "{}"):
                                        if _granted not in handler._scene_targeted_ability_guids:
                                            handler._scene_targeted_ability_guids.append(_granted)
                                        # Preserve the controller of
                                        # the hidden setup card so a
                                        # typed IsControlledBy filter
                                        # continues to mean "your"
                                        # troops when the passive is
                                        # applied to cards materialized
                                        # later in the warzone.
                                        handler._scene_targeted_ability_owners.setdefault(
                                            _granted, 0 if mod.get("target") == "AIPlayer"
                                            else int(handler.user_profile["id"]))
                    # Scene ability cards (for example Big Game) grant
                    # a passive ability to the encounter's troops.
                    # Resolve the grant through the ability metadata;
                    # do not infer it from display text.
                    if (mg and mg_key not in targeted_scene_mod_guids
                            and mg_key not in scene_setup_card_guids):
                        _abilities_json = db_template_ability_guids(
                        mg, conn=db._db)
                        try:
                            _ags = _aj.loads(_abilities_json or "[]")
                        except (TypeError, ValueError):
                            _ags = []
                        for _ag in _ags:
                            for _eg, _etype, _param in db_ability_effect_rows(
                                _ag, conn=db._db):
                                if _etype != "GrantAbilityEffectTemplate":
                                    continue
                                try:
                                    _gp = _aj.loads(_param or "{}")
                                    _grant = _gp.get("ability_guid") or _gp.get("abilityId")
                                except (TypeError, ValueError):
                                    _grant = _param if _param else None
                                if _grant and _grant not in scene_ai_ability_guids:
                                    scene_ai_ability_guids.append(_grant)
            # Scene-wide statics are applied silently when a matching
            # card is materialized by _card_full_data. This covers
            # cards created later without pushing setup abilities onto
            # the chain. The ability's own target filter determines
            # which cards qualify.
            handler._scene_global_ability_guids = list(
                scene_ai_ability_guids)
        # If we couldn't resolve an AI deck, fall back to FRA-style.
        if ai_card_specs:
            random.shuffle(ai_card_specs)
            ai_setup_cache = {}
            ai_template_rows = {}
            ai_materialized = []
            ai_insert_rows = []
            hidden_bstate = {"stack": [], "ability_lists": {},
                             "_rules_port_attached":
                             bool(setup_bstate.get("_rules_port_attached"))}
            for pos, (cg, gem_type, gem_ability_guids) in enumerate(
                    ai_card_specs):
                cid = game._new_card_id()
                _is_scene_mod = str(cg).lower() in scene_mod_card_guids
                if not _is_scene_mod:
                    ai_deck_cards.append(cid)
                cg_key = str(cg).lower()
                if cg_key not in ai_template_rows:
                    ai_template_rows[cg_key] = db_template_card_info(
                    cg, conn=db._db)
                r = ai_template_rows[cg_key]
                if r:
                    ct2 = game_engine.card_type_from_db(r[0])
                    game.card_defs[cid] = game_engine.CardDef(
                        r[1], ct2, r[2] or 0, r[3] or 0, r[4] or 0, [], [])
                else:
                    game.card_defs[cid] = game_engine.CardDef(
                        "Card", game_engine.ECardTypes.Troop, 2, 2, 2, [], [])
                # Encounter-mod cards are placed directly in the AI
                # warzone so their GameStarted abilities resolve;
                # normal encounter cards remain in the deck.
                col = (game_engine.ECardCollections.Warzone
                       if _is_scene_mod else game_engine.ECardCollections.Deck)
                _ai_ct = r[0] if r else "Troop"
                setup_data = handler._battle_setup_template_data(
                    cg, ai_setup_cache)
                if setup_data:
                    abilities_json, effective_attrs, base_ags, _threshold = setup_data
                else:
                    abilities_json, effective_attrs, base_ags = "[]", 0, []
                abilities = list(base_ags)
                for gem_guid in gem_ability_guids or []:
                    gem_guid = str(gem_guid).lower()
                    if gem_guid not in abilities:
                        abilities.append(gem_guid)
                ai_insert_rows.append(
                    (0, session.session_id, cid.uid.to_uint64(), cg,
                     _ai_ct, cg, 'mod' if _is_scene_mod else 'deck',
                     pos, 0, cg, _aj.dumps(abilities),
                     effective_attrs, "{}", gem_type or 0))
                ai_materialized.append((cid, cg, _is_scene_mod, col))
            if ai_insert_rows:
                db_insert_game_cards(ai_insert_rows, conn=db._db)
            db._db.commit()
            for cid, cg, _is_scene_mod, col in ai_materialized:
                if not _is_scene_mod:
                    game.push_card_updated(
                        cid, ai_uid_t, col,
                        game.card_defs[cid].card_type, nulling=True)
                # Build the authoritative CardDef after the complete
                # batch is visible. This preserves gem and scene setup
                # behavior without a SELECT/UPDATE pair per card.
                handler._card_full_data(game, cid, cg)
                if _is_scene_mod:
                    # Scene setup cards are hidden battleboard cards.
                    # The client emits both zone-entry events when a
                    # hidden permanent is placed into the warzone;
                    # keep DeployHidden on the same generic trigger
                    # dispatcher instead of treating setup cards as a
                    # special list of encounter names.
                    from rules_port.triggers import dispatch_native_trigger
                    dispatch_native_trigger(
                        db=db._db, handler=handler, game=game,
                        session=session, player_uid=pl_uid_t,
                        ai_uid=ai_uid_t,
                        battle_state=hidden_bstate,
                        event_type="HiddenCardEnteredZoneEvent",
                        source_card_id=cid.uid.to_uint64(),
                        source_player_id=0,
                        data={"zones": ("mod",),
                              "event_source_collection": "mod",
                              "event_destination_collection": "warzone"})
                    # Setup happens before the persistent battle-state
                    # stack exists. Hidden deploy abilities are not
                    # universally marked IgnoresChain in the source
                    # metadata, so drain their local setup chain here
                    # using the same authoritative resolver.
                    _hidden_resolved = 0
                    while (hidden_bstate.get("stack") and
                           _hidden_resolved < 256):
                        _hidden_item = hidden_bstate["stack"].pop()
                        from rules_port.resolution import resolve_port_trigger
                        resolve_port_trigger(
                            handler, game, session, db._db, pl_uid_t,
                            ai_uid_t, hidden_bstate, _hidden_item)
                        _hidden_resolved += 1
            db._db.commit()
            source = (f"encounter {ai_deck_guid}" if ai_deck_guid
                      else "player mirror" if is_practice
                      else "Human starter fallback")
            log_req(f"    AI deck: {len(ai_deck_cards)} cards from "
                f"{source} (hands dealt after PickGoesFirst)")
    if ai_deck_cards:
        # A dungeon run's "until the end of the dungeon" bonuses carry into this battle's state, where the continuous
        # scan adds them to the AI troops (before the deck is pushed, so the client sees the bonused stats).
        from campaign_runtime import dungeon_buffs
        dungeon_camp = dungeon_buffs.camp_id_from_session(getattr(session, "session_name", ""))
        if dungeon_camp is not None:
            dungeon_buffs.carry_into_battle(db._db, dungeon_camp, setup_bstate)
        game.push_deck_created_with_cards(
            ai_uid_t, ai_deck_cards,
            sleeve_guid=ai_deck_sleeve_guid)
    if fra_mod_setup:
        handler._apply_fra_battle_modifications(
            game, session, pl_uid_t, ai_uid_t, setup_bstate,
            fra_mod_setup.get("modifications") or [],
            player_deck_cards, ai_deck_cards,
            _drain_setup_stack)
    db._db.commit()
    # C# writes StartingDeckSize to each champion card's
    # PlayerGameStats before it runs CardCreated/PreGame abilities.
    # Preserve that opening count in the shared runtime TAC projection
    # before setup triggers can draw, move, or create cards.
    from rules_port.statistics import set_tac_stat
    for _owner, _champ_uid, _deck_size in (
            (int(handler.user_profile["id"]),
             int(player_champ_id.uid.uid64), len(player_deck_cards)),
            (0, int(ai_champ_id.uid.uid64), len(ai_deck_cards))):
        set_tac_stat(
            setup_bstate, "cards", _champ_uid,
            "PlayerGameStats", "StartingDeckSize", _deck_size)
    if setup_bstate.get("_rules_port_attached"):
        from rules_port.persistence import save_state
        save_state(session, setup_bstate)
    # CardCreatedEvent triggers fire when the deck's cards are created
    # (the client's ActivateCardCreationAbilities during deck setup) —
    # e.g. Pterobot's permanent "cost -1 for each Dwarf and/or Robot
    # you control" modifier.  These resolve immediately (IgnoresChain)
    # into the DB; no priority window exists during setup.
    from rules_port.triggers import dispatch_native_trigger
    cc_bstate = {"stack": [], "ability_lists": {},
                 "_rules_port_attached": True,
                 "champ_map": setup_bstate.get("champ_map", {}),
                 "talent_guids_by_owner": setup_bstate.get(
                     "talent_guids_by_owner", {}),
                 "tac_statistics": setup_bstate.get(
                     "tac_statistics", {})}
    for cid in player_deck_cards:
        dispatch_native_trigger(
            db=db._db, handler=handler, game=game, session=session,
            player_uid=pl_uid_t, ai_uid=ai_uid_t,
            battle_state=cc_bstate,
            event_type="CardCreatedEvent",
            source_card_id=cid.uid.to_uint64(),
            source_player_id=handler.user_profile["id"],
            data={"zones": ()})
        _drain_setup_stack(cc_bstate)
    for cid in ai_deck_cards:
        dispatch_native_trigger(
            db=db._db, handler=handler, game=game, session=session,
            player_uid=pl_uid_t, ai_uid=ai_uid_t,
            battle_state=cc_bstate,
            event_type="CardCreatedEvent",
            source_card_id=cid.uid.to_uint64(),
            source_player_id=0, data={"zones": ()})
        _drain_setup_stack(cc_bstate)

    # PreGameState enqueues one metadata event for each champion after
    # the decks exist, so deck-held PreGame abilities must be resolved
    # from the live card ability lists before the opening hand is
    # dealt.  This is intentionally one shared event path for PVE
    # player and encounter decks; champion talent setup below remains
    # the separate champion-source compatibility path.
    pregame_bstate = {
        "stack": [],
        "ability_lists": {},
        "_rules_port_attached": True,
        "player_health": int(game.player_health),
        "ai_health": int(game.ai_health),
        "player_resources": int(getattr(game, "player_resources", 0)),
        "ai_resources": int(getattr(game, "ai_resources", 0)),
        "champ_map": setup_bstate.get("champ_map", {}),
        "talent_guids_by_owner": setup_bstate.get(
            "talent_guids_by_owner", {}),
        "tac_statistics": setup_bstate.get("tac_statistics", {}),
    }
    for owner in (handler.user_profile["id"], 0):
        dispatch_native_trigger(
            db=db._db, handler=handler, game=game, session=session,
            player_uid=pl_uid_t, ai_uid=ai_uid_t,
            battle_state=pregame_bstate,
            event_type="PreGameEvent", source_card_id=None,
            source_player_id=owner,
            data={"zones": ("deck",)})
        _drain_setup_stack(pregame_bstate)
    army_resources = _army_of_myth_ai_resources(session)
    if army_resources is not None:
        pregame_bstate["ai_resources"] = army_resources
    game.player_health = int(pregame_bstate.get(
        "player_health", game.player_health))
    game.ai_health = int(pregame_bstate.get(
        "ai_health", game.ai_health))
    game.player_resources = int(pregame_bstate.get(
        "player_resources", getattr(game, "player_resources", 0)))
    game.ai_resources = int(pregame_bstate.get(
        "ai_resources", getattr(game, "ai_resources", 0)))
    # NOTE: the AI's mulligan decision is NOT made here. It happens as
    # part of the Mulligan phase, resolved after the human acts (keep or
    # redraw) — see the 3029 handler's _resolve_ai_mulligan.
    
    # One phase at a time. PreGame first — no player input; the client
    # runs scenario setup synchronously and sends no transaction.
    # Apply PreGame-triggered champion abilities (Shard Attuned health)
    # for BOTH players before they pass priority in PreGame.
    pl_guids = [str(a.guid) for a in getattr(handler, "_player_champ_abilities", [])]
    if pl_guids:
        try:
            # Native dispatch handles authored trigger/BOM effects.
            # Apply the remaining metadata-only modifiers (such as
            # Fury's -7 health/+2 charges, whose setup effect is not
            # reliably materialized by native discovery) without
            # duplicating native effects.
            from rules_port.pregame import apply_pregame_abilities
            apply_pregame_abilities(
                game, session, db._db, handler, pl_uid_t,
                handler.user_profile["id"], pl_guids,
                "player_health", metadata_only=True)
            log_req("    Native PreGame: applied non-trigger champion modifiers")
        except Exception as e:
            log_req(f"    Player PreGame ability error: {e}")
    ai_guids = [ai_charge_power] if ai_charge_power else []
    if ai_guids:
        try:
            from rules_port.pregame import apply_pregame_abilities
            apply_pregame_abilities(
                game, session, db._db, handler, ai_uid_t, 0,
                ai_guids, "ai_health", metadata_only=True)
            log_req("    Native PreGame: applied non-trigger AI modifiers")
        except Exception as e:
            log_req(f"    AI PreGame ability error: {e}")
    # Carry PreGame charge modifiers into the DB-backed state created
    # after Mulligan, just as we already carry starting health.
    handler._player_starting_charges = getattr(game, "player_charges", 0)
    handler._ai_starting_charges = getattr(game, "ai_charges", 0)
    # PreGame resources and thresholds (a champion's "gain 3 resources, 3 Ruby
    # threshold") live in the PreGame state only; the battle state seeded after
    # mulligan reads them from here, or the first resource play starts at 1.
    handler._pregame_resource_seed = {
        key: pregame_bstate[key] for key in (
            "player_resources", "player_total_resources", "player_threshold",
            "ai_resources", "ai_total_resources", "ai_threshold")
        if key in pregame_bstate}
    # Persist the PreGame-boosted health so the DB battle state is
    # seeded from the ACTUAL value, not the un-boosted starting health.
    # Shard Attuned bumps game.player_health during PreGame; bstate is
    # seeded later from _player_starting_health, so without this every
    # phase push reports the un-boosted value (the 17->20 flicker at
    # turn start). game.player_health/ai_health are the boosted values.
    handler._player_starting_health = game.player_health
    handler._ai_starting_health = game.ai_health
    log_req(f"    PreGame: player health {game.player_health}, AI health {game.ai_health}")
    game.push_turn_phase(game_engine.ETurnPhases.PreGame)
    if campaign_encounter_conversation_guid:
        event = game._make_event(
            game_engine.EncounterModDialogSessionEventArgs)
        event.player_id = pl_uid_t
        event.conversation_template_id = game_engine.ResourceId.from_str(
            campaign_encounter_conversation_guid)
        game._push(event)
        log_req("    Campaign encounter conversation queued internally: "
            f"{campaign_encounter_conversation_guid[:8]}")
    if fra_mod_setup:
        # A challenge can carry dialogue without one of the supported
        # round-zero mod classes.  Queue the saved challenge
        # conversations themselves as well as any mod-level ids, so
        # starting a fresh encounter always presents its assigned
        # challenge.
        conversation_candidates = [
            (fra_mod_setup.get("challenge") or {}).get(
                "conversation_guid")]
        conversation_candidates.extend(
            item.get("conversation_guid")
            for item in fra_mod_setup.get("active_challenges") or ()
            if isinstance(item, dict))
        conversation_candidates.extend(
            item.get("conversation_id")
            for item in fra_mod_setup.get("modifications") or ()
            if isinstance(item, dict))
        conversation_ids = list(dict.fromkeys(
            str(value).lower() for value in conversation_candidates
            if value))
        for conversation_id in conversation_ids:
            event = game._make_event(
                game_engine.EncounterModDialogSessionEventArgs)
            event.player_id = pl_uid_t
            event.conversation_template_id = (
                game_engine.ResourceId.from_str(conversation_id))
            game._push(event)
        if conversation_ids:
            log_req("    FRA challenge conversation queued internally: "
                + ",".join(value[:8] for value in conversation_ids))
    pkt = game.make_network_packet(pl_uid_t)
    evt_bytes = compress_gzip(encode_sync_event(pkt))
    evt_dw = encode_datawrapper(0, 3055, evt_bytes, 1, "00000000-0000-0000-0000-000000000000")
    handler.scnt += 1
    handler._game_scnt = handler.scnt  # Save game scnt for subsequent events
    handler.send({"issuer": f"0.0.0.0.ServiceGameSession.{SERVICE_GAME_SESSION_UID}.{session.session_id}.{handler.scnt}", "target": "ServiceGameSession", "instance": gs_instance, "reqid": 0, "c": 0, "conh": 0, "sid": handler.sid}, evt_dw)
    log_req(f"    Pushed game init (decks, no hands yet) + PreGame ({len(evt_dw)}b)")

    if coin_winner_is_player:
        if getattr(handler, "_pve_player_cannot_choose_play_first", False):
            # The stock client cannot hide just the Play button.  Do
            # not expose an invalid choice; resolve the toss winner's
            # only legal result directly through the normal Draw path.
            from types import SimpleNamespace
            handler._pve_forced_draw_first = True
            forced_draw = SimpleNamespace(
                inner_bytes=b"ChooseDrawTransaction",
                is_set_ability_data=False,
                is_ability_activate=False,
                ai_choice=False)
            log_req("    Weight: player won toss; forcing Draw first")
            handler._handle_choose_pick_transaction(session, forced_draw)
        else:
            # The human won the toss: show the normal Play/Draw dialog
            # and wait for its ChoosePlay/ChooseDraw transaction.
            game2 = game_engine.Game(session.session_id, pl_uid_t, ai_uid_t)
            game2.push_turn_phase(game_engine.ETurnPhases.PickGoesFirst, pl_uid_t, pl_uid_t)
            game2.push_green_light(pl_uid_t, game_engine.EPriorityContext.Normal)
            handler._sync_rules_port_setup_checkpoint(
                session, game_engine.ETurnPhases.PickGoesFirst, pl_uid_t)
            pkt2 = game2.make_network_packet(pl_uid_t)
            evt_bytes2 = compress_gzip(encode_sync_event(pkt2))
            evt_dw2 = encode_datawrapper(0, 3055, evt_bytes2, 1, "00000000-0000-0000-0000-000000000000")
            handler.scnt += 1
            handler._game_scnt = handler.scnt  # Save game scnt for subsequent events
            handler.send({"issuer": f"0.0.0.0.ServiceGameSession.{SERVICE_GAME_SESSION_UID}.{session.session_id}.{handler.scnt}", "target": "ServiceGameSession", "instance": gs_instance, "reqid": 0, "c": 0, "conh": 0, "sid": handler.sid}, evt_dw2)
            log_req(f"    Pushed PickGoesFirst phase ({len(evt_dw2)}b)")
    else:
        # The AI won the toss. It plays first 90% of the time and
        # draws first 10%; resolve the same server path as a player
        # choice so hands, client messaging, and pending turn state
        # stay identical.
        from types import SimpleNamespace
        ai_plays_first = random.random() < 0.90
        ai_pick = SimpleNamespace(
            inner_bytes=(b"ChoosePlayTransaction"
                         if ai_plays_first else b"ChooseDrawTransaction"),
            is_set_ability_data=False,
            is_ability_activate=False,
            ai_choice=True)
        log_req("    FRA AI won coin toss: chose %s first (90/10)" %
            ("Play" if ai_plays_first else "Draw"))
        handler._handle_choose_pick_transaction(session, ai_pick)






LEGACY_HANDLERS = {
    22013: handle_tryreconnectiontodisconnectedgame_22013,
    22031: handle_readytostartgame_22031,
}
