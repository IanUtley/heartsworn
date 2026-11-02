"""Blueprint card text: Inquisition parses to its steps, and the name checks refuse what would not run."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pytest  # noqa: E402

from gamedata import ir_blueprint as bp  # noqa: E402

INQUISITION = """\
Inquisition [BasicAction, cost 2]
  Target  <- OpposingChampion
  Look    : Reveal(Target.Hand)                  -> Revealed
  Choose  : Choose(Revealed, NonResource)        -> Pick
  Discard : Discard(Pick, Owner=Target)
"""


def test_blueprint_parses_inquisition_and_refuses_bad_names():
    card = bp.parse(INQUISITION)
    assert (card.name, card.card_type, card.cost) == ("Inquisition", "BasicAction", 2)
    assert [type(s).__name__ for s in card.steps] == ["Binding", "Step", "Step", "Step"]
    assert card.steps[2].args == ["Revealed", "NonResource"] and card.steps[2].out == "Pick"
    assert card.steps[3].args == ["Pick", {"key": "Owner", "value": "Target"}]

    used_before = INQUISITION.replace("Choose(Revealed, NonResource)", "Choose(Pick, NonResource)")
    with pytest.raises(bp.BlueprintError, match="used before"):
        bp.parse(used_before)

    unused = INQUISITION.replace("Discard(Pick, Owner=Target)", "Discard(Target, Owner=Target)")
    with pytest.raises(bp.BlueprintError, match="never used"):
        bp.parse(unused)


def test_every_inquisition_action_has_a_lowering_entry():
    card = bp.parse(INQUISITION)
    actions = {s.action for s in card.steps if isinstance(s, bp.Step)}
    assert actions <= set(bp.ACTIONS), actions - set(bp.ACTIONS)
    assert "local ability" in bp.ACTIONS["Choose"]["lowers_to"]


def test_non_resource_helper_round_trips_through_the_ir_text():
    from gamedata import ir_python, ir_surface
    text = ir_python.dumps(ir_surface.non_resource())
    assert text == ("NotCardFilter(target_filter=OrCardFilter(target_filters="
                    "[IsResource(is_basic_resource=False, threshold_color_flags='Any')]))")
    names = ir_python.loads("filter_ = " + text)
    assert ir_python.dumps(names["filter_"]) == text


def test_move_card_keeps_its_owner_for_the_clause_target():
    from gamedata import ir_surface
    move = ir_surface.move_card("They discard that card.", id="00000000-0000-0000-0000-00000000d15c",
                                zone="Discard", owner="Target")
    assert move._surface_owner == "Target"
    assert str(move.m_DestinationCollection) == "Discard"
    plain = ir_surface.move_card("Discard it.", id="00000000-0000-0000-0000-00000000d15d", zone="Discard")
    assert plain._surface_owner is None


def test_choose_revealed_is_a_local_ability_invoked_by_activate():
    from gamedata import ir_surface
    discard = ir_surface.move_card("They discard that card.", id="00000000-0000-0000-0000-00000000d1a1", zone="Discard")
    choose = ir_surface.choose_revealed(
        "Choose a revealed non-resource card. They discard that card.",
        id="00000000-0000-0000-0000-0000000c0a01", target_id="00000000-0000-0000-0000-0000000c0a02",
        card_filter=ir_surface.non_resource(), does=[discard])
    assert choose._surface_guid == "00000000-0000-0000-0000-0000000c0a01"
    assert choose._surface_targets[0].m_CardFilter is not None
    invoke = ir_surface.activate("You invoke it.", id="00000000-0000-0000-0000-0000000c0a03", ability=choose)
    assert invoke._surface_invokes is choose
    assert str(invoke.m_AbilityToInvoke["m_Guid"]) == "00000000-0000-0000-0000-0000000c0a01"


def test_lowering_inquisition_matches_the_records_effects_and_target_order():
    from gamedata import ir_blueprint as bp2
    lowered = bp2.lower(bp.parse(INQUISITION))
    refs = [c["m_EffectTemplateId"]["m_Guid"] for c in lowered.clauses]
    assert refs[0] == "e9aea9b3-e553-22fe-f985-45934d2d8907"     # store the opposing champion (Records clause 1)
    assert refs[1] == "ceb72f0c-d606-0ac4-fbe3-08816f78bc9b"     # reveal the hand (Records clause 2)
    assert [c["m_TargetTemplateIndex"] for c in lowered.clauses] == [0, 1, 2]
    assert lowered.local._surface_targets[0].m_CardFilter is not None
    assert lowered.invoke._surface_invokes is lowered.local


def test_emitted_inquisition_text_loads_back_to_the_same_objects():
    from gamedata import ir_blueprint as bp2
    from gamedata import ir_python
    text = bp2.emit(bp.parse(INQUISITION), "Look at target opposing champion's hand.")
    names = ir_python.loads(text)
    assert {"CHOOSE", "CHOOSE_TARGET_0", "CHOOSE_DOES_0", "INVOKE", "ROOT"} <= set(names)
    assert ir_python.dumps(ir_python.loads(text)["ROOT"]) == ir_python.dumps(names["ROOT"])
    assert "[rule text not in the Blueprint yet]" in text
