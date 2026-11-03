"""Card-choice constants and protocol helpers.

The client implements ``DoubleChoiceAbilityEffectTemplate`` by creating
temporary choice cards and exposing the built-in ``ChooseAndPlay`` ability;
``rules_port.choice_effects`` owns that behaviour.  This module keeps the
shared ability GUIDs and the transaction helper the protocol layer uses.
"""

import re
import struct


CHOOSE_AND_PLAY_ABILITY = "7db268ea-c960-68ba-be49-712d760d7ba4"
# Built-in copy ability referenced by the printed Iconoclast ability.  The
# client uses this relationship to display the source card's localized text.
CHOICE_COPY_ABILITY = "d5b56bd5-4d06-995d-6487-d1db36e29853"
CHOICE_TARGET_TEMPLATE = "6f83ae25-2c6d-42af-8635-b7a4174b0405"


def choice_prompt_ability(pending):
    """Return the ability GUID a Choosing-zone picker advertises.

    The client titles the picker from this ability (BattleStateTargetBase.
    GetAbilityTargetText: the source card's ActivateAbility that invokes it,
    else its own m_GameText).  An authored ActivateAbility child that waits on
    the picker is the ability actually being resolved, so advertise it
    (Iconoclast's child is the copy ability; Trial of the Thorn's is
    ChooseAndPlay).  DoubleChoice activates the built-in ChooseAndPlay, as
    DoubleChoiceAbilityEffectTemplate does in C#.
    """
    pending = pending or {}
    if pending.get("kind") == "choice_zone_target":
        child = str((pending.get("continuation") or {}).get("ability_guid")
                    or pending.get("ability_guid") or "").lower()
        if child:
            return child
    return CHOOSE_AND_PLAY_ABILITY


def extract_card_uids(raw):
    """Return Card SessionCardIds embedded in an activation transaction."""
    result = []
    if not isinstance(raw, bytes):
        return result
    for match in re.finditer(
            rb"m_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});", raw):
        try:
            uid64 = struct.unpack("<Q", bytes.fromhex(
                match.group(1).decode()))[0]
        except (TypeError, ValueError, struct.error):
            continue
        if (uid64 & 0xFF) == 1:
            result.append(int(uid64))
    return result


__all__ = [
    "CHOOSE_AND_PLAY_ABILITY", "CHOICE_COPY_ABILITY",
    "CHOICE_TARGET_TEMPLATE", "choice_prompt_ability",
    "extract_card_uids",
]
