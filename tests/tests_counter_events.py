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


DAZED_COUNTER_GUID = "9c98dace-4cac-0fda-8242-f7ef2c1d3cf0"


def test_counter_changes_follow_the_csharp_counter_modifier():
    """A removal clamps at zero; a change that leaves the count as it is writes and projects nothing;
    and a Dazed add the card blocks with CantAddDazedCounters is a no-op."""
    context = SimpleNamespace(
        db=mock.Mock(), bstate={}, session=SimpleNamespace(session_id=1),
        _emit_trigger=mock.Mock())
    with mock.patch.object(counter_effects, "_champion_owner", return_value=None), \
            mock.patch("pvp_db.db_card_mutation_field", return_value='{"counters": {"dazed": 2}}'), \
            mock.patch("pvp_db.db_set_card_mutation_field") as write, \
            mock.patch("pvp_db.db_card_owner_id", return_value=7), \
            mock.patch.object(counter_effects, "_project") as project:
        assert counter_effects.change_counter(
            context, 123, "dazed", DAZED_COUNTER_GUID, 5, "remove") == (2, 0)
        assert counter_effects.change_counter(
            context, 123, "dazed", DAZED_COUNTER_GUID, 0, "add") == (2, 2)
        with mock.patch("rules_port.damage_effects._int_attr", return_value=1):
            assert counter_effects.change_counter(
                context, 123, "dazed", DAZED_COUNTER_GUID, 1, "add") == (2, 2)
    # Only the clamped removal writes and projects; the two no-ops do neither.
    write.assert_called_once()
    project.assert_called_once()


if __name__ == "__main__":
    test_native_card_counter_gain_emits_counter_added_event()
    test_counter_changes_follow_the_csharp_counter_modifier()
    print("PASS counter event tests")
