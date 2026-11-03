"""
Shared SQLite access layer for the Hex TCG private server.

Owns the single module-level connection (``_db``), transactions, logging
context and a small set of shared helpers.  Domain persistence lives in the
domain modules (``pvp_db``, ``profile_db``, ``pve_db``, ``tournament_db``,
``replay_db``, ``chat_db``), which all use this connection.  All schema (DDL)
lives in ``static.py`` (``static.ensure_schema``) — this module only performs
DML::

    from db import _db, transaction
    from profile_db import db_get_inventory

The connection is ``check_same_thread=False`` with WAL so concurrent handler
threads can share it (SQLite serializes writes internally).
"""

import os
import sqlite3
import threading
import time
import hashlib
import re
from contextlib import contextmanager
from contextvars import ContextVar
from binascii import hexlify
from typing import Any, Callable, TypeVar

DB_PATH = os.environ.get(
    "HEX_DB_PATH",
    os.path.join(os.path.dirname(__file__), "hconnect.db"),
)

REQUEST_LOG = "/tmp/hconnect_requests.log"
_log_req_file = open(REQUEST_LOG, "a", buffering=1)
SESSION_LOG_DIR = os.environ.get("HEX_SESSION_LOG_DIR", "/tmp/hconnect_sessions")
PLAYER_LOG_DIR = os.environ.get("HEX_PLAYER_LOG_DIR", SESSION_LOG_DIR)
_log_session_id = ContextVar("hex_log_session_id", default=None)
_log_player_id = ContextVar("hex_log_player_id", default=None)
_log_session_players = ContextVar("hex_log_session_players", default=())

# Logging is intentionally small and dependency-free because this module is
# imported by every service.  Keep INFO as the default for compatibility with
# the existing server diagnostics; callers such as the simulator can select a
# quieter threshold without changing the individual logging call sites.
LOG_LEVELS = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
    "OFF": 100,
}
_LOG_LEVEL_NAMES = {value: name for name, value in LOG_LEVELS.items()}
_log_level_lock = threading.RLock()
log_lock = threading.RLock()


def _coerce_log_level(level: str | int) -> int:
    if isinstance(level, int):
        if level in _LOG_LEVEL_NAMES:
            return level
        raise ValueError(f"unknown log level value: {level}")
    name = str(level).strip().upper()
    try:
        return LOG_LEVELS[name]
    except KeyError as exc:
        valid = ", ".join(LOG_LEVELS)
        raise ValueError(
            f"unknown log level {level!r}; expected one of {valid}") from exc


try:
    _log_level = _coerce_log_level(os.environ.get("HEX_LOG_LEVEL", "INFO"))
except ValueError:
    # A malformed environment setting must not prevent the server from
    # starting.  The explicit set_log_level API still rejects bad values.
    _log_level = LOG_LEVELS["INFO"]


def set_log_level(level: str | int) -> str:
    """Set the process-wide logging threshold and return its canonical name."""
    global _log_level
    value = _coerce_log_level(level)
    with _log_level_lock:
        _log_level = value
    return _LOG_LEVEL_NAMES[value]


def get_log_level() -> str:
    """Return the current process-wide logging threshold."""
    with _log_level_lock:
        return _LOG_LEVEL_NAMES[_log_level]


def _log_enabled(level: str | int) -> bool:
    value = _coerce_log_level(level)
    with _log_level_lock:
        return value >= _log_level


def _normalize_log_token(value):
    """Return a safe, stable token for a log filename or lookup."""
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _normalize_log_session_id(session_id):
    return _normalize_log_token(session_id)


def _normalize_log_participants(participant_ids):
    if participant_ids is None:
        return ()
    if isinstance(participant_ids, (str, bytes)):
        participant_ids = (participant_ids,)
    else:
        try:
            participant_ids = tuple(participant_ids)
        except TypeError:
            participant_ids = (participant_ids,)
    values = {_normalize_log_token(value) for value in participant_ids}
    return tuple(sorted(value for value in values if value is not None))


def set_log_context(player_id=None, session_id=None, participant_ids=None):
    """Bind player and optional game-session logging context."""
    _log_player_id.set(_normalize_log_token(player_id))
    _log_session_id.set(_normalize_log_session_id(session_id))
    _log_session_players.set(_normalize_log_participants(participant_ids))
    return get_log_session()


def get_log_session():
    """Return the current thread/context's bound game-session token."""
    return _log_session_id.get()


def get_log_player():
    """Return the current thread/context's stable profile token."""
    return _log_player_id.get()


def get_log_session_players():
    """Return normalized game participant tokens for the current context."""
    return _log_session_players.get()


def player_log_path(player_id=None):
    """Return a player's cross-session log path, if a player is bound."""
    value = (_normalize_log_token(player_id)
             if player_id is not None else get_log_player())
    if value is None:
        return None
    return os.path.join(PLAYER_LOG_DIR, f"player-{value}.log")


def session_log_path(session_id=None, participant_ids=None):
    """Return the per-session log path, including known player tokens."""
    value = (_normalize_log_session_id(session_id)
             if session_id is not None else get_log_session())
    if value is None:
        return None
    participants = (_normalize_log_participants(participant_ids)
                    if participant_ids is not None
                    else get_log_session_players())
    suffix = "".join(f"-p{participant}" for participant in participants)
    return os.path.join(SESSION_LOG_DIR, f"session-{value}{suffix}.log")


