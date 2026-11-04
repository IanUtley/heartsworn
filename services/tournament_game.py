"""Tournament PvP game setup — push initial battle events to both players."""

import json, re, struct
from types import SimpleNamespace
from typing import Any

import game_engine as _ge
from services.pvp_watchdog import pvp_start_priority_watchdog
from services.pvp_state import (
    pvp_session_lock, pvp_discard_session_lock, pvp_shared_port, set_pvp_shared_port, pvp_discard_shared_port, _pvp_locked, pvp_load_state, pvp_save_state)
from services.pvp_priority import flush_priority_clock, priority_elapsed_ticks
from rules_port.pvp_lifecycle import (phase_is_stop as port_phase_is_stop,
                                      player_auto_passes as port_player_auto_passes,
                                      phase_after_blockers as port_phase_after_blockers,
                                      phase_transition as port_phase_transition,
                                      enter_phase as port_enter_phase,
                                      record_phase_pass as port_record_phase_pass,
                                      set_priority as port_set_priority,
                                      reset_priority_interval as port_reset_priority_interval,
                                      waiting_player_requires_priority as port_waiting_player_requires_priority,
                                      stack_pass_transition as port_stack_pass_transition,
                                      advance_turn_state as port_advance_turn_state,
                                      queue_stack_item as port_queue_stack_item,
                                      default_pvp_state as port_default_pvp_state,
                                      mulligan_transition as port_mulligan_transition,
                                      skips_draw_phase as port_skips_draw_phase,
                                      turn_phase_list as port_turn_phase_list)
from gamedata import DEFAULT_RECORD_STORE, ability_graph, PlayPlan
from services.pvp_core import (
    _ability_has_effects_from,
    _objfmt_uid_field,
    _pvp_ability_cost_targets,
    _pvp_add_play_target_options,
    _pvp_apply_visibility,
    _pvp_card_playable,
    _pvp_card_source_location,
    _pvp_chain_active,
    _pvp_defer_trigger_event,
    _pvp_discard_prompt_data,
    _pvp_discard_prompt_pid,
    _pvp_dispatch_triggers,
    _pvp_emit_paid_cost_events,
    _pvp_fra_view,
    _pvp_log_stack,
    _pvp_push_reconnect_underground,
    _pvp_push_turn_phase_with_elapsed,
    _pvp_raw_player_id,
    _pvp_resolve_ability,
    _pvp_resource_charge_points,
    _pvp_select_card_play_costs,
    _pvp_session_pids,
    _pvp_set_handler_champions,
    _pvp_state_thresholds,
    _pvp_sync_view_to_state,
    _pvp_thresholds_met,
    _pvp_transaction_card_uids,
    _pvp_turn_phase_list,
    _pvp_typed_card_targets,
    _send_pvp_packet,
    _session_has_reconnect_player,
    _take_pending_gain_charge_amount,
    _uid_int,
    pvp_combat_has_swiftstrike,
    pvp_default_state,
    pvp_phase_is_stop,
    pvp_player_auto_passes,
    pvp_turn_has_attackers,
)
import services.pvp_core as _pvp_core
from services.pvp_core import (
    _pvp_advance_from_damage_step,
    _pvp_advance_past_declare_defense,
    pvp_advance_to_declare_defense,
    pvp_skip_to_second_main,
)
from services.pvp_core import (
    _pvp_resolve_optional_trigger,
    _pvp_run_phase_start,
    handle_ready_to_continue_game,
    notify_pvp_player_disconnected,
)
from services.pvp_core import (
    _pvp_push_reconnect_snapshot,
    _pvp_reassign_priority_after_disconnect,
    _pvp_resolve_conversation,
    _pvp_resolve_discard_prompt,
    _pvp_resolve_trigger_target,
    _wire_pvp_state_based_handler,
    pvp_debug_draw,
    pvp_push_current_phase_options,
)
from services.pvp_core import (
    _PvpChainHost,
    _pvp_offer_opponent_response,
    _pvp_resolve_combat,
    handle_ready_for_game_events,
    pvp_push_attack_options,
    pvp_push_main_phase_options,
)
from services.pvp_core import (
    _pvp_apply_card_play_costs,
    _pvp_check_game_end,
    _pvp_gain_charge_trigger_game,
    _pvp_gain_threshold_trigger_game,
    _pvp_push_mulligan_prompt,
    _pvp_resolve_deck_search,
    _pvp_resolve_granted_resource_abilities,
    _pvp_run_draw,
    _pvp_sync_game_state,
    handle_join_disconnected_game,
    push_pvp_game_start,
    pvp_concede,
    pvp_push_blocker_options,
    pvp_push_empty_blockers,
    pvp_push_phase_options,
    pvp_push_warzone_updates,
)
from services.pvp_core import (
    _push_to_both_players,
    _pvp_add_champion_options,
    _pvp_add_hand_card_updates,
    _pvp_add_troop_ability_options,
    _pvp_affordable_troop_abilities,
    _pvp_defender_blockable_count,
    _pvp_drive_f10_priority,
    _pvp_end_game,
    _pvp_populate_game_state,
    _pvp_push_discard_prompt,
    _pvp_push_waiting_on,
    _pvp_ready_step,
    _pvp_send_same_events,
    _pvp_typed_picker_uid,
    _raw_player_id,
    campaign_session_reconnectable,
    fra_session_reconnectable,
    handle_ready_for_game_setup,
    pvp_phase_after_blockers,
)
from application.player_transactions import extract_ability_guid  # noqa: F401  (re-exported for tests)
from db import _db, log_req
from pvp_db import (
    db_game_session_pids,
    db_game_champion,
    db_card_owner_id,
    db_game_cards_at_location,
    db_game_deck_cards,
    db_game_draw_cards,
    db_game_card_type,
    db_card_instance_dynamic_projections,
    db_game_shuffle_deck,
    db_champion_template_health,
    db_discard_card,
    db_delete_game_session,
    db_card_ability_list,
    db_ability_effect_type_params,
    db_card_state_value,
    db_card_set_attacking_state,
    db_bulk_blocker_state,
    db_is_champion_template,
    db_hand_cards_with_templates,
    db_card_template_attrs_joined,
    db_template_by_guid,
    db_set_card_location,
    db_set_card_state_or,
    db_update_card_state,
    db_card_location,
    db_card_basic,
    db_warzone_troops_with_state,
    db_warzone_attack_option_rows,
    db_warzone_blocker_uids,
    db_game_get_hand,
    db_card_attribute_rows,
    db_card_uids_in_zone,
    db_ability_option_cards,
    db_card_ability_payload,
    db_template_ability_payload,
    db_card_zone_details,
    db_hand_exists,
    db_hand_card_for_discard,
    db_deck_top_card,
    db_hand_count,
    db_card_play_info,
    db_warzone_display_rows,
    db_template_name,
    db_target_template_info,
    db_card_zone_projection,
    db_card_owner_zone_state,
    db_champion_ability_guids,
    db_champion_ability_costs,
    db_champion_ability_thresholds,
)
from tournament_db import (db_tournament_by_id,
                           db_tournament_player_name_for_session)
from encoder import encode_datawrapper, encode_sync_event, compress_gzip, encode_objfmt_response, client_session_guid
from gamemodes.tournament_engine import (
    player_handlers, player_handler_lock, record_tournament_game_result,
    tournament_id_from_session_name,
)
from domain.constants import DEFAULT_MAX_HAND_SIZE

# Session id -> ready count for tournament PvP setup, shared by ReadyForGameSetup.
PVP_READY = {}
# Session id -> ready count for tournament PvP events (ReadyForGameEvents).
PVP_EVENTS_READY = {}


_ECardCollections = _ge.ECardCollections
_ECardTypes = _ge.ECardTypes
_RECORD_STORE = DEFAULT_RECORD_STORE




_pvp_core.bind_namespace(__import__('sys').modules[__name__])


















# ── PvP state persistence (session.turn_order / turn_order_json) ────────────
# Mirrors the rules_port.lifecycle load_state / save_state pattern with a PvP-
# specific schema (two human players, no AI).  State lives in the DB so a
# reconnect can resume.



def pvp_mulligan_next(session, state, just_acted_pid):
    """After a keep, pass the active Mulligan phase to the other player.

    Redraws retain priority and do not call this function. Once both players
    have kept, the mulligan ends and RulesPort advances into the first turn.
    Returns True when the other player was prompted, False when mulligan ended.
    """
    pids = db_game_session_pids(session.session_id)
    if len(pids) < 2:
        return False
    mulligan = port_mulligan_transition(state, pids, just_acted_pid)
    if mulligan["action"] == "start_turn":
        # Both players kept.  Keep the native session at Mulligan until it
        # performs the legal Mulligan -> StartGame -> StartTurn transition;
        # the RulesPort phase scheduler owns the remainder of the first turn.
        # A service-side phase loop here can get out of sync with the native
        # priority action (the original source of second-main/discard tennis).
        state["phase"] = int(_ge.ETurnPhases.Mulligan)
        state.pop("priority_pid", None)
        state["passes"] = []
        state.pop("mulligan_pid", None)
        pvp_save_state(session, state)
        native_handler = player_handlers.get(int(pids[0]))
        if native_handler is None:
            log_req("    PvP mulligan complete but no player handler is "
                    "available to attach RulesPort")
            return False
        turn_uid = _ge.UID.make(244, int(state["turn_pid"]))
        opponent_pid = next(int(pid) for pid in pids
                            if int(pid) != int(state["turn_pid"]))
        native_game = _ge.Game(
            int(session.session_id), turn_uid,
            _ge.UID.make(244, opponent_pid))
        port = attach_pvp_rules_port(
            native_handler, session, native_game, state)
        if port is None:
            log_req("    PvP mulligan complete but RulesPort attachment failed")
            return False
        port.begin_pvp_turn()
        live = pvp_load_state(session) or state
        log_req("    PvP mulligan complete — native scheduler reached "
                f"phase {live.get('phase')} for turn player "
                f"{live.get('turn_pid')}")
        # Start the server-side priority watchdog for clock flushing and
        # inactivity expiry. It does not send periodic client events.
        pvp_start_priority_watchdog(session)
        # No dialog is open now; clear any "opponent is mulliganing" state.
        _pvp_push_waiting_on(session, None)
        # The sequential mulligan prompt disabled every client that was not the
        # active mulliganer (_pvp_push_mulligan_prompt ->
        # push_disable_interface).  Nothing else re-enables them, so the player
        # who kept first is left with m_DisabledInput=true and every
        # button-driven action (charge power, Pass) is silently dropped by
        # UIBattle.HandleInputs until the inactivity timeout.  The first turn
        # is live now, so re-enable both clients.
        for pid in pids:
            h = player_handlers.get(int(pid))
            if not h:
                continue
            pt = _ge.UID.make(244, int(pid))
            opp = _ge.UID.make(
                244, int(pids[0]) if int(pid) == int(pids[1]) else int(pids[1]))
            enable = _ge.Game(int(session.session_id), pt, opp)
            enable.push_disable_interface(False)
            _send_pvp_packet(h, session, enable, pt,
                             "mulligan-end-enable-input")
        return False
    # Only one (or neither) has kept.  Ask the other player if they haven't
    # kept yet; otherwise (the other player already kept) re-ask the player
    # who just acted (they must keep or redraw again, one fewer card).
    next_pid = mulligan["next_player"]
    state["mulligan_pid"] = next_pid
    pvp_save_state(session, state)
    _pvp_push_mulligan_prompt(session, state, next_pid)
    return True




def project_accepted_pvp_transaction(handler, session, kind, transaction,
                                     *, port=None):
    """Apply the PvP wire/database projection for an accepted intent.

    RulesPort is the sole owner of transaction classification, legality,
    costs, and ordering.  This function is deliberately the only compatibility
    boundary used by the live PvP adapter; it receives a typed, already
    accepted intent and exists only to emit the historical PvP event stream.

    The implementation is kept separate from attachment so the remaining
    legacy projection can be replaced one transaction family at a time
    without reintroducing a second ingress or validation path.
    """
    payload = getattr(transaction, "payload", {}) or {}
    raw = getattr(transaction, "raw_transaction", None)
    if raw is None:
        raw = getattr(getattr(session, "_rules_port_dispatch_command", None),
                      "inner_bytes", b"")
    current = getattr(session, "_rules_port_dispatch_handler", None) or handler
    try:
        my_pid = int(current.client_reck_id)
    except (AttributeError, TypeError, ValueError):
        return False

    # Continuations are already represented by explicit typed RulesPort
    # transaction kinds. Route them directly to their mode projection instead
    # of sending them back through the old all-purpose transaction parser.
    # This is especially important after reconnect, where the raw envelope
    # may not contain the original ability class name.
    if kind == "conversation":
        return bool(_pvp_resolve_conversation(current, session, raw, my_pid))
    if kind == "choice":
        if (pvp_load_state(session) or {}).get("pending_choice"):
            # The native RulesPort continuation carries the selected target in
            # the typed payload (``activation_data.target_map``).  The legacy
            # ``raw`` envelope is empty on this path, so pass the payload
            # through; ``_pvp_resolve_choice`` reads the typed target when the
            # raw scan finds nothing.
            return bool(_pvp_resolve_choice(
                current, session, raw, my_pid, typed_payload=payload))
        return True
    if kind == "discard":
        live = pvp_load_state(session) or {}
        if live.get("pending_discard_ability"):
            return bool(_pvp_resolve_discard_prompt(
                current, session, raw, my_pid, typed_payload=payload))
        try:
            card_uid = _uid_int(payload.get("card_id"))
        except (TypeError, ValueError):
            return False
        pids = tuple(int(pid) for pid in db_game_session_pids(
            session.session_id))
        if my_pid not in pids:
            return False
        opponent = next((pid for pid in pids if pid != my_pid), my_pid)
        from rules_port.host_mutations import discard_card_to_owner
        projected, _owner = discard_card_to_owner(
            current, session, _ge.UID.make(244, my_pid),
            _ge.UID.make(244, opponent), card_uid)
        if projected is None:
            return False
        _pvp_send_same_events(
            session, projected, _ge.UID.make(244, my_pid),
            _ge.UID.make(244, opponent))
        if int(live.get("phase", 0) or 0) == int(_ge.ETurnPhases.Discard):
            hand_count = db_hand_count(
                session.session_id, my_pid, conn=_db)
            live["discard_required"] = hand_count > DEFAULT_MAX_HAND_SIZE
            pvp_save_state(session, live)
            if live["discard_required"]:
                # The C# AI needs another GreenLight to discard again when
                # one card did not bring its hand under the limit.
                discard_uid = _ge.UID.make(244, my_pid)
                game = _ge.Game(int(session.session_id), discard_uid,
                                _ge.UID.make(244, opponent))
                game.push_green_light(discard_uid)
                _send_pvp_packet(current, session, game, discard_uid,
                                 "discard-continue")
            elif port is not None:
                # DiscardTransaction moves a card but does not itself pass
                # priority in RulesPort. Finish the native Discard window
                # once the hand is legal, then persist the next phase.
                if not port.pass_priority_and_drive(transaction.player_id):
                    log_req("    PvP discard could not consume native "
                            f"priority for pid {my_pid}")
                    return False
                live = pvp_load_state(session) or live
                port.sync_to_pvp_state(live)
                pvp_save_state(session, live)
                try:
                    port.persist()
                except Exception as exc:
                    log_req(f"    PvP RulesPort post-discard persistence "
                            f"failed: {exc}")
        return True
    if kind == "discard_continuation":
        return bool(_pvp_resolve_discard_prompt(
            current, session, raw, my_pid, typed_payload=payload))
    if kind == "triggered":
        pending = pvp_load_state(session) or {}
        if pending.get("pending_trigger"):
            if (pending.get("pending_trigger") or {}).get("optional"):
                return bool(_pvp_resolve_optional_trigger(
                    current, session, raw, my_pid, typed_payload=payload))
            return bool(_pvp_resolve_trigger_target(
                current, session, raw, my_pid, typed_payload=payload))
        search = pending.get("pending_deck_search") or {}
        search_kind = str(search.get("kind") or "")
        if search_kind in ("revealed_troop", "revealed_card"):
            return bool(_pvp_resolve_revealed_choice(
                current, session, raw, my_pid, typed_payload=payload))
        if search_kind == "shard":
            return bool(_pvp_resolve_shard_choice(
                current, session, raw, my_pid, typed_payload=payload))
        if search_kind == "matching_target":
            return bool(_pvp_resolve_matching_target(
                current, session, raw, my_pid, typed_payload=payload))
        if search:
            return bool(_pvp_resolve_deck_search(
                current, session, raw, my_pid, typed_payload=payload))
        return True
    if kind == "attack":
        # RulesPort has already validated and staged these declarations.
        # Pass the accepted attacker IDs through so the legacy event
        # projection does not re-check them against a stale PvP snapshot and
        # accidentally drop a valid attack.
        return bool(_pvp_declare_attackers(
            current, session, raw, my_pid,
            declarations=payload.get("declarations")))
    if kind == "defense":
        return bool(_pvp_declare_blockers(
            current, session, raw, my_pid,
            declarations=payload.get("declarations")))
    if kind == "ready":
        return True
    if kind == "damage":
        # AssignDamageOrder is an automatic client transaction. Its only
        # mutable payload is blocker order; combat resolution remains the
        # native phase/action-stack boundary.
        live = pvp_load_state(session) or {}
        try:
            selected = []
            for match in re.finditer(
                    rb'm_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});', raw):
                value = struct.unpack(
                    '<Q', bytes.fromhex(match.group(1).decode()))[0]
                if (value & 0xFF) == 1:
                    selected.append(int(value))
            blockers = {int(key): {int(value) for value in values}
                        for key, values in
                        (live.get("blockers") or {}).items()}
            order_map = {}
            for attacker, blocker_set in blockers.items():
                ordered = [uid for uid in selected if uid in blocker_set]
                ordered.extend(uid for uid in blocker_set if uid not in ordered)
                if ordered:
                    order_map[attacker] = ordered
            if order_map:
                live["damage_order"] = {
                    str(key): [str(value) for value in values]
                    for key, values in order_map.items()}
            phase = int(live.get("phase", 0) or 0)
            if phase in (_ge.ETurnPhases.AssignFirstStrikeDamage,
                         _ge.ETurnPhases.AssignDamage):
                _pvp_resolve_combat(
                    session, live,
                    first_strike=(phase == _ge.ETurnPhases.AssignFirstStrikeDamage))
                # AssignDamageOrder is the client's automatic pass through
                # this phase. Resolve the projected combat effects, then let
                # the native PriorityWindowAction and phase graph consume that
                # pass. Manually advancing only the PvP checkpoint leaves the
                # RulesPort cursor behind (for example native phase 16 while
                # turn_order says 18); the next request reattaches the native
                # phase and resolves the same damage step again.
                if str(getattr(session, "state", "") or "").lower() != "ended":
                    if port is not None:
                        player_id = getattr(transaction, "player_id", None)
                        if not port.pass_priority_and_drive(player_id):
                            log_req(
                                "    PvP AssignDamageOrder could not consume "
                                f"native priority: phase={phase} player={player_id!r}")
                            return False
                        live = pvp_load_state(session) or live
                        port.sync_to_pvp_state(live)
                        pvp_save_state(session, live)
                        try:
                            port.persist()
                        except Exception as exc:
                            log_req(
                                "    PvP RulesPort post-damage persistence "
                                f"failed: {exc}")
                    else:
                        # Compatibility for callers that have not attached a
                        # native PvP scheduler.
                        _pvp_advance_from_damage_step(session, live, phase)
            pvp_save_state(session, live)
            return True
        except (TypeError, ValueError, struct.error) as exc:
            log_req(f"    PvP AssignDamageOrder projection error: {exc}")
            return False
    if kind == "play_resource":
        try:
            card_uid = _uid_int(payload.get("card_id"))
        except (TypeError, ValueError):
            return False
        pids = db_game_session_pids(session.session_id)
        if len(pids) < 2 or my_pid not in [int(pid) for pid in pids]:
            return False
        crow = db_card_play_info(session.session_id, card_uid, conn=_db)
        if not crow or str(crow[1] or "") != "Resource":
            return False
        opponent = next(int(pid) for pid in pids if int(pid) != my_pid)
        return bool(_pvp_project_resource_play(
            current, session, raw, my_pid, card_uid, crow, crow[2],
            tuple(int(pid) for pid in pids), _ge.UID.make(244, my_pid),
            _ge.UID.make(244, opponent)))
    card_uid = payload.get("card_id")
    if kind in {"play_troop", "play_artifact", "play_spell",
                "play_champion"} and card_uid is not None:
        try:
            card_uid = int(getattr(card_uid, "uid64", card_uid))
            row = db_card_play_info(session.session_id, card_uid, conn=_db)
            if row:
                card_type = str(row[1] or "")
                if kind == "play_spell" or any(
                        name in card_type for name in ("BasicAction", "QuickAction")):
                    return bool(_pvp_play_spell(
                        current, session, card_uid, int(current.client_reck_id),
                        raw, typed_payload=payload, native_port=port))
                return bool(_pvp_play_troop(
                    current, session, card_uid, int(current.client_reck_id), raw,
                    typed_payload=payload, native_port=port))
        except (TypeError, ValueError):
            return False
    # Every transaction kind wired by attach_pvp_rules_port must be handled
    # above. Unknown kinds are rejected here rather than being reinterpreted
    # by the legacy all-purpose dispatcher.
    log_req(f"    PvP RulesPort projection: unsupported kind={kind}")
    return False




