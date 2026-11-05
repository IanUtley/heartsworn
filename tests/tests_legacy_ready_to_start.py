"""Characterization of the ReadyToStartGame (22031) legacy handler.

Pins the two branches that run without campaign or battle state: no session
(nothing is sent) and a PvP session (a single acknowledgement packet). The
PvE and campaign setup branches need battle state and are not pinned here.
"""

import hashlib
import os
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database  # noqa: E402

fresh_database()

from services.legacy_handlers import LEGACY_HANDLERS  # noqa: E402

PLAYER_ID = 67890
# Pinned from the current handler: issuer and body sha256 of the acknowledgement.
PVP_ACK_ISSUER = "0.0.0.0.ServiceLoadBalancer.252.ServicePlayer.5.9001"
PVP_ACK_BODY_SHA256 = "fa04efd9788848a47f7a2e526fbb1ffc005bbbe7e111d11963d97f9b74817785"


class _StubHandler:
    client_reck_id = str(PLAYER_ID)
    client_uid = 5
    sid = "sid-1"
    scnt = 0
    user_profile = {"id": PLAYER_ID, "name": "pvp"}

    def __init__(self):
        self.sent = []

    def send(self, headers, body=b""):
        self.sent.append((headers, body))


def _run(session):
    handler = _StubHandler()
    with mock.patch("services.legacy_loadbalancer.game_session.find_session_by_player",
                    return_value=session):
        LEGACY_HANDLERS[22031](handler, "target", 3, 22031, 9000, False, "session-x",
                               "conh-1", {}, b"", b"")
    return handler.sent


def test_no_session_sends_nothing():
    assert _run(None) == []


def test_pvp_session_sends_one_acknowledgement():
    session = SimpleNamespace(session_id=7010, session_name="pvp-7010", server_id=1,
                              encounter_data={}, players=[], turn_order=[], owner_uid=0,
                              state=0, seed_z=0, seed_w=0, deck_template_id=None)
    sent = _run(session)
    assert len(sent) == 1, sent
    headers, body = sent[0]
    assert headers["issuer"] == PVP_ACK_ISSUER
    assert hashlib.sha256(body).hexdigest() == PVP_ACK_BODY_SHA256


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
