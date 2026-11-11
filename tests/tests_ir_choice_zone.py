"""The interpreter runs an authored pick from the Choosing zone. Its pause is presented as the choice-zone picker, chosen
from the authored InZone filter rather than the collection flags."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import tests.ir_equivalence as H  # noqa: E402
from tests.tests_combat import SessionStub  # noqa: E402
from tests.test_db import isolated_db  # noqa: E402

# An ability whose invoked child picks a card from the Choosing zone (the native path ran it before this change). The
# parent summons the Choosing cards on a damage trigger; the child is run here on the cards the test places.
CHOOSING_PARENT = "bffcd77d-0e4f-34fc-0d73-67cd114c014d"
CHOOSING_CHILD = "d5b56bd5-4d06-995d-6487-d1db36e29853"


def test_pick_kind_follows_the_authored_zone_not_the_flags():
    from rules_port.picks import CHOICE_ZONE, MATCHING_TARGET, REVEALED, SEARCH, ZONE_PICK, pick_kind

    assert pick_kind(authored_zone="choosing", revealed=False, match=False) == CHOICE_ZONE
    assert pick_kind(authored_zone="choosing", revealed=True, match=False) == REVEALED
    assert pick_kind(authored_zone="deck", revealed=False, match=False) == SEARCH
    assert pick_kind(authored_zone="deck", revealed=False, match=True) == MATCHING_TARGET
    assert pick_kind(authored_zone="warzone", revealed=False, match=False) == ZONE_PICK


def _choosing_battle():
    """A battle with two cards of the player's in the Choosing zone, which the ability picks from. The ability is not
    carried by a card template here, so the harness's troop scenario cannot be used."""
    db = H.make_db()
    H.dbmod._db = db
    pl_t, ai_t, game, handler, bstate, _pc, _ac = H.scenarios._setup(db)
    if getattr(handler, "_ai_champ_guid", None) is None:
        handler._ai_champ_guid = H._test_champion_guid()
        handler._player_champ_guid = H._test_champion_guid()
    for uid in (0x201, 0x202):
        H.add_card(db, uid, H.TROOP_OWNER, H.scenarios.TPL_GLADIATOR, loc="choosing")
    db.commit()
    return db, pl_t, ai_t, game, handler, bstate


@isolated_db
def test_choosing_pick_is_run_by_the_interpreter_and_pauses_with_its_zone():
    from rules_port import interpreted_prompts
    from rules_port import ir_exec
    from rules_port.context import EffectContext

    assert ir_exec.engine_runs(CHOOSING_PARENT, 900, 5), "a Choosing pick is no longer routed to the native path"
    db, pl_t, ai_t, game, handler, bstate = _choosing_battle()
    context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
    try:
        ir_exec.run_ability(CHOOSING_CHILD, None, context=context, source_uid=900, owner_uid=5,
                            event={"player": 5})
    except ir_exec.NeedsChoice as pause:
        assert pause.authored_zone == "choosing"
        assert pause.candidates, "the pause names the cards in the Choosing zone"
        assert pause.continuation["ability_guid"]
        # The choice-zone presenter receives the interpreter continuation and the candidates, which the answer paths
        # (application/pending_choices.py and services/pvp_flow.py) resume the interpreter with.
        from rules_port import resolution
        shown = {}

        def prompt_choice_cards(game, session, pl_t, ai_t, bstate, pending):
            shown.update(pending)

        handler._prompt_choice_cards = prompt_choice_cards
        assert interpreted_prompts.prompt_interpreted_reveal(handler, game, SessionStub(), db, pl_t, ai_t, bstate,
                                                    pause) == "waiting"
        assert shown["kind"] == "interpreted_choice_zone"
        assert shown["choice_uids"] == list(pause.candidates)
        assert shown["interpreter"] == pause.continuation
        assert bstate["pending_choice"]["kind"] == "interpreted_choice_zone"
        assert bstate.get("resolution_paused")
    else:
        raise AssertionError("the Choosing pick did not pause for the player")


