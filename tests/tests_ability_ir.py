"""Ability IR variable layer: every seeded variable round-trips, and every expression parses."""
import json
import os
import sqlite3
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "campaign_editor"))

from tests.test_db import fresh_database  # noqa: E402

SRC = fresh_database()

import ability_ir as ir  # noqa: E402


def _abilities():
    """Yield (ability_guid, [variable records]) for every seeded ability."""
    db = sqlite3.connect(SRC)
    try:
        for guid, raw in db.execute(
                "select ability_guid, raw_json from card_abilities_meta "
                "where raw_json is not null and raw_json != ''"):
            yield guid, (json.loads(raw).get("m_Variables") or [])
    finally:
        db.close()


def _target_records():
    """Every AbilityTargetTemplate record from the Records files (skips without them)."""
    import card_abilities  # noqa: E402  (imports paths, which points at Records)
    from paths import RECORDS
    if not (RECORDS / "AbilityTargetTemplate.jsonl").exists():
        raise unittest.SkipTest("Records not present")
    return list(card_abilities._iter_records("AbilityTargetTemplate"))


class AbilityIRVariables(unittest.TestCase):
    def test_every_variable_round_trips_through_the_ir(self):
        checked = 0
        for guid, records in _abilities():
            for record in records:
                self.assertEqual(ir.to_record(ir.from_record(record)), record,
                                 f"{guid} {record.get('_t')}")
                checked += 1
        self.assertGreater(checked, 9000)

    def test_expressions_parse_emit_the_same_text_and_resolve(self):
        for guid, records in _abilities():
            variables = [ir.from_record(record) for record in records]
            for var in variables:
                if var.kind == "expression":
                    text = var.fields["text"]
                    self.assertEqual(ir.emit_expression(ir.parse_expression(text)), text,
                                     f"{guid}: {text!r}")
            self.assertEqual(ir.unresolved_refs(variables), set(), guid)


class AbilityIRTargets(unittest.TestCase):
    def test_every_target_template_round_trips_and_every_class_has_a_kind(self):
        records = _target_records()
        kinds = set()
        for record in records:
            target = ir.target_from_record(record)
            kinds.add(target.kind)
            self.assertEqual(ir.target_to_record(target), record, record.get("m_TemplateId"))
        self.assertEqual(kinds, set(ir.TARGET_KINDS))


if __name__ == "__main__":
    unittest.main()
