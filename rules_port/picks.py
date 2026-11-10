"""Which picker presents an interpreter pause, decided from the authored target and nothing else.

The interpreter pauses with ``ir_exec.NeedsChoice`` when the player picks a target. The presenter kind is chosen here
from the pause's flags and its authored ``InZone`` zone (``NeedsChoice.authored_zone``). The collection flags are a
visibility union and must not decide it.

A presenter is a function of the pick kind, so a second front end (a web client) can register its own presenter for a
kind without changing the interpreter. ``resolution.prompt_interpreted_reveal`` calls ``pick_kind`` and then the
presenter for that kind; ``pickers/`` holds the wire forms (Unity events and the web message).
"""

from __future__ import annotations

REVEALED = "revealed"
CHOICE_ZONE = "choice_zone"
MATCHING_TARGET = "matching_target"
SEARCH = "search"
ZONE_PICK = "zone_pick"

# The authored zones a single-zone pick (a trigger-target prompt: the client's option list and class-39 request) covers.
# Hand is the original case; discard, underground and warzone (a flags-only troop pick) are the same prompt over
# another zone. "any" is a pick from several zones at once (a union), which the same prompt also covers.
ZONE_PICK_ZONES = frozenset({"hand", "discard", "underground", "warzone", "any"})

PICK_KINDS = (REVEALED, CHOICE_ZONE, MATCHING_TARGET, SEARCH, ZONE_PICK)

# The pending-choice kind an interpreter's choice-zone pick is stored under (rules_port/resolution.py) and answered by.
INTERPRETED_CHOICE_ZONE = "interpreted_choice_zone"


def pick_kind(*, authored_zone: str, revealed: bool, match: bool) -> str:
    """The presenter kind for one pause. A reveal keeps its revealed-card picker whatever zone it names. An authored
    ``Choosing`` pick is the choice-zone picker, and an authored ``Hand``, ``Discard`` or ``Underground`` pick is the zone
    picker (the trigger-target prompt the native path uses). Any other pick is the deck-search picker (its matching-target mode for a chosen
    match)."""
    if revealed:
        return REVEALED
    if authored_zone == "choosing":
        return CHOICE_ZONE
    if authored_zone in ZONE_PICK_ZONES:
        return ZONE_PICK
    return MATCHING_TARGET if match else SEARCH


def has_constant_maximum(raw_target) -> bool:
    """Whether an authored target template's maximum card count is a constant (one card, or several up to that number).
    A variable maximum is not wired: its count is only known when the ability runs."""
    maximum = raw_target.get("m_MaxTargetCount") if isinstance(raw_target, dict) else None
    return isinstance(maximum, dict) and str(maximum.get("_t") or "").rsplit(".", 1)[-1] == "TargetConstant"


def has_countable_maximum(raw_target) -> bool:
    """Whether an authored target template's maximum card count is typed (a constant or a variable). The interpreter
    works out the count from the ability's variables when it pauses, so the variable form is wired too."""
    maximum = raw_target.get("m_MaxTargetCount") if isinstance(raw_target, dict) else None
    return isinstance(maximum, dict) and str(maximum.get("_t") or "").rsplit(".", 1)[-1] in ("TargetConstant",
                                                                                            "TargetVariable")


def answer_uids(raw_uids, candidates) -> list[int]:
    """The chosen card UIDs the client named, in order, that are legal candidates of the pick (duplicates dropped)."""
    legal = {int(uid) for uid in candidates}
    chosen: list[int] = []
    for uid in raw_uids:
        uid = int(uid)
        if uid in legal and uid not in chosen:
            chosen.append(uid)
    return chosen


def hand_answer(raw_uids, candidates, *, minimum: int, maximum: int, optional: bool):
    """The interpreter's answer to a hand pick, and whether the player has chosen enough to send it.

    A single pick (maximum 1) answers one UID, or None for a declined optional pick. A multi-card pick answers the list
    of UIDs (empty when an optional pick is declined). A required pick with too few cards is not complete."""
    chosen = answer_uids(raw_uids, candidates)
    if maximum > 1:
        return chosen, len(chosen) >= minimum or (optional and not chosen)
    if chosen:
        return chosen[0], True
    return None, bool(optional)
