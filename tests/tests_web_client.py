"""Browser seat: prompts, captured events and hidden-zone filtering.

A ``WebMatch`` plays a real tournament PvP session against the server AI.
The browser only ever sees the packets captured for its own player at the
``Game.make_network_packet`` boundary, so the opponent's hand must arrive as
hidden cards while the browser's own hand carries real templates.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database

fresh_database()   # bind this process's database before ``db`` is imported

from web_client import match as web_match  # noqa: E402
from tests.test_db import isolated_db  # noqa: E402

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


def _bound_to_current_db(test):
    """Run a web test against the current test database.

    The web client reads the connection through ``db.conn()`` at call time, so it no longer needs rebinding. Modules
    that still capture ``_db`` at import (the autoplay module, for one) are rebound here, and an earlier test may have
    left them pointing at another database."""
    import functools
    import db as dbmod

    @functools.wraps(test)
    def run(*args, **kwargs):
        import sqlite3
        import sys
        current = dbmod._db
        # Every module that captured ``_db`` at import (the match and the autoplay module use it) points at the
        # current database for the test, and goes back afterwards.
        rebound = [(module, module._db) for module in list(sys.modules.values())
                   if isinstance(getattr(module, "_db", None), sqlite3.Connection) and module._db is not current]
        for module, _held in rebound:
            module._db = current
        try:
            return test(*args, **kwargs)
        finally:
            for module, held in rebound:
                module._db = held

    return run


def _new_human_first_match(decks, attempts=5):
    """A web match whose human seat takes the first turn. The server's coin flip depends on the session, so the match is
    created, its first prompt read, and it is replaced by the next slot's match when the AI would go first."""
    for _ in range(attempts):
        match = web_match.WebMatch(decks[0]["deck_guid"], decks[1]["deck_guid"], seed=11, ai_delay=0)
        snap, packets, after = _settle(match)
        if snap["prompt"]["kind"] == "goes_first":
            return match, snap, packets, after
        match.close()
    raise AssertionError("no slot gave the human seat the first turn")


def _with_human_first(test):
    """Run a web test on a match whose human seat takes the first turn (the coin flip depends on the session)."""
    import functools

    @functools.wraps(test)
    def run(*args, **kwargs):
        previous_slots = web_match._slots
        web_match._slots = _human_goes_first_slots()
        try:
            return test(*args, **kwargs)
        finally:
            web_match._slots = previous_slots

    return run


@_bound_to_current_db
def test_web_seat_gets_its_options_and_never_the_opponents_hand():
    decks = _ensure_two_decks()
    assert len(decks) >= 2, "no complete encounter decks to play"
    match, snap, packets, after = _new_human_first_match(decks)
    try:
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


def _human_goes_first_slots():
    """A slot counter whose next match has the human seat going first. The server's coin flip is a hash of the session
    and its players, so the first player depends on the session the match gets."""
    import hashlib
    import itertools
    # Start after the slots earlier matches in this process already used, so the session ids are new.
    start = next(web_match._slots) + 1
    for k in range(start, start + 1000):
        slot = web_match.WEB_RUN_SLOT_BASE + k
        session_id, pids = web_match.autoplay._autoplay_identity(slot, slot)
        if hashlib.md5(f"{int(session_id)}:{pids[0]}:{pids[1]}".encode()).digest()[0] & 1:
            return itertools.count(k)
    raise RuntimeError("no slot gives the human seat the first turn")


@_bound_to_current_db
def test_shards_of_fate_offers_the_web_seat_a_threshold_pick():
    _shards_of_fate_threshold_pick()


def _shards_of_fate_threshold_pick():
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
    match, snap, packets, after = _new_human_first_match(decks)
    try:
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


