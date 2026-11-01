#!/usr/bin/env python3
"""Possible issues in the campaign's flags, counters and conversation endings.

- A flag or counter name used only once across the campaign files is often a
  typo: ``clear_flag(beat_work_w2)`` where the flag is ``beat_worm_w2`` clears
  nothing. Counted across ``campaign/campaign_data`` and
  ``campaign/campaign_rules``: ``set_flag(X)`` / ``clear_flag(X)`` actions,
  ``move_counter(A,B)`` actions, and ``counter(X,...)`` / ``"counter": "X"``
  conditions. A wildcard name (``devonshire_*``) is never reported, and a name a
  wildcard matches counts as used by it.
- An ending key ``A<n>`` in a conversation's actions is reported only when the
  client sends that ending: a ``ServerScriptEvent`` answer in the conversation
  Records. Any other ``A<n>`` key never reaches the server, so its actions run
  only in the simulator.

The editor's Rules check lists these per node (``zone_problems``); this script
lists them for the whole campaign:

    python3 campaign_editor/check_names.py

Exits 0 either way: the output lists possible issues to review, not failures.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import threading
from collections import defaultdict
from pathlib import Path

from paths import CAMPAIGN_ROOT, RECORDS  # noqa: E402  (also sets sys.path)

NAME = r"([^(),\s\"]+)"
PATTERNS = {
    "flag": [re.compile(r"(?:set|clear)_flag\(\s*" + NAME + r"\s*\)")],
    "counter": [re.compile(r"move_counter\(\s*" + NAME + r"\s*,\s*" + NAME + r"\s*\)"),
                re.compile(r"(?<![A-Za-z_])counter\(\s*" + NAME + r"\s*[,)]"),
                re.compile(r"\"counter\"\s*:\s*\"([^\"]+)\"")],
}
EXCEPTIONS_PATH = CAMPAIGN_ROOT / "issue_exceptions.json"
_EXCEPTIONS_LOCK = threading.Lock()
ENDING_KEY = re.compile(r"A\d+")
SERVER_EVENT = re.compile(r'ServerScriptEvent",\s*"m_EventName"\s*:\s*"([^"]*)"')

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[object, object]] = {}


def _names_in(text: str):
    """``[(kind, name)]`` for each flag or counter name a text writes or tests."""
    found = []
    for kind, patterns in PATTERNS.items():
        for pattern in patterns:
            for match in pattern.finditer(text):
                found.extend((kind, name.strip()) for name in match.groups() if name.strip())
    return found


def _campaign_files() -> list[Path]:
    return sorted((CAMPAIGN_ROOT / "campaign_data").glob("*.json")) + \
        sorted((CAMPAIGN_ROOT / "campaign_rules").glob("*.json"))


def _cached(key: str, paths: list[Path], build):
    """``build(paths)``, redone only when one of the files changes."""
    stamp = tuple((str(p), p.stat().st_mtime_ns) for p in paths if p.exists())
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]
    value = build(paths)
    with _CACHE_LOCK:
        _CACHE[key] = (stamp, value)
    return value


def _uses(paths) -> dict[tuple[str, str], list[str]]:
    """``{(kind, name): [file, ...]}``, one entry per occurrence."""
    found = defaultdict(list)
    for path in paths:
        for key in _names_in(path.read_text(encoding="utf-8")):
            found[key].append(path.name)
    return found


def _single_names(paths) -> set[tuple[str, str]]:
    found = _uses(paths)
    wildcards = {name for _kind, name in found if "*" in name}
    return {(kind, name) for (kind, name), files in found.items()
            if len(files) == 1 and "*" not in name and not any(
                fnmatch.fnmatchcase(name, pattern) for pattern in wildcards)}


def single_names() -> set[tuple[str, str]]:
    """Flag and counter names used exactly once across the campaign files."""
    return _cached("singles", _campaign_files(), _single_names)


FLAG_READ = [re.compile(r'"flag"\s*:\s*"([^"]+)"'),
             re.compile(r'(?<![A-Za-z_])flag\(\s*([^()"]+?)\s*\)')]
FLAG_WRITE = [re.compile(r'"set_flag"\s*:\s*"([^"]+)"'),
              re.compile(r'set_flag\(\s*([^()"]+?)\s*\)')]


def _flag_names_in(text: str) -> tuple[set[str], set[str]]:
    """``(read, set)`` flag names in a text. Flags the engine sets itself are left out: ``conversation(...)``
    (a finished conversation) and ``tamed:...`` (a tamed creature)."""
    def names(patterns):
        found = set()
        for pattern in patterns:
            found.update(name.strip() for name in pattern.findall(text) if name.strip())
        return {name for name in found if not name.startswith(("conversation(", "tamed:"))}
    return names(FLAG_READ), names(FLAG_WRITE)


def _flag_usage(paths) -> tuple[set[str], set[str]]:
    reads: set[str] = set()
    writes: set[str] = set()
    for path in paths:
        found_reads, found_writes = _flag_names_in(path.read_text(encoding="utf-8"))
        reads |= found_reads
        writes |= found_writes
    return reads, writes


def campaign_flag_usage() -> tuple[set[str], set[str]]:
    """``(read, set)`` flags across every campaign file."""
    return _cached("flags", _campaign_files(), _flag_usage)


def _covered(name: str, pool: set[str]) -> bool:
    """Whether a name is in ``pool`` exactly, or a wildcard on either side matches it."""
    return any(name == other or fnmatch.fnmatchcase(name, other) or fnmatch.fnmatchcase(other, name)
               for other in pool)


def flag_problems(rules: dict) -> list[dict[str, str]]:
    """Flags this zone reads or sets that nothing in the campaign sets or reads, located at a node."""
    all_reads, all_writes = campaign_flag_usage()
    reads, writes = _flag_names_in(json.dumps(rules))
    node_names = {node_id: _flag_names_in(json.dumps(spec)) for node_id, spec in
                  (rules.get("nodes") or {}).items()}

    def where(name):
        return next((node_id for node_id, (node_reads, node_writes) in node_names.items()
                     if name in node_reads | node_writes), "flags")

    problems = []
    for name in sorted(reads):
        if not _covered(name, all_writes):
            problems.append({"level": "warn", "where": where(name), "text":
                             f"flag {name!r} is read but never set anywhere in the campaign"})
    for name in sorted(writes):
        if not _covered(name, all_reads):
            problems.append({"level": "info", "where": where(name), "text":
                             f"flag {name!r} is set but never read anywhere in the campaign"})
    return problems


def server_endings() -> dict[str, set[str]]:
    """``{conversation GUID: names the client reports as ServerScriptEvent endings}``."""
    def build(_paths):
        found: dict[str, set[str]] = defaultdict(set)
        for line in (RECORDS / "ConversationTemplate.jsonl").read_text(encoding="utf-8").splitlines():
            text = json.loads(line) if line.strip() else None
            if not isinstance(text, str):
                continue
            guid = re.search(r'"m_Id"\s*:\s*\{\s*"m_Guid"\s*:\s*"([^"]+)"', text)
            if guid:
                found[guid.group(1).lower()].update(SERVER_EVENT.findall(text))
        return dict(found)
    return _cached("endings", [RECORDS / "ConversationTemplate.jsonl"], lambda p: build(p))


def ending_keys(paths):
    """``[(file, template, node, conversation GUID, key)]`` for each ``A<n>`` key in a conversation's actions."""
    rows = []
    for path in paths:
        stack = [json.loads(path.read_text(encoding="utf-8"))]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                guid = str(node.get("conversation_guid") or "").lower()
                trigger = node.get("trigger_json")
                actions = trigger.get("actions") if isinstance(trigger, dict) else None
                if guid and isinstance(actions, dict):
                    node_id = str(node.get("node_id") or "")
                    template = str(node.get("campaign_template") or "")
                    rows += [(path.name, template, node_id, guid, key) for key in actions
                             if ENDING_KEY.fullmatch(key)]
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return rows


