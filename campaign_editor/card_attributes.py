"""A card's keywords and integer attributes, by the client's own definitions.

* Keywords are the members of the client's ``[Flags]`` enum ``ECardAttributes``
  (Game/Shared/Mechanics/ECardAttributes.cs). A CardTemplate holds them in
  ``m_AttributeFlags``, members joined by ``|`` ("Flight|Speed"), ``Unknown`` for
  none. ``keywords()`` lists the single-bit members with the display name their
  ``[ToolName]`` attribute gives (Juggernaught is "Crush"); a member that is the
  union of others (PreventAllDamage) is read as its parts.
* Integer attributes are the client's ``IntAttrs`` names (Lethal, Rage, Deploy, ...).
  A CardTemplate holds its own in ``m_SerializedTAC``: base64 of a TAC, the client's
  attribute collection (TAC.cs ``Write`` and ``AppendFromFile``): an Int16 version
  (2), then entries of an Int32 name hash and a value (an Int32 for an IntAttrs, a
  string for a StringAttrs, a nested TAC for a TACAttrs or ListAttrs entry), ended
  by a 0 hash. The hash is the first four bytes of the name's MD5, read big-endian,
  with a zero first or fourth byte made 1 (AttributeName.CreateHash). Every Records
  card TAC decodes this way. ``with_ints`` rewrites only the integer entries.
* ``records_cards()``: each Records card's keywords and integer attributes.
  ``int_attributes()``: the integer attributes Records cards carry, most used first.
  ``flag_attributes()``: those that every Records card carrying them holds at 1
  and that name no keyword (Lethal): the ability sentences say "T gets Lethal".

Records is only read.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import struct
import threading
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from paths import RECORDS  # noqa: E402  (also sets sys.path)
import client_schema  # noqa: E402

FLAGS_ENUM = "ECardAttributes"
NO_FLAGS = "Unknown"   # m_AttributeFlags of a card without keywords
TAC_VERSION = 2
INT32 = (-(1 << 31), (1 << 31) - 1)
_RECORDS_CARDS = RECORDS / "CardTemplate.jsonl"

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {}


# ---------------------------------------------------------------- keywords

def _members() -> list[dict[str, Any]]:
    return client_schema.enum_members(FLAGS_ENUM)


def _single_bit(value: int) -> bool:
    value &= 0xFFFFFFFF
    return value != 0 and value & (value - 1) == 0


def keywords() -> list[dict[str, Any]]:
    """``[{name, label, value}]``: the single-bit ``ECardAttributes`` members, in enum
    order, each with the number of Records cards that have it (``uses``)."""
    uses = _records_state()["keyword_uses"]
    return [dict(member, uses=uses.get(member["name"], 0))
            for member in _members() if _single_bit(member["value"])]


def _expand(names: list[str]) -> list[str]:
    """Member names as their single-bit members, in enum order (``ValueError`` for a
    name the enum lacks). ``Unknown`` adds nothing."""
    by_name = {m["name"].lower(): m for m in _members()}
    bits = 0
    for name in names:
        member = by_name.get(str(name).strip().lower())
        if member is None:
            raise ValueError(f"{name!r} is not a keyword (an {FLAGS_ENUM} member)")
        bits |= member["value"] & 0xFFFFFFFF
    return [m["name"] for m in _members() if _single_bit(m["value"]) and bits & (m["value"] & 0xFFFFFFFF)]


def parse_flags(text: Any) -> list[str]:
    """``m_AttributeFlags`` ("Flight|Speed") as single-bit member names."""
    return _expand([part for part in str(text or NO_FLAGS).split("|") if part.strip()])


def flags_text(names: list[str]) -> str:
    """Member names as ``m_AttributeFlags`` text; ``Unknown`` for none."""
    return "|".join(names) or NO_FLAGS


def clean_keywords(raw: Any) -> list[str]:
    """Validate a card's keywords: a list of ``ECardAttributes`` member names."""
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ValueError("keywords must be a list")
    return _expand([str(name) for name in raw])


# ---------------------------------------------------------------- integer attributes