def session_log_paths_for_players(player_ids):
    """Return session logs containing any exact participant token, newest first."""
    tokens = _normalize_log_participants(player_ids)
    if not tokens:
        return []
    try:
        entries = os.scandir(SESSION_LOG_DIR)
    except OSError:
        return []

    matches = []
    try:
        for entry in entries:
            name = entry.name
            if (not entry.is_file() or not name.startswith("session-") or
                    not name.endswith(".log")):
                continue
            if not any(re.search(
                    rf"(?:^|-)p{re.escape(token)}(?:-|\.log$)", name)
                    for token in tokens):
                continue
            try:
                matches.append((entry.stat().st_mtime, entry.path))
            except OSError:
                continue
    finally:
        entries.close()
    matches.sort(key=lambda item: item[0], reverse=True)
    return [path for _mtime, path in matches]


def latest_session_log_path_for_players(player_ids, session_id=None):
    """Return the newest matching session log, optionally for one session."""
    paths = session_log_paths_for_players(player_ids)
    if session_id is not None:
        token = _normalize_log_session_id(session_id)
        prefix = f"session-{token}-"
        exact = f"session-{token}.log"
        paths = [path for path in paths
                 if (os.path.basename(path).startswith(prefix) or
                     os.path.basename(path) == exact)]
    return paths[0] if paths else None


def _write_log_path_locked(path, line):
    """Append one already-timestamped line to one diagnostic log."""
    if path is None:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8", buffering=1) as stream:
            stream.write(line)
    except OSError:
        return


def _write_bound_logs_locked(line):
    """Append the line to the player log and, when present, session log."""
    paths = (player_log_path(), session_log_path())
    written = set()
    for path in paths:
        if path is not None and path not in written:
            _write_log_path_locked(path, line)
            written.add(path)

# SQLITE_BUSY honors the connection busy timeout; SQLITE_LOCKED variants do
# not.  The latter can occur while another service is finishing a short
# session/tournament save, so retry briefly instead of consuming a valid PvP
# action and acknowledging a no-op.
# Keep the explicit SQLITE_LOCKED backoff below the five-second busy timeout.
_SQLITE_RETRY_DELAYS = (0.05, 0.10, 0.25, 0.50, 1.00, 2.00)
_SQLITE_LOCK_CODES = {
    getattr(sqlite3, "SQLITE_BUSY", 5),
    getattr(sqlite3, "SQLITE_LOCKED", 6),
    getattr(sqlite3, "SQLITE_BUSY_SNAPSHOT", 517),
    getattr(sqlite3, "SQLITE_LOCKED_SHAREDCACHE", 262),
}

_named_row_types = {}
_RetryResult = TypeVar("_RetryResult")


def _named_row_factory(cursor, values):
    """Return a tuple-compatible row that also supports column-name access.

    Keeping the tuple base is intentional: a large amount of older server
    code compares returned rows with tuples or iterates them.  New code can
    use ``row[\"column_name\"]`` without having to know where that column is
    positioned in the SELECT list.
    """
    columns = tuple(column[0] for column in cursor.description)
    row_type = _named_row_types.get(columns)
    if row_type is None:
        class NamedRow(tuple):
            __columns__ = columns

            def __getitem__(self, key):
                if isinstance(key, str):
                    try:
                        key = self.__columns__.index(key)
                    except ValueError as exc:
                        raise IndexError(key) from exc
                return super().__getitem__(key)

            def keys(self):
                return self.__columns__

            def get(self, key, default=None):
                try:
                    return self[key]
                except IndexError:
                    return default

        row_type = NamedRow
        row_type.__name__ = "NamedRow"
        _named_row_types[columns] = row_type
    return row_type(values)


