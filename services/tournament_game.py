"""Tournament PvP game setup — push initial battle events to both players."""


import game_engine as _ge
from gamedata import DEFAULT_RECORD_STORE
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
from types import (
    SimpleNamespace,
)
from services.pvp_state import (
    pvp_discard_session_lock,
    pvp_shared_port,
    pvp_discard_shared_port,
)
from services.pvp_priority import (
    flush_priority_clock,
    priority_elapsed_ticks,
)
from rules_port.pvp_lifecycle import (
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
    skips_draw_phase as port_skips_draw_phase,
)
from gamedata import (
    PlayPlan,
)
from pvp_db import (
    db_game_champion,
    db_game_deck_cards,
    db_game_draw_cards,
    db_game_card_type,
    db_card_instance_dynamic_projections,
    db_game_shuffle_deck,
    db_champion_template_health,
    db_discard_card,
    db_delete_game_session,
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
    db_warzone_troops_with_state,
    db_warzone_attack_option_rows,
    db_warzone_blocker_uids,
    db_game_get_hand,
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
    db_card_zone_projection,
    db_card_owner_zone_state,
    db_champion_ability_guids,
    db_champion_ability_costs,
    db_champion_ability_thresholds,
)
from tournament_db import (
    db_tournament_by_id,
    db_tournament_player_name_for_session,
)
from encoder import (
    encode_objfmt_response,
)
from gamemodes.tournament_engine import (
    player_handler_lock,
    record_tournament_game_result,
    tournament_id_from_session_name,
)
from domain.constants import (
    DEFAULT_MAX_HAND_SIZE,
)
from services.pvp_watchdog import (
    pvp_start_priority_watchdog,
)
from services.pvp_state import (
    pvp_session_lock,
    set_pvp_shared_port,
    pvp_load_state,
    pvp_save_state,
)
from rules_port.pvp_lifecycle import (
    mulligan_transition as port_mulligan_transition,
)
from gamedata import (
    ability_graph,
)
from db import (
    _db,
)
from pvp_db import (
    db_game_session_pids,
)
from encoder import (
    encode_datawrapper,
    encode_sync_event,
    compress_gzip,
    client_session_guid,
)
from gamemodes.tournament_engine import (
    player_handlers,
)
from services.pvp_flow import (
    _PvpChainHost,
    _pvp_advance_from_damage_step,
    _pvp_declare_attackers,
    _pvp_declare_blockers,
    _pvp_offer_trigger_response,
    _pvp_play_spell,
    _pvp_play_troop,
    _pvp_project_resource_play,
    _pvp_push_mulligan_prompt,
    _pvp_push_reconnect_snapshot,
    _pvp_reassign_priority_after_disconnect,
    _pvp_resolve_ability_item,
    _pvp_resolve_choice,
    _pvp_resolve_combat,
    _pvp_resolve_deck_search,
    _pvp_resolve_matching_target,
    _pvp_resolve_optional_trigger,
    _pvp_resolve_revealed_choice,
    _pvp_resolve_shard_choice,
    _pvp_resolve_trigger_target,
    _pvp_run_phase_start,
    attach_pvp_rules_port,
    handle_ready_for_game_events,
    project_accepted_pvp_transaction,
    pvp_advance_past_non_stops,
    pvp_advance_to_declare_defense,
    pvp_mulligan_next,
    pvp_phase_after_blockers,
    pvp_push_current_phase_options,
    route_pvp_pass,
    set_pvp_auto_pass,
)
from services.pvp_core import (
    _pvp_auto_pass_chain_priority,
    _pvp_auto_pass_opponent_stop,
    _pvp_resolve_stack_item,
    pvp_handle_discard,
)
from services.pvp_core import (
    _pvp_advance_past_declare_defense,
    pvp_skip_to_second_main,
)
from services.pvp_core import (
    handle_ready_to_continue_game,
    notify_pvp_player_disconnected,
)
from services.pvp_core import (
    _pvp_resolve_conversation,
    _pvp_resolve_discard_prompt,
    _wire_pvp_state_based_handler,
    pvp_debug_draw,
)
from services.pvp_core import (
    _pvp_offer_opponent_response,
    pvp_push_attack_options,
    pvp_push_main_phase_options,
)
from services.pvp_core import (
    _pvp_apply_card_play_costs,
    _pvp_check_game_end,
    _pvp_gain_charge_trigger_game,
    _pvp_gain_threshold_trigger_game,
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
)
from application.player_transactions import extract_ability_guid  # noqa: F401  (re-exported for tests)

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





























