"""A PvP chain item resolves against the persisted checkpoint, not the side-oriented effect view.

Two live browser-match symptoms came from code that assumed the view:

* Chlorophyllia ("Play a random Wild Shard from your deck") moved the shard, and the champion panel showed no gain.
  The free play added to the view's ``player_*`` pools, which a raw checkpoint does not have, so the real pools
  (``res_<pid>``) never changed and the events reported pools counted up from zero.
* Inquisition ("Choose a revealed non-resource card. They discard that card.") asked for a discard once per card in
  the hand. The pick's continuation ran on a view without the paused chain item's marker, so the item was never
  marked completed and the next chain pass ran the whole ability again.
"""

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database

fresh_database()

from tests.tests_crap_top40 import _pvp_session  # noqa: E402
from tests.tests_combat import make_db, add_card, TPL_GLADIATOR  # noqa: E402

WILD_SHARD = "cd41bd00-7585-4762-a721-6163bdaee3c3"
WILD = 32   # the Wild threshold flag
DIAMOND = 8
# Inquisition's invoked child: one SourceRevealed pick, then DiscardCard on it.
INQUISITION_CHILD = "15223813-202e-6864-a891-b12262831365"


def _bound(db):
    import db as shared
    import hconnect_server
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
    handler.user_profile = {"id": 5}
    handler.client_reck_id = 5
    handler.send = lambda header, body: None
    return shared, previous, handler


def test_free_resource_play_adds_to_the_raw_pvp_pools():
    import game_engine
    from rules_port.host_mutations import play_free_resource_card
    db = make_db()
    shared, previous, handler = _bound(db)
    try:
        add_card(db, 702, 5, WILD_SHARD, "deck")
        db.execute("UPDATE game_cards SET card_type='Resource' WHERE card_uid=702")
        db.commit()
        session = _pvp_session(db)
        state = session._rules_port_battle_state
        state.update({"pids": [5, 6], "champ_map": {"5": 2304257, "6": 4864257},
                      "res_5": 2, "res_total_5": 4, "chg_5": 1, # A checkpoint read back from JSON keys its thresholds by text.
                      "thresh_5": {str(DIAMOND): 1, str(WILD): 1}})
        player, opponent = game_engine.UID.make(244, 5), game_engine.UID.make(244, 6)
        game = game_engine.Game(int(session.session_id), player, opponent)
        play_free_resource_card(handler, game, session, db, player, opponent, state, 702)

        assert (state["res_5"], state["res_total_5"], state["chg_5"]) == (3, 5, 2), state
        assert {int(flag): count for flag, count in state["thresh_5"].items()} == {DIAMOND: 1, WILD: 2}, \
            state["thresh_5"]
        assert "player_resources" not in state, "the free play wrote view keys into the raw checkpoint"
        updated = [e for e in game.events if type(e).__name__ == "PlayerUpdatedSessionEventArgs"
                   and int(e.player_id.uid64) == int(player.uid64)]
        assert updated, "the free play sent no PlayerUpdated for its owner"
        assert (updated[-1].resources, updated[-1].total_resources, updated[-1].charges) == (3, 5, 2)
    finally:
        shared._db = previous


def test_revealed_pick_completes_the_paused_chain_item():
    import services.tournament_game as tournament_game
    db = make_db()
    shared, previous, handler = _bound(db)
    try:
        hand = [2049, 2305, 2561]   # card uids carry the card type in their low byte
        for uid in hand:
            add_card(db, uid, 6, TPL_GLADIATOR, "hand")
        session = _pvp_session(db)
        state = session._rules_port_battle_state
        state.update({
            "pids": [5, 6], "resolution_paused": True, "paused_chain_instance_id": 1,
            "stack": [{"kind": "spell", "source_uid": 900, "instance_id": 1, "ability_guids": []}],
            "pending_deck_search": {
                "kind": "revealed_card", "candidates": list(hand), "revealed_cards": list(hand),
                "owner_id": 5, "source_uid": 900, "ability_guid": INQUISITION_CHILD, "instance_id": 2,
                "continuation": {"target_index": 0, "ability_guid": INQUISITION_CHILD, "ability_instance_id": 1,
                                 "source_uid": 900, "owner_id": 5}}})
        tournament_game.pvp_save_state(session, state)
        choice = b"m_UID64;0;1;1;" + struct.pack("<Q", 2305).hex().encode() + b";"
        tournament_game._pvp_resolve_revealed_choice(handler, session, choice, 5)

        after = tournament_game.pvp_load_state(session) or {}
        # The item is marked completed, so the chain pass finishes it instead of running its effects again.
        assert after.get("completed_chain_instance_id") == 1, after.get("completed_chain_instance_id")
        assert not after.get("paused_chain_instance_id")
        assert not after.get("pending_deck_search"), "the pick asked again"
        locations = dict(db.execute(
            "SELECT card_uid, location FROM game_cards WHERE card_uid IN (2049, 2305, 2561)").fetchall())
        assert locations == {2049: "hand", 2305: "discard", 2561: "hand"}, locations
    finally:
        shared._db = previous
