"""Client built-in champion keyword rules (Parser.cs BuiltInResources)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database

SRC = fresh_database()

import game_engine

from tests.tests_combat import (make_db, add_card, HandlerStub, SessionStub,
                                TPL_GLADIATOR)

BURNING = "3932ad3a-9807-de2a-e1b9-a8b50e81def9"
VULNERABLE = "b01d4e3c-bae6-c55f-ff4e-db0513d74484"


def _setup(db):
    pl_t = game_engine.UID.make(244, 5)
    ai_t = game_engine.UID.make(3, 1000)
    game = game_engine.Game(1, pl_t, ai_t)
    handler = HandlerStub(db)
    ai_champ = int(handler._ai_champ_scid.uid.uid64)
    player_champ = int(handler._player_champ_scid.uid.uid64)
    bstate = {"player_health": 20, "ai_health": 20, "turn_number": 3,
              "turn_player": "player", "stack": [],
              "_rules_port_attached": True,
              "champ_map": {"0": ai_champ, "5": player_champ}}
    handler._current_bstate = bstate
    return pl_t, ai_t, game, handler, bstate, player_champ, ai_champ


def test_burning_champion_takes_damage_at_its_own_end_of_turn(db):
    from rules_port.triggers import dispatch_native_trigger
    pl_t, ai_t, game, handler, bstate, player_champ, ai_champ = _setup(db)
    bstate["champion_counters"] = {str(player_champ): {BURNING: 2},
                                   str(ai_champ): {BURNING: 2}}
    result = dispatch_native_trigger(
        db=db, handler=handler, game=game, session=SessionStub(),
        player_uid=pl_t, ai_uid=ai_t, battle_state=bstate,
        event_type="TurnEndedEvent", source_card_id=player_champ,
        source_player_id=5)
    print(result, game.events)
    assert bstate["player_health"] == 18, (result, bstate)
    assert bstate["champion_counters"][str(player_champ)] == {BURNING: 1}
    # Only the active player's champion burns.
    assert bstate["ai_health"] == 20
    assert bstate["champion_counters"][str(ai_champ)] == {BURNING: 2}
    assert not bstate.get("stack")
    health = [e for e in game.events
              if isinstance(e, game_engine.ChampionHealthChangedSessionEventArgs)]
    assert [(e.old_damage_value, e.new_damage_value) for e in health] == [(20, 18)]


def _context(db, handler, game, pl_t, ai_t, bstate):
    from rules_port.context import EffectContext
    return EffectContext.from_rules_port(
        game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")


def test_vulnerable_troop_takes_double_damage(db):
    import json
    from rules_port.damage_effects import deal_damage
    pl_t, ai_t, game, handler, bstate, _pc, _ac = _setup(db)
    add_card(db, 201, 5, TPL_GLADIATOR, loc="warzone")
    add_card(db, 202, 5, TPL_GLADIATOR, loc="warzone")
    db.execute(
        "UPDATE game_cards SET permanent_buffs=? WHERE card_uid=201",
        (json.dumps({"counters": {"vulnerable": 1},
                     "counter_guids": {"vulnerable": VULNERABLE}}),))
    db.commit()
    context = _context(db, handler, game, pl_t, ai_t, bstate)
    deal_damage(context, 201, 1)
    deal_damage(context, 202, 1)
    damage = dict(db.execute(
        "SELECT card_uid, card_damage FROM game_cards "
        "WHERE card_uid IN (201, 202)").fetchall())
    assert damage == {201: 2, 202: 1}, damage


def test_soulcursed_death_sacrifices_each_soulcursed_troop(db):
    import json
    from rules_port.death_effects import kill_troop
    from rules_port.resolution import resolve_port_trigger
    pl_t, ai_t, game, handler, bstate, _pc, _ac = _setup(db)
    cursed = json.dumps({"int_attrs": {"Soulcursed": 1}})
    for uid in (301, 302, 303):
        add_card(db, uid, 0, TPL_GLADIATOR, loc="warzone")
    db.execute("UPDATE game_cards SET permanent_buffs=? "
               "WHERE card_uid IN (301, 302)", (cursed,))
    db.commit()
    context = _context(db, handler, game, pl_t, ai_t, bstate)
    kill_troop(context, 301)
    for _ in range(4):
        if not bstate.get("stack"):
            break
        item = bstate["stack"].pop()
        resolve_port_trigger(handler, game, SessionStub(), db, pl_t, ai_t,
                             bstate, item)
    locations = dict(db.execute(
        "SELECT card_uid, location FROM game_cards "
        "WHERE card_uid IN (301, 302, 303)").fetchall())
    assert locations == {301: "discard", 302: "discard", 303: "warzone"}, (
        locations, bstate.get("stack"))


if __name__ == "__main__":
    # Written in pytest style: run through pytest so each test executes and failures
    # set the exit status. A missing pytest fails loudly.
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
