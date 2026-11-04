"""Authoritative registry for extracted client-request service handlers.

The protocol server owns transport concerns (headers, compression and the
application transaction boundary). This package owns the mapping from a
client data type to the small handler module that implements it. Entries are
kept as ``(module, function, extra_kwargs)`` so imports remain lazy.

Requests not present here intentionally remain in the legacy compatibility
dispatcher in :mod:`hconnect_server`; that is the remaining extraction
boundary, rather than a second competing registry.
"""

# fmt: off
_SERVICE_TABLE = {
    # Decks
    2081: ("services.decks", "handle_get_player_decks", {}),
    2083: ("services.decks", "handle_get_deck_info", {}),
    2033: ("services.deck_editing", "handle_deck_edit", {"data_type": 2033}),
    2035: ("services.deck_editing", "handle_deck_edit", {"data_type": 2035}),
    2037: ("services.deck_editing", "handle_deck_edit", {"data_type": 2037}),
    2043: ("services.deck_editing", "handle_deck_edit", {"data_type": 2043}),
    2089: ("services.deck_editing", "handle_deck_edit", {"data_type": 2089}),
    2091: ("services.deck_editing", "handle_deck_edit", {"data_type": 2091}),
    2095: ("services.deck_editing", "handle_deck_edit", {"data_type": 2095}),
    2185: ("services.deck_editing", "handle_deck_edit", {"data_type": 2185}),
    2187: ("services.deck_editing", "handle_deck_edit", {"data_type": 2187}),

    # Mail
    60001: ("services.mail", "handle_send_mail", {}),
    60002: ("services.mail", "handle_receive", {}),
    60003: ("services.mail", "handle_delete", {}),
    60004: ("services.mail", "handle_send", {}),
    60005: ("services.mail", "handle_mark_read", {}),
    60006: ("services.mail", "handle_claim", {}),
    60007: ("services.mail", "handle_get_unread", {}),
    60008: ("services.mail", "handle_mark_sent_delete", {}),
    9001: ("services.mail", "handle_ping_mail_server", {}),

    # Social / friends
    2149: ("services.social", "handle_add_friend", {}),
    2157: ("services.social", "handle_accept_friend_request", {}),
    2159: ("services.social", "handle_ignore_friend_request", {}),
    2161: ("services.social", "handle_remove_friend", {}),
    2163: ("services.social", "handle_ignore_player", {}),
    2165: ("services.social", "handle_unignore_player", {}),

    # ServiceLoadBalancer session requests
    22015: ("services.loadbalancer", "handle_start_session", {"data_type": 22015}),
    22017: ("services.loadbalancer", "handle_start_encounter", {"data_type": 22017}),
    22019: ("services.loadbalancer", "handle_find_session", {"data_type": 22019}),
    22021: ("services.loadbalancer", "handle_join_session", {"data_type": 22021}),
    22027: ("services.loadbalancer", "handle_ready_for_game_setup_request", {"data_type": 22027}),
    22029: ("services.loadbalancer", "handle_ready_for_game_events_request", {"data_type": 22029}),
    22011: ("services.loadbalancer", "handle_quit_on_reconnect", {"data_type": 22011}),

    # Tournament requests with fixed response shapes
    25005: ("services.tournaments", "handle_unsubscribe_description_listener", {"data_type": 25005}),
    25007: ("services.tournaments", "handle_get_tournament_info", {"data_type": 25007}),
    25035: ("services.tournaments", "handle_leave_waiting_room", {"data_type": 25035}),
    70015: ("services.tournaments", "handle_pooled_messaging_flush", {"data_type": 70015}),
    70027: ("services.tournaments", "handle_ladder_tier_rewards_list", {"data_type": 70027}),
    70028: ("services.tournaments", "handle_ladder_ranking_list", {"data_type": 70028}),
    70023: ("services.tournaments", "handle_ladder_record", {"data_type": 70023}),
    25019: ("services.tournaments", "handle_create_waiting_room", {"data_type": 25019}),
    25027: ("services.tournaments", "handle_test_tournament_entry", {"data_type": 25027}),
    25031: ("services.tournaments", "handle_leave_tournament", {"data_type": 25031}),
    25011: ("services.tournaments", "handle_save_tournament_deck", {"data_type": 25011}),
    25021: ("services.tournaments", "handle_try_reconnection_tournament", {"data_type": 25021}),
    25029: ("services.tournaments", "handle_enter_tournament", {"data_type": 25029}),
    25039: ("services.tournaments", "handle_game_entrance", {"data_type": 25039}),
    160000: ("services.replay", "handle_replay_fetch_request", {"data_type": 160000}),
    22013: ("services.loadbalancer", "handle_try_reconnection", {"data_type": 22013}),

    # Matchmaking and direct challenges
    4001: ("services.matchmaking", "handle_ping_matchmaking", {}),
    4013: ("services.matchmaking", "handle_send_quick_match_challenge", {}),
    4017: ("services.matchmaking", "handle_send_challenge_response", {}),
    70022: ("services.matchmaking", "handle_ladder_find_match", {}),

    # Tournament game requests
    22023: ("services.tournament_game", "handle_join_disconnected_game", {}),
    22025: ("services.tournament_game", "handle_ready_to_continue_game", {}),

    # Store / escrow
    6009: ("services.store", "handle_get_items", {}),
    6011: ("services.store", "handle_purchase", {}),
    6013: ("services.store", "handle_redeem", {}),
    80000: ("services.profile_requests", "handle_profile_request", {"data_type": 80000}),
    110000: ("services.campaign_requests", "handle_campaign_service_request", {"data_type": 110000}),
    140000: ("services.campaign_requests", "handle_campaign_service_request", {"data_type": 140000}),
    150000: ("services.campaign_requests", "handle_campaign_service_request", {"data_type": 150000}),
    2049: ("services.chest_requests", "handle_chest_request", {"data_type": 2049}),
    2127: ("services.chest_requests", "handle_chest_request", {"data_type": 2127}),
    2129: ("services.chest_requests", "handle_chest_request", {"data_type": 2129}),

    # Auction House. The implementation is intentionally still a stub, but
    # its API surface belongs in the live request registry.
    50004: ("services.auction", "handle_query", {}),
    50010: ("services.auction", "handle_query_items_info", {}),

    # Frost Ring Arena
    10001: ("services.arena", "handle_request", {"data_type": 10001}),
    10003: ("services.arena", "handle_request", {"data_type": 10003}),
    10005: ("services.arena", "handle_request", {"data_type": 10005}),
    10007: ("services.arena", "handle_request", {"data_type": 10007}),
    10009: ("services.arena", "handle_request", {"data_type": 10009}),
    10011: ("services.arena", "handle_request", {"data_type": 10011}),
    10013: ("services.arena", "handle_request", {"data_type": 10013}),
    10017: ("services.arena", "handle_request", {"data_type": 10017}),
    10019: ("services.arena", "handle_request", {"data_type": 10019}),
    10027: ("services.arena", "handle_request", {"data_type": 10027}),
    10029: ("services.arena", "handle_request", {"data_type": 10029}),
    10031: ("services.arena", "handle_request", {"data_type": 10031}),
    10033: ("services.arena", "handle_request", {"data_type": 10033}),
}
# fmt: on


def dispatch(data_type: int):
    """Return ``(module, function, extra_kwargs)`` for a request, if any."""
    return _SERVICE_TABLE.get(data_type)
