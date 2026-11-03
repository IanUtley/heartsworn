"""PvP priority watchdog: ends a game whose priority holder exceeds the inactivity window.

One daemon thread per session loads the checkpoint under the session lock, flushes the
priority clock, and ends the game once the holder has kept priority past the timeout.
"""

import threading
import time

from db import log_req
from pvp_db import db_game_session_pids
from services.pvp_priority import priority_window_elapsed_seconds
from services.pvp_state import pvp_load_state, pvp_save_state, pvp_session_lock

_PVP_INACTIVITY_TIMEOUT_SECONDS = 5 * 60


_watchdog_sessions = set()
_watchdog_lock = threading.Lock()


def pvp_start_priority_watchdog(session):
    """Start the per-session priority watchdog (idempotent per session)."""
    sid = int(session.session_id)
    with _watchdog_lock:
        if sid in _watchdog_sessions:
            return
        _watchdog_sessions.add(sid)
    threading.Thread(target=_pvp_priority_watchdog_loop,
                     args=(session, sid), daemon=True).start()
    log_req(f"    PvP priority watchdog started for session {sid}")


def _pvp_priority_watchdog_loop(session, sid):
    import game_session as _gs
    try:
        while True:
            try:
                # The transaction handlers call find_session_by_player, which
                # returns a NEW GameSession object per request.  Load that
                # object only AFTER taking the per-session lock: loading it
                # before the lock lets the watchdog re-save an old snapshot
                # after a phase transition (for example, erasing a resource
                # refill from 2 back to 0).
                with pvp_session_lock(session):
                    fresh = _gs.find_session_by_id(sid)
                    if fresh is None:
                        log_req(f"    PvP priority watchdog stopped for {sid}: "
                                "session not found")
                        return
                    if getattr(fresh, "state", "") == "ended":
                        log_req(f"    PvP priority watchdog stopped for {sid}: "
                                "session ended")
                        return
                    state = pvp_load_state(fresh)
                    if not state or not state.get("pvp"):
                        log_req(f"    PvP priority watchdog stopped for {sid}: "
                                "no PvP state")
                        return
                    pid = state.get("priority_pid")
                    if not pid:
                        log_req(f"    PvP priority watchdog stopped for {sid}: "
                                "no priority owner")
                        return
                    pids = db_game_session_pids(fresh.session_id)
                    if len(pids) < 2:
                        log_req(f"    PvP priority watchdog stopped for {sid}: "
                                f"only {len(pids)} player(s)")
                        return
                    if pid not in pids:
                        log_req(f"    PvP priority watchdog stopped for {sid}: "
                                f"priority owner {pid} is not in session")
                        return
                    # Flush the active priority interval while holding the
                    # same lock used by transactions.  The latest DB snapshot
                    # is now the one being flushed, so resource/phase writes
                    # cannot be overwritten by a stale watchdog snapshot.
                    pvp_save_state(fresh, state)
                    if (priority_window_elapsed_seconds(state, pid) >=
                            _PVP_INACTIVITY_TIMEOUT_SECONDS):
                        winner_pid = (pids[1]
                                      if pids[0] == pid else pids[0])
                        log_req(
                            f"    PvP inactivity timeout: pid {pid} "
                            f"exceeded {_PVP_INACTIVITY_TIMEOUT_SECONDS}s "
                            f"of priority in session {sid}")
                        from services.tournament_game import _pvp_end_game
                        _pvp_end_game(
                            fresh, state, winner_pid, pid,
                            "priority inactivity timeout")
                        return
            except Exception as e:
                log_req(f"    PvP priority watchdog stopped for {sid}: {e}")
                return
            time.sleep(5)
    finally:
        with _watchdog_lock:
            _watchdog_sessions.discard(sid)
        log_req(f"    PvP priority watchdog stopped for session {sid}")
