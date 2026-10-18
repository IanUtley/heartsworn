"""Browser seat: prompts, captured events and hidden-zone filtering.

A ``WebMatch`` plays a real tournament PvP session against the server AI.
The browser only ever sees the packets captured for its own player at the
``Game.make_network_packet`` boundary, so the opponent's hand must arrive as
hidden cards while the browser's own hand carries real templates.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database

fresh_database()   # bind this process's database before ``db`` is imported

from web_client import match as web_match  # noqa: E402

HAND = 2
NO_GUID = "00000000-0000-0000-0000-000000000000"


def _ensure_two_decks():
    """The metadata baseline has encounter decks but no FRA catalogue; list
    two complete encounter decks there the way the bootstrap repair does."""
    from db import _db
    decks = web_match.deck_choices()
    if len(decks) >= 2:
        return decks
    rows = _db.execute(
        "SELECT s.ai_deck_guid, s.name, s.ai_champion_guid "
        "FROM encounter_scenes s JOIN encounter_deck_cards d "
        "ON d.deck_guid=s.ai_deck_guid "
        "WHERE COALESCE(s.ai_champion_guid, '')<>'' "
        "GROUP BY s.ai_deck_guid HAVING SUM(d.quantity) >= 40 "
        "ORDER BY s.ai_deck_guid LIMIT 2").fetchall()
    for deck_guid, name, champion_guid in rows:
        _db.execute(
            "INSERT OR IGNORE INTO fra_encounters (deck_guid, deck_name, "
            "name, champion_guid, base_deck_name) VALUES (?, ?, ?, ?, ?)",
            (deck_guid, name, name, champion_guid, name))
    _db.commit()
    return web_match.deck_choices()


def _settle(match, after=0):
    packets = []
    while True:
        snap = match.snapshot(after, wait=60)
        packets.extend(snap["packets"])
        after += len(snap["packets"])
        if not snap["busy"]:
            return snap, packets, after


def test_web_seat_gets_its_options_and_never_the_opponents_hand():
    decks = _ensure_two_decks()
    assert len(decks) >= 2, "no complete encounter decks to play"
    match = web_match.WebMatch(decks[0]["deck_guid"], decks[1]["deck_guid"],
                               seed=11, ai_delay=0)
    try:
        snap, packets, after = _settle(match)
        for _ in range(6):
            kind = snap["prompt"]["kind"]
            if kind not in ("goes_first", "mulligan"):
                break
            match.submit({"type": "play_first" if kind == "goes_first"
                          else "keep"})
            snap, more, after = _settle(match, after)
            packets.extend(more)
        assert snap["error"] is None, snap["error"]
        assert snap["prompt"]["kind"] in ("priority", "attack", "defend",
                                          "choose"), snap["prompt"]

        info = snap["match"]
        events = [e for p in packets for e in p.get("events", ())]
        assert any(e["type"] == "PlayerOptionList" and
                   e["player_id"] == info["player_uid"] for e in events)
        latest = {}
        for event in events:
            if event["type"] == "CardUpdated":
                latest[event["session_card_id"]] = event
        mine = [e for e in latest.values()
                if e["collection"] == HAND and
                e["controller"] == info["player_uid"]]
        theirs = [e for e in latest.values()
                  if e["collection"] == HAND and
                  e["controller"] == info["opponent_uid"]]
        assert mine and all(e["card_id"] != NO_GUID for e in mine)
        assert theirs and all(e["card_id"] == NO_GUID for e in theirs)
        assert not any(e["type"] == "PlayerOptionList" and
                       e["player_id"] == info["opponent_uid"]
                       for e in events)
    finally:
        match.close()


if __name__ == "__main__":
    test_web_seat_gets_its_options_and_never_the_opponents_hand()
    print("ok")
