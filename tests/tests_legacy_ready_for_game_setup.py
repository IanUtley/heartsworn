"""Characterization of the ReadyForGameSetup (22027) legacy handler.

Pins the packet for the session-present branch (a non-tournament session, so
the tournament hook leaves the response unchanged) and the no-session branch.
"""

import hashlib
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database  # noqa: E402

fresh_database()

from services import legacy_loadbalancer as lb  # noqa: E402

PLAYER_ID = 67890
SESSION = SimpleNamespace(
    session_id=7005, session_name="camp_9", encounter_data={})
# Pinned from the current handler. Each entry: (issuer, body sha256).
EXPECTED = {
    "session": ("0.0.0.0.ServiceLoadBalancer.252.ServicePlayer.5.9001",
                "24e9aedc848fdeb11a94823c1aaf08dad54bd14c3d236fe3c0cc4ed80b83735b"),
    "no_session": ("0.0.0.0.ServiceLoadBalancer.252.ServicePlayer.5.9001",
                   "cd5e82274b236364bb90939b58ad0f418545dba28ba5f9e24af6393e4a90033b"),
}


class _StubHandler:
    client_reck_id = str(PLAYER_ID)
    client_uid = 5
    sid = "sid-1"
    scnt = 0

    def __init__(self, session):
        self.sent = []
        self._application = SimpleNamespace(
            execute=lambda command: SimpleNamespace(value=session))

    def send(self, headers, body=b""):
        self.sent.append((headers, body))


def _packet(session):
    handler = _StubHandler(session)
    lb.handle_readyforgamesetup_22027(
        handler, "target", 3, 22027, 9000, False, "session-x", "conh-1",
        {}, b"", b"")
    assert len(handler.sent) == 1, handler.sent
    headers, body = handler.sent[0]
    return headers["issuer"], hashlib.sha256(body).hexdigest()


def test_ready_for_game_setup_session_branch_matches_the_snapshot():
    assert _packet(SESSION) == EXPECTED["session"]


def test_ready_for_game_setup_no_session_branch_matches_the_snapshot():
    assert _packet(None) == EXPECTED["no_session"]
