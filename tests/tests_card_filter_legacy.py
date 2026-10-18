"""Characterization tests for the legacy CardFilter fallback in
``evaluate_card_filter``.  The native port owns filter semantics by default;
these pin the fallback branches so the dispatch can be restructured without
changing results.  ``HEX_LEGACY_FILTERS_PORT=0`` forces the fallback path."""

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database

SRC = fresh_database()

import game_engine
from abilities.framework.targeting import evaluate_card_filter


def _legacy(card, filter_json, **kwargs):
    with mock.patch.dict(os.environ, {"HEX_LEGACY_FILTERS_PORT": "0"}):
        return evaluate_card_filter(card, filter_json, kwargs.pop("source_uid", 1),
                                    **kwargs)


def test_boolean_combinators():
    troop = {"card_uid": 5, "card_type": "Troop"}
    is_troop = {"_t": "IsTroop"}
    is_hero = {"_t": "IsHero"}
    assert _legacy(troop, {"_t": "AndCardFilter",
                           "m_TargetFilters": [is_troop, {"_t": "NotCardFilter",
                                                          "m_TargetFilter": is_hero}]})
    assert _legacy(troop, {"_t": "OrCardFilter", "m_TargetFilters": [is_hero, is_troop]})
    assert not _legacy(troop, {"_t": "OrCardFilter", "m_TargetFilters": [is_hero]})
    assert _legacy(troop, {"_t": "AndCardFilter", "m_TargetFilters": []})


def test_non_dict_filter_matches_everything():
    assert _legacy({"card_uid": 5}, None)


def test_composite_card_type_matches_any_component():
    argus = {"card_uid": 5, "card_type": "Troop|Artifact"}
    assert _legacy(argus, {"_t": "IsType", "m_CardType": "Artifact"})
    assert _legacy(argus, {"_t": "IsNotType", "m_CardType": "Resource"})
    assert not _legacy(argus, {"_t": "IsNotType", "m_CardType": "Troop"})


def test_subtype_and_rarity_are_case_insensitive():
    card = {"card_uid": 5, "subtype": "Beast  Spirit", "rarity": "Rare"}
    assert _legacy(card, {"_t": "IsSubType", "m_SubType": "spirit"})
    assert _legacy(card, {"_t": "IsSubType", "m_SubType": ""})
    assert _legacy(card, {"_t": "IsRarity", "m_Rarity": "RARE"})
    assert not _legacy(card, {"_t": "IsRarity", "m_Rarity": "Common"})


def test_socketable_comparison_ops():
    card = {"card_uid": 5, "socket_count": 2}
    cases = {"GreaterThanOrEqual": True, "LessThanOrEqual": True,
             "Equal": True, "GreaterThan": False, "LessThan": False}
    for op, expected in cases.items():
        assert _legacy(card, {"_t": "IsSocketable", "m_ComparisonOp": op,
                              "m_SocketValue": 2}) is expected, op


def test_int_attr_absent_defaults_to_zero_and_can_compare_to_cost():
    untamed = {"card_uid": 5, "int_attrs": {}}
    tame_filter = {"_t": "IntAttrFilter", "m_Attribute": "Tamed",
                   "m_ComparisonOp": "GreaterThanOrEqual", "m_Value": 1}
    assert not _legacy(untamed, tame_filter)
    priced = {"card_uid": 5, "cost": 3, "int_attrs": {"Spent": 3}}
    assert _legacy(priced, {"_t": "IntAttrFilter", "m_Attribute": "Spent",
                            "m_CompareToCost": True, "m_ComparisonOp": "Equals"})


def test_attribute_flags_all_and_any():
    flight = getattr(game_engine.ECardAttributes, "Flight")
    shield = getattr(game_engine.ECardAttributes, "SpellShield")
    card = {"card_uid": 5, "attributes": flight}
    all_filter = {"_t": "HasAllAttributeFlags", "m_CardAttributeFlags": "Flight|SpellShield"}
    any_filter = {"_t": "HasAnyAttributeFlags", "m_CardAttributeFlags": "Flight|SpellShield"}
    assert not _legacy(card, all_filter)
    assert _legacy(card, any_filter)
    assert _legacy({"card_uid": 5, "attributes": flight | shield}, all_filter)
    assert _legacy(card, {"_t": "HasAllAttributeFlags", "m_CardAttributeFlags": ""})


def test_name_and_controller_filters():
    card = {"card_uid": 5, "name": "Pet Stag", "user_id": 7, "src_owner_id": 7}
    assert _legacy(card, {"_t": "HasName", "m_Name": "pet stag"})
    assert _legacy(card, {"_t": "NameContainsFilter", "m_ContainsString": "STAG"})
    assert _legacy(card, {"_t": "IsControlledBy"}, source_uid=1)
    assert not _legacy(card, {"_t": "IsNotControlledBy"})
    assert _legacy(card, {"_t": "IsAbilitySource"}, source_uid=5)


def test_stored_name_uses_last_entry_and_empty_is_wildcard():
    card = {"card_uid": 5, "name": "Pet Stag"}
    stored = {"_t": "HasName", "m_UseStoredName": True}
    assert _legacy(card, stored, stored_names=["Other", "Pet Stag"])
    assert _legacy(card, stored, stored_names=[])
    assert _legacy({"card_uid": 5}, {"_t": "HasName", "m_Name": ""})


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print("Card filter legacy tests passed")