def _is_sqlite_lock_error(exc):
    """Whether *exc* is a transient SQLite writer/schema lock."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    message = str(exc).lower()
    return code in _SQLITE_LOCK_CODES or any(
        text in message for text in (
            "database is locked",
            "database table is locked",
            "database schema is locked",
        )
    )


class _LockedCursor:
    """Serialize a cursor's execute/fetch pair on its shared connection.

    ``sqlite3`` permits a connection to be shared when
    ``check_same_thread=False`` is set, but a bare ``execute(...).fetchone()``
    still has two C-level operations with a scheduling point between them.
    The server uses that shape throughout its data layer.  Keeping the
    connection lock until the first fetch prevents another thread from
    interleaving a statement between those operations while retaining the
    existing per-statement retry behavior.
    """

    def __init__(self, connection, cursor):
        self._connection = connection
        self._cursor = cursor
        self._lease_held = False

    def hold_lease(self):
        self._connection._retry_lock.acquire()
        self._lease_held = True

    def _release_lease(self):
        if self._lease_held:
            self._lease_held = False
            self._connection._retry_lock.release()

    def __del__(self):
        # Queries such as startup PRAGMAs are often intentionally discarded
        # without a fetch. Release their connection lease when the temporary
        # cursor wrapper goes out of scope instead of leaving other threads
        # blocked indefinitely. CPython drops these short-lived wrappers at
        # the end of the expression; explicit ``close`` remains available for
        # retained cursors.
        try:
            self._release_lease()
        except (AttributeError, RuntimeError):
            pass

    def fetchone(self):
        if self._lease_held:
            try:
                return self._cursor.fetchone()
            finally:
                self._release_lease()
        with self._connection._retry_lock:
            return self._cursor.fetchone()

    def fetchmany(self, size=None):
        if self._lease_held:
            try:
                if size is None:
                    return self._cursor.fetchmany()
                return self._cursor.fetchmany(size)
            finally:
                self._release_lease()
        with self._connection._retry_lock:
            if size is None:
                return self._cursor.fetchmany()
            return self._cursor.fetchmany(size)

    def fetchall(self):
        if self._lease_held:
            try:
                return self._cursor.fetchall()
            finally:
                self._release_lease()
        with self._connection._retry_lock:
            return self._cursor.fetchall()

    def __iter__(self):
        return self

    def __next__(self):
        if self._lease_held:
            try:
                return next(self._cursor)
            finally:
                self._release_lease()
        with self._connection._retry_lock:
            return next(self._cursor)

    def close(self):
        if self._lease_held:
            try:
                return self._cursor.close()
            finally:
                self._release_lease()
        with self._connection._retry_lock:
            return self._cursor.close()

    def __enter__(self):
        with self._connection._retry_lock:
            self._cursor.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            with self._connection._retry_lock:
                return self._cursor.__exit__(exc_type, exc_value, traceback)
        finally:
            self._release_lease()

    def __getattr__(self, name):
        value = getattr(self._cursor, name)
        if not callable(value):
            return value

        def call(*args, **kwargs):
            with self._connection._retry_lock:
                return value(*args, **kwargs)

        return call


# Process-wide count of write statements.  Projections that memoize per card
# key their entries on this value, so any write made through a connection
# retires every memo entry.  It is bumped after the statement runs (even if it
# raised, since a failed statement can still have applied part of its effect)
# so a reader can never tag a pre-write value with the post-write version.
_write_version = 0
_write_version_lock = threading.Lock()
_WRITE_KEYWORDS = frozenset({"INSERT", "UPDATE", "DELETE", "REPLACE",
                             "CREATE", "DROP", "ALTER"})


def board_write_version():
    """Return the number of write statements run so far in this process."""
    with _write_version_lock:
        return _write_version


def _note_write():
    global _write_version
    with _write_version_lock:
        _write_version += 1


def _is_write_statement(sql):
    words = str(sql or "").lstrip().split(None, 1)
    return not words or words[0].upper() in _WRITE_KEYWORDS


class RetryingConnection(sqlite3.Connection):
    """SQLite connection that retries only transient lock operations.

    Retrying individual statements/commits is safe for this application:
    retrying a whole helper could duplicate an INSERT or other side effect.
    The connection-level busy timeout handles normal writer contention; the
    short retry loop also covers SQLITE_LOCKED variants that do not honor the
    busy handler.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._retry_lock = threading.RLock()

    def _with_retry(self, operation: Callable[..., _RetryResult],
                    *args: Any, **kwargs: Any) -> _RetryResult:
        for attempt, delay in enumerate((0.0,) + _SQLITE_RETRY_DELAYS):
            try:
                return operation(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                if (not _is_sqlite_lock_error(exc)
                        or attempt >= len(_SQLITE_RETRY_DELAYS)):
                    raise
                if delay:
                    time.sleep(delay)
        raise RuntimeError("SQLite retry loop exhausted without a result")

    def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        with self._retry_lock:
            try:
                cursor = self._with_retry(super().execute, sql, parameters)
            finally:
                if _is_write_statement(sql):
                    _note_write()
            wrapped = _LockedCursor(self, cursor)
            # A statement with a result set is normally consumed immediately
            # by ``execute(...).fetchone/fetchall``. Hold the lock through that
            # first fetch so another thread cannot interleave on this shared
            # sqlite3 connection. DML cursors have no result set and release
            # the execute lock as usual.
            if cursor.description is not None:
                wrapped.hold_lease()
            return wrapped

    def executemany(self, sql: str, parameters: Any) -> sqlite3.Cursor:
        with self._retry_lock:
            try:
                cursor = self._with_retry(super().executemany, sql, parameters)
            finally:
                _note_write()
            wrapped = _LockedCursor(self, cursor)
            if cursor.description is not None:
                wrapped.hold_lease()
            return wrapped

    def executescript(self, sql_script: str) -> sqlite3.Cursor:
        with self._retry_lock:
            try:
                cursor = self._with_retry(super().executescript, sql_script)
            finally:
                _note_write()
            wrapped = _LockedCursor(self, cursor)
            if cursor.description is not None:
                wrapped.hold_lease()
            return wrapped

    def commit(self) -> None:
        with self._retry_lock:
            return self._with_retry(super().commit)

    def rollback(self) -> None:
        with self._retry_lock:
            return super().rollback()


def connect(database_path=None, *, check_same_thread=True):
    """Open a short-lived application connection.

    The legacy module-level ``_db`` connection remains available while the
    server is migrated. New application commands should use this factory so
    their transaction is owned by the command boundary rather than by an
    individual SQL helper.
    """
    conn = sqlite3.connect(
        database_path or DB_PATH,
        timeout=5.0,
        factory=RetryingConnection,
        check_same_thread=check_same_thread,
        # Autocommit: a write releases SQLite's write lock immediately, so a
        # helper that forgets to commit can no longer pin the database and
        # block the other processes.  Operations that must be atomic use the
        # explicit ``transaction()`` context manager (BEGIN IMMEDIATE).
        isolation_level=None,
    )
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = _named_row_factory
    return conn


@contextmanager
def transaction(database_path=None, immediate=True):
    """Run one application operation in an explicit SQLite transaction.

    Transactions are intentionally short-lived. Callers must not wait for
    client input or publish network events while inside this context.
    ``BEGIN IMMEDIATE`` serializes competing state-changing commands early,
    which is important for game state and priority transitions.
    """
    conn = connect(database_path)
    try:
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def hexdump(data, max_bytes=256):
    if not data:
        return "(empty)"
    s = hexlify(data[:max_bytes]).decode("ascii")
    if len(data) > max_bytes:
        s += f"... ({len(data)}b total)"
    return s


# --- Per-instance card helpers (game_cards) --------------------------------
# Reusable reads/writes for a card instance's ability/state/stat data. These
# back the handler-level wrappers so ability.py and the server share one source
# of truth for the game_cards row layout.


def db_backfill_ability_effect_meta(db=None):
    """Restore the gamedata effect structure onto ability_effects rows:
    effect_group_id / condition_id / target_index from each ability's
    m_AbilityEffectList (card_abilities_meta.raw_json).  The client's engine
    walks effects by group and gates them on conditions; without these columns
    the flat BOM walk cannot reproduce it."""
    import json as _j
    import re as _re
    con = db or _db
    # Ensure the mapping columns exist (fresh DBs get them from static.DDL;
    # older DBs need ALTER TABLE before the backfill can write them).
    try:
        ecols = {r[1] for r in con.execute("PRAGMA table_info(ability_effects)")}
        for col, ddl in (
                ("effect_instance_id", "ALTER TABLE ability_effects ADD COLUMN effect_instance_id INTEGER DEFAULT -1"),
                ("contingent_effect_instance_id", "ALTER TABLE ability_effects ADD COLUMN contingent_effect_instance_id INTEGER DEFAULT -1"),
                ("secondary_target_index", "ALTER TABLE ability_effects ADD COLUMN secondary_target_index INTEGER DEFAULT -1"),
                ("recalculate_targets", "ALTER TABLE ability_effects ADD COLUMN recalculate_targets INTEGER DEFAULT -1"),
                ("is_optional", "ALTER TABLE ability_effects ADD COLUMN is_optional INTEGER DEFAULT 0"),
                ("effect_duration", "ALTER TABLE ability_effects ADD COLUMN effect_duration TEXT DEFAULT 'Instant'"),
                ("output_variables", "ALTER TABLE ability_effects ADD COLUMN output_variables TEXT DEFAULT '{}'"),
        ):
            if col not in ecols:
                con.execute(ddl)
        con.commit()
    except Exception:
        pass
    _recalc_map = {"True": 1, "False": 0, "UseDefault": -1, None: -1}
    rows = con.execute(
        "SELECT ability_guid, raw_json FROM card_abilities_meta "
        "WHERE raw_json IS NOT NULL AND raw_json != ''").fetchall()
    updated = 0
    for ag, raw in rows:
        try:
            rec = _j.loads(raw)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        lst = rec.get("m_AbilityEffectList") or []
        for order, entry in enumerate(lst):
            eg = (entry.get("m_EffectTemplateId") or {}).get("m_Guid", "")
            if not eg:
                continue
            cid = (entry.get("m_ConditionId") or {}).get("m_Guid", "") or ""
            if cid.lower() == "00000000-0000-0000-0000-000000000000":
                cid = ""
            # m_TargetTemplateIndex is a real index: 0 must survive (the old
            # `int(x or -1)` collapsed index 0 to -1, breaking every first
            # target).  Same care for the other index fields.
            def _idx(v):
                if v is None:
                    return -1
                try:
                    return int(v)
                except (ValueError, TypeError):
                    return -1
            recalc = entry.get("m_RecalculateTargets")
            outvars = entry.get("m_OutputVariables") or {}
            if not isinstance(outvars, dict):
                outvars = {}
            con.execute(
                "UPDATE ability_effects SET effect_group_id=?, condition_id=?, "
                "target_index=?, effect_instance_id=?, "
                "contingent_effect_instance_id=?, secondary_target_index=?, "
                "recalculate_targets=?, is_optional=?, effect_duration=?, "
                "output_variables=? WHERE ability_guid=? AND effect_guid=? "
                "AND effect_order=?",
                (_idx(entry.get("m_EffectGroupId")),
                 cid.lower() if cid else "",
                 _idx(entry.get("m_TargetTemplateIndex")),
                 _idx(entry.get("m_EffectInstanceId")),
                 _idx(entry.get("m_ContingentEffectInstanceId")),
                 _idx(entry.get("m_SecondaryTargetIndex")),
                 _recalc_map.get(str(recalc), -1),
                 _idx(entry.get("m_IsOptional")),
                 str(entry.get("m_EffectDuration") or "Instant"),
                 _j.dumps(outvars),
                 ag.lower(), eg.lower(), order))
            updated += 1

    con.commit()
    return updated


def db_ensure_resource_grants(db=None):
    """Populate card_templates.current_resources_granted /
    max_resources_granted from the gamedata template fields
    (m_CurrentResourcesGranted / m_MaxResourcesGranted).  Basic shards grant
    1/1; Shards of Fate grants 0/1 — it increases MAX mana only.  The fields
    are not part of the CARD_TEMPLATES seed tuple, so the live DB backfills
    them here; resources missing from Records default to 1/1 like a shard."""
    import json as _json
    import os as _os
    con = db or _db
    try:
        ecols = {r[1] for r in con.execute("PRAGMA table_info(card_templates)")}
        if "current_resources_granted" not in ecols:
            con.execute("ALTER TABLE card_templates "
                        "ADD COLUMN current_resources_granted INTEGER DEFAULT 0")
        if "max_resources_granted" not in ecols:
            con.execute("ALTER TABLE card_templates "
                        "ADD COLUMN max_resources_granted INTEGER DEFAULT 0")
        con.commit()
    except Exception:
        pass
    try:
        _rec_path = _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)),
            "hex-private", "Records", "CardTemplate.jsonl")
        if _os.path.exists(_rec_path):
            _grants = {}
            with open(_rec_path) as _fh:
                for _line in _fh:
                    _line = _line.rstrip("\n")
                    if not _line or _line.startswith('"$'):
                        continue
                    try:
                        _inner = _json.loads(_line)
                    except Exception:
                        continue
                    if not isinstance(_inner, str):
                        continue
                    try:
                        _rec = _json.loads(_inner)
                    except Exception:
                        continue
                    if not isinstance(_rec, dict):
                        continue
                    _g = ((_rec.get("m_Id") or {}).get("m_Guid") or "").lower()
                    if not _g:
                        continue
                    _grants[_g] = (
                        int(_rec.get("m_CurrentResourcesGranted") or 0),
                        int(_rec.get("m_MaxResourcesGranted") or 0))
            for _g, (_cur, _max) in _grants.items():
                con.execute(
                    "UPDATE card_templates SET current_resources_granted=?, "
                    "max_resources_granted=? WHERE guid=?",
                    (_cur, _max, _g))
            con.commit()
    except Exception:
        pass
    # Insurance for resource templates missing from Records: behave like a
    # basic shard (+1/+1).
    con.execute(
        "UPDATE card_templates SET current_resources_granted=1, "
        "max_resources_granted=1 WHERE card_type='Resource' "
        "AND current_resources_granted=0 AND max_resources_granted=0")
    con.commit()


