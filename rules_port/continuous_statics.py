"""Continuous stat clauses for the interpreter.

A stat modifier whose clause lasts WhileCardInPlay is a continuous effect. The native static path
(``rules_port.static_rules``) projects it from its source card while that card is in play, and removes it when the
card leaves. The interpreter therefore does not apply it. It accepts the clause only on an ability with no trigger,
which the chain never resolves, so the static path is the only thing that applies it. A triggered ability with such
a clause stays refused.
"""

from __future__ import annotations

# The clause durations the native static path owns.
STATIC_OWNED_DURATIONS = frozenset({"WhileCardInPlay"})


def is_static_owned(duration: str) -> bool:
    """Whether a clause's duration is projected by the native static path rather than run by the interpreter."""
    return duration in STATIC_OWNED_DURATIONS


def covered_as_static(duration: str, trigger: str) -> bool:
    """Whether the interpreter accepts a clause with this duration: only a static-owned one on an ability that has
    no trigger."""
    return is_static_owned(duration) and not trigger
