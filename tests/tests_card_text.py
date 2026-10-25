"""The editor's card text follows the client's CardTemplate.BuildGameText: a keyword line
first, then each ability's text on its own line, and a card below NeedsPvPTesting shows
only its own text."""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path[:0] = [ROOT, os.path.join(ROOT, "campaign"), os.path.join(ROOT, "campaign_editor")]

import card_text  # noqa: E402


def test_keyword_line_then_ability_lines_and_stage_fallback():
    troop = {"name": "Angel", "impl_stage": "Done", "own_text": "Printed text", "keywords": [],
             "int_attributes": {}, "rage": 0, "socket_count": 0}
    abilities = [{"text": "Flight", "hidden": False},
                 {"text": "<b>Deploy</b> - Draw a card.", "hidden": False}]
    built = card_text.build(troop, abilities)
    assert built["text"] == "Flight\n<b>Deploy</b> - Draw a card."

    unfinished = dict(troop, impl_stage="NeedsScripting")
    assert card_text.build(unfinished, abilities)["text"] == "Printed text"


def test_sockets_round_trip_through_the_game_text():
    text = "<b>SOCKETABLE MINOR</b><p><p><b>Deploy</b> - Draw a card."
    assert card_text.socket_types(text) == ["Minor"]
    written = card_text.with_sockets(text, ["Major", "Minor"])
    assert card_text.socket_types(written) == ["Major", "Minor"]
    assert written.endswith("<p><p><b>Deploy</b> - Draw a card.")
    assert card_text.with_sockets(written, ["Major", "Minor"]) == written

    troop = {"name": "Socketed", "impl_stage": "Done", "own_text": text, "socket_types": ["Major", "Minor"]}
    built = card_text.build(troop, [])
    assert built["text"] == "Socketable Major\nSocketable Minor"
