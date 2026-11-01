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
import ability_ir_filters as irf  # noqa: E402
import ability_ir_effects as ire  # noqa: E402
import ability_ir_clauses as irc  # noqa: E402
import ability_ir_root as irr  # noqa: E402
import ability_ir_conditions as irk  # noqa: E402
import ability_ir_json as irj  # noqa: E402


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


FILTER_SECTIONS = ("AbilityTemplate", "AbilityTargetTemplate",
                   "AbilityEffectTemplate", "AbilityEffectConditionTemplate")


def _top_level_filters(node):
    """Filter records under ``node``, not descending into a filter once found."""
    if isinstance(node, dict):
        if str(node.get("_t") or "").startswith(irf.FILTER_PREFIX):
            yield node
            return
        for value in node.values():
            yield from _top_level_filters(value)
    elif isinstance(node, list):
        for item in node:
            yield from _top_level_filters(item)


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


class AbilityIRFilters(unittest.TestCase):
    def test_every_filter_in_the_ability_sections_round_trips(self):
        from gamedata.records import deserialize_line  # noqa: E402
        from paths import RECORDS
        if not RECORDS.exists():
            raise unittest.SkipTest("Records not present")
        checked = set()
        for section in FILTER_SECTIONS:
            with (RECORDS / f"{section}.jsonl").open(encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    try:
                        record = deserialize_line(line).to_dict()
                    except Exception:
                        continue  # the editor's loader skips these too
                    for filt in _top_level_filters(record):
                        self.assertEqual(irf.filter_to_record(irf.filter_from_record(filt)), filt,
                                         f"{section}: {filt.get('_t')}")
                        checked.add(filt["_t"])
        self.assertTrue(checked)


class AbilityIREffects(unittest.TestCase):
    def test_every_effect_template_round_trips(self):
        from gamedata.records import deserialize_line  # noqa: E402
        from paths import RECORDS
        if not RECORDS.exists():
            raise unittest.SkipTest("Records not present")
        checked = 0
        with (RECORDS / "AbilityEffectTemplate.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    record = deserialize_line(line).to_dict()
                except Exception:
                    continue  # the editor's loader skips these too
                self.assertEqual(ire.node_to_record(ire.node_from_record(record)), record,
                                 record.get("_t"))
                checked += 1
        self.assertGreater(checked, 5000)


class AbilityIRClauses(unittest.TestCase):
    def test_every_clause_round_trips(self):
        from gamedata.records import deserialize_line  # noqa: E402
        from paths import RECORDS
        if not RECORDS.exists():
            raise unittest.SkipTest("Records not present")
        checked = 0
        with (RECORDS / "AbilityTemplate.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    record = deserialize_line(line).to_dict()
                except Exception:
                    continue  # the editor's loader skips these too
                for stored in record.get("m_AbilityEffectList") or []:
                    self.assertEqual(irc.clause_to_record(irc.clause_from_record(stored)), stored)
                    checked += 1
        self.assertGreater(checked, 16000)


class AbilityIRRoots(unittest.TestCase):
    def test_every_ability_root_round_trips(self):
        from gamedata.records import deserialize_line  # noqa: E402
        from paths import RECORDS
        if not RECORDS.exists():
            raise unittest.SkipTest("Records not present")
        checked = 0
        with (RECORDS / "AbilityTemplate.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    record = deserialize_line(line).to_dict()
                except Exception:
                    continue  # the editor's loader skips these too
                self.assertEqual(irr.node_to_record(irr.node_from_record(record)), record,
                                 record.get("m_Name"))
                checked += 1
        self.assertGreater(checked, 8900)


class AbilityIRConditions(unittest.TestCase):
    def test_every_effect_condition_template_round_trips(self):
        from gamedata.records import deserialize_line  # noqa: E402
        from paths import RECORDS
        if not RECORDS.exists():
            raise unittest.SkipTest("Records not present")
        checked = 0
        with (RECORDS / "AbilityEffectConditionTemplate.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    record = deserialize_line(line).to_dict()
                except Exception:
                    continue  # the editor's loader skips these too
                self.assertEqual(irk.node_to_record(irk.node_from_record(record)), record,
                                 record.get("m_Name"))
                checked += 1
        self.assertGreater(checked, 500)


class AbilityIRJson(unittest.TestCase):
    def test_every_ability_root_survives_json_text(self):
        from gamedata.records import deserialize_line  # noqa: E402
        from paths import RECORDS
        if not RECORDS.exists():
            raise unittest.SkipTest("Records not present")
        checked = 0
        with (RECORDS / "AbilityTemplate.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    record = deserialize_line(line).to_dict()
                except Exception:
                    continue  # the editor's loader skips these too
                text = json.dumps(irj.to_json(irr.node_from_record(record)))
                rebuilt = irr.node_to_record(irj.from_json(json.loads(text)))
                self.assertEqual(rebuilt, record, record.get("m_Name"))
                checked += 1
        self.assertGreater(checked, 8900)


if __name__ == "__main__":
    unittest.main()
