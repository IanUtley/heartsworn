"""A WhileCardInPlay stat is projected by the native static path, so the interpreter accepts it on a trigger-less
ability and does not apply it again when it runs that ability."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.tests_builtin_counter_rules import TPL_GLADIATOR, SessionStub, _setup  # noqa: E402
from tests.tests_combat import add_card, make_db  # noqa: E402

COMMAND_TOWER = "b3c53fd1-6acf-3fce-78e2-ac332551b211"  # Troops you control have +1[ATK]/+0[DEF]. (static)
TOWER_UID = 900
TROOP_UID = 950


def _battle(db, tower_in_play=True):
    pl_t, ai_t, game, handler, bstate, _pc, _ac = _setup(db)
    add_card(db, TOWER_UID, 5, TPL_GLADIATOR, loc="warzone" if tower_in_play else "deck")
    db.execute("UPDATE game_cards SET card_abilities=? WHERE card_uid=?", (json.dumps([COMMAND_TOWER]), TOWER_UID))
    add_card(db, TROOP_UID, 5, TPL_GLADIATOR, loc="warzone")
    db.commit()
    session = db.execute("SELECT session_id FROM game_cards WHERE card_uid=?", (TROOP_UID,)).fetchone()[0]
    return pl_t, ai_t, game, handler, bstate, session


def test_command_tower_is_covered_by_the_interpreter():
    from rules_port import ir_exec

    assert ir_exec.coverage(COMMAND_TOWER) == []


def test_static_stat_follows_the_source_while_it_is_in_play():
    from rules_port.static_rules import effective_stats

    db = make_db()
    _pl_t, _ai_t, _game, _handler, bstate, session = _battle(db)
    assert effective_stats(db, session, bstate, TROOP_UID)[0] == 3  # the troop's 2 attack, plus the tower's +1
    db.execute("UPDATE game_cards SET location='deck' WHERE card_uid=?", (TOWER_UID,))
    db.commit()
    assert effective_stats(db, session, bstate, TROOP_UID)[0] == 2


def test_running_the_static_ability_does_not_apply_its_stat_twice():
    from rules_port.context import EffectContext
    from rules_port.static_rules import effective_stats
    from rules_port import ir_exec

    db = make_db()
    pl_t, ai_t, game, handler, bstate, session = _battle(db)
    before = effective_stats(db, session, bstate, TROOP_UID)[0]
    context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
    ir_exec.run_ability(COMMAND_TOWER, None, context=context, source_uid=TOWER_UID, owner_uid=5, event={"player": 5})
    assert effective_stats(db, session, bstate, TROOP_UID)[0] == before
    assert db.execute("SELECT permanent_buffs FROM game_cards WHERE card_uid=?", (TROOP_UID,)).fetchone()[0] == "{}"


if __name__ == "__main__":
    # Written in pytest style: run through pytest so each test executes and failures
    # set the exit status. A missing pytest fails loudly.
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
