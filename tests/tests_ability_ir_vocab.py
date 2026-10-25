"""The IR vocabulary is read from the Records, and completion finds a node kind and its fields."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign_editor"))

import ability_ir_vocab as vocab


class VocabularyTest(unittest.TestCase):
    def test_completion_finds_a_node_kind_and_its_fields(self):
        found = vocab.completions("counter")
        self.assertIn({"text": "source_card_has_counters", "type": "node",
                       "class": "Game.Shared.Mechanics.Abilities.Conditions.SourceCardHasCounters",
                       "layer": "node"}, found)
        self.assertTrue(any(item["type"] == "field" and item["text"] == "required_counters" for item in found))


if __name__ == "__main__":
    unittest.main()
