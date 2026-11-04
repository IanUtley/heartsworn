"""Characterization of the ReadyForGameEvents (22029) legacy handler.

A regular session gets no reply from this request. A tournament session is
handed to the tournament module together with the hub's PvP events-ready
state. These checks pin both behaviours before the handler is ported.
"""

import os
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database  # noqa: E402

fresh_database()

import hconnect_server as hc  # noqa: E402
from services import legacy_loadbalancer as lb  # noqa: E402

PLAYER_ID = 67890


class _StubHandler:
    client_reck_id = str(PLAYER_ID)
    client_uid = 5
    sid = "sid-1"
    scnt = 0

    def __init__(self):
        self.sent = []

    def send(self, headers, body=b""):
        self.sent.append((headers, body))


def _drive(session):
    handler = _StubHandler()
    with mock.patch.object(hc.game_session, "find_session_by_player",
                           return_value=session), \
         mock.patch("services.tournament_game.handle_ready_for_game_events") as hand_off:
        lb.handle_readyforgameevents_22029(
            handler, "target", 3, 22029, 9000, False, "session-x", "conh-1",
            {}, b"", b"")
    return handler, hand_off


def test_regular_session_gets_no_reply_and_no_hand_off():
    session = SimpleNamespace(session_name="camp_9")
    handler, hand_off = _drive(session)
    assert handler.sent == []
    hand_off.assert_not_called()


def test_tournament_session_is_handed_off_with_the_hub_events_state():
    session = SimpleNamespace(session_name="tourney-4")
    handler, hand_off = _drive(session)
    assert handler.sent == []
    hand_off.assert_called_once()
    args = hand_off.call_args.args
    assert args[0] is handler and args[1] is session
    assert args[2] is hc._pvp_events_ready
