"""The interpreter runs the Burning ability from its IR and reaches the live engine's outcome."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.tests_builtin_counter_rules import BURNING, TPL_GLADIATOR, SessionStub, _setup  # noqa: E402
from tests.tests_combat import make_db, add_card  # noqa: E402  (the fixture reads make_db from this module)

DAZED = "9c98dace-4cac-0fda-8242-f7ef2c1d3cf0"
VULNERABLE = "b01d4e3c-bae6-c55f-ff4e-db0513d74484"
TROOP_UID = 201
SOULCURSED_ABILITY_GUID = "e2464b7a-15f1-46a2-8b9b-74e03f313043"
SPITFIRE_ABILITY_GUID = "f875adf6-11e2-c6e4-a08f-9ef79d10b460"


def test_burning_ability_runs_from_its_ir(db):
    from rules_port.builtin_rules import BURNING_ABILITY_GUID
    from rules_port.context import EffectContext
    from rules_port.ir_exec import run_ability

    pl_t, ai_t, game, handler, bstate, player_champ, ai_champ = _setup(db)
    bstate["champion_counters"] = {str(player_champ): {BURNING: 2},
                                   str(ai_champ): {BURNING: 2}}
    context = EffectContext.from_rules_port(
        game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
    fired = run_ability(BURNING_ABILITY_GUID, "TurnEndedEvent", context=context,
                        source_uid=player_champ, owner_uid=5)
    assert fired
    # The live test's outcome: the player's champion takes 2 and loses one counter; the AI is untouched.
    assert bstate["player_health"] == 18, bstate
    assert bstate["champion_counters"][str(player_champ)] == {BURNING: 1}
    assert bstate["ai_health"] == 20
    assert bstate["champion_counters"][str(ai_champ)] == {BURNING: 2}


def test_dazed_and_vulnerable_removals_match_the_live_engine(db):
    """The removal abilities leave the counters the live engine leaves: a champion's Dazed, and a
    champion's and a troop's Vulnerable (the troop is an automatic target found by its filter)."""
    import json
    from rules_port.builtin_rules import DAZED_REMOVAL_ABILITY_GUID, VULNERABLE_REMOVAL_ABILITY_GUID
    from rules_port.context import EffectContext
    from rules_port.ir_exec import run_ability

    pl_t, ai_t, game, handler, bstate, player_champ, _ai = _setup(db)
    bstate["champion_counters"] = {str(player_champ): {DAZED: 2}}
    context = EffectContext.from_rules_port(
        game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
    assert run_ability(DAZED_REMOVAL_ABILITY_GUID, "TurnEndedEvent", context=context,
                       source_uid=player_champ, owner_uid=5)
    assert bstate["champion_counters"][str(player_champ)] == {DAZED: 1}

    pl_t, ai_t, game, handler, bstate, player_champ, _ai = _setup(db)
    bstate["champion_counters"] = {str(player_champ): {VULNERABLE: 2}}
    add_card(db, TROOP_UID, 5, TPL_GLADIATOR, loc="warzone")
    db.execute("UPDATE game_cards SET permanent_buffs=? WHERE card_uid=?",
               (json.dumps({"counters": {"vulnerable": 1},
                            "counter_guids": {"vulnerable": VULNERABLE}}), TROOP_UID))
    db.commit()
    context = EffectContext.from_rules_port(
        game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
    assert run_ability(VULNERABLE_REMOVAL_ABILITY_GUID, "TurnEndedEvent", context=context,
                       source_uid=player_champ, owner_uid=5)
    assert bstate["champion_counters"][str(player_champ)] == {VULNERABLE: 1}
    troop = json.loads(db.execute("SELECT permanent_buffs FROM game_cards WHERE card_uid=?",
                                  (TROOP_UID,)).fetchone()[0])
    assert troop["counters"] == {}, troop


def test_soulcursed_sacrifices_each_soulcursed_troop_when_one_dies(db):
    """A Soulcursed troop dying (a zone move from the warzone to the discard) sacrifices each other
    troop of its owner that is Soulcursed, as the live death path does."""
    import json
    from rules_port.context import EffectContext
    from rules_port.ir_exec import run_ability

    pl_t, ai_t, game, handler, bstate, _player_champ, ai_champ = _setup(db)
    cursed = json.dumps({"int_attrs": {"Soulcursed": 1}})
    for uid in (301, 302, 303):
        add_card(db, uid, 0, TPL_GLADIATOR, loc="warzone")
    db.execute("UPDATE game_cards SET permanent_buffs=? WHERE card_uid IN (301, 302)", (cursed,))
    db.execute("UPDATE game_cards SET location='discard' WHERE card_uid=301")
    db.commit()
    context = EffectContext.from_rules_port(
        game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
    assert run_ability(SOULCURSED_ABILITY_GUID, "CardEnteredZoneEvent", context=context,
                       source_uid=ai_champ, owner_uid=0,
                       event={"player": 0, "card": 301, "from": "warzone", "to": "discard"})
    locations = dict(db.execute(
        "SELECT card_uid, location FROM game_cards WHERE card_uid IN (301, 302, 303)").fetchall())
    assert locations == {301: "discard", 302: "discard", 303: "warzone"}, locations


def test_static_effects_match_the_live_engine_when_opted_in(db):
    """Vulnerable's damage factor and Dazed's cost increase: the interpreter gives the live engine's
    values for a Vulnerable troop and champion, and for a Dazed troop and resource in hand."""
    import json
    from rules_port import ir_exec
    from rules_port.builtin_rules import (DAZED_ABILITY_GUID, VULNERABLE_ABILITY_GUID,
                                          dazed_cost_increase, vulnerable_damage_factor)
    from rules_port.context import EffectContext
    from rules_port.static_rules import champion_static_intattrs  # noqa: F401  (import check)

    pl_t, ai_t, game, handler, bstate, player_champ, _ai = _setup(db)
    bstate["champion_counters"] = {str(player_champ): {VULNERABLE: 2, DAZED: 1}}
    add_card(db, TROOP_UID, 5, TPL_GLADIATOR, loc="warzone")
    db.execute("UPDATE game_cards SET permanent_buffs=? WHERE card_uid=?",
               (json.dumps({"counters": {"vulnerable": 1},
                            "counter_guids": {"vulnerable": VULNERABLE}}), TROOP_UID))
    add_card(db, 401, 5, TPL_GLADIATOR, loc="hand")
    resource = db.execute("SELECT guid FROM card_templates WHERE card_type='Resource' LIMIT 1").fetchone()[0]
    add_card(db, 402, 5, resource, loc="hand")
    db.execute("UPDATE game_cards SET card_type='Resource' WHERE card_uid=402")
    db.commit()
    context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
    view = ir_exec.BattleView(db, 1, bstate)

    def live():
        return (vulnerable_damage_factor(context, TROOP_UID, False),
                vulnerable_damage_factor(context, player_champ, True),
                dazed_cost_increase(None, bstate, 5, "Troop"),
                dazed_cost_increase(None, bstate, 5, "Resource"))

    def interpreted():
        return (ir_exec.static_int_set(context, TROOP_UID, "DamageReceivedMultiplier") or 1,
                ir_exec.static_int_set(context, player_champ, "DamageReceivedMultiplier") or 1,
                ir_exec.static_cost_increase(view, 401, 5),
                ir_exec.static_cost_increase(view, 402, 5))

    expected = live()
    assert expected == (2, 2, 1, 0), expected
    ir_exec.ENABLED.update({VULNERABLE_ABILITY_GUID, DAZED_ABILITY_GUID})
    try:
        assert interpreted() == expected, (interpreted(), expected)
        assert vulnerable_damage_factor(context, TROOP_UID, False) == 2
    finally:
        ir_exec.ENABLED.clear()


def test_spitfire_elemental_damages_each_opposing_champion_and_troop(db):
    """A troop's start-of-its-owner's-turn ability: 1 damage to each opposing champion and troop,
    dealt by the troop (the live engine's result: the champion goes 20 to 19, the troop takes 1)."""
    from rules_port.context import EffectContext
    from rules_port.ir_exec import run_ability

    spitfire = db.execute("SELECT guid FROM card_templates WHERE name='Spitfire Elemental' LIMIT 1").fetchone()[0]
    pl_t, ai_t, game, handler, bstate, _player_champ, _ai = _setup(db)
    add_card(db, 501, 0, spitfire, loc="warzone")
    add_card(db, 502, 5, TPL_GLADIATOR, loc="warzone")
    db.commit()
    context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
    assert run_ability(SPITFIRE_ABILITY_GUID, "TurnStartedEvent", context=context,
                       source_uid=501, owner_uid=0)
    assert bstate["player_health"] == 19, bstate
    assert db.execute("SELECT card_damage FROM game_cards WHERE card_uid=502").fetchone()[0] == 1


def test_burning_runs_from_the_loaded_card_file(db):
    """The interpreter runs Burning from its card file (ir_cards/surface/burning.py): the root and the two
    effects come from the card, and the outcome is the same as from the Records."""
    from ir_cards.load import load_card
    from rules_port import ir_exec
    from rules_port.builtin_rules import BURNING_ABILITY_GUID
    from rules_port.context import EffectContext

    ir_exec.register_card(load_card("ir_cards/surface/burning.py"))
    try:
        assert ("AbilityTemplate", BURNING_ABILITY_GUID) in ir_exec._CARD_RECORDS
        pl_t, ai_t, game, handler, bstate, player_champ, ai_champ = _setup(db)
        bstate["champion_counters"] = {str(player_champ): {BURNING: 2}, str(ai_champ): {BURNING: 2}}
        context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
        assert ir_exec.run_ability(BURNING_ABILITY_GUID, "TurnEndedEvent", context=context,
                                   source_uid=player_champ, owner_uid=5)
        assert bstate["player_health"] == 18, bstate
        assert bstate["champion_counters"][str(player_champ)] == {BURNING: 1}
        assert bstate["ai_health"] == 20
    finally:
        ir_exec.clear_cards()


CUSTOM_CARD = """
TWO = EffectConstant(value=2)
DEAL_TWO = CardModifierAbilityEffectTemplate(
    name="CustomDealTwoToThis",
    template_id=Ref("00000000-0000-0000-0000-00000000c0de"),
    modifier=DamageModifier(input_value=TWO),
)
CUSTOM = AbilityTemplate(
    ability_template_id=Ref("00000000-0000-0000-0000-00000000c0de"),
    name="CustomAtTheStartOfYourTurnThisTakesTwoDamage",
    trigger_event_type={"m_InternalType": "Game.Shared.Mechanics.TurnStartedEvent"},
    trigger_condition=TriggerPlayerControlsAbilitySource(),
    ability_target_template_ids=[Ref("190a4d8c-7c2c-10d0-6429-99c5aeb0791f")],
    ability_effect_list=[Clause(effect_template_id=Ref("00000000-0000-0000-0000-00000000c0de"),
                                target_template_index=0)],
)
"""


def test_a_python_only_card_runs_in_the_interpreter(db):
    """A card that is not in the Records runs from its Python text: the text is loaded (not executed), its
    templates are registered, and the interpreter runs it. Unsafe text is refused."""
    from gamedata import ir_python
    from rules_port import ir_exec
    from rules_port.context import EffectContext

    from gamedata import ir_registry
    ir_registry.clear()
    names = ir_python.loads(CUSTOM_CARD)
    for text in ("import os", "x = __import__('os')", "x = open('f')", "x = AbilityTemplate(name=y)"):
        try:
            ir_python.loads(text)
        except ValueError:
            continue
        raise AssertionError(f"unsafe text was accepted: {text}")
    ir_exec.register_card(names)
    try:
        pl_t, ai_t, game, handler, bstate, _player_champ, _ai = _setup(db)
        add_card(db, 501, 5, TPL_GLADIATOR, loc="warzone")
        db.commit()
        context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
        assert ir_exec.run_ability("00000000-0000-0000-0000-00000000c0de", "TurnStartedEvent",
                                   context=context, source_uid=501, owner_uid=5)
        assert db.execute("SELECT card_damage FROM game_cards WHERE card_uid=501").fetchone()[0] == 2
    finally:
        ir_exec.clear_cards()
        ir_registry.clear()


def test_named_guids_resolve_across_cards_and_refuse_clashes(db):
    """A template bound to a name in one card is found by name from later text; a clashing name is refused."""
    from gamedata import ir_python, ir_registry
    from ir_cards.load import load_card

    ir_registry.clear()
    try:
        load_card("ir_cards/custom/start_of_turn_two_damage.py")
        clause = ir_python.loads(
            'X = Clause(effect_template_id=Named("start_of_turn_two_damage.DEAL_TWO"), target_template_index=0)')["X"]
        assert clause.get("m_EffectTemplateId") == {"m_Guid": "00000000-0000-0000-0000-00000000c0df"}, clause
        try:
            ir_python.loads('DEAL_TWO = CardModifierAbilityEffectTemplate(template_id=Ref("00000000-0000-0000-0000-0000000000ff"))',
                            scope="start_of_turn_two_damage")
        except ValueError:
            pass
        else:
            raise AssertionError("a clashing name was accepted")
    finally:
        ir_registry.clear()


if __name__ == "__main__":
    import db as dbmod
    for run in (test_burning_ability_runs_from_its_ir, test_dazed_and_vulnerable_removals_match_the_live_engine,
                test_soulcursed_sacrifices_each_soulcursed_troop_when_one_dies,
                test_static_effects_match_the_live_engine_when_opted_in,
                test_spitfire_elemental_damages_each_opposing_champion_and_troop,
                test_burning_runs_from_the_loaded_card_file,
                test_a_python_only_card_runs_in_the_interpreter,
                test_named_guids_resolve_across_cards_and_refuse_clashes):
        dbmod._db = make_db()
        run(dbmod._db)
    print("ok")