# ── priority watchdog ──────────────────────────────────────────────────────
# The original implementation periodically re-pushed GreenLight events to
# correct client-side priority drift.  Normal PvP transitions now explicitly
# send the correct priority to both clients, so periodic client events are no
# longer needed and would pollute replay capture.  Keep the daemon only as a
# server-side clock/inactivity watchdog.



















































































































# Campaign battles cannot be rejoined yet: the rebuilt board is incomplete (see the
# campaign reconnect notes). Set True to re-enable the offer, FindSession resume and join.
CAMPAIGN_REJOIN_ENABLED = False


__all__ = [
    "_ability_has_effects_from",
    "_objfmt_uid_field",
    "_pvp_ability_cost_targets",
    "_pvp_add_play_target_options",
    "_pvp_apply_visibility",
    "_pvp_card_playable",
    "_pvp_card_source_location",
    "_pvp_chain_active",
    "_pvp_defer_trigger_event",
    "_pvp_discard_prompt_data",
    "_pvp_discard_prompt_pid",
    "_pvp_dispatch_triggers",
    "_pvp_emit_paid_cost_events",
    "_pvp_fra_view",
    "_pvp_log_stack",
    "_pvp_push_reconnect_underground",
    "_pvp_push_turn_phase_with_elapsed",
    "_pvp_raw_player_id",
    "_pvp_resolve_ability",
    "_pvp_resource_charge_points",
    "_pvp_select_card_play_costs",
    "_pvp_session_pids",
    "_pvp_set_handler_champions",
    "_pvp_state_thresholds",
    "_pvp_sync_view_to_state",
    "_pvp_thresholds_met",
    "_pvp_transaction_card_uids",
    "_pvp_turn_phase_list",
    "_pvp_typed_card_targets",
    "_send_pvp_packet",
    "_session_has_reconnect_player",
    "_take_pending_gain_charge_amount",
    "_uid_int",
    "pvp_combat_has_swiftstrike",
    "pvp_default_state",
    "pvp_phase_is_stop",
    "pvp_player_auto_passes",
    "pvp_turn_has_attackers",
    "_PvpChainHost",
    "_pvp_advance_from_damage_step",
    "_pvp_declare_attackers",
    "_pvp_declare_blockers",
    "_pvp_offer_trigger_response",
    "_pvp_play_spell",
    "_pvp_play_troop",
    "_pvp_project_resource_play",
    "_pvp_push_mulligan_prompt",
    "_pvp_push_reconnect_snapshot",
    "_pvp_reassign_priority_after_disconnect",
    "_pvp_resolve_ability_item",
    "_pvp_resolve_choice",
    "_pvp_resolve_combat",
    "_pvp_resolve_deck_search",
    "_pvp_resolve_matching_target",
    "_pvp_resolve_optional_trigger",
    "_pvp_resolve_revealed_choice",
    "_pvp_resolve_shard_choice",
    "_pvp_resolve_trigger_target",
    "_pvp_run_phase_start",
    "attach_pvp_rules_port",
    "handle_ready_for_game_events",
    "project_accepted_pvp_transaction",
    "pvp_advance_past_non_stops",
    "pvp_advance_to_declare_defense",
    "pvp_mulligan_next",
    "pvp_phase_after_blockers",
    "pvp_push_current_phase_options",
    "route_pvp_pass",
    "set_pvp_auto_pass",
    "_pvp_auto_pass_chain_priority",
    "_pvp_auto_pass_opponent_stop",
    "_pvp_resolve_stack_item",
    "pvp_handle_discard",
    "_pvp_advance_past_declare_defense",
    "pvp_skip_to_second_main",
    "handle_ready_to_continue_game",
    "notify_pvp_player_disconnected",
    "_pvp_resolve_conversation",
    "_pvp_resolve_discard_prompt",
    "_wire_pvp_state_based_handler",
    "pvp_debug_draw",
    "_pvp_offer_opponent_response",
    "pvp_push_attack_options",
    "pvp_push_main_phase_options",
    "_pvp_apply_card_play_costs",
    "_pvp_check_game_end",
    "_pvp_gain_charge_trigger_game",
    "_pvp_gain_threshold_trigger_game",
    "_pvp_resolve_granted_resource_abilities",
    "_pvp_run_draw",
    "_pvp_sync_game_state",
    "handle_join_disconnected_game",
    "push_pvp_game_start",
    "pvp_concede",
    "pvp_push_blocker_options",
    "pvp_push_empty_blockers",
    "pvp_push_phase_options",
    "pvp_push_warzone_updates",
    "_push_to_both_players",
    "_pvp_add_champion_options",
    "_pvp_add_hand_card_updates",
    "_pvp_add_troop_ability_options",
    "_pvp_affordable_troop_abilities",
    "_pvp_defender_blockable_count",
    "_pvp_drive_f10_priority",
    "_pvp_end_game",
    "_pvp_populate_game_state",
    "_pvp_push_discard_prompt",
    "_pvp_push_waiting_on",
    "_pvp_ready_step",
    "_pvp_send_same_events",
    "_pvp_typed_picker_uid",
    "_raw_player_id",
    "campaign_session_reconnectable",
    "fra_session_reconnectable",
    "handle_ready_for_game_setup",
    "pvp_start_priority_watchdog",
    "pvp_session_lock",
    "set_pvp_shared_port",
    "pvp_load_state",
    "pvp_save_state",
    "port_mulligan_transition",
    "ability_graph",
    "_db",
    "db_game_session_pids",
    "encode_datawrapper",
    "encode_sync_event",
    "compress_gzip",
    "client_session_guid",
    "player_handlers",
    "SimpleNamespace",
    "pvp_discard_session_lock",
    "pvp_shared_port",
    "pvp_discard_shared_port",
    "flush_priority_clock",
    "priority_elapsed_ticks",
    "port_phase_after_blockers",
    "port_phase_transition",
    "port_enter_phase",
    "port_record_phase_pass",
    "port_set_priority",
    "port_reset_priority_interval",
    "port_waiting_player_requires_priority",
    "port_stack_pass_transition",
    "port_advance_turn_state",
    "port_queue_stack_item",
    "port_skips_draw_phase",
    "PlayPlan",
    "db_game_champion",
    "db_game_deck_cards",
    "db_game_draw_cards",
    "db_game_card_type",
    "db_card_instance_dynamic_projections",
    "db_game_shuffle_deck",
    "db_champion_template_health",
    "db_discard_card",
    "db_delete_game_session",
    "db_card_state_value",
    "db_card_set_attacking_state",
    "db_bulk_blocker_state",
    "db_is_champion_template",
    "db_hand_cards_with_templates",
    "db_card_template_attrs_joined",
    "db_template_by_guid",
    "db_set_card_location",
    "db_set_card_state_or",
    "db_update_card_state",
    "db_card_location",
    "db_warzone_troops_with_state",
    "db_warzone_attack_option_rows",
    "db_warzone_blocker_uids",
    "db_game_get_hand",
    "db_card_uids_in_zone",
    "db_ability_option_cards",
    "db_card_ability_payload",
    "db_template_ability_payload",
    "db_card_zone_details",
    "db_hand_exists",
    "db_hand_card_for_discard",
    "db_deck_top_card",
    "db_hand_count",
    "db_card_play_info",
    "db_warzone_display_rows",
    "db_template_name",
    "db_card_zone_projection",
    "db_card_owner_zone_state",
    "db_champion_ability_guids",
    "db_champion_ability_costs",
    "db_champion_ability_thresholds",
    "db_tournament_by_id",
    "db_tournament_player_name_for_session",
    "encode_objfmt_response",
    "player_handler_lock",
    "record_tournament_game_result",
    "tournament_id_from_session_name",
    "DEFAULT_MAX_HAND_SIZE",
]
