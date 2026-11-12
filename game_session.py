"""
Game session management for Hex TCG private server.

Handles the LoadBalancer service DataTypes (session lifecycle) and
interfaces with the GameSession service for per-session game events.

Architecture:
  LoadBalancer Service (stateless, DataTypes 22001-22045)
    - StartSession, StartEncounter, FindSession, JoinSession
    - ReadyForGameSetup, ReadyForGameEvents, ReadyToStartGame
    - LeaveSession, EndSession

  GameSession Service (stateful, DataTypes 3001-3056)
    - PlayerTransaction, PlayerAdded, PlayerRemoved
    - GameStarted, GameEnded, SessionSyncEvent
    - ReadyForGameSetup, ReadyForGameEvents (session-specific)

Session lifecycle:
  1. Client sends StartEncounter/StartSession -> Server creates session
  2. Client sends JoinSession -> Server adds player
  3. Client sends ReadyForGameSetup -> Server returns opponent info + seeds
  4. Client sends ReadyForGameEvents -> Server acks
  5. Server pushes GameStarted -> Game begins
  6. Game engine processes transactions and pushes SessionSyncEvents
  7. Server pushes GameEnded -> Game ends
  8. Client sends LeaveSession/EndSession -> Cleanup

All session state is stored in the SQLite `game_sessions` table so the
server can run many handler threads concurrently (one thread per client)
without shared in-memory mutable state.
"""

import json
import os

import db as connection_db
import pvp_db as db_layer
from domain.uids import UID_TYPE, make_uid

_DB_PATH = os.environ.get(
    "HEX_DB_PATH",
    os.path.join(os.path.dirname(__file__), "hconnect.db"),
)


class GameSession:
    """A single game session, backed by a row in the game_sessions table."""

    def __init__(self, session_id, server_id, session_name, owner_uid):
        self.session_id = session_id
        self.server_id = server_id
        self.session_name = session_name
        self.owner_uid = owner_uid
        self.players = []
        self.encounter_data = {}
        self.state = "created"
        self.turn_order = []
        self.seed_z = 12345
        self.seed_w = 67890
        self.deck_template_id = "00000000-0000-0000-0000-000000000000"
        # Transient HConnect projection marker reset around each RulesPort
        # transaction.  Keeping it on the shared session lets both client
        # handlers report whether a mutation packet was already emitted.
        self._rules_port_mutation_emitted = False

    def add_player(self, player_uid, player_position, conn=None):
        self.players.append((player_uid, player_position))
        self._persist(conn=conn)

    def set_state(self, state, conn=None):
        self.state = state
        self._persist(conn=conn)

    def get_player_state_list(self):
        return [
            {"PlayerId": puid, "PlayerPosition": ppos}
            for puid, ppos in self.players
        ]

    def get_uid_list(self):
        return [puid for puid, _ in self.players]

    # -- persistence -------------------------------------------------------
    def _persist(self, conn=None):
        _save(self, conn=conn)


def _db():
    # Session state is written from HConnect while the separate tournament
    # scheduler may be updating the same WAL database.  Use the same wait
    # policy as db.py so a transient writer collision does not abort a turn.
    if _DB_PATH == ":memory:":
        # A new ":memory:" connection is an empty database; use the shared connection that holds the schema.
        # Callers close what they open, so the shared connection is handed out with close() disabled.
        return _SharedMemoryConnection(connection_db._db)
    return connection_db.connect(_DB_PATH)


class _SharedMemoryConnection:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        pass


def _next_instance(conn=None):
    """Return a monotonically increasing instance number from the DB.

    Stored in a tiny key/value table so concurrent threads never reuse an
    instance number (and thus never collide on session_id/server_id).
    """
    owns_connection = conn is None
    conn = conn or _db()
    try:
        nxt = db_layer.db_next_session_instance(conn=conn)
        if owns_connection:
            conn.commit()
        return nxt
    finally:
        if owns_connection:
            conn.close()


def create_encounter_session(session_name, encounter_data, player_uid, conn=None):
    """Create a new encounter session and persist it to the DB."""
    inst = _next_instance(conn=conn)
    session_id = make_uid(UID_TYPE["ServiceGameSession"], inst)
    server_id = make_uid(UID_TYPE["ServiceGameSession"], inst * 7)
    session = GameSession(session_id, server_id, session_name, player_uid)
    session.encounter_data = encounter_data or {}
    session.add_player(player_uid, 0, conn=conn)
    session.state = "created"
    session._persist(conn=conn)
    return session


