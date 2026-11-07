"""Characterization of six simple tournament request handlers.

Each handler answers with a fixed response shape. The check resolves a request
through the services registry and compares the issuer and body hash of the
packet sent.
"""

import hashlib
import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database  # noqa: E402

fresh_database()

from services import dispatch as service_dispatch  # noqa: E402

ISSUER = "0.0.0.0.ServiceTournaments.252.ServicePlayer.5.9001"
# Pinned from the handlers. Each entry: body sha256.
EXPECTED = {
    25005: "6c226f1aecfe29e6b8bd1977bf8dd2998fcb5df06686a7025509ba763676dd7c",
    25007: "2a089c20e0c7c74722f2130db070d5640aeaa7cdc564849bada46324e9e6454d",
    25035: "0b51404d5113cf13fdc80104bb48ea5280d251b0c573b3b7cc0a3a1dd65e2500",
    70015: "5bb7e3cc459f6a8882e668d33f92bceb9635615f4fdc50df1770ef175a999518",
    70027: "8e86562adf94ec0aa1bd594fbc21f94697b329c7574f661696f3a692a8546c9c",
    70028: "09e078d2f0826c30b63343f1bad839a813e96b31962ea665c31b94a5f98cf28d",
    70023: "e9c50b5108675b895bc35c538e5f0ba0a547178655751bee44958af1fb458ba5",
}


class _StubHandler:
    client_reck_id = "67890"
    client_uid = 5
    sid = "sid-1"
    scnt = 0
    user_profile = {"id": 9}

    def __init__(self):
        self.sent = []

    def send(self, headers, body=b""):
        self.sent.append((headers, body))


def _packet(reqid):
    module_name, fn_name, extra = service_dispatch(reqid)
    fn = getattr(importlib.import_module(module_name), fn_name)
    handler = _StubHandler()
    request_id = 9000  # the id the pinned packets were captured with
    fn(handler, "target", 3, request_id, False, "session-x", "conh-1",
       inner_obj={"TournamentID": 4}, inner_bytes=b"", SERVICE_MAIL_UID=252,
       SERVICE_PROFILE_UID=0, log_req=lambda *_a, **_k: True, **extra)
    headers, body = handler.sent[-1]
    return headers["issuer"], hashlib.sha256(body).hexdigest()


def _check(reqid):
    assert _packet(reqid) == (ISSUER, EXPECTED[reqid]), reqid


def test_unsubscribe_description_listener_matches_the_snapshot():
    _check(25005)


def test_get_tournament_info_matches_the_snapshot():
    _check(25007)


def test_leave_waiting_room_matches_the_snapshot():
    _check(25035)


def test_pooled_messaging_flush_matches_the_snapshot():
    _check(70015)


def test_ladder_tier_rewards_list_matches_the_snapshot():
    _check(70027)


def test_ladder_ranking_list_matches_the_snapshot():
    _check(70028)


def test_ladder_record_matches_the_snapshot():
    _check(70023)


if __name__ == "__main__":
    test_unsubscribe_description_listener_matches_the_snapshot()
    test_get_tournament_info_matches_the_snapshot()
    test_leave_waiting_room_matches_the_snapshot()
    test_pooled_messaging_flush_matches_the_snapshot()
    test_ladder_tier_rewards_list_matches_the_snapshot()
    test_ladder_ranking_list_matches_the_snapshot()
    test_ladder_record_matches_the_snapshot()
    print("PASS tests_legacy_tournament_simple: 7 checks")