def attribute_hash(name: str) -> int:
    """The client's hash of an attribute name, as a signed Int32."""
    digest = bytearray(hashlib.md5(name.encode("ascii")).digest()[:4])
    if digest[0] == 0:
        digest[0] = 1
    if digest[3] == 0:
        digest[3] = 1
    return struct.unpack(">i", bytes(digest))[0]


def _by_hash() -> dict[int, tuple[str, str]]:
    """``{hash: (kind, name)}`` for every client attribute name."""
    with _LOCK:
        if "by_hash" not in _STATE:
            _STATE["by_hash"] = {attribute_hash(name): (kind, name)
                                 for kind, names in client_schema.attribute_names().items()
                                 for name in names}
        return _STATE["by_hash"]


def int_attribute_names() -> list[str]:
    """Every ``IntAttrs`` name the client declares."""
    return client_schema.attribute_names().get("IntAttrs", [])


def _read_string(data: bytes, at: int) -> tuple[str, int]:
    """A BinaryWriter string: a 7-bit encoded length, then UTF-8."""
    length, shift = 0, 0
    while True:
        byte = data[at]
        at += 1
        length |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            break
    return data[at:at + length].decode("utf-8"), at + length


def _write_string(text: str) -> bytes:
    raw = text.encode("utf-8")
    out, length = bytearray(), len(raw)
    while True:
        byte, length = length & 0x7F, length >> 7
        out.append(byte | (0x80 if length else 0))
        if not length:
            return bytes(out) + raw


def _read_entries(data: bytes, at: int) -> tuple[list[tuple[str, str, Any]], int]:
    entries = []
    names = _by_hash()
    while True:
        (key,) = struct.unpack_from("<i", data, at)
        at += 4
        if key == 0:
            return entries, at
        if key not in names:
            raise ValueError(f"TAC names an attribute the client lacks (hash {key})")
        kind, name = names[key]
        if kind == "IntAttrs":
            (value,) = struct.unpack_from("<i", data, at)
            at += 4
        elif kind == "StringAttrs":
            value, at = _read_string(data, at)
        else:
            value, at = _read_entries(data, at)
        entries.append((kind, name, value))


def _write_entries(entries: list[tuple[str, str, Any]]) -> bytes:
    out = bytearray()
    for kind, name, value in entries:
        out += struct.pack("<i", attribute_hash(name))
        if kind == "IntAttrs":
            out += struct.pack("<i", value)
        elif kind == "StringAttrs":
            out += _write_string(value)
        else:
            out += _write_entries(value)
    return bytes(out) + struct.pack("<i", 0)


def decode_tac(data: Any) -> list[tuple[str, str, Any]]:
    """The entries of a serialized TAC (base64), as ``(kind, name, value)``."""
    if not data:
        return []
    raw = base64.b64decode(str(data), validate=True)
    entries, _ = _read_entries(raw, 2)   # after the Int16 version
    return entries


def encode_tac(entries: list[tuple[str, str, Any]]) -> str:
    """Entries as a serialized TAC (base64), in the client's order: strings, integers,
    TACs, then lists (TAC.Write)."""
    order = {"StringAttrs": 0, "IntAttrs": 1, "TACAttrs": 2, "ListAttrs": 3}
    ordered = sorted(entries, key=lambda entry: order[entry[0]])   # stable: keeps each kind's order
    return base64.b64encode(struct.pack("<h", TAC_VERSION) + _write_entries(ordered)).decode("ascii")


def tac_ints(data: Any) -> dict[str, int]:
    """A serialized TAC's integer attributes, ``{name: value}``."""
    return {name: value for kind, name, value in decode_tac(data) if kind == "IntAttrs"}


def with_ints(data: Any, ints: dict[str, int]) -> str:
    """``data`` (a serialized TAC, or empty) with its integer attributes replaced by
    ``ints``; its other entries stay. Unchanged integers return ``data`` as it was."""
    if tac_ints(data) == ints and data:
        return str(data)
    kept = [entry for entry in decode_tac(data) if entry[0] != "IntAttrs"]
    return encode_tac(kept + [("IntAttrs", name, value) for name, value in ints.items()])


