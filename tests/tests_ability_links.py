"""Child links: a declared binding is checked against both abilities' inputs."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign_editor"))

import ability_ir_link as ln

ACTIVATE_GUID = "a0000000-0000-0000-0000-000000000001"
DAMAGE_GUID = "a0000000-0000-0000-0000-000000000002"
CHILD_GUID = "c0000000-0000-0000-0000-000000000001"


class ChildLinkTest(unittest.TestCase):
    def test_binding_must_name_an_input_on_each_side(self):
        effects = {
            ACTIVATE_GUID: {"_t": "Game.Shared.Mechanics.Abilities.ActivateAbilityEffectTemplate",
                            "m_AbilityToInvoke": {"m_Guid": CHILD_GUID}},
            DAMAGE_GUID: {"_t": "Game.Shared.Mechanics.Abilities.DamageAbilityEffectTemplate"},
        }
        child = {"m_AbilityTargetTemplateIds": [{"m_Guid": "c0"}],
                 "m_Variables": [{"_t": "x.AbilityVariable", "m_Name": "C1I1"}],
                 "m_AbilityEffectList": [{"m_EffectTemplateId": {"m_Guid": DAMAGE_GUID}, "m_TargetTemplateIndex": 0,
                                          "m_EffectInstanceId": 0, "m_EffectGroupId": 1,
                                          "m_ContingentEffectInstanceId": -1}]}
        parent = {"m_AbilityTargetTemplateIds": [{"m_Guid": "p0"}],
                  "m_Variables": [{"_t": "x.AbilityVariable", "m_Name": "I1"}],
                  "m_AbilityEffectList": [{"m_EffectTemplateId": {"m_Guid": ACTIVATE_GUID},
                                           "m_TargetTemplateIndex": 0, "m_EffectInstanceId": 0,
                                           "m_EffectGroupId": 1, "m_ContingentEffectInstanceId": -1}]}
        effect_of = lambda guid: effects.get(guid, {})
        root_of = lambda guid: child if guid == CHILD_GUID else None
        target_of = lambda guid: {"m_IsAutoTarget": 1}
        condition_of = lambda guid: None

        (link,) = ln.links_of(parent, effect_of)
        self.assertEqual(link.child, CHILD_GUID)
        self.assertEqual(link.bindings, ())
        self.assertEqual(ln.check(link, parent, effect_of, root_of, target_of, condition_of), [])
        self.assertEqual(ln.to_entry(link), parent["m_AbilityEffectList"][0])

        # C1I1 = $I1 is valid: the child has C1I1 and the parent has I1. The sentence form reads back.
        link.bindings = (ln.Binding("C1I1", "I1"),)
        self.assertEqual(ln.binding_text(link.bindings[0]), "C1I1 = $I1")
        self.assertEqual(ln.parse_binding("C1I1 = $I1"), link.bindings[0])
        with self.assertRaises(ValueError):
            ln.parse_binding("C1I1 = I1")
        self.assertEqual(ln.check(link, parent, effect_of, root_of, target_of, condition_of), [])

        # A name missing on either side is reported, one problem each.
        link.bindings = (ln.Binding("C1I1", "Nope"), ln.Binding("Nope", "I1"))
        problems = ln.check(link, parent, effect_of, root_of, target_of, condition_of)
        self.assertEqual(problems, ["binding C1I1 = $Nope: the parent has no input Nope",
                                    "binding Nope = $I1: the child has no input Nope"])


    def test_binding_compiles_to_a_read_through_alias_or_fails_the_whole_compile(self):
        set_guid = "a0000000-0000-0000-0000-000000000003"
        effects = {
            ACTIVATE_GUID: {"_t": "Game.Shared.Mechanics.Abilities.ActivateAbilityEffectTemplate",
                            "m_AbilityToInvoke": {"m_Guid": CHILD_GUID}},
            DAMAGE_GUID: {"_t": "Game.Shared.Mechanics.Abilities.DamageAbilityEffectTemplate"},
            set_guid: {"_t": "Game.Shared.Mechanics.Abilities.SetConstantValueVariableEffectTemplate",
                       "m_VariableName": "C1I1", "m_Value": 0},
        }
        effect_of = lambda guid: effects.get(guid, {})
        target_of = lambda guid: {"m_IsAutoTarget": 1}
        condition_of = lambda guid: None

        def child_with(effect_guid):
            return {"m_AbilityTargetTemplateIds": [{"m_Guid": "c0"}],
                    "m_Variables": [{"_t": "x.AbilityVariable", "m_Name": "C1I1"}],
                    "m_AbilityEffectList": [{"m_EffectTemplateId": {"m_Guid": effect_guid},
                                             "m_TargetTemplateIndex": 0, "m_EffectInstanceId": 0,
                                             "m_EffectGroupId": 1, "m_ContingentEffectInstanceId": -1}]}

        parent = {"m_AbilityTargetTemplateIds": [{"m_Guid": "p0"}],
                  "m_Variables": [{"_t": "x.AbilityVariable", "m_Name": "I1"}],
                  "m_AbilityEffectList": [{"m_EffectTemplateId": {"m_Guid": ACTIVATE_GUID},
                                           "m_TargetTemplateIndex": 0, "m_EffectInstanceId": 0,
                                           "m_EffectGroupId": 1, "m_ContingentEffectInstanceId": -1}]}
        (link,) = ln.links_of(parent, effect_of)
        link.bindings = (ln.Binding("C1I1", "I1"),)

        # The child only reads C1I1: the clause is inlined and C1I1 reads the parent's I1.
        child = child_with(DAMAGE_GUID)
        compiled, problems = ln.compile_root(parent, [link], effect_of,
                                             lambda guid: child if guid == CHILD_GUID else None,
                                             target_of, condition_of)
        self.assertEqual(problems, [])
        self.assertEqual([v["m_Name"] for v in compiled["m_Variables"]], ["I1", "C1I1"])
        self.assertEqual(compiled["m_Variables"][1]["m_ExpressionText"], "I1")
        self.assertEqual(compiled["m_AbilityEffectList"][0]["m_EffectTemplateId"], {"m_Guid": DAMAGE_GUID})

        # A bound name the client reads as an attribute (DamageDealt) is refused, not compiled.
        link.bindings = (ln.Binding("C1I1", "DamageDealt"),)
        child = child_with(DAMAGE_GUID)
        compiled, problems = ln.compile_root(parent, [link], effect_of,
                                             lambda guid: child if guid == CHILD_GUID else None,
                                             target_of, condition_of)
        self.assertIsNone(compiled)
        self.assertTrue(any("DamageDealt" in problem for problem in problems))
        link.bindings = (ln.Binding("C1I1", "I1"),)

        # The child sets C1I1: an alias cannot be set, so nothing is compiled.
        child = child_with(set_guid)
        compiled, problems = ln.compile_root(parent, [link], effect_of,
                                             lambda guid: child if guid == CHILD_GUID else None,
                                             target_of, condition_of)
        self.assertIsNone(compiled)
        self.assertEqual(problems, ["child changes C1I1, which a binding can only read"])


if __name__ == "__main__":
    unittest.main()
