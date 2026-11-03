"""The PvP priority clock: time accrues to whoever holds priority, and the inactivity window
restarts when priority changes hands. Pure state arithmetic, no database."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from services.pvp_priority import (  # noqa: E402
    flush_priority_clock, priority_elapsed_ticks, priority_window_elapsed_seconds)

SECOND_NS = 1_000_000_000


def test_clock_charges_the_holder_and_restarts_on_handover():
    # Timestamps start at 1s: a zero start time means "not running" to the clock.
    state = {"priority_pid": 5}
    flush_priority_clock(state, now_ns=1 * SECOND_NS)   # player 5 gains priority
    flush_priority_clock(state, now_ns=4 * SECOND_NS)
    assert priority_elapsed_ticks(state, 5, now_ns=4 * SECOND_NS) == 30_000_000
    state["priority_pid"] = 6                           # handover closes 5's interval at 5s
    flush_priority_clock(state, now_ns=5 * SECOND_NS)
    assert priority_elapsed_ticks(state, 5, now_ns=10 * SECOND_NS) == 40_000_000
    assert priority_elapsed_ticks(state, 6, now_ns=10 * SECOND_NS) == 50_000_000


def test_inactivity_window_restarts_on_handover_but_not_on_a_flush():
    state = {"priority_pid": 5}
    flush_priority_clock(state, now_ns=1 * SECOND_NS)
    flush_priority_clock(state, now_ns=3 * SECOND_NS)   # same holder: window keeps running
    assert priority_window_elapsed_seconds(state, 5, now_ns=8 * SECOND_NS) == 7
    state["priority_pid"] = 6
    flush_priority_clock(state, now_ns=8 * SECOND_NS)   # handover restarts the window
    assert priority_window_elapsed_seconds(state, 6, now_ns=10 * SECOND_NS) == 2
    assert priority_window_elapsed_seconds(state, 5, now_ns=10 * SECOND_NS) == 0


if __name__ == "__main__":
    test_clock_charges_the_holder_and_restarts_on_handover()
    test_inactivity_window_restarts_on_handover_but_not_on_a_flush()
    print("PASS priority clock")
