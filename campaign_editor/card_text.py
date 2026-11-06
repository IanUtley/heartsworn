"""The card text the client prints for a card: a port of ``CardTemplate.BuildGameText``
(hex-private/HexClient/.../Reckoning/Game/CardTemplate.cs) for the card editor.

The client's text, in order:

* a keyword line (bold in the client): the keywords the card has (flags, Rage, Lethal,
  Armor, Unblockable, ...), and the keyword abilities whose text is a keyword name;
* the ability lines: each ability's ``m_GameText`` in the card's ability order, one per
  line, skipping a ``Shift [(n)]:`` text the card already shifted in and a "This gets ...
  in all zones" text;
* the socketable ability lines, after the keyword line and before the others.

A card below ``NeedsPvPTesting`` in the client's implementation stages shows only its own
``m_GameText``. The text the client builds from a runtime state the editor does not have
is left out and listed in ``unresolved``: the localised reminders (deck sizes, "Beasts
can't block this.", Prevent damage, "Socketable" placeholders), damage multipliers, the
escalation, variable, pet and counter placeholders, activation costs, and the card's
Additional Card Text. Records text is English, so keyword names print as their keys.
"""

from __future__ import annotations

import re
from typing import Any

import client_schema  # noqa: E402

IMPL_ENUM = "ECardImplStage"
FLAGS_ENUM = "ECardAttributes"
IMPL_PVP_TESTING = "NeedsPvPTesting"
# ECardAttributes members the keyword loop skips (CardTemplate.BuildGameText).
SKIPPED_FLAGS = {"Unknown", "Inspire", "Rage", "QuickAction"}
# Flag names the client renames before it looks them up as shift texts.
FLAG_RENAMES = {"SpiritDrain": "Lifedrain", "Juggernaught": "Crush", "FirstStrike": "Swiftstrike",
                "CantBeBlocked": "Unblockable", "Immortal": "Invincible"}
# Flags printed as a keyword with a bracketed icon in Records (CardTemplate.s_Keywords).
KEYWORD_NAMES = {"Flight", "Juggernaught", "Crush", "Defensive", "SpiritDrain", "Lifedrain", "Steadfast",
                 "SpellShield", "SkyGuard", "Immortal", "Invincible", "Speed", "FirstStrike", "Swiftstrike",
                 "Lethal", "Rage"}
REPEATED_KEYWORDS = {"Mobilize"}            # one keyword per point of the attribute
NUMERIC_KEYWORDS = {"Gladiator", "Momentum"}  # the keyword shows its value
# Flags that print their own text rather than a keyword (CardTemplate.IsGametextKeyword).
GAMETEXT_FLAGS = {"PreventCombatDamage", "PreventNonCombatDamage", "PreventAllDamage", "MustBeBlocked"}
# Flags the client prints from the localisation table (CardTemplate.IsLocalizedGameTextKeyword).
LOCALIZED_FLAGS = {"CantAttack", "CantBlock", "ForceAttack", "CantReadyAutomatically"}
# Integer attributes the client never prints by name (CardTemplate.s_HiddenAttrs).
HIDDEN_ATTRS = {"DamageMultiplier", "CombatDamageMultiplier", "ArmorUsed", "CurrentResources",
                "TotalResources", "Lethal", "Armor", "Unique", "Tunneling", "Rage", "CantBeBlocked",
                "Inspire", "MinConstructedDeckSize", "MinLimitedDeckSize", "DamageShield", "Faction",
                "AIMustPlay", "Immediate", "Illuminated", "Indexed", "CantBeBlockedByBeasts"}
