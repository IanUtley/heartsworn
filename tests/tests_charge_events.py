"""Focused regression coverage for per-point charge trigger cardinality."""

import os
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from tests.test_db import fresh_database  # noqa: E402
fresh_database()   # bind the scratch test database before any module imports db
from tests.leaf_routing import install_leaf_routes as _install_leaf_routes
_install_leaf_routes()
import rules_port.leaves_abilities as _leaves_abilities
import rules_port.leaves_modifiers as _leaves_modifiers


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

    with mock.patch.object(_leaves_abilities, "_emit_trigger",
                           return_value="queued") as emit:
        assert _leaves_modifiers.emit_gain_charge_triggers(context, 2, owner_id=5) == [
            "queued", "queued"]
    assert emit.call_count == 2
    assert all(call.args[1:4] == ("GainChargeEvent", 12345, 5)
               for call in emit.call_args_list)


if __name__ == "__main__":
    test_charge_gain_emits_one_trigger_per_point()
    print("PASS charge event cardinality")