def _save(session, conn=None):
    # Inside a battle dispatch the row is written once when the batch ends.
    if db_layer.queue_session_save(session):
        return
    # Saves run after nearly every transaction, so reuse the server's shared
    # connection (as persistence.save_state does) rather than opening one per
    # save.  Without a caller connection this commits the shared connection.
    owns_commit = conn is None
    conn = connection_db._db if conn is None else conn
    # session_id/server_id/owner_uid are unsigned 64-bit UIDs which can
    # exceed SQLite's signed 64-bit INTEGER range, so store them as TEXT.
    db_layer.db_save_session(session, conn=conn)
    if owns_commit:
        conn.commit()


def _load(row):
    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return v
    s = GameSession(_int(row["session_id"]), _int(row["server_id"]),
                    row["session_name"], _int(row["owner_uid"]))
    s.state = row["state"]
    try:
        s.encounter_data = json.loads(row["encounter_data"] or "{}")
    except (ValueError, TypeError):
        s.encounter_data = {}
    try:
        s.players = [tuple(p) for p in json.loads(row["players_json"] or "[]")]
    except (ValueError, TypeError):
        s.players = []
    try:
        s.turn_order = json.loads(row["turn_order_json"] or "[]")
    except (ValueError, TypeError):
        s.turn_order = []
    s.seed_z = row["seed_z"]
    s.seed_w = row["seed_w"]
    s.deck_template_id = row["deck_template_id"]
    return s


def get_session(session_name):
    conn = _db()
    try:
        row = db_layer.db_get_session_by_name(session_name, conn=conn)
        return _load(row) if row else None
    finally:
        conn.close()


def find_session_by_id(session_id, conn=None):
    owns_connection = conn is None
    conn = conn or _db()
    try:
        row = db_layer.db_get_session(session_id, conn=conn)
        return _load(row) if row else None
    finally:
        if owns_connection:
            conn.close()


def _player_id_variants(player_uid):
    """Both representations of one player: the raw Reckoning ID and the typed ServicePlayer UID."""
    player_uid = int(player_uid)
    raw_player_id = (player_uid >> 8
                     if (player_uid & 0xff) == UID_TYPE["ServicePlayer"]
                     else player_uid)
    return {raw_player_id, make_uid(UID_TYPE["ServicePlayer"], raw_player_id)}


def _row_has_player(row, player_ids):
    try:
        players = json.loads(row["players_json"] or "[]")
    except (ValueError, TypeError):
        return False
    return any(isinstance(p, (list, tuple)) and p and int(p[0]) in player_ids
               for p in players)


def find_session_by_player(player_uid, conn=None):
    """Find the most recent session containing this player.

    Callers use both the raw Reckoning ID and the typed ServicePlayer UID.
    Tournament sessions persist the latter, while reconnect requests carry
    the former, so compare both representations.
    """
    owns_connection = conn is None
    conn = conn or _db()
    try:
        player_ids = _player_id_variants(player_uid)
        for row in db_layer.db_get_sessions(conn=conn):
            if _row_has_player(row, player_ids):
                return _load(row)
        return None
    finally:
        if owns_connection:
            conn.close()


def resolve_session(player_uid, conn=None):
    """Return ``(session, reason)`` for the player's active battle session.

    Ended rows are skipped: they stay in the table for the replay and
    tournament processes, which read them from the file, but a live request
    must not bind to one.  ``reason`` is None when a session is found, and
    otherwise says why none was, for the caller's log line.
    """
    owns_connection = conn is None
    conn = conn or _db()
    try:
        player_ids = _player_id_variants(player_uid)
        ended_match = False
        for row in db_layer.db_get_sessions(conn=conn):
            if not _row_has_player(row, player_ids):
                continue
            if str(row["state"] or "").lower() == "ended":
                ended_match = True
                continue
            return _load(row), None
        return None, ("only ended sessions contain player" if ended_match
                      else "no session contains player")
    finally:
        if owns_connection:
            conn.close()


def remove_session(session_name, conn=None):
    """Remove a session, using the caller's transaction when supplied."""
    owns_connection = conn is None
    conn = conn or _db()
    try:
        db_layer.db_remove_session(session_name, conn=conn)
        if owns_connection:
            conn.commit()
    finally:
        if owns_connection:
            conn.close()


