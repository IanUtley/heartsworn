"""Characterization of the PlayerTransaction (3029) handler.

Three outcomes are pinned by the handler methods they call, in order:
no session answers nothing; a session pass-priority transaction and a session
quit transaction each go through fresh-game setup, the rules-port dispatch with
the classified kind, and the transaction acknowledgement. The session name is
not a replay session, so the trace capture (database writes) is not exercised.
The handler is resolved through the services registry when it is registered,
falling back to the legacy table until it is ported.
"""

import importlib
import os
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database  # noqa: E402

fresh_database()

from services import dispatch as service_dispatch  # noqa: E402
from services import legacy_session  # noqa: E402

REQUEST_ID = 9000
CAMP_SESSION = SimpleNamespace(session_id=7, session_name="camp_1", state="", encounter_data={})
PASS_MARKER = b"PassPriorityTransaction"
QUIT_MARKER = b"QuitGameTransaction"


class _RecordingHandler:
    """Records calls to the handler methods the transaction handler uses."""

    client_reck_id = "67890"
    client_uid = 5
    sid = "sid-1"
    scnt = 0
    user_profile = {"id": 9}
    _application = None

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith(("_handle", "_dispatch", "_push", "_fresh_game", "_resolve")):
            def record(*args, **kwargs):
                kinds = []
                for arg in args:
                    if type(arg).__name__ == "PlayerTransactionCommand":
                        kinds = sorted(f for f in vars(arg)
                                       if f.startswith(("is_pass", "is_quit", "is_choose",
                                                        "is_mulligan", "is_cheat"))
                                       and getattr(arg, f) is True)
                self.calls.append((name, kinds))
                return True
            return record
        raise AttributeError(name)


def _run(session, marker):
    handler = _RecordingHandler()
    inner = marker or b""
    entry = service_dispatch(3029)
    with mock.patch.object(legacy_session.game_session, "find_session_by_player",
                           return_value=session):
        if entry:
            module_name, fn_name, extra = entry
            fn = getattr(importlib.import_module(module_name), fn_name)
            fn(handler, "target", 3, REQUEST_ID, False, "session-x", "conh-1",
               inner_obj={}, inner_bytes=inner, SERVICE_MAIL_UID=252,
               SERVICE_PROFILE_UID=0, log_req=lambda *_a, **_k: True, **extra)
        else:
            legacy_session.LEGACY_HANDLERS[3029](
                handler, "target", 3, 3029, REQUEST_ID, False, "session-x", "conh-1",
                {}, inner, b"")
    return handler.calls


def test_no_session_transaction_is_not_routed():
    assert _run(None, PASS_MARKER) == []


def test_session_pass_priority_is_routed_to_the_rules_port_dispatch():
    assert _run(CAMP_SESSION, PASS_MARKER) == [
        ("_fresh_game", []),
        ("_dispatch_rules_port_transaction", ["is_pass_priority"]),
        ("_push_transaction_ack", []),
    ]


def test_session_quit_is_routed_to_the_rules_port_dispatch():
    assert _run(CAMP_SESSION, QUIT_MARKER) == [
        ("_fresh_game", []),
        ("_dispatch_rules_port_transaction", ["is_quit_game"]),
        ("_push_transaction_ack", []),
    ]


if __name__ == "__main__":
    test_no_session_transaction_is_not_routed()
    test_session_pass_priority_is_routed_to_the_rules_port_dispatch()
    test_session_quit_is_routed_to_the_rules_port_dispatch()
    print("PASS tests_legacy_player_transaction: 3 checks")
