"""Characterization of the TryReconnectionToDisconnectedGame (22013) legacy handler.

Pins the response packet for three campaign-only outcomes: no game to rejoin,
a campaign battle the player can rejoin, and the same battle after the player
has declined it. The FRA and tournament branches are out of scope here.
"""

import hashlib
import os
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database  # noqa: E402

fresh_database()

import hconnect_server as hc  # noqa: E402
from services import legacy_loadbalancer as lb  # noqa: E402
from services.reconnect import _record_reconnect_decline  # noqa: E402

PLAYER_ID = 67890
CAMPAIGN_SESSION = SimpleNamespace(
    session_id=7006, session_name="camp_5", state="", encounter_data={},
    turn_order=None)
# Pinned from the current handler. Each entry: (issuer, body sha256).
EXPECTED = {
    "no_game": ("0.0.0.0.ServiceLoadBalancer.252.ServicePlayer.5.9001",
                "f2ee41f616814e28d2bd9417c15bc6faced436f79ba4217c6d573dafa3919a28"),
    "campaign_offer": ("0.0.0.0.ServiceLoadBalancer.252.ServicePlayer.5.9001",
                       "d536bab3084fcb2f02a723be289af0f6994ee3424d27f85efef525a8071997b9"),
    "campaign_declined": ("0.0.0.0.ServiceLoadBalancer.252.ServicePlayer.5.9001",
                        "f2ee41f616814e28d2bd9417c15bc6faced436f79ba4217c6d573dafa3919a28"),
}


class _StubHandler:
    client_reck_id = str(PLAYER_ID)
    client_uid = 5
    sid = "sid-1"
    scnt = 0
    user_profile = {"id": 9}

    def __init__(self):
        self.sent = []

    def send(self, headers, body=b""):
        self.sent.append((headers, body))


def _packet(session, rules_state, reqid=9000):
    handler = _StubHandler()
    lock = mock.MagicMock()
    with mock.patch.object(hc.game_session, "find_session_by_player", return_value=session), \
         mock.patch("services.tournament_game.campaign_session_reconnectable",
                    return_value=session is not None), \
         mock.patch("services.tournament_game.pvp_session_lock", return_value=lock), \
         mock.patch("rules_port.persistence.load_state", return_value=rules_state):
        lb.handle_tryreconnectiontodisconnectedgame_22013(
            handler, "target", 3, 22013, reqid, False, "session-x", "conh-1",
            {}, b"", b"")
    assert len(handler.sent) == 1, handler.sent
    headers, body = handler.sent[0]
    return headers["issuer"], hashlib.sha256(body).hexdigest()


def test_no_game_branch_matches_the_snapshot():
    assert _packet(None, {}) == EXPECTED["no_game"]


def test_campaign_offer_branch_matches_the_snapshot():
    assert _packet(CAMPAIGN_SESSION, {}) == EXPECTED["campaign_offer"]


def test_campaign_declined_branch_matches_the_snapshot():
    declined = {}
    _record_reconnect_decline(declined, PLAYER_ID)
    assert _packet(CAMPAIGN_SESSION, declined) == EXPECTED["campaign_declined"]

if __name__ == "__main__":
    test_no_game_branch_matches_the_snapshot()
    test_campaign_offer_branch_matches_the_snapshot()
    test_campaign_declined_branch_matches_the_snapshot()
    print("PASS tests_legacy_try_reconnection: 3 checks")
