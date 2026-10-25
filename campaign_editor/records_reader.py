"""Read Records JSONL lines the way gamedata does, and also the ones it rejects.

A Records line is a JSON string whose content is the record's own JSON. Some lines hold a raw
control character (a tab or newline) inside a string, which the strict JSON parser refuses. The
lenient parse below accepts those lines. Everything else is the same as ``gamedata.records``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from gamedata.records import RecordDecodeError, RecordObject, deserialize

_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def decode_line(line: str) -> RecordObject:
    """One Records line as a typed record object. ``RecordDecodeError`` when it is not a record."""
    try:
        outer = json.loads(line, strict=False)
        inner = json.loads(_TRAILING_COMMA.sub(r"\1", outer), strict=False) if isinstance(outer, str) else outer
        value = deserialize(inner)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RecordDecodeError(str(exc)) from exc
    if not isinstance(value, RecordObject):
        raise RecordDecodeError("record did not contain a typed object")
    return value


def records(path: str) -> list[dict[str, Any]]:
    """Every record in a Records file, as plain dicts, in file order. Header lines are skipped."""
    out = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                out.append(decode_line(line).to_dict())
            except RecordDecodeError:
                continue
    return out