# Diagnostics-only UID -> name annotation.  Template names are immutable so
# they cache by GUID; the instance -> template mapping is read per call so a
# transformed or replaced card never logs a stale name.  The cache is guarded
# because log lines are emitted from several session threads, and it stays
# bounded by the template table (~7k rows).
_template_label_cache: dict = {}
_template_label_lock = threading.Lock()


def db_card_template_label(template_guid):
    """``(name, attack, defense)`` for one card template (cached by GUID)."""
    if not template_guid:
        return "", 0, 0
    with _template_label_lock:
        cached = _template_label_cache.get(str(template_guid))
        if cached is not None:
            return cached
    row = _db.execute(
        "SELECT name, attack, defense FROM card_templates WHERE guid=?",
        (str(template_guid),)).fetchone()
    label = ((row[0] if row and row[0] else ""),
             int(row[1] or 0) if row else 0,
             int(row[2] or 0) if row else 0)
    with _template_label_lock:
        _template_label_cache.setdefault(str(template_guid), label)
    return label


def card_label(session_id, card_uid, *, stats=False):
    """``"0x301 Abominate (0/0)"`` for diagnostics; ``"0x301"`` when unknown."""
    try:
        uid = int(card_uid)
    except (TypeError, ValueError):
        return str(card_uid)
    try:
        row = _db.execute(
            "SELECT template_guid FROM game_cards "
            "WHERE session_id=? AND card_uid=?",
            (int(session_id), uid)).fetchone()
    except (TypeError, ValueError):
        row = None
    name, attack, defense = db_card_template_label(row[0] if row else None)
    text = hex(uid)
    if name:
        text += f" {name}"
        if stats:
            text += f" ({attack}/{defense})"
    return text


