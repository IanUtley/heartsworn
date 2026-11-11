"""The main page's deck templates: a deck saves only the copies the profile owns, and it round-trips."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database, isolated_db

fresh_database()   # bind this process's database before ``db`` is imported

import db as _dbmod  # noqa: E402
from web_client import profile  # noqa: E402


@isolated_db
def test_deck_saves_only_owned_copies_and_round_trips(monkeypatch):
    user_id = _dbmod._db.execute(
        "INSERT INTO users (name) VALUES ('deck tester')").lastrowid
    card = _dbmod._db.execute("SELECT guid FROM card_templates LIMIT 1").fetchone()[0]
    _dbmod._db.execute("INSERT INTO collections (user_id, card_template_id, quantity) VALUES (?, ?, 3)",
                (user_id, card))
    _dbmod._db.commit()
    monkeypatch.setenv("HEX_WEB_PROFILE_ID", str(user_id))

    saved = profile.save_deck({"name": "Probe", "cards": [{"guid": card, "quantity": 2}]})
    assert saved["cards"] == 2 and saved["valid"] is False   # below the minimum: a draft

    with pytest.raises(ValueError):
        profile.save_deck({"name": "Too many", "cards": [{"guid": card, "quantity": 4}]})

    deck = next(d for d in profile.decks() if d["id"] == saved["id"])
    assert deck["name"] == "Probe"
    assert deck["cards"] == [{"guid": card, "quantity": 2, "reserves": 0}]

    assert profile.delete_deck(saved["id"]) is True
    assert all(d["id"] != saved["id"] for d in profile.decks())


def _playable_champion():
    """A champion the client lets a player select and the engine can seed (see profile._PLAYABLE_CHAMPION)."""
    return _dbmod._db.execute(
        "SELECT LOWER(e.guid) FROM champion_templates_extended e WHERE e.is_selectable=1 AND EXISTS("
        "SELECT 1 FROM champion_abilities ca WHERE LOWER(ca.champion_guid)=LOWER(e.guid)) LIMIT 1").fetchone()[0]


@isolated_db
def test_saving_from_the_browser_keeps_what_the_client_wrote(monkeypatch):
    """A template the Unity client wrote (gems, flags, equipment, personality, sleeve) survives a browser save: its
    fields the request omits and the gems and flags of cards whose quantity is unchanged are kept as stored."""
    import json
    import profile_db
    user_id = _dbmod._db.execute(
        "INSERT INTO users (name) VALUES ('unity player')").lastrowid
    card = _dbmod._db.execute("SELECT guid FROM card_templates LIMIT 1").fetchone()[0]
    champion = _dbmod._db.execute("SELECT guid FROM champion_templates LIMIT 1").fetchone()[0]
    _dbmod._db.execute("INSERT INTO collections (user_id, card_template_id, quantity) VALUES (?, ?, 3)",
                       (user_id, card))
    _dbmod._db.commit()
    monkeypatch.setenv("HEX_WEB_PROFILE_ID", str(user_id))
    gems = json.dumps([["Fire"], []])
    flags = json.dumps({"extended": True, "copies": [{"reserves": False}, {"reserves": True}]})
    template_id = profile_db.db_save_profile_deck_template(
        user_id, None,
        {"name": "Unity deck", "champion_guid": champion, "sleeve_guid": "sleeve-1",
         "gameboard_guid": "board-1", "personality": "persona-1",
         "equipment_json": json.dumps({"Head": "helm"})},
        [(card, 2, gems, flags)])

    saved = profile.save_deck({"id": template_id, "name": "Renamed",
                               "cards": [{"guid": card, "quantity": 2}]})
    assert saved["id"] == template_id and saved["cards"] == 2

    stored = profile_db.db_profile_deck_template(user_id, template_id)
    assert stored["name"] == "Renamed"
    assert stored["personality"] == "persona-1"
    assert stored["equipment_json"] == json.dumps({"Head": "helm"})
    assert stored["sleeve_guid"] == "sleeve-1" and stored["gameboard_guid"] == "board-1"
    assert stored["cards"][0]["gem_types_list_json"] == gems
    assert stored["cards"][0]["flags_json"] == flags

    # Dropping a copy keeps the first copy's gems and flags.
    profile.save_deck({"id": template_id, "name": "Renamed", "cards": [{"guid": card, "quantity": 1}]})
    resized = profile_db.db_profile_deck_template(user_id, template_id)["cards"][0]
    assert json.loads(resized["gem_types_list_json"]) == [["Fire"]]
    assert json.loads(resized["flags_json"]) == {"extended": True, "copies": [{"reserves": False}]}


@isolated_db
def test_decks_are_listed_by_format_and_only_playable_champions_are_offered(monkeypatch):
    """A deck carries one format; the listing filters on it, a bad format is refused, and only champions the player
    may select and the engine can play are offered or accepted."""
    user_id = _dbmod._db.execute("INSERT INTO users (name) VALUES ('format tester')").lastrowid
    card = _dbmod._db.execute("SELECT guid FROM card_templates LIMIT 1").fetchone()[0]
    champion = _playable_champion()
    _dbmod._db.execute("INSERT INTO collections (user_id, card_template_id, quantity) VALUES (?, ?, 3)",
                       (user_id, card))
    _dbmod._db.execute("INSERT INTO champion_templates_extended (guid, name, race, champion_class, gender, "
                       "is_selectable, starting_health, faction) VALUES "
                       "('ffffffff-0000-0000-0000-000000000000', 'Hidden', 'x', 'y', '', 0, 20, '')")
    _dbmod._db.commit()
    monkeypatch.setenv("HEX_WEB_PROFILE_ID", str(user_id))
    cards = [{"guid": card, "quantity": 2}]

    standard = profile.save_deck({"name": "S", "format": "standard", "champion_guid": champion, "cards": cards})
    immortal = profile.save_deck({"name": "I", "format": "immortal", "champion_guid": champion, "cards": cards})
    assert [d["id"] for d in profile.decks("standard")] == [standard["id"]]
    assert {d["id"] for d in profile.decks()} >= {standard["id"], immortal["id"]}
    assert profile.deck(standard["id"])["format"] == "standard"

    # A save that omits the format keeps the stored one.
    profile.save_deck({"id": standard["id"], "name": "S renamed", "cards": cards})
    assert [d["id"] for d in profile.decks("standard")] == [standard["id"]]

    with pytest.raises(ValueError):
        profile.save_deck({"name": "bad", "format": "casual", "champion_guid": champion, "cards": cards})
    with pytest.raises(ValueError):
        profile.decks("casual")
    with pytest.raises(ValueError):
        profile.save_deck({"name": "not playable", "format": "standard",
                           "champion_guid": "ffffffff-0000-0000-0000-000000000000", "cards": cards})

    offered = {row["guid"] for row in profile.champions("standard")}
    assert champion in offered and "ffffffff-0000-0000-0000-000000000000" not in offered

    index = _dbmod._db.execute("SELECT name FROM sqlite_master WHERE type='index' "
                               "AND name='idx_player_deck_templates_user_format'").fetchone()
    assert index is not None


@isolated_db
def test_validate_reads_the_owners_copies_and_the_card_rules(monkeypatch):
    user_id = _dbmod._db.execute("INSERT INTO users (name) VALUES ('validator')").lastrowid
    card = _dbmod._db.execute("SELECT guid FROM card_templates WHERE is_pve=0 LIMIT 1").fetchone()[0]
    champion = _playable_champion()
    _dbmod._db.execute("INSERT INTO collections (user_id, card_template_id, quantity) VALUES (?, ?, 2)",
                       (user_id, card))
    _dbmod._db.commit()
    monkeypatch.setenv("HEX_WEB_PROFILE_ID", str(user_id))
    result = profile.validate_deck({"format": "pve", "champion_guid": champion,
                                    "cards": [{"guid": card, "quantity": 3}]})
    codes = {error["code"] for error in result["errors"]}
    assert result["valid"] is False
    assert {"TooFewCards", "NotOwned"} <= codes, result


@isolated_db
def test_reserves_are_saved_as_the_client_stores_them(monkeypatch):
    user_id = _dbmod._db.execute("INSERT INTO users (name) VALUES ('reserve tester')").lastrowid
    card = _dbmod._db.execute("SELECT guid FROM card_templates LIMIT 1").fetchone()[0]
    champion = _playable_champion()
    _dbmod._db.execute("INSERT INTO collections (user_id, card_template_id, quantity) VALUES (?, ?, 3)",
                       (user_id, card))
    _dbmod._db.commit()
    monkeypatch.setenv("HEX_WEB_PROFILE_ID", str(user_id))
    saved = profile.save_deck({"name": "Reserves", "format": "standard", "champion_guid": champion,
                               "cards": [{"guid": card, "quantity": 2, "reserve": False},
                                         {"guid": card, "quantity": 1, "reserve": True}]})
    view = profile.deck(saved["id"])
    assert view["cards"] == [{"guid": card, "quantity": 3, "reserves": 1}]

    profile.save_deck({"id": saved["id"], "name": "Reserves", "cards": [{"guid": card, "quantity": 3,
                                                                          "reserve": False}]})
    assert profile.deck(saved["id"])["cards"] == [{"guid": card, "quantity": 3, "reserves": 0}]


if __name__ == "__main__":
    # Written in pytest style: run through pytest so each test executes and failures
    # set the exit status. A missing pytest fails loudly.
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
