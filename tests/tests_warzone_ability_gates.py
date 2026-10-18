"""Characterization tests for the entry gates of ``ai.ai_use_warzone_ability``.

The AI must not start a manual activation while a chain is on the stack, and
the optional resource sink only acts in Second Main.  Both gates answer
``None`` for decision-only callers and ``False`` otherwise."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database

SRC = fresh_database()

import ai as ai_mod
import db as dbmod
import game_engine
from tests.tests_combat import HandlerStub, SessionStub

PLAYER = SimpleNamespace(uid64=0)


def _engine(stack_empty=True, phase=game_engine.ETurnPhases.SecondMainPhase):
    return SimpleNamespace(stack_empty=lambda state: stack_empty,
                           current_phase=lambda state: phase)


def _call(engine, **kwargs):
    original = ai_mod._checkpoint_engine
    ai_mod._checkpoint_engine = lambda session, state: engine
    try:
        return ai_mod.ai_use_warzone_ability(
            HandlerStub(dbmod._db), None, SessionStub(), None, PLAYER,
            {"ai_resources": 5}, **kwargs)
    finally:
        ai_mod._checkpoint_engine = original


def test_non_empty_stack_blocks_activation():
    engine = _engine(stack_empty=False)
    assert _call(engine) is False
    assert _call(engine, decision_only=True) is None


def test_resource_sink_only_acts_in_second_main():
    engine = _engine(phase=game_engine.ETurnPhases.FirstMainPhase)
    assert _call(engine, resource_sink=True) is False
    assert _call(engine, resource_sink=True, decision_only=True) is None


def test_no_warzone_cards_means_no_activation():
    engine = _engine()
    assert _call(engine) is False
    assert _call(engine, decision_only=True) is None


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print("Warzone ability gate tests passed")
