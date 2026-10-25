""""Your actions in all zones cost N more this turn": the DSL writes it as Records writes its own
all-zones cost (a CardCostModifier on the actions you control in every zone, lasting this turn)."""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path[:0] = [ROOT, os.path.join(ROOT, "campaign"), os.path.join(ROOT, "campaign_editor")]

import ability_dsl as dsl  # noqa: E402

SENTENCE = ("At the start of each champion's draw phase, you may draw a card.\n"
            "If you do, your actions in all zones cost 2 more this turn.")


def test_all_zone_action_cost_compiles_to_a_turn_long_cost_and_reads_back():
    compiled = dsl.compile_text(SENTENCE)
    cost = compiled["root"]["m_AbilityEffectList"][1]
    assert cost["m_EffectDuration"] == "EndOfTurn" and cost["m_ContingentEffectInstanceId"] == 0
    target = dsl._target_content("your actions in all zones")
    assert target["m_CollectionFlags"] == dsl._ALL_ZONES
    assert "BasicAction|QuickAction" in str(target["m_CardFilter"])
