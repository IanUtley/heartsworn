"""Card counter templates round-trip, and a counter reference in an effect reads as a Ref."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign_editor"))

import ability_ir_clauses as irc
import ability_ir_counters as cc
import ability_ir_effects as ef
from gamedata.records import deserialize_line


class CounterTemplateTest(unittest.TestCase):
    def test_counter_records_round_trip_and_counter_references_are_refs(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hex-private", "Records",
                            "CardCounterTemplate.jsonl")
        decoded = 0
        for line in open(path, encoding="utf-8"):
            try:
                record = deserialize_line(line).to_dict()
            except Exception:
                continue   # the file's header line
            decoded += 1
            self.assertEqual(cc.to_record(cc.from_record(record)), record)
        self.assertEqual(decoded, 135)

        # A modifier that adds a counter names it by a Ref to its CardCounterTemplate.
        modifier = {"_t": "Game.Shared.Mechanics.Modifiers.CounterModifier",
                    "m_CardCounterTemplateId": {"m_Guid": "dc4ee67d-a2e2-e1e3-02ce-7587d6c8984e"}}
        node = ef.node_from_record(modifier)
        self.assertIsInstance(node.fields["card_counter_template_id"], irc.Ref)
        self.assertEqual(ef.node_to_record(node), modifier)


if __name__ == "__main__":
    unittest.main()
