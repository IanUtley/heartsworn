"""Characterization of the TryReconnectionToDisconnectedTournament (25021) handler.

Three outcomes are pinned: a request before auth is deferred; an authenticated
request with no live tournament answers "no active tournament"; an authenticated
request for an active Corinth signup resumes the lobby and pushes the deck
construction. Each check pins the reply and the tournament calls. The functions
are patched in whichever module currently serves the request, so the checks hold
before and after the port.
"""

import hashlib
import importlib
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database  # noqa: E402

fresh_database()

from services import dispatch as service_dispatch  # noqa: E402
from services import legacy_tournaments  # noqa: E402

ISSUER = "0.0.0.0.ServiceTournaments.252.ServicePlayer.5.9001"
REQUEST_ID = 9000
# Pinned from the legacy handler: reply body sha256 per outcome.
EXPECTED_REPLY = {
    "deferred": "b09388c0bfb084285b3c432606ed63ed660d316618f254aa340789d582d0dc5c",
    "no_active": "849cd8a39a19d1b17219b74a560e0d91eb019ff802d74122e2c710c325539a46",
    "resumed": "b09388c0bfb084285b3c432606ed63ed660d316618f254aa340789d582d0dc5c",
}
CORINTH_ROOM = {"status": "started", "style": "async", "format": 0}
ACTIVE_SIGNUP = {"status": "active", "deck_ready": False}


class _StubHandler:
    client_reck_id = "67890"
    client_uid = 5
    sid = "sid-1"
    scnt = 0
    user_profile = {"name": "Tester"}

    def __init__(self, authenticated):
        self.authenticated = authenticated
        self.sent = []

    def send(self, headers, body=b""):
        self.sent.append((headers, body))


def _run(authenticated, room, signup, live, pool):
    calls = []

    def recorder(name, ret=None):
        def record(*args, **kwargs):
            shown = [a if isinstance(a, (int, str, bool, tuple, list, dict, type(None)))
                     else type(a).__name__ for a in args]
            calls.append([name, shown, sorted(kwargs)])
            return ret
        return record

    entry = service_dispatch(25021)
    handler = _StubHandler(authenticated)
    inner = {"forceReconnectToAsynch": 4}
    if entry:
        module_name, fn_name, extra = entry
        fn = getattr(importlib.import_module(module_name), fn_name)
        module = importlib.import_module(module_name)
        registry = True
    else:
        module = legacy_tournaments
        registry = False
    handlers_registry = {}
    patches = [
        mock.patch.object(module, "db_tournament_room_for_game",
                          side_effect=recorder("db_tournament_room_for_game", room)),
        mock.patch.object(module, "db_tournament_signup_by_player",
                          side_effect=recorder("db_tournament_signup_by_player", signup)),
        mock.patch.object(module, "db_tournament_player_run_live",
                          side_effect=recorder("db_tournament_player_run_live", live)),
        mock.patch.object(module, "db_tournament_pool",
                          side_effect=recorder("db_tournament_pool", pool)),
        mock.patch.object(module, "db_seed_tournament_pool",
                          side_effect=recorder("db_seed_tournament_pool")),
        mock.patch.object(module, "db_tournament_signup_set_status",
                          side_effect=recorder("db_tournament_signup_set_status")),
        mock.patch.object(module, "db_tournament_signup_set_async_state",
                          side_effect=recorder("db_tournament_signup_set_async_state")),
        mock.patch.object(module, "_is_corinth_room", return_value=True),
        mock.patch.object(module, "_push_corinth_deck_construction",
                          side_effect=recorder("_push_corinth_deck_construction")),
    ]
    for p in patches:
        p.start()
    try:
        if registry:
            fn(handler, "target", 3, REQUEST_ID, False, "session-x", "conh-1",
               inner_obj=inner, inner_bytes=b"", SERVICE_MAIL_UID=252,
               SERVICE_PROFILE_UID=0, log_req=lambda *_a, **_k: True, **extra)
        else:
            legacy_tournaments.LEGACY_HANDLERS[25021](
                handler, "target", 3, 25021, REQUEST_ID, False, "session-x", "conh-1",
                inner, b"", b"")
    finally:
        for p in patches:
            p.stop()
    headers, body = handler.sent[-1]
    return calls, headers["issuer"], hashlib.sha256(body).hexdigest()


def test_unauthenticated_request_is_deferred_without_database_calls():
    calls, issuer, reply = _run(False, None, None, None, [])
    assert calls == []
    assert (issuer, reply) == (ISSUER, EXPECTED_REPLY["deferred"])


def test_authenticated_request_with_no_live_tournament_answers_no_active():
    calls, issuer, reply = _run(True, None, None, None, [])
    assert [c[0] for c in calls] == ["db_tournament_room_for_game", "db_tournament_signup_by_player"]
    assert (issuer, reply) == (ISSUER, EXPECTED_REPLY["no_active"])


def test_active_corinth_signup_resumes_the_lobby_and_pushes_the_deck():
    pool = [(i, "g", 0) for i in range(1)]
    calls, issuer, reply = _run(True, CORINTH_ROOM, ACTIVE_SIGNUP, False, pool)
    names = [c[0] for c in calls]
    assert names[0] == "db_tournament_room_for_game"
    assert "_push_corinth_deck_construction" in names
    assert "db_tournament_signup_set_status" not in names
    assert (issuer, reply) == (ISSUER, EXPECTED_REPLY["resumed"])


if __name__ == "__main__":
    test_unauthenticated_request_is_deferred_without_database_calls()
    test_authenticated_request_with_no_live_tournament_answers_no_active()
    test_active_corinth_signup_resumes_the_lobby_and_pushes_the_deck()
    print("PASS tests_legacy_tournament_reconnect: 3 checks")