def attach_pvp_rules_port(handler, session, game, state):
    """Attach the generic RulesPort scheduler to a two-human PvP session.

    The tournament service remains a projection adapter for the established
    PvP wire/database shape.  Transaction classification, requirements, and
    scheduler ordering are supplied by ``PvpAuthoritativeSession``; the
    adapter callbacks below only apply accepted mutations and publish events.
    """
    if not session:
        return None
    # The caller may have obtained ``state`` from the generic RulesPort
    # snapshot while rebuilding a fresh Game.  That snapshot is scheduler
    # metadata and can lag the tournament turn_order checkpoint (notably
    # during the Mulligan -> FirstMain transition).  PvP's turn_order is the
    # authoritative phase/priority projection; never let a stale generic
    # snapshot rehydrate the native port or reject the first shard/card play.
    authoritative = pvp_load_state(session)
    if isinstance(authoritative, dict) and authoritative.get("pvp"):
        state = authoritative
    if not isinstance(state, dict) or not state.get("pvp"):
        return None
    cached = (pvp_shared_port(session) or
              getattr(session, "_rules_port_session", None))
    if cached is not None:
        # Point this request wrapper at the shared port FIRST so
        # ``pvp_load_state`` resolves the one authoritative checkpoint dict.
        session._rules_port_session = cached
        set_pvp_shared_port(session, cached)
        # A reconnect can materialize a fresh turn_order dictionary while the
        # native PvP session object remains cached. Refresh both its phase /
        # priority view and runtime facts before accepting another request;
        # otherwise validation can use the old participant checkpoint.
        live_state = pvp_load_state(session) or state
        live_state["_rules_port_attached"] = True
        session._rules_port_battle_state = live_state
        cached._pvp_state = live_state
        # Record the request-scoped dispatch identity: the port's projections
        # must attribute cost/resource/threshold changes to THIS handler, not
        # the handler that happened to create the shared port.
        cached._pvp_current_handler = handler
        cached._pvp_current_session = session
        # The shared port is the single live scheduler for this game, so its
        # in-memory phase/priority are authoritative.  A request-scoped
        # wrapper can have loaded ``turn_order`` before the other connection
        # advanced the phase (e.g. the mulligan completion's first-turn
        # drive); syncing the port FROM that stale checkpoint rolled it back
        # to Ready/Prep and rejected the first shard/card play.  Project the
        # port OUT to the checkpoint instead.
        sync_out = getattr(cached, "sync_to_pvp_state", None)
        if callable(sync_out):
            sync_out(live_state)
        sink = getattr(cached, "event_sink", None)
        if sink is not None:
            sink.game = game
        _wire_pvp_state_based_handler(cached, handler, session, game)
        facts = getattr(cached, "runtime_facts", None)
        if facts is not None:
            facts.battle_state = live_state
            facts.client_player_uid = game.player_uid
            try:
                facts.player_owner_id = int(handler.client_reck_id)
                facts.ai_owner_id = int(next(
                    pid for pid in db_game_session_pids(session.session_id)
                    if int(pid) != int(handler.client_reck_id)))
            except (AttributeError, StopIteration, TypeError, ValueError):
                pass
        # Keep the snapshot store pointed at the current wrapper, or persist()
        # would write through a stale request-scoped session object.
        snapshot = getattr(cached, "snapshot_store", None)
        if snapshot is not None:
            snapshot.game_session = session
        return cached
    from rules_port import (GameEngineEventSink, PvpAuthoritativeSession,
                            SQLiteRulesSnapshot, attach_pvp_runtime_facts)
    pids = db_game_session_pids(session.session_id)
    if len(pids) < 2:
        # The participant rows are written with the opening hands, so the
        # first gameplay transaction of a resumed match can arrive before they
        # exist.  A tourney game always runs on the native host: fall back to
        # the checkpoint's own participant list instead of dropping the
        # session onto the legacy stack.
        pids = _pvp_session_pids(session, state)
    if len(pids) < 2:
        return None
    # ``session.players`` is legacy transport metadata and may contain the
    # stale packed IDs originally sent by the client.  The game-card/session
    # participant rows are authoritative for live PvP RulesPort ownership.
    player_ids = tuple(_ge.UID.make(244, pid) for pid in pids[:2])
    state["_rules_port_attached"] = True
    session._rules_port_battle_state = state
    port = PvpAuthoritativeSession(
        session.session_id, player_ids,
        seed_z=int(getattr(session, "seed_z", 22222)),
        seed_w=int(getattr(session, "seed_w", 11111)),
        event_sink=GameEngineEventSink(game),
        snapshot=SQLiteRulesSnapshot(session))
    # Rehydrate port-owned scheduler/combat descriptors before wiring the
    # mode projections; the PvP checkpoint remains authoritative for the
    # current phase and priority values.
    port.restore_snapshot(port.snapshot_store.load())
    port.sync_from_pvp_state(state)
    # Resource plays leave the player in the same main phase.  The legacy PvP
    # projection sends GreenLight directly, but without a native priority
    # action the following PassPriority is rejected and the client can apply
    # a stale/default PlayerUpdated while trying to advance the phase.
    # Reconcile an empty main-phase stack from the durable checkpoint; existing
    # chain/priority actions restored above remain untouched.
    priority_uid = port._uid_for_raw_player(state.get("priority_pid"))
    active_uid = port._uid_for_raw_player(state.get("turn_pid"))
    if priority_uid is not None and active_uid is not None:
        port.sync_checkpoint(
            phases=[port.current_turn_phase], phase_idx=0,
            active_player_id=active_uid, client_player_id=priority_uid,
            # Rebuild a native window for every interactive phase after
            # reconnect. ``restore_snapshot`` intentionally restores action
            # descriptors without fabricating action objects; limiting this
            # to First/Second Main left DeclareAttackPriorityWindow (phase
            # 11) with a durable priority owner but no live action.
            ensure_main_priority=True,
            ensure_current_priority=True)
    facts = attach_pvp_runtime_facts(
        port, session, state, player_uid=game.player_uid,
        ai_uid=game.ai_uid)
    facts.client_player_uid = game.player_uid
    # PvP card owners are raw participant ids, unlike Practice's profile/AI
    # pair. The runtime adapter derives these from the typed request.
    facts.player_owner_id = int(handler.client_reck_id)
    facts.ai_owner_id = int(next(pid for pid in pids
                                 if int(pid) != int(handler.client_reck_id)))

    def current_handler():
        # The port is shared by both connections, so the request-scoped
        # handler captured at creation is stale for the other player.  Use the
        # handler recorded for the transaction currently being projected.
        return (getattr(port, "_pvp_current_handler", None)
                or getattr(session, "_rules_port_dispatch_handler", None)
                or handler)

    def raw_transaction():
        command = getattr(session, "_rules_port_dispatch_command", None)
        return getattr(command, "inner_bytes", b"")

    def pvp_projection(_kind, _transaction):
        # All RulesPort-accepted PvP intents cross one named projection seam.
        # The frozen RulesTransaction is intentionally not mutated. The raw
        # command remains available only through the session dispatch context
        # for the historical event projection.
        return project_accepted_pvp_transaction(
            current_handler(), session, _kind, _transaction, port=port)

    def native_activation_resolver(ability):
        """Resolve simple manual abilities through the native port lifecycle.

        The PvP checkpoint uses raw participant ids, while the RulesPort
        transaction uses typed ServicePlayer ids.  Keep that translation at
        this mode boundary and let the shared native effect dispatcher own the
        actual BOM traversal.
        """
        from rules_port.resolution import resolve_port_ability
        owner_id = int(getattr(ability, "metadata", ability).owner_id)
        other_id = next((int(pid) for pid in pids if int(pid) != owner_id),
                        owner_id)
        live = pvp_load_state(session) or {}
        view = _pvp_fra_view(live, owner_id, other_id)
        player_uid = _ge.UID.make(244, owner_id)
        opponent_uid = _ge.UID.make(244, other_id)
        game = _ge.Game(int(session.session_id), player_uid, opponent_uid)
        _pvp_populate_game_state(game, live, owner_id, other_id)
        cost_selections = live.pop("_rules_port_pvp_cost_selections", ())
        if cost_selections:
            _pvp_apply_card_play_costs(
                current_handler(), game, session, live, player_uid,
                opponent_uid, cost_selections, int(ability.source_uid))
        exhausted = live.pop("_rules_port_pvp_exhausted", ())
        if exhausted:
            source_uid = int(ability.source_uid)
            row = db_card_basic(session.session_id, source_uid, conn=_db)
            if row:
                scid = _ge.SessionCardId(_ge.UID(source_uid))
                _tpl, ctype, _name, cost, attack, defense, gems = \
                    current_handler()._card_full_data(game, scid, row[0])
                game.push_card_updated(
                    scid, player_uid, _ge.ECardCollections.Warzone, ctype,
                    template_id=_tpl, state=int(db_card_state_value(
                        session.session_id, source_uid, conn=_db) or
                        _ge.ECardStates.Tapped), cost=cost, attack=attack,
                    defense=defense, gems=gems)
        # Activation prompts and native effect events must share the same
        # recipient-aware PvP projection used by the rest of the packet path.
        event_sink = port.event_sink
        if event_sink is not None:
            event_sink.game = game
        result = resolve_port_ability(
            current_handler(), game, session, _db, player_uid, opponent_uid,
            view, ability.ability_template_id, int(ability.source_uid),
            owner_id,
            target_map=dict(getattr(ability.activation, "target_map", {}) or {}),
            variables=dict(getattr(ability.activation, "variables", {}) or {}),
            cost_target_map=dict(
                getattr(ability.activation, "cost_target_map", {}) or {}),
            instance_id=int(ability.instance_id))
        live["stack"] = view.get("stack") or []
        live["stack_passed"] = []
        _pvp_sync_view_to_state(live, view, owner_id, other_id)
        pvp_save_state(session, live)
        _pvp_send_same_events(session, game, player_uid, opponent_uid)
        session._rules_port_mutation_emitted = True
        return result

    def native_chain_resolver(ability):
        from rules_port.pvp_session import ProjectedChainAbility
        if not isinstance(ability, ProjectedChainAbility):
            return native_activation_resolver(ability)
        state = pvp_load_state(session) or {}
        descriptor = dict(ability.descriptor)
        # PvpAuthoritativeSession stores projected-chain owners as typed
        # ServicePlayer UIDs in memory, while reconnect descriptors contain
        # raw player ids.  Normalize both representations before rebuilding
        # a typed UID; shifting an already-typed UID overflows uint64 during
        # CardUpdated packet serialization.
        owner_id = _raw_player_id(ability.owner_id)
        if int(state.get("completed_chain_instance_id", 0) or 0) == int(
                ability.instance_id):
            # Manual/champion abilities retain PvP-specific cost and health
            # projections while they resolve. Once a picker has resumed the
            # complete BOM, use the shared lifecycle only for its finishing
            # chain events; running the PvP resolver again would draw again.
            from rules_port.actions import AbilityResolutionState
            from rules_port.chain_items import resolve_chain_item
            pids = db_game_session_pids(session.session_id)
            if len(pids) < 2:
                pids = _pvp_session_pids(session, state)
            if len(pids) < 2:
                return AbilityResolutionState.COMPLETED
            opponent_id = next(pid for pid in pids
                               if int(pid) != int(owner_id))
            host = _PvpChainHost(
                current_handler(), session, state, owner_id, opponent_id)
            resolution = resolve_chain_item(
                host, port, session, _db, ability,
                _ge.UID.make(244, int(owner_id)),
                _ge.UID.make(244, int(opponent_id)))
            pvp_save_state(session, state)
            session._rules_port_mutation_emitted = True
            return resolution
        # The PvP host projection consumes the same persisted descriptor; only
        # its chain ownership has moved to the native action stack.
        stack = state.setdefault("stack", [])
        if not stack or int(stack[-1].get("instance_id", -1)) != int(
                descriptor.get("instance_id", -2)):
            stack.append(descriptor)
        if descriptor.get("kind") == "ability":
            # Manual/champion abilities carry PvP-only projections (charge /
            # spell-power HUD events before the picker opens, and the
            # champion-health delta after it resolves).  Those stay in the PvP
            # projection; the chain-item lifecycle around them is the shared
            # one.
            _pvp_resolve_ability_item(
                session, state, current_handler(), owner_id, descriptor)
        else:
            # Troop/artifact/constant, spell and triggered chain items resolve
            # through the same seam as Practice/PvE
            # (``rules_port.chain_items``): one lifecycle, one picker-
            # continuation marker, one set of authored effect resolvers.  The
            # PvP host below supplies only the two-client packet projection.
            from rules_port.actions import AbilityResolutionState
            from rules_port.chain_items import resolve_chain_item
            pids = db_game_session_pids(session.session_id)
            if len(pids) < 2:
                pids = _pvp_session_pids(session, state)
            if len(pids) < 2:
                session._rules_port_mutation_emitted = True
                return AbilityResolutionState.COMPLETED
            opponent_id = next(pid for pid in pids
                               if int(pid) != int(owner_id))
            host = _PvpChainHost(current_handler(), session, state,
                                 owner_id, opponent_id)
            resolution = resolve_chain_item(
                host, port, session, _db, ability,
                _ge.UID.make(244, int(owner_id)),
                _ge.UID.make(244, int(opponent_id)))
            session._rules_port_mutation_emitted = True
            if resolution is not AbilityResolutionState.COMPLETED:
                return resolution
            state = host.state
        pvp_save_state(session, state)
        from rules_port.actions import AbilityResolutionState
        if (not bool(getattr(ability, "ignores_chain", False)) and
                any(state.get(key) for key in (
                    "pending_choice", "pending_trigger",
                    "pending_deck_search", "pending_conversation",
                    "pending_discard_ability", "resolution_paused"))):
            if str(descriptor.get("kind") or "") == "ability":
                state["paused_chain_instance_id"] = int(
                    ability.instance_id)
                pvp_save_state(session, state)
            # A real chain ability paused on an interactive prompt.  Keep the
            # chain item (do not forget the projected chain) so the client's
            # picker is not torn down; resolution resumes when the prompt is
            # answered.
            session._rules_port_mutation_emitted = True
            return AbilityResolutionState.WAITING_FOR_INPUT
        port.forget_projected_chain(int(ability.instance_id))
        # Triggers or nested effects may have placed another descriptor on
        # the persisted PvP stack. Reify its response window in RulesPort as
        # well, so the next pass cannot fall back to the legacy stack owner.
        remaining = state.get("stack") or []
        if remaining and not any(state.get(key) for key in (
                "pending_choice", "pending_deck_search", "pending_trigger",
                "pending_discard_ability")):
            pids = db_game_session_pids(session.session_id)
            if len(pids) < 2:
                pids = _pvp_session_pids(session, state)
            next_item = remaining[-1]
            next_source = int(next_item.get("source_uid") or 0)
            next_owner = owner_id
            if next_source:
                next_row = db_card_basic(
                    session.session_id, next_source, conn=_db)
                if next_row:
                    next_owner = int(next_row[1])
            next_other = next((int(pid) for pid in pids
                               if int(pid) != next_owner), next_owner)
            port.queue_projected_chain(
                next_item, next_owner,
                first_player_id=_ge.UID.make(244, next_other))
        session._rules_port_mutation_emitted = True
        return AbilityResolutionState.COMPLETED

    def native_activation_cost_payer(ability):
        """Apply the numeric portion of a simple native ability cost."""
        from rules_port.costs import (
            ability_cost_targets, plan_ability_cost,
            validate_cost_target_selection,
        )
        from rules_port.resources import pay_resource_for_player
        metadata = getattr(ability, "metadata", ability)
        ability_guid = (getattr(metadata, "ability_template_id", None) or
                        getattr(metadata, "ability_guid", None) or
                        getattr(metadata, "runtime_ability_guid", ""))
        owner_id = int(getattr(metadata, "owner_id", 0) or 0)
        state = pvp_load_state(session) or {}
        graph = getattr(metadata, "graph", None)
        cost_selections = []
        if graph is not None:
            costs_by_index = {
                int(cost.index): cost for cost in ability_cost_targets(
                    graph, _db, session.session_id, owner_id,
                    int(getattr(metadata, "source_uid", 0) or 0),
                    battle_state=state)}
            activation_data = getattr(metadata, "activation", None)
            cost_map = getattr(activation_data, "cost_target_map", {}) or {}
            for index, cost in costs_by_index.items():
                selected = cost_map.get(index, cost_map.get(str(index), ()))
                if cost.is_source_auto_target and not selected:
                    selected = (int(metadata.source_uid),)
                selected = validate_cost_target_selection(
                    _db, session.session_id, owner_id,
                    int(getattr(metadata, "source_uid", 0) or 0), cost,
                    selected, battle_state=state)
                if selected is None:
                    return False
                if selected:
                    from rules_port.costs import cost_type_for_kind
                    cost_selections.append((
                        {"kind": cost.kind, "minimum": cost.minimum,
                         "maximum": cost.maximum,
                         "cost_type": cost_type_for_kind(cost.kind),
                         "auto": cost.is_source_auto_target},
                        selected))
        costs = getattr(metadata, "costs", None)
        activation = getattr(metadata, "activation", None)
        current = int(state.get(f"res_{owner_id}", 0) or 0)
        plan = plan_ability_cost(
            costs, activation, current_resource=current,
            charges=int(state.get(f"chg_{owner_id}", 0) or 0),
            spell_points=int(state.get(f"sp_{owner_id}", 0) or 0),
            health=int(state.get(f"hp_{owner_id}", 20) or 0),
            spell_uses=state.get(f"sp_uses_{owner_id}", {}) or {},
            ability_key=str(ability_guid))
        if plan is None:
            return False
        from rules_port.costs import (
            apply_source_counter_cost_plan,
            plan_source_counter_costs,
        )
        source_uid = getattr(metadata, "source_uid", None)
        counter_cost_plan = plan_source_counter_costs(
            _db, session.session_id, source_uid, costs, state)
        if counter_cost_plan is None:
            return False
        if plan.resource:
            pay_resource_for_player(state, owner_id, int(plan.resource))
        state[f"chg_{owner_id}"] = max(
            0, int(state.get(f"chg_{owner_id}", 0) or 0) - int(plan.charge_points))
        state[f"sp_{owner_id}"] = max(
            0, int(state.get(f"sp_{owner_id}", 0) or 0) - int(plan.spell_points))
        if plan.life:
            state[f"hp_{owner_id}"] = max(
                0, int(state.get(f"hp_{owner_id}", 20) or 0) - int(plan.life))
        # The PvE payer emits the pool-change events the client HUD listens
        # for.  The native PvP payer only mutated the checkpoint, so a paid
        # charge/spell point never animated (and the charge power button stayed
        # lit).  Record the deltas and project them with the resolution events.
        if (plan.resource or plan.charge_points or plan.spell_points or
                plan.life):
            state.setdefault("_pvp_paid_costs", []).append({
                "owner_id": int(owner_id),
                "resource": int(plan.resource or 0),
                "charge": int(plan.charge_points or 0),
                "spell": int(plan.spell_points or 0),
                "life": int(plan.life or 0),
                "res_new": int(state.get(f"res_{owner_id}", 0) or 0),
                "chg_new": int(state.get(f"chg_{owner_id}", 0) or 0),
                "sp_new": int(state.get(f"sp_{owner_id}", 0) or 0),
                "hp_new": int(state.get(f"hp_{owner_id}", 20) or 0),
            })
        if bool(getattr(costs, "exhausts_card_on_use", False)):
            exhaust_source_uid = int(getattr(metadata, "source_uid", 0) or 0)
            db_set_card_state_or(session.session_id, exhaust_source_uid,
                                 _ge.ECardStates.Tapped)
            state.setdefault("_rules_port_pvp_exhausted", []).append(
                exhaust_source_uid)
        if counter_cost_plan:
            counter_changes = apply_source_counter_cost_plan(
                _db, session.session_id, int(source_uid), counter_cost_plan,
                state, ability_instance_id=getattr(
                    ability, "instance_id", None))
            if counter_changes:
                state.setdefault("_pvp_paid_counter_costs", []).extend(
                    [{"source_uid": int(change["source_uid"]),
                      "counter_guid": str(change["counter_guid"]),
                      "old": int(change["old"]),
                      "new": int(change["new"]),
                      "owner_id": int(owner_id)}
                     for change in counter_changes])
        pvp_save_state(session, state)
        if cost_selections:
            # C# pays card costs (void/sacrifice/exhaust/discard) at
            # activation, before the item reaches the chain.  The executor
            # always queues a ProjectedChainAbility, whose resolver never ran
            # the deferred selections, so Bun'jitsu's two troops stayed in
            # play and Dread Factory exhausted nothing server-side.
            other_id = next((int(pid) for pid in pids
                             if int(pid) != owner_id), owner_id)
            payer_uid = _ge.UID.make(244, owner_id)
            other_uid = _ge.UID.make(244, other_id)
            cost_game = _ge.Game(int(session.session_id), payer_uid, other_uid)
            _pvp_populate_game_state(cost_game, state, owner_id, other_id)
            _pvp_apply_card_play_costs(
                current_handler(), cost_game, session, state, payer_uid,
                other_uid, cost_selections,
                int(getattr(metadata, "source_uid", 0) or 0))
            pvp_save_state(session, state)
            if cost_game.events:
                _pvp_send_same_events(session, cost_game, payer_uid, other_uid)
        return True

    def native_card_projection(kind, transaction):
        if kind != "activate_ability":
            return pvp_projection(kind, transaction)
        # Activation is fully owned by the native MetadataCardTransaction
        # executor; this callback exists only for the non-activation kinds.
        return False

    def publish_card_activated(ability, transaction):
        sink = getattr(port, "event_sink", None)
        if sink is None:
            return False
        raw_player = getattr(transaction, "player_id", 0)
        raw_player = int(getattr(raw_player, "uid64", raw_player) or 0)
        owner_id = (raw_player >> 8 if raw_player & 0xFF == 244
                    else raw_player)
        pids = db_game_session_pids(session.session_id)
        opponent_id = next((int(pid) for pid in pids
                            if int(pid) != int(owner_id)), None)
        if opponent_id is None:
            return False
        state = pvp_load_state(session) or {}
        from rules_port.triggers import dispatch_card_activated

        def publish(**event):
            data = dict(event.pop("data", {}) or {})
            return _pvp_dispatch_triggers(
                handler, event["game"], session, state,
                event["player_uid"], event["ai_uid"],
                event["event_type"], event["source_card_id"],
                source_owner_id=event["source_player_id"],
                target_card_id=event.get("target_card_id"), **data)

        try:
            result = dispatch_card_activated(
                db=_db, handler=handler, game=sink.game, session=session,
                player_uid=sink.game.player_uid, ai_uid=sink.game.ai_uid,
                battle_state=state,
                ability_guid=getattr(ability, "ability_template_id", ""),
                source_card_id=int(getattr(ability, "source_uid", 0) or 0),
                source_player_id=int(owner_id),
                ability_instance_id=int(ability.instance_id),
                activation_data=ability.activation.as_dict(),
                dispatcher=publish)
            pvp_save_state(session, state)
            return result is not False
        except Exception as exc:
            log_req(f"    PvP CardActivatedEvent failed: {exc}")
            return False

    from rules_port.card_transactions import MetadataCardTransactionExecutor
    from gamedata import ability_graph as _ability_graph
    native_executor = MetadataCardTransactionExecutor(
        port, graph_loader=lambda guid: _ability_graph(_RECORD_STORE, str(guid).lower()),
        owner_id=0,
        owner_id_resolver=lambda tx: (
            (int(getattr(tx.player_id, "uid64", tx.player_id)) >> 8)
            if (int(getattr(tx.player_id, "uid64", tx.player_id)) & 0xFF) == 244
            else int(getattr(tx.player_id, "uid64", tx.player_id))),
        projection=native_card_projection,
        activation_event=publish_card_activated,
        play_plan_loader=lambda template_guid, source_uid, owner_id:
            PlayPlan.from_card(_RECORD_STORE, template_guid,
                               source_uid=source_uid, owner_id=owner_id))
    port.set_ability_resolver(native_chain_resolver)
    port.set_ability_cost_payer(native_activation_cost_payer)

    def pvp_pass(_transaction):
        # Native manual/triggered abilities use the RulesPort priority action
        # rather than the legacy PvP stack. Rehydrate the native response
        # action if a reconnect restored only the durable descriptor. Once a
        # PvP RulesPort host is attached, do not route a missing action through
        # route_pvp_pass: that would create a second priority/chain authority.
        from rules_port.kernel import PriorityWindowAction
        live = pvp_load_state(session) or {}
        # While an interactive prompt is open (e.g. Corinth's charge-power
        # picker) the only valid client input is the answer.  A stray pass
        # must not advance the phase out from under the picker.
        if any(live.get(key) for key in (
                "pending_choice", "pending_deck_search", "pending_trigger",
                "pending_conversation", "pending_discard_ability")):
            log_req("    PvP pass ignored while an interactive prompt is "
                    "pending")
            return True
        previous_phase = int(live.get("phase", 0) or 0)
        previous_priority = int(live.get("priority_pid", 0) or 0)
        if (port.action_stack.peek() is None and live.get("stack") and
                getattr(port, "rehydrate_projected_chain", None)):
            port.rehydrate_projected_chain()
        if isinstance(port.action_stack.peek(), PriorityWindowAction):
            native_priority_action = port.action_stack.peek()
            player_id = getattr(_transaction, "player_id", None)
            live_phase = int(live.get("phase", 0) or 0)
            if live_phase == int(_ge.ETurnPhases.FirstMainPhase):
                # FirstMainState chooses the next phase using this fact. It
                # must be refreshed before the final pass ticks the native
                # scheduler, otherwise an empty board enters DeclareCombat
                # and can wait for a combat action that cannot exist.
                has_attackers = pvp_turn_has_attackers(
                    session, int(live.get("turn_pid", 0) or 0))
                port.has_legal_attackers = bool(has_attackers)
                port.active_player_skips_attack = not bool(has_attackers)
            # Keep a reconnectable pass record, while the native action queue
            # remains the authority for accepting and ordering the pass.
            raw_player = _uid_int(player_id)
            pass_pid = (raw_player >> 8
                        if (raw_player & 0xFF) == 244 else raw_player)
            responding_to_chain = (
                getattr(native_priority_action, "ability_responding_to", None)
                is not None)
            pass_key = "stack_passed" if responding_to_chain else "passes"
            passed = set(int(value) for value in
                         (live.get(pass_key) or ()))
            passed.add(pass_pid)
            live[pass_key] = sorted(passed)
            if not port.pass_priority_and_drive(player_id):
                return False
            # The native scheduler has now either handed off, resolved the
            # chain, or entered the next phase. Project that result once.
            port.sync_to_pvp_state(live)
            pvp_save_state(session, live)
            # Keep the native scheduler checkpoint in lockstep with the PvP
            # projection.  Without this, the wire handoff can name the next
            # player while a reconnect or the next transaction rehydrates the
            # old PriorityWindowAction owner and rejects that player's pass.
            try:
                port.persist()
            except Exception as exc:
                log_req(f"    PvP RulesPort post-pass persistence failed: {exc}")
            # A chain resolution can suspend on an interactive prompt (for
            # example Corinth's charge power picks a card in the Choosing
            # zone).  The prompt helper already sent the private picker and
            # owns the next green light.  Pushing the ordinary priority
            # handoff/phase options here would immediately tear that picker
            # down, so leave priority with the pending input.
            if any(live.get(key) for key in (
                    "pending_choice", "pending_deck_search", "pending_trigger",
                    "pending_conversation", "pending_discard_ability")):
                pvp_save_state(session, live)
                log_req("    PvP pass paused for pending input; priority "
                        "handoff skipped")
                return True
            # RulesPort owns the queue mutation, but the host still owns the
            # historical client event projection. Without this handoff the
            # next player never receives GreenLight after the first pass and
            # the client remains stuck in Declare Combat.
            next_priority = int(live.get("priority_pid", 0) or 0)
            current_phase = int(live.get("phase", 0) or 0)
            if (next_priority and current_phase == previous_phase and
                    next_priority != previous_priority):
                pids_live = db_game_session_pids(session.session_id)
                for target_pid in pids_live:
                    target_h = player_handlers.get(int(target_pid))
                    if not target_h:
                        continue
                    other_pid = next(
                        (int(value) for value in pids_live
                         if int(value) != int(target_pid)), int(target_pid))
                    target_uid = _ge.UID.make(244, int(target_pid))
                    other_uid = _ge.UID.make(244, other_pid)
                    handoff = _ge.Game(
                        int(session.session_id), target_uid, other_uid)
                    _pvp_populate_game_state(
                        handoff, live, int(target_pid), other_pid)
                    handoff.push_green_light(
                        _ge.UID.make(244, next_priority),
                        _ge.EPriorityContext.Normal)
                    # A same-phase native handoff must rebuild the client's
                    # phase state as well as toggle GreenLight.  The legacy
                    # pass path already does this; omitting it here leaves
                    # the receiving client in an inactive/stale MainPhase
                    # state with no pass button even though the checkpoint
                    # correctly assigns it priority.
                    _pvp_push_turn_phase_with_elapsed(
                        handoff, current_phase,
                        _ge.UID.make(244, int(live.get("turn_pid") or
                                              next_priority)),
                        _ge.UID.make(244, next_priority),
                        priority_elapsed_ticks(
                            live, next_priority) // 10_000_000)
                    _send_pvp_packet(
                        target_h, session, handoff, target_uid,
                        "rules-port-priority-handoff")
                pvp_push_phase_options(
                    session, live, pid=next_priority)
                log_req(f"    PvP RulesPort priority handoff: "
                        f"{previous_priority} -> {next_priority} "
                        f"phase={current_phase}")
            return True
        log_req("    PvP RulesPort rejected pass: native priority action missing")
        return False

    def native_phase_entry(phase):
        """Project a native RulesPort phase entry to the PvP wire state.

        ``_pvp_run_phase_start`` remains a packet/state projection: the
        native phase state has already selected the transition and will create
        the priority action immediately after this callback returns.  It no
        longer owns phase progression or pass handling.
        """
        live = pvp_load_state(session) or state
        turn_pid = int(live.get("turn_pid") or 0)
        if not turn_pid:
            return False
        pids_live = db_game_session_pids(session.session_id)
        if len(pids_live) < 2:
            return False
        port_enter_phase(live, phase)
        defender = next((int(pid) for pid in pids_live if int(pid) != turn_pid),
                         turn_pid)
        phase_priority = port.priority_players_for_phase(
            live, int(phase), turn_pid, defender)
        if phase_priority.name == "NONE":
            live.pop("priority_pid", None)
        else:
            live["priority_pid"] = (defender
                                     if phase == _ge.ETurnPhases.DeclareDefense
                                     else turn_pid)
        if (phase == _ge.ETurnPhases.DeclareDefensePriorityWindow and
                live.get("attackers") and not live.get("blockers") and
                _pvp_defender_blockable_count(session, live) <= 0):
            # The native graph skips DeclareDefense when nobody can block.
            # Keep the client-visible empty declaration from the old
            # auto-pass path, and persist it to prevent duplicate publication
            # if this phase is re-entered after reconnect.
            pvp_push_empty_blockers(session, live)
            live["blockers"] = {
                str(attacker): []
                for attacker in (live.get("attackers") or {})}
        if phase == _ge.ETurnPhases.Discard:
            try:
                live["discard_required"] = (
                    db_hand_count(session.session_id, turn_pid, conn=_db)
                    > DEFAULT_MAX_HAND_SIZE)
            except (TypeError, ValueError):
                live["discard_required"] = False
        else:
            live.pop("discard_required", None)
        # FirstMainState's branch fact is refreshed at the native boundary;
        # this prevents a stale legacy cursor from reintroducing combat when
        # no legal attacker exists.
        live["_rules_port_attached"] = True
        try:
            port.active_player_skips_attack = not pvp_turn_has_attackers(
                session, turn_pid)
        except Exception:
            port.active_player_skips_attack = False
        # The native Prep state branches straight to FirstMainPhase when the
        # active player has no draw step.  Without this fact the scheduler
        # enters Draw and the legacy projection deals a real card, which is
        # the live symptom in a format whose champion power says "Skip your
        # draw phase".
        port.active_player_skips_draw = port_skips_draw_phase(live)
        pvp_save_state(session, live)
        _pvp_run_phase_start(session, live, phase)
        session._rules_port_mutation_emitted = True
        return True

    def native_phase_exit(phase):
        """Publish C# combat completion when its native phase exits."""
        if phase == _ge.ETurnPhases.FirstStrikePriorityWindow:
            if port.combat_has_standard_damage:
                return None
        elif phase != _ge.ETurnPhases.AssignDamage:
            return None
        live = pvp_load_state(session) or state
        turn_pid = int(live.get("turn_pid") or 0)
        pids_live = db_game_session_pids(session.session_id)
        other_pid = next((int(pid) for pid in pids_live
                          if int(pid) != turn_pid), None)
        if not turn_pid or other_pid is None:
            return None
        sink = getattr(port, "event_sink", None)
        projection = getattr(sink, "game", None)
        if projection is None:
            projection = _ge.Game(
                int(session.session_id), _ge.UID.make(244, turn_pid),
                _ge.UID.make(244, other_pid))
            _pvp_populate_game_state(
                projection, live, turn_pid, other_pid)
            if sink is not None:
                sink.game = projection
        view = _pvp_fra_view(live, turn_pid, other_pid)
        _pvp_dispatch_triggers(
            current_handler(), projection, session, view,
            projection.player_uid, projection.ai_uid,
            "CombatEndedEvent", None, turn_pid)
        _pvp_sync_view_to_state(live, view, turn_pid, other_pid)
        pvp_save_state(session, live)
        # C# enqueues CombatEndedEvent from OnExit before the next phase's
        # OnEntry events. Flush this objective stream now to both clients;
        # native_phase_entry will project the subsequent phase/options packet.
        _pvp_send_same_events(
            session, projection, projection.player_uid, projection.ai_uid)
        session._rules_port_mutation_emitted = True
        return None

    def native_turn_boundary(active_player_id):
        """Persist the RulesPort's completed EndTurn rotation for PvP."""
        live = pvp_load_state(session) or state
        try:
            raw = int(getattr(active_player_id, "uid64", active_player_id))
            next_pid = raw >> 8 if (raw & 0xFF) == 244 else raw
        except (TypeError, ValueError):
            return False
        if not next_pid:
            return False
        pids_live = db_game_session_pids(session.session_id)
        # The native rotation does not know about a queued bonus turn
        # (GiveBonusTurn: "take an additional turn after this one"); the
        # checkpoint's pending bonus selects the incoming player instead.
        bonus_pid = int(live.get("bonus_turn_pid", 0) or 0)
        if bonus_pid in {int(pid) for pid in pids_live}:
            next_pid = bonus_pid
        boundary = port_advance_turn_state(
            live, pids_live, incoming_player_id=next_pid)
        pvp_save_state(session, live)
        log_req("    PvP RulesPort turn boundary: next turn player "
                f"{boundary['turn_pid']}"
                + (" (bonus)" if boundary["bonus_used"] else ""))
        # Returning the typed identity lets the generic native scheduler keep
        # its active-player cursor aligned if this was a bonus/current-player
        # turn rather than the ordinary alternating handoff.
        return _ge.UID.make(244, int(boundary["turn_pid"]))

    def pvp_hand(kind, transaction):
        current = current_handler()
        if kind == "accept_starting_hand":
            return bool(current._handle_mulligan_keep_transaction(
                session, getattr(session, "_rules_port_dispatch_command", None)))
        if kind == "mulligan":
            return bool(current._handle_mulligan_redraw_transaction(
                session, getattr(session, "_rules_port_dispatch_command", None)))
        return False

    def setup_pick(transaction):
        """Project the typed Play/Draw choice into the PvP setup state."""
        original = getattr(session, "_rules_port_dispatch_command", None)
        if original is None:
            return False
        try:
            handled = bool(current_handler()._handle_choose_pick_transaction(
                session, original))
            if handled:
                session._rules_port_mutation_emitted = True
                # The play/draw choice is made; clear the opponent's wait.
                _pvp_push_waiting_on(session, None)
            return handled
        except Exception as exc:
            log_req(f"    PvP setup mutation failed: {exc}")
            return False

    def cancel_auto_pass(_transaction):
        live = pvp_load_state(session) or {}
        pid = int(current_handler().client_reck_id)
        if int(live.get("autopass_pid", 0) or 0) != pid:
            return True
        live.pop("autopass_pid", None)
        live.pop("autopass_state", None)
        pvp_save_state(session, live)
        return True

    def set_stops(transaction):
        live = pvp_load_state(session) or state
        pid = int(current_handler().client_reck_id)
        payload = getattr(transaction, "payload", {}) or {}
        live[f"stops_self_{pid}"] = list(payload.get("self_phases", ()))
        other = next((int(value) for value in pids if int(value) != pid), None)
        if other is not None:
            live[f"stops_opp_{other}"] = list(
                payload.get("opponent_phases", ()))
        pvp_save_state(session, live)
        return True

    def options(_transaction):
        live = pvp_load_state(session) or state
        # The native scheduler owns the phase.  A client asking for a resync
        # must be answered from the authoritative port, never by echoing a
        # stale checkpoint back (that is how the client and server drifted).
        # Align the checkpoint to the port so the projection and any reconnect
        # agree.
        phase = int(getattr(port, "current_turn_phase",
                            live.get("phase", 0)) or 0)
        live["phase"] = phase
        priority = getattr(port.action_stack, "priority_player_id", None)
        if priority is not None:
            raw_priority = int(getattr(priority, "uid64", priority))
            live["priority_pid"] = (raw_priority >> 8
                                    if (raw_priority & 0xFF) == 244
                                    else raw_priority)
        pvp_save_state(session, live)
        # Project the SAME option list the phase entry pushed.  The client
        # replaces its entire option set on every PlayerOptionList, so
        # answering a DeclareAttack/DeclareDefense resync with the generic
        # quick-action window would clear ECardUsage.Attack/Defend and leave
        # every ready troop unselectable.
        pvp_push_current_phase_options(session, live)
        return True

    # All callbacks are projections. No callback performs a second rules
    # validation path; the port has already accepted the typed transaction.
    port.set_card_transaction_resolver(native_executor)
    port.set_resource_transaction_resolver(lambda tx: pvp_projection(
        "play_resource", tx))
    port.set_setup_transaction_resolver(setup_pick)
    port.set_priority_transaction_resolver(pvp_pass)
    port.set_hand_transaction_resolver(pvp_hand)
    port.set_auto_pass_transaction_resolver(
        lambda tx: set_pvp_auto_pass(current_handler(), session,
                                     (getattr(tx, "payload", {}) or {}).get(
                                         "passing_state", 2)))
    port.set_cancel_auto_pass_transaction_resolver(cancel_auto_pass)
    port.set_priority_sync_resolver(options)
    port.set_turn_phase_resolver(set_stops)
    port.set_turn_boundary_resolver(native_turn_boundary)
    port.set_turn_phase_entry_resolver(native_phase_entry)
    port.set_turn_phase_exit_resolver(native_phase_exit)
    port.set_discard_transaction_resolver(lambda tx: pvp_projection(
        "discard", tx))
    port.set_discard_continuation_resolver(lambda tx: pvp_projection(
        "discard_continuation", tx))
    port.set_triggered_ability_transaction_resolver(lambda tx: pvp_projection(
        "triggered", tx))
    port.set_attack_transaction_resolver(lambda tx: pvp_projection(
        "attack", tx))
    port.set_defense_transaction_resolver(lambda tx: pvp_projection(
        "defense", tx))
    port.set_damage_transaction_resolver(lambda tx: pvp_projection(
        "damage", tx))
    port.set_choice_transaction_resolver(lambda tx: pvp_projection(
        "choice", tx))
    port.set_player_options_resolver(options)
    port.set_ready_card_transaction_resolver(lambda tx: pvp_projection(
        "ready", tx))
    port.set_encounter_mod_resolver(lambda tx: pvp_projection(
        "conversation", tx))
    port.set_quit_game_resolver(lambda _tx: bool(
        pvp_concede(current_handler(), session)))
    port.assert_projection_wiring()
    port.rehydrate_combats()
    # ``restore_snapshot`` runs before the PvP callbacks above exist.  Now
    # that the native chain resolver is wired, rebuild the current projected
    # response window if this handler was materialized by reconnect.
    port.rehydrate_projected_chain()
    setattr(port, "_pvp_current_handler", handler)
    setattr(port, "_pvp_current_session", session)
    setattr(port, "_pvp_state", state)
    _wire_pvp_state_based_handler(port, handler, session, game)
    session._rules_port_session = port
    set_pvp_shared_port(session, port)
    # ``restore_snapshot`` may have loaded a pre-migration native owner while
    # the PvP checkpoint has the current owner.  Persist the reconciled native
    # snapshot immediately so reconnect cannot resurrect that stale turn
    # owner on the next process or handler attach.
    port.persist()
    log_req(f"    PvP RulesPort attached session={session.session_id}",
            level="DEBUG")
    return port


















# ── priority watchdog ──────────────────────────────────────────────────────
# The original implementation periodically re-pushed GreenLight events to
# correct client-side priority drift.  Normal PvP transitions now explicitly
# send the correct priority to both clients, so periodic client events are no
# longer needed and would pollute replay capture.  Keep the daemon only as a
# server-side clock/inactivity watchdog.































def _pvp_auto_pass_chain_priority(session, state, pid):
    """Consume a chain response for an F10-enabled player.

    The client normally submits the follow-up PassPriority itself.  PvP can
    hand a GreenLight to an auto-passing client while it is rebuilding its
    chain/priority UI, leaving the opponent's card waiting for a manual
    Resolve click.  The server owns the authoritative two-pass state, so
    consume this response here and use the normal pass route.
    """
    if (not _pvp_chain_active(session, state) or
            not pvp_player_auto_passes(state, pid) or
            int(state.get("autopass_state", 2) or 2) != 2):
        return False
    if int(state.get("priority_pid") or 0) != int(pid):
        return False
    h = player_handlers.get(int(pid))
    if h is None:
        return False
    log_req(f"    PvP F10: server auto-passing chain priority for {pid}")
    route_pvp_pass(h, session)
    return True




@_pvp_locked
def set_pvp_auto_pass(handler, session, passing_state=2):
    """Enable F10 auto-pass for one PvP client and consume its current pass.

    The client will send the subsequent passes itself whenever a GreenLight is
    handed back to it, including ResolveTopOfChain.  Record which player's
    configured stops should be ignored, then consume the current pass
    immediately so the server can advance through the current stop too.
    """
    pid = int(handler.client_reck_id)
    state = pvp_load_state(session)
    if not state:
        return False
    state["autopass_pid"] = pid
    state["autopass_state"] = int(passing_state or 2)
    pvp_save_state(session, state)
    current_priority = state.get("priority_pid")
    route_pvp_pass(handler, session)
    _pvp_drive_f10_priority(session, state)
    log_req(f"    PvP SetAutoPass: pid={pid} state={passing_state} "
            f"priority_was={current_priority}")
    return True


def _pvp_auto_pass_opponent_stop(session, state, turn_pid, opp_pid):
    """Hand an opponent-only configured stop to the opponent during F10.

    The normal phase walker pushes priority to the active player first.  When
    that player is auto-passing, an opponent stop must consume the active
    player's pass before the opponent can receive priority; otherwise the
    walker appears to stop on the active player's screen (notably at Second
    Main, which is in the opponent defaults too).
    """
    from rules_port import lifecycle as _be
    phase = int(state.get("phase", 0))
    if not pvp_player_auto_passes(state, turn_pid):
        return False
    if phase in _be.SELF_ALWAYS_STOPS or phase in _be.OPP_ALWAYS_STOPS:
        return False
    opp_stops = set(state.get(f"stops_opp_{opp_pid}")
                    or _be.OPP_DEFAULT_STOPS)
    if phase not in opp_stops:
        return False
    h = player_handlers.get(turn_pid)
    if not h:
        return False
    route_pvp_pass(h, session)
    log_req(f"    PvP F10: passed active priority at opponent stop "
            f"phase {phase} to {opp_pid}")
    return True


def pvp_advance_past_non_stops(session, state):
    """Auto-advance the PvP phase one step at a time (pushing each phase and
    running its start-of-phase logic — Prep resource, Draw) until the next
    phase that is a STOP for either player, at which point the both-pass
    cycle takes over.

    Non-interactive phases like Ready/Prep/Draw are marched through
    server-side (neither client auto-passes them and the opponent can't pass
    without a priority handoff), but a phase is NEVER auto-passed if the turn
    player has a self-stop on it or the opponent has an opponent-stop on it —
    the player's configured stops are respected.  Returns True if it advanced
    at least one phase."""
    pids = db_game_session_pids(session.session_id)
    if len(pids) < 2:
        return False
    turn_pid = state.get("turn_pid")
    opp_pid = pids[0] if pids[1] == turn_pid else pids[1]
    if _pvp_auto_pass_opponent_stop(session, state, turn_pid, opp_pid):
        return True
    # The current phase was just entered by the caller.  If either player has
    # a stop on it, don't auto-advance — let the both-pass cycle handle it.
    if pvp_phase_is_stop(state, int(state.get("phase", 6)), turn_pid, opp_pid):
        log_req(f"    PvP auto-advance: phase {state.get('phase')} is a "
                f"player stop — not auto-passing")
        return False
    # Combat steps only when the turn player controls a troop ELIGIBLE to
    # attack (untapped, not summoning sick) — mirrors the PvE build_turn_phases
    # / ai.player_can_attack_troops.  With no eligible attackers the turn skips
    # DeclareAttack/DeclareDefense/AssignDamage entirely (FirstMain -> SecondMain).
    has_ready = pvp_turn_has_attackers(session, turn_pid)
    phase_list = _pvp_turn_phase_list(state, turn_pid, has_ready)
    try:
        cur = phase_list.index(int(state.get("phase", 6)))
    except ValueError:
        cur = 0
    advanced = False
    while True:
        cur += 1
        if cur >= len(phase_list):
            log_req("    PvP advance: reached the end of the phase list")
            return advanced
        new_phase = phase_list[cur]
        port_enter_phase(state, new_phase)
        pvp_save_state(session, state)
        # _pvp_run_phase_start pushes the TurnPhase + GreenLight to both in one
        # packet each (greenlight first, so the client never sees the phase
        # without priority).
        log_req(f"    PvP auto-advance: phase {new_phase} to both")
        _pvp_run_phase_start(session, state, new_phase)
        advanced = True
        if _pvp_auto_pass_opponent_stop(session, state, turn_pid, opp_pid):
            return advanced
        if pvp_phase_is_stop(state, new_phase, turn_pid, opp_pid):
            log_req(f"    PvP auto-advance: stopped at phase {new_phase} "
                    f"(player stop)")
            return advanced
        # Discard (21): stop only when the turn player's hand exceeds the max
        # hand size (7) — mirror PvE: hand fits -> auto-advance.
        if new_phase == _ge.ETurnPhases.Discard:
            hand_count = db_hand_count(
                session.session_id, turn_pid, conn=_db)
            try:
                from rules_port.static_rules import hand_size_unlimited
                guid_map = state.setdefault("champ_guid_map", {})
                for _pid in (turn_pid, opp_pid):
                    if str(_pid) in guid_map:
                        continue
                    _row = db_game_champion(session.session_id, _pid)
                    if _row and _row[1]:
                        guid_map[str(_pid)] = _row[1]
                unlimited = hand_size_unlimited(
                    _db, session.session_id, state)
            except Exception:
                unlimited = False
            if hand_count > DEFAULT_MAX_HAND_SIZE and not unlimited:
                log_req(f"    PvP auto-advance: stopped at Discard "
                        f"(hand {hand_count} > {DEFAULT_MAX_HAND_SIZE})")
                return advanced


































@_pvp_locked
def route_pvp_pass(handler, session):
    """Handle a PassPriority in a tournament PvP session.

    When both players pass the current phase, the server atomically
    advances: pushes the new phase to BOTH players, runs start-of-phase
    logic, then gives GreenLight to the turn player.  No intermediate
    green-light passthrough — that broke client state.

    Phase progression reuses the battle engine's per-turn phase lists
    (BASE_TURN_PHASES / COMBAT_TURN_PHASES), so PvP wraps at EndTurn, switches
    the turn player and runs the same start-of-phase logic (Prep resources,
    Draw, GreenLight) as the AI path.
    """
    if not (session.session_name or "").startswith("tourney-"):
        return False
    # All live tournament sessions are RulesPort-owned by default. Internal
    # callers (auto-pass, reconnect repair, and legacy service helpers) may
    # still arrive here without going through the transaction callback. Route
    # those passes through the native action stack as well, instead of
    # allowing a second state["stack"] priority implementation to run.
    native_port = getattr(session, "_rules_port_session", None)
    if native_port is not None:
        from rules_port.kernel import PriorityWindowAction
        live_native = pvp_load_state(session) or {}
        if (native_port.action_stack.peek() is None and
                live_native.get("stack") and
                getattr(native_port, "rehydrate_projected_chain", None)):
            native_port.rehydrate_projected_chain()
        action = native_port.action_stack.peek()
        if not isinstance(action, PriorityWindowAction):
            log_req("    PvP RulesPort rejected internal pass: native priority action missing")
            return False
        player_id = _ge.UID.make(244, int(
            handler.client_reck_id if hasattr(handler, "client_reck_id") else 0))
        if not native_port.pass_priority_and_drive(player_id):
            return False
        next_uid = native_port.action_stack.priority_player_id
        # A native PvP priority window still needs the mode's stop policy.
        # The old projection auto-completed the other player's pass during a
        # main phase when they had neither an explicit opponent stop nor a
        # legal quick action.  The RulesPort handoff above establishes the
        # same priority window, but without this check an empty Second Main
        # phase waits forever for a second client pass.
        if next_uid is not None:
            live_after_pass = pvp_load_state(session) or live_native
            phase = int(live_after_pass.get("phase", 0) or 0)
            raw_waiting = int(getattr(next_uid, "uid64", next_uid))
            waiting_pid = (raw_waiting >> 8
                           if (raw_waiting & 0xFF) == 244 else raw_waiting)
            if phase in (_ge.ETurnPhases.FirstMainPhase,
                         _ge.ETurnPhases.SecondMainPhase):
                try:
                    has_quick_action = bool(_pvp_affordable_troop_abilities(
                        session, live_after_pass, pid=waiting_pid))
                except Exception as exc:
                    has_quick_action = False
                    log_req(f"    PvP native auto-pass quick-action check "
                            f"failed for {waiting_pid}: {exc}")
                if native_port.auto_pass_waiting_player(
                        live_after_pass, next_uid,
                        has_quick_action=has_quick_action):
                    next_uid = native_port.action_stack.priority_player_id
                    log_req(
                        f"    PvP RulesPort auto-completed priority for "
                        f"{waiting_pid} on phase {phase} "
                        f"(no opponent stop or quick action)")
        native_port.sync_to_pvp_state(live_native)
        pvp_save_state(session, live_native)
        # A phase transition may have opened another priority window for the
        # same player who enabled F10.  Drain it before returning so the
        # client's one-transaction gate is not left waiting on a pass it
        # expected the server to perform.
        _pvp_drive_f10_priority(session, live_native)
        return True
    my_pid = int(handler.client_reck_id) if hasattr(handler, 'client_reck_id') else 0
    pids = db_game_session_pids(session.session_id)
    if len(pids) < 2:
        return False

    state = pvp_load_state(session)
    if state is None:
        state = pvp_default_state(my_pid, my_pid)
        port_enter_phase(state, 10)
        pvp_save_state(session, state)

    # Never auto-pass during Mulligan / PickGoesFirst.
    if state["phase"] in (3, 4):
        log_req(f"    PvP: ignoring pass in phase {state['phase']} (mulligan/setup)")
        return True

    # Instant-speed response window: after the turn player plays a card, the
    # OPPONENT gets priority to respond with a quick action.  When the opponent
    # passes, hand priority BACK to the caster (don't advance the phase) so they
    # can continue or, if the chain is non-empty, resolve it.
    resp_wait = state.get("response_waiting_pid")
    if resp_wait == my_pid:
        caster_pid = (state.get("response_caster_pid") or
                      state.get("turn_pid"))
        if caster_pid is None:
            return True
        caster_pid = int(caster_pid)
        state.pop("response_waiting_pid", None)
        state.pop("response_caster_pid", None)
        port_set_priority(state, caster_pid)
        pvp_save_state(session, state)
        # If a chain item is pending (e.g. Adamanthian Scrivener's enters-play
        # trigger), the opponent's response pass counts as their stack pass —
        # so the caster is ONE pass from resolving the top item.  Seed
        # stack_passed with the opponent so the caster's Resolve pass triggers
        # the both-pass stack rule (see _pvp_resolve_stack_item).
        if state.get("stack"):
            sp = set(state.get("stack_passed") or [])
            sp.add(my_pid)  # the opponent just passed the stack
            state["stack_passed"] = sorted(sp)
            pvp_save_state(session, state)
        caster_h = player_handlers.get(caster_pid)
        if caster_h:
            caster_uid = _ge.UID.make(244, caster_pid)
            opp_uid = _ge.UID.make(244, my_pid)
            gg = _ge.Game(int(session.session_id), caster_uid, opp_uid)
            ctx = (_ge.EPriorityContext.ResolveTopOfChain
                   if state.get("stack") else _ge.EPriorityContext.Normal)
            gg.push_green_light(caster_uid, ctx)
            _send_pvp_packet(caster_h, session, gg, caster_uid,
                             "response-window-close")
        _st2 = pvp_load_state(session) or {}
        if _st2.get("phase") in (_ge.ETurnPhases.FirstMainPhase,
                                 _ge.ETurnPhases.SecondMainPhase) \
                and not state.get("stack"):
            pvp_push_main_phase_options(session, _st2)
        log_req(f"    PvP response window: {my_pid} passed — priority back to "
                f"caster {caster_pid}"
                + (", caster 1 pass from resolving chain"
                   if state.get("stack") else ""))
        return True

    # Chain/stack resolution: while the chain is non-empty, the client's pass
    # button is "Resolve" — but BOTH players must pass for the top item to
    # resolve (mirrors PvE stack_set_pass / stack_both_passed).  The first
    # passer hands priority to the OTHER player (ResolveTopOfChain) so they
    # can respond; only when both have passed does the item resolve.
    if state.get("stack"):
        stack_pass = port_stack_pass_transition(
            state.get("stack_passed"), my_pid, pids)
        if stack_pass["action"] == "duplicate":
            # Already passed — ignore the duplicate.
            return True
        sp = set(stack_pass["passed"])
        other_pid = stack_pass["other_player"]
        if stack_pass["action"] == "handoff":
            # Only one player has passed: hand priority to the other so they
            # can cast a response (quick action) or pass to resolve.
            state["stack_passed"] = sorted(sp)
            port_set_priority(state, other_pid)
            pvp_save_state(session, state)
            _pvp_log_stack(state, f"pass-1/2 by {my_pid}")
            if _pvp_auto_pass_chain_priority(session, state, other_pid):
                return True
            other_h = player_handlers.get(other_pid)
            if other_h:
                my_uid = _ge.UID.make(244, my_pid)
                other_uid = _ge.UID.make(244, other_pid)
                gg = _ge.Game(int(session.session_id), other_uid, my_uid)
                gg.push_green_light(other_uid,
                                    _ge.EPriorityContext.ResolveTopOfChain)
                _send_pvp_packet(other_h, session, gg, other_uid,
                                 "chain-respond")
                # Offer the responding player their quick actions / champion
                # powers so they can actually respond (e.g. Countermagic the
                # troop/spell on the stack, or cast an interrupt).
                try:
                    pvp_push_phase_options(session, state, pid=other_pid)
                except Exception as e:
                    log_req(f"    PvP chain-respond options error: {e}")
                log_req(f"    PvP chain: {my_pid} passed 1/2 — priority to "
                        f"{other_pid} to respond")
            return True
        # Both players passed — resolve the top item.
        state["stack_passed"] = []
        pvp_save_state(session, state)
        _pvp_log_stack(state, f"pass-2/2 by {my_pid}")
        return _pvp_resolve_stack_item(session, state, handler, my_pid)

    # Record this player's pass.
    passes = port_record_phase_pass(state, my_pid)
    pvp_save_state(session, state)

    if len(passes) < 2:
        waiting_pid = pids[0] if pids[1] in passes else pids[1]
        # If the phase is an OPPONENT-STOP for the waiting player, hand them
        # priority so they can actually act (the client only shows the pass
        # button while holding priority).  Otherwise they have nothing to
        # respond to (no instants in PvP yet) — auto-complete their pass so
        # the turn player's single pass advances the phase ("Continue to
        # Second Main Phase" just works instead of stalling at 1/2).
        # Only stop the waiting player for MANDATORY opponent phases
        # (OPP_ALWAYS_STOPS — DeclareDefense, where they must decide blocks)
        # or phases they EXPLICITLY configured as opponent-stops.  The client's
        # DEFAULT opponent stops (SecondMain, DeclareAttackPriorityWindow,
        # DeclareDefensePriorityWindow) would otherwise force a manual pass
        # from the opponent every turn even though PvP has no instants to
        # respond with — stalling at the turn player's pass.
        # A QuickAction permanent ability is a real response option during an
        # opponent's main phase too.  The previous auto-complete path only
        # considered configured opponent stops, so the active player's pass
        # advanced the phase before the other client could activate cards such
        # as Construction Plans: Ingenuity Engine.
        quick_action_wait = False
        if int(state.get("phase", -1)) in (
                _ge.ETurnPhases.FirstMainPhase,
                _ge.ETurnPhases.SecondMainPhase):
            try:
                quick_action_wait = bool(_pvp_affordable_troop_abilities(
                    session, state, pid=waiting_pid))
            except Exception:
                quick_action_wait = False
        if port_waiting_player_requires_priority(
                state, int(state["phase"]), waiting_pid,
                has_quick_action=quick_action_wait):
            waiting_h = player_handlers.get(waiting_pid)
            if waiting_h:
                waiting_uid = _ge.UID.make(244, waiting_pid)
                other_pid = pids[1] if waiting_pid == pids[0] else pids[0]
                other_uid = _ge.UID.make(244, other_pid)
                g = _ge.Game(int(session.session_id), waiting_uid, other_uid)
                # A normal GreenLight only toggles the client's priority bit.
                # If an earlier animation/state transition left the priority
                # window missing, the client can show activatable abilities
                # but no Continue button.  Re-assert the current phase after
                # GreenLight so the client rebuilds its phase state, matching
                # the reconnect snapshot path.
                g.push_green_light(waiting_uid, _ge.EPriorityContext.Normal)
                port_set_priority(state, waiting_pid)
                pvp_save_state(session, state)
                _pvp_push_turn_phase_with_elapsed(
                    g, int(state["phase"]),
                    _ge.UID.make(244, int(state.get("turn_pid") or waiting_pid)),
                    waiting_uid,
                    priority_elapsed_ticks(state, waiting_pid) // 10_000_000)
                # Champion charge powers must stay interactive in ANY priority
                # window (mirrors PvE _push_phase_options_empty): push an
                # empty options list carrying the champion so the buttons
                # remain lit on the opponent's screen during their stop.
                g.push_options(waiting_uid, [])
                affordable_wait = _pvp_affordable_troop_abilities(
                    session, state, pid=waiting_pid)
                if affordable_wait:
                    _pvp_add_troop_ability_options(
                        g, session, state, waiting_uid, other_uid,
                        waiting_pid, affordable_wait)
                _pvp_add_champion_options(g, session, state, waiting_pid,
                                          waiting_uid)
                _send_pvp_packet(waiting_h, session, g, waiting_uid,
                                 "pass-handoff")
                # GreenLight is local client state.  The waiting client must
                # gain it, but the player who just passed must also receive
                # the same owner so it immediately loses priority and clears
                # its options.  Relying on the server-side watchdog for the
                # second half leaves the two clients disagreeing during the
                # handoff and can strand the UI between priority windows.
                passed_h = player_handlers.get(my_pid)
                if passed_h:
                    passed_uid = _ge.UID.make(244, my_pid)
                    passed_game = _ge.Game(
                        int(session.session_id), passed_uid, waiting_uid)
                    passed_game.push_green_light(
                        waiting_uid, _ge.EPriorityContext.Normal)
                    _pvp_push_turn_phase_with_elapsed(
                        passed_game, int(state["phase"]),
                        _ge.UID.make(
                            244, int(state.get("turn_pid") or waiting_pid)),
                        waiting_uid,
                        priority_elapsed_ticks(
                            state, waiting_pid) // 10_000_000)
                    _send_pvp_packet(passed_h, session, passed_game,
                                     passed_uid, "pass-handoff-lost")
                log_req(f"    PvP pass: {len(passes)}/2 — priority handed to "
                        f"{waiting_pid} ({'quick action' if quick_action_wait else 'opponent stop'} "
                        f"on phase {state['phase']})")
            else:
                log_req(f"    PvP pass: {len(passes)}/2 — waiting for opponent")
            return True
        # No opponent-stop: the waiting player has nothing to respond to —
        # auto-complete their pass and advance.
        passes = port_record_phase_pass(state, waiting_pid)
        pvp_save_state(session, state)
        log_req(f"    PvP pass: auto-completed opponent {waiting_pid}'s pass "
                f"(no opponent stop on phase {state['phase']})")

    # ── both players have passed ──────────────────────────────────────
    old_phase = state["phase"]
    # Decide the phase list for this turn: combat steps only when the turn
    # player controls a ready troop (mirrors build_turn_phases).  CRITICAL:
    # once we are PAST the first combat phase (>= DeclareAttack=12) we must
    # stay on COMBAT_TURN_PHASES even if the attacker's troops are now tapped
    # (they declared attacks) — otherwise passing DeclareDefense wraps to a NEW
    # turn (phase 7) instead of resolving Swiftstrike/AssignDamage damage, so
    # combat is skipped, the champion takes no damage, and a fresh turn starts.
    turn_pid_value = state.get("turn_pid")
    if turn_pid_value is None:
        return
    turn_pid = int(turn_pid_value)
    old_phase = int(old_phase)
    if old_phase >= _ge.ETurnPhases.DeclareAttack \
            and old_phase != _ge.ETurnPhases.SecondMainPhase:
        has_ready = True
    else:
        has_ready = pvp_turn_has_attackers(session, turn_pid)
    phase_list = _pvp_turn_phase_list(state, turn_pid, has_ready)
    # The client chooses the next combat phase only after Declare Blockers has
    # completed and the DeclareDefensePriorityWindow response window has
    # closed.  Evaluate the live combat here so a Quick Action that grants
    # Swiftstrike to an attacker or blocker is included.
    if old_phase == _ge.ETurnPhases.DeclareDefensePriorityWindow:
        new_phase = pvp_phase_after_blockers(session, state)
        transition = port_phase_transition(
            phase_list, old_phase, after_blockers=new_phase)
    else:
        transition = port_phase_transition(phase_list, old_phase)
    cur_idx = transition["current_index"]
    next_idx = transition["next_index"]
    new_phase = transition["new_phase"]
    if transition["wrapped"]:
        pids_ = db_game_session_pids(session.session_id)
        # EndTurn passed: fire "At the end of your turn" triggers for the
        # outgoing turn player, then switch the turn player, wrap to StartTurn.
        # Mirrors PvE (hconnect ~3266): TurnEndedEvent + temporary_attributes
        # expiration + warzone re-push.
        try:
            end_h = player_handlers.get(turn_pid)
            if end_h:
                end_opp = pids_[0] if pids_[1] == turn_pid else pids_[1]
                end_opp_uid = _ge.UID.make(244, int(end_opp))
                end_uid = _ge.UID.make(244, turn_pid)
                eg = _ge.Game(int(session.session_id), end_uid, end_opp_uid)
                eg.player_health = int(state.get(f"hp_{turn_pid}", 20))
                eg.ai_health = int(state.get(f"hp_{end_opp}", 20))
                if not state.get("turn_end_trigger_fired"):
                    _pvp_dispatch_triggers(
                        end_h, eg, session,
                        _pvp_fra_view(state, turn_pid, end_opp),
                        end_uid, end_opp_uid, "TurnEndedEvent", None,
                        turn_pid)
                    if state.get("stack"):
                        # Hold the current EndTurn until its triggered ability
                        # resolves.  Otherwise the next turn begins with the
                        # trigger still on the stack and a token such as Blaze
                        # Elemental is sacrificed during the next First Main.
                        state["turn_end_trigger_fired"] = True
                # Combat damage and "until end of turn" attributes expire at
                # cleanup, not at the next turn's Prep.  Clear every warzone
                # card because combat can damage either player's troops.
                from rules_port.lifecycle import (
                    clear_combat_damage, clear_expired_temporary_attributes)
                clear_combat_damage(_db, session.session_id)
                clear_expired_temporary_attributes(
                    _db, session.session_id, turn_pid, "end_turn",
                    clear_stat_buffs=True)
                # CardUpdated rebuilds the CardDef after card_damage is
                # cleared, so both clients immediately see healed defense.
                pvp_push_warzone_updates(session, state)
                ev_view = _pvp_fra_view(state, turn_pid, end_opp)
                if ev_view.get("player_health") is not None:
                    state[f"hp_{turn_pid}"] = int(ev_view["player_health"])
                if ev_view.get("ai_health") is not None:
                    state[f"hp_{end_opp}"] = int(ev_view["ai_health"])
                pvp_save_state(session, state)
                if eg.events:
                    _pvp_send_same_events(session, eg, end_uid, end_opp_uid)
                log_req(f"    PvP TurnEndedEvent fired for {turn_pid}")
                if state.get("stack"):
                    port_reset_priority_interval(state, turn_pid)
                    pvp_save_state(session, state)
                    end_uid = _ge.UID.make(244, turn_pid)
                    end_opp_uid = _ge.UID.make(244, int(end_opp))
                    eg_priority = _ge.Game(
                        int(session.session_id), end_uid, end_opp_uid)
                    eg_priority.push_green_light(
                        end_uid, _ge.EPriorityContext.ResolveTopOfChain)
                    _send_pvp_packet(end_h, session, eg_priority, end_uid,
                                     "turn-ended-chain")
                    pvp_push_phase_options(session, state, pid=turn_pid)
                    log_req(f"    PvP EndTurn chain held priority for "
                            f"{turn_pid}; turn remains at phase {old_phase}")
                    return True
        except Exception as e:
            import traceback
            log_req(f"    PvP TurnEndedEvent error: {e}")
            traceback.print_exc()
        # Fresh turn: reset the "resource already played this turn" flag for
        # both players (the new turn player can play one again).
        # F10 EndOfTurn belongs only to the outgoing turn.  If it leaks across
        # the boundary, the next turn can skip FirstMain/DeclareAttack stops
        # and appear to jump straight into combat.
        turn_boundary = port_advance_turn_state(state, pids_)
        if turn_boundary["bonus_used"]:
            log_req(f"    PvP: bonus turn for {turn_boundary['turn_pid']}")
        next_idx = 0
        new_phase = phase_list[0]
    elif old_phase != _ge.ETurnPhases.DeclareDefensePriorityWindow:
        new_phase = transition["new_phase"]
    port_enter_phase(state, new_phase)
    pvp_save_state(session, state)
    log_req(f"    PvP: both passed phase {old_phase} → {new_phase} "
            f"(idx {cur_idx}->{next_idx} of {len(phase_list)}, "
            f"list={'C' if has_ready else 'B'}, turn={state.get('turn_pid')})")

    # Leaving AssignDamage resolves the declared combat — through the SAME
    # shared resolver the AI path uses, then events go to both players.
    # Leaving AssignFirstStrikeDamage resolves the Swiftstrike step first
    # (only FirstStrike/DualStrike combatants deal; casualties removed before
    # the normal step) — mirrors PvE's two-step resolution.
    if old_phase == _ge.ETurnPhases.AssignFirstStrikeDamage:
        _pvp_resolve_combat(session, state, first_strike=True)
    elif old_phase == _ge.ETurnPhases.AssignDamage:
        _pvp_resolve_combat(session, state, first_strike=False)
    # A champion may have died from combat or a lingering effect — end the
    # game before pushing any further phase.
    if _pvp_check_game_end(session, state):
        return True

    # Push the new phase to BOTH players — _pvp_run_phase_start sends the
    # TurnPhase + GreenLight together in one packet each (greenlight first so
    # the client holds priority when it processes the phase; a phase event
    # naming the player as priority without a greenlight triggers a spurious
    # RequestPrioritySync + state-stack churn).
    _pvp_run_phase_start(session, state, new_phase)
    # Auto-pass non-stop phases (Ready/Prep/Draw...) — but NEVER a phase that
    # either player has configured a stop on (self-stop for the turn player or
    # opponent-stop for the opponent); those wait for the both-pass cycle.
    # Every new turn starts at StartTurn, so this also marches each turn to
    # its first real interaction.
    pvp_advance_past_non_stops(session, state)
@_pvp_locked




@_pvp_locked
def pvp_handle_discard(handler, session, inner_bytes):
    """Resolve one normal hand-discard transaction in tournament PvP.

    This must stay out of HCPHandler's human-vs-AI discard path: that path
    loads a PvE checkpoint and can replace the persisted PvP state with a
    Practice-style turn, which is how a discard previously jumped back to
    First Main and could produce a false deck-out victory.
    """
    if not session or not (session.session_name or '').startswith('tourney-'):
        return False
    pids = db_game_session_pids(session.session_id)
    if len(pids) < 2:
        return False
    my_pid = int(handler.client_reck_id)
    state = pvp_load_state(session) or {}
    if (int(state.get('phase', -1)) != _ge.ETurnPhases.Discard or
            int(state.get('turn_pid', -1)) != my_pid):
        log_req(f"    PvP discard rejected: pid {my_pid} phase="
                f"{state.get('phase')} turn={state.get('turn_pid')}")
        return True

    card_uids = _pvp_transaction_card_uids(inner_bytes)
    card_uid = card_uids[-1] if card_uids else None
    row = None
    if card_uid is not None:
        row = db_hand_card_for_discard(
            session.session_id, card_uid, my_pid, conn=_db)
    if not row:
        log_req(f"    PvP discard ignored: no hand card for pid {my_pid} "
                f"uid={card_uid}")
        return True

    card_controller, card_owner, tpl_guid, instance_id = row
    # PvP owners are ServicePlayer ids.  A stolen card still returns to its
    # original owner's discard, but never let malformed ownership point an
    # event at the Practice AI UID.
    owner_pid = int(card_owner)
    if owner_pid not in pids:
        owner_pid = my_pid
    db_discard_card(session.session_id, card_uid, owner_user_id=owner_pid,
                    connection=_db)

    opp_pid = pids[0] if pids[1] == my_pid else pids[1]
    my_uid = _ge.UID.make(244, my_pid)
    opp_uid = _ge.UID.make(244, opp_pid)
    g = _ge.Game(int(session.session_id), my_uid, opp_uid)
    _pvp_populate_game_state(g, state, my_pid, opp_pid)
    scid = _ge.SessionCardId(_ge.UID(card_uid))
    owner_uid = _ge.UID.make(244, owner_pid)
    h_card = player_handlers.get(my_pid) or handler
    if h_card:
        _tpl, ct, _name, cost, atk, defense, gems = h_card._card_full_data(
            g, scid, tpl_guid, instance_id)
        g.push_card_updated(
            scid, owner_uid, _ge.ECardCollections.Discard,
            ct, attack=atk, defense=defense, cost=cost,
            template_id=_tpl, gems=gems)
    # A crypt-entry trigger may legitimately put an item on the chain.  Keep
    # it on the shared PvP state and broadcast its events just like a normal
    # card resolution.
    view = _pvp_fra_view(state, owner_pid, opp_pid)
    try:
        _pvp_dispatch_triggers(
            h_card, g, session, view, _ge.UID.make(244, owner_pid),
            _ge.UID.make(244, opp_pid), "CardEnteredZoneEvent", card_uid,
            owner_pid)
    except Exception as exc:
        log_req(f"    PvP discard trigger error: {exc}")
    _pvp_sync_view_to_state(state, view, owner_pid, opp_pid)
    state["stack"] = view.get("stack") or state.get("stack") or []
    state["stack_passed"] = []
    state["priority_pid"] = my_pid
    pvp_save_state(session, state)
    _pvp_send_same_events(session, g, my_uid, opp_uid)

    hand_count = db_hand_count(session.session_id, my_pid, conn=_db)
    log_req(f"    PvP discarded card {card_uid} (hand={hand_count})")
    if state.get("stack"):
        for pid in pids:
            h = player_handlers.get(pid)
            if not h:
                continue
            recipient = _ge.UID.make(244, pid)
            other = _ge.UID.make(244, pids[0] if pids[1] == pid else pids[1])
            gp = _ge.Game(int(session.session_id), recipient, other)
            gp.push_green_light(my_uid, _ge.EPriorityContext.ResolveTopOfChain)
            _send_pvp_packet(h, session, gp, recipient, "discard-chain")
        pvp_push_phase_options(session, state, pid=my_pid)
    elif hand_count > DEFAULT_MAX_HAND_SIZE:
        gp = _ge.Game(int(session.session_id), my_uid, opp_uid)
        gp.push_green_light(my_uid, _ge.EPriorityContext.Normal)
        _send_pvp_packet(handler, session, gp, my_uid, "discard-more")
    else:
        # A successful final discard is equivalent to the turn player passing
        # the Discard phase.  route_pvp_pass performs the normal cleanup and
        # phase transition while preserving the PvP state machine.
        route_pvp_pass(handler, session)
    return True


@_pvp_locked


@_pvp_locked
def _pvp_project_resource_play(handler, session, inner_bytes, my_pid,
                               played_card_uid, crow, card_name, pids,
                               my_uid, opp_uid):
    """Project an already RulesPort-validated PvP resource play."""
    opp_pid = pids[0] if pids[1] == my_pid else pids[1]
    state = pvp_load_state(session) or {}
    from rules_port.resources import can_play_resource, resource_play_limit
    resource_limit = resource_play_limit(
        _db, session.session_id, state, int(my_pid))
    if not can_play_resource(
            state, "player", limit=resource_limit,
            player_id=int(my_pid)):
        log_req(f"    PvP REJECTED resource {card_name}: per-turn allowance used")
        return False
    source_location = _pvp_card_source_location(
        session, state, my_pid, int(played_card_uid))
    if source_location is None:
        log_req(f"    PvP REJECTED resource {card_name}: card is not in a playable zone")
        return False
    prior_card_zone = db_card_owner_zone_state(
        session.session_id, int(played_card_uid), conn=_db)
    from pvp_db import db_move_card_if_in_zone
    from domain.constants import PLAYED_CARD_POSITION
    if db_move_card_if_in_zone(
            session.session_id, int(played_card_uid), int(my_pid),
            source_location, "PlayedResources", PLAYED_CARD_POSITION,
            conn=_db) <= 0:
        return False
    _db.commit()
    log_req(f"    PvP resource play: {card_name} by pid {my_pid}")

    # PlayResource raises CardCast before moving the resource card. Preserve
    # that trigger order in one shared event buffer; each recipient packet
    # below receives the same ordered trigger events without redispatching.
    trigger_game = _ge.Game(int(session.session_id), my_uid, opp_uid)
    _pvp_populate_game_state(trigger_game, state, my_pid, opp_pid)
    trigger_host = _PvpChainHost(handler, session, state, my_pid, opp_pid)
    from rules_port import chain_items as _chain_items
    _chain_items.dispatch_card_cast(
        trigger_host, session, trigger_game, state, my_uid, opp_uid,
        int(played_card_uid), my_pid)
    _chain_items.dispatch_card_zone_transition(
        trigger_host, session, trigger_game, state, my_uid, opp_uid,
        int(played_card_uid), my_pid, source_location, "PlayedResources",
        int(prior_card_zone[2] or 0) if prior_card_zone else 0)

    # Resource templates carry their own current/maximum grants.  Most basic
    # shards grant both, while Shards of Fate grants only maximum resources
    # and then asks the player to choose a Standard resource for its threshold.
    current_grant = int(crow[3] or 0)
    max_grant = int(crow[4] or 0)
    if not current_grant and not max_grant:
        # Keep old/imported resource rows playable while the data migration is
        # being applied; normal Set 1 rows have explicit values.
        current_grant = max_grant = 1
    shard_ability = shard_tpl = None
    resource_choice_ability = None
    ability_guids = []
    if crow[5]:
        try:
            ability_guids = json.loads(crow[5])
        except Exception:
            ability_guids = []
        shard_ability, shard_tpl = handler._shards_of_fate_template(
            ability_guids)
        if not shard_tpl:
            from rules_port.resources import printed_resource_choice_ability
            resource_choice_ability = printed_resource_choice_ability(
                ability_guids)
    is_shards_of_fate = bool(shard_tpl)
    log_req(f"    PvP resource metadata: {card_name} "
            f"abilities={[str(g)[:8] for g in ability_guids]} "
            f"choice={str(resource_choice_ability or '')[:8] or 'none'} "
            f"shards_of_fate={is_shards_of_fate}")

    # Track resources, threshold, and champion charge in PvP state.  Shards of
    # Fate is excluded only from the ordinary-shard threshold path; its
    # selected deck card supplies the threshold after the prompt resolves.
    # Resource charge generation is defined by the card's BOM.  Do not add a
    # universal +1 here: Set 1 shards already contain a gain-one-charge leaf.
    charge_grant = _pvp_resource_charge_points(session, played_card_uid)
    # Thresholds come from the resource's authored CardModifier leaves: a
    # colourless name (Bloodstone, Wakuna Coins, ...) still grants its
    # authored colour, and Primal-Shard-shaped condition-gated leaves grant
    # the missing thresholds in this player's hand.
    from rules_port.resources import (play_resource_for_player,
                                      resource_threshold_grants)
    threshold_grants = []
    if not is_shards_of_fate and not resource_choice_ability:
        threshold_grants = resource_threshold_grants(
            _db, session.session_id, my_pid, ability_guids, state,
            source_uid=played_card_uid)
    resource_play = play_resource_for_player(
        state, my_pid, current_grant, max_grant,
        charge_amount=charge_grant,
        additional_plays=max(0, resource_limit - 1))
    charge_delta = max(
        0, int(resource_play.charge.new_value) -
        int(resource_play.charge.old_value))
    # A normal resource can fire one GainChargeEvent per actual point.
    # Shards of Fate has a nested deck choice below, so defer those events
    # until the picker has completed and is no longer active.
    charge_trigger_game = None
    if charge_delta and not (is_shards_of_fate or resource_choice_ability):
        charge_trigger_game = _pvp_gain_charge_trigger_game(
            handler, session, state, my_pid, amount=charge_delta)
    elif charge_delta:
        state["pending_gain_charge_pid"] = my_pid
        state["pending_gain_charge_amount"] = charge_delta
    thresholds = state.setdefault(f"thresh_{my_pid}", {})
    for flag, amount in threshold_grants:
        thresholds[flag] = int(thresholds.get(flag, 0) or 0) + int(amount)
    pvp_save_state(session, state)
    # Read the post-payment threshold values for the client events.  This must
    # be reconstructed after play_resource_for_player mutates the per-player
    # state; the old path referenced a variable that was never initialized.
    thresh = _pvp_state_thresholds(state, my_pid)
    threshold_trigger_games = []
    for flag, _amount in threshold_grants:
        trigger = _pvp_gain_threshold_trigger_game(
            handler, session, state, my_pid, flag)
        if trigger is not None:
            threshold_trigger_games.append(trigger)
    # Shards of Fate resolves its printed parent in the deck-prompt branch
    # below; every other resource resolves its printed abilities here, except
    # a printed choice ability which the choice flow owns (as in PvE).
    printed_skip = None
    if not is_shards_of_fate:
        printed_skip = ({str(resource_choice_ability).lower()}
                        if resource_choice_ability else set())
    resource_ability_events = _pvp_resolve_granted_resource_abilities(
        handler, session, state, int(played_card_uid), my_pid,
        printed_skip=printed_skip)
    # The resource is now played — refresh the turn player's options so the
    # second shard no longer highlights.
    if (not is_shards_of_fate and not resource_choice_ability and
            state.get("phase") in (_ge.ETurnPhases.FirstMainPhase,
                                    _ge.ETurnPhases.SecondMainPhase)):
        # The persisted stack is a compatibility mirror and can retain
        # scheduler entries after the typed RulesPort chain has settled.
        # Let pvp_push_main_phase_options inspect the authoritative chain so
        # it can send either refreshed main-phase choices or response-window
        # choices; otherwise the client may retain a stale second resource.
        pvp_push_main_phase_options(session, state)
    champ_map = state.get("champ_map", {})

    # Push card events + resource/threshold/charge/PlayerUpdated for BOTH
    # players in one packet each.
    trigger_events = list(trigger_game.events)
    trigger_card_defs = dict(trigger_game.card_defs)
    for pid in pids:
        h = player_handlers.get(pid)
        if not h:
            continue
        is_me = (pid == my_pid)
        pl_uid = my_uid if is_me else opp_uid
        other_uid = opp_uid if is_me else my_uid
        g = _ge.Game(int(session.session_id), pl_uid, other_uid)
        _pvp_populate_game_state(
            g, state, pid, pids[1] if pid == pids[0] else pids[0])
        scid = _ge.SessionCardId(_ge.UID(int(played_card_uid)))
        # Real health/resource values from the PvP state (a bare Game defaults
        # to 20/20 and 0/0, which made every client show its own champion
        # gain 1 health and wiped the resource bar).
        g.player_health = int(state.get(f"hp_{pid}", 20))
        g.ai_health = int(state.get(f"hp_{pids[1] if pid == pids[0] else pids[0]}", 20))
        g.player_resources = int(state.get(f"res_{pid}", 0))
        g.player_total_resources = int(state.get(f"res_total_{pid}", 0))
        g.ai_resources = int(state.get(f"res_{pids[1] if pid == pids[0] else pids[0]}", 0))
        g.ai_total_resources = int(state.get(f"res_total_{pids[1] if pid == pids[0] else pids[0]}", 0))
        g.player_charges = int(state.get(f"chg_{pid}", 0))
        g.ai_charges = int(state.get(f"chg_{pids[1] if pid == pids[0] else pids[0]}", 0))

        # Rebuild the instance definition so the client retains any current
        # ability list (including a granted Gain-a-charge ability).
        _rtpl, rct, _rn, rcost, ratk, rdef, _rgem = \
            handler._card_full_data(g, scid, crow[0])
        g.push_card_updated(scid, my_uid, _ECardCollections.PlayedResources,
                            rct, template_id=_rtpl, cost=rcost,
                            attack=ratk, defense=rdef, nulling=False)
        g.push_resource_card_played(scid, my_uid, free=False)
        my_uid_p = _ge.UID.make(244, my_pid)
        # Current + total resource pool display.
        if current_grant:
            ev_cur = _ge.PlayerCurrentResourcePoolChangedSessionEventArgs()
            ev_cur.player_id = my_uid_p
            ev_cur.operation = 1
            ev_cur.delta = current_grant
            ev_cur.new_value = int(state.get(f"res_{my_pid}", 0))
            g._push(ev_cur)
        if max_grant:
            ev_tot = _ge.PlayerTotalResourcePoolChangedSessionEventArgs()
            ev_tot.player_id = my_uid_p
            ev_tot.operation = 1
            ev_tot.delta = max_grant
            ev_tot.new_value = int(state.get(f"res_total_{my_pid}", 0))
            g._push(ev_tot)
        # Threshold gems for every colour the resource granted.
        for flag, amount in threshold_grants:
            ev_th = _ge.PlayerResourceThresholdChangedSessionEventArgs()
            ev_th.player_id = my_uid_p
            ev_th.color = flag
            ev_th.operation = 1
            ev_th.delta = int(amount)
            ev_th.new_value = int(thresh.get(flag, 0))
            g._push(ev_th)
        # Champion charge generated by the resource's BOM.
        ev_chg = _ge.ChampionChargePointsChangedSessionEventArgs()
        ev_chg.player_id = my_uid_p
        ev_chg.operation = 1
        ev_chg.delta = charge_delta
        ev_chg.new_value = int(state.get(f"chg_{my_pid}", 0))
        g._push(ev_chg)
        if charge_trigger_game:
            for trigger_event in charge_trigger_game.events:
                g._push(trigger_event)
        for threshold_trigger_game in threshold_trigger_games:
            for trigger_event in threshold_trigger_game.events:
                g._push(trigger_event)
        for resource_event in resource_ability_events:
            g._push(resource_event)
        for trigger_event in trigger_events:
            g._push(trigger_event)
        g.card_defs.update(trigger_card_defs)
        # PlayerUpdated for both — health / charges / resources.
        for target_pid in pids:
            target_uid = _ge.UID.make(244, target_pid)
            cu = int(champ_map.get(str(target_pid), 0))
            champ_scid = _ge.SessionCardId(_ge.UID(cu)) if cu else None
            g.push_player_updated(target_uid, champ_id=champ_scid)

        if g.events:
            try:
                _cls2 = [getattr(type(_e), "CLASS_ID", 0) for _e in g.events]
                log_req(f"    PvP resource-audit -> pid {pid}: "
                        f"classes={_cls2}")
            except Exception as _e2:
                log_req(f"    PvP resource-audit error: {_e2}")
            pkt = g.make_network_packet(pl_uid)
            dw = encode_datawrapper(0, 3055, compress_gzip(encode_sync_event(pkt)), 1,
                                     client_session_guid(h))
            h.scnt += 1
            h.send({"issuer": f"0.0.0.0.ServiceGameSession.246.{session.session_id}.{h.scnt}",
                    "target": "ServiceGameSession", "instance": str(session.server_id),
                    "reqid": 0, "c": 0, "conh": 0, "sid": h.sid}, dw)
            log_req(f"    PvP resource: pushed to pid {pid}")

    # Some clients receive a later phase/options packet after the resource
    # packet.  That packet can contain PlayerUpdated events built from a bare
    # Game and overwrite the just-applied charge/resource values in the HUD.
    # Send one final authoritative player-state snapshot after all resource
    # events so the last PlayerUpdated values are the durable PvP state.
    champ_map = state.get("champ_map", {})
    for pid in pids:
        refresh_handler = player_handlers.get(pid)
        if not refresh_handler:
            continue
        opponent_pid = pids[1] if pid == pids[0] else pids[0]
        player_uid = _ge.UID.make(244, int(pid))
        opponent_uid = _ge.UID.make(244, int(opponent_pid))
        refresh_game = _ge.Game(
            int(session.session_id), player_uid, opponent_uid)
        _pvp_populate_game_state(
            refresh_game, state, int(pid), int(opponent_pid))
        refresh_game.push_player_updated(
            player_uid,
            champ_id=_ge.SessionCardId(
                _ge.UID(int(champ_map.get(str(pid), 0)))))
        refresh_game.push_player_updated(
            opponent_uid,
            champ_id=_ge.SessionCardId(
                _ge.UID(int(champ_map.get(str(opponent_pid), 0)))))
        _send_pvp_packet(
            refresh_handler, session, refresh_game, player_uid,
            "resource-state-refresh")
    log_req("    PvP resource: final player-state refresh pushed")
    if (charge_trigger_game and charge_trigger_game.events
            and state.get("stack")):
        # The resource packet above contains the charge event and the
        # triggered ability entry.  Give the opponent the first response
        # window, matching permanent/spell plays already on the PvP chain.
        _pvp_offer_trigger_response(session, state, my_pid)
        return True
    if resource_choice_ability:
        # Resource events must arrive before the built-in choice picker. The
        # printed ability creates private Choosing-zone cards and the shared
        # prompt helper sends the class-23 activation request to the owner.
        state["priority_pid"] = my_pid
        pvp_save_state(session, state)
        owner_handler = player_handlers.get(my_pid) or handler
        prompt_game = _ge.Game(int(session.session_id), my_uid, opp_uid)
        _pvp_populate_game_state(prompt_game, state, my_pid, opp_pid)
        prompt_view = _pvp_fra_view(state, my_pid, opp_pid)
        _pvp_resolve_ability(
            owner_handler, prompt_game, session, prompt_view, my_uid, opp_uid,
            resource_choice_ability, int(played_card_uid), my_pid,
            target_map={})
        state["stack"] = prompt_view.get("stack") or []
        state["stack_player_passed"] = False
        state["stack_ai_passed"] = False
        _pvp_sync_view_to_state(state, prompt_view, my_pid, opp_pid)
        # The prompt helper persists its private pending state while the
        # resolver is running. Preserve those markers when copying the FRA
        # view back into the authoritative PvP state.
        persisted = pvp_load_state(session) or {}
        for pending_key in ("pending_choice", "resolution_paused"):
            if persisted.get(pending_key):
                state[pending_key] = persisted[pending_key]
        pvp_save_state(session, state)
        if state.get("pending_choice"):
            log_req(f"    PvP resource choice: awaiting picker for pid "
                    f"{my_pid}")
            return True
        pending_charge_amount = _take_pending_gain_charge_amount(
            state, my_pid)
        if pending_charge_amount:
            charge_trigger_game = _pvp_gain_charge_trigger_game(
                owner_handler, session, state, my_pid,
                amount=pending_charge_amount)
            if charge_trigger_game and charge_trigger_game.events:
                _pvp_send_same_events(
                    session, charge_trigger_game, my_uid, opp_uid)
                if state.get("stack"):
                    _pvp_offer_trigger_response(session, state, my_pid)
                    return True
    if is_shards_of_fate:
        # Resolve the resource's printed parent once. Its authored child
        # ability owns the class-39 deck target and threshold effect.
        state["priority_pid"] = my_pid
        pvp_save_state(session, state)
        prompt_game = _ge.Game(int(session.session_id), my_uid, opp_uid)
        _pvp_populate_game_state(prompt_game, state, my_pid, opp_pid)
        prompt_view = _pvp_fra_view(state, my_pid, opp_pid)
        owner_handler = player_handlers.get(my_pid) or handler
        from rules_port.resources import resolve_printed_resource_abilities
        ability_logs = resolve_printed_resource_abilities(
            prompt_game, session, _db, owner_handler, my_uid, opp_uid,
            prompt_view, int(played_card_uid), my_pid)
        if ability_logs:
            log_req("    PvP Shards of Fate ability: " +
                    "; ".join(ability_logs))
        state["stack"] = prompt_view.get("stack") or []
        state["stack_player_passed"] = False
        state["stack_ai_passed"] = False
        _pvp_sync_view_to_state(state, prompt_view, my_pid, opp_pid)
        persisted = pvp_load_state(session) or {}
        for pending_key in (
                "pending_deck_search", "pending_choice", "pending_trigger",
                "pending_conversation"):
            if persisted.get(pending_key):
                state[pending_key] = persisted[pending_key]
        if prompt_view.get("resolution_paused") or persisted.get(
                "resolution_paused"):
            state["resolution_paused"] = True
        pvp_save_state(session, state)
        if state.get("pending_deck_search"):
            log_req(f"    PvP Shards of Fate: awaiting threshold choice "
                    f"for pid {my_pid}")
            return True

        # No picker remains, so a charge trigger deferred above can now be
        # put on the shared PvP chain and offered to the opponent.
        pending_charge_amount = _take_pending_gain_charge_amount(
            state, my_pid)
        if pending_charge_amount:
            charge_trigger_game = _pvp_gain_charge_trigger_game(
                handler, session, state, my_pid,
                amount=pending_charge_amount)
            if charge_trigger_game and charge_trigger_game.events:
                _pvp_send_same_events(
                    session, charge_trigger_game, my_uid, opp_uid)
                if state.get("stack"):
                    _pvp_offer_trigger_response(session, state, my_pid)
                    return True

    # The client clears its LOCAL greenlight after playing a card
    # (BattleStatePlayCard.LoseGreenLight) — the server must re-grant
    # priority to the turn player or nobody can act/pass afterwards.
    turn_h = player_handlers.get(my_pid)
    if turn_h:
        gg = _ge.Game(int(session.session_id), my_uid, opp_uid)
        gg.push_green_light(my_uid, _ge.EPriorityContext.Normal)
        _send_pvp_packet(turn_h, session, gg, my_uid, "greenlight-after-play")
    state["priority_pid"] = my_pid
    pvp_save_state(session, state)
    return True




def _pvp_resolve_choice(handler, session, inner_bytes, my_pid,
                        typed_payload=None):
    """Resolve a private ChooseAndPlay choice and resume its parent BOM."""
    from rules_port.choice_effects import (
        extract_card_uids, _play_choice_card, _resolve_choice_card_abilities)
    state = pvp_load_state(session) or {}
    pending = state.get("pending_choice")
    if not pending:
        return False
    selected = extract_card_uids(inner_bytes)
    if not selected and isinstance(typed_payload, dict):
        activation = typed_payload.get("activation_data")
        target_map = (activation.get("target_map")
                      if isinstance(activation, dict) else None)
        if isinstance(target_map, dict):
            for target in target_map.values():
                values = target if isinstance(target, (list, tuple, set)) else (target,)
                for value in values:
                    try:
                        uid = int(value)
                    except (TypeError, ValueError):
                        continue
                    if (uid & 0xFF) == 1:
                        selected.append(uid)
    legal = {int(uid) for uid in pending.get("choice_uids", [])}
    chosen_uid = next((uid for uid in reversed(selected) if int(uid) in legal),
                      None)
    owner_id = int(pending.get("owner_id", 0))
    log_req(f"    PvP choice parse: selected={[hex(int(u)) for u in selected]} "
            f"legal={[hex(int(u)) for u in legal]} chosen="
            f"{hex(int(chosen_uid)) if chosen_uid else None}")
    if owner_id != int(my_pid) or chosen_uid is None:
        log_req(f"    PvP choice answer invalid: pid={my_pid} "
                f"chosen={chosen_uid} owner={owner_id}")
        return True

    pids = db_game_session_pids(session.session_id)
    if len(pids) < 2:
        return True
    opponent_id = next(pid for pid in pids if int(pid) != owner_id)
    pl_t = _ge.UID.make(244, owner_id)
    ai_t = _ge.UID.make(244, opponent_id)
    state.pop("pending_choice", None)
    state.pop("resolution_paused", None)
    choice_zone_target = pending.get("kind") == "choice_zone_target"
    choice_zone_copy = pending.get("kind") == "choice_zone_copy"
    if choice_zone_copy:
        # Keep the selected original in Choosing while the child ability
        # copies it to hand; the remaining generated options are discarded
        # only after the copy has resolved.
        state["selected_choice_uid"] = int(chosen_uid)
    view = _pvp_fra_view(state, owner_id, opponent_id)
    view.pop("pending_choice", None)
    view.pop("resolution_paused", None)
    if choice_zone_copy:
        view["choice_copy_to_hand"] = True
    g = _ge.Game(int(session.session_id), pl_t, ai_t)
    _pvp_populate_game_state(g, state, owner_id, opponent_id)
    from rules_port.context import EffectContext
    choice_context = EffectContext.from_rules_port(
        g, session, _db, handler, pl_t, ai_t, view,
        "choice", ability=None)
    if choice_zone_target:
        # The authored child target opened the picker (e.g. Corinth's charge
        # power, "a card in the choice zone").  Resolve that child against the
        # selected token, then resume the enclosing ability so its later
        # effect groups still run.  Mirrors the PvE choice_zone_target
        # continuation; the selected card is only targeted, never "played".
        continuation = pending.get("continuation") or {}
        child_guid = str(continuation.get("ability_guid") or
                         pending.get("ability_guid") or "").lower()
        child_source = int(continuation.get(
            "source_uid", pending.get("source_uid", 0)) or 0)
        child_owner = int(continuation.get("owner_id", owner_id) or owner_id)
        child_targets = {int(key): value for key, value in
                         (continuation.get("target_map") or {}).items()}
        child_targets[int(continuation.get("target_index", 0) or 0)] = \
            int(chosen_uid)
        log_req("    PvP choice_zone_target DEBUG: "
                f"child={child_guid} child_resume="
                f"{continuation.get('resume_effect_order')} "
                f"parent={pending.get('parent', {}).get('ability_guid')} "
                f"parent_resume="
                f"{pending.get('parent', {}).get('resume_effect_order')}",
                level="DEBUG")
        _pvp_resolve_ability(
            handler, g, session, view, pl_t, ai_t, child_guid,
            child_source, child_owner, target_map=child_targets,
            variables=continuation.get("variables") or {},
            resume_from_order=int(
                continuation.get("resume_effect_order", 0) or 0))
        parent = pending.get("parent") or {}
        parent_guid = str(parent.get("ability_guid") or "").lower()
        if parent_guid and not _ability_has_effects_from(
                parent_guid, parent.get("resume_effect_order", 0)):
            # The picker was the parent's last effect, so the parent has
            # nothing left to run.  Mark its chain item complete so the chain
            # finishes it; resuming it from order 0 would reopen the picker.
            parent_guid = ""
        if parent_guid and not state.get("pending_choice"):
            _pvp_resolve_ability(
                handler, g, session, view, pl_t, ai_t, parent_guid,
                parent.get("source_uid"),
                int(parent.get("owner_id", owner_id) or owner_id),
                target_map={int(key): value for key, value in
                            (parent.get("target_map") or {}).items()},
                variables=parent.get("variables") or {},
                resume_from_order=int(
                    parent.get("resume_effect_order", 0) or 0))
    else:
        if not choice_zone_copy and not _play_choice_card(
                choice_context, chosen_uid, owner_id):
            log_req(f"    PvP choice card no longer selectable: {chosen_uid}")
            state["pending_choice"] = pending
            state["resolution_paused"] = True
            pvp_save_state(session, state)
            return True
        # The charge-power choices are templates to copy, not cards whose
        # printed abilities should be cast while answering the picker.
        # Resolving those abilities here can require unrelated targets (for
        # example GrantAbility) and abort the parent before its copy-to-hand
        # effect runs.
        if not choice_zone_copy:
            _resolve_choice_card_abilities(
                choice_context, chosen_uid, pending.get("source_uid"),
                owner_id)

        target_map = {int(key): value for key, value in
                      (pending.get("target_map") or {}).items()}
        _pvp_resolve_ability(
            handler, g, session, view, pl_t, ai_t,
            pending["ability_guid"], pending.get("source_uid"), owner_id,
            target_map=target_map, variables=pending.get("variables") or {},
            resume_from_order=int(pending.get("resume_effect_order", 0)))
    if choice_zone_copy:
        view.pop("choice_copy_to_hand", None)
        from rules_port.choice_effects import _clear_choice_zone
        _clear_choice_zone(choice_context)
        state.pop("selected_choice_uid", None)
    if not state.get("pending_choice") and not state.get("resolution_paused"):
        # The picker finished the chain item it paused.  Hand its real chain
        # instance id to the completion marker so the next pass finishes the
        # item instead of re-running its printed effects from the start.
        completed = int(state.pop("paused_chain_instance_id", 0) or 0)
        if completed:
            state["completed_chain_instance_id"] = completed
    state["stack"] = view.get("stack") or []
    state["stack_player_passed"] = False
    state["stack_ai_passed"] = False
    _pvp_sync_view_to_state(state, view, owner_id, opponent_id)
    charge_trigger_game = None
    pending_charge_amount = (
        _take_pending_gain_charge_amount(state, owner_id)
        if not view.get("pending_choice") else 0)
    if pending_charge_amount:
        charge_trigger_game = _pvp_gain_charge_trigger_game(
            handler, session, state, owner_id,
            amount=pending_charge_amount)
        if charge_trigger_game:
            for trigger_event in charge_trigger_game.events:
                g._push(trigger_event)
    pvp_save_state(session, state)
    _pvp_send_same_events(session, g, pl_t, ai_t)

    if state.get("pending_choice"):
        # The resumed second DoubleChoice prompt was sent privately by the
        # shared prompt helper. Only the selected card's public move was sent
        # above; leave priority in the picker until the next answer.
        log_req(f"    PvP choice selected: {hex(int(chosen_uid))}; "
                "second choice pending")
        return True

    # The chooser answered; clear the opponent's "opponent is choosing" state.
    _pvp_push_waiting_on(session, None)

    if charge_trigger_game and state.get("stack"):
        _pvp_offer_trigger_response(session, state, owner_id)
        return True

    g2 = _ge.Game(int(session.session_id), pl_t, ai_t)
    g2.push_chain_empty()
    state["priority_pid"] = int(state.get("turn_pid") or owner_id)
    pvp_save_state(session, state)
    turn_pid = int(state["priority_pid"])
    turn_handler = player_handlers.get(turn_pid)
    if turn_handler is not None:
        turn_uid = _ge.UID.make(244, turn_pid)
        other_uid = _ge.UID.make(244, opponent_id if turn_pid == owner_id
                                  else owner_id)
        g2 = _ge.Game(int(session.session_id), turn_uid, other_uid)
        g2.push_green_light(turn_uid, _ge.EPriorityContext.Normal)
        _send_pvp_packet(turn_handler, session, g2, turn_uid,
                         "greenlight-after-choice")
    if state.get("phase") in (_ge.ETurnPhases.FirstMainPhase,
                               _ge.ETurnPhases.SecondMainPhase):
        pvp_push_main_phase_options(session, state)
    log_req(f"    PvP choice selected: {hex(int(chosen_uid))}; "
            "choice sequence complete")
    return True








def _pvp_resolve_matching_target(handler, session, inner_bytes, my_pid,
                                 typed_payload=None):
    """Resolve a PvP deck target whose typed effect keeps it in the deck.

    This is the PvP counterpart of the FRA continuation: Scheme's selected
    action is never moved to hand, all picker candidates are hidden again,
    and the child BOM is resumed with the selected TargetMap before priority
    is returned to the active player.  A SourceRevealed picker (Oakhenge)
    shares the same continuation boundary; its child effect owns the move of
    the chosen card and the return of every other revealed card.
    """
    import struct

    state = pvp_load_state(session) or {}
    pend = state.pop("pending_deck_search", None)
    if not pend:
        return False
    state.pop("resolution_paused", None)
    chosen_uid = None
    if isinstance(inner_bytes, bytes):
        for m_du in re.finditer(
                rb'm_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});',
                inner_bytes):
            try:
                uid64 = struct.unpack('<Q', bytes.fromhex(m_du.group(1).decode()))[0]
                if (uid64 & 0xFF) == 1:
                    chosen_uid = int(uid64)
            except Exception:
                continue
    continuation = pend.get("continuation") or {}
    typed_uid = _pvp_typed_picker_uid(
        typed_payload, continuation.get("target_index", 0))
    if typed_uid is not None:
        chosen_uid = typed_uid
    candidates = [int(uid) for uid in (pend.get("candidates") or [])]
    owner_id = int(pend.get("owner_id", my_pid) or my_pid)
    pids = db_game_session_pids(session.session_id)
    if (not chosen_uid or chosen_uid not in candidates or
            owner_id not in pids or int(my_pid) != owner_id or len(pids) < 2):
        pvp_save_state(session, state)
        log_req(f"    PvP matching-target invalid choice: "
                f"chosen={chosen_uid} candidates={candidates}")
        handler._push_transaction_ack(session)
        return True

    opp_pid = next(pid for pid in pids if int(pid) != owner_id)
    pl_t = _ge.UID.make(244, owner_id)
    ai_t = _ge.UID.make(244, opp_pid)
    view = _pvp_fra_view(state, owner_id, opp_pid)
    view.pop("pending_deck_search", None)
    view.pop("resolution_paused", None)
    g = _ge.Game(int(session.session_id), pl_t, ai_t)
    _pvp_populate_game_state(g, state, owner_id, opp_pid)
    # A SourceRevealed picker presents the whole reveal (the cards that were
    # not legal targets arrive as AdditionalTargets), so all of them go back
    # to a hidden deck projection; a plain deck search only presents its
    # candidates.
    handler._hide_candidates_to_deck(
        g, session, pl_t, ai_t,
        [int(uid) for uid in (pend.get("revealed_cards") or candidates)])

    child_guid = str(continuation.get("ability_guid") or "").lower()
    child_source = int(continuation.get("source_uid") or 0)
    child_owner = int(continuation.get("owner_id", owner_id) or owner_id)
    child_targets = {
        int(key): value for key, value in
        (continuation.get("target_map") or {}).items()
    }
    child_targets[int(continuation.get("target_index", 0))] = int(chosen_uid)
    _pvp_resolve_ability(
        handler, g, session, view, pl_t, ai_t,
        child_guid, child_source, child_owner,
        target_map=child_targets,
        variables=continuation.get("variables") or {},
        event_tac=continuation.get("event_tac"))

    from rules_port.resolution import resume_ability_continuation_parents
    resume_ability_continuation_parents(
        handler, g, session, _db, pl_t, ai_t, view, continuation)

    state["stack"] = view.get("stack") or []
    state["stack_player_passed"] = False
    state["stack_ai_passed"] = False
    _pvp_sync_view_to_state(state, view, owner_id, opp_pid)
    for key in (
            "resolution_paused", "paused_chain_instance_id",
            "completed_chain_instance_id",
            "rules_port_suspended_chain_continuation"):
        if key in view:
            state[key] = view[key]
        else:
            state.pop(key, None)
    persisted = pvp_load_state(session) or {}
    for key in ("pending_trigger", "pending_deck_search", "pending_choice",
                "pending_conversation"):
        if persisted.get(key):
            state[key] = persisted[key]
    pending_charge_amount = _take_pending_gain_charge_amount(
        state, owner_id)
    if pending_charge_amount:
        charge_trigger_game = _pvp_gain_charge_trigger_game(
            handler, session, state, owner_id,
            amount=pending_charge_amount)
        if charge_trigger_game:
            for trigger_event in charge_trigger_game.events:
                g._push(trigger_event)
    pvp_save_state(session, state)
    _pvp_send_same_events(session, g, pl_t, ai_t)

    if (state.get("pending_trigger") or state.get("pending_deck_search") or
            state.get("pending_choice") or state.get("pending_conversation")):
        handler._push_transaction_ack(session)
        return True

    if state.get("stack"):
        _pvp_offer_trigger_response(session, state, owner_id)
    else:
        state["priority_pid"] = int(state.get("turn_pid") or owner_id)
        state["stack_passed"] = []
        pvp_save_state(session, state)
        priority_pid = int(state["priority_pid"])
        priority_handler = player_handlers.get(priority_pid)
        if priority_handler:
            priority_uid = _ge.UID.make(244, priority_pid)
            other_uid = _ge.UID.make(
                244, next(pid for pid in pids if int(pid) != priority_pid))
            resume = _ge.Game(int(session.session_id), priority_uid, other_uid)
            resume.push_chain_empty()
            resume.push_green_light(priority_uid, _ge.EPriorityContext.Normal)
            _send_pvp_packet(priority_handler, session, resume, priority_uid,
                             "greenlight-after-matching-target")
        if state.get("phase") in (_ge.ETurnPhases.FirstMainPhase,
                                   _ge.ETurnPhases.SecondMainPhase):
            pvp_push_main_phase_options(session, state)
    handler._push_transaction_ack(session)
    log_req(f"    PvP matching target chosen: {hex(int(chosen_uid))}; "
            "created matching cards and restored priority")
    return True


def _pvp_resolve_revealed_choice(handler, session, inner_bytes, my_pid,
                                typed_payload=None):
    """Resolve an explicit choice from a SourceRevealed prompt.

    A typed continuation resumes the child ability that owns the move
    (Oakhenge's "put a revealed troop into your hand, then put the remaining
    cards into your deck") through the shared deck-target continuation; a
    legacy prompt without one applies the pick directly.
    """
    import re as _re
    import struct as _st
    pids = db_game_session_pids(session.session_id)
    if len(pids) < 2:
        return False
    state = pvp_load_state(session) or {}
    pend = state.get("pending_deck_search") or {}
    if pend.get("kind") not in ("revealed_troop", "revealed_card"):
        return False
    if pend.get("continuation"):
        return _pvp_resolve_matching_target(
            handler, session, inner_bytes, my_pid,
            typed_payload=typed_payload)
    state.pop("pending_deck_search", None)
    chosen_uid = None
    if isinstance(inner_bytes, bytes):
        for m_du in _re.finditer(
                rb'm_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});',
                inner_bytes):
            try:
                uid64 = _st.unpack('<Q', bytes.fromhex(m_du.group(1).decode()))[0]
                if (uid64 & 0xFF) == 1:
                    chosen_uid = int(uid64)
            except Exception:
                continue
    continuation = pend.get("continuation") or {}
    typed_uid = _pvp_typed_picker_uid(
        typed_payload, continuation.get("target_index", 0))
    if typed_uid is not None:
        chosen_uid = typed_uid
    candidates = [int(c) for c in (pend.get("candidates") or [])]
    revealed = [int(c) for c in (pend.get("revealed_cards") or [])]
    owner_id = int(pend.get("owner_id", my_pid))
    if not chosen_uid or chosen_uid not in candidates:
        pvp_save_state(session, state)
        log_req(f"    PvP revealed choice invalid: chosen={chosen_uid} "
                f"candidates={candidates}")
        return True
    opp_pid = pids[0] if pids[1] == owner_id else pids[1]
    pl_t = _ge.UID.make(244, owner_id)
    ai_t = _ge.UID.make(244, opp_pid)
    g = _ge.Game(int(session.session_id), pl_t, ai_t)
    _pvp_populate_game_state(g, state, owner_id, opp_pid)
    from rules_port.context import EffectContext
    from rules_port.deck_effects import move_deck_card_to_hand
    state["_rules_port_attached"] = True
    move_deck_card_to_hand(EffectContext.from_rules_port(
        g, session, _db, handler, pl_t, ai_t, state,
        "move_deck_card_to_hand", ability=None), chosen_uid, owner_id)
    remaining = [cu for cu in revealed if cu != chosen_uid]
    if remaining:
        from pvp_db import db_randomly_insert_deck_cards
        db_randomly_insert_deck_cards(
            session.session_id, owner_id, remaining, connection=_db)
    handler._hide_candidates_to_deck(
        g, session, pl_t, ai_t,
        remaining)
    _pvp_send_same_events(session, g, pl_t, ai_t)
    pvp_save_state(session, state)
    log_req(f"    PvP revealed choice resolved: {hex(chosen_uid)} -> hand "
            f"(pid {owner_id})")
    state["priority_pid"] = owner_id
    pvp_save_state(session, state)
    chooser = player_handlers.get(owner_id)
    if chooser:
        g2 = _ge.Game(int(session.session_id), pl_t, ai_t)
        _pvp_populate_game_state(g2, state, owner_id, opp_pid)
        g2.push_green_light(pl_t, _ge.EPriorityContext.Normal)
        _send_pvp_packet(chooser, session, g2, pl_t,
                         "greenlight-after-revealed-choice")
    if state.get("phase") in (_ge.ETurnPhases.FirstMainPhase,
                               _ge.ETurnPhases.SecondMainPhase):
        pvp_push_main_phase_options(session, state)
    return True


def _pvp_resolve_shard_choice(handler, session, inner_bytes, my_pid,
                             typed_payload=None):
    """Resolve the PvP Shards of Fate deck choice.

    The selected Standard resource remains in the deck; only its threshold is
    granted.  The candidate cards are then hidden back into the deck for both
    clients and the turn player's normal priority is restored.
    """
    import struct
    state = pvp_load_state(session) or {}
    pend = state.pop("pending_deck_search", None)
    if not pend:
        return False
    chosen_uid = None
    if isinstance(inner_bytes, bytes):
        for m_du in re.finditer(
                rb'm_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});',
                inner_bytes):
            try:
                uid64 = struct.unpack('<Q', bytes.fromhex(m_du.group(1).decode()))[0]
                if (uid64 & 0xFF) == 1:
                    chosen_uid = int(uid64)
            except Exception:
                continue
    typed_uid = _pvp_typed_picker_uid(typed_payload)
    if typed_uid is not None:
        chosen_uid = typed_uid
    candidates = [int(c) for c in (pend.get("candidates") or [])]
    if not chosen_uid or chosen_uid not in candidates:
        pvp_save_state(session, state)
        log_req(f"    PvP Shards of Fate invalid choice: "
                f"chosen={chosen_uid} candidates={candidates}")
        return True

    owner_id = int(pend["owner_id"])
    pids = db_game_session_pids(session.session_id)
    if len(pids) < 2 or owner_id not in pids:
        pvp_save_state(session, state)
        return True
    opp_pid = [p for p in pids if p != owner_id][0]
    pl_t = _ge.UID.make(244, owner_id)
    ai_t = _ge.UID.make(244, opp_pid)
    g = _ge.Game(int(session.session_id), pl_t, ai_t)
    _pvp_populate_game_state(g, state, owner_id, opp_pid)

    from rules_port.resources import card_template_threshold_flags
    provided = card_template_threshold_flags(
        _db, session.session_id, int(chosen_uid))
    threshold_colors = sorted({
        int(flag) for flag in _ge.SHARD_TO_FLAG.values()
        if provided & int(flag)})
    from pvp_db import db_randomly_insert_deck_cards
    db_randomly_insert_deck_cards(
        session.session_id, owner_id, pend.get("candidates") or [])
    thresh_key = f"thresh_{owner_id}"
    thresh = dict(state.get(thresh_key) or {})
    for flag in threshold_colors:
        cur = thresh.get(flag)
        if cur is None:
            cur = thresh.get(str(flag), 0)
        thresh[flag] = int(cur or 0) + 1
        state[thresh_key] = thresh
        g.player_threshold = dict(thresh)
        ev_th = _ge.PlayerResourceThresholdChangedSessionEventArgs()
        ev_th.player_id = pl_t
        ev_th.color = flag
        ev_th.operation = 1
        ev_th.delta = 1
        ev_th.new_value = int(thresh[flag])
        g._push(ev_th)
        threshold_trigger_game = _pvp_gain_threshold_trigger_game(
            handler, session, state, owner_id, flag)
        if threshold_trigger_game:
            for trigger_event in threshold_trigger_game.events:
                g._push(trigger_event)
    if threshold_colors:
        state[thresh_key] = thresh
        g.player_threshold = dict(thresh)

    # The selected card is not moved into hand or PlayedResources.  All
    # presented candidates, including the selected one, return face-down to
    # the deck in the clients' views.
    handler._hide_candidates_to_deck(g, session, pl_t, ai_t, candidates)
    champ_map = state.get("champ_map", {})
    for target_pid in pids:
        target_uid = _ge.UID.make(244, target_pid)
        cu = int(champ_map.get(str(target_pid), 0))
        champ_scid = _ge.SessionCardId(_ge.UID(cu)) if cu else None
        g.push_player_updated(target_uid, champ_id=champ_scid)
    pending_charge_amount = _take_pending_gain_charge_amount(
        state, owner_id)
    if pending_charge_amount:
        charge_trigger_game = _pvp_gain_charge_trigger_game(
            handler, session, state, owner_id,
            amount=pending_charge_amount)
        if charge_trigger_game:
            for trigger_event in charge_trigger_game.events:
                g._push(trigger_event)
    _pvp_send_same_events(session, g, pl_t, ai_t)
    pvp_save_state(session, state)
    log_req(f"    PvP Shards of Fate resolved: gained threshold flags "
            f"{threshold_colors} "
            f"(chosen {hex(int(chosen_uid))}, pid {owner_id})")

    if state.get("stack"):
        _pvp_offer_trigger_response(session, state, owner_id)
        return True

    state["priority_pid"] = owner_id
    pvp_save_state(session, state)
    turn_h = player_handlers.get(owner_id)
    if turn_h:
        g2 = _ge.Game(int(session.session_id), pl_t, ai_t)
        _pvp_populate_game_state(g2, state, owner_id, opp_pid)
        g2.push_green_light(pl_t, _ge.EPriorityContext.Normal)
        _send_pvp_packet(turn_h, session, g2, pl_t,
                         "greenlight-after-shard")
    if state.get("phase") in (_ge.ETurnPhases.FirstMainPhase,
                              _ge.ETurnPhases.SecondMainPhase):
        pvp_push_main_phase_options(session, state)
    return True
























# Campaign battles cannot be rejoined yet: the rebuilt board is incomplete (see the
# campaign reconnect notes). Set True to re-enable the offer, FindSession resume and join.
CAMPAIGN_REJOIN_ENABLED = False

















def _pvp_resolve_stack_item(session, state, handler, my_pid):
    """Resolve the top persisted chain item when no native host is attached.

    Reached only when this tourney session has no RulesPort host — the setup
    hand-off, or a wrapper that could not build one.  Card, spell and
    triggered items go through the same shared seam the native path uses, so
    the two can no longer drift; a manual/champion ability keeps its
    PvP-specific projection.  Returns True when an item was resolved.
    """
    from rules_port import lifecycle as _be
    from rules_port.actions import AbilityResolutionState
    from rules_port.chain_items import resolve_chain_item
    from rules_port.session import projected_ability_ignores_chain

    stack = state.get("stack") or []
    if not stack:
        return False
    kind = str((stack[-1] or {}).get("kind") or "")
    if kind == "ability":
        # The ability projection owns its own mirror strip.
        return bool(_pvp_resolve_ability_item(
            session, state, handler, my_pid, dict(stack[-1])))
    item = dict(_be.stack_pop(state) or {})
    if not item:
        return False
    ability = SimpleNamespace(
        instance_id=int(item.get("instance_id", 1) or 1),
        descriptor=item,
        ignores_chain=projected_ability_ignores_chain(item))
    owner_id = int(my_pid)
    source_uid = int(item.get("source_uid") or 0)
    if source_uid:
        owner_row = db_card_basic(session.session_id, source_uid, conn=_db)
        if owner_row:
            owner_id = int(owner_row[1])
    pids = _pvp_session_pids(session, state) or [
        owner_id, int(my_pid) if int(my_pid) != owner_id else owner_id]
    if len(pids) < 2:
        return False
    opponent_id = next((int(pid) for pid in pids
                        if int(pid) != owner_id), owner_id)
    host = _PvpChainHost(handler, session, state, owner_id, opponent_id)
    resolution = resolve_chain_item(
        host, None, session, _db, ability,
        _ge.UID.make(244, owner_id), _ge.UID.make(244, opponent_id))
    return resolution is AbilityResolutionState.COMPLETED


def _pvp_resolve_ability_item(session, state, handler, my_pid, item):
    """Resolve one manual/champion ability chain item in PvP.

    Card, spell and triggered item resolution is shared with Practice/PvE
    through ``rules_port.chain_items``; a manual/champion ability carries
    PvP-only projections (charge/spell-power cost events before its picker
    opens, the champion-health delta after it resolves, and the deferred
    class-23 discard prompt for a nested discard leaf), so its projection
    stays here.  The item, its mirror and the chain presentation are owned by
    the caller; this function only projects the effect and the priority
    hand-off.
    """
    if not item:
        return False
    from rules_port import lifecycle as _be

    # The persisted stack is only the reconnect/wire mirror; drop this item's
    # entry by identity.  Leaving it behind re-queued the resolved item as the
    # "next" chain item.
    item = dict(item)
    stack = state.get("stack") or []
    if stack and int(stack[-1].get("instance_id", -1)) == int(
            item.get("instance_id", -2)):
        stack.pop()
    pids = db_game_session_pids(session.session_id)
    if len(pids) < 2:
        pids = _pvp_session_pids(session, state)
    if len(pids) < 2:
        return False
    kind = "ability"
    ag = str(item.get("ability_guid") or "")
    instance_id = int(item.get("instance_id", 1))
    src_uid = int(item.get("source_uid") or 0)
    # Owner of the chain item's source card (triggers belong to their card).
    owner_id = my_pid
    if src_uid:
        orow = db_card_basic(session.session_id, src_uid)
        if orow:
            owner_id = orow[1]
    opp_pid = pids[0] if pids[1] == owner_id else pids[1]
    pl_t = _ge.UID.make(244, owner_id)
    ai_t = _ge.UID.make(244, opp_pid)
    view = _pvp_fra_view(state, owner_id, opp_pid)
    view["resolving_owner_id"] = owner_id
    view["resolving_source_uid"] = src_uid
    view["player_mod_target"] = item.get("target_uid")
    view["player_spell_target"] = item.get("target_uid")
    g = _ge.Game(int(session.session_id), pl_t, ai_t)
    _pvp_populate_game_state(g, state, owner_id, opp_pid)
    if kind == "ability":
        # Champion charge/spell power on the chain (e.g. Dimmid's Lifedrain):
        # resolve its BOM through the same resolver the PvE "ability" path
        # uses.  The source is the champion card — not a game_cards row — so
        # resolving_source_uid / resolving_owner_id carry the owner's pid.
        ag = str(item.get("ability_guid") or "")
        if ag.lower() == "f2d6797b-1a24-4c3d-9239-a27a2e0de0ff":
            from rules_port.tunneling import surface_source_is_underground
            if not surface_source_is_underground(
                    _db, session, item.get("source_uid")):
                log_req(f"    Ignoring stale PvP tunneling Surface "
                        f"source={item.get('source_uid')}")
                ag = ""
        if ag:
            try:
                from rules_port.resolution import resolve_port_ability
                view["player_mod_target"] = item.get("target_uid")
                view["player_spell_target"] = item.get("target_uid")
                view["resolving_ability"] = ag
                view["resolving_source_uid"] = src_uid
                view["resolving_owner_id"] = owner_id
                # Pay-cost HUD events (charge/resource/spell) must reach the
                # client BEFORE any picker this BOM opens.  The client
                # re-evaluates ability options when the charge changes and
                # would otherwise close the chooser it just opened (the picker
                # showed for ~1s then vanished).  Mirrors the client's own
                # ordering where the cost is paid at activation, before the
                # ability resolves.
                cost_game = _ge.Game(int(session.session_id), pl_t, ai_t)
                _pvp_populate_game_state(cost_game, state, owner_id, opp_pid)
                _pvp_emit_paid_cost_events(
                    cost_game, state, handler=handler, session=session,
                    db=_db, player_uid=pl_t, ai_uid=ai_t)
                if cost_game.events:
                    _pvp_send_same_events(session, cost_game, pl_t, ai_t)
                ability_event_start = len(g.events)
                ability_player_health_before = int(view.get("player_health", 20))
                ability_ai_health_before = int(view.get("ai_health", 20))
                activation_data = item.get("activation_data") or {}
                target_map = (dict(activation_data.get("target_map") or {})
                              if isinstance(activation_data, dict) else {})
                if not target_map and item.get("target_uid") is not None:
                    from gamedata import ability_graph, DEFAULT_RECORD_STORE
                    graph = ability_graph(DEFAULT_RECORD_STORE, ag)
                    if graph is not None:
                        for index, spec in enumerate(graph.targets):
                            if spec.requires_input:
                                target_map[index] = int(item["target_uid"])
                                break
                # Cards exhausted/sacrificed to pay the cost feed "for each
                # troop exhausted this way" (Dread Factory, Construction
                # Plans); the same descriptor fields the shared chain-item
                # resolver passes in Practice/PvE.
                resolve_port_ability(
                    handler, g, session, _db, pl_t, ai_t, view, ag,
                    src_uid, owner_id, target_map=target_map,
                    variables=(activation_data.get("variables") or {}
                               if isinstance(activation_data, dict) else {}),
                    cost_target_map=(
                        activation_data.get("cost_target_map") or {}
                        if isinstance(activation_data, dict) else {}),
                    instance_id=instance_id)
                # PortAbilityResolver adopts the shared authoritative PvP
                # checkpoint before walking effects. Rebuild this FRA-shaped
                # view from that updated checkpoint so the stale pre-resolution
                # health cannot overwrite damage when we sync below.
                resolved_state = pvp_load_state(session)
                if isinstance(resolved_state, dict) and resolved_state:
                    if resolved_state is not state:
                        state.clear()
                        state.update(resolved_state)
                    view = _pvp_fra_view(state, owner_id, opp_pid)
                ability_player_health_after = int(
                    view.get("player_health", ability_player_health_before))
                ability_ai_health_after = int(
                    view.get("ai_health", ability_ai_health_before))
                g.player_health = ability_player_health_after
                g.ai_health = ability_ai_health_after
                g.push_champion_health_changed_if_missing(
                    pl_t, ability_player_health_before,
                    ability_player_health_after, since=ability_event_start)
                g.push_champion_health_changed_if_missing(
                    ai_t, ability_ai_health_before,
                    ability_ai_health_after, since=ability_event_start)
            except Exception as e:
                import traceback
                log_req(f"    PvP chain ability resolve error: {e}")
                traceback.print_exc()
            view.pop("player_mod_target", None)
            view.pop("player_spell_target", None)
            view.pop("resolving_ability", None)
            view.pop("resolving_source_uid", None)
    # Persist health + remaining stack (the view aliases state's stack, but
    # copy back explicitly so nothing is lost).
    state["stack"] = view.get("stack") or []
    state["stack_player_passed"] = False
    state["stack_ai_passed"] = False
    state["stack_passed"] = []
    state["_next_instance_id"] = view.get("_next_instance_id", 1)
    if view.get("player_health") is not None:
        state[f"hp_{owner_id}"] = int(view["player_health"])
    if view.get("ai_health") is not None:
        state[f"hp_{opp_pid}"] = int(view["ai_health"])
    _pvp_sync_view_to_state(state, view, owner_id, opp_pid)
    # Interactive BOM prompts persist their continuation through the handler
    # (the PvP prompt packet is private to the choosing client).  Reload just
    # those continuation markers so this resolver does not immediately send
    # the ordinary chain-empty/priority packet over the picker.
    persisted = pvp_load_state(session) or {}
    for _pending_key in ("pending_trigger", "pending_deck_search",
                         "pending_choice", "pending_conversation"):
        if persisted.get(_pending_key):
            state[_pending_key] = persisted[_pending_key]

    # Champion abilities can contain a nested DiscardCard leaf just like
    # troop abilities (Blue Sparrow draws, then chooses and discards). The
    # BOM resolver performs the draw, but the discard is a client class-23
    # follow-up rather than an ordinary effect target. Schedule the shared
    # metadata-derived PvP picker after the resolution events are sent.
    pending_discard = False
    discard_pid = int(owner_id)
    if ag:
        discard_prompt = _pvp_discard_prompt_data(ag)
        if discard_prompt:
            # "Target/each opposing champion discards a card" is answered by
            # that champion's controller, not the activator.
            item_activation = item.get("activation_data") or {}
            discard_pid = _pvp_discard_prompt_pid(
                session, state, ag, owner_id, opp_pid, src_uid,
                target_uid=item.get("target_uid"),
                target_map=(item_activation.get("target_map")
                            if isinstance(item_activation, dict) else None))
        hand_exists = db_hand_exists(
            session.session_id, discard_pid, conn=_db)
        if discard_prompt and hand_exists:
            state["pending_discard_ability"] = discard_prompt[0]
            state["pending_discard_target_template"] = discard_prompt[1]
            state["pending_discard_source_uid"] = int(src_uid)
            state["pending_discard_pid"] = int(discard_pid)
            state["priority_pid"] = int(discard_pid)
            pending_discard = True
    chain_empty = _be.stack_empty(state)
    pending_revealed_choice = (
        (state.get("pending_deck_search") or {}).get("kind")
        in ("revealed_troop", "revealed_card"))
    pending_trigger = bool(state.get("pending_trigger"))
    pending_choice = bool(state.get("pending_choice"))
    pending_conversation = bool(state.get("pending_conversation"))
    # Shards of Fate / Adaptable Infusion Device sends a private class-39
    # deck picker to the controller.  Its picker packet already owns the
    # next green-light; sending the normal chain-empty/options packet here
    # tears down that UI and leaves PvP priority stranded.
    pending_deck_search = bool(state.get("pending_deck_search"))
    # State-based actions are checked again after any start-of-turn trigger
    # chain resolves.  A tunneled card that reached its threshold while that
    # chain was on top must still surface before normal phase priority returns.
    if (chain_empty and not (pending_revealed_choice or pending_deck_search
                             or pending_trigger or pending_choice
                             or pending_conversation or pending_discard)):
        from rules_port.tunneling import queue_surfaces
        turn_pid = int(state.get("turn_pid") or 0)
        tunnel_handler = player_handlers.get(turn_pid) or handler
        turn_pt = _ge.UID.make(244, turn_pid)
        turn_opp = _ge.UID.make(
            244, pids[1] if turn_pid == pids[0] else pids[0])
        state["_rules_port_attached"] = True
        from rules_port.context import EffectContext
        tunnel_context = EffectContext.from_rules_port(
            g, session, _db, tunnel_handler, turn_pt, turn_opp, state,
            "", ability=None)
        tunnel_surfaces = queue_surfaces(tunnel_context, turn_pid)
        if tunnel_surfaces:
            chain_empty = False
            pvp_save_state(session, state)
            log_req(f"    PvP state-based tunneling: queued surfaces "
                    f"{tunnel_surfaces}")
    _pvp_log_stack(state, "resolve")
    if chain_empty and not (pending_revealed_choice or pending_deck_search
                            or pending_trigger or pending_choice or
                            pending_conversation):
        g.push_chain_empty()
    pvp_save_state(session, state)
    # State-based deaths: when the stack empties, troops at <=0 effective
    # defense die (spell/trigger -X/-X, etc.) — mirrors PvE
    # _resolve_stack_item (hconnect ~3087).  The death events go into the SAME
    # stream so both clients see the graveyard move + Deathcry.
    if chain_empty:
        try:
            from rules_port.context import EffectContext
            from rules_port.death_effects import state_based_deaths
            view["_rules_port_attached"] = True
            state_based_deaths(EffectContext.from_rules_port(
                g, session, _db, handler, pl_t, ai_t, view,
                "state_based_death", ability=None))
        except Exception as e:
            log_req(f"    PvP state-based deaths error: {e}")
        # Copy health back again (deaths can heal via triggers).
        if view.get("player_health") is not None:
            state[f"hp_{owner_id}"] = int(view["player_health"])
        if view.get("ai_health") is not None:
            state[f"hp_{opp_pid}"] = int(view["ai_health"])
        state["stack"] = view.get("stack") or []
        _pvp_sync_view_to_state(state, view, owner_id, opp_pid)
        pvp_save_state(session, state)
    # The chain item is only removed once the ability FULLY resolves, and an
    # IgnoresChain ability is never added to the client's chain at all
    # (UIBattle.OnAbilityPushedOnChain plays a card event instead).  Emitting
    # TopOfChainResolved/RemovedTopOfChain for it — or while the BOM is paused
    # on an interactive prompt — would tear down the picker the client just
    # opened.  Mirrors the client: ResolveTopOfChainAction removes the item
    # only on COMPLETED, and only for a real chain entry.
    ignores_chain = False
    if kind == "ability":
        try:
            from rules_port.session import projected_ability_ignores_chain
            ignores_chain = projected_ability_ignores_chain(item)
        except Exception:
            ignores_chain = False
    if not ignores_chain and not any(state.get(key) for key in (
            "pending_choice", "pending_trigger", "pending_deck_search",
            "pending_conversation", "pending_discard_ability",
            "resolution_paused")):
        g.push_top_of_chain_resolved(instance_id)
        g.push_removed_top_of_chain(instance_id)
    _pvp_emit_paid_cost_events(
        g, state, handler=handler, session=session, db=_db,
        player_uid=pl_t, ai_uid=ai_t)
    _pvp_send_same_events(session, g, pl_t, ai_t)
    if chain_empty and pending_discard:
        discard_other = (int(opp_pid) if discard_pid == int(owner_id)
                         else int(owner_id))
        _pvp_push_discard_prompt(
            session, state, discard_pid, discard_other, int(src_uid))
        pvp_save_state(session, state)
        log_req(f"    PvP chain paused for discard choice: {ag[:8]} "
                f"source={hex(int(src_uid))}")
        return True
    if chain_empty and pending_revealed_choice:
        # The private picker packet was sent by _prompt_revealed_choice.  Do
        # not follow it with the ordinary chain-empty greenlight/options
        # packet; that would replace the client's ConfigureAbility state
        # before the player can answer it.
        pvp_save_state(session, state)
        log_req("    PvP chain paused for revealed-card choice")
        return True
    if chain_empty and pending_deck_search:
        # _prompt_deck_search already pushed the private picker and greenlight
        # to the choosing player.  Wait for SetAbilityActivationDataTransaction
        # before announcing chain-empty or re-pushing phase options.
        pvp_save_state(session, state)
        log_req("    PvP chain paused for deck-search choice")
        return True
    if chain_empty and pending_trigger:
        # _prompt_trigger_targets already sent the private PlayerOptionList,
        # class-39 activation request, and green light.  A normal chain-empty
        # refresh here would immediately replace BattleStateConfigureAbility,
        # which is why the target cursor appeared briefly and then vanished.
        pvp_save_state(session, state)
        log_req("    PvP chain paused for triggered target choice")
        return True
    if chain_empty and pending_choice:
        # The private picker packet was sent by _prompt_choice_cards. Do not
        # replace it with a chain-empty/normal-priority packet.
        pvp_save_state(session, state)
        log_req("    PvP chain paused for card choice")
        return True
    if chain_empty and pending_conversation:
        # Class 55 was sent privately to the controller by the conversation
        # effect.  Do not replace it with chain-empty/priority events until
        # EncounterModDialogTransaction resumes the BOM.
        pvp_save_state(session, state)
        log_req("    PvP chain paused for encounter conversation")
        return True
    # Chain damage can kill a champion (e.g. burn / Lifedrain) — end the game
    # properly instead of continuing into the next priority handoff.
    if _pvp_check_game_end(session, state):
        return True
    if chain_empty:
        turn_pid = state.get("turn_pid")
        turn_h = player_handlers.get(turn_pid)
        state["priority_pid"] = turn_pid
        pvp_save_state(session, state)
        if turn_h:
            turn_pt = _ge.UID.make(244, turn_pid)
            turn_opp = _ge.UID.make(
                244, pids[1] if turn_pid == pids[0] else pids[0])
            gg = _ge.Game(int(session.session_id), turn_pt, turn_opp)
            gg.push_green_light(turn_pt, _ge.EPriorityContext.Normal)
            _pvp_push_turn_phase_with_elapsed(
                gg, int(state.get("phase", 0)), turn_pt, turn_pt,
                priority_elapsed_ticks(state, turn_pid) // 10_000_000)
            _send_pvp_packet(turn_h, session, gg, turn_pt, "chain-empty")
        # Mirror PvE (hconnect ~8880): re-announce the current phase + push the
        # phase-appropriate options so the client rebuilds its state and the
        # stale "Continue to Second Main Phase <Card>" pass-button tail clears.
        _cur_phase = int(state.get("phase", 0))
        if _cur_phase in (_ge.ETurnPhases.FirstMainPhase,
                          _ge.ETurnPhases.SecondMainPhase):
            pvp_push_main_phase_options(session, state)
        elif _cur_phase == _ge.ETurnPhases.DeclareAttack:
            pvp_push_attack_options(session, state)
        elif _cur_phase == _ge.ETurnPhases.DeclareDefense:
            pvp_push_blocker_options(session, state)
        elif _cur_phase not in (3, 4, 5, 6, 7, 8, 9):
            pvp_push_phase_options(session, state, pid=turn_pid)
        log_req(f"    PvP chain resolved ({kind}) — empty, priority to "
                f"turn player {turn_pid}, phase {_cur_phase} options re-pushed")
    else:
        other_pid = pids[1] if my_pid == pids[0] else pids[0]
        state["priority_pid"] = other_pid
        pvp_save_state(session, state)
        if _pvp_auto_pass_chain_priority(session, state, other_pid):
            return True
        other_h = player_handlers.get(other_pid)
        if other_h:
            other_pt = _ge.UID.make(244, other_pid)
            other_opp = _ge.UID.make(
                244, pids[1] if other_pid == pids[0] else pids[0])
            gg = _ge.Game(int(session.session_id), other_pt, other_opp)
            gg.push_green_light(other_pt, _ge.EPriorityContext.ResolveTopOfChain)
            _send_pvp_packet(other_h, session, gg, other_pt, "chain-next")
        log_req(f"    PvP chain resolved ({kind}) — "
                f"{len(state.get('stack') or [])} item(s) left, priority to "
                f"{other_pid}")
    return True






def _pvp_play_troop(handler, session, played_card_uid, my_pid, inner_bytes,
                    typed_payload=None, native_port=None):
    """Play a non-resource permanent in PvP: hand -> CastSpells -> Warzone,
    fire enters-play triggers, and push the events to BOTH players.  Returns
    True when handled."""
    from gamedata import PlayPlan
    pids = db_game_session_pids(session.session_id)
    if len(pids) < 2:
        return False
    opp_pid = pids[0] if pids[1] == my_pid else pids[1]
    crow = db_card_play_info(
        session.session_id, played_card_uid, conn=_db)
    if not crow:
        return False
    tpl_guid, card_type, card_name = crow[0], crow[1], crow[2]
    card_abilities = crow[5]
    ctype_num = _ge.card_type_from_db(card_type)
    is_permanent = bool(ctype_num & (_ge.ECardTypes.Troop |
                                     _ge.ECardTypes.Artifact |
                                     _ge.ECardTypes.Constant))
    if not is_permanent:
        log_req(f"    PvP: {card_name} is an action — not yet resolved")
        return False
    scid = _ge.SessionCardId(_ge.UID(int(played_card_uid)))
    my_uid = _ge.UID.make(244, my_pid)
    opp_uid = _ge.UID.make(244, opp_pid)
    state = pvp_load_state(session) or {}
    source_location = _pvp_card_source_location(
        session, state, my_pid, int(played_card_uid))
    if source_location is None:
        log_req(f"    PvP REJECTED permanent {card_name}: card is not in a playable zone")
        return True
    prior_card_zone = db_card_owner_zone_state(
        session.session_id, int(played_card_uid), conn=_db)
    try:
        play_plan = PlayPlan.from_card(
            _RECORD_STORE, tpl_guid, source_uid=int(played_card_uid),
            owner_id=my_pid)
    except KeyError as exc:
        log_req(f"    PvP REJECTED permanent {card_name}: {exc}")
        return True
    # Decode the one client TargetMap before paying any resource or card cost.
    # Cost targets and effect targets share the wire list, so the PlayPlan
    # partitions them using the authored target templates.
    targets = _pvp_typed_card_targets(typed_payload, played_card_uid)
    if typed_payload is None:
        try:
            targets = handler._extract_transaction_targets(
                inner_bytes, int(played_card_uid))
        except Exception:
            targets = []
    champ_map = state.get("champ_map") or {}
    champ_targets = []
    for cpid in state.get("pids") or pids:
        cuid = int(champ_map.get(str(cpid), 0))
        if cuid:
            champ_targets.append((
                cuid, int(cpid), "Champion",
                int(state.get(f"hp_{cpid}", 20))))
    cost_selection = _pvp_select_card_play_costs(
        handler, session, state, play_plan, int(played_card_uid), my_pid,
        targets, champ_targets)
    if cost_selection is None:
        log_req(f"    PvP REJECTED spell {card_name}: incomplete/illegal "
                "card cost target")
        return True
    cost_selections, cost_uids = cost_selection
    ability_data = ((typed_payload or {}).get("ability_data") or ())
    ability_data = ((typed_payload or {}).get("ability_data") or ())
    activations, _cost_target_map = play_plan.activation_bundle(
        targets, ability_data=ability_data)
    ability_cost_selection = handler._ability_card_play_cost_selections(
        session, play_plan, int(played_card_uid), activations,
        owner_id=my_pid, champions=champ_targets, battle_state=state)
    if ability_cost_selection is None:
        log_req(f"    PvP REJECTED play {card_name}: invalid ability cost target")
        return True
    ability_cost_selections, ability_cost_uids = ability_cost_selection
    cost_selections.extend(ability_cost_selections)
    cost_uids.update(ability_cost_uids)
    # Defense-in-depth cost check: the client's options are the normal gate,
    # but don't let a drag play an unaffordable card and go negative.
    _trow = db_template_by_guid(tpl_guid)
    cost = play_plan.cost.resource
    if not cost and _trow and not play_plan.cost.variable:
        cost = _trow[3] or 0
    # Effective cost (static cost modifiers) — charge what the client showed.
    try:
        from rules_port.static_rules import effective_cost as _ec
        cost = _ec(_db, session.session_id,
                   _pvp_fra_view(state, my_pid, opp_pid), int(played_card_uid))
    except Exception:
        pass
    available = int(state.get(f"res_{my_pid}", 0))
    if native_port is None and cost > available:
        log_req(f"    PvP REJECTED play {card_name}: cost {cost} > "
                f"resources {available}")
        pvp_push_main_phase_options(session, state)
        return True
    selected_cost_map = {
        int(spec.get("index", index)): tuple(selected)
        for index, (spec, selected) in enumerate(cost_selections)
        if selected and not spec.get("ability_guid")
    }
    plan_errors = (play_plan.validate(
        activations=activations, cost_target_map=selected_cost_map)
                   if native_port is None else ())
    if plan_errors:
        log_req(f"    PvP REJECTED play {card_name}: activation validation: "
                f"{'; '.join(plan_errors)}")
        pvp_push_main_phase_options(session, state)
        return True
    # Pay the cost FIRST (before any resolution) so the resource pool is
    # correct regardless of triggers firing.
    from rules_port.card_transactions import apply_card_play_for_player
    card_transition = apply_card_play_for_player(
        _db, session.session_id, state, int(played_card_uid), my_pid, cost,
        expected_location=source_location)
    if card_transition is None:
        log_req(f"    PvP REJECTED play {card_name}: card left its playable zone")
        return True
    # "You played N non-resource cards this turn" (ChampionCardsCastCondition)
    # reads the cast statistics the PvE play path records with the same call.
    from rules_port.cast_stats import record_card_cast
    record_card_cast(state, my_pid, resource=False,
                     card_uid=int(played_card_uid), card_type=card_type,
                     resource_cost=cost)
    view = _pvp_fra_view(state, my_pid, opp_pid)
    # Move the card onto the CHAIN (CastSpells visual) — mirroring how spells
    # are cast.  Troops/artifacts/constants do NOT resolve instantly: they stay
    # on the stack so the opponent can respond (e.g. Countermagic) before they
    # resolve to the warzone via the both-pass chain flow (see
    # _pvp_resolve_stack_item).
    g = _ge.Game(int(session.session_id), my_uid, opp_uid)
    _pvp_populate_game_state(g, state, my_pid, opp_pid)
    _pvp_apply_card_play_costs(
        handler, g, session, state, my_uid, opp_uid, cost_selections,
        int(played_card_uid), scrounge=any(
            getattr(ability, "is_scrounge", False)
            for ability in play_plan.cast_abilities))
    # Register the CardDef FIRST so every CardUpdated carries the full stats
    # (mirrors the spell path).
    _tpl, ct, _n, cost2, atk, def_, gems = \
        handler._card_full_data(g, scid, tpl_guid)
    g.push_card_updated(scid, my_uid, _ge.ECardCollections.CastSpells,
                        ctype_num, template_id=tpl_guid, cost=cost2,
                        attack=atk, defense=def_, gems=gems)
    # Push the permanent onto the chain as a "troop" item.
    inst_id = port_queue_stack_item(state, {
        "kind": "troop", "source_uid": int(played_card_uid),
        "ability_guids": [], "target_uid": None,
        "x_cost": 0,
    })
    # The card's presence on the chain: the client populates ChainView ONLY
    # from AbilityPushedOnChain (GoChainView has no CastSpells zone mapping, and
    # resources are the only cards moved there explicitly).  OnAbilityPushedOnChain
    # is gated on TemplateManager.Abilities.ContainsKey(AbilityTemplateId), so the
    # template id MUST be a valid client ability template — a card template GUID
    # fails the gate and the chain stays empty for BOTH players.  Use the troop's
    # OWN first ability GUID when it has one (a real AbilityTemplate), else the
    # client's built-in PlayCardAbilityTemplateId (always registered, semantically
    # "cast this card").  The card rendered is still the ACTUAL card instance
    # (SourceCardId -> non-clone AddAbilityInstanceWithSource path), with its real
    # stats/buffs — the ability id is only the client's chain-render key.
    import json as _chj
    _chain_tpl = _ge.PLAY_CARD_ABILITY_TEMPLATE_ID
    try:
        _tabs = _chj.loads(card_abilities) if card_abilities else []
        if _tabs:
            _chain_tpl = str(_tabs[0]).lower()
    except Exception:
        pass
    g.push_ability_on_chain(scid, _ge.ResourceId.from_str(_chain_tpl),
                            ability_instance_id=inst_id)
    state["stack"] = view.get("stack") or []
    state["stack_player_passed"] = False
    state["stack_ai_passed"] = False
    state["stack_passed"] = []
    from rules_port import chain_items as _chain_items
    trigger_host = _PvpChainHost(handler, session, state, my_pid, opp_pid)
    _chain_items.dispatch_card_zone_transition(
        trigger_host, session, g, state, my_uid, opp_uid,
        int(played_card_uid), my_pid, source_location, "CastSpells",
        int(prior_card_zone[2] or 0) if prior_card_zone else 0)
    _chain_items.dispatch_card_cast(
        trigger_host, session, g, state, my_uid, opp_uid,
        int(played_card_uid), my_pid)
    pvp_save_state(session, state)
    # Reflect the cost paid on BOTH clients (objective UIDs).
    g.player_resources = int(state.get(f"res_{my_pid}", 0))
    g.player_total_resources = int(state.get(f"res_total_{my_pid}", 0))
    g.ai_resources = int(state.get(f"res_{opp_pid}", 0))
    g.ai_total_resources = int(state.get(f"res_total_{opp_pid}", 0))
    ev_spent = _ge.PlayerCurrentResourcePoolChangedSessionEventArgs()
    ev_spent.player_id = my_uid
    ev_spent.operation = 2
    ev_spent.delta = cost
    ev_spent.new_value = g.player_resources
    g._push(ev_spent)
    champ_map = state.get("champ_map") or {}
    for target_pid in pids:
        target_uid = _ge.UID.make(244, target_pid)
        cu = int(champ_map.get(str(target_pid), 0))
        champ_scid = _ge.SessionCardId(_ge.UID(cu)) if cu else None
        g.push_player_updated(target_uid, champ_id=champ_scid)
    _pvp_send_same_events(session, g, my_uid, opp_uid)
    if native_port is not None:
        native_port.queue_projected_chain(
            {"kind": "troop", "source_uid": int(played_card_uid),
             "ability_guids": [], "target_uid": None,
             "instance_id": int(inst_id), "x_cost": 0},
            my_pid, first_player_id=opp_uid)
        state["priority_pid"] = int(opp_pid)
        pvp_save_state(session, state)
        opp_h = player_handlers.get(opp_pid)
        if opp_h:
            response = _ge.Game(int(session.session_id), opp_uid, my_uid)
            response.push_green_light(
                opp_uid, _ge.EPriorityContext.ResolveTopOfChain)
            _send_pvp_packet(opp_h, session, response, opp_uid,
                             "native-troop-chain-opp")
            pvp_push_phase_options(session, state, pid=opp_pid)
        caster_h = player_handlers.get(my_pid)
        if caster_h:
            response = _ge.Game(int(session.session_id), my_uid, opp_uid)
            response.push_green_light(
                opp_uid, _ge.EPriorityContext.ResolveTopOfChain)
            _send_pvp_packet(caster_h, session, response, my_uid,
                             "native-troop-chain-caster")
        session._rules_port_mutation_emitted = True
        return True
    log_req(f"    PvP troop play: {card_name} by pid {my_pid} (paid {cost}, "
            f"stack={len(state.get('stack') or [])} item(s))")
    _pvp_log_stack(state, f"troop-play {card_name}")
    # The card is on the stack.  Priority passes to the OPPONENT FIRST (they
    # get the response window / Resolve on the chain item), then back to the
    # caster — mirroring the PvP flow where the non-actor responds to a cast
    # before the actor resolves it.  The both-pass stack rule in route_pvp_pass
    # hands priority to the other player after the first pass; seeding the
    # opponent as priority here means THEY click Resolve first.
    opp_h = player_handlers.get(opp_pid)
    if opp_h and not pvp_player_auto_passes(state, opp_pid):
        gg = _ge.Game(int(session.session_id), opp_uid, my_uid)
        gg.push_green_light(opp_uid, _ge.EPriorityContext.ResolveTopOfChain)
        _send_pvp_packet(opp_h, session, gg, opp_uid, "troop-on-stack-opp")
        # Offer the opponent their quick actions so they can respond first.
        try:
            pvp_push_phase_options(session, state, pid=opp_pid)
        except Exception as _e:
            log_req(f"    PvP troop-on-stack opp options error: {_e}")
    # Explicitly clear the caster's local green-light as well.  The play-card
    # UI normally does this optimistically, but a second authoritative packet
    # prevents the activating client from retaining a stale priority state
    # when the opponent is the first responder.
    caster_h = player_handlers.get(my_pid)
    if caster_h:
        caster_game = _ge.Game(int(session.session_id), my_uid, opp_uid)
        caster_game.push_green_light(opp_uid, _ge.EPriorityContext.ResolveTopOfChain)
        _send_pvp_packet(caster_h, session, caster_game, my_uid,
                         "troop-on-stack-caster-lost")
    state["priority_pid"] = opp_pid
    pvp_save_state(session, state)
    _pvp_log_stack(state, f"troop-play-{card_name}-opp-first")
    _pvp_auto_pass_chain_priority(session, state, opp_pid)
    return True




def _pvp_offer_trigger_response(session, state, caster_pid):
    """Offer the opponent priority for a newly-created trigger chain."""
    _pvp_offer_opponent_response(session, state, caster_pid)
    pids = db_game_session_pids(session.session_id)
    if len(pids) < 2:
        return
    opp_pid = pids[0] if pids[1] == caster_pid else pids[1]
    caster_h = player_handlers.get(caster_pid)
    if caster_h:
        caster_uid = _ge.UID.make(244, caster_pid)
        opp_uid = _ge.UID.make(244, opp_pid)
        g = _ge.Game(int(session.session_id), caster_uid, opp_uid)
        g.push_green_light(opp_uid, _ge.EPriorityContext.ResolveTopOfChain)
        _send_pvp_packet(caster_h, session, g, caster_uid,
                         "trigger-on-stack-caster-lost")
    _pvp_auto_pass_chain_priority(session, state, opp_pid)


def _pvp_play_spell(handler, session, played_card_uid, my_pid, inner_bytes,
                    typed_payload=None, native_port=None):
    """Cast a BasicAction/QuickAction spell in PvP: hand -> CastSpells, push
    the spell onto the chain, then resolve its BOM when the chain resolves and
    send CastSpells -> Discard.  A player may cast a QuickAction any time they
    hold priority and can pay the cost.  Pushes events to BOTH players."""
    from gamedata import PlayPlan
    pids = db_game_session_pids(session.session_id)
    if len(pids) < 2:
        return False
    opp_pid = pids[0] if pids[1] == my_pid else pids[1]
    crow = db_card_play_info(
        session.session_id, played_card_uid, conn=_db)
    if not crow:
        return False
    tpl_guid, card_type, card_name, _current_grant, _max_grant, ab_json = crow
    try:
        play_plan = PlayPlan.from_card(
            _RECORD_STORE, tpl_guid, source_uid=int(played_card_uid),
            owner_id=my_pid)
    except KeyError as exc:
        log_req(f"    PvP REJECTED spell {card_name}: {exc}")
        return True
    scid = _ge.SessionCardId(_ge.UID(int(played_card_uid)))
    my_uid = _ge.UID.make(244, my_pid)
    opp_uid = _ge.UID.make(244, opp_pid)
    state = pvp_load_state(session) or {}
    source_location = _pvp_card_source_location(
        session, state, my_pid, int(played_card_uid))
    if source_location is None:
        log_req(f"    PvP REJECTED spell {card_name}: card is not in a playable zone")
        return True
    prior_card_zone = db_card_owner_zone_state(
        session.session_id, int(played_card_uid), conn=_db)
    targets = _pvp_typed_card_targets(typed_payload, played_card_uid)
    if typed_payload is None:
        try:
            targets = handler._extract_transaction_targets(
                inner_bytes, int(played_card_uid))
        except Exception:
            targets = []
    champ_map = state.get("champ_map") or {}
    champ_targets = []
    for cpid in state.get("pids") or pids:
        cuid = int(champ_map.get(str(cpid), 0))
        if cuid:
            champ_targets.append((
                cuid, int(cpid), "Champion",
                int(state.get(f"hp_{cpid}", 20))))
    cost_selection = _pvp_select_card_play_costs(
        handler, session, state, play_plan, int(played_card_uid), my_pid,
        targets, champ_targets)
    if cost_selection is None:
        log_req(f"    PvP REJECTED spell {card_name}: incomplete/illegal "
                "card cost target")
        return True
    cost_selections, cost_uids = cost_selection
    # Defense-in-depth cost check; pay FIRST so resources are right even if
    # the resolution is interrupted.
    _trow = db_template_by_guid(tpl_guid)
    cost = _trow[3] if _trow else 0
    # Effective cost (static cost modifiers) — charge what the client showed.
    try:
        from rules_port.static_rules import effective_cost as _ec
        cost = _ec(_db, session.session_id,
                   _pvp_fra_view(state, my_pid, opp_pid), int(played_card_uid))
    except Exception:
        pass
    # Read and validate the selected X before mutating resources.  The client
    # enforces this in AreXCostsComplete; the server must reject stale or
    # forged transactions by the same plan boundary.
    x_cost = max((int(item.get("x_cost", 0) or 0)
                  for item in (typed_payload or {}).get("ability_data", ())
                  if isinstance(item, dict)), default=0)
    if typed_payload is None:
        try:
            x_cost = max(0, int(handler._extract_int32_field(
                inner_bytes, "m_ResourceXCost") or 0))
        except Exception:
            x_cost = 0
    if play_plan.cost.variable:
        if x_cost < play_plan.cost.variable_minimum:
            log_req(f"    PvP REJECTED spell {card_name}: X={x_cost} below "
                    f"minimum {play_plan.cost.variable_minimum}")
            return True
    elif x_cost:
        log_req(f"    PvP REJECTED spell {card_name}: non-variable card has X={x_cost}")
        return True
    available = int(state.get(f"res_{my_pid}", 0))
    x_payment = x_cost * play_plan.cost.variable_multiplier
    if native_port is None and cost + x_payment > available:
        log_req(f"    PvP REJECTED spell {card_name}: cost {cost} > "
                f"resources {available}")
        pvp_push_main_phase_options(session, state)
        return True
    # Validate the complete current-record activation before paying or moving
    # the card.  Target and option failures must reject the play atomically.
    ability_data = ((typed_payload or {}).get("ability_data") or ())
    activations, _cost_target_map = play_plan.activation_bundle(
        targets, x_cost=x_cost, ability_data=ability_data)
    ability_cost_selection = handler._ability_card_play_cost_selections(
        session, play_plan, int(played_card_uid), activations,
        owner_id=my_pid, champions=champ_targets, battle_state=state)
    if ability_cost_selection is None:
        log_req(f"    PvP REJECTED spell {card_name}: invalid ability cost target")
        return True
    ability_cost_selections, ability_cost_uids = ability_cost_selection
    cost_selections.extend(ability_cost_selections)
    cost_uids.update(ability_cost_uids)
    selected_cost_map = {
        int(spec.get("index", index)): tuple(selected)
        for index, (spec, selected) in enumerate(cost_selections)
        if selected and not spec.get("ability_guid")
    }
    plan_errors = (play_plan.validate(
        variable_cost=x_cost, activations=activations,
        cost_target_map=selected_cost_map) if native_port is None else ())
    if plan_errors:
        log_req(f"    PvP REJECTED spell {card_name}: activation validation: "
                f"{'; '.join(plan_errors)}")
        pvp_push_main_phase_options(session, state)
        return True
    from rules_port.card_transactions import apply_card_play_for_player
    card_transition = apply_card_play_for_player(
        _db, session.session_id, state, int(played_card_uid), my_pid,
        cost + x_payment, expected_location=source_location)
    if card_transition is None:
        log_req(f"    PvP REJECTED spell {card_name}: card left its playable zone")
        return True
    from rules_port.cast_stats import record_card_cast
    record_card_cast(state, my_pid, resource=False,
                     card_uid=int(played_card_uid), card_type=card_type,
                     resource_cost=cost)
    if x_cost:
        log_req(f"    PvP spell {card_name}: X cost {x_cost} paid "
                f"(resources left {state.get(f'res_{my_pid}')})")
    view = _pvp_fra_view(state, my_pid, opp_pid)
    effect_targets = [uid for uid in targets if int(uid) not in cost_uids]
    target_uid = effect_targets[-1] if effect_targets else None
    view["player_spell_target"] = target_uid
    view["resolving_owner_id"] = my_pid
    view["resolving_source_uid"] = int(played_card_uid)
    g = _ge.Game(int(session.session_id), my_uid, opp_uid)
    _pvp_populate_game_state(g, state, my_pid, opp_pid)
    _pvp_apply_card_play_costs(
        handler, g, session, state, my_uid, opp_uid, cost_selections,
        int(played_card_uid), scrounge=any(
            getattr(ability, "is_scrounge", False)
            for ability in play_plan.cast_abilities))
    _tpl, ct, _n, cost2, atk, def_, gems = \
        handler._card_full_data(g, scid, tpl_guid)
    g.push_card_updated(scid, my_uid, _ge.ECardCollections.CastSpells, ct,
                        template_id=tpl_guid, cost=cost2, attack=atk,
                        defense=def_, gems=gems)
    g.push_spell_card_cast(scid, my_uid, free=False)
    from rules_port.card_transactions import automatic_instance_ability_guids
    ability_guids = automatic_instance_ability_guids(
        _db, session.session_id, int(played_card_uid),
        [ability.ability_guid for ability in play_plan.cast_abilities],
        in_play=False)
    played_from_hand = str(source_location).lower() == "hand"
    inst_id = port_queue_stack_item(state, {
        "kind": "spell", "source_uid": int(played_card_uid),
        "ability_guids": ability_guids, "target_uid": target_uid,
        "x_cost": x_cost, "played_from_hand": played_from_hand,
        "activations": {
            guid: activation.as_dict()
            for guid, activation in activations.items()
        },
    })
    # Chain entry: must carry a VALID client ability template (the client's
    # OnAbilityPushedOnChain is gated on TemplateManager.Abilities.ContainsKey).
    # Use the spell's first ability GUID (a real AbilityTemplate), else the
    # client built-in PlayCardAbilityTemplateId.
    _chain_tpl2 = (_ge.PLAY_CARD_ABILITY_TEMPLATE_ID
                   if not ability_guids else ability_guids[0])
    g.push_ability_on_chain(scid,
                            _ge.ResourceId.from_str(_chain_tpl2),
                            ability_instance_id=inst_id)
    state["stack"] = view.get("stack") or []
    state["stack_player_passed"] = False
    state["stack_ai_passed"] = False
    state["stack_passed"] = []
    from rules_port import chain_items as _chain_items
    trigger_host = _PvpChainHost(handler, session, state, my_pid, opp_pid)
    _chain_items.dispatch_card_zone_transition(
        trigger_host, session, g, state, my_uid, opp_uid,
        int(played_card_uid), my_pid, source_location, "CastSpells",
        int(prior_card_zone[2] or 0) if prior_card_zone else 0)
    _chain_items.dispatch_card_cast(
        trigger_host, session, g, state, my_uid, opp_uid,
        int(played_card_uid), my_pid)
    pvp_save_state(session, state)
    # Reflect the cost paid on BOTH clients' resource displays (the event
    # carries the caster's absolute UID, so each client renders "You spent"
    # vs "Opponent spent" from its own perspective).
    g.player_health = int(state.get(f"hp_{my_pid}", 20))
    g.ai_health = int(state.get(f"hp_{opp_pid}", 20))
    g.player_resources = int(state.get(f"res_{my_pid}", 0))
    g.player_total_resources = int(state.get(f"res_total_{my_pid}", 0))
    g.ai_resources = int(state.get(f"res_{opp_pid}", 0))
    g.ai_total_resources = int(state.get(f"res_total_{opp_pid}", 0))
    ev_spent = _ge.PlayerCurrentResourcePoolChangedSessionEventArgs()
    ev_spent.player_id = my_uid
    ev_spent.operation = 2
    ev_spent.delta = cost + x_payment
    ev_spent.new_value = g.player_resources
    g._push(ev_spent)
    champ_map = state.get("champ_map") or {}
    for _tpid in pids:
        _tuid = _ge.UID.make(244, _tpid)
        cu = int(champ_map.get(str(_tpid), 0))
        champ_scid = _ge.SessionCardId(_ge.UID(cu)) if cu else None
        g.push_player_updated(_tuid, champ_id=champ_scid)
    _pvp_send_same_events(session, g, my_uid, opp_uid)
    if native_port is not None:
        native_port.queue_projected_chain(
            {"kind": "spell", "source_uid": int(played_card_uid),
             "ability_guids": ability_guids,
             "target_uid": target_uid, "instance_id": int(inst_id),
             "x_cost": x_cost, "played_from_hand": played_from_hand,
             "activations": {
                 guid: activation.as_dict()
                 for guid, activation in activations.items()}},
            my_pid, first_player_id=opp_uid)
        state["priority_pid"] = int(opp_pid)
        pvp_save_state(session, state)
        opp_h = player_handlers.get(opp_pid)
        if opp_h:
            response = _ge.Game(int(session.session_id), opp_uid, my_uid)
            response.push_green_light(
                opp_uid, _ge.EPriorityContext.ResolveTopOfChain)
            _send_pvp_packet(opp_h, session, response, opp_uid,
                             "native-spell-chain-opp")
            pvp_push_phase_options(session, state, pid=opp_pid)
        caster_h = player_handlers.get(my_pid)
        if caster_h:
            response = _ge.Game(int(session.session_id), my_uid, opp_uid)
            response.push_green_light(
                opp_uid, _ge.EPriorityContext.ResolveTopOfChain)
            _send_pvp_packet(caster_h, session, response, my_uid,
                             "native-spell-chain-caster")
        session._rules_port_mutation_emitted = True
        return True
    log_req(f"    PvP spell cast: {card_name} by pid {my_pid} (paid {cost}, "
            f"target={hex(target_uid) if target_uid else None}, "
            f"stack={len(state.get('stack') or [])})")
    _pvp_log_stack(state, f"spell-cast {card_name}")
    # The card is on the stack.  Priority passes to the OPPONENT FIRST (they get
    # the response window / Resolve on the spell), then back to the caster —
    # mirroring the PvP flow where the non-actor responds to a cast first.
    try:
        opp_h = player_handlers.get(opp_pid)
        if opp_h and not pvp_player_auto_passes(state, opp_pid):
            gg = _ge.Game(int(session.session_id), opp_uid, my_uid)
            gg.push_green_light(opp_uid, _ge.EPriorityContext.ResolveTopOfChain)
            _send_pvp_packet(opp_h, session, gg, opp_uid, "chain-priority-opp")
            try:
                pvp_push_phase_options(session, state, pid=opp_pid)
            except Exception:
                pass
        state["priority_pid"] = opp_pid
        pvp_save_state(session, state)
        _pvp_log_stack(state, f"spell-cast-{card_name}-opp-first")
        _pvp_auto_pass_chain_priority(session, state, opp_pid)
    except Exception as e:
        # Never let a post-play refresh (greenlight/options) kill the session
        # thread and disconnect both clients — log and return True so the
        # transaction is acked and the game keeps running.
        import traceback
        log_req(f"    PvP spell post-refresh error: {e}")
        traceback.print_exc()
    return True






def _pvp_declare_attackers(handler, session, inner_bytes, my_pid,
                           declarations=None):
    """Record the turn player's declared attackers and push the combat
    listing to both players."""
    pids = db_game_session_pids(session.session_id)
    if len(pids) < 2:
        return False
    opp_pid = pids[0] if pids[1] == my_pid else pids[1]
    state = pvp_load_state(session) or {}
    if state.get("turn_pid") != my_pid:
        return False
    attacker_uids = []
    if declarations is not None:
        for _defender, declared in declarations:
            for attacker in declared or ():
                try:
                    attacker_uids.append(int(attacker))
                except (TypeError, ValueError):
                    continue
    elif isinstance(inner_bytes, bytes):
        for m_du in re.finditer(rb'm_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});',
                                inner_bytes):
            try:
                import struct as _st
                uid64 = _st.unpack('<Q', bytes.fromhex(m_du.group(1).decode()))[0]
                if (uid64 & 0xFF) == 1:
                    attacker_uids.append(int(uid64))
            except Exception:
                continue
    if declarations is None:
        # Legacy ingress has not crossed RulesPort validation, so still treat
        # the raw transaction as untrusted and enforce the authored combat
        # eligibility checks here.
        wz_rows = [
            (uid, card_state, attrs)
            for uid, card_state, _card_type, attrs in
            db_warzone_attack_option_rows(
                session.session_id, my_pid, conn=_db)
        ]
        from rules_port.static_rules import effective_attributes
        wz = set()
        for uid, cstate, attrs in wz_rows:
            cstate = int(cstate or 0)
            attrs = int(attrs or 0) | int(effective_attributes(
                _db, session.session_id, state, int(uid)) or 0)
            if ((cstate & (_ge.ECardStates.Tapped |
                           _ge.ECardStates.Attacking)) or
                    attrs & (_ge.ECardAttributes.CantAttack |
                             _ge.ECardAttributes.Defensive) or
                    not ((cstate & _ge.ECardStates.StartedATurnOnYourSide) or
                         attrs & _ge.ECardAttributes.Speed)):
                continue
            wz.add(int(uid))
        attacker_uids = [u for u in attacker_uids if u in wz]
    champ_map = state.get("champ_map") or {}
    my_champ = int(champ_map.get(str(my_pid), 0))
    opp_champ = int(champ_map.get(str(opp_pid), 0))
    # MERGE the manually-committed attackers with what's ALREADY declared in
    # state — which includes auto-declared ForceAttack troops (Savage Raider
    # "must attack") that the server pushed at DeclareAttack.  Without this
    # merge, a CommitTroopsToAttackTransaction carrying only the manually
    # selected UIDs overwrites the forced attacker, it vanishes, and combat is
    # skipped -> the forced troop never attacks / deals no damage.
    existing = {int(k): int(v)
                for k, v in (state.get("attackers") or {}).items()}
    merged = dict(existing)
    for u in attacker_uids:
        merged[u] = opp_champ
    attackers = list(merged.keys())
    state["attackers"] = {str(u): str(v) for u, v in merged.items()}
    pvp_save_state(session, state)
    my_uid = _ge.UID.make(244, my_pid)
    opp_uid = _ge.UID.make(244, opp_pid)
    # Mark each attacker Attacking|HasAttacked (+Tapped UNLESS Steadfast) in
    # the DB and build ONE objective event stream (AttackDeclared + CombatListing
    # + CardUpdated with the new state + "when this attacks" trigger events)
    # that goes to BOTH players — mirrors PvE _auto_declare_force_attackers /
    # the CommitTroopsToAttack handler.  Only the NEWLY committed attackers get
    # events here; auto-declared ForceAttack troops already got theirs when the
    # phase opened (in pvp_push_attack_options), so no duplicate push (mirrors
    # PvE: `new_attackers = [u for u in attackers if u not in existing]`).
    new_attackers = [u for u in attacker_uids if u not in existing]
    from pvp_db import db_card_set_attacking_state
    g = _ge.Game(int(session.session_id), my_uid, opp_uid)
    g.player_health = int(state.get(f"hp_{my_pid}", 20))
    g.ai_health = int(state.get(f"hp_{opp_pid}", 20))
    combats = []
    for i, u in enumerate(new_attackers):
        cid = _ge.CombatId(my_uid, i + 1)
        scid = _ge.SessionCardId(_ge.UID(u))
        g.push_attack_declared(cid, my_uid,
                               _ge.SessionCardId(_ge.UID(opp_champ)) if opp_champ
                               else _ge.SessionCardId(opp_uid), scid)
        trow = db_card_template_attrs_joined(session.session_id, int(u))
        tpl_guid = trow[0] if trow else None
        attrs = (trow[1] if trow and trow[1] else 0) | \
                (trow[2] if trow and trow[2] else 0)
        cstate = (_ge.ECardStates.Attacking |
                  _ge.ECardStates.HasAttacked)
        if not (attrs & _ge.ECardAttributes.Steadfast):
            cstate |= _ge.ECardStates.Tapped
        db_card_set_attacking_state(session.session_id, int(u), cstate)
        pushed_state = db_card_state_value(session.session_id, int(u))
        if not pushed_state:
            pushed_state = cstate
        handler._card_full_data(g, scid, tpl_guid)
        g.push_card_updated(scid, my_uid, _ge.ECardCollections.Warzone,
                            _ge.ECardTypes.Troop, template_id=tpl_guid,
                            state=pushed_state)
        cs = _ge.CombatSessionEventArgs()
        cs.player_id = my_uid
        cs.id = cid
        cs.attacker = scid
        cs.blockers = []
        combats.append(cs)
        # "When this attacks" triggers + Rage.
        view = _pvp_fra_view(state, my_pid, opp_pid)
        _pvp_dispatch_triggers(
            handler, g, session, view, my_uid, opp_uid,
            "CardAttackedEvent", int(u), my_pid)
        _pvp_dispatch_triggers(
            handler, g, session, view, my_uid, opp_uid,
            "CardAttackedOrBlockedEvent", int(u), my_pid)
        from rules_port.context import EffectContext
        from rules_port.combat_effects import apply_rage
        apply_rage(EffectContext.from_rules_port(
            g, session, _db, handler, my_uid, opp_uid, view,
            "", ability=None), int(u))
        # Persist any trigger/rage health changes.
        if view.get("player_health") is not None:
            state[f"hp_{my_pid}"] = int(view["player_health"])
        if view.get("ai_health") is not None:
            state[f"hp_{opp_pid}"] = int(view["ai_health"])
        _pvp_sync_view_to_state(state, view, my_pid, opp_pid)
        pvp_save_state(session, state)
    if attackers:
        # One champion-scoped event represents the whole declaration; do not
        # emit one event per attacker because metadata conditions consume the
        # authored NumAttackers TAC value.
        from rules_port.tac import _tac_attr_hash
        _pvp_dispatch_triggers(
            handler, g, session, state, my_uid, opp_uid,
            "CardsAttackedEvent", my_champ or int(my_uid.uid64), my_pid,
            event_tac={_tac_attr_hash("NumAttackers"): len(attackers)})
    _db.commit()
    if combats:
        g.push_combat_listing(my_uid, combats)
    _pvp_send_same_events(session, g, my_uid, opp_uid)
    log_req(f"    PvP attack: pid {my_pid} declared {len(attackers)} attacker(s)")
    # No attackers declared (e.g. every troop is summoning sick): skip the
    # remaining combat steps straight to SecondMainPhase instead of leaving
    # the game stuck waiting for passes through DeclareDefense/AssignDamage.
    if not attackers:
        pvp_skip_to_second_main(session, state)
        return True
    # Attackers WERE declared: the combat now moves to the defender.  Advance
    # through DeclareAttackPriorityWindow (13, response window) into
    # DeclareDefense (14) and hand the DEFENDER priority + blocker options so
    # they can set up blockers.  The responder (attacker) gets a QuickAction
    # window at 13; the defender acts at 14.
    if declarations is None:
        pvp_advance_to_declare_defense(session, state)
    return True












def _pvp_declare_blockers(handler, session, inner_bytes, my_pid,
                          declarations=None):
    """Record the defender's declared blockers and push BlockersAssigned to
    both players."""
    pids = db_game_session_pids(session.session_id)
    if len(pids) < 2:
        return False
    opp_pid = pids[0] if pids[1] == my_pid else pids[1]
    state = pvp_load_state(session) or {}
    attackers = {int(k): int(v) for k, v in (state.get("attackers") or {}).items()}
    if not attackers:
        return False
    my_wz = set(r[0] for r in db_card_uids_in_zone(
        session.session_id, my_pid, "warzone", conn=_db))
    all_uids = []
    blockers_map = {}
    if declarations is not None:
        for attacker, blockers in declarations:
            try:
                attacker_uid = int(getattr(attacker, "uid64", attacker))
            except (TypeError, ValueError):
                continue
            parsed = []
            for blocker in blockers or ():
                try:
                    parsed.append(int(getattr(blocker, "uid64", blocker)))
                except (TypeError, ValueError):
                    continue
            blockers_map[attacker_uid] = parsed
    elif isinstance(inner_bytes, bytes):
        for m_du in re.finditer(rb'm_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});',
                                inner_bytes):
            try:
                import struct as _st
                uid64 = _st.unpack('<Q', bytes.fromhex(m_du.group(1).decode()))[0]
                if (uid64 & 0xFF) == 1:
                    all_uids.append(int(uid64))
            except Exception:
                continue
    if declarations is None:
        cur = None
        for u in all_uids:
            if u in attackers:
                cur = u
                blockers_map.setdefault(cur, [])
            elif cur is not None and u in my_wz:
                from rules_port.combat_rules import can_block
                if can_block(_db, session.session_id, _pvp_fra_view(state, opp_pid, my_pid),
                             cur, u):
                    blockers_map[cur].append(u)
    state["blockers"] = {str(k): [str(b) for b in v]
                         for k, v in blockers_map.items()}
    # Mark each blocker Blocking in the DB so reconnect / HasBlocked logic and
    # the shared resolver's end-of-combat clear work (mirrors PvE
    # db_bulk_blocker_state).
    db_bulk_blocker_state(session.session_id,
                          [int(b) for bs in blockers_map.values() for b in bs])
    pvp_save_state(session, state)
    champ_map = state.get("champ_map") or {}
    opp_champ = int(champ_map.get(str(opp_pid), 0))
    opp_uid = _ge.UID.make(244, opp_pid)
    _ge.UID.make(244, my_pid)
    for pid in pids:
        h = player_handlers.get(pid)
        if not h:
            continue
        pl_uid = _ge.UID.make(244, pid)
        g = _ge.Game(int(session.session_id), pl_uid,
                     _ge.UID.make(244, pids[1] if pid == pids[0] else pids[0]))
        player_champ = _ge.SessionCardId(_ge.UID(opp_champ)) if opp_champ \
            else _ge.SessionCardId(opp_uid)
        combats = []
        for u in attackers:
            cid = _ge.CombatId(opp_uid, u & 0xFFFF)
            blockers = [_ge.SessionCardId(_ge.UID(int(b)))
                        for b in blockers_map.get(u, [])]
            g.push_blockers_assigned(cid, _ge.SessionCardId(_ge.UID(u)),
                                     player_champ, blockers)
            cs = _ge.CombatSessionEventArgs()
            cs.player_id = opp_uid
            cs.id = cid
            cs.attacker = _ge.SessionCardId(_ge.UID(u))
            cs.blockers = blockers
            combats.append(cs)
        if combats:
            g.push_combat_listing(opp_uid, combats)
        _send_pvp_packet(h, session, g, pl_uid, "defense")
    log_req(f"    PvP defense: pid {my_pid} declared "
            f"{sum(len(v) for v in blockers_map.values())} blocker(s)")
    # The defender's decision is made — advance out of DeclareDefense (14)
    # into DeclareDefensePriorityWindow (15), a response window, and hand
    # priority to the TURN player so combat can proceed to AssignDamage.  The
    # normal pass cycle then carries both players through 15 -> AssignFirst
    # StrikeDamage (16) -> ... -> AssignDamage (18) -> SecondMain (19).
    # (Without this the game sat on DeclareDefense forever once the defender
    # declared — even declaring NO blockers.)
    if declarations is None:
        _pvp_advance_past_declare_defense(session, state)
    return True








