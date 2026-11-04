"""Characterization of the EnterTournament (25029) handler.

Three outcomes are pinned: an ordinary join that only replies; a failed join with
no existing signup, which sends the enter-error packet; and a Corinth entry, which
starts deck construction. The room-full game start is not covered here. The
functions are patched in whichever module currently serves the request, so the
checks hold before and after the port.
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
from services import legacy_tournaments  # noqa: E402

ISSUER = "0.0.0.0.ServiceTournaments.252.ServicePlayer.5.9001"
REQUEST_ID = 9000
# Pinned from the legacy handler: reply body sha256 per outcome.
EXPECTED_REPLY = {
    "joined": "ddacb975d82ccae97ee63804a59ee7c4c81cf14f0c531c5d5e8aed3e43a306a6",
    "refused": "44a6e373d2231ab910f4c0a14ad9a5b5e85ad8c3e90da1caa48a25d8bd43a774",
    "corinth": "ddacb975d82ccae97ee63804a59ee7c4c81cf14f0c531c5d5e8aed3e43a306a6",
}
CORINTH_ROOM = {"status": "started", "style": "async", "format": 0}
PLAIN_ROOM = {"status": "waiting", "style": "constructed", "format": 0}


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


def _run(room, joined, corinth, signup):
    calls = []

    def recorder(name, ret=None):
        def record(*args, **kwargs):
            shown = [a if isinstance(a, (int, str, bool, tuple, list, dict, type(None)))
                     else type(a).__name__ for a in args]
            calls.append([name, shown, sorted(kwargs)])
            return ret
        return record

    entry = service_dispatch(25029)
    handler = _StubHandler()
    inner = {"TournamentID": 4, "EntryGroup": 0}
    if entry:
        module_name, fn_name, extra = entry
        fn = getattr(importlib.import_module(module_name), fn_name)
        module = importlib.import_module(module_name)
        registry = True
    else:
        module = legacy_tournaments
        registry = False
    patches = [
        mock.patch.object(module, "db_tournament_room_for_game",
                          side_effect=recorder("db_tournament_room_for_game", room)),
        mock.patch.object(module, "db_tournament_signup_by_player",
                          side_effect=recorder("db_tournament_signup_by_player", signup)),
        mock.patch.object(module, "db_tournament_signups_by_tournament",
                          side_effect=recorder("db_tournament_signups_by_tournament", [])),
        mock.patch.object(module, "_is_corinth_room", return_value=corinth),
        mock.patch.object(module, "db_tournament_pool",
                          side_effect=recorder("db_tournament_pool", [])),
        mock.patch.object(module, "db_seed_tournament_pool",
                          side_effect=recorder("db_seed_tournament_pool")),
        mock.patch.object(module, "_push_corinth_deck_construction",
                          side_effect=recorder("_push_corinth_deck_construction")),
        mock.patch.object(module, "start_waiting_room_game",
                          side_effect=recorder("start_waiting_room_game")),
        mock.patch.object(module, "player_decks", {}),
        mock.patch.object(module, "player_handlers", {}),
        mock.patch.object(tournament_server, "leave_all",
                          side_effect=recorder("leave_all")),
        mock.patch.object(tournament_server, "join_tournament",
                          side_effect=recorder("join_tournament", joined)),
    ]
    for p in patches:
        p.start()
    try:
        if registry:
            fn(handler, "target", 3, REQUEST_ID, False, "session-x", "conh-1",
               inner_obj=inner, inner_bytes=b"", SERVICE_MAIL_UID=252,
               SERVICE_PROFILE_UID=0, log_req=lambda *_a, **_k: True, **extra)
        else:
            legacy_tournaments.LEGACY_HANDLERS[25029](
                handler, "target", 3, 25029, REQUEST_ID, False, "session-x", "conh-1",
                inner, b"", b"")
    finally:
        for p in patches:
            p.stop()
    headers, body = handler.sent[-1]
    return calls, headers["issuer"], hashlib.sha256(body).hexdigest()


def test_ordinary_join_only_replies():
    calls, issuer, reply = _run(PLAIN_ROOM, joined=(True, 1, 4, 0), corinth=False, signup=None)
    names = [c[0] for c in calls]
    assert "_push_corinth_deck_construction" not in names
    assert "start_waiting_room_game" not in names
    assert (issuer, reply) == (ISSUER, EXPECTED_REPLY["joined"])


def test_failed_join_without_a_signup_sends_the_enter_error():
    calls, issuer, reply = _run(PLAIN_ROOM, joined=(False, 0, 0, 0), corinth=False, signup=None)
    assert [c[0] for c in calls][0] == "db_tournament_room_for_game"
    assert "join_tournament" in [c[0] for c in calls]
    assert (issuer, reply) == (ISSUER, EXPECTED_REPLY["refused"])


def test_corinth_entry_starts_deck_construction():
    calls, issuer, reply = _run(CORINTH_ROOM, joined=(True, 1, 4, 0), corinth=True, signup=None)
    assert "_push_corinth_deck_construction" in [c[0] for c in calls]
    assert (issuer, reply) == (ISSUER, EXPECTED_REPLY["corinth"])


if __name__ == "__main__":
    test_ordinary_join_only_replies()
    test_failed_join_without_a_signup_sends_the_enter_error()
    test_corinth_entry_starts_deck_construction()
    print("PASS tests_legacy_enter_tournament: 3 checks")
