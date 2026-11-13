"""Hiding a revealed set must not move cards that a hand reveal left in hand.

Live symptom (Inquisition cast on the AI, "look at target opposing champion's
hand"): the reveal presented the AI's hand, and the resume step then hid every
revealed card back to the deck.  The whole hand went into the deck, and the
chosen card could no longer be discarded from hand.  Only cards the reveal
actually took from the deck or the choosing zone may be hidden back.
"""

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database

fresh_database()

from tests.tests_combat import make_db, add_card, TPL_GLADIATOR  # noqa: E402


def test_hand_revealed_cards_stay_in_hand_when_hidden():
    import hconnect_server as hcs
    from application.pending_choices import PendingChoicesMixin

    db = make_db()
    add_card(db, 900, 0, TPL_GLADIATOR, loc="hand")
    add_card(db, 901, 0, TPL_GLADIATOR, loc="deck")
    session = type("Session", (), {"session_id": 1})()

    host = mock.Mock()
    host._card_full_data.return_value = (None, "Troop", "Gladiator", 0, 0, 0, "")
    with mock.patch.object(hcs, "_db", db):
        PendingChoicesMixin._hide_candidates_to_deck(
            host, mock.Mock(), session, 1, 2, [900, 901])

    locations = dict(db.execute(
        "SELECT card_uid, location FROM game_cards WHERE card_uid IN (900, 901)"))
    assert locations[900] == "hand"
    assert locations[901] == "deck"


def test_opposing_seat_deck_cards_are_reported_under_their_owner():
    """A PvP deck search of the opposing seat hides that seat's cards with that seat as controller.

    Live symptom (the AI's Mindsnatch Raven searched the human's deck): the cards were reported under the chooser,
    so the human's deck showed 0 and the AI's showed 96.
    """
    import game_engine
    from application.pending_choices import PendingChoicesMixin

    db = make_db()
    add_card(db, 910, 7, TPL_GLADIATOR, loc="deck")
    session = type("Session", (), {"session_id": 1})()
    chooser = game_engine.UID.make(244, 5)
    opponent = game_engine.UID.make(244, 7)

    host = mock.Mock()
    host._card_full_data.return_value = (None, "Troop", "Gladiator", 0, 0, 0, "")
    game = mock.Mock()
    with mock.patch("application.pending_choices.db", type("D", (), {"_db": db})):
        PendingChoicesMixin._hide_candidates_to_deck(host, game, session, chooser, opponent, [910])

    owners = [call.args[1].uid64 for call in game.push_card_updated.call_args_list]
    assert owners == [opponent.uid64]


if __name__ == "__main__":
    # Written in pytest style: run through pytest so each test executes and failures
    # set the exit status. A missing pytest fails loudly.
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
