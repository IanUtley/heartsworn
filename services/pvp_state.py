"""PvP session state: the per-session mutation lock, the shared RulesPort, and the
load/save path for the battle checkpoint.

Both players' connections mutate one game, so every read-modify-write of its state goes
through the session lock and the shared port defined here.
"""

import threading

from rules_port.persistence import load_pvp_state, save_pvp_state
from services.pvp_priority import flush_priority_clock


# ── per-session mutation lock ───────────────────────────────────────────────
# Each client connection runs its own thread (hconnect_server.main spawns one
# thread per socket).  In a PvP game BOTH players' threads mutate the SAME
# session's turn_order_json (load -> mutate -> save via pvp_save_state), so a
# simultaneous pass/card-play from the two clients is a read-modify-write race
# that can silently clobber a priority/phase write.  A per-session lock
# serializes all PvP state mutations for one game, while different sessions
# (practice mode, multiple 1v1s) proceed fully in parallel.  RLock so a thread
# that already holds the lock (e.g. pass -> resolve_chain) can re-enter.

_session_locks = {}
_session_locks_guard = threading.Lock()


def pvp_session_lock(session):
    """Return the per-session RLock guarding PvP state mutations."""
    sid = int(session.session_id)
    with _session_locks_guard:
        lock = _session_locks.get(sid)
        if lock is None:
            lock = threading.RLock()
            _session_locks[sid] = lock
    return lock


def pvp_discard_session_lock(session):
    """Drop the per-session lock when a game ends (frees memory)."""
    sid = int(session.session_id)
    with _session_locks_guard:
        _session_locks.pop(sid, None)


# The C# ``AuthoritativeSession`` is a single per-game object shared by both
# participants.  HConnect materializes a fresh ``GameSession`` wrapper per
# request, so storing the native port on that wrapper (``_rules_port_session``)
# gave each connection its own scheduler: the two threads restored separate
# snapshots and clobbered each other's phase/priority (the first shard/card
# play after mulligan was rejected).  Keep one shared port per game session so
# both connections drive the same scheduler, exactly like the client.
_pvp_ports = {}
_pvp_ports_guard = threading.Lock()


def pvp_shared_port(session):
    sid = int(session.session_id)
    with _pvp_ports_guard:
        return _pvp_ports.get(sid)


def set_pvp_shared_port(session, port):
    sid = int(session.session_id)
    with _pvp_ports_guard:
        if port is None:
            _pvp_ports.pop(sid, None)
        else:
            _pvp_ports[sid] = port


def pvp_discard_shared_port(session):
    set_pvp_shared_port(session, None)


def _pvp_locked(fn):
    """Decorator: run `fn(...)` holding the per-session mutation lock, so both
    players' threads serialize their PvP state read-modify-write cycles for
    THIS session (other sessions stay parallel).  Accepts the session as the
    first positional arg (pvp_mulligan_next(session, ...)) or the second
    (route_pvp_pass(handler, session, ...)) or keyword ``session``."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        session = kwargs.get("session")
        if session is None and len(args) > 1 \
                and hasattr(args[1], "session_id"):
            session = args[1]
        elif session is None and len(args) > 0 \
                and hasattr(args[0], "session_id"):
            session = args[0]
        if session is None:
            return fn(*args, **kwargs)
        with pvp_session_lock(session):
            return fn(*args, **kwargs)
    return wrapper


@_pvp_locked
def pvp_load_state(session):
    # A live game has ONE authoritative checkpoint shared by both connections.
    # HConnect builds a fresh ``GameSession`` wrapper per request, so reading
    # the wrapper's own ``_rules_port_battle_state``/``turn_order`` makes the
    # two players diverge (a shard's charge/threshold landed in one wrapper's
    # dict while the options refresh read another).  Resolve through the shared
    # port when it exists so every wrapper sees the same dict.
    port = (getattr(session, "_rules_port_session", None) or
            pvp_shared_port(session))
    if port is not None:
        state = getattr(port, "_pvp_state", None)
        if isinstance(state, dict) and state.get("pvp"):
            return state
    shared = getattr(session, "_rules_port_battle_state", None)
    if isinstance(shared, dict) and shared.get("pvp"):
        return shared
    return load_pvp_state(session)


def pvp_save_state(session, state):
    if isinstance(state, dict) and state.get("pvp"):
        # Publish to the shared port so both connections and every per-request
        # wrapper operate on this one dict.
        port = (getattr(session, "_rules_port_session", None) or
                pvp_shared_port(session))
        if port is not None:
            port._pvp_state = state
        # Keep the PvP projection and the RulesPort snapshot on one mutable
        # root.  Without this assignment, SQLiteRulesSnapshot.save() can
        # persist a stale pre-rotation root and overwrite turn_order after a
        # successful native EndTurn transition.
        shared = getattr(session, "_rules_port_battle_state", None)
        if isinstance(shared, dict) and shared is not state:
            if "rules_port" not in state and "rules_port" in shared:
                state["rules_port"] = shared["rules_port"]
            session._rules_port_battle_state = state
    # Keep the champion-card identity as session metadata when a RulesPort
    # transition supplies a reduced PvP state dictionary.  Losing champ_map
    # makes the next reconnect serialize Undefined.0 in PlayerUpdated.
    if isinstance(state, dict) and not state.get("champ_map"):
        existing = getattr(session, "turn_order", None)
        if isinstance(existing, dict) and existing.get("champ_map"):
            state["champ_map"] = dict(existing["champ_map"])
    save_pvp_state(session, state, flush_clock=flush_priority_clock)
