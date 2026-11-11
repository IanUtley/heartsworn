"""Replay a captured web match through the battle reducer (web_client/static/battle_state.js) in Node.

The fixture is a real capture (tests/fixtures/web_capture_sample.json). The test asserts what the browser seat sees
after replaying it: its own hand has real cards, the opponent's hand is hidden, and two replays agree.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)

REDUCER = os.path.join(ROOT, "web_client", "static", "battle_state.js")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "web_capture_sample.json")
HAND = 2   # ECardCollections.Hand, as the event stream numbers it
NO_GUID = "00000000-0000-0000-0000-000000000000"

REPLAY = r"""
const reducer = require(process.argv[1]);
const capture = require(process.argv[2]);
function replay() {
  const state = reducer.createState(capture.match.player_uid);
  for (const packet of capture.packets) {
    for (const event of packet.events || []) reducer.reduceEvent(state, event);
  }
  const cards = [...state.cards.values()].map((c) => ({ uid: c.uid, collection: c.collection,
    controller: c.controller, card_id: c.card_id }));
  return { cards, ended: state.ended, turn_phase: state.phase.turn_phase ?? null };
}
const first = replay();
const second = replay();
console.log(JSON.stringify({ first, second }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the browser's reducer")
def test_a_captured_match_replays_to_the_seats_zones():
    run = subprocess.run(["node", "-e", REPLAY, REDUCER, FIXTURE],
                         capture_output=True, text=True, timeout=120, check=True)
    result = json.loads(run.stdout)
    with open(FIXTURE) as handle:
        me = json.load(handle)["match"]["player_uid"]
    cards = result["first"]["cards"]
    hand = [card for card in cards if card["collection"] == HAND]
    mine = [card for card in hand if card["controller"] == me]
    theirs = [card for card in hand if card["controller"] != me]
    # The seat sees its own cards and the opponent's hand only as hidden cards.
    assert mine and all(card["card_id"] != NO_GUID for card in mine), mine
    assert theirs and all(card["card_id"] == NO_GUID for card in theirs), theirs
    assert result["first"]["turn_phase"] is not None
    # Replaying the same packets gives the same state.
    assert result["first"] == result["second"]
