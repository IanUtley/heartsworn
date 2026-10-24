"""The semantic ability form: sampled exact abilities read, check clean, and render back."""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "campaign_editor"))

from tests.test_db import fresh_database  # noqa: E402

SRC = fresh_database()

import ability_ir_semantic as sem  # noqa: E402


def _sample(limit=40):
    db = sqlite3.connect(SRC)
    try:
        return [row[0] for row in db.execute(
            "select ability_guid from card_abilities_meta order by ability_guid limit ?", (limit,))]
    finally:
        db.close()


def test_sampled_exact_abilities_read_check_and_render_back():
    readable = 0
    for guid in _sample():
        found = sem.read(guid)
        if not found["semantic"]:
            continue
        readable += 1
        assert sem.check(found["ir"]) == [], guid
        assert sem.text(found["ir"]) == found["text"], guid
    assert readable >= 10
