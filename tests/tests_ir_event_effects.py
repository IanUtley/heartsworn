"""The TAC and event-firing leaves the interpreter covers: which TAC operations it runs, and which it refuses."""

import base64
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from rules_port.ir_event_effects import NATIVE_LEAF_EFFECTS, TAC_FUNCTIONS, coverage_of  # noqa: E402
from rules_port.tac import _tac_attr_hash  # noqa: E402


def _serialized_function(name):
    """A serialized TAC whose FunctionName is ``name``, in the client's layout (a header, then the attribute)."""
    raw = b"\x00\x00" + struct.pack("<I", _tac_attr_hash("FunctionName"))
    encoded = name.encode("utf-8")
    raw += bytes([len(encoded)]) + encoded + struct.pack("<I", 0)
    return base64.b64encode(raw).decode("ascii")


def test_covered_tac_operation_has_no_missing_construct():
    effect = {"m_SerializedTAC": {"data": _serialized_function("ShiftAbility")}}
    assert coverage_of("TACAbilityEffectTemplate", effect) == []


def test_uncovered_tac_operation_is_reported():
    effect = {"m_SerializedTAC": {"data": _serialized_function("NoSuchOperation")}}
    assert coverage_of("TACAbilityEffectTemplate", effect) == ["TAC NoSuchOperation"]


def test_every_listed_tac_operation_is_decoded_by_name():
    for name in TAC_FUNCTIONS:
        assert coverage_of("TACAbilityEffectTemplate", {"m_SerializedTAC": _serialized_function(name)}) == []


def test_fire_event_is_a_native_leaf_with_no_missing_construct():
    assert "FireEventEffectTemplate" in NATIVE_LEAF_EFFECTS
    assert coverage_of("FireEventEffectTemplate", {}) == []


def test_bookkeeping_effects_are_native_leaves():
    for kind in ("StoreNameAbilityEffectTemplate", "RememberKeywordPowersEffectTemplate",
                 "RegisterTriggerAbilityEffectTemplate", "ActivateTriggeredAbilityEffectTemplate"):
        assert kind in NATIVE_LEAF_EFFECTS
        assert coverage_of(kind, {}) == []
