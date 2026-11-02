"""The main page's deck templates: a deck saves only the copies the profile owns, and it round-trips."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database

fresh_database()   # bind this process's database before ``db`` is imported

from db import _db  # noqa: E402
from web_client import profile  # noqa: E402


def test_deck_saves_only_owned_copies_and_round_trips(monkeypatch):
    user_id = _db.execute(
        "INSERT INTO users (name) VALUES ('deck tester')").lastrowid
    card = _db.execute("SELECT guid FROM card_templates LIMIT 1").fetchone()[0]
    _db.execute("INSERT INTO collections (user_id, card_template_id, quantity) VALUES (?, ?, 3)",
                (user_id, card))
    _db.commit()
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