def log_req(msg: str, level: str | int = "INFO") -> bool:
    """Print and persist a message when *level* meets the threshold."""
    if not _log_enabled(level):
        return False
    with log_lock:
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        print(line.rstrip("\n"), flush=True)
        _log_req_file.write(line)
        _write_bound_logs_locked(line)
    return True


def log(msg: str, level: str | int = "INFO") -> bool:
    """Print a message when *level* meets the process-wide threshold."""
    if not _log_enabled(level):
        return False
    with log_lock:
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        print(line.rstrip("\n"), flush=True)
        _write_bound_logs_locked(line)
    return True


# --- Idle-transaction watchdog -------------------------------------------
# SQLite allows one writer at a time.  A connection that starts a write
# transaction and never commits/rolls it back pins the database's write lock
# until some later commit on that same connection, blocking every other
# process ("database is locked").  The shared connection runs in autocommit
# mode so a forgotten commit cannot do this; this watchdog is a belt-and-
# braces guard for any explicit BEGIN that is left open.
_idle_tx_guard = threading.Lock()
_idle_tx_seen = {}
_LOCK_WATCHDOG_INTERVAL = 10.0
_LOCK_WATCHDOG_MAX_IDLE = 30.0
_lock_watchdog_started = False


def rollback_idle_transaction(conn=None, *, max_idle_seconds=_LOCK_WATCHDOG_MAX_IDLE):
    """Roll back a connection left inside a transaction for too long."""
    conn = conn if conn is not None else _db
    key = id(conn)
    now = time.monotonic()
    with _idle_tx_guard:
        if getattr(conn, "in_transaction", False):
            first = _idle_tx_seen.setdefault(key, now)
            idle = now - first
        else:
            _idle_tx_seen.pop(key, None)
            idle = 0.0
    if idle < max_idle_seconds:
        return False
    try:
        conn.rollback()
    except sqlite3.Error as exc:
        log(f"db: idle-transaction rollback failed: {exc}")
        return False
    with _idle_tx_guard:
        _idle_tx_seen.pop(key, None)
    log(f"db: rolled back a transaction left open {idle:.0f}s "
        f"on {getattr(conn, 'database', '?')}")
    return True


def _lock_watchdog_loop():
    while True:
        try:
            rollback_idle_transaction(_db)
        except Exception as exc:  # never let the watchdog die
            log(f"db: lock watchdog error: {exc}")
        time.sleep(_LOCK_WATCHDOG_INTERVAL)


def start_lock_watchdog():
    """Start the process-wide idle-transaction watchdog (idempotent)."""
    global _lock_watchdog_started
    if _lock_watchdog_started:
        return
    _lock_watchdog_started = True
    threading.Thread(target=_lock_watchdog_loop, daemon=True,
                     name="db-lock-watchdog").start()


# === SQLite connection ===

# The HConnect process and the tournament scheduler are separate processes
# sharing this WAL database.  A short write collision is normal when a game
# session is persisted while the scheduler refills its room pool; five seconds
# gives the writer a brief chance to finish without holding the request open.
_db = connect(DB_PATH, check_same_thread=False)
_db.execute("PRAGMA busy_timeout=5000")
_db.execute("PRAGMA journal_mode=WAL")
_db.execute("PRAGMA foreign_keys=ON")