# Integer attributes the client flags to notify the client (IntAttrs NotifyClient=true).
NOTIFY_ATTRS = {"AllowYardInspire", "Armor", "CantBeBlockedByBeasts", "CantBeInterrupted",
                "CantHealAtEndOfTurn", "CantIncludeInDeck", "CantReadyNormally", "ChampionClass",
                "ChampionGender", "ChampionLevel", "ChampionRace",
                "CreateRandomRobotInsteadOfWorkerBotIfThisIsInPlay", "CurrentResources", "DeathSentence",
                "Enthralled", "Faction", "Feral", "Fusible", "Gladiator", "Illuminated", "Indexed",
                "Lifebound", "Marked", "Meditating", "Mobilize", "Momentum", "MustAttack", "MustBlock",
                "OpposingTroopsDontHealAtEndOfTurn", "OpposingTroopsEnterPlayExhausted",
                "OwnerCanPlayForFree", "PlayedFromHand", "Prophesied", "RefusesToBlock",
                "SettlersCreationBonus", "Soulcursed", "Spectral", "SporeaticGrowth", "Tamed", "TotalResources",
                "Toxified", "Trained", "Unique", "Unlimited", "Untamed", "Valorous"}
# Texts the client removes once a card's shift text already says them.
SOCKET_TOKEN = re.compile(r"socketable (major|minor)", re.IGNORECASE)
SOCKET_LINE = re.compile(r"<b>SOCKETABLE (?:MAJOR|MINOR)</b>(?:<p>)*", re.IGNORECASE)
SOCKET_TYPES = ("Major", "Minor")
SHIFT = re.compile(r"^(<b>)?(s|S)hift(</b>)? \[\([0-9]?[0-9]\)\]: ")
ALL_ZONES = re.compile(r"^This\sgets\s.*\sin\sall\szones\.")


def _impl_order() -> list[str]:
    return [member["name"] for member in client_schema.enum_members(IMPL_ENUM)]


def _flag_order() -> list[str]:
    """The ECardAttributes members in enum order, as the client loops over them."""
    return [member["name"] for member in client_schema.enum_members(FLAGS_ENUM)]


def _shift_texts(ability_texts: list[str]) -> list[str]:
    """The texts the card's ``Shift [(n)]:`` abilities already print (GetShiftAbilityGameTexts)."""
    return [SHIFT.sub("", text).strip() for text in ability_texts if text and SHIFT.search(text)]


def _take_shifted(text: str, shifted: list[str]) -> bool:
    """Whether a shift text matches ``text``, using it up (ShouldRemoveShiftedAbility)."""
    for candidate in (text, f"<b>{text}</b>", f"<b>{text}</b>."):
        if candidate in shifted:
            shifted.remove(candidate)
            return True
    return False


def socket_types(text: str) -> list[str]:
    """The card's sockets in the order its game text lists them (``Major``/``Minor``), as
    the client counts them (CardTemplate.GetMajorSocketCount, GetMinorSocketCount)."""
    return [match.group(1).capitalize() for match in SOCKET_TOKEN.finditer(text or "")]


def with_sockets(text: str, types: list[str]) -> str:
    """The game text with its socket tokens set to ``types``: each token a line of its own
    (``<b>SOCKETABLE MAJOR</b><p>``), before the rest of the text, as Records writes them.
    Text that already lists these sockets is returned unchanged."""
    if socket_types(text) == list(types):
        return text
    body = re.sub(r"^(?:<p>|\s)+", "", SOCKET_LINE.sub("", text or ""))
    tokens = "".join(f"<b>SOCKETABLE {kind.upper()}</b><p>" for kind in types)
    if not tokens:
        return body
    return tokens + "<p>" + body


def _normalise(text: str) -> str:
    """The client's final clean-up of the assembled text."""
    text = text.replace("\r\n", "\n")
    if text.startswith("<p>"):
        text = text[3:]
    if "<pp>" in text:
        head, tail = text.split("<pp>", 1)
        text = head + "\n\n" + tail.replace("<p>", "\n")
    else:
        text = text.replace("<p>", "\n")
    while "\n \n" in text:
        text = text.replace("\n \n", "\n\n")
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.rstrip("\n")


