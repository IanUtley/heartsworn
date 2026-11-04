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
from services import dispatch as service_dispatch  # noqa: E402
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
    "fra_offer": ("0.0.0.0.ServiceLoadBalancer.252.ServicePlayer.5.9003",
                  "839399620b892a318fd585178840976e85677774571aa3cf7de7581e7dceffa1"),
    "tournament_offer": ("0.0.0.0.ServiceLoadBalancer.252.ServicePlayer.5.9001",
                         "14d27c73f7cd5137b8bebf5941c7b7c7cfe97cbf80f9b7217236663bacda028e"),
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

    def _resolve_fra_deck_id(self, deck_id):
        return deck_id


def _packet(session, rules_state, reqid=9000, fra=False, tournament_pvp=None, handler=None):
    handler = handler or _StubHandler()
    lock = mock.MagicMock()
    import importlib
    module_name, fn_name, extra = service_dispatch(22013)
    fn = getattr(importlib.import_module(module_name), fn_name)
    with mock.patch.object(hc.game_session, "find_session_by_player", return_value=session), \
         mock.patch("services.tournament_game.campaign_session_reconnectable",
                    return_value=session is not None and not fra and tournament_pvp is None), \
         mock.patch("services.tournament_game.fra_session_reconnectable", return_value=fra), \
         mock.patch("services.tournament_game.pvp_load_state", return_value=tournament_pvp or {}), \
         mock.patch("services.tournament_game.pvp_session_lock", return_value=lock), \
         mock.patch("rules_port.persistence.load_state", return_value=rules_state):
        fn(handler, "target", 3, reqid, False, "session-x", "conh-1",
           inner_obj={}, inner_bytes=b"", SERVICE_MAIL_UID=252,
           SERVICE_PROFILE_UID=0, log_req=lambda *_a, **_k: True, **extra)
    headers, body = handler.sent[-1]  # the last response on this connection
    return headers["issuer"], hashlib.sha256(body).hexdigest()


def test_no_game_branch_matches_the_snapshot():
    assert _packet(None, {}) == EXPECTED["no_game"]


def test_campaign_offer_branch_matches_the_snapshot():
    assert _packet(CAMPAIGN_SESSION, {}) == EXPECTED["campaign_offer"]


FRA_SESSION = SimpleNamespace(
    session_id=7007, session_name="fra_3", state="",
    encounter_data={"SessionFlags": 128, "ArenaInstance": 3},
    turn_order={"checkpoint": 1})
TOURNAMENT_SESSION = SimpleNamespace(
    session_id=7008, session_name="tourney-4", state="", encounter_data={},
    turn_order=None)


def test_fra_checkpoint_branch_matches_the_snapshot():
    # The FRA offer waits for the arena-screen probe: the second distinct
    # request on the same connection. The first request answers "no game".
    connection = _StubHandler()
    _packet(FRA_SESSION, {}, fra=True, reqid=9000, handler=connection)
    assert _packet(FRA_SESSION, {}, fra=True, reqid=9002, handler=connection) == EXPECTED["fra_offer"]


def test_tournament_pvp_branch_matches_the_snapshot():
    assert _packet(TOURNAMENT_SESSION, {}, tournament_pvp={"pvp": True}) == EXPECTED["tournament_offer"]


def test_campaign_declined_branch_matches_the_snapshot():
    declined = {}
    _record_reconnect_decline(declined, PLAYER_ID)
    assert _packet(CAMPAIGN_SESSION, declined) == EXPECTED["campaign_declined"]

if __name__ == "__main__":
    test_no_game_branch_matches_the_snapshot()
    test_campaign_offer_branch_matches_the_snapshot()
    test_fra_checkpoint_branch_matches_the_snapshot()
    test_tournament_pvp_branch_matches_the_snapshot()
    test_campaign_declined_branch_matches_the_snapshot()
    print("PASS tests_legacy_try_reconnection: 5 checks")
