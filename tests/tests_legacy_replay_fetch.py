"""Characterization of the ReplayFetch (160000) handler.

Pins the response for a valid replay fetch (replay_fetch is patched so the
bytes are fixed) and for an invalid envelope. The check resolves through the
services registry, falling back to the legacy table until the handler is
ported.
"""

import hashlib
import importlib
import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database  # noqa: E402

fresh_database()

from services import dispatch as service_dispatch  # noqa: E402

REQUEST_ID = 9000
# Pinned from the legacy handler. Each entry: (issuer, body sha256).
EXPECTED = {
    "valid": ("0.0.0.0.ServiceGameSession.246.3.9001",
              "d79ffe9a9017c0a3ca52130bd18fd8dfe897281667ef3d178c7494b200d643cb"),
    "invalid": ("0.0.0.0.ServiceGameSession.246.3.9001",
                "317354e7b441f563f8f72727784aeef815beebb00bb5b7e539d414989ffff3ed"),
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


def _packet(envelope):
    entry = service_dispatch(160000)
    handler = _StubHandler()
    inner = {"Envelope": envelope}
    with mock.patch("services.replay.replay_fetch", return_value=b"REPLAY-BYTES"):
        if entry:
            module_name, fn_name, extra = entry
            fn = getattr(importlib.import_module(module_name), fn_name)
            fn(handler, "target", 3, REQUEST_ID, False, "session-x", "conh-1",
               inner_obj=inner, inner_bytes=b"", SERVICE_MAIL_UID=252,
               SERVICE_PROFILE_UID=0, log_req=lambda *_a, **_k: True, **extra)
        else:
            raise AssertionError("160000 is not registered")
    headers, body = handler.sent[-1]
    return headers["issuer"], hashlib.sha256(body).hexdigest()


def test_valid_replay_fetch_matches_the_snapshot():
    envelope = json.dumps({"action": "replayfetch", "Session": "s1", "Offset": 0}).encode()
    assert _packet(envelope) == EXPECTED["valid"]


def test_invalid_replay_envelope_matches_the_snapshot():
    assert _packet(b"not json") == EXPECTED["invalid"]


if __name__ == "__main__":
    test_valid_replay_fetch_matches_the_snapshot()
    test_invalid_replay_envelope_matches_the_snapshot()
    print("PASS tests_legacy_replay_fetch: 2 checks")
