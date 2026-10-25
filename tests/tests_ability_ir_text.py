"""The IR's code form: a sample of abilities is written as code and read back to the same Records, and bad
code raises a TextError."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign_editor"))

import ability_ir_text as tx
import card_abilities


def _expected(root):
    """The root and the effects, conditions and targets it refers to, as the Records store them."""
    out = [root]
    seen = set()
    for clause in root.get("m_AbilityEffectList") or []:
        for field, section in (("m_EffectTemplateId", "AbilityEffectTemplate"),
                               ("m_ConditionId", "AbilityEffectConditionTemplate")):
            guid = str((clause.get(field) or {}).get("m_Guid") or "").lower()
            if guid and not guid.startswith("00000000") and (section, guid) not in seen:
                seen.add((section, guid))
                record, _ = card_abilities.lookup(section, guid)
                if record is not None:
                    out.append(record)
    for target in root.get("m_AbilityTargetTemplateIds") or []:
        guid = str(target.get("m_Guid")).lower()
        if ("target", guid) not in seen:
            seen.add(("target", guid))
            record, _ = card_abilities.lookup("AbilityTargetTemplate", guid)
            if record is not None:
                out.append(record)
    return out


def _canon(record):
    return json.dumps({key: value for key, value in record.items() if key != "_v"}, sort_keys=True)


class IrTextTest(unittest.TestCase):
    def test_sampled_abilities_round_trip_through_code(self):
        roots = list(card_abilities._iter_records("AbilityTemplate"))[::150]
        self.assertGreater(len(roots), 50)
        for root in roots:
            guid = str(root["m_AbilityTemplateId"]["m_Guid"]).lower()
            written = tx.loads_ability(tx.ability_code(guid))["records"]
            self.assertEqual(sorted(map(_canon, written)), sorted(map(_canon, _expected(root))), guid)

    def test_bound_child_link_compiles_to_an_alias_or_is_refused(self):
        guid = "a2e69e41-aed9-6877-898b-d97e72f9fc41"   # a child link the inliner takes
        lines = tx.ability_code(guid).splitlines()
        start = next(i for i, line in enumerate(lines) if "link(" in line)
        end = next(i for i in range(start + 1, len(lines)) if lines[i].strip().startswith(")"))

        def with_binding(parent):
            out = list(lines)
            out.insert(end, f"      bindings=[EqualToStoredTargetAtk = ${parent}],")
            return "\n".join(out) + "\n"

        root = tx.loads_ability(with_binding("DamagedCards"))["root"]
        alias = [v for v in root["m_Variables"] if v.get("m_Name") == "EqualToStoredTargetAtk"]
        self.assertEqual(alias[0]["m_ExpressionText"], "DamagedCards")
        with self.assertRaises(tx.TextError):
            tx.loads_ability(with_binding("NoSuchInput"))

    def test_bad_code_raises(self):
        with self.assertRaises(tx.TextError):
            tx.loads_ability("root.ability_template(name=)")
        with self.assertRaises(tx.TextError):
            tx.loads_ability('effect E0 = effect.activate()\nroot.ability_template(ability_effect_list=[E9])')


if __name__ == "__main__":
    unittest.main()
