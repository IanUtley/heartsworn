"""The interpreter's play-for-free effect (PlayCardAbilityEffectTemplate) plays the cards the live path plays."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pytest  # noqa: E402

import tests.ir_equivalence as H  # noqa: E402
import db as dbmod  # noqa: E402  (after the harness sets up the test database)
from tests.tests_combat import SessionStub, add_card  # noqa: E402
import db as dbmod

TRANSCEND_ABILITY = "f0104d23-f3c2-b457-fef5-af6eaa9c9b05"  # "Play all Standard resources in your deck."
RESOURCE_TEMPLATE = "b253393b-fdde-47c4-9288-4b8efb0698b1"  # Blood Shard (a Standard resource)
RESOURCE_UIDS = (950, 951)


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


def _scenario_with_deck_resources():
    scenario = H._scenario(TRANSCEND_ABILITY, [], any_template=True)
    assert scenario is not None
    db = scenario[0]
    for uid in RESOURCE_UIDS:
        add_card(db, uid, H.TROOP_OWNER, RESOURCE_TEMPLATE, loc="deck")
        db.execute("UPDATE game_cards SET card_type='Resource' WHERE card_uid=?", (uid,))
    db.commit()
    return scenario


def _run_interpreted(scenario):
    from rules_port.context import EffectContext
    from rules_port.ir_exec import run_ability

    db, pl_t, ai_t, game, handler, bstate, player_champ, ai_champ = scenario
    start = len(game.events)
    context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
    assert run_ability(TRANSCEND_ABILITY, None, context=context, source_uid=900, owner_uid=H.TROOP_OWNER,
                       event={"player": H.TROOP_OWNER})
    return H._snapshot(db, bstate, player_champ, ai_champ, game, start)


def _run_live(scenario):
    from rules_port.resolution import resolve_port_ability

    db, pl_t, ai_t, game, handler, bstate, player_champ, ai_champ = scenario
    start = len(game.events)
    resolve_port_ability(handler, game, SessionStub(), db, pl_t, ai_t, bstate, TRANSCEND_ABILITY, 900,
                         H.TROOP_OWNER)
    return H._snapshot(db, bstate, player_champ, ai_champ, game, start)


def test_transcend_plays_the_deck_resources_for_free_as_the_live_path_does():
    interpreted = _run_interpreted(_scenario_with_deck_resources())
    live = _run_live(_scenario_with_deck_resources())
    assert interpreted == live
    # The two Standard resources were played, so they left the deck.
    locations = {uid: location for uid, _owner, location, _damage, _buffs in interpreted["cards"]}
    for uid in RESOURCE_UIDS:
        assert locations[uid] != "deck", locations
    assert interpreted["events"], "the free plays pushed no client events"


if __name__ == "__main__":
    # Written in pytest style: run through pytest so each test executes and failures
    # set the exit status. A missing pytest fails loudly.
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