# Ensure the static schema (incl. game_cards, session_events) exists and apply
# any column migrations for databases created before columns were added.
#
# The test runner supplies a fully initialized, immutable SQLite snapshot and
# asks each subprocess to restore it into its own in-memory connection.  This
# keeps production startup unchanged while avoiding a full seed/migration pass
# for every test process.
import static
_test_db_template = os.environ.get("HEX_TEST_DB_TEMPLATE")
if (os.environ.get("HEX_TEST_DB_READY") == "1"
        and DB_PATH == ":memory:"
        and _test_db_template):
    _template_db = sqlite3.connect(_test_db_template)
    try:
        _template_db.backup(_db)
    finally:
        _template_db.close()
elif os.environ.get("HEX_DB_BOOTSTRAPPED") == "1":
    # restart.sh and the Docker entrypoint run the full schema/seed bootstrap
    # once before starting services. All service processes inherit this marker;
    # they open the prepared database without repeating startup seed work.
    pass
else:
    static.ensure_schema(_db)


# === Identity helpers ===

def player_id_from_name(name):
    """Derive a stable numeric player ID from the full identity string.

    The identity is "Display#Discriminator" (e.g. "Neverness#1234"). The
    player ID is a 63-bit hash of that FULL string so different players with
    the same display name still get distinct IDs, and the same player always
    maps to the same ID across sessions.
    """
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()
    return int(digest[:16], 16) % (2 ** 63)


def player_id_from_steam(steam_id):
    """Derive a player ID from a Steam account ID (steamId).

    SteamID64 values (17-digit, ~7.6e16 max) fit comfortably in a 63-bit
    signed int, so we use the raw Steam ID as the player ID.  This lets the
    auth proxy's Steam ID become the authoritative account key even though
    the in-game display name stays the fixed "TestPlayer".
    """
    try:
        return int(steam_id) % (2 ** 63)
    except (TypeError, ValueError):
        return player_id_from_name(str(steam_id))


def display_name_from_identity(name):
    """Strip the hidden '#discriminator' suffix for display (e.g. 'Neverness')."""
    if name and "#" in name:
        return name.rsplit("#", 1)[0]
    return name or ""


# === Users / profile ===


# Stardust template GUIDs
STARDUST_TEMPLATES = {
    "common": "ab4a63a8-c378-4693-8b5a-97e423d3d47b",
    "uncommon": "3f2af3a4-8780-4095-ae81-d32b618f0595",
    "rare": "a2a6129b-978a-40ce-9673-73588e6a40c3",
    "legendary": "259a55a1-9e42-4a41-b8a1-ba33d32e62ed",
    "promo": "4844eb99-457a-457f-a246-449954ff305a",
}
CHEST_TEMPLATE = "a9ae9af2-e27a-48e0-9cd2-490d252fffe4"


# === Inventory ===


# === Arena (Frost Ring Arena) ===


# === Redeem codes / store ===


def db_get_store_item(item_id, conn=None):
    """Return one store item row by its numeric client ID."""
    connection = conn or _db
    return connection.execute(
        "SELECT name, price, currency, template_guid, store_tab "
        "FROM store_items WHERE id=?", (int(item_id),)).fetchone()


# === Emails ===


# === Decks ===


# === Sessions (reconnect) ===


# === Chat ===

CHAT_HISTORY_HOURS = 24

def db_store_chat(user_id, sender, room, message, icon="", flags=""):
    _db.execute("INSERT INTO chat_messages (user_id, sender, room, message, icon, flags) VALUES (?,?,?,?,?,?)",
                (user_id, sender, room, message, icon, flags))
    _db.commit()
    # Keep only last 500 messages per room
    _db.execute("DELETE FROM chat_messages WHERE room=? AND id NOT IN (SELECT id FROM chat_messages WHERE room=? ORDER BY id DESC LIMIT 500)",
                (room, room))
    _db.commit()


def db_get_recent_chat(room, limit=30):
    rows = _db.execute(
        "SELECT sender, message, icon, flags, created_at "
        "FROM chat_messages "
        "WHERE room=? AND created_at >= datetime('now', ?) "
        "ORDER BY id DESC LIMIT ?",
        (room, f"-{CHAT_HISTORY_HOURS} hours", limit)).fetchall()
    return [{"user": r[0], "msg": r[1], "icon": r[2], "flags": r[3], "time": r[4]} for r in reversed(rows)]


# === Inbound transaction capture -------------------------------------------

def _session_capture_connection():
    """Return a short-lived connection for optional PvP transaction capture.

    Transaction capture is diagnostic data, not authoritative game state.  It
    must not share the legacy module connection: handler threads can otherwise
    interleave a cursor's ``fetch*`` calls, and a failed audit INSERT can leave
    the gameplay connection contending for the single SQLite writer slot.
    A busy database is expected while a game is being saved, so this connection
    deliberately gives up quickly and lets the caller skip the audit record.
    """
    if (DB_PATH == ":memory:" or
            not isinstance(_db, RetryingConnection)):
        # Isolated in-memory connections do not see the test schema/state.
        return _db, False
    capture_db = sqlite3.connect(DB_PATH, timeout=0.25,
                                 check_same_thread=False)
    capture_db.execute("PRAGMA busy_timeout=250")
    return capture_db, True


# === Replay event log ===

# Only real player-versus-player sessions are candidates for the public replay
# browser.  Practice uses ``Session-*`` and Campaign uses ``camp_*``; neither
# should retain its event stream.  ``Challenge_*`` covers direct PvP invites,
# while ``pvp-`` is reserved for other direct/matchmaking PvP sessions.
_REPLAYABLE_PVP_SESSION_PREFIXES = ("tourney-", "pvp-", "Challenge_")


# --- Tournaments ---------------------------------------------------------------

