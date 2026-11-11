"""Deck validity rules (services/deck_validation.py), one table of cases over pure inputs."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from services.deck_validation import CardRules, validate_deck  # noqa: E402

CHAMPION = "00000000-0000-0000-0000-0000000000c0"
PVE = "00000000-0000-0000-0000-00000000000b"
OFF_STANDARD = "00000000-0000-0000-0000-00000000000c"
SQUIRREL = "00000000-0000-0000-0000-00000000000d"
RESOURCE = "00000000-0000-0000-0000-00000000000e"
# Distinct standard cards, so a deck of them never trips the copy limit by accident.
FILLERS = [f"00000000-0000-0000-0001-{index:012x}" for index in range(80)]

RULES = {guid: CardRules(guid, f"Filler {index}", basic=False, unlimited=False, pve=False, standard_legal=True)
         for index, guid in enumerate(FILLERS)}
RULES.update({
    PVE: CardRules(PVE, "Dungeon Beast", basic=False, unlimited=False, pve=True, standard_legal=True),
    OFF_STANDARD: CardRules(OFF_STANDARD, "Old Relic", basic=False, unlimited=False, pve=False, standard_legal=False),
    SQUIRREL: CardRules(SQUIRREL, "Mystic Squirrel", basic=False, unlimited=False, pve=False, standard_legal=True),
    RESOURCE: CardRules(RESOURCE, "Shard", basic=True, unlimited=True, pve=False, standard_legal=True),
})


def _deck(cards, deck_format="standard", champion=CHAMPION):
    return {"format": deck_format, "champion_guid": champion, "cards": cards}


def _fillers(count, first=0, reserve=False):
    return [{"guid": FILLERS[first + index], "quantity": 1, **({"reserve": True} if reserve else {})}
            for index in range(count)]


def _owns(deck, short=()):
    """The copies the owner has: exactly what the deck uses, less one copy of each card named in ``short``."""
    owned = {}
    for entry in deck["cards"]:
        guid = entry["guid"].lower()
        owned[guid] = owned.get(guid, 0) + entry["quantity"]
    for guid in short:
        owned[guid] -= 1
    return owned


def _case(cards, deck_format="standard", champion=CHAMPION, playable=True, short=()):
    deck = _deck(cards, deck_format, champion)
    return deck, _owns(deck, short), playable


@pytest.mark.parametrize("name, case, expected", [
    ("legal standard deck", _case(_fillers(60)), set()),
    ("too few cards", _case(_fillers(59)), {"TooFewCards"}),
    ("five copies of one card",
     _case(_fillers(55) + [{"guid": OFF_STANDARD, "quantity": 5}]), {"TooManyOfOneCard", "InvalidCards"}),
    ("mystic squirrel is exempt from the copy limit",
     _case(_fillers(55) + [{"guid": SQUIRREL, "quantity": 5}]), set()),
    ("shards are exempt from the copy limit",
     _case(_fillers(55) + [{"guid": RESOURCE, "quantity": 5}]), set()),
    ("too many reserves", _case(_fillers(60) + _fillers(16, first=60, reserve=True)), {"TooManyReserves"}),
    ("a card not owned", _case(_fillers(60), short=(FILLERS[0],)), {"NotOwned"}),
    ("a PvE card in standard", _case(_fillers(59) + [{"guid": PVE, "quantity": 1}]), {"InvalidCards"}),
    ("a PvE card is fine in the PvE format",
     _case(_fillers(59) + [{"guid": PVE, "quantity": 1}], deck_format="pve"), set()),
    ("a card without a standard version in standard",
     _case(_fillers(59) + [{"guid": OFF_STANDARD, "quantity": 1}]), {"InvalidCards"}),
    ("no format chosen", _case(_fillers(60), deck_format=""), {"Format"}),
    ("champion not playable", _case(_fillers(60), playable=False), {"ChampionIDNotFound"}),
    ("no champion chosen", _case(_fillers(60), champion=""), {"NoChampionSelected"}),
])
def test_deck_rules(name, case, expected):
    deck, owned, playable = case
    result = validate_deck(deck, RULES, owned, playable)
    assert {error["code"] for error in result["errors"]} == expected, (name, result["errors"])
    assert result["valid"] is (not expected)
    assert result["main"] + result["reserves"] == sum(entry["quantity"] for entry in deck["cards"])