# A child that picks one card from the player's hand (the parent's Deploy). Run here on the cards the test places.
HAND_PARENT = "55a6024b-1d23-f18f-0ebf-615d0b7c5273"
HAND_CHILD = "06570445-27e3-fc87-2e17-a7b5e1de693d"


@isolated_db
def test_single_hand_pick_is_run_by_the_interpreter_and_prompts_through_the_trigger_targets():
    from rules_port import interpreted_prompts, ir_exec
    from rules_port.context import EffectContext

    assert ir_exec.engine_runs(HAND_PARENT, 900, 5), "a single hand pick is no longer routed to the native path"
    db, pl_t, ai_t, game, handler, bstate = _choosing_battle()
    for uid in (0x301, 0x302):
        H.add_card(db, uid, H.TROOP_OWNER, H.scenarios.TPL_GLADIATOR, loc="hand")
    db.commit()
    context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
    try:
        ir_exec.run_ability(HAND_CHILD, None, context=context, source_uid=900, owner_uid=5, event={"player": 5})
    except ir_exec.NeedsChoice as pause:
        assert pause.authored_zone == "hand"
        assert pause.maximum == 1, "only a single-card hand pick is wired"
        assert pause.candidates, "the pause names the cards in hand"
        shown = {}

        def prompt_trigger_targets(game, pl_t, ai_t, session, bstate, source_uid, ability_guid, template_ids,
                                   candidates, **kwargs):
            shown.update(ability_guid=ability_guid, candidates=candidates, **kwargs)

        handler._prompt_trigger_targets = prompt_trigger_targets
        assert interpreted_prompts.prompt_interpreted_reveal(handler, game, SessionStub(), db, pl_t, ai_t, bstate,
                                                    pause) == "waiting"
        assert shown["ability_guid"] == HAND_CHILD
        assert shown["candidates"] == [list(pause.candidates)]
        assert shown["interpreter"] == pause.continuation
    else:
        raise AssertionError("the hand pick did not pause for the player")


# A parent whose child picks two cards from the player's hand (a constant maximum of 2).
MULTI_PARENT = "df41fd00-65c3-c619-d105-ed49e4961026"
MULTI_CHILD = "2f1a7aca-a70c-ab25-faca-84ac4047de5c"


@isolated_db
def test_multi_card_hand_pick_takes_a_set_answer():
    from rules_port import interpreted_prompts
    from rules_port import ir_exec, picks, resolution
    from rules_port.context import EffectContext

    assert picks.hand_answer([0x301, 0x302], [0x301, 0x302, 0x303], minimum=2, maximum=2, optional=False) == \
        ([0x301, 0x302], True)
    assert picks.hand_answer([0x301], [0x301, 0x302], minimum=2, maximum=2, optional=False)[1] is False
    assert ir_exec.engine_runs(MULTI_PARENT, 900, 5), "a constant-maximum hand pick is no longer routed to the native path"
    db, pl_t, ai_t, game, handler, bstate = _choosing_battle()
    for uid in (0x301, 0x302, 0x303):
        H.add_card(db, uid, H.TROOP_OWNER, H.scenarios.TPL_GLADIATOR, loc="hand")
    db.commit()
    context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
    try:
        ir_exec.run_ability(MULTI_CHILD, None, context=context, source_uid=900, owner_uid=5, event={"player": 5})
    except ir_exec.NeedsChoice as pause:
        assert pause.authored_zone == "hand"
        assert pause.maximum == 2, "the pause asks for the authored number of cards"
        shown = {}

        def prompt_trigger_targets(game, pl_t, ai_t, session, bstate, source_uid, ability_guid, template_ids,
                                   candidates, **kwargs):
            shown.update(candidates=candidates, **kwargs)

        handler._prompt_trigger_targets = prompt_trigger_targets
        assert interpreted_prompts.prompt_interpreted_reveal(handler, game, SessionStub(), db, pl_t, ai_t, bstate,
                                                    pause) == "waiting"
        assert shown["interpreter"] == pause.continuation
        # The interpreter takes the set of chosen cards as its answer.
        context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
        ir_exec.resume_ability(pause.continuation, [0x301, 0x302], context=context)
    else:
        raise AssertionError("the multi-card hand pick did not pause for the player")