def build(card: dict[str, Any], abilities: list[dict[str, Any]]) -> dict[str, Any]:
    """``{text, unresolved}`` for a card.

    ``card``: ``name``, ``impl_stage``, ``own_text`` (its m_GameText), ``keywords`` (ECardAttributes
    member names), ``int_attributes`` ({IntAttrs name: value}), ``rage`` and ``socket_types`` (its sockets, ``Major``/``Minor``, in order).
    ``unresolved`` lists what the editor could not print, so the editor can say so.
    ``abilities``: its ability records in order, each ``{text, hidden}``: ``text`` the
    ability's m_GameText and ``hidden`` its HiddenAbility attribute.
    """
    unresolved: list[str] = []
    stage = str(card.get("impl_stage") or "Done")
    order = _impl_order()
    if stage in order and order.index(stage) < order.index(IMPL_PVP_TESTING):
        return {"text": _normalise(str(card.get("own_text") or "").strip()), "unresolved": []}

    flags = set(card.get("keywords") or [])
    sockets = list(card.get("socket_types") or [])
    ints = dict(card.get("int_attributes") or {})
    bar: list[str] = []          # the keyword line, in the client's order
    body: list[str] = []         # ability lines
    socket_lines: list[str] = []  # socketable ability lines, printed before the body
    shifted = _shift_texts([ability.get("text") or "" for ability in abilities])

    rage = int(card.get("rage") or 0)
    if rage > 0 and not _take_shifted(f"Rage {rage}", shifted):
        bar.append("Rage")
    bar.extend(label for attr, label in (("Tunneling", "Tunneling"), ("Armor", "Armor"), ("Lethal", "Lethal")) if ints.get(attr) and not _take_shifted(label, shifted))
    if "CantBeBlocked" in ints and not _take_shifted("Unblockable", shifted):
        bar.append("Unblockable")
    if "CantBeBlockedByBeasts" in ints:
        unresolved.append("Beasts can't block this (localised)")
    for name in sorted(ints):
        if name not in NOTIFY_ATTRS or name in HIDDEN_ATTRS:
            continue
        if name in KEYWORD_NAMES:
            bar.extend((name) for _ in (range(int(ints[name]) if name in REPEATED_KEYWORDS else 1)))
        else:
            unresolved.append(f"{name} reminder (localised)")

    for flag in _flag_order():
        if flag in SKIPPED_FLAGS or flag not in flags:
            continue
        if _take_shifted(FLAG_RENAMES.get(flag, flag), shifted):
            continue
        if flag in GAMETEXT_FLAGS or flag in LOCALIZED_FLAGS:
            unresolved.append(f"{flag} line (localised)")
        else:
            bar.append(flag)

    for ability in abilities:
        text = str(ability.get("text") or "")
        if not text:
            continue
        if _take_shifted(text, shifted):
            continue
        if ALL_ZONES.search(text):
            continue
        plain = text.replace("<b>", "").replace("</b>", "")
        if plain in KEYWORD_NAMES:
            bar.append(plain)
            continue
        line = _ability_line(text, card, unresolved)
        if not line:
            continue
        (socket_lines if SOCKET_TOKEN.search(text) else body).append(line)

    if sockets and not socket_lines:
        # Each empty socket: the client prints its label (SOCKETABLE_MAJOR, then SOCKETABLE_MINOR).
        unresolved.append("socket labels are guessed, not localised")
        socket_lines = [f"Socketable {kind}" for kind in SOCKET_TYPES
                        for _ in range(sockets.count(kind))]

    parts = []
    if bar:
        parts.append(", ".join(bar) + "\n")
    parts.extend(line + "\n" for line in socket_lines)
    parts.extend(line + "\n" for line in body)
    return {"text": _normalise("".join(parts)), "unresolved": unresolved}


def _ability_line(text: str, card: dict[str, Any], unresolved: list[str]) -> str:
    """One ability's line: its text with the card's name in place of ``#SELF#``. Runtime
    placeholders stay as written and are reported."""
    line = text.replace("#SELF#", str(card.get("name") or "this card")).strip()
    unresolved.extend(
        f"{label} placeholder kept as written"
        for pattern, label in (
            (r"\[?\(?ESC:\d+\)?\]?", "escalation"),
            (r"#VAR\d+_\d+#", "variable"),
            (r"#PET_[^# ]*#", "pet name"),
        )
        if re.search(pattern, line)
    )
    return line.strip()
