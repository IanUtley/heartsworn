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
    # Cliffs of Nausea (AZ2 Node028): a real deck whose unresolved card is mapped to
    # the Shards of Fate stand-in; the deck view names it and counts it as unresolved.
    import catalog
    db = catalog.connect(catalog.REPO / "hconnect.db")
    deck = catalog.encounter_deck(db, "2a68104b-7257-4f4c-92fd-a6416366f03b")
    mapped = [card for card in deck["cards"] if card["mapped"]]
    assert mapped and all(card["missing"] and card["display_guid"] == SHARD for card in mapped)
    assert all(card["name"] == "Shards of Fate" for card in mapped)
    assert deck["unresolved"] == sum(card["quantity"] for card in deck["cards"] if card["missing"])


def test_server_battle_deck_swaps_unresolved_cards_in_place():
    import pvp_db
    # A scene opponent deck with an unresolved card (mapped to the stand-in):
    # the battle read keeps every row, in order, with the card swapped.
    rows = pvp_db.db_encounter_deck_cards("039e2011-c53f-4fd4-bd21-20481887d7e2")
    assert len(rows) == 7
    assert rows[0][0] == SHARD
    assert all(len(row) == 3 for row in rows)


if __name__ == "__main__":
    # Written in pytest style: run through pytest so each test executes and failures
    # set the exit status. A missing pytest fails loudly.
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
