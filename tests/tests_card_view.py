"""The static-filter card view: its fields, the permanent-buffs override and the champion entry."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from rules_port.card_view import CardView  # noqa: E402

ROW = (101, "Troop", "warzone", 5, 4, 3, 2, "Plain", None, "Robot",
       json.dumps({"list": [5]}), 1, 2)


def test_row_maps_to_the_static_filter_mapping():
    view = CardView.from_static_row(ROW)
    assert view.as_mapping() == {
        "card_uid": 101, "card_type": "Troop", "location": "warzone", "user_id": 5,
        "state": 4, "attack": 3, "defense": 2, "name": "Plain", "cost": 0,
        "subtype": "Robot", "shards": [64], "attributes": 3,
        "damaged_opponent_this_turn": [], "src_owner_side": "player",
    }


def test_permanent_buffs_override_subtype_and_thresholds_only():
    view = CardView.from_static_row(ROW)
    changed = view.with_permanent_buffs(json.dumps({"subtype": "Dwarf", "thresholds": [1, 4]}))
    assert (changed.subtype, changed.shards) == ("Dwarf", [1, 4])
    assert changed.attack == view.attack and changed.cost == view.cost
    assert view.with_permanent_buffs("not json") is view


def test_champion_entry_keeps_its_side_and_health():
    ai = CardView.champion("ai", 0, 13).as_mapping()
    assert (ai["card_uid"], ai["defense"], ai["src_owner_side"]) == (-1, 13, "ai")
    player = CardView.champion("player", 5, 17).as_mapping()
    assert (player["card_uid"], player["user_id"], player["src_owner_side"]) == (-2, 5, "player")


COND_ROW = (202, "Troop", "hand", 6, 1, 3, 2, "tpl-guid", "Named", 2, "Dwarf",
            json.dumps({"list": [5]}), 4, 8, 1, -1, "{}", "[]", 3, "orig-guid", 7, "Rare", 9)


def test_condition_card_adds_the_instance_modifiers_and_condition_fields():
    view = CardView.from_condition_row(
        COND_ROW, buffs={"int_attrs": {"Lethal": 1}, "tags": {"t": 1}}, card_abilities=["a"],
        counters={"c": 2}, counter_guids={"c": "g"}, effective_cost=5,
        damaged_opponent_this_turn=[7])
    mapping = view.as_condition_mapping()
    assert mapping["attack"] == 4 and mapping["defense"] == 1      # template + modifiers
    assert mapping["cost"] == 5 and mapping["owner_id"] == mapping["controller_id"] == 6
    assert mapping["socket_count"] == 9 and mapping["gems"] == 7 and mapping["position"] == 3
    assert mapping["int_attrs"] == {"Lethal": 1} and mapping["tags"] == {"t": 1}
    assert mapping["damaged_opponent_this_turn"] == [7]
    assert "src_owner_side" not in mapping and "state" in mapping


def test_condition_champion_has_its_own_key_set():
    entry = CardView.from_condition_champion(10001, 5, None, 17, {}, {}, {}).as_condition_mapping()
    assert entry["card_type"] == "Champion" and entry["location"] == "champions"
    assert entry["name"] == "Champion" and entry["defense"] == 17
    assert "state" not in entry and "damaged_opponent_this_turn" not in entry


def test_every_shape_returns_exactly_its_keys():
    from rules_port.card_view import SHAPES
    view = CardView.from_projected_row(
        (7, "Troop", "warzone", 5, "tpl", 0, 3, 2, "Name", 2, "Robot", "{}", "[]", "{}",
         "Common", 0, 0, "", 0))
    for shape, keys in SHAPES.items():
        expected = set(keys) - ({"src_owner_id"} if shape == "scene_card" else set())
        assert set(view.as_shape(shape)) == expected, shape
    assert "thresholds" not in view.as_shape("projected")
    effective = view.with_effective([64], "Dwarf", (9, 8))
    projected = effective.as_shape("projected")
    assert projected["thresholds"] == [64] and projected["attack"] == 9
    assert projected["subtype"] == "Dwarf"


if __name__ == "__main__":
    test_every_shape_returns_exactly_its_keys()
    test_row_maps_to_the_static_filter_mapping()
    test_permanent_buffs_override_subtype_and_thresholds_only()
    test_champion_entry_keeps_its_side_and_health()
    test_condition_card_adds_the_instance_modifiers_and_condition_fields()
    test_condition_champion_has_its_own_key_set()
    print("PASS card view")
