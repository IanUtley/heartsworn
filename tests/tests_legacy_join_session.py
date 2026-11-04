"""Characterization of the JoinSession (22021) legacy handler.

Pins the packet for the found branch (the inner bytes name a session the
application resolves) and the not-found branch, so a later port can be
checked against the same bytes.
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
# A JoinSession inner body with a SessionId whose m_UID64 holds 0x...07.
FOUND_INNER = b"SessionId m_UID64;0;1;1;0000000000000007;tail"
NOT_FOUND_INNER = b"no session id here"
JOINED = SimpleNamespace(
    session_id=7004, session_name="joinable",
    players=[SimpleNamespace(player_id=PLAYER_ID)])
# Pinned from the current handler. Each entry: (issuer, body sha256).
EXPECTED = {
    "found": ("0.0.0.0.ServiceLoadBalancer.252.ServicePlayer.5.9001",
              "8664c3abef86d5267b841cba1f6274b01295e3b9364d8d9f4ddb35a3864d8dbb"),
    "not_found": ("0.0.0.0.ServiceLoadBalancer.252.ServicePlayer.5.9001",
                  "e8d4b03536682c7ec5d76ccc5bbd9d2209094a312f08a6dc7dd2017f15568326"),
}


class _StubHandler:
    client_reck_id = str(PLAYER_ID)
    client_uid = 5
    sid = "sid-1"
    scnt = 0
    user_profile = None
    _pending_player_stops = None

    def __init__(self, session):
        self.sent = []
        self._application = SimpleNamespace(
            execute=lambda command: SimpleNamespace(value=session))

    def _extract_enum_list(self, inner, first, second):
        return None

    def _save_player_stops(self, *args):
        pass

    def send(self, headers, body=b""):
        self.sent.append((headers, body))


def _packet(inner, session):
    """Drive the request through the services registry, as the hub does."""
    import importlib
    module_name, fn_name, extra = service_dispatch(22021)
    fn = getattr(importlib.import_module(module_name), fn_name)
    handler = _StubHandler(session)
    fn(handler, "target", 3, 9000, False, "session-x", "conh-1",
       inner_obj={}, inner_bytes=inner, SERVICE_MAIL_UID=252,
       SERVICE_PROFILE_UID=0, log_req=lambda *_a, **_k: True, **extra)
    assert len(handler.sent) == 1, handler.sent
    headers, body = handler.sent[0]
    return headers["issuer"], hashlib.sha256(body).hexdigest()


def test_join_session_found_branch_matches_the_snapshot():
    assert _packet(FOUND_INNER, JOINED) == EXPECTED["found"]


def test_join_session_not_found_branch_matches_the_snapshot():
    assert _packet(NOT_FOUND_INNER, None) == EXPECTED["not_found"]
