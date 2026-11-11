#!/usr/bin/env python3
"""Tunneling modifiers start from the printed value (Tectonic Megahulk).

Excavation Bot's power tunnels a random Robot and gives it Tunneling +4 only
if it does not already have Tunneling.  Tectonic Megahulk prints Tunneling 15
in its CardTemplate TAC and gets "Tunneling -1 for each Dwarf and/or Robot you
control" at the start of its controller's turn.  It used to surface on the
next turn: the filter missed the printed value (so +4 applied, from 0), and
the -1 then started from that 4.

    python3 tests/tests_tunneling_modifiers.py
"""

import contextlib
import io
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests"),
                os.path.join(ROOT, "campaign")]
from tests.test_db import fresh_database  # noqa: E402
fresh_database()   # bind the scratch test database before any module imports db

import sqlite3  # noqa: E402

import tests_card_play_matrix as matrix  # noqa: E402
from rules_port.context import EffectContext  # noqa: E402
from rules_port.resolution import resolve_port_ability  # noqa: E402
from rules_port.triggers import dispatch_native_trigger  # noqa: E402
from rules_port.tunneling import advance, queue_surfaces, tunneling_value  # noqa: E402

# The card Excavation Bot (Robot, 4/3/2) and its Deploy: choose one of your underground troops, add two tunneling counters.
EXCAVATION_CARD = "0d77f670-caaf-4e12-a605-35bd9c704191"
EXCAVATION_DEPLOY = "8e167978-9b2f-0ec6-5202-05b02f692bca"
BOT_UID = 0x701
EXCAVATION_BOT = "25ce3c67-3b47-4f64-a3c0-66eb30486b6f"
CHARGE_BOT = "7325706e-6bf1-4ca4-8d6b-5da13ac069f4"
# The alternate-art template's TAC starts with BaseVersionGuid.
MEGAHULK = "0c7a4e38-25ab-45e4-9d6b-faefea971b89"
HULK = 20001


def _seat(db, robots):
    db.execute("DELETE FROM game_cards")
    abilities = [row[0] for row in db.execute(
        "SELECT ability_guid FROM champion_abilities WHERE champion_guid=?",
        (EXCAVATION_BOT,))]
    db.execute(
        "INSERT INTO game_cards (session_id,user_id,card_uid,template_guid,card_template_id,"
        "location,position,card_state,card_abilities,card_type,card_attributes,card_attack_mod,"
        "card_defense_mod,card_cost_mod,card_damage,permanent_buffs,temporary_buffs,card_uses,"
        "resolved_at,original_template_guid,is_champion) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (matrix.SESSION_ID, matrix.AI_OWNER, matrix.AI_CHAMP, EXCAVATION_BOT, EXCAVATION_BOT,
         "warzone", 0, 0, json.dumps(abilities), "Champion", 0, 0, 0, 0, 0, "{}", "{}", "{}",
         0, EXCAVATION_BOT, 1))
    # The Megahulk is already underground: the Excavation Bot's Deploy chooses it.
    matrix._insert_card(db, HULK, matrix.AI_OWNER, "underground", MEGAHULK, 0)
    matrix._insert_card(db, BOT_UID, matrix.AI_OWNER, "warzone", EXCAVATION_CARD, 0)
    for index in range(robots):
        matrix._insert_card(db, 20100 + index, matrix.AI_OWNER, "warzone", CHARGE_BOT, index)
    db.commit()


def _hulk(db):
    buffs = json.loads(db.execute(
        "SELECT permanent_buffs FROM game_cards WHERE card_uid=?", (HULK,)).fetchone()[0] or "{}")
    attrs = buffs.get("int_attrs", {})
    return (int(buffs.get("counters", {}).get("tunneling", 0)),
            tunneling_value(db, MEGAHULK, attrs), attrs)


def _run(robots, max_turns=20):
    """Return [(turn, counter, threshold)] up to and including the surface."""
    db = sqlite3.connect(matrix.SRC)
    db.row_factory = sqlite3.Row
    _seat(db, robots)
    handler = matrix.HandlerStub(db)
    session = matrix.SessionStub()
    state = matrix._new_state(handler)
    state["turn_player"] = "ai"
    state["resolving_owner_id"] = matrix.AI_OWNER
    state["resolving_source_uid"] = matrix.AI_CHAMP
    with contextlib.redirect_stdout(io.StringIO()):
        game = handler._fresh_game(session, matrix.PLAYER_UID, matrix.AI_UID, state)
        resolve_port_ability(handler, game, session, db, matrix.PLAYER_UID, matrix.AI_UID,
                             state, EXCAVATION_DEPLOY, BOT_UID, matrix.AI_OWNER,
                             target_map={0: [HULK]})
        matrix._drain_triggers(handler, game, session, db, state)
    counter, threshold, attrs = _hulk(db)
    assert threshold == 15 and "Tunneling" not in attrs, \
        f"Megahulk already has Tunneling 15; the +4 must not apply: {attrs}"
    assert counter == 2, f"Excavation Bot adds two counters, got {counter}"
    history = []
    for turn in range(1, max_turns + 1):
        with contextlib.redirect_stdout(io.StringIO()):
            game = handler._fresh_game(session, matrix.PLAYER_UID, matrix.AI_UID, state)
            context = EffectContext.from_rules_port(
                game, session, db, handler, matrix.PLAYER_UID, matrix.AI_UID, state, "",
                ability=None)
            advance(context, matrix.AI_OWNER)
            dispatch_native_trigger(
                db=db, handler=handler, game=game, session=session,
                player_uid=matrix.PLAYER_UID, ai_uid=matrix.AI_UID, battle_state=state,
                event_type="TurnStartedEvent", source_card_id=matrix.AI_CHAMP,
                source_player_id=matrix.AI_OWNER)
            matrix._drain_triggers(handler, game, session, db, state)
            counter, threshold, _attrs = _hulk(db)
            surfaced = queue_surfaces(context, matrix.AI_OWNER)
        history.append((turn, counter, threshold))
        if surfaced:
            return history
    return history