@_bound_to_current_db
def test_a_saved_deck_starts_a_match_and_an_unplayable_one_is_refused():
    """A saved deck the rules accept plays a match; one that breaks them (too few cards) is refused with the reason."""
    import os
    from db import _db
    from web_client import profile
    decks = _ensure_two_decks()
    user_id = _db.execute("INSERT INTO users (name) VALUES ('saved player')").lastrowid
    names = _db.execute(
        "SELECT MIN(guid) FROM card_templates WHERE is_pve=0 AND card_type NOT LIKE '%Resource%' "
        "GROUP BY name LIMIT 15").fetchall()
    champion = _db.execute(
        "SELECT LOWER(e.guid) FROM champion_templates_extended e WHERE e.is_selectable=1 AND EXISTS("
        "SELECT 1 FROM champion_abilities ca WHERE LOWER(ca.champion_guid)=LOWER(e.guid)) LIMIT 1").fetchone()[0]
    assert len(names) == 15 and champion, "the test database lacks the cards or champion a deck needs"
    for (guid,) in names:
        _db.execute("INSERT INTO collections (user_id, card_template_id, quantity) VALUES (?, ?, 4)", (user_id, guid))
    _db.commit()
    previous = os.environ.get("HEX_WEB_PROFILE_ID")
    os.environ["HEX_WEB_PROFILE_ID"] = str(user_id)
    try:
        short = profile.save_deck({"name": "Short", "format": "immortal", "champion_guid": champion,
                                   "cards": [{"guid": names[0][0], "quantity": 4}]})
        with pytest.raises(ValueError, match="TooFewCards"):
            profile.play_spec(short["id"])
        full = profile.save_deck({"name": "Full", "format": "immortal", "champion_guid": champion,
                                  "cards": [{"guid": guid, "quantity": 4} for (guid,) in names]})
        assert profile.play_spec(full["id"]) is not None
        match = web_match.WebMatch(None, decks[1]["deck_guid"], seed=11, ai_delay=0,
                                   player_spec=profile.play_spec(full["id"]))
        try:
            snap, _packets, _after = _settle(match)
            assert snap["error"] is None, snap["error"]
            assert snap["prompt"]["kind"] in ("goes_first", "mulligan", "priority"), snap["prompt"]
        finally:
            match.close()
    finally:
        if previous is None:
            os.environ.pop("HEX_WEB_PROFILE_ID", None)
        else:
            os.environ["HEX_WEB_PROFILE_ID"] = previous


def test_missing_session_stops_match_with_error():
    """A lost battle session stops the match: the error is recorded and no further action is accepted."""
    import threading
    from unittest import mock

    match = object.__new__(web_match.WebMatch)
    match.human_pid = 7
    match.slot = 1
    match.cond = threading.Condition()
    match.busy = True
    match.error = None
    match.stopped = False
    match.ended = False
    with mock.patch.object(web_match.gs, "find_session_by_player", return_value=None), \
         mock.patch.object(web_match.autoplay, "log_req", return_value=True):
        try:
            match._session()
        except web_match.MatchSessionLost:
            match._fail()
    assert match.stopped is True
    assert match.error.startswith("match stopped:")
    assert match.busy is False
    with pytest.raises(web_match.ActionError):
        match.submit({"kind": "pass"})


def test_a_two_target_trigger_takes_two_cards_and_other_choices_take_one():
    """Crocosaur's Deploy names two opposing troops (min 2, max 2): the web choice takes two cards, others take one."""
    trigger = {"target_entries": [{"minimum": 2, "maximum": 2}]}
    assert web_match.WebMatch._pick_bounds("pending_trigger", trigger) == (2, 2)
    assert web_match.WebMatch._pick_bounds("pending_choice", {}) == (1, 1)


