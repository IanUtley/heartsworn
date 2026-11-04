"""Characterization of the QuitOnReconnectionGame (22011) legacy handler.

The request is fire-and-forget: it sends no packet. These checks pin what it
records and logs for the paths a player can reach: no session, an ended
session, a PvP session that records the decline, and a session that cannot be
reconnected to.
"""

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


class _StubHandler:
    client_reck_id = str(PLAYER_ID)
    client_uid = 5
    sid = "sid-1"
    scnt = 0

    def __init__(self):
        self.sent = []

    def send(self, headers, body=b""):
        self.sent.append((headers, body))


def _drive(session, pvp_state=None):
    """Drive the request through the services registry; return (logs, save mock, handler)."""
    import importlib
    module_name, fn_name, extra = service_dispatch(22011)
    fn = getattr(importlib.import_module(module_name), fn_name)
    logs = []
    handler = _StubHandler()
    lock = mock.MagicMock()
    with mock.patch.object(hc.game_session, "find_session_by_player",
                           return_value=session), \
         mock.patch(f"{module_name}.pvp_session_lock", return_value=lock), \
         mock.patch(f"{module_name}.pvp_load_state", return_value=pvp_state), \
         mock.patch(f"{module_name}.pvp_save_state") as save:
        fn(handler, "target", 3, 9000, False, "session-x", "conh-1",
           inner_obj={}, inner_bytes=b"", SERVICE_MAIL_UID=252,
           SERVICE_PROFILE_UID=0, log_req=logs.append, **extra)
    return logs, save, handler


def test_no_session_is_logged_and_nothing_is_saved():
    logs, save, handler = _drive(None)
    assert logs == [">>> QuitOnReconnectionGame: no session"]
    save.assert_not_called()
    assert handler.sent == []


def test_ended_session_is_ignored():
    session = SimpleNamespace(session_name="tourney-1", state="ended", encounter_data=None)
    logs, save, _handler = _drive(session)
    assert logs == [f">>> QuitOnReconnectionGame: ignored ended session for pid {PLAYER_ID}"]
    save.assert_not_called()


def test_pvp_decline_is_recorded_and_saved():
    session = SimpleNamespace(session_name="tourney-2", state="", encounter_data=None)
    logs, save, _handler = _drive(session, pvp_state={"pvp": True})
    assert logs == [f">>> QuitOnReconnectionGame: pid {PLAYER_ID} declined reconnect "
                    "for PvP session; other participants remain eligible"]
    save.assert_called_once()
    saved_session, saved_state = save.call_args.args
    assert saved_session is session and saved_state.get("pvp") is True


def test_non_reconnectable_session_is_ignored():
    session = SimpleNamespace(session_name="camp_3", state="", encounter_data=None)
    logs, save, _handler = _drive(session, pvp_state={})
    assert logs == [f">>> QuitOnReconnectionGame: ignored non-reconnectable session "
                    f"for pid {PLAYER_ID}"]
    save.assert_not_called()
