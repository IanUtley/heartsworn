"""An encounter deck's unresolved cards show as the card map says, else as ??????."""

import os
import sqlite3
import sys
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path[:0] = [ROOT, os.path.join(ROOT, "campaign"), os.path.join(ROOT, "campaign_editor")]

import catalog  # noqa: E402

SHARD = "00e13fdf-b2c3-4fe7-a064-ce4481b24e8d"
REAL = "11111111-1111-4111-8111-111111111111"
MAPPED = "22222222-2222-4222-8222-222222222222"
UNMAPPED = "33333333-3333-4333-8333-333333333333"
DECK = "44444444-4444-4444-8444-444444444444"
SCENE = "55555555-5555-4555-8555-555555555555"


def test_unresolved_cards_follow_the_card_map():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE card_templates (guid TEXT, name TEXT, cost INTEGER, "
               "card_type TEXT, attack INTEGER, defense INTEGER, threshold_json TEXT)")
    db.execute("CREATE TABLE encounter_scenes (guid TEXT, name TEXT, title TEXT, "
               "ai_deck_guid TEXT)")
    db.execute("CREATE TABLE encounter_deck_cards (deck_guid TEXT, card_guid TEXT, "
               "quantity INTEGER)")
    db.executemany("INSERT INTO card_templates VALUES (?,?,?,?,?,?,?)", [
        (REAL, "Real Card", 2, "Troop", 1, 2, "{}"),
        (SHARD, "Shards of Fate", 0, "Resource", None, None, "{}"),
    ])
    db.execute("INSERT INTO encounter_scenes VALUES (?,?,?,?)", (SCENE, "scene", "Scene", DECK))
    db.executemany("INSERT INTO encounter_deck_cards VALUES (?,?,?)", [
        (DECK, REAL, 3), (DECK, MAPPED, 2), (DECK, UNMAPPED, 1)])
    card_map = {MAPPED: SHARD}
    with mock.patch.object(catalog.campaign_data, "load_card_map", return_value=card_map):
        deck = catalog.encounter_deck(db, SCENE)

    by_guid = {card["guid"]: card for card in deck["cards"]}
    assert by_guid[REAL]["name"] == "Real Card" and not by_guid[REAL]["missing"]
    assert by_guid[MAPPED]["mapped"] and by_guid[MAPPED]["name"] == "Shards of Fate"
    assert by_guid[MAPPED]["display_guid"] == SHARD and by_guid[MAPPED]["missing"]
    assert by_guid[UNMAPPED]["name"] == "??????" and not by_guid[UNMAPPED]["mapped"]
    assert deck["unresolved"] == 3
    assert deck["card_count"] == 6


def test_server_battle_deck_swaps_unresolved_cards_in_place():
    import campaign_source
    import pvp_db
    campaign_source.ensure_campaign_path()
    import campaign_data

    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE encounter_deck_cards (deck_guid TEXT, card_guid TEXT, "
               "quantity INTEGER, gem_types_new_list_json TEXT)")
    db.executemany("INSERT INTO encounter_deck_cards VALUES (?,?,?,?)", [
        (DECK, REAL, 3, "[]"), (DECK, MAPPED, 2, '[["Ruby"]]')])
    with mock.patch.object(campaign_data, "load_card_map", return_value={MAPPED: SHARD}):
        rows = pvp_db.db_encounter_deck_cards(DECK, conn=db)
    assert rows == [(REAL, 3, "[]"), (SHARD, 2, '[["Ruby"]]')]
