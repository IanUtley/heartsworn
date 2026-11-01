"""Characterization tests for ``ai._ai_csharp_manual_ability_decision``.

The generic C# decision fallback only acts on abilities whose target
templates are known.  These pin the no-op outcomes so the case table can be
restructured without changing what the AI chooses."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database

SRC = fresh_database()

import ai as ai_mod
import db as dbmod
from tests.tests_combat import HandlerStub, SessionStub

UNKNOWN_ABILITY = "00000000-0000-0000-0000-00000000dead"


def _run(effect_params, champion=False):
    # The seeded static database carries the champion/ability tables the
    # decision reads; the unknown GUID keeps the answer empty.
    db = dbmod._db
    handler = HandlerStub(db)
    session = SessionStub()
    return ai_mod._ai_csharp_manual_ability_decision(
        handler, session, {}, UNKNOWN_ABILITY, 101, effect_params,
        ai_owner_id=0, opponent_owner_id=1, champion=champion)


def test_unknown_ability_has_no_useful_activation():
    assert _run([]) == (False, None)


def test_unknown_champion_ability_has_no_useful_activation():
    assert _run([], champion=True) == (False, None)


def test_move_effect_without_targets_is_not_useful():
    move = {"destination": "hand", "ability_owner_takes_control": 1}
    assert _run([("MoveCardToZoneEffectTemplate", move)]) == (False, None)


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print("AI manual decision tests passed")
