"""Characterization of the StartEncounter (22017) legacy handler.

Pins the packet the handler sends for the campaign-scene branch and the
default branch, so a later port can be checked against the same bytes.
"""

import hashlib
import os
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database  # noqa: E402

fresh_database()

import hconnect_server as hc  # noqa: E402
from services import dispatch as service_dispatch  # noqa: E402

PLAYER_ID = 67890
SESSION_ID = 7002
SERVER_ID = 7102
CAMPAIGN_SCENE = "11111111-2222-3333-4444-555555555555"
# Pinned from the current handler. Each entry: (issuer, body sha256).
EXPECTED = {
    "campaign": ("0.0.0.0.ServiceLoadBalancer.252.ServicePlayer.5.9001",
                 "6c623f9310414d06258673372842341137f237f03b188e2ce9953295427fe78b"),
    "default": ("0.0.0.0.ServiceLoadBalancer.252.ServicePlayer.5.9001",
                "aef8bd4053cd06416ee8223f3e00cb51c0d28b7f2f7a201e8024338eacc864fe"),
}


class _StubHandler:
    client_reck_id = str(PLAYER_ID)
    client_uid = 5
    sid = "sid-1"
    scnt = 0

    def __init__(self):
        self.sent = []
        self._application = SimpleNamespace(
            execute=lambda command: SimpleNamespace(
                value=SimpleNamespace(session_id=SESSION_ID, server_id=SERVER_ID)))

    def send(self, headers, body=b""):
        self.sent.append((headers, body))


def _run(session_name):
    """Drive the request through the services registry, as the hub does."""
    import importlib
    module_name, fn_name, extra = service_dispatch(22017)
    fn = getattr(importlib.import_module(module_name), fn_name)
    handler = _StubHandler()
    with mock.patch.object(hc.campaign, "_last_encounter_scene",
                           {"camp_7": CAMPAIGN_SCENE}):
        fn(handler, "target", 3, 9000, False, "session-x", "conh-1",
           inner_obj={"SessionName": session_name, "EncounterData": {}},
           inner_bytes=b"", SERVICE_MAIL_UID=252, SERVICE_PROFILE_UID=0,
           log_req=lambda *_a, **_k: True, **extra)
    return handler


def _packet(session_name):
    handler = _run(session_name)
    assert len(handler.sent) == 1, handler.sent
    headers, body = handler.sent[0]
    return headers["issuer"], hashlib.sha256(body).hexdigest()


def test_start_encounter_campaign_scene_branch_matches_the_snapshot():
    assert _packet("camp_7") == EXPECTED["campaign"]


def test_start_encounter_default_branch_matches_the_snapshot():
    assert _packet("tutorial") == EXPECTED["default"]
