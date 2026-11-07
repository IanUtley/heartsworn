"""Characterization of the ReadyToStartGame (22031) legacy handler.

Pins the no-session branch (nothing is sent), the PvP branch (one
acknowledgement packet) and the campaign setup branch (the acknowledgement
and the four ServiceGameSession setup packets). The campaign branch is run
with a seeded RNG and a pinned clock, because gzip stamps the header with
the current time and the coin toss and shuffles draw from ``random``.
"""

import hashlib
import os
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database  # noqa: E402

fresh_database()

from services.ready_to_start import handle_readytostartgame_22031  # noqa: E402


def _ready_to_start(handler):
    handle_readytostartgame_22031(handler, "target", 3, 9000, False, "session-x",
                                  "conh-1", {}, b"", SERVICE_MAIL_UID=252,
                                  log_req=lambda *_a, **_k: None, data_type=22031)

PLAYER_ID = 67890
# Pinned from the current handler: issuer and body sha256 of the acknowledgement.
PVP_ACK_ISSUER = "0.0.0.0.ServiceLoadBalancer.252.ServicePlayer.5.9001"
PVP_ACK_BODY_SHA256 = "fa04efd9788848a47f7a2e526fbb1ffc005bbbe7e111d11963d97f9b74817785"
# The campaign setup: (issuer, body sha256) for each packet, in send order.
CAMPAIGN_PACKETS = [
    ("0.0.0.0.ServiceLoadBalancer.252.ServicePlayer.5.9001",
     "fa04efd9788848a47f7a2e526fbb1ffc005bbbe7e111d11963d97f9b74817785"),
    ("0.0.0.0.ServiceLoadBalancer.252.ServicePlayer.5.1",
     "39551d319e9bb5f52c5a9724e0bff85c0872405f03a343c405984bdcf77cefc2"),
    ("0.0.0.0.ServiceGameSession.246.7020.2",
     "7133f93446d08138cb8c1ab4b781476c001a39ececa9c5c821db3093eb9264f0"),
    ("0.0.0.0.ServiceGameSession.246.7020.3",
     "c9a8ae942ef3199374ad7d03ebc1d20c603a4d880adb7e4d87271dfb8b03dd83"),
    ("0.0.0.0.ServiceGameSession.246.7020.4",
     "746ef715bc597da0530c90632f193306e84940e139e2cd0b6bd452096c84b86e"),
]


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
    with mock.patch("services.ready_to_start.game_session.find_session_by_player",
                    return_value=session):
        _ready_to_start(handler)
    return handler.sent


def _campaign_run():
    """Drive the campaign setup through a real HCPHandler on a fresh database, with the RNG and clock pinned."""
    import random
    import time
    import db as shared
    import hconnect_server
    from game_session import GameSession
    from tests.tests_combat import make_db

    db = make_db()
    previous_db, previous_hcs = shared._db, hconnect_server._db
    shared._db = hconnect_server._db = db
    db.execute("INSERT OR IGNORE INTO users (id, name, gold, platinum) VALUES (67890, 'camp', 0, 0)")
    db.commit()
    handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
    handler.user_profile = {"id": PLAYER_ID, "name": "camp"}
    handler.client_reck_id, handler.client_uid, handler.sid, handler.scnt = PLAYER_ID, 5, "sid-1", 0
    sent = []
    handler.send = lambda header, body=b"": sent.append((header, body))
    session = GameSession(7020, 1, "camp_9", PLAYER_ID)
    session.encounter_data = {}
    random.seed(1234)
    real_time = time.time
    time.time = lambda: 1700000000.0  # the gzip header stamps the clock
    try:
        with mock.patch("services.ready_to_start.game_session.find_session_by_player",
                        return_value=session):
            _ready_to_start(handler)
    finally:
        time.time = real_time
        shared._db, hconnect_server._db = previous_db, previous_hcs
    return sent


def test_campaign_setup_matches_the_pinned_packets():
    sent = _campaign_run()
    got = [(header["issuer"], hashlib.sha256(body).hexdigest()) for header, body in sent]
    assert got == CAMPAIGN_PACKETS, got


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