@_bound_to_current_db
def test_a_start_of_game_choice_is_asked_before_the_first_turn():
    """A champion whose start-of-game trigger makes each champion choose a card in hand (Blood Sphinx) asks the web
    seat before the first turn, asks the AI seat too, and leaves both chosen cards with the granted ability.

    The triggers used to stay on the PvP stack (the RulesPort chain never held them), so the opening passes ran whole
    turns instead; the second champion's choice was dropped when the first paused; and the grant was removed at the
    next phase because a PvP champion's card row lists none of its abilities. A seat holding a single action was not
    asked at all: the one candidate was taken silently, so the player never saw the choice.
    """
    from db import _db
    from pvp_db import db_get_card_abilities
    from gamedata import DEFAULT_RECORD_STORE, ability_graph
    # Found from the authored data: a GameStarted champion trigger that invokes a child which grants an ability to a
    # card the player picks.
    rows = _db.execute(
        "SELECT ca.champion_guid, inv.param, g.param FROM champion_abilities ca "
        "JOIN card_abilities_meta cam ON cam.ability_guid=ca.ability_guid "
        "JOIN ability_effects inv ON inv.ability_guid=ca.ability_guid "
        "AND inv.effect_type='ActivateAbilityEffectTemplate' "
        "JOIN ability_effects g ON g.ability_guid=inv.param AND g.effect_type='GrantAbilityEffectTemplate' "
        "WHERE cam.trigger_event_type LIKE '%GameStartedEvent' ORDER BY ca.champion_guid").fetchall()
    trigger = next((row for row in rows if any(
        target.requires_input and not target.is_auto
        for target in getattr(ability_graph(DEFAULT_RECORD_STORE, str(row[1]).lower()), "targets", ()) or ())), None)
    assert trigger, "no champion with a start-of-game hand choice in the seed"
    action = _db.execute(
        "SELECT guid, card_type FROM card_templates WHERE card_type IN ('BasicAction', 'QuickAction') "
        "AND is_pve=0 ORDER BY guid LIMIT 1").fetchone()
    decks = _ensure_two_decks()
    previous = _db.execute("SELECT champion_guid FROM fra_encounters WHERE deck_guid=?",
                           (decks[0]["deck_guid"],)).fetchone()[0]
    _db.execute("UPDATE fra_encounters SET champion_guid=? WHERE deck_guid=?", (trigger[0], decks[0]["deck_guid"]))
    _db.commit()
    match = None
    try:
        match, snap, packets, after = _new_human_first_match(web_match.deck_choices())

        def hand(pid):
            return [int(row[0]) for row in _db.execute(
                "SELECT card_uid FROM game_cards WHERE session_id=? AND user_id=? AND location='hand' "
                "ORDER BY position", (match.session_id, pid)).fetchall()]

        def granted():
            return {int(row[0]) for row in _db.execute(
                "SELECT card_uid FROM game_cards WHERE session_id=? AND location='hand' AND card_abilities LIKE ?",
                (match.session_id, f"%{str(trigger[2]).lower()}%")).fetchall()}

        abilities = db_get_card_abilities(action[0].lower())[0]
        forced = False
        for _ in range(6):
            kind = snap["prompt"]["kind"]
            if kind == "mulligan" and not forced:
                # The web seat holds one action (still asked: a pick from hand is never taken silently) and the AI
                # seat two.
                for pid in (match.human_pid, match.ai_pid):
                    for uid in hand(pid)[:1 if pid == match.human_pid else 2]:
                        _db.execute(
                            "UPDATE game_cards SET template_guid=?, card_template_id=?, card_type=?, "
                            "card_abilities=? WHERE session_id=? AND card_uid=?",
                            (action[0].lower(), action[0].lower(), action[1], abilities, match.session_id, uid))
                _db.commit()
                forced = True
            if kind not in ("goes_first", "mulligan"):
                break
            match.submit({"type": "play_first" if kind == "goes_first" else "keep"})
            snap, more, after = _settle(match, after)
        assert forced, "the mulligan prompt never came"
        assert snap["error"] is None, snap["error"]

        # The choice is the first thing the web seat is asked: turn 1, both hands still seven cards.
        prompt = snap["prompt"]
        mine = hand(match.human_pid)
        assert prompt["kind"] == "choose" and prompt["pending"] == "pending_trigger", prompt
        assert (prompt["turn_number"], prompt["min_picks"], prompt["max_picks"]) == (1, 1, 1), prompt
        assert prompt["candidates"] == mine[:1], (prompt, mine)
        assert (len(mine), len(hand(match.ai_pid))) == (7, 7)

        match.submit({"type": "choose", "card": prompt["candidates"][0]})
        snap, more, after = _settle(match, after)
        assert snap["error"] is None, snap["error"]
        # The AI seat chose as well, nothing is left on the chain, and it is still the first turn.
        state = match._state()
        assert snap["prompt"]["kind"] == "priority" and snap["prompt"]["turn_number"] == 1, snap["prompt"]
        assert not state.get("stack") and not state.get("pvp_game_start_chain"), state.get("stack")
        chosen = granted()
        assert prompt["candidates"][0] in chosen and len(chosen) == 2, chosen
        assert len(chosen & set(hand(match.ai_pid))) == 1, chosen

        # A champion's grant lasts while the champion is in play: it survives the next phase.
        match.submit({"type": "pass"})
        snap, more, after = _settle(match, after)
        assert snap["error"] is None, snap["error"]
        assert granted() == chosen, (granted(), chosen)
    finally:
        if match is not None:
            match.close()
        _db.execute("UPDATE fra_encounters SET champion_guid=? WHERE deck_guid=?", (previous, decks[0]["deck_guid"]))
        _db.commit()