_TOURNEY_LIST_COLS = (
    "t.id", "t.type_id", "t.status", "t.players_json", "t.session_id",
    "t.created_at", "tt.name AS type_name", "tt.style", "tt.format",
    "tt.min_players", "tt.max_players", "tt.games_count", "tt.set_id")


def db_tournament_close_orphaned_started():
    """Close started tournaments whose persisted game session is gone.

    ``started`` is an active match state, while ``waiting`` is deliberately
    kept open by the tournament pool scheduler.  A started room with no
    corresponding game session cannot be resumed and otherwise remains
    permanently open in the tournament database, so mark only those rows
    closed.  Live sessions, including disconnected games that can be
    rejoined, are left untouched.
    """
    try:
        cursor = _db.execute(
            "UPDATE tournaments SET status='closed' "
            "WHERE status='started' "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM tournament_types tt "
            "  WHERE tt.id=tournaments.type_id "
            "  AND LOWER(COALESCE(tt.style, '')) IN ('async', 'asynchronous')"
            ") "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM game_sessions gs "
            "  WHERE CAST(gs.session_id AS TEXT)=CAST(tournaments.session_id AS TEXT)"
            ")"
        )
        _db.commit()
    except BaseException:
        _db.rollback()
        raise
    return int(cursor.rowcount or 0)


