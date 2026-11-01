#!/usr/bin/env python3
"""Count the Records abilities the IR interpreter refuses, and why.

Every AbilityTemplate in the Records is passed to ``rules_port.ir_exec.coverage``. An ability with any
refusal stays on the native resolver path. The report lists the refusal reasons, most common first, so the
gap document (docs/IR_INTERPRETER_GAPS.md) can be regenerated from the current code and Records.

A refused ability that no game record can reach is an orphan: no card, champion, talent, deck or encounter starts
it, directly or through the records they refer to. Orphans are listed separately and are not counted in the reasons.

Usage (from the repo root, with the Records checked out at hex-private/Records):
    python3 tools/ir_coverage/ir_gap_report.py
    python3 tools/ir_coverage/ir_gap_report.py --top 80 --json /tmp/ir_gaps.json
"""

import argparse
import collections
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# The placeholder-art cards: implemented, excluded from the counts (see main()).
PLACEHOLDER = {
    "7cd00dbf-8ff4-0522-557b-3e8cce82ba8c",   # Footsteps from the Hall (A)
    "93dbc878-527e-e55d-f011-2a19c235f021",   # Footsteps from the Hall (B)
    "5f28daa1-7500-9f85-e688-fe542c1d04bf",   # ChooseRevealAndPlay
    "a38c4b86-e95f-1dec-60e0-d6d90f578ed8",   # OpponentChooseAndPlay
}


# The kinds that a game can start from: cards, champions, talents, decks, encounters, scenes and the rest. The
# ability's own parts (the ability, its effects, conditions and targets) are reached only through these.
ABILITY_PART_KINDS = {"AbilityTemplate", "AbilityEffectTemplate", "AbilityEffectConditionTemplate",
                      "AbilityTargetTemplate"}


def reachable_guids(store):
    """Every GUID a game can reach: the Records entries the game starts from, and everything they refer to, transitively.

    A reference is any GUID that appears in a record's data. The ability's own parts (effects, conditions, targets)
    count only when a reachable record reaches them, so an ability no card or other reachable record names is not
    reachable even when its own effect rows refer to it."""
    kinds = sorted(name[:-len(".jsonl")] for name in os.listdir(store.records_dir) if name.endswith(".jsonl"))
    edges = {}
    roots = []
    for kind in kinds:
        for record in store.load(kind):
            own = str(record.guid).lower()
            refs = {match for match in GUID.findall(json.dumps(record.raw).lower()) if match != own}
            edges.setdefault(own, set()).update(refs)
            if kind not in ABILITY_PART_KINDS:
                roots.append(own)
    seen = set(roots)
    stack = list(roots)
    while stack:
        for nxt in edges.get(stack.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


# The Records entries that are known data errors, not interpreter gaps (IR_ENGINE_GOAL.md, non-goals). Each is
# listed by name in the gap document, and the Records fix is what closes it.
RECORDS_ERRORS = {
    "9b36aabc-dffc-edf3-eaf9-cd514adf3d6a",   # its clause points at a null template
}


def referrers(store):
    """``(kinds, parents)``: each record's kind by GUID, and for each GUID the GUIDs of the records that refer to it."""
    kinds = {}
    parents = {}
    for name in sorted(os.listdir(store.records_dir)):
        if not name.endswith(".jsonl"):
            continue
        kind = name[:-len(".jsonl")]
        for record in store.load(kind):
            own = str(record.guid).lower()
            kinds[own] = kind
            for match in GUID.findall(json.dumps(record.raw).lower()):
                if match != own:
                    parents.setdefault(match, set()).add(own)
    return kinds, parents


def covered_in_context(guid, refusals, kinds, parents):
    """Whether a refused ability runs correctly inside the abilities that invoke it (its parents), because every
    ability above it is covered. A card or champion that names it directly makes it a root: it is not covered in
    context. (An invoked child's refusal also shows on its parent as "invoked: ...", so a covered parent has checked
    the child in its linked context.)"""
    if any(kinds.get(parent) not in ABILITY_PART_KINDS for parent in parents.get(guid, ())):
        return False
    ancestors = set()
    stack = list(parents.get(guid, ()))
    while stack:
        current = stack.pop()
        if current in ancestors or current == guid:
            continue
        ancestors.add(current)
        stack.extend(parents.get(current, ()))
    abilities = [item for item in ancestors if kinds.get(item) == "AbilityTemplate"]
    if not abilities:
        return False
    return all(item not in refusals for item in abilities)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--top", type=int, default=60, help="how many reasons to print")
    parser.add_argument("--json", metavar="PATH", help="also write the full tally as JSON")
    args = parser.parse_args(argv)

    from gamedata import DEFAULT_RECORD_STORE as STORE
    from rules_port.ir_exec import coverage

    # Abilities of cards whose art is still a placeholder (Records m_ArtStatus). Their implementation is done, and the
    # migration counts them as done; they are reported separately, not as gaps.
    guids = [str(record.guid).lower() for record in STORE.load("AbilityTemplate")
             if str(record.guid).lower() not in PLACEHOLDER]
    refusals = {}
    broken = 0
    for guid in guids:
        try:
            missing = coverage(guid)
        except Exception as exc:  # a record the coverage check cannot read is reported, not counted as covered
            broken += 1
            missing = [f"coverage error {type(exc).__name__}: {exc}"]
        if missing:
            refusals[guid] = missing

    reachable = reachable_guids(STORE)
    kinds, parents = referrers(STORE)
    orphans = sorted(guid for guid in refusals if guid not in reachable)
    records_errors = sorted(guid for guid in refusals if guid in RECORDS_ERRORS)
    in_context = sorted(guid for guid in refusals if guid in reachable and guid not in RECORDS_ERRORS and
                        covered_in_context(guid, refusals, kinds, parents))
    excluded = set(orphans) | set(records_errors) | set(in_context)
    reasons = collections.Counter()
    for guid, missing in refusals.items():
        if guid not in excluded:
            reasons.update(set(missing))

    print(f"abilities {len(guids)}")
    print(f"refused {len(refusals)}")
    print(f"orphans {len(orphans)} (refused, and not reachable from any card or other game record)")
    print(f"records errors {len(records_errors)} (Records data to fix; not interpreter gaps)")
    print(f"covered in context {len(in_context)} (refused only as a standalone root; every ability above them is covered)")
    print(f"refused, reachable {len(refusals) - len(orphans) - len(records_errors) - len(in_context)}")
    print(f"coverage errors {broken}")
    for reason, count in reasons.most_common(args.top):
        print(f"{count} {reason}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as stream:
            json.dump({"abilities": len(guids), "refused": len(refusals), "orphans": len(orphans),
                       "orphan_guids": orphans, "records_errors": records_errors, "in_context": in_context,
                       "coverage_errors": broken,
                       "reasons": reasons.most_common()}, stream, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
