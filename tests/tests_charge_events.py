"""Focused regression coverage for per-point charge trigger cardinality."""

import os
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def test_charge_gain_emits_one_trigger_per_point():
    from rules_port.context import EffectContext

    champion = SimpleNamespace(uid=SimpleNamespace(uid64=12345))
    handler = SimpleNamespace(
        _player_champ_scid=champion,
        _ai_champ_scid=None,
        user_profile={"id": 5},
    )
    context = EffectContext(
        game=None, session=None, db=None, handler=handler,
        player_uid=None, ai_uid=None, bstate={"champ_map": {5: 12345}},
        effect_guid="charge-test")

    with mock.patch.object(context, "_emit_trigger",
                           return_value="queued") as emit:
        assert context.emit_gain_charge_triggers(2, owner_id=5) == [
            "queued", "queued"]
    assert emit.call_count == 2
    assert all(call.args[:3] == ("GainChargeEvent", 12345, 5)
               for call in emit.call_args_list)


if __name__ == "__main__":
    test_charge_gain_emits_one_trigger_per_point()
    print("PASS charge event cardinality")
