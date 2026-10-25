"""Inlining an ActivateAbility child: its targets, clauses and instance ids move into the parent."""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign_editor"))

import ability_inline as inl

ACTIVATE_GUID = "a0000000-0000-0000-0000-000000000001"
DAMAGE_GUID = "a0000000-0000-0000-0000-000000000002"
CHILD_GUID = "c0000000-0000-0000-0000-000000000001"
SET_GUID = "a0000000-0000-0000-0000-000000000003"
COND_GUID = "d0000000-0000-0000-0000-000000000001"


def _effects():
    return {
        ACTIVATE_GUID: {"_t": "Game.Shared.Mechanics.Abilities.ActivateAbilityEffectTemplate",
                        "m_AbilityToInvoke": {"m_Guid": CHILD_GUID}},
        DAMAGE_GUID: {"_t": "Game.Shared.Mechanics.Abilities.DamageAbilityEffectTemplate"},
    }


def _entry(guid, target, instance, group=1, contingent=-1):
    return {"m_EffectTemplateId": {"m_Guid": guid}, "m_TargetTemplateIndex": target,
            "m_EffectInstanceId": instance, "m_EffectGroupId": group,
            "m_ContingentEffectInstanceId": contingent, "m_RecalculateTargets": "UseDefault"}


class InlineChildTest(unittest.TestCase):
    def test_child_clauses_join_the_parent_with_shifted_targets_and_ids(self):
        parent = {"m_AbilityTargetTemplateIds": [{"m_Guid": "p0"}],
                  "m_Variables": [{"_t": "x.AbilityVariable", "m_Name": "Mine"}],
                  "m_AbilityEffectList": [_entry(DAMAGE_GUID, 0, 0),
                                          _entry(ACTIVATE_GUID, 0, 1, group=2)]}
        child = {"m_AbilityTargetTemplateIds": [{"m_Guid": "c0"}],
                 "m_Variables": [{"_t": "x.AbilityVariable", "m_Name": "Theirs"}],
                 "m_AbilityEffectList": [_entry(DAMAGE_GUID, 0, 0, group=1),
                                         _entry(DAMAGE_GUID, 0, 1, group=1, contingent=0)]}
        effects = _effects()
        inlined, report = inl.inline_children(
            parent, lambda guid: effects.get(guid, {}), lambda guid: child if guid == CHILD_GUID else None,
            lambda guid: {"m_IsAutoTarget": 1}, lambda guid: None)

        self.assertEqual(report, [])
        self.assertEqual([t["m_Guid"] for t in inlined["m_AbilityTargetTemplateIds"]], ["p0", "c0"])
        self.assertEqual([v["m_Name"] for v in inlined["m_Variables"]], ["Mine", "Theirs"])
        clauses = inlined["m_AbilityEffectList"]
        self.assertEqual(len(clauses), 3)
        # The child's clauses point at the child's target, now at index 1.
        self.assertEqual([c["m_TargetTemplateIndex"] for c in clauses], [0, 1, 1])
        # Instance ids follow clause order, and the child's contingency follows its clause.
        self.assertEqual([c["m_EffectInstanceId"] for c in clauses], [0, 1, 2])
        self.assertEqual(clauses[2]["m_ContingentEffectInstanceId"], 1)
        # Auto targets of inlined clauses regenerate before they run, as the child's did.
        self.assertTrue(all(c["m_RecalculateTargets"] == "True" for c in clauses[1:]))

    def test_variable_test_condition_is_copied_only_when_the_child_leaves_its_variable_alone(self):
        # The invoking clause runs only when Mine <= 1. The client checks that once; an inlined
        # clause checks it when it runs, so the child must not change Mine.
        condition = {"m_Condition": {"_t": "Game.Shared.Mechanics.Abilities.Conditions.AbilityVariableCondition",
                                     "m_Lhs": "Mine", "m_Rhs": 1, "m_ComparisonOp": "LessThanOrEqual"}}
        conditions = {COND_GUID: condition}
        parent = {"m_AbilityTargetTemplateIds": [{"m_Guid": "p0"}],
                  "m_Variables": [{"_t": "x.AbilityVariable", "m_Name": "Mine"}],
                  "m_AbilityEffectList": [_entry(ACTIVATE_GUID, 0, 0)]}
        parent["m_AbilityEffectList"][0]["m_ConditionId"] = {"m_Guid": COND_GUID}
        child = {"m_AbilityTargetTemplateIds": [{"m_Guid": "c0"}], "m_Variables": [],
                 "m_AbilityEffectList": [_entry(DAMAGE_GUID, 0, 0)]}

        def run(child_effect_guid):
            effects = _effects()
            effects[SET_GUID] = {"_t": "Game.Shared.Mechanics.Abilities.SetConstantValueVariableEffectTemplate",
                                 "m_VariableName": "Mine", "m_Value": 0}
            child["m_AbilityEffectList"][0]["m_EffectTemplateId"] = {"m_Guid": child_effect_guid}
            return inl.inline_children(
                parent, lambda guid: effects.get(guid, {}),
                lambda guid: child if guid == CHILD_GUID else None,
                lambda guid: {"m_IsAutoTarget": 1}, lambda guid: conditions.get(guid))

        # Child leaves Mine alone: the clause is inlined and every inlined clause carries the condition.
        inlined, report = run(DAMAGE_GUID)
        self.assertEqual(report, [])
        self.assertEqual(inlined["m_AbilityEffectList"][0]["m_ConditionId"], {"m_Guid": COND_GUID})

        # Child sets Mine: the invoking clause stays an ActivateAbility, with the reason reported.
        inlined, report = run(SET_GUID)
        self.assertEqual(report, ["child changes the variable its condition tests"])
        self.assertEqual(inlined["m_AbilityEffectList"][0]["m_EffectTemplateId"], {"m_Guid": ACTIVATE_GUID})


if __name__ == "__main__":
    unittest.main()