def test_megahulk_tunneling_counts_down_from_printed_value():
    # One seated Robot plus the Excavation Bot: Tunneling is 15 - 2 = 13 on turn 1 and drops 2 per turn (one per
    # Robot). The Deploy's 2 counters gain 1 per turn, so 2 + t meets 15 - 2t at turn 5 (7 >= 5).
    history = _run(robots=1)
    assert history[0] == (1, 3, 13), history[0]
    assert history[-1] == (5, 7, 5), history
    assert all(counter < threshold for _turn, counter, threshold in history[:-1]), history


def test_megahulk_reduced_below_zero_surfaces():
    # C# surfaces once counters >= Tunneling, including a value pushed <= 0.
    history = _run(robots=20)
    assert history == [(1, 3, 0)], history





SUB_ABILITY = "9543d275-f046-d225-e1be-af514335adca"
SUB_TARGET_TEMPLATE = "bd2d0a91-be12-76d0-b183-786b16c928cc"
SECOND_TROOP = 0x702


def _choice_board(db):
    """The player's Excavation Bot is in play. Two of the player's troops are underground: the Megahulk and a second
    Robot, so the Deploy must ask the player which one to target rather than pick one."""
    db.execute("DELETE FROM game_cards")
    matrix._insert_card(db, HULK, matrix.PLAYER_OWNER, "underground", MEGAHULK, 0)
    matrix._insert_card(db, SECOND_TROOP, matrix.PLAYER_OWNER, "underground", CHARGE_BOT, 0)
    matrix._insert_card(db, BOT_UID, matrix.PLAYER_OWNER, "warzone", EXCAVATION_CARD, 0)
    db.commit()


def _resolve_choice(db, target_map):
    import db as db_module
    previous_default = db_module._db
    db_module._db = db
    handler = matrix.HandlerStub(db)
    session = matrix.SessionStub()
    state = matrix._new_state(handler)
    state["turn_player"] = "player"
    state["resolving_owner_id"] = matrix.PLAYER_OWNER
    state["resolving_source_uid"] = BOT_UID
    with contextlib.redirect_stdout(io.StringIO()):
        game = handler._fresh_game(session, matrix.PLAYER_UID, matrix.AI_UID, state)
        resolve_port_ability(handler, game, session, db, matrix.PLAYER_UID, matrix.AI_UID,
                             state, SUB_ABILITY, BOT_UID, matrix.PLAYER_OWNER, target_map=target_map)
        matrix._drain_triggers(handler, game, session, db, state)
    db_module._db = previous_default
    return state


def _counters_of(db, uid):
    buffs = json.loads(db.execute(
        "SELECT permanent_buffs FROM game_cards WHERE card_uid=?", (uid,)).fetchone()[0] or "{}")
    return int((buffs.get("counters", {}) or {}).get("tunneling", 0))


OPPOSING_TROOP = 0x703


def test_excavation_deploy_adds_counters_only_to_the_chosen_underground_troop():
    """The Deploy's target is one of your own underground troops, chosen by you. Nothing is picked for you, and a
    troop that is not yours or not underground is refused."""
    db = sqlite3.connect(matrix.SRC)
    db.row_factory = sqlite3.Row
    _choice_board(db)
    matrix._insert_card(db, OPPOSING_TROOP, matrix.AI_OWNER, "underground", CHARGE_BOT, 0)
    db.commit()
    # No choice made: the engine does not pick an underground troop for the player.
    _resolve_choice(db, {})
    assert (_counters_of(db, HULK), _counters_of(db, SECOND_TROOP)) == (0, 0)
    # Choose the Megahulk: it gets two counters, the other troop none.
    _resolve_choice(db, {0: [HULK]})
    assert (_counters_of(db, HULK), _counters_of(db, SECOND_TROOP)) == (2, 0)
    # Choose the second Robot instead.
    db = sqlite3.connect(matrix.SRC)
    db.row_factory = sqlite3.Row
    _choice_board(db)
    _resolve_choice(db, {0: [SECOND_TROOP]})
    assert (_counters_of(db, HULK), _counters_of(db, SECOND_TROOP)) == (0, 2)
    # The choices offered are your own underground troops: the opposing troop is not among them.
    from rules_port.targeting import legal_targets
    db = sqlite3.connect(matrix.SRC)
    db.row_factory = sqlite3.Row
    _choice_board(db)
    matrix._insert_card(db, OPPOSING_TROOP, matrix.AI_OWNER, "underground", CHARGE_BOT, 0)
    db.commit()
    offered = legal_targets(db, 1, matrix.PLAYER_OWNER, SUB_TARGET_TEMPLATE, BOT_UID,
                            both_players=True, champions=[], battle_state={})
    assert set(offered) == {HULK, SECOND_TROOP}, offered


if __name__ == "__main__":
    test_excavation_deploy_adds_counters_only_to_the_chosen_underground_troop()
    print("PASS Excavation Bot Deploy targets only the chosen underground troop")
    test_megahulk_tunneling_counts_down_from_printed_value()
    test_megahulk_reduced_below_zero_surfaces()
    print("ok")
