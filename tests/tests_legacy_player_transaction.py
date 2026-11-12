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
import game_session  # noqa: E402

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
    with mock.patch.object(game_session, "resolve_session", return_value=(session, None)):
        if entry:
            module_name, fn_name, extra = entry
            fn = getattr(importlib.import_module(module_name), fn_name)
            fn(handler, "target", 3, REQUEST_ID, False, "session-x", "conh-1",
               inner_obj={}, inner_bytes=inner, SERVICE_MAIL_UID=252,
               SERVICE_PROFILE_UID=0, log_req=lambda *_a, **_k: True, **extra)
        else:
            raise AssertionError("3029 is not registered")
    return handler.calls


def test_no_session_transaction_is_not_routed():
    # Not dispatched to any handler; only the empty 3055 acknowledgement is sent.
    assert _run(None, PASS_MARKER) == [("_push_transaction_ack", [])]


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


def test_replay_session_capture_records_the_real_wrapper_bytes():
    """Replay traces keep the request's wrapper bytes, which the dispatch now carries."""
    replay_session = SimpleNamespace(session_id=8, session_name="tourney-4", state="",
                                     encounter_data={})
    recorded = {}

    def record_transaction(*args, **kwargs):
        recorded["raw_data_wrapper_bytes"] = kwargs.get("raw_data_wrapper_bytes")
        return 1

    with mock.patch("services.transactions.db_session_state_snapshot", return_value="{}"), \
         mock.patch("services.transactions.db_rules_port_state_snapshot", return_value="{}"), \
         mock.patch("services.transactions.db_record_session_transaction",
                    side_effect=record_transaction), \
         mock.patch("services.transactions.set_session_transaction_trace_ids"):
        from application.service_dispatch import dispatch_service
        handler = _RecordingHandler()
        handler._application = SimpleNamespace(
            dispatch_request=lambda command, run: run(command))
        with mock.patch.object(game_session, "resolve_session",
                               return_value=(replay_session, None)):
            dispatch_service(handler, 3029, "target", 3, REQUEST_ID, False, "session-x",
                             "conh-1", {}, PASS_MARKER,
                             service_uids={"mail": 252, "profile": 0},
                             log_req=lambda *_a, **_k: True,
                             raw_data_wrapper_bytes=b"WRAPPER")
    assert recorded.get("raw_data_wrapper_bytes") == b"WRAPPER"


if __name__ == "__main__":
    test_no_session_transaction_is_not_routed()
    test_session_pass_priority_is_routed_to_the_rules_port_dispatch()
    test_session_quit_is_routed_to_the_rules_port_dispatch()
    test_replay_session_capture_records_the_real_wrapper_bytes()
    print("PASS tests_legacy_player_transaction: 4 checks")