def unsent_endings(guid: str, keys) -> list[str]:
    """The ``A<n>`` keys among ``keys`` that the client never sends for this conversation."""
    sent = server_endings().get(str(guid or "").lower(), set())
    return [key for key in keys if ENDING_KEY.fullmatch(key) and key not in sent]


def zone_problems(rules: dict, template: str = "") -> list[dict[str, str]]:
    """Rules-check problems for one zone's built rules, located at each node. ``template`` is the zone's
    campaign template: only that template's conversation rows are its own."""
    problems = []
    singles = single_names()
    nodes = rules.get("nodes") or {}
    # A campaign row names its node, or else the built offer ``tagged:<first 8 GUID characters>`` names it.
    offered = {}
    for node_id, spec in nodes.items():
        for entry in spec.get("entries") or ():
            if str(entry.get("id", "")).startswith("tagged:"):
                offered.setdefault(entry["id"][len("tagged:"):].lower(), node_id)
    for file, row_template, node_id, guid, key in sorted(ending_keys(_campaign_files())):
        if template and row_template.lower() != template.lower():
            continue
        node_id = node_id if node_id in nodes else offered.get(guid[:8], "")
        if node_id and unsent_endings(guid, [key]):
            problems.append({"level": "warn", "where": node_id, "text":
                             f"ending {key} of conversation {guid} ({file}) is not sent by the client; "
                             "its actions run only in the simulator"})
    for node_id, spec in nodes.items():
        for entry in spec.get("entries") or ():
            where = f"{node_id}.{entry.get('id', '?')}"
            for kind, name in sorted(set(_names_in(json.dumps(entry)))):
                if (kind, name) in singles:
                    problems.append({"level": "info", "where": where, "text":
                                     f"{kind} {name!r} is used only once in the campaign (possible typo)"})
    return problems + flag_problems(rules)


