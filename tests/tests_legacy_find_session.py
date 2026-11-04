"""Characterization of the FindSession (22019) legacy handler.

Pins the packet for the resume branch (a live campaign battle to rejoin) and
the no-session branch, so a later port can be checked against the same bytes.
"""

import hashlib
import os
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database  # noqa: E402

fresh_database()

from services import dispatch as service_dispatch  # noqa: E402

PLAYER_ID = 67890
RESUME_SESSION = SimpleNamespace(
    session_id=7003, session_name="camp_7",
    encounter_data={"SceneTemplateId": {"m_Guid": "11111111-2222-3333-4444-555555555555"},
                    "SessionFlags": 1})
# Pinned from the current handler. Each entry: (issuer, body sha256).
EXPECTED = {
    "resume": ("0.0.0.0.ServiceLoadBalancer.252.ServicePlayer.5.9001",
               "ef9a2ac6f66948523a0a131e8e7a449aa1fd712bbaacc81f41595418a8751c9b"),
    "none": ("0.0.0.0.ServiceLoadBalancer.252.ServicePlayer.5.9001",
             "039f8a7d8ecbbd4ae32001000b1894380ea2ed240eb3631bf52906f8822c1022"),
}


class _StubHandler:
    client_reck_id = str(PLAYER_ID)
    client_uid = 5
    sid = "sid-1"
    scnt = 0

    def __init__(self):
        self.sent = []

    def send(self, headers, body=b""):
        self.sent.append((headers, body))


def _packet(resume):
    """Drive the request through the services registry, as the hub does."""
    import importlib
    module_name, fn_name, extra = service_dispatch(22019)
    fn = getattr(importlib.import_module(module_name), fn_name)
    handler = _StubHandler()
    with mock.patch(f"{module_name}._campaign_resume_session", return_value=resume):
        fn(handler, "target", 3, 9000, False, "session-x", "conh-1",
           inner_obj={}, inner_bytes=b"", SERVICE_MAIL_UID=252,
           SERVICE_PROFILE_UID=0, log_req=lambda *_a, **_k: True, **extra)
    assert len(handler.sent) == 1, handler.sent
    headers, body = handler.sent[0]
    return headers["issuer"], hashlib.sha256(body).hexdigest()


def test_find_session_resume_branch_matches_the_snapshot():
    assert _packet(RESUME_SESSION) == EXPECTED["resume"]


def test_find_session_no_session_branch_matches_the_snapshot():
    assert _packet(None) == EXPECTED["none"]

if __name__ == "__main__":
    test_find_session_resume_branch_matches_the_snapshot()
    test_find_session_no_session_branch_matches_the_snapshot()
    print("PASS tests_legacy_find_session: 2 checks")
