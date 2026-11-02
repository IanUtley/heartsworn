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
