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
    import campaign_source
    campaign_source.ensure_campaign_path()
    import campaign_content
    totals = {}
    for card in campaign_content.table("encounter_deck_cards"):
        deck = str(card["deck_guid"]).lower()
        totals[deck] = totals.get(deck, 0) + int(card["quantity"] or 0)
    rows = []
    seen = set()
    for scene in sorted(campaign_content.scenes(), key=lambda r: str(r.get("ai_deck_guid") or "")):
        deck_guid, name, champion_guid = scene.get("ai_deck_guid"), scene.get("name"), scene.get("ai_champion_guid")
        if not str(champion_guid or ""):
            continue
        key = str(deck_guid).lower()
        if key in seen or totals.get(key, 0) < 40:
            continue
        seen.add(key)
        rows.append((deck_guid, name, champion_guid))
        if len(rows) == 2:
            break
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


def test_shards_of_fate_offers_the_web_seat_a_threshold_pick():
    """Playing Shards of Fate in a PvP session (the web seat's) must resolve to a threshold pick, not abort.

    The opening hand is forced to hold Shards of Fate (a Resource whose abilities are the template's), so the
    play is deterministic. The granted-ability resolver used to fail on its ``target_map`` keyword, which left the
    pick unopened.
    """
    from db import _db
    from pvp_db import db_get_card_abilities
    shards_of_fate = _db.execute(
        "SELECT guid FROM card_templates WHERE name='Shards of Fate' LIMIT 1").fetchone()[0].lower()
    decks = _ensure_two_decks()
    match = web_match.WebMatch(decks[0]["deck_guid"], decks[1]["deck_guid"],
                               seed=11, ai_delay=0)
    try:
        snap, packets, after = _settle(match)
        forced = None
        for _ in range(6):
            kind = snap["prompt"]["kind"]
            if kind == "mulligan" and forced is None:
                uid = _db.execute(
                    "SELECT card_uid FROM game_cards WHERE session_id=? AND user_id=? "
                    "AND location='hand' ORDER BY position LIMIT 1",
                    (match.session_id, match.human_pid)).fetchone()[0]
                _db.execute(
                    "UPDATE game_cards SET template_guid=?, card_template_id=?, card_type='Resource', "
                    "card_abilities=? WHERE session_id=? AND card_uid=?",
                    (shards_of_fate, shards_of_fate, db_get_card_abilities(shards_of_fate)[0],
                     match.session_id, uid))
                _db.commit()
                forced = int(uid)
            if kind not in ("goes_first", "mulligan"):
                break
            match.submit({"type": "play_first" if kind == "goes_first" else "keep"})
            snap, more, after = _settle(match, after)
            packets.extend(more)
        assert forced is not None, "the mulligan prompt never came"

        match.submit({"type": "play", "card": forced})
        snap, more, after = _settle(match, after)
        packets.extend(more)
        assert snap["error"] is None, snap["error"]
        prompt = snap["prompt"]
        assert prompt["kind"] == "choose" and prompt["pick_kind"] == "threshold", prompt
        candidates = prompt["candidates"]
        assert candidates, "no Standard resource offered"

        match.submit({"type": "choose", "card": candidates[0]})
        snap, more, after = _settle(match, after)
        packets.extend(more)
        assert snap["error"] is None, snap["error"]
        assert snap["prompt"]["kind"] == "priority", snap["prompt"]
        events = [e for p in packets for e in p.get("events", ())]
        assert any(e["type"] == "PlayerResourceThresholdChanged" for e in events)
    finally:
        match.close()
