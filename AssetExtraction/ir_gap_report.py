#!/usr/bin/env python3
"""Count the Records abilities the IR interpreter refuses, and why.

Every AbilityTemplate in the Records is passed to ``rules_port.ir_exec.coverage``. An ability with any
refusal stays on the native resolver path. The report lists the refusal reasons, most common first, so the
gap document (docs/IR_INTERPRETER_GAPS.md) can be regenerated from the current code and Records.

Usage (from the repo root, with the Records checked out at hex-private/Records):
    python3 AssetExtraction/ir_gap_report.py
    python3 AssetExtraction/ir_gap_report.py --top 80 --json /tmp/ir_gaps.json
"""

import argparse
import collections
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--top", type=int, default=60, help="how many reasons to print")
    parser.add_argument("--json", metavar="PATH", help="also write the full tally as JSON")
    args = parser.parse_args(argv)

    from gamedata import DEFAULT_RECORD_STORE as STORE
    from rules_port.ir_exec import coverage

    guids = [str(record.guid).lower() for record in STORE.load("AbilityTemplate")]
    reasons = collections.Counter()
    refused = 0
    broken = 0
    for guid in guids:
        try:
            missing = coverage(guid)
        except Exception as exc:  # a record the coverage check cannot read is reported, not counted as covered
            broken += 1
            missing = [f"coverage error {type(exc).__name__}: {exc}"]
        if missing:
            refused += 1
            reasons.update(set(missing))

    print(f"abilities {len(guids)}")
    print(f"refused {refused}")
    print(f"coverage errors {broken}")
    for reason, count in reasons.most_common(args.top):
        print(f"{count} {reason}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as stream:
            json.dump({"abilities": len(guids), "refused": refused, "coverage_errors": broken,
                       "reasons": reasons.most_common()}, stream, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
