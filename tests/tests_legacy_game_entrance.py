"""Characterization of the GameEntrance (25039) handler.

Three outcomes are pinned: cancelling a Corinth search leaves the saved pool and
clears the searching flag; a search in a room that is not Iconoclast replies failure and
applies nothing; a new Corinth search with an eligible deck re-activates the
signup, marks it searching and starts matchmaking. Each check pins the reply and
the tournament calls. The functions are patched in whichever module currently
serves the request, so the checks hold before and after the port.
"""

import hashlib
import importlib
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database  # noqa: E402

fresh_database()

from domain.enums import ETournamentFormats  # noqa: E402
from services import dispatch as service_dispatch  # noqa: E402

ISSUER = "0.0.0.0.ServiceTournaments.252.ServicePlayer.5.9001"
REQUEST_ID = 9000
# Pinned from the legacy handler: reply body sha256 per outcome.
EXPECTED_REPLY = {
    "cancel": "a358e4d2454832cc2eb9ecfcfe20232d75e54a98b689addd387641f8505c1d08",
    "non_iconoclast": "7617d1ce8bb928ab0feb24b1cb8a0d24fd755e04c9b7929e0a12e0ea801c7c7b",
    "corinth_new_search": "a358e4d2454832cc2eb9ecfcfe20232d75e54a98b689addd387641f8505c1d08",
}
ICONOCLAST = {"format": int(ETournamentFormats.Iconoclast), "style": "async", "status": "started"}
PLAIN = {"format": 0, "style": "constructed", "status": "waiting"}
MAIN_POOL = [(101 + i, "11111111-2222-3333-4444-555555555555", 0) for i in range(20)]


class _StubHandler:
    client_reck_id = "67890"
    client_uid = 5
    sid = "sid-1"
    scnt = 0
    user_profile = {"name": "Tester"}

    def __init__(self):
        self.sent = []

    def send(self, headers, body=b""):
        self.sent.append((headers, body))


def _run(room, corinth, searching, match, signup):
    calls = []

    def recorder(name, ret=None):
        def record(*args, **kwargs):
            shown = [a if isinstance(a, (int, str, bool, tuple, list, dict, type(None)))
                     else type(a).__name__ for a in args]
            calls.append([name, shown, sorted(kwargs)])
            return ret
        return record

    handler = _StubHandler()
    inner = {"TournamentID": 4, "searching": searching}
    module_name, fn_name, extra = service_dispatch(25039)
    fn = getattr(importlib.import_module(module_name), fn_name)
    module = importlib.import_module(module_name)
    patches = [
        mock.patch.object(module, "db_tournament_room_for_game",
                          side_effect=recorder("db_tournament_room_for_game", room)),
        mock.patch.object(module, "db_tournament_pool",
                          side_effect=recorder("db_tournament_pool", MAIN_POOL)),
        mock.patch.object(module, "_is_corinth_room", return_value=corinth),
        mock.patch.object(module, "db_tournament_match_session",
                          side_effect=recorder("db_tournament_match_session", match)),
        mock.patch.object(module, "db_game_session_pids",
                          side_effect=recorder("db_game_session_pids", [])),
        mock.patch.object(module, "db_tournament_signup_by_player",
                          side_effect=recorder("db_tournament_signup_by_player", signup)),
        mock.patch.object(module, "db_tournament_signup_set_async_state",
                          side_effect=recorder("db_tournament_signup_set_async_state")),
        mock.patch.object(module, "db_tournament_signup_set_status",
                          side_effect=recorder("db_tournament_signup_set_status")),
        mock.patch.object(module, "db_tournament_discard_match",
                          side_effect=recorder("db_tournament_discard_match")),
        mock.patch.object(module, "db_delete_game_session",
                          side_effect=recorder("db_delete_game_session")),
        mock.patch.object(module, "db_apply_tournament_main_deck",
                          side_effect=recorder("db_apply_tournament_main_deck")),
        mock.patch.object(module, "try_start_corinth_match",
                          side_effect=recorder("try_start_corinth_match")),
        mock.patch.object(module, "push_tournament_session_start",
                          side_effect=recorder("push_tournament_session_start")),
    ]
    for p in patches:
        p.start()
    try:
        fn(handler, "target", 3, REQUEST_ID, False, "session-x", "conh-1",
           inner_obj=inner, inner_bytes=b"", SERVICE_MAIL_UID=252,
           SERVICE_PROFILE_UID=0, log_req=lambda *_a, **_k: True, **extra)
    finally:
        for p in patches:
            p.stop()
    headers, body = handler.sent[-1]
    return calls, headers["issuer"], hashlib.sha256(body).hexdigest()


def test_cancelling_a_corinth_search_clears_the_flag_and_keeps_the_pool():
    calls, issuer, reply = _run(ICONOCLAST, corinth=True, searching=False, match=None, signup=None)
    names = [c[0] for c in calls]
    assert "db_tournament_signup_set_async_state" in names
    assert "try_start_corinth_match" not in names
    assert (issuer, reply) == (ISSUER, EXPECTED_REPLY["cancel"])


def test_search_in_a_non_iconoclast_room_replies_failure_and_applies_nothing():
    calls, issuer, reply = _run(PLAIN, corinth=False, searching=True, match=None, signup=None)
    names = [c[0] for c in calls]
    assert "db_apply_tournament_main_deck" not in names
    assert "push_tournament_session_start" not in names
    assert (issuer, reply) == (ISSUER, EXPECTED_REPLY["non_iconoclast"])


def test_new_corinth_search_reactivates_the_signup_and_starts_matchmaking():
    calls, issuer, reply = _run(ICONOCLAST, corinth=True, searching=True, match=None,
                                signup={"status": "withdrawn", "searching": 0})
    names = [c[0] for c in calls]
    assert "db_tournament_signup_set_status" in names
    assert "try_start_corinth_match" in names
    assert (issuer, reply) == (ISSUER, EXPECTED_REPLY["corinth_new_search"])


if __name__ == "__main__":
    test_cancelling_a_corinth_search_clears_the_flag_and_keeps_the_pool()
    test_search_in_a_non_iconoclast_room_replies_failure_and_applies_nothing()
    test_new_corinth_search_reactivates_the_signup_and_starts_matchmaking()
    print("PASS tests_legacy_game_entrance: 3 checks")
