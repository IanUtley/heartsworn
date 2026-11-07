"""Characterization of the LeaveTournament (25031) handler.

The reply is fixed, so it is pinned by hash. What differs by branch is the
sequence of tournament calls: a forfeit that completes the tournament cleans up
the PvP session; a plain leave retires the player's run. Each check records the
calls and asserts the exact sequence. The functions are patched in whichever
module currently serves the request, so the checks hold before and after the port.
"""

import hashlib
import importlib
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database  # noqa: E402

fresh_database()

import gamemodes.tournament_server as tournament_server  # noqa: E402
from services import dispatch as service_dispatch  # noqa: E402

ISSUER = "0.0.0.0.ServiceTournaments.252.ServicePlayer.5.9001"
REPLY_SHA256 = "8abd2874d84a0db03a61b03b741babd0fb2c855c5ca031ac6b35d099b47e8ded"
REQUEST_ID = 9000
PLAIN_LEAVE_CALLS = [
    ["db_tournament_match_session", [4, 67890], ["conn"]],
    ["record_tournament_forfeit", [4, 67890, "_StubHandler"], []],
    ["db_tournament_retire_player_run", [4, 67890], ["conn"]],
    ["leave_all", [67890], []],
]
FORFEIT_COMPLETE_CALLS = [
    ["db_tournament_match_session", [4, 67890], ["conn"]],
    ["record_tournament_forfeit", [4, 67890, "_StubHandler"], []],
    ["db_tournament_status", [4], ["conn"]],
    ["db_delete_game_session", [77], []],
    ["leave_all", [67890], []],
]


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


def _run(forfeit, status, session):
    calls = []

    def recorder(name, ret=None):
        def record(*args, **kwargs):
            shown = [a if isinstance(a, (int, str, bool, tuple, list, type(None)))
                     else type(a).__name__ for a in args]
            calls.append([name, shown, sorted(kwargs)])
            return ret
        return record

    module_name, fn_name, extra = service_dispatch(25031)
    handler = _StubHandler()
    fn = getattr(importlib.import_module(module_name), fn_name)
    module = importlib.import_module(module_name)
    patches = [
        mock.patch.object(module, "db_tournament_match_session",
                          side_effect=recorder("db_tournament_match_session", session)),
        mock.patch.object(module, "record_tournament_forfeit",
                          side_effect=recorder("record_tournament_forfeit", forfeit)),
        mock.patch.object(module, "db_tournament_status",
                          side_effect=recorder("db_tournament_status", status)),
        mock.patch.object(module, "db_delete_game_session",
                          side_effect=recorder("db_delete_game_session")),
        mock.patch.object(module, "db_tournament_retire_player_run",
                          side_effect=recorder("db_tournament_retire_player_run")),
        mock.patch.object(tournament_server, "leave_all",
                          side_effect=recorder("leave_all")),
    ]
    for p in patches:
        p.start()
    try:
        fn(handler, "target", 3, REQUEST_ID, False, "session-x", "conh-1",
           inner_obj={"TournamentID": 4}, inner_bytes=b"",
           SERVICE_MAIL_UID=252, SERVICE_PROFILE_UID=0,
           log_req=lambda *_a, **_k: True, **extra)
    finally:
        for p in patches:
            p.stop()
    headers, body = handler.sent[-1]
    return calls, headers["issuer"], hashlib.sha256(body).hexdigest()


def test_plain_leave_calls_and_reply_match_the_snapshot():
    calls, issuer, reply = _run(forfeit=False, status=None, session=None)
    assert calls == PLAIN_LEAVE_CALLS
    assert (issuer, reply) == (ISSUER, REPLY_SHA256)


def test_forfeit_that_completes_the_tournament_cleans_up_and_replies():
    calls, issuer, reply = _run(forfeit=True, status="complete", session=(77,))
    assert calls == FORFEIT_COMPLETE_CALLS
    assert (issuer, reply) == (ISSUER, REPLY_SHA256)


if __name__ == "__main__":
    test_plain_leave_calls_and_reply_match_the_snapshot()
    test_forfeit_that_completes_the_tournament_cleans_up_and_replies()
    print("PASS tests_legacy_leave_tournament: 2 checks")
