"""The surface writer: a sample of stored abilities is written as surface text and read back to the same Records,
and text that does not bind one ability is refused."""

import unittest

from gamedata import DEFAULT_RECORD_STORE as STORE
from gamedata import ir_python as P
from gamedata import ir_registry
from gamedata import ir_surface as surface
from gamedata import ir_surface_write as W


class SurfaceWriteTest(unittest.TestCase):
    def test_sampled_abilities_round_trip_through_surface_text(self):
        guids = [record.guid.lower() for record in STORE.load("AbilityTemplate")][::300]
        self.assertGreater(len(guids), 10)
        for guid in guids:
            self.assertEqual(W.round_trip_problems(guid), [], guid)

    def test_an_ability_is_written_without_its_empty_targets(self):
        text = W.ability_text("1068f0c2-b21d-e7d8-747b-6a213600a543")
        self.assertNotIn("discard_target", text)
        self.assertNotIn("NoRef()", text)
        self.assertIn("ability_effect_list=[Clause(", text)

    def test_a_written_ability_has_no_boilerplate_it_does_not_store(self):
        text = W.ability_text("1068f0c2-b21d-e7d8-747b-6a213600a543")
        self.assertNotIn("m_AICustomAbilityEvaluator", text)

    def test_simple_checks_build_the_stored_filters(self):
        cost = surface.cost_is(3)
        self.assertEqual(cost.to_record()["m_CastingCost"], 3)
        self.assertEqual(surface.attribute_is("Soulcursed", 2).to_record()["m_Attribute"], "Soulcursed")
        self.assertEqual(surface.attribute_is("Soulcursed", 1).to_record()["m_Value"], 1)

    def test_shared_effect_names_read_back_to_their_records(self):
        ir_registry.clear()
        names = P.loads('ABILITY = ability("Draw a card.", ability_effect_list=[Clause(effect_template_id=DRAW_A_CARD)])')
        clause = names["ABILITY"].to_record()["m_AbilityEffectList"][0]
        self.assertEqual(clause["m_EffectTemplateId"]["m_Guid"], surface.SHARED_EFFECTS["DRAW_A_CARD"])

    def test_text_that_binds_no_ability_is_refused(self):
        with self.assertRaises(ValueError):
            W.record_for("X = 1")
        with self.assertRaises(ValueError):
            W.record_for("import os")


if __name__ == "__main__":
    unittest.main()
