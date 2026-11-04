"""Characterization of the StartSession (22015) legacy handler.

Pins the packet the handler sends today, so a port of the handler into the
services layer can be checked against the same bytes.
"""

import hashlib
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database  # noqa: E402

fresh_database()

from services import dispatch as service_dispatch  # noqa: E402

PLAYER_ID = 67890
SESSION_ID = 7001
SESSION_NAME = "characterization"
# sha256 of the datawrapper body the handler sends for the inputs above.
EXPECTED_BODY_SHA256 = "f84416a8222b32f0d7a4442aa04eede8979a849531b94ebb2be297dbe98ecba9"
EXPECTED_ISSUER = "0.0.0.0.ServiceLoadBalancer.252.ServicePlayer.5.9001"


class _StubHandler:
    client_reck_id = str(PLAYER_ID)
    client_uid = 5
    sid = "sid-1"
    scnt = 0

    def __init__(self):
        self.sent = []
        self._application = SimpleNamespace(
            execute=lambda command: SimpleNamespace(
                value=SimpleNamespace(session_id=SESSION_ID)))

    def send(self, headers, body=b""):
        self.sent.append((headers, body))


def _run_handler():
    """Drive the request through the services registry, as the hub does."""
    import importlib
    module_name, fn_name, extra = service_dispatch(22015)
    fn = getattr(importlib.import_module(module_name), fn_name)
    handler = _StubHandler()
    fn(handler, "target", 3, 9000, False, "session-x", "conh-1",
       inner_obj={"SessionName": SESSION_NAME}, inner_bytes=b"",
       SERVICE_MAIL_UID=252, SERVICE_PROFILE_UID=0, log_req=lambda *_a, **_k: True,
       **extra)
    return handler


def test_start_session_sends_one_packet_with_the_expected_issuer():
    handler = _run_handler()
    assert len(handler.sent) == 1, handler.sent
    headers, _body = handler.sent[0]
    assert headers["issuer"] == EXPECTED_ISSUER, headers["issuer"]
    assert headers["reqid"] == 9001 and headers["c"] is False
    assert handler.scnt == 1


def test_start_session_body_matches_the_snapshot():
    _headers, body = _run_handler().sent[0]
    assert hashlib.sha256(body).hexdigest() == EXPECTED_BODY_SHA256
