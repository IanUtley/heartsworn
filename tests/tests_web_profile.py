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
    assert deck["cards"] == [{"guid": card, "quantity": 2}]

    assert profile.delete_deck(saved["id"]) is True
    assert all(d["id"] != saved["id"] for d in profile.decks())


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


if __name__ == "__main__":
    # Written in pytest style: run through pytest so each test executes and failures
    # set the exit status. A missing pytest fails loudly.
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
