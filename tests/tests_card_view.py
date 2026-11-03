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


if __name__ == "__main__":
    test_row_maps_to_the_static_filter_mapping()
    test_permanent_buffs_override_subtype_and_thresholds_only()
    test_champion_entry_keeps_its_side_and_health()
    print("PASS card view")
