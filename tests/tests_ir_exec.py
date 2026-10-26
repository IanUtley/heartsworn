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


def test_is_subtype_matches_the_way_csharp_has_subtype(db):
    """IsSubType: "*" matches any card; a single word matches a whole word, ignoring case; a multi-word
    subtype matches as a substring; an empty subtype line matches nothing but "*"."""
    from rules_port import ir_exec

    def card(subtype):
        return ir_exec.Card(uid=1, owner=5, zone="warzone", is_champion=False, card_type="Troop",
                            counters={}, subtype=subtype)

    assert ir_exec._is_subtype("*", card(""))
    assert ir_exec._is_subtype("Plant", card("Plant Wormoid"))
    assert ir_exec._is_subtype("plant", card("Plant Wormoid"))
    assert not ir_exec._is_subtype("Plan", card("Plant Wormoid"))
    assert ir_exec._is_subtype("Human Cleric", card("Human Cleric"))
    assert not ir_exec._is_subtype("Cleric Human", card("Human Cleric"))
    assert not ir_exec._is_subtype("Plant", card(""))


PLAYER_DAMAGE_ABILITY = "862684d8-ae12-f328-d385-3605d5a1b28e"   # "At the start of your turn, #SELF# deals 2 damage to you."
FIXED_SUMMON_ABILITY = "8c275413-824d-a72b-bbf1-c206f27f645a"     # "Deploy - Summon an <token>."
FIXED_TOKEN_TEMPLATE = "b325a831-b111-4c27-9901-15c73f8a333b"


def test_an_automatic_player_target_is_the_controllers_champion(db):
    """"You" is an automatic player target: the controller's champion takes the damage, the opponent's does not."""
    from rules_port.context import EffectContext
    from rules_port.ir_exec import run_ability

    pl_t, ai_t, game, handler, bstate, _player_champ, _ai_champ = _setup(db)
    add_card(db, 900, 5, TPL_GLADIATOR, loc="warzone")
    db.commit()
    context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
    assert run_ability(PLAYER_DAMAGE_ABILITY, "TurnStartedEvent", context=context,
                       source_uid=900, owner_uid=5, event={"player": 5})
    assert bstate["player_health"] == 18 and bstate["ai_health"] == 20, bstate


def test_a_fixed_token_summon_creates_the_token_for_the_controller(db):
    """A summon with no choice creates its token in the controller's warzone, through the live token primitive."""
    from rules_port.context import EffectContext
    from rules_port.ir_exec import run_ability

    pl_t, ai_t, game, handler, bstate, _player_champ, _ai_champ = _setup(db)
    add_card(db, 900, 5, TPL_GLADIATOR, loc="warzone")
    db.commit()
    context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
    assert run_ability(FIXED_SUMMON_ABILITY, "CardEnteredZoneEvent", context=context, source_uid=900,
                       owner_uid=5, event={"player": 5, "card": 900, "from": "hand", "to": "warzone"})
    tokens = db.execute("SELECT user_id, location FROM game_cards WHERE template_guid=? AND card_uid!=900",
                        (FIXED_TOKEN_TEMPLATE,)).fetchall()
    assert tokens == [(5, "warzone")], tokens


SACRIFICE_CHOICE_ABILITY = "0d6e4d1f-58ce-93b1-50d5-33d114d332a9"   # "At the start of your turn, sacrifice another troop."


