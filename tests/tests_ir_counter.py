"""The interpreter's counter-spell effect (CounterSpellAbilityEffectTemplate) counters the card the live path counters."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pytest  # noqa: E402

import tests.ir_equivalence as H  # noqa: E402
import db as dbmod  # noqa: E402  (after the harness sets up the test database)
from tests.tests_combat import SessionStub, add_card  # noqa: E402

DISCOMBOBULATE_ABILITY = "2e4efa51-f2d3-fa11-c9ea-d37eccdfd0ca"  # "Interrupt target card"
SPELL_UID = 960
SPELL_OWNER = 0  # the AI casts the spell the player counters


@pytest.fixture(autouse=True)
def _own_database():
    """_scenario replaces the module's database and closes the one it found. Start from none, then close this test's
    own database and put the original back, so later tests keep a database that is open."""
    original = dbmod._db
    dbmod._db = None
    yield
    if dbmod._db is not None and dbmod._db is not original:
        dbmod._db.close()
    dbmod._db = original


def _scenario_with_spell_on_chain():
    scenario = H._scenario(DISCOMBOBULATE_ABILITY, [], any_template=True)
    assert scenario is not None
    db = scenario[0]
    add_card(db, SPELL_UID, SPELL_OWNER, H.scenarios.TPL_GLADIATOR, loc="CastSpells")
    db.commit()
    return scenario


def _run_interpreted(scenario):
    from rules_port.context import EffectContext
    from rules_port.ir_exec import NeedsChoice, resume_ability, run_ability

    db, pl_t, ai_t, game, handler, bstate, player_champ, ai_champ = scenario
    start = len(game.events)
    context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
    try:
        run_ability(DISCOMBOBULATE_ABILITY, None, context=context, source_uid=900, owner_uid=H.TROOP_OWNER,
                    event={"player": H.TROOP_OWNER})
    except NeedsChoice as pause:
        assert SPELL_UID in pause.candidates, pause.candidates
        resume_ability(pause.continuation, SPELL_UID, context=context)
    return H._snapshot(db, bstate, player_champ, ai_champ, game, start)


def _run_live(scenario):
    from rules_port.resolution import resolve_port_ability

    db, pl_t, ai_t, game, handler, bstate, player_champ, ai_champ = scenario
    start = len(game.events)
    resolve_port_ability(handler, game, SessionStub(), db, pl_t, ai_t, bstate, DISCOMBOBULATE_ABILITY, 900,
                         H.TROOP_OWNER, target_map={0: SPELL_UID})
    return H._snapshot(db, bstate, player_champ, ai_champ, game, start)


def test_discombobulate_counters_the_chosen_spell_as_the_live_path_does():
    interpreted = _run_interpreted(_scenario_with_spell_on_chain())
    live = _run_live(_scenario_with_spell_on_chain())
    assert interpreted == live
    locations = {uid: location for uid, _owner, location, _damage, _buffs in interpreted["cards"]}
    assert locations[SPELL_UID].lower() != "castspells", locations