def db_tournament_cleanup_old(age_days=1):
    """Close stale tournaments and remove their database-owned game state.

    Tournament rows are retained as closed history.  Only sessions referenced
    by tournaments past the age threshold are removed, so old campaign/PvP
    sessions outside the tournament system are not affected.  ``created_at``
    is stored as SQLite's UTC datetime text by the schema.
    """
    try:
        days = max(1, int(age_days))
    except (TypeError, ValueError):
        days = 1
    cutoff = f"-{days} days"

    # Cleanup runs in the tournament scheduler while HConnect may be creating
    # a battle and inserting its deck.  Do not use the process-wide legacy
    # connection here: if a competing writer wins between two statements, a
    # failed UPDATE can leave that shared connection inside an uncommitted
    # transaction and block the battle for the remainder of the request.
    # Instead, take one short-lived write transaction.  A busy database is a
    # normal scheduler condition; leave this pass for the next interval.
    owns_cleanup_db = True
    # Keep the in-memory test database usable when callers temporarily replace
    # the legacy connection.  The production connection is a
    # RetryingConnection, so it always uses the isolated connection below and
    # never touches a handler's active transaction from the scheduler thread.
    if isinstance(_db, sqlite3.Connection) and not isinstance(
            _db, RetryingConnection):
        cleanup_db = _db
        owns_cleanup_db = False
    else:
        cleanup_db = sqlite3.connect(DB_PATH, timeout=0.5)
    try:
        cleanup_db.execute("PRAGMA busy_timeout=500")
        cleanup_db.execute("BEGIN IMMEDIATE")
        has_tournament_types = bool(cleanup_db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='tournament_types'").fetchone())
        persistent_event_clause = ""
        if has_tournament_types:
            persistent_event_clause = (
                " AND NOT EXISTS (SELECT 1 FROM tournament_types tt "
                "WHERE tt.id=tournaments.type_id AND "
                "LOWER(COALESCE(tt.style, '')) IN "
                "('async', 'asynchronous'))")
        # Keep an ended session's event source until the replay worker has
        # indexed it. Abandoned/non-ended sessions can be removed immediately;
        # completed sessions require a ready replay (or no events at all).
        stale_sessions = [
            str(row[0]) for row in cleanup_db.execute(
                "SELECT DISTINCT t.session_id FROM tournaments t "
                "JOIN game_sessions gs ON CAST(gs.session_id AS TEXT) "
                "= CAST(t.session_id AS TEXT) "
                "WHERE t.session_id IS NOT NULL AND TRIM(t.session_id)<>'' "
                "AND t.created_at IS NOT NULL "
                "AND datetime(t.created_at) <= datetime('now', ?) "
                "AND (gs.state <> 'ended' OR NOT EXISTS ("
                "  SELECT 1 FROM session_events se "
                "  WHERE CAST(se.session_id AS TEXT)=CAST(gs.session_id AS TEXT)"
                ") OR EXISTS ("
                "  SELECT 1 FROM game_replays gr "
                "  WHERE CAST(gr.session_id AS TEXT)=CAST(gs.session_id AS TEXT) "
                "  AND gr.status='ready'"
                "))",
                (cutoff,)).fetchall()
            if row and row[0] is not None
        ]

        closed_cursor = cleanup_db.execute(
            "UPDATE tournaments SET status='closed' "
            "WHERE status<>'closed' AND created_at IS NOT NULL "
            "AND datetime(created_at) <= datetime('now', ?)" +
            persistent_event_clause,
            (cutoff,))

        # Tournament-owned pools and match history are run state, not the
        # permanent tournament descriptor.  Retain the descriptor for lobby
        # history, but release all per-run data once the event has expired.
        expired_tournaments = [
            row[0] for row in cleanup_db.execute(
                "SELECT id FROM tournaments WHERE created_at IS NOT NULL "
                "AND datetime(created_at) <= datetime('now', ?)" +
                persistent_event_clause,
                (cutoff,)).fetchall()
        ]
        if expired_tournaments:
            placeholders = ",".join("?" for _ in expired_tournaments)
            pool_exists = cleanup_db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='tournament_pool'").fetchone()
            if pool_exists:
                cleanup_db.execute(
                    "DELETE FROM tournament_pool WHERE tournament_id IN (" +
                    placeholders + ")", expired_tournaments)
            matches_exists = cleanup_db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='tournament_matches'").fetchone()
            if matches_exists:
                cleanup_db.execute(
                    "DELETE FROM tournament_matches WHERE tournament_id IN (" +
                    placeholders + ")", expired_tournaments)

        cards_removed = 0
        sessions_removed = 0
        for session_id in stale_sessions:
            card_cursor = cleanup_db.execute(
                "DELETE FROM game_cards WHERE CAST(session_id AS TEXT)=?",
                (session_id,))
            cards_removed += int(card_cursor.rowcount or 0)
            session_cursor = cleanup_db.execute(
                "DELETE FROM game_sessions "
                "WHERE CAST(session_id AS TEXT)=?",
                (session_id,))
            sessions_removed += int(session_cursor.rowcount or 0)

        cleanup_db.commit()
        return {
            "tournaments_closed": int(closed_cursor.rowcount or 0),
            "game_sessions_removed": sessions_removed,
            "game_cards_removed": cards_removed,
        }
    except sqlite3.OperationalError as exc:
        cleanup_db.rollback()
        if _is_sqlite_lock_error(exc):
            return {
                "tournaments_closed": 0,
                "game_sessions_removed": 0,
                "game_cards_removed": 0,
            }
        raise
    finally:
        if owns_cleanup_db:
            cleanup_db.close()


_SIGNUP_KEYS = ("id", "tournament_id", "player_uid", "player_name", "deck_id",
                "entry_group", "fee_paid", "status", "created_at")


_TOURNAMENT_MATCH_KEYS = (
    "id", "tournament_id", "round_id", "match_id", "player1_uid",
    "player2_uid", "session_id", "state", "status", "start_time",
    "end_time", "game1_winner", "game2_winner", "game3_winner",
)


# ---------------------------------------------------------------------------
# Game cards helpers — reusable self-contained queries.
# ---------------------------------------------------------------------------


# --- Friend system helpers -------------------------------------------------


# === Battle query helpers (read-only) ========================================


def db_talent_ability_costs(ability_guid):
    """Return (charge_cost, spell_cost, activatable_phases, casting_behavior) or None."""
    row = _db.execute(
        "SELECT charge_cost, spell_cost, activatable_phases, casting_behavior "
        "FROM talent_abilities WHERE ability_guid=? LIMIT 1",
        (str(ability_guid),)).fetchone()
    return row


def db_game_card_effective_cost(session_id, card_uid, battle_state=None):
    """Return the current payable cost for one card instance.

    The RulesPort facts adapter must validate a card with the same cost that
    the legacy play/payment path uses.  A template's printed cost is only the
    starting point: ``game_cards.card_cost_mod`` and dynamic/static modifiers
    (for example Infernal Professor's reduction) are part of the effective
    cost.  Keep this calculation behind the shared DB facade so the ported
    rules kernel remains SQL-free and both paths use one authority.

    ``battle_state`` is optional for callers that only need persisted instance
    modifiers; the canonical static evaluator accepts an empty state in that
    case.  Older focused fixtures may not contain all modifier columns, so a
    conservative template-plus-instance fallback is retained.
    """
    try:
        from rules_port.static_rules import effective_cost
        return max(0, int(effective_cost(
            _db, session_id, battle_state or {}, int(card_uid))))
    except Exception:
        try:
            row = _db.execute(
                "SELECT ct.cost, gc.card_cost_mod "
                "FROM game_cards gc JOIN card_templates ct "
                "ON ct.guid=gc.template_guid "
                "WHERE gc.session_id=? AND gc.card_uid=?",
                (session_id, int(card_uid))).fetchone()
        except sqlite3.OperationalError:
            # Keep older/minimal test schemas usable when the per-instance
            # modifier columns have not been migrated yet.
            row = _db.execute(
                "SELECT ct.cost FROM game_cards gc "
                "JOIN card_templates ct ON ct.guid=gc.template_guid "
                "WHERE gc.session_id=? AND gc.card_uid=?",
                (session_id, int(card_uid))).fetchone()
        if not row:
            return 0
        cost = int(row[0] or 0)
        if len(row) > 1:
            cost += int(row[1] or 0)
        return max(0, cost)


# === Battle mutation helpers (read/write) ====================================


def db_discard_card(session_id, card_uid, owner_user_id=None,
                    extra_set=None, extra_params=None, connection=None):
    """Move one card to its owner's discard pile at the next position.

    ``position`` is an append order within one session/player discard pile.
    The optional ``extra_set``/``extra_params`` are for discard paths that
    also clear combat state or reset damage.  The SQL fragments are supplied
    only by trusted server code, never by a client request.

    Return the user id that owns the resulting discard pile, or ``None`` if
    the card was not found.
    """
    connection = connection or _db
    row = connection.execute(
        "SELECT user_id FROM game_cards "
        "WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).fetchone()
    if not row:
        return None
    owner = int(row[0] if owner_user_id is None else owner_user_id)
    position = connection.execute(
        "SELECT COALESCE(MAX(position) + 1, 1) "
        "FROM game_cards "
        "WHERE session_id=? AND user_id=? AND location='discard'",
        (session_id, owner)).fetchone()[0]

    sql = "UPDATE game_cards SET user_id=?, location='discard', position=?"
    params = [owner, int(position or 1)]
    if extra_set:
        sql += ", " + extra_set
        params.extend(extra_params or [])
    sql += " WHERE session_id=? AND card_uid=?"
    params.extend([session_id, int(card_uid)])
    connection.execute(sql, params)
    connection.commit()
    return owner


# === Service query helpers ===================================================


# === Game sessions ==========================================================


# === Replay persistence =====================================================


# Card instance resolve (for player collection cards): template_guid + card_type.


# Ability-grant lookup: for a troop ability activation, find the source card
# whose card_abilities JSON contains the given ability_guid.


log(f"DB initialized at {DB_PATH}")
