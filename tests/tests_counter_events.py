"""A native counter gain emits CounterAddedToCardEvent.

Regression: a Seed Pod's "when this gains a seed counter, if there are three or
more, transform" trigger never fired, because the native counter path
(``rules_port.counter_effects.change_counter``) updated the counter without
emitting the event that trigger listens for.
"""

import os
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database

fresh_database()

from rules_port import counter_effects  # noqa: E402
from rules_port.tac import _tac_attr_hash  # noqa: E402

SEED_COUNTER_GUID = "d5d41a79-8ee3-9443-ff83-544f139a98be"


def test_native_card_counter_gain_emits_counter_added_event():
    context = SimpleNamespace(
        db=mock.Mock(), bstate={}, session=SimpleNamespace(session_id=1),
        _emit_trigger=mock.Mock())
    with mock.patch.object(counter_effects, "_champion_owner", return_value=None), \
            mock.patch("pvp_db.db_card_mutation_field", return_value='{"counters": {"seed": 2}}'), \
            mock.patch("pvp_db.db_set_card_mutation_field"), \
            mock.patch("pvp_db.db_card_owner_id", return_value=7), \
            mock.patch.object(counter_effects, "_project"):
        assert counter_effects.change_counter(
            context, 123, "seed", SEED_COUNTER_GUID, 1, "add") == (2, 3)
    context._emit_trigger.assert_called_once_with(
        "CounterAddedToCardEvent", 123, 7,
        event_tac={_tac_attr_hash("GainedCounterType"): "seed"})


if __name__ == "__main__":
    test_native_card_counter_gain_emits_counter_added_event()
    print("PASS counter event tests")
