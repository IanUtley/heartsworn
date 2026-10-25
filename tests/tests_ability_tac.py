"""The client's TAC codec: a payload decodes to its attributes and encodes back to the same bytes."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign_editor"))

import ability_ir_tac as tac


class TacCodecTest(unittest.TestCase):
    def test_payload_decodes_to_its_attributes_and_encodes_back(self):
        # An empty payload (version 2, no entries) and one with an int and a string.
        self.assertEqual(tac.decode("AgAAAAAA"), {})
        self.assertEqual(tac.encode({}), "AgAAAAAA")
        attributes = {"Deploy": 1, "Name": "Spectral Assassin"}
        payload = tac.encode(attributes)
        self.assertEqual(tac.decode(payload), attributes)
        self.assertEqual(tac.encode(tac.decode(payload)), payload)


if __name__ == "__main__":
    unittest.main()
