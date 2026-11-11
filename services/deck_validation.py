"""Deck validity, ported from the Unity client's DeckValidator (Game/Shared/Mechanics/DeckValidator.cs) and Format.cs.

``validate_deck`` is a pure function over the deck, the card rules and the owned copies, so it is stateless and safe to
call from any thread. ``load_card_rules`` reads those rules from the database for the cards a deck names.

Result: ``{"valid": bool, "errors": [{"code", "args"}], "warnings": [{"code", "args"}], "main": n, "reserves": n}``.
Codes are the client's ``CardCollection_DeckValidation_*`` keys without the prefix, except ``NotOwned``, which has no
client counterpart and is checked here because the browser saves against the owner's collection.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

MINIMUM_DECK_SIZE = 60        # Format.cs c_MinimumDeckSize; DeckValidator.cs:436-452
MAXIMUM_RESERVES = 15         # Format.cs:47-57
MAXIMUM_COPIES = 4            # DeckValidator.cs:516-575
FORMATS = ("immortal", "standard", "pve", "siege")
# The one card exempt from the copy limit by name (DeckValidator.cs:499). Mystic Squirrel is the morph target of
# Spectral Acorn (BuiltInResources.MorphingCards).
COPY_LIMIT_EXEMPT_NAMES = frozenset({"mystic squirrel"})


@dataclass(frozen=True)
class CardRules:
    """What validation needs to know about one card template."""
    guid: str
    name: str
    basic: bool            # a basic resource (shards): exempt from the copy limit
    unlimited: bool        # m_Unlimited: exempt from the copy limit
    pve: bool              # is_pve: a PvE-only card, refused in PvP formats
    standard_legal: bool   # has a version in a Standard set (or is basic)


def _error(code: str, **args) -> dict:
    return {"code": code, "args": args}


def validate_deck(deck: dict, rules: dict[str, CardRules], owned: dict[str, int], champion_playable: bool) -> dict:
    """Check a deck. ``deck`` is ``{"format", "champion_guid", "cards": [{"guid", "quantity", "reserve"?}]}``;
    ``rules`` maps lowercase GUIDs to CardRules; ``owned`` maps GUIDs to owned copies; ``champion_playable`` says the
    champion is a player champion the client lets the player select."""
    errors: list[dict] = []
    warnings: list[dict] = []
    deck_format = str(deck.get("format") or "").strip().lower()
    main: Counter = Counter()
    reserves: Counter = Counter()
    for entry in deck.get("cards") or []:
        guid = str(entry.get("guid") or "").lower()
        quantity = int(entry.get("quantity") or 0)
        if quantity <= 0:
            continue
        (reserves if entry.get("reserve") else main)[guid] += quantity
    everything = main + reserves
    main_count = sum(main.values())
    reserve_count = sum(reserves.values())

    if deck_format not in FORMATS:
        errors.append(_error("Format", format=deck_format, expected=list(FORMATS)))

    champion = str(deck.get("champion_guid") or "").lower()
    if not champion:
        errors.append(_error("NoChampionSelected"))
    elif not champion_playable:
        errors.append(_error("ChampionIDNotFound", champion=champion))

    if main_count < MINIMUM_DECK_SIZE:
        errors.append(_error("TooFewCards", count=main_count, minimum=MINIMUM_DECK_SIZE))
    if reserve_count > MAXIMUM_RESERVES:
        errors.append(_error("TooManyReserves", count=reserve_count, maximum=MAXIMUM_RESERVES))

    # Copies are counted by card name across the main deck and the reserves.
    copies_by_name: Counter = Counter()
    for guid, count in everything.items():
        rule = rules.get(guid)
        name = rule.name.lower() if rule else guid
        copies_by_name[name] += count
    for name, count in sorted(copies_by_name.items()):
        exempt = name in COPY_LIMIT_EXEMPT_NAMES or any(
            rule.name.lower() == name and (rule.basic or rule.unlimited)
            for rule in rules.values())
        if count > MAXIMUM_COPIES and not exempt:
            errors.append(_error("TooManyOfOneCard", card=name, count=count, maximum=MAXIMUM_COPIES))

    unknown = sorted(guid for guid in everything if guid not in rules)
    if unknown:
        errors.append(_error("CardNotLoaded", cards=unknown))

    for guid, count in sorted(everything.items()):
        if count > owned.get(guid, 0):
            errors.append(_error("NotOwned", card=guid, owned=owned.get(guid, 0), needed=count))

    # PvP formats refuse PvE-only cards; Standard also needs a Standard version of each card (or a basic resource).
    if deck_format in ("immortal", "standard", "siege"):
        refused = sorted(guid for guid in everything
                         if guid in rules and rules[guid].pve)
        if refused:
            errors.append(_error("InvalidCards", cards=refused, reason="pve"))
    if deck_format == "standard":
        outside = sorted(guid for guid in everything
                         if guid in rules and not rules[guid].standard_legal and not rules[guid].basic)
        if outside:
            errors.append(_error("InvalidCards", cards=outside, reason="not_standard"))
    if deck_format == "siege":
        warnings.append(_error("SiegeRulesNotChecked"))

    return {"valid": not errors, "errors": errors, "warnings": warnings,
            "main": main_count, "reserves": reserve_count}


def load_card_rules(connection, guids, standard_sets, is_unlimited) -> dict[str, CardRules]:
    """Read the rules for these card GUIDs. ``standard_sets`` are the set GUIDs legal in Standard; ``is_unlimited(guid)``
    says whether the card's gamedata marks it m_Unlimited. Unknown GUIDs are absent from the result."""
    wanted = sorted({str(guid).lower() for guid in guids if guid})
    if not wanted:
        return {}
    marks = ",".join("?" * len(standard_sets))
    rows = connection.execute(
        "SELECT LOWER(t.guid), t.name, t.card_type, t.subtype, t.is_pve, "
        f"EXISTS(SELECT 1 FROM card_templates o WHERE o.name = t.name "
        f"AND LOWER(o.set_guid) IN ({marks})) "
        f"FROM card_templates t WHERE LOWER(t.guid) IN ({','.join('?' * len(wanted))})",
        (*standard_sets, *wanted)).fetchall()
    rules = {}
    for guid, name, card_type, subtype, pve, standard in rows:
        basic = "Resource" in str(card_type or "") and "Standard" in str(subtype or "")
        unlimited = bool(is_unlimited(guid))
        rules[guid] = CardRules(guid=guid, name=str(name or ""), basic=basic, unlimited=unlimited,
                                pve=bool(pve), standard_legal=bool(standard) or basic)
    return rules
