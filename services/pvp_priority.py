"""Priority clock: how long each player has held priority in a PvP battle.

The clock lives in the persisted PvP state (``priority_elapsed_ticks`` and the
window markers). It is pure state arithmetic: these functions read and write the
state dict and the wall clock, and nothing else.
"""

import time


def flush_priority_clock(state, now_ns=None):
    """Accumulate wall-clock time for the currently prioritised player.

    The PvP state is written after priority-changing actions, so this clock is
    deliberately independent of the mutable ``priority_pid`` value.  That
    lets us account for the interval belonging to the previous priority owner
    before starting the new owner's interval.  The watchdog also flushes it
    periodically, limiting time lost if the process stops unexpectedly.
    """
    if not isinstance(state, dict):
        return
    now_ns = int(now_ns if now_ns is not None else time.time_ns())
    try:
        current_pid = int(state.get("priority_pid") or 0)
    except (TypeError, ValueError):
        current_pid = 0
    try:
        clock_pid = int(state.get("_priority_clock_pid") or 0)
    except (TypeError, ValueError):
        clock_pid = 0
    try:
        started_ns = int(state.get("_priority_clock_started_ns") or 0)
    except (TypeError, ValueError):
        started_ns = 0

    elapsed = state.get("priority_elapsed_ticks")
    if not isinstance(elapsed, dict):
        elapsed = {}
        state["priority_elapsed_ticks"] = elapsed

    if clock_pid and started_ns:
        delta_ticks = max(0, now_ns - started_ns) // 100
        if delta_ticks:
            key = str(clock_pid)
            elapsed[key] = int(elapsed.get(key, 0) or 0) + delta_ticks

    # A priority change closes the old interval and starts the new one.  When
    # priority is unset, leave the clock stopped rather than charging either
    # player while the game is in a non-priority setup phase.
    state["_priority_clock_pid"] = current_pid
    state["_priority_clock_started_ns"] = now_ns if current_pid else 0

    # This marker is intentionally NOT refreshed on every state save.  It is
    # the client's five-minute inactivity/turn-phase window, which remains
    # active for as long as the same player retains priority.  The cumulative
    # clock above is flushed frequently; conflating the two would prevent the
    # timeout from ever firing.
    try:
        window_pid = int(state.get("_priority_window_pid") or 0)
        window_started_ns = int(
            state.get("_priority_window_started_ns") or 0)
    except (TypeError, ValueError):
        window_pid = window_started_ns = 0
    if current_pid != window_pid:
        window_pid = current_pid
        window_started_ns = now_ns if current_pid else 0
    elif current_pid and not window_started_ns:
        window_started_ns = now_ns
    state["_priority_window_pid"] = window_pid
    state["_priority_window_started_ns"] = window_started_ns


def priority_elapsed_ticks(state, pid, now_ns=None):
    """Return persisted plus currently-running priority time for ``pid``."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return 0
    elapsed = state.get("priority_elapsed_ticks") if isinstance(state, dict) else None
    try:
        total = int((elapsed or {}).get(str(pid), 0) or 0)
    except (AttributeError, TypeError, ValueError):
        total = 0
    try:
        clock_pid = int(state.get("_priority_clock_pid") or 0)
        started_ns = int(state.get("_priority_clock_started_ns") or 0)
    except (TypeError, ValueError):
        clock_pid = started_ns = 0
    if clock_pid == pid and started_ns:
        now_ns = int(now_ns if now_ns is not None else time.time_ns())
        total += max(0, now_ns - started_ns) // 100
    return total


def priority_window_elapsed_seconds(state, pid, now_ns=None):
    """Return seconds since ``pid`` most recently gained priority."""
    try:
        pid = int(pid)
        window_pid = int(state.get("_priority_window_pid") or 0)
        started_ns = int(state.get("_priority_window_started_ns") or 0)
    except (TypeError, ValueError):
        return 0
    if pid <= 0 or window_pid != pid or not started_ns:
        return 0
    now_ns = int(now_ns if now_ns is not None else time.time_ns())
    return max(0, now_ns - started_ns) // 1_000_000_000
