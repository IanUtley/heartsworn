#!/usr/bin/env python3
"""Check that campaign_content matches the campaign tables in hconnect.db.

Run before the tables are dropped (migration.py). Each in-scope table is compared
row by row on the columns both sides have: GUIDs case-insensitively, ``*_json``
columns as parsed JSON, and NULL as ''. A table that differs, or is missing from
the database, makes the check exit non-zero.

    python3 campaign_editor/check_content.py [--db hconnect.db]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter

from paths import REPO  # noqa: E402  (also sets sys.path)

import campaign_source  # noqa: E402
campaign_source.ensure_campaign_path()
import campaign_content  # noqa: E402

# table -> natural key (GUID columns compared case-insensitively)
KEYS = {
    "encounter_scenes": ("guid",),
    "campaign_node_conversations": ("campaign_template", "conversation_guid", "node_id"),
    "quest_conversations": ("quest_script", "conversation_guid", "campaign_template",
                            "node_id", "role"),
    "quest_templates": ("script_name",),
    "campaign_node_edges": ("campaign_template", "from_node", "to_node", "path_name"),
}
IGNORED = {"origin", "rowid"}


def _value(column: str, value):
    if value is None:
        return ""
    if column.endswith("_json") and isinstance(value, str):
        try:
            return json.dumps(json.loads(value), sort_keys=True)
        except ValueError:
            return value
    if column.endswith("_json"):
        return json.dumps(value, sort_keys=True)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, str) and len(value) == 36 and value.count("-") == 4:
        return value.lower()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _signature(row: dict, columns: list[str]) -> tuple:
    return tuple((c, _value(c, row.get(c))) for c in columns)


def check_table(connection: sqlite3.Connection, table: str, keys: tuple[str, ...],
                built: list[dict]) -> list[str]:
    try:
        cursor = connection.execute(f"SELECT * FROM {table}")
    except sqlite3.Error as exc:
        return [f"{table}: not readable in the database ({exc})"]
    names = [d[0] for d in cursor.description]
    db_rows = [dict(zip(names, row)) for row in cursor.fetchall()]
    columns = [c for c in names if c not in IGNORED and (not built or c in built[0])]

    def group(rows):
        out: dict[tuple, Counter] = {}
        for row in rows:
            key = tuple(_value(k, row.get(k)) for k in keys)
            out.setdefault(key, Counter())[_signature(row, columns)] += 1
        return out

    db_groups, built_groups = group(db_rows), group(built)
    problems = []
    for key in sorted(set(db_groups) | set(built_groups), key=repr):
        if key not in built_groups:
            problems.append(f"{table}: {key} is in the database only")
        elif key not in db_groups:
            problems.append(f"{table}: {key} is in campaign_content only")
        elif db_groups[key] != built_groups[key]:
            problems.append(f"{table}: {key} differs")
    if not problems:
        print(f"{table}: {len(db_rows)} rows match ({len(columns)} columns compared)")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(REPO / "hconnect.db"))
    args = parser.parse_args()
    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    problems: list[str] = []
    for table, keys in KEYS.items():
        problems += check_table(connection, table, keys, campaign_content.table(table))
    for problem in problems[:50]:
        print("DIFFERENCE:", problem)
    if problems:
        print(f"{len(problems)} difference(s): campaign_content does not match the database")
        return 1
    print("campaign_content matches the database on every in-scope table")
    return 0


if __name__ == "__main__":
    sys.exit(main())
