"""PlayerTransaction session resolution: ended rows are skipped; a missing session is logged and acknowledged."""

import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database

fresh_database()   # bind this process's database before ``db`` is imported

import db
from game_session import resolve_session
from services import transactions


PLAYER = 42


def test_resolve_session_skips_newer_ended_row():
    conn = db._db
    rows = [("9101", "resolve-older-active", "active", "2026-01-01 10:00:00"),
            ("9102", "resolve-newer-ended", "ended", "2026-01-02 10:00:00")]
    try:
        for sid, name, state, created in rows:
            conn.execute(
                "INSERT INTO game_sessions (session_id, server_id, session_name, owner_uid, "
                "state, players_json, created_at) VALUES (?,?,?,?,?,?,?)",
                (sid, "9", name, str(PLAYER), state, json.dumps([[PLAYER, 0]]), created))
        conn.commit()
        session, reason = resolve_session(PLAYER, conn=conn)
        assert reason is None
        assert session.session_name == "resolve-older-active"
    finally:
        conn.execute("DELETE FROM game_sessions WHERE session_name LIKE 'resolve-%'")
        conn.commit()


def test_missing_session_logs_error_and_acks():
    handler = mock.Mock()
    handler.client_reck_id = PLAYER
    handler._game_sync_send_count = 0
    logged = []

    def capture(msg, level="INFO"):
        logged.append((msg, level))
        return True

    with mock.patch.object(transactions.game_session, "resolve_session",
                           return_value=(None, "no session contains player")):
        transactions.handle_player_transaction(
            handler, "target", 777, 1, 2, "guid-from-request", 0, {}, b"",
            SERVICE_MAIL_UID=0, log_req=capture, data_type=0)

    errors = [msg for msg, level in logged if level == "ERROR"]
    assert errors, logged
    assert "reason=no session contains player" in errors[0]
    handler._push_transaction_ack.assert_called_once_with(
        None, session_id=0, server_id=777)
