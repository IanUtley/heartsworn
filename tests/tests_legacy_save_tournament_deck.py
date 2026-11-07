"""Characterization of the SaveTournamentDeck (25011) handler.

A deck is accepted only in an Iconoclast room, with 20 main cards that match the
player's pool exactly. The accepted branch rewrites the pool; the rejected branch
writes nothing. Each branch's reply and tournament calls are pinned. The
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

from domain.enums import ETournamentFormats  # noqa: E402
from services import dispatch as service_dispatch  # noqa: E402

ISSUER = "0.0.0.0.ServiceTournaments.252.ServicePlayer.5.9001"
REQUEST_ID = 9000
# Pinned from the legacy handler. Reply body sha256 for each branch.
EXPECTED_REPLY = {
    "accepted": "df3b9e4c0f7bcee850523f0617d0611dd31f2f32f98283cd16a3b8e266784a15",
    "rejected": "7f85391a51c5c0881c2a1b3cb3e38b58443632ca977362083df1edb9d4675071",
}
ICONOCLAST_ROOM = {"format": int(ETournamentFormats.Iconoclast), "style": "constructed"}
PLAIN_ROOM = {"format": 0, "style": "constructed"}
GUID = "11111111-2222-3333-4444-555555555555"


def _deck(count):
    return {"CardsInDeck": [{"Id": 101 + i, "TemplateID": {"guid": GUID}} for i in range(count)],
            "CardsInSideboard": []}


POOL = [(101 + i, GUID, 0) for i in range(20)]


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


def _run(room, pool):
    calls = []

    def recorder(name, ret=None):
        def record(*args, **kwargs):
            shown = [a if isinstance(a, (int, str, bool, tuple, list, dict, type(None)))
                     else type(a).__name__ for a in args]
            calls.append([name, shown, sorted(kwargs)])
            return ret
        return record

    handler = _StubHandler()
    inner = {"TournamentID": 4, "TournamentDeck": _deck(20)}
    module_name, fn_name, extra = service_dispatch(25011)
    fn = getattr(importlib.import_module(module_name), fn_name)
    module = importlib.import_module(module_name)
    patches = [
        mock.patch.object(module, "db_tournament_room_for_game",
                          side_effect=recorder("db_tournament_room_for_game", room)),
        mock.patch.object(module, "db_tournament_pool",
                          side_effect=recorder("db_tournament_pool", pool)),
        mock.patch.object(module, "db_tournament_pool_replace",
                          side_effect=recorder("db_tournament_pool_replace")),
        mock.patch.object(module, "db_tournament_signup_set_async_state",
                          side_effect=recorder("db_tournament_signup_set_async_state")),
        mock.patch.object(module, "_is_corinth_room", return_value=False),
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


def test_accepted_deck_rewrites_the_pool_and_replies_success():
    calls, issuer, reply = _run(ICONOCLAST_ROOM, POOL)
    assert [c[0] for c in calls] == ["db_tournament_room_for_game", "db_tournament_pool",
                                     "db_tournament_pool_replace"]
    assert (issuer, reply) == (ISSUER, EXPECTED_REPLY["accepted"])


def test_rejected_deck_writes_nothing_and_replies_failure():
    calls, issuer, reply = _run(PLAIN_ROOM, POOL)
    assert "db_tournament_pool_replace" not in [c[0] for c in calls]
    assert (issuer, reply) == (ISSUER, EXPECTED_REPLY["rejected"])


if __name__ == "__main__":
    test_accepted_deck_rewrites_the_pool_and_replies_success()
    test_rejected_deck_writes_nothing_and_replies_failure()
    print("PASS tests_legacy_save_tournament_deck: 2 checks")
