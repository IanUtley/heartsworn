"""The TAC decoder follows the client's reader: a card's own Guid is not an alternate version's."""

import base64
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from gamedata import tac  # noqa: E402


def _string(text):
    raw = text.encode("utf-8")
    length = bytearray()
    value = len(raw)
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            length.append(byte | 0x80)
        else:
            length.append(byte)
            break
    return bytes(length) + raw


def _key(name):
    """An attribute key as the client writes it: the unsigned hash, little-endian."""
    return struct.pack("<I", tac.attr_hash(name))


def test_alternate_version_guid_is_not_the_cards_guid():
    alternate = (_key("Guid") + _string("b6aea7d5-7b42-4b78-b0a1-fd7e65205bcf")
                 + struct.pack("<i", 0))
    data = (struct.pack("<h", 2) + _key("AlternateVersions") + alternate
            + struct.pack("<i", 0))
    encoded = base64.b64encode(data).decode("ascii")
    assert tac.tac_guid(encoded) is None
    versions = tac.decode_tac_tree(encoded)[tac.attr_hash("AlternateVersions")]
    assert versions[0][tac.attr_hash("Guid")] == "b6aea7d5-7b42-4b78-b0a1-fd7e65205bcf"


def test_unknown_attribute_key_is_not_decoded_as_a_value():
    data = struct.pack("<h", 2) + struct.pack("<i", 123456789) + struct.pack("<i", 7) + struct.pack("<i", 0)
    assert tac.decode_tac_tree(base64.b64encode(data).decode("ascii")) == {}


if __name__ == "__main__":
    test_alternate_version_guid_is_not_the_cards_guid()
    test_unknown_attribute_key_is_not_decoded_as_a_value()
    print("PASS TAC decoder")