def test_a_chosen_target_pauses_and_resumes_from_its_continuation(db):
    """A target the player picks pauses the ability with a continuation that survives a JSON round trip; the
    answer resumes it, and an answer that is not a candidate is refused."""
    import json
    from rules_port import ir_exec
    from rules_port.context import EffectContext

    pl_t, ai_t, game, handler, bstate, _player_champ, _ai_champ = _setup(db)
    add_card(db, 900, 5, TPL_GLADIATOR, loc="warzone")
    add_card(db, 600, 5, TPL_GLADIATOR, loc="warzone")
    add_card(db, 601, 5, TPL_GLADIATOR, loc="warzone")
    db.commit()
    context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
    try:
        ir_exec.run_ability(SACRIFICE_CHOICE_ABILITY, "TurnStartedEvent", context=context,
                            source_uid=900, owner_uid=5, event={"player": 5})
        raise AssertionError("the ability did not pause for its choice")
    except ir_exec.NeedsChoice as pause:
        candidates = sorted(pause.candidates)
        continuation = json.loads(json.dumps(pause.continuation))
    assert candidates == [600, 601], candidates
    try:
        ir_exec.resume_ability(continuation, 999, context=context)
        raise AssertionError("a non-candidate answer was accepted")
    except ValueError:
        pass
    ir_exec.resume_ability(continuation, 601, context=context)
    locations = dict(db.execute("SELECT card_uid, location FROM game_cards WHERE card_uid IN (600, 601)").fetchall())
    assert locations == {600: "warzone", 601: "discard"}, locations


INVOKER_TEXT = """
INVOKE = ActivateAbilityEffectTemplate(
    name="InvokeTheSacrificeChoice",
    template_id=Ref("00000000-0000-0000-0000-00000000c0d1"),
    ability_to_invoke=Ref("0d6e4d1f-58ce-93b1-50d5-33d114d332a9"),
)
INVOKER = AbilityTemplate(
    ability_template_id=Ref("00000000-0000-0000-0000-00000000c0d0"),
    name="InvokesTheSacrificeChoiceAtTheStartOfYourTurn",
    trigger_event_type={"m_InternalType": "Game.Shared.Mechanics.TurnStartedEvent"},
    trigger_condition=TriggerPlayerControlsAbilitySource(),
    ability_target_template_ids=[Ref("190a4d8c-7c2c-10d0-6429-99c5aeb0791f")],
    ability_effect_list=[Clause(effect_template_id=Ref("00000000-0000-0000-0000-00000000c0d1"),
                                target_template_index=0)],
)
"""


def test_an_invoked_choice_pauses_and_resumes_the_invoking_ability(db):
    """An ability that invokes another ability which pauses for a choice keeps the invoking ability as an outer
    frame of the continuation (JSON round trip included). The answer resumes the child, and the invoker resumes
    after its invoke clause."""
    import json
    from gamedata import ir_python, ir_registry
    from rules_port import ir_exec
    from rules_port.context import EffectContext

    ir_registry.clear()
    names = ir_python.loads(INVOKER_TEXT)
    ir_exec.register_card(names)
    try:
        pl_t, ai_t, game, handler, bstate, _player_champ, _ai_champ = _setup(db)
        add_card(db, 900, 5, TPL_GLADIATOR, loc="warzone")
        add_card(db, 600, 5, TPL_GLADIATOR, loc="warzone")
        add_card(db, 601, 5, TPL_GLADIATOR, loc="warzone")
        db.commit()
        context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
        invoker = str(names["INVOKER"].m_AbilityTemplateId["m_Guid"]).lower()
        try:
            ir_exec.run_ability(invoker, "TurnStartedEvent", context=context, source_uid=900, owner_uid=5,
                                event={"player": 5})
            raise AssertionError("the invoked choice did not pause")
        except ir_exec.NeedsChoice as pause:
            candidates = sorted(pause.candidates)
            continuation = json.loads(json.dumps(pause.continuation))
        assert candidates == [600, 601], candidates
        assert continuation["parent"]["ability_guid"] == invoker, continuation
        ir_exec.resume_ability(continuation, 601, context=context)
        locations = dict(db.execute("SELECT card_uid, location FROM game_cards WHERE card_uid IN (600, 601)").fetchall())
        assert locations == {600: "warzone", 601: "discard"}, locations
    finally:
        ir_exec.clear_cards()
        ir_registry.clear()


