"""Characterization of the TestTournamentEntry (25027) handler.

The reply depends on the tournament row: an open tournament answers success,
a missing one does not. The lookup is patched in whichever module currently
serves the request, so the same checks hold before and after the port.
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
# Pinned from the legacy handler. Each entry: body sha256.
EXPECTED = {
    "open": "25323f037cca768dc0109102563033125c01353b2c7c9d04b53c40c7e567143d",
    "missing": "823d2d576873f8f5ffa7a6c2978495cd938431e93d5134b3409386beae0964b1",
}
OPEN_ROOM = {"status": "waiting", "style": "constructed"}


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


def _packet(room):
    entry = service_dispatch(25027)
    handler = _StubHandler()
    if entry:
        module_name, fn_name, extra = entry
        fn = getattr(importlib.import_module(module_name), fn_name)
        patch_target = f"{module_name}.db_tournament_by_id"
        with mock.patch(patch_target, return_value=room):
            fn(handler, "target", 3, REQUEST_ID, False, "session-x", "conh-1",
               inner_obj={"TournamentID": 4}, inner_bytes=b"", SERVICE_MAIL_UID=252,
               SERVICE_PROFILE_UID=0, log_req=lambda *_a, **_k: True, **extra)
    else:
        with mock.patch.object(legacy_tournaments, "db_tournament_by_id", return_value=room):
            legacy_tournaments.LEGACY_HANDLERS[25027](
                handler, "target", 3, 25027, REQUEST_ID, False, "session-x", "conh-1",
                {"TournamentID": 4}, b"", b"")
    headers, body = handler.sent[-1]
    return headers["issuer"], hashlib.sha256(body).hexdigest()


def test_open_tournament_entry_matches_the_snapshot():
    assert _packet(OPEN_ROOM) == (ISSUER, EXPECTED["open"])


def test_missing_tournament_entry_matches_the_snapshot():
    assert _packet(None) == (ISSUER, EXPECTED["missing"])


if __name__ == "__main__":
    test_open_tournament_entry_matches_the_snapshot()
    test_missing_tournament_entry_matches_the_snapshot()
    print("PASS tests_legacy_test_tournament_entry: 2 checks")
