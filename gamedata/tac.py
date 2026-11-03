"""The client's Template Attribute Collection (TAC), decoded faithfully.

A TAC is ``Game.Shared.Mechanics.TAC`` serialized to bytes (base64 in the Records and in event
payloads). This module is the one decoder the server uses. It follows the client's reader
(``TAC.AppendFromFile`` and the byte-array constructor) rather than guessing:

* the stream starts with an int16 version; version 2 and 1 are the hash-keyed format and version 0
  the old name-keyed one. A stream that fails to read is retried as version 1, then 0, and a stream
  that still fails is empty, as the client does;
* each entry is an int32 attribute key followed by its value. The key is the first four MD5 bytes
  of the attribute name (see ``attr_hash``), and the registry (``gamedata/tac_attributes.py``) says
  whether the value is a string, an int, a nested collection or a list entry;
* an unknown key is a read error, not a value of a guessed type.

Decoded trees are keyed by ``attr_hash(name)`` (an unsigned 32-bit int), so existing callers keep
working with the names they already hash. A nested collection is a dict, a list attribute is a list
of dicts, a string is a str and an int is an int.
"""

from __future__ import annotations

import base64
import hashlib
import struct
from typing import Any

from gamedata.tac_attributes import ATTRIBUTES

_MASK = 0xFFFFFFFF


def attr_hash(name: str) -> int:
    """The unsigned hash the client uses as an attribute key (``AttributeName.CreateHash``)."""
    digest = bytearray(hashlib.md5(str(name).encode("ascii")).digest()[:4])
    if digest[0] == 0:
        digest[0] = 1
    if digest[3] == 0:
        digest[3] = 1
    value = 0
    for byte in digest:
        value = (value << 8) + byte
    return value


class _TacError(Exception):
    """A stream the client's reader would reject."""


# Keys are read as signed int32 (the client writes them that way); the registry is keyed unsigned.
_BY_HASH = {attr_hash(name): (name, kind) for name, kind in ATTRIBUTES.items()}


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def take(self, count: int) -> bytes:
        if count < 0 or self.pos + count > len(self.data):
            raise _TacError("truncated")
        chunk = self.data[self.pos:self.pos + count]
        self.pos += count
        return chunk

    def int32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def string(self) -> str:
        """``BinaryReader.ReadString``: a 7-bit length prefix, then UTF-8."""
        length = shift = 0
        while True:
            byte = self.take(1)[0]
            length |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
            shift += 7
        return self.take(length).decode("utf-8")


def _read_entries(reader: _Reader, version: int, out: dict[int, Any]) -> None:
    """``TAC.AppendFromFile``: entries until a zero key."""
    if version == 0:
        _read_named_entries(reader, out)
        return
    while True:
        key = reader.int32()
        if key == 0:
            return
        found = _BY_HASH.get(key & _MASK)
        if found is None:
            raise _TacError(f"unknown attribute key {key}")
        name, kind = found
        value_key = attr_hash(name)
        if kind == "String":
            out[value_key] = reader.string()
        elif kind == "Int":
            out[value_key] = reader.int32()
        elif kind == "TAC":
            child: dict[int, Any] = {}
            _read_entries(reader, 1, child)
            out[value_key] = child
        else:  # List
            child = {}
            _read_entries(reader, 1, child)
            out.setdefault(value_key, []).append(child)


def _read_named_entries(reader: _Reader, out: dict[int, Any]) -> None:
    """``TAC.AppendFromOldFileFormat1``: name-keyed entries ended by "END"."""
    while True:
        name = reader.string()
        if name == "END":
            return
        if name == "":
            continue
        kind = ATTRIBUTES.get(name)
        if kind is None:
            raise _TacError(f"unknown attribute {name}")
        value_key = attr_hash(name)
        if kind == "String":
            out[value_key] = reader.string()
        elif kind == "Int":
            out[value_key] = reader.int32()
        elif kind == "TAC":
            child: dict[int, Any] = {}
            _read_named_entries(reader, child)
            out[value_key] = child
        else:
            child = {}
            _read_named_entries(reader, child)
            out.setdefault(value_key, []).append(child)


def _decode_bytes(raw: bytes) -> dict[int, Any]:
    """The client's byte-array constructor.

    It reads the int16 version, then the entries. When that fails, it retries from the start of the
    stream (before the version) as version 1, then 0, and returns an empty collection if all fail.
    """
    try:
        reader = _Reader(raw)
        version = struct.unpack("<h", reader.take(2))[0]
        out: dict[int, Any] = {}
        _read_entries(reader, version, out)
        return out
    except (_TacError, struct.error, UnicodeDecodeError, IndexError):
        pass
    for version in (1, 0):
        out = {}
        try:
            _read_entries(_Reader(raw), version, out)
            return out
        except (_TacError, struct.error, UnicodeDecodeError, IndexError):
            continue
    return {}


def decode_tac_tree(data: Any) -> dict[int, Any]:
    """Decode a base64 TAC into ``{attr_hash(name): value}``. Empty when it does not decode."""
    if not isinstance(data, (str, bytes)) or not data:
        return {}
    try:
        raw = base64.b64decode(data)
    except (ValueError, TypeError):
        return {}
    if len(raw) < 2:
        return {}
    return _decode_bytes(raw)


decode_tac = decode_tac_tree


def tac_int(data: Any, name: str, default: int = 0) -> int:
    value = decode_tac_tree(data).get(attr_hash(name))
    return value if isinstance(value, int) else default


def tac_string(data: Any, name: str, default: str = "") -> str:
    value = decode_tac_tree(data).get(attr_hash(name))
    return value if isinstance(value, str) else default


def tac_guid(data: Any) -> str | None:
    value = decode_tac_tree(data).get(attr_hash("Guid"))
    return value if isinstance(value, str) and value else None


def tac_function(data: Any) -> str:
    """The TACAbilityEffectTemplate operation key (``StringAttrs.FunctionName``)."""
    value = decode_tac_tree(data).get(attr_hash("FunctionName"))
    return value if isinstance(value, str) else ""