# A parent whose child picks a troop from the opponent's discard pile (IsNotControlledBy), so the cards are the AI's.
DISCARD_PARENT = "32f42e74-0318-bf6d-2949-6e6a7c54562b"
DISCARD_CHILD = "60f051ff-fcab-9166-9ab8-b16383ef3300"


@isolated_db
def test_discard_pick_is_run_by_the_interpreter_and_prompts_through_the_zone_picker():
    from rules_port import interpreted_prompts, ir_exec
    from rules_port.context import EffectContext

    assert ir_exec.engine_runs(DISCARD_PARENT, 900, 5), "a discard pick is no longer routed to the native path"
    db, pl_t, ai_t, game, handler, bstate = _choosing_battle()
    for uid in (0x401, 0x402):
        H.add_card(db, uid, H.OPPOSING_OWNER, H.scenarios.TPL_GLADIATOR, loc="discard")
    db.commit()
    context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
    try:
        ir_exec.run_ability(DISCARD_CHILD, None, context=context, source_uid=900, owner_uid=5, event={"player": 5})
    except ir_exec.NeedsChoice as pause:
        assert pause.authored_zone == "discard"
        assert pause.candidates, "the pause names the cards in the discard pile"
        shown = {}

        def prompt_trigger_targets(game, pl_t, ai_t, session, bstate, source_uid, ability_guid, template_ids,
                                   candidates, **kwargs):
            shown.update(candidates=candidates, **kwargs)

        handler._prompt_trigger_targets = prompt_trigger_targets
        assert interpreted_prompts.prompt_interpreted_reveal(handler, game, SessionStub(), db, pl_t, ai_t, bstate,
                                                    pause) == "waiting"
        assert shown["interpreter"] == pause.continuation
        assert shown["candidates"] == [list(pause.candidates)]
    else:
        raise AssertionError("the discard pick did not pause for the player")


# A warzone troop pick (the caster sacrifices one of its own troops): its filter names no InZone, so the zone is the one its collection flags name (checked against the
# native picker by the census in docs/IR_RETIREMENT_LIST.md).
WARZONE_PARENT = "639deae6-1a56-dbc1-1247-db64aeb587a6"
WARZONE_CHILD = "83fc0da9-caad-71c5-751a-007ff3a23db1"


@isolated_db
def test_warzone_troop_pick_is_run_by_the_interpreter_and_prompts_through_the_zone_picker():
    from rules_port import interpreted_prompts, ir_exec
    from rules_port.context import EffectContext

    assert ir_exec.engine_runs(WARZONE_PARENT, 900, 5), "a warzone troop pick is no longer routed to the native path"
    db, pl_t, ai_t, game, handler, bstate = _choosing_battle()
    for uid in range(0x501, 0x507):
        H.add_card(db, uid, H.TROOP_OWNER, H.scenarios.TPL_GLADIATOR, loc="warzone")
    db.commit()
    context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
    try:
        ir_exec.run_ability(WARZONE_CHILD, None, context=context, source_uid=900, owner_uid=5, event={"player": 5})
    except ir_exec.NeedsChoice as pause:
        assert pause.authored_zone == "warzone"
        assert len(pause.candidates) > 1
        shown = {}

        def prompt_trigger_targets(game, pl_t, ai_t, session, bstate, source_uid, ability_guid, template_ids,
                                   candidates, **kwargs):
            shown.update(candidates=candidates, **kwargs)

        handler._prompt_trigger_targets = prompt_trigger_targets
        assert interpreted_prompts.prompt_interpreted_reveal(handler, game, SessionStub(), db, pl_t, ai_t, bstate,
                                                    pause) == "waiting"
        assert shown["interpreter"] == pause.continuation
    else:
        raise AssertionError("the warzone troop pick did not pause for the player")