def accepted_issues() -> set[tuple[str, str, str]]:
    """``{(zone, where, text)}`` for each Rules-check problem the editor's users accepted as known."""
    try:
        data = json.loads(EXCEPTIONS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    return {(str(e["zone"]), str(e["where"]), str(e["text"])) for e in data.get("accepted") or ()}


def set_accepted(zone: str, where: str, text: str, accept: bool) -> dict[str, int]:
    """Accept (or un-accept) one Rules-check problem; ``{"accepted": count}``."""
    entry = {"zone": zone, "where": where, "text": text}
    with _EXCEPTIONS_LOCK:
        entries = [e for e in (json.loads(EXCEPTIONS_PATH.read_text(encoding="utf-8"))["accepted"]
                               if EXCEPTIONS_PATH.exists() else [])]
        entries = [e for e in entries if e != entry]
        if accept:
            entries.append(entry)
        temporary = EXCEPTIONS_PATH.with_name(EXCEPTIONS_PATH.name + ".tmp")
        temporary.write_text(json.dumps({"accepted": entries}, indent=1) + "\n", encoding="utf-8")
        os.replace(temporary, EXCEPTIONS_PATH)
    return {"accepted": len(entries)}


def main() -> int:
    paths = _campaign_files()
    singles = single_names()
    for kind, name in sorted(singles):
        print(f"possible issue: {kind} {name!r} is used only once ({_uses(paths)[(kind, name)][0]})")
    print(f"{len(singles)} name(s) used once")
    for file, _template, _node, guid, key in sorted(ending_keys(paths)):
        if unsent_endings(guid, [key]):
            print(f"possible issue: ending {key} of conversation {guid} ({file}) is not sent by the client; "
                  "its actions run only in the simulator")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
