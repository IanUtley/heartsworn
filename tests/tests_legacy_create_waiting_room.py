"""Characterization of the CreateWaitingRoom (25019) handler.

The reply is an empty response, pinned by hash. The handler also records a
waiting room in the store the tournament module owns; the check reads that
store wherever it lives, so the same test holds before and after the state
moves out of the hub. Resolved through the services registry, falling back to
the legacy table until the handler is ported.
"""

import hashlib
import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database  # noqa: E402

fresh_database()

import hconnect_server as hc  # noqa: E402
from services import dispatch as service_dispatch  # noqa: E402
from services import legacy_tournaments  # noqa: E402

ISSUER = "0.0.0.0.ServiceTournaments.252.ServicePlayer.5.9001"
# Pinned from the legacy handler: body sha256 of the empty CreateWaitingRoom reply.
EXPECTED_BODY = "d51d2ba3dae2b77dd149e97a53ec0bae931095caa62b9a7980ad9ea5a567e5cf"
REQUEST_ID = 9000


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


def _store():
    """The waiting-room store, wherever the tournament module keeps it now."""
    entry = service_dispatch(25019)
    if entry:
        module = importlib.import_module(entry[0])
        return module._waiting_rooms
    return hc._waiting_rooms


def _run(inner):
    entry = service_dispatch(25019)
    handler = _StubHandler()
    if entry:
        module_name, fn_name, extra = entry
        fn = getattr(importlib.import_module(module_name), fn_name)
        fn(handler, "target", 3, REQUEST_ID, False, "session-x", "conh-1",
           inner_obj=inner, inner_bytes=b"", SERVICE_MAIL_UID=252,
           SERVICE_PROFILE_UID=0, log_req=lambda *_a, **_k: True, **extra)
    else:
        legacy_tournaments.LEGACY_HANDLERS[25019](
            handler, "target", 3, 25019, REQUEST_ID, False, "session-x", "conh-1",
            inner, b"", b"")
    return handler


def test_create_waiting_room_replies_empty_and_records_the_room():
    store = _store()
    before = set(store)
    handler = _run({"format": "constructed", "style": "se", "playerCount": 2})
    headers, body = handler.sent[-1]
    assert headers["issuer"] == ISSUER
    assert hashlib.sha256(body).hexdigest() == EXPECTED_BODY
    new = [store[k] for k in set(store) - before]
    assert len(new) == 1, new
    room = new[0]
    assert (room["style"], room["format"], room["playerCount"], room["status"]) == \
        ("se", "constructed", 2, "waiting")


if __name__ == "__main__":
    test_create_waiting_room_replies_empty_and_records_the_room()
    print("PASS tests_legacy_create_waiting_room: 1 check")