ROLLER_TEXT = """
ROLL = RandomizeVariableEffectTemplate(
    name="RollOneOrTwo",
    template_id=Ref("00000000-0000-0000-0000-00000000c0e1"),
    variable_name="RandomNumber", min_value=1, max_value=2,
)
ONE_ROLLED = AbilityEffectConditionTemplate(
    name="RolledOne",
    template_id=Ref("00000000-0000-0000-0000-00000000c0e2"),
    condition=AbilityVariableCondition(lhs="RandomNumber", rhs="1", comparison_op="Equals"),
)
DAMAGE_IF_ONE = ActivateAbilityEffectTemplate(
    name="InvokeTheThreeDamage",
    template_id=Ref("00000000-0000-0000-0000-00000000c0e3"),
    ability_to_invoke=Ref("79e4a481-fc29-14c0-4524-5db861bd6f0b"),
)
ROLLER = AbilityTemplate(
    ability_template_id=Ref("00000000-0000-0000-0000-00000000c0e0"),
    name="RollsOneOrTwoAndDealsThreeIfOne",
    trigger_event_type={"m_InternalType": "Game.Shared.Mechanics.TurnStartedEvent"},
    trigger_condition=TriggerPlayerControlsAbilitySource(),
    variables=[AbilityVariable(name="RandomNumber")],
    ability_target_template_ids=[Ref("190a4d8c-7c2c-10d0-6429-99c5aeb0791f")],
    ability_effect_list=[
        Clause(effect_template_id=Ref("00000000-0000-0000-0000-00000000c0e1"), target_template_index=0),
        Clause(effect_template_id=Ref("00000000-0000-0000-0000-00000000c0e3"), target_template_index=0,
               condition_id=Ref("00000000-0000-0000-0000-00000000c0e2")),
    ],
)
"""


class _FixedRng:
    """A session RNG that always draws the same number (the draw is taken modulo the bound)."""

    def __init__(self, value):
        self.value = value

    def next(self, bound):
        return self.value % bound


def test_a_random_variable_picks_the_branch_its_condition_tests(db):
    """RandomizeVariable draws from the session RNG into the ability's variable; a clause conditioned on the
    variable runs its invoke only for the drawn value. The draw of 1 invokes the damage, the draw of 2 does not."""
    from gamedata import ir_python, ir_registry
    from rules_port import ir_exec
    from rules_port.context import EffectContext

    ir_registry.clear()
    names = ir_python.loads(ROLLER_TEXT)
    ir_exec.register_card(names)
    try:
        roller = str(names["ROLLER"].m_AbilityTemplateId["m_Guid"]).lower()
        for draw, expected_ai_health in ((0, 17), (1, 20)):
            pl_t, ai_t, game, handler, bstate, _player_champ, _ai_champ = _setup(db)
            bstate["_rules_rng"] = _FixedRng(draw)
            add_card(db, 900, 5, TPL_GLADIATOR, loc="warzone")
            db.commit()
            context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
            assert ir_exec.run_ability(roller, "TurnStartedEvent", context=context, source_uid=900,
                                       owner_uid=5, event={"player": 5})
            assert bstate["ai_health"] == expected_ai_health, (draw, bstate["ai_health"])
    finally:
        ir_exec.clear_cards()
        ir_registry.clear()


if __name__ == "__main__":
    import db as dbmod
    for run in (test_burning_ability_runs_from_its_ir, test_dazed_and_vulnerable_removals_match_the_live_engine,
                test_soulcursed_sacrifices_each_soulcursed_troop_when_one_dies,
                test_static_effects_match_the_live_engine_when_opted_in,
                test_spitfire_elemental_damages_each_opposing_champion_and_troop,
                test_burning_runs_from_the_loaded_card_file,
                test_a_python_only_card_runs_in_the_interpreter,
                test_named_guids_resolve_across_cards_and_refuse_clashes,
                test_is_subtype_matches_the_way_csharp_has_subtype,
                test_an_automatic_player_target_is_the_controllers_champion,
                test_a_fixed_token_summon_creates_the_token_for_the_controller,
                test_a_chosen_target_pauses_and_resumes_from_its_continuation,
                test_an_invoked_choice_pauses_and_resumes_the_invoking_ability,
                test_a_random_variable_picks_the_branch_its_condition_tests):
        dbmod._db = make_db()
        run(dbmod._db)
    print("ok")
