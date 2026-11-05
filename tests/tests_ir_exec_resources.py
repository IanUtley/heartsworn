"""The interpreter runs the resource, threshold, charge and life abilities of the shards and their kin, and reaches
the same pools the live leaves reach."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.tests_builtin_counter_rules import SessionStub, _setup  # noqa: E402
from tests.tests_combat import make_db  # noqa: E402  (the fixture reads make_db from this module)

# A basic shard's threshold ability (Blood) and charge ability (one charge point).
BLOOD_THRESHOLD_ABILITY_GUID = "f8c43282-6df8-3b71-341b-6ce279cd09c9"
CHARGE_ABILITY_GUID = "43461713-afe7-299f-9aba-40002e9105eb"
# Crimson Clarity's resource pair, and Song of the Sun's replenish.
CRIMSON_CLARITY_ABILITY_GUID = "7d564c37-3172-7d9f-62f8-b35e22084558"
SONG_OF_THE_SUN_ABILITY_GUID = "58bfb046-e469-c2a1-eb9e-9493acd54f60"
BLOOD = 4


def _context(db, bstate, pl_t, ai_t, game, handler):
    from rules_port.context import EffectContext
    return EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")


def test_threshold_ability_adds_its_shard_to_the_player(db):
    from rules_port.ir_exec import coverage, run_ability

    assert coverage(BLOOD_THRESHOLD_ABILITY_GUID) == []
    pl_t, ai_t, game, handler, bstate, player_champ, _ai = _setup(db)
    bstate["player_threshold"] = {BLOOD: 1}
    context = _context(db, bstate, pl_t, ai_t, game, handler)
    assert run_ability(BLOOD_THRESHOLD_ABILITY_GUID, None, context=context,
                       source_uid=player_champ, owner_uid=5)
    assert int(bstate["player_threshold"][BLOOD]) == 2


def test_charge_ability_gains_one_charge_point_on_the_player_pool(db):
    from rules_port.ir_exec import coverage, run_ability

    assert coverage(CHARGE_ABILITY_GUID) == []
    pl_t, ai_t, game, handler, bstate, player_champ, _ai = _setup(db)
    bstate["player_charges"] = 0
    context = _context(db, bstate, pl_t, ai_t, game, handler)
    assert run_ability(CHARGE_ABILITY_GUID, None, context=context,
                       source_uid=player_champ, owner_uid=5)
    assert int(bstate["player_charges"]) == 1


def test_replenish_refills_the_current_pool_to_its_total(db):
    from rules_port.ir_exec import coverage, run_ability

    assert coverage(SONG_OF_THE_SUN_ABILITY_GUID) == []
    pl_t, ai_t, game, handler, bstate, player_champ, _ai = _setup(db)
    bstate["player_resources"] = 1
    bstate["player_total_resources"] = 4
    context = _context(db, bstate, pl_t, ai_t, game, handler)
    assert run_ability(SONG_OF_THE_SUN_ABILITY_GUID, None, context=context,
                       source_uid=player_champ, owner_uid=5)
    assert int(bstate["player_resources"]) == 4


def test_card_cost_with_a_lasting_duration_is_refused_not_run(db):
    """A cost change that lasts while its card is in play belongs to the static path (area H), so the interpreter
    refuses the ability rather than running it."""
    from rules_port.ir_exec import coverage

    assert coverage("319ca38e-1fad-bbaa-e5a7-d11239f3a34f") == []
    assert coverage("4023fec8-f9ff-7f40-70f0-5f672348699e") == ["cost duration WhileCardInPlay"]


if __name__ == "__main__":
    # Written in pytest style: run through pytest so each test executes and failures
    # set the exit status. A missing pytest fails loudly.
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
