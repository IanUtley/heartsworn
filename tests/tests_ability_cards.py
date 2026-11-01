"""Card templates round-trip, with each card's abilities read as references to ability roots."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign_editor"))

import ability_ir_cards as cards
import ability_ir_clauses as irc
from gamedata.records import deserialize_line


class CardTemplateTest(unittest.TestCase):
    def test_cards_round_trip_and_abilities_are_refs(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hex-private", "Records",
                            "CardTemplate.jsonl")
        decoded = abilities = 0
        for line in open(path, encoding="utf-8"):
            try:
                record = deserialize_line(line).to_dict()
            except Exception:
                continue   # the file's header line
            decoded += 1
            card = cards.from_record(record)
            self.assertEqual(cards.to_record(card), record)
            for ability in card.fields.get("card_abilities") or []:
                self.assertIsInstance(ability.ability, irc.Ref)
                abilities += 1
        self.assertEqual(decoded, 7213)
        self.assertEqual(abilities, 11278)


if __name__ == "__main__":
    unittest.main()
