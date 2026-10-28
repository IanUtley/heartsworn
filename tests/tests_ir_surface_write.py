"""The surface writer: a sample of stored abilities is written as surface text and read back to the same Records,
and text that does not bind one ability is refused."""

import unittest

from gamedata import DEFAULT_RECORD_STORE as STORE
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

    def test_text_that_binds_no_ability_is_refused(self):
        with self.assertRaises(ValueError):
            W.record_for("X = 1")
        with self.assertRaises(ValueError):
            W.record_for("import os")


if __name__ == "__main__":
    unittest.main()