def clean_int_attributes(raw: Any) -> dict[str, int]:
    """Validate a card's integer attributes: ``{IntAttrs name: whole number}``. A 0 is
    the client's value for an attribute a card lacks, so it is dropped."""
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise ValueError("integer attributes must be an object")
    names = {name.lower(): name for name in int_attribute_names()}
    out: dict[str, int] = {}
    for key, value in raw.items():
        name = names.get(str(key).strip().lower())
        if name is None:
            raise ValueError(f"{key!r} is not an integer attribute (a client IntAttrs name)")
        if value is None or str(value).strip() == "":
            continue   # left blank: the card lacks it
        if isinstance(value, bool) or not re.fullmatch(r"-?\d+", str(value).strip()):
            raise ValueError(f"{name} must be a whole number")
        number = int(str(value).strip())
        if not INT32[0] <= number <= INT32[1]:
            raise ValueError(f"{name} must fit in 32 bits")
        if number:
            out[name] = number
    return dict(sorted(out.items()))


# ---------------------------------------------------------------- Records cards

def _field(text: str, pattern: str) -> str:
    found = re.search(pattern, text)
    return found.group(1) if found else ""


def _load_records(path: Path) -> dict[str, Any]:
    cards: dict[str, dict[str, Any]] = {}
    keyword_uses, int_uses = Counter(), Counter()
    int_values: dict[str, set[int]] = defaultdict(set)
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            text = json.loads(line) if line.strip() else None
        except ValueError:
            continue
        if not isinstance(text, str) or '"_t"' not in text:
            continue
        guid = _field(text, r'"m_Id"\s*:\s*\{\s*"m_Guid"\s*:\s*"([^"]+)"').lower()
        if not guid:
            continue
        try:
            flags = parse_flags(_field(text, r'"m_AttributeFlags"\s*:\s*"([^"]*)"'))
            ints = tac_ints(_field(text, r'"m_SerializedTAC"\s*:\s*\{\s*"data"\s*:\s*"([^"]*)"'))
        except (ValueError, struct.error, IndexError):   # not a card the client could load
            continue
        cards[guid] = {"keywords": flags, "int_attributes": dict(sorted(ints.items()))}
        keyword_uses.update(flags)
        int_uses.update(ints)
        for name, value in ints.items():
            int_values[name].add(value)
    return {"cards": cards, "keyword_uses": keyword_uses, "int_uses": int_uses,
            "int_values": dict(int_values)}


def _records_state() -> dict[str, Any]:
    """Records cards' keywords and integers, read once per change of CardTemplate.jsonl."""
    try:
        mtime = _RECORDS_CARDS.stat().st_mtime_ns
    except FileNotFoundError:
        return {"cards": {}, "keyword_uses": Counter(), "int_uses": Counter(), "int_values": {}}
    with _LOCK:
        hit = _STATE.get("records")
    if hit is None or hit[0] != mtime:
        hit = (mtime, _load_records(_RECORDS_CARDS))   # outside the lock: it reads the hashes
        with _LOCK:
            _STATE["records"] = hit
    return hit[1]


def records_card(guid: str) -> dict[str, Any]:
    """A Records card's ``{keywords, int_attributes}`` (empty for any other card)."""
    found = _records_state()["cards"].get(str(guid or "").lower())
    return json.loads(json.dumps(found)) if found else {"keywords": [], "int_attributes": {}}


def int_attributes() -> list[dict[str, Any]]:
    """``[{name, uses}]``: the integer attributes Records cards carry, most used first."""
    uses = _records_state()["int_uses"]
    return [{"name": name, "uses": count} for name, count in
            sorted(uses.items(), key=lambda item: (-item[1], item[0]))]


def flag_attributes() -> set[str]:
    """Integer attributes every Records card that carries them holds at 1, and whose
    name is no keyword: on or off, like Lethal."""
    keyword_names = {m["name"].lower() for m in _members()}
    return {name for name, values in _records_state()["int_values"].items()
            if values == {1} and name.lower() not in keyword_names}
