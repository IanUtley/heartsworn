"""The stored and follow-up target helpers of the IR interpreter (gap area B)."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from rules_port import ir_exec_targets  # noqa: E402

TARGET_CONSTANT = "Game.Shared.Mechanics.Abilities.TargetTemplates.TargetConstant"


def test_referenced_instances_are_the_secondary_indexes_of_the_clauses():
    root = SimpleNamespace(m_AbilityEffectList=[
        {"m_SecondaryTargetIndex": 2}, {"m_SecondaryTargetIndex": -1}, SimpleNamespace(m_SecondaryTargetIndex=5),
        {},
    ])
    assert ir_exec_targets.referenced_instances(root) == {2, 5}


def test_follow_up_gaps_allow_a_chosen_secondary_target_and_refuse_a_variable_count():
    # A "one of them" pick is prompted for by the interpreter, so it is covered; a chosen match target is not.
    chosen = {"m_IsAutoTarget": 0}
    assert ir_exec_targets.follow_up_gaps(chosen, "SecondaryTargetTemplate") == []
    assert ir_exec_targets.follow_up_gaps(chosen, "MatchSecondaryTargetTemplate") == ["chosen follow-up target"]
    automatic = {"m_IsAutoTarget": 1, "m_MaxTargetCount": {"_t": TARGET_CONSTANT, "m_Value": 1}}
    assert ir_exec_targets.follow_up_gaps(automatic, "MatchSecondaryTargetTemplate") == []
    variable = {"m_IsAutoTarget": 1, "m_MaxTargetCount": {"_t": "Game.Shared.Mechanics.Abilities.TargetTemplates.TargetVariable"}}
    assert ir_exec_targets.follow_up_gaps(variable, "MatchSecondaryTargetTemplate") == ["target MaxTargetCount"]
    # A list target takes a constant count, as the native resolver caps it; a variable count is still refused.
    assert ir_exec_targets.follow_up_gaps(automatic, "SourceBuriedTargetTemplate") == []
    assert ir_exec_targets.follow_up_gaps(variable, "SourceBuriedTargetTemplate") == ["target MaxTargetCount"]


def test_follow_up_gaps_run_the_trigger_target_selector():
    # TriggerTarget is a supported selector (TRIGGER_SELECTORS): the card the event's other side names is wired.
    template = {"m_IsAutoTarget": 1, "m_TriggerSelector": "TriggerTarget"}
    assert ir_exec_targets.follow_up_gaps(template, "AbilityTriggerCardTargetTemplate") == []
    assert ir_exec_targets.follow_up_gaps({"m_TriggerSelector": "TriggerSource"},
                                          "AbilityTriggerCardTargetTemplate") == []


def test_constant_count_reads_a_target_constant_only():
    template = {"m_MaxTargetCount": {"_t": TARGET_CONSTANT, "m_Value": 3}}
    assert ir_exec_targets.constant_count(template, "m_MaxTargetCount") == 3
    assert ir_exec_targets.constant_count(template, "m_MinTargetCount") == 0


def test_trigger_card_is_stored_when_some_ability_stored_the_moved_card():
    context = SimpleNamespace(bstate={"stored_targets": {"guid-a": [7], "guid-b": [9]}})
    assert ir_exec_targets.trigger_card_is_stored(SimpleNamespace(context=context, event={"card": 7}))
    assert not ir_exec_targets.trigger_card_is_stored(SimpleNamespace(context=context, event={"card": 8}))
    assert not ir_exec_targets.trigger_card_is_stored(SimpleNamespace(context=context, event={}))


class _StoreContext:
    """Records the store leaf's calls, with the template and the ability it saw."""

    def __init__(self):
        self.bstate = {"resolving_ability": "outer-guid"}
        self.effect_template_override = None
        self.effect_targets = ()
        self.calls = []

    def store_target(self, target=None):
        self.calls.append((int(target), self.effect_template_override, self.bstate["resolving_ability"]))


def test_store_targets_stores_each_target_for_this_ability_and_restores_the_state():
    context = _StoreContext()
    scope = SimpleNamespace(context=context)
    effect_raw = {"m_OnlyUntilEndOfTurn": False}
    ir_exec_targets.store_targets(scope, "root-guid", effect_raw, [4, 6])
    assert context.calls == [(4, effect_raw, "root-guid"), (6, effect_raw, "root-guid")]
    assert context.bstate["resolving_ability"] == "outer-guid"
    assert context.effect_template_override is None


if __name__ == "__main__":
    # Written in pytest style: run through pytest so each test executes and failures
    # set the exit status. A missing pytest fails loudly.
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
