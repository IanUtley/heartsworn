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
