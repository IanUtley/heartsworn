"""Champion templates round-trip, including the records the strict Records reader rejects."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign_editor"))

import ability_ir_champions as champions
import records_reader


class ChampionTemplateTest(unittest.TestCase):
    def test_every_champion_round_trips(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hex-private", "Records",
                            "ChampionTemplate.jsonl")
        records = records_reader.records(path)
        self.assertEqual(len(records), 663)
        for record in records:
            self.assertEqual(champions.to_record(champions.from_record(record)), record)


if __name__ == "__main__":
    unittest.main()