# A parent whose deck pick compares the card's attribute to its cost (IntAttrFilter with m_CompareToCost). The interpreter
# reads the printed cost, as the native filter does, so the two pick the same cards.
COST_COMPARE_PARENT = "00d452f1-0a56-1045-751f-11278a7840c8"
COST_COMPARE_CHILD = "f0fc538d-7bff-87af-cadb-a203e5ddec74"


@isolated_db
def test_cost_comparing_deck_pick_reads_the_current_cost_from_game_cards():
    from rules_port import ir_exec, targeting
    from rules_port.context import EffectContext
    from rules_port.ir_exec import _guid, _load

    assert not ir_exec.coverage(COST_COMPARE_PARENT), "a cost-comparing filter is covered by the interpreter"
    assert ir_exec.engine_runs(COST_COMPARE_PARENT, 900, 5)
    db, pl_t, ai_t, game, handler, bstate = _choosing_battle()
    CASTER_DECK_CARD = 0x651  # the caster's first deck card (owner 5 offset in the loop below)
    for owner in (H.TROOP_OWNER, H.OPPOSING_OWNER):
        for uid in range(0x601 + owner * 16, 0x603 + owner * 16):
            H.add_card(db, uid, owner, H.scenarios.TPL_GLADIATOR, loc="deck")
    db.commit()
    root = _load("AbilityTemplate", COST_COMPARE_CHILD)
    target = str(_guid(root.m_AbilityTargetTemplateIds[0])).lower()
    context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")

    def candidates():
        scope = ir_exec.Scope(context=context, source_uid=900, owner_uid=H.TROOP_OWNER, table=root.version_table,
                              event={"player": H.TROOP_OWNER}, root_ability=COST_COMPARE_CHILD, instance_id=None)
        return {int(uid) for uid in ir_exec._choice_candidates(root, 0, scope)[0]}

    # The gladiator's current cost is 2 (game_cards has no modifier). It has no damage dealt, so it fails
    # "damage >= cost"; it would pass against the authored 0. Native and interpreter both exclude it.
    assert ir_exec._effective_cost(context, CASTER_DECK_CARD, H.scenarios.TPL_GLADIATOR) == 2
    assert candidates() == {int(uid) for uid in targeting.legal_targets(
        db, 1, H.TROOP_OWNER, target, 900, both_players=True, champions=handler._champion_targets(),
        battle_state=bstate)} == set()
    # A cost change in game_cards is what the filter reads: with a -2 modifier the card costs 0, so it passes.
    db.execute("UPDATE game_cards SET card_cost_mod=-2 WHERE card_uid=?", (CASTER_DECK_CARD,))
    db.commit()
    assert ir_exec._effective_cost(context, CASTER_DECK_CARD, H.scenarios.TPL_GLADIATOR) == 0
    assert candidates() == {CASTER_DECK_CARD}
    # A card a template makes but that has no game_cards row yet costs its template's cost.
    assert ir_exec._effective_cost(context, 0x999, H.scenarios.TPL_GLADIATOR) == 2


# An ability that invokes a random Lucky pool ability (its authored invoke is null). Its gate used to raise KeyError on the
# null child; it now checks the pool, and the interpreter runs it.
RANDOM_INVOKE_NULL_CHILD = "9b36aabc-dffc-edf3-eaf9-cd514adf3d6a"


def test_random_invoke_with_a_null_authored_child_runs_in_the_interpreter():
    from rules_port import ir_exec

    assert ir_exec.coverage(RANDOM_INVOKE_NULL_CHILD) == []
    assert ir_exec.engine_runs(RANDOM_INVOKE_NULL_CHILD, 900, 5)
