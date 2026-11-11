"""The browser's collection and deck templates, read and written through ``profile_db``.

These are the Unity client's own tables (``collections`` for owned cards, ``player_deck_templates`` for decks), so a deck
saved here is stored in the same tables the client reads for its mercenary decks. The profile is ``HEX_WEB_PROFILE_ID``
when set, else the account that owns the most collection rows.

Profile data always goes to the live database: the web server plays matches on a disposable snapshot, and
``HEX_LIVE_DB_PATH`` names the live file it saved in ``use_snapshot_database``. Without that variable (tests, or a server
started with ``--live-db``) the shared connection is used.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager

import db as dbmod
import profile_db

MINIMUM_DECK_SIZE = 60   # the client's c_MinimumDeckSize (UIPlayerDeckContentsViewModel)
NAME_LIMIT = 40
# EDeckFormat (Game/Shared/Mechanics/EDeckFormat.cs). '' is unset: templates the Unity client wrote.
FORMATS = ("immortal", "standard", "pve", "siege")


@contextmanager
def _connection():
    """Yield the connection profile data is read and written on."""
    live = os.environ.get("HEX_LIVE_DB_PATH")
    if not live:
        yield dbmod._db
        return
    connection = dbmod.connect(live)
    try:
        yield connection
    finally:
        connection.close()


def _richest_collection_user(connection):
    row = connection.execute(
        "SELECT user_id FROM collections GROUP BY user_id "
        "ORDER BY COUNT(*) DESC, user_id LIMIT 1").fetchone()
    return int(row[0]) if row else None


def profile_user_id(conn=None):
    configured = os.environ.get("HEX_WEB_PROFILE_ID", "").strip()
    if configured.isdigit():
        return int(configured)
    if conn is not None:
        return _richest_collection_user(conn)
    with _connection() as own:
        return _richest_collection_user(own)


def profile_summary() -> dict:
    with _connection() as conn:
        user_id = profile_user_id(conn)
        row = conn.execute("SELECT name FROM users WHERE id=?", (user_id,)).fetchone() if user_id else None
    return {"user_id": user_id, "name": str(row[0]) if row and row[0] else "", "minimum_deck_size": MINIMUM_DECK_SIZE}


def _owned_cards(user_id, conn) -> list[dict]:
    rows = profile_db.db_collection_card_list(user_id, conn=conn)
    return [{"guid": str(guid).lower(), "name": str(name or ""), "cost": cost, "attack": attack,
             "defense": defense, "type": str(card_type or ""), "quantity": int(quantity or 0)}
            for guid, name, cost, attack, defense, quantity, card_type in rows if int(quantity or 0) > 0]


def collection() -> list[dict]:
    """Owned cards, one entry per template, with the copies owned."""
    with _connection() as conn:
        user_id = profile_user_id(conn)
        if user_id is None:
            return []
        return _owned_cards(user_id, conn)


def _format(value) -> str:
    """A deck format name, or '' for unset. Anything else is refused, so a typo cannot silently file a deck under no
    format."""
    text = str(value or "").strip().lower()
    if text and text not in FORMATS:
        raise ValueError(f"unknown deck format {text!r}; expected one of {', '.join(FORMATS)}")
    return text


_PLAYABLE_CHAMPION = ("SELECT 1 FROM champion_templates ct "
                      "LEFT JOIN champion_templates_extended cte ON cte.guid = ct.guid "
                      "WHERE ct.guid=? AND ct.is_player=1 AND COALESCE(cte.is_selectable, 1)=1")


def champions(format=None) -> list[dict]:
    """The champions a player can take into a deck: player champions the client lets the player select.

    ``format`` is validated and accepted, but the list is the same for every format for now: set legality per format
    needs card-set data this view does not yet read.
    """
    _format(format)
    with _connection() as conn:
        return [{"guid": str(row[0]).lower()} for row in conn.execute(
            "SELECT ct.guid FROM champion_templates ct "
            "LEFT JOIN champion_templates_extended cte ON cte.guid = ct.guid "
            "WHERE ct.is_player=1 AND COALESCE(cte.is_selectable, 1)=1 ORDER BY ct.guid").fetchall()]


def decks(format=None) -> list[dict]:
    """The player's deck templates, or only those saved for ``format`` (unset decks are listed only unfiltered)."""
    wanted = None if format is None else _format(format)
    if wanted == "":
        raise ValueError("list decks by a format name, not the unset format")
    with _connection() as conn:
        user_id = profile_user_id(conn)
        if user_id is None:
            return []
        return [_deck_view(template)
                for template in profile_db.db_profile_deck_templates(user_id, conn=conn, format=wanted)]


def deck(template_id: int) -> dict | None:
    with _connection() as conn:
        user_id = profile_user_id(conn)
        template = (profile_db.db_profile_deck_template(user_id, template_id, conn=conn)
                    if user_id is not None else None)
        return _deck_view(template) if template else None


def _reserve_copies(flags_json: str, quantity: int) -> int:
    """How many of a card's copies are reserves: the ``copies`` list when the copies differ, else the shared flag."""
    flags = json.loads(flags_json or "{}")
    copies = flags.get("copies")
    if isinstance(copies, list):
        return sum(1 for copy in copies[:quantity] if copy.get("reserves"))
    return quantity if flags.get("reserves") else 0


def _with_reserves(flags_json: str, quantity: int, reserve: int) -> str:
    """Write how many of a card's copies are reserves, in the client's flag shape: one shared flag object when every
    copy is alike, else a ``copies`` list with one object per copy. Other flags (extended, foil) are kept."""
    flags = json.loads(flags_json or "{}")
    flags.pop("copies", None)
    flags.pop("reserves", None)
    if reserve in (0, quantity):
        flags["reserves"] = reserve == quantity and quantity > 0
        return json.dumps(flags)
    flags["copies"] = [dict(flags, reserves=index < reserve) for index in range(quantity)]
    return json.dumps(flags)


def _deck_view(template: dict) -> dict:
    return {"id": int(template["id"]), "name": str(template.get("name") or ""),
            "format": str(template.get("format") or ""),
            "champion_guid": str(template.get("champion_guid") or "").lower(),
            "sleeve_guid": str(template.get("sleeve_guid") or ""),
            "gameboard_guid": str(template.get("gameboard_guid") or ""),
            "cards": [{"guid": str(card["card_guid"]).lower(), "quantity": int(card["quantity"]),
                       "reserves": _reserve_copies(card.get("flags_json") or "{}", int(card["quantity"]))}
                      for card in template.get("cards") or []]}


def _resized_gems(gems_json: str, quantity: int) -> str:
    """Per-copy gem lists, one per copy: keep the first ``quantity`` and pad with empty lists."""
    gems = json.loads(gems_json or "[]")
    if not gems:
        return "[]"
    return json.dumps(gems[:quantity] + [[] for _ in range(quantity - len(gems))])


def _resized_flags(flags_json: str, quantity: int) -> str:
    """Copy flags: the top-level flags apply to every copy; a ``copies`` list (one entry per copy) is resized."""
    flags = json.loads(flags_json or "{}")
    copies = flags.get("copies")
    if isinstance(copies, list):
        base = {key: value for key, value in flags.items() if key != "copies"}
        resized = copies[:quantity] + [dict(base) for _ in range(quantity - len(copies))]
        flags["copies"] = resized
    return json.dumps(flags)


def _card_state(previous: dict | None, quantity: int) -> tuple[str, str]:
    """The stored gem and flag JSON for one card row. An unchanged quantity keeps the stored data exactly; a changed
    quantity resizes its per-copy lists; a card the deck did not hold starts with no gems and no flags."""
    if previous is None:
        return "[]", "{}"
    if int(previous["quantity"]) == quantity:
        return previous["gem_types_list_json"], previous["flags_json"]
    return (_resized_gems(previous["gem_types_list_json"], quantity),
            _resized_flags(previous["flags_json"], quantity))


def _keep_or_set(body: dict, existing: dict | None, key: str) -> str:
    """A top-level deck field: the request's value when it sent one, else the stored value (never a default)."""
    if key in body:
        return str(body.get(key) or "")
    return str((existing or {}).get(key) or "")


def save_deck(body: dict) -> dict:
    """Create or update a deck from ``{id?, name, champion_guid?, gameboard_guid?, sleeve_guid?, cards}``.

    A field the request omits keeps its stored value, and so do a card's gems and flags when its quantity is unchanged;
    saving from the browser therefore never discards what the Unity client wrote. Each card's copies must be owned. A
    deck below the minimum size is saved as a draft; the page warns about it.
    """
    with _connection() as conn:
        user_id = profile_user_id(conn)
        if user_id is None:
            raise ValueError("no profile to save the deck to")
        owned = {card["guid"]: card["quantity"] for card in _owned_cards(user_id, conn)}
        wanted: dict[str, int] = {}
        reserved: dict[str, int] = {}
        # A card's copies may come as a main-deck entry and a reserve entry; the request says which with "reserve".
        reserve_info = any("reserve" in card for card in body.get("cards") or [])
        for card in body.get("cards") or []:
            guid = str(card.get("guid") or "").lower()
            quantity = int(card.get("quantity") or 0)
            if quantity <= 0:
                continue
            wanted[guid] = wanted.get(guid, 0) + quantity
            if card.get("reserve"):
                reserved[guid] = reserved.get(guid, 0) + quantity
        for guid, quantity in wanted.items():
            if quantity > owned.get(guid, 0):
                raise ValueError(f"only {owned.get(guid, 0)} copies of {guid} are owned, not {quantity}")
        champion = _keep_or_set(body, None, "champion_guid").lower()
        if champion and not conn.execute(_PLAYABLE_CHAMPION, (champion,)).fetchone():
            raise ValueError("that champion cannot be played in a deck")
        template_id = body.get("id")
        existing = (profile_db.db_profile_deck_template(user_id, template_id, conn=conn)
                    if template_id else None)
        deck_format = _format(body["format"]) if "format" in body else _format((existing or {}).get("format"))
        previous_cards = {str(card["card_guid"]).lower(): card for card in (existing or {}).get("cards") or []}
        columns = {"name": str(body.get("name") or "").strip()[:NAME_LIMIT] or "New deck",
                   "champion_guid": champion if "champion_guid" in body else _keep_or_set({}, existing, "champion_guid"),
                   "sleeve_guid": _keep_or_set(body, existing, "sleeve_guid"),
                   "gameboard_guid": _keep_or_set(body, existing, "gameboard_guid"),
                   "personality": _keep_or_set({}, existing, "personality"),
                   "equipment_json": _keep_or_set({}, existing, "equipment_json") or "{}",
                   "format": deck_format}
        card_rows = []
        for guid, quantity in sorted(wanted.items()):
            gems, flags = _card_state(previous_cards.get(guid), quantity)
            if reserve_info:
                flags = _with_reserves(flags, quantity, reserved.get(guid, 0))
            card_rows.append((guid, quantity, gems, flags))
        template_id = profile_db.db_save_profile_deck_template(
            user_id, template_id, columns, card_rows, conn=conn)
    return {"id": int(template_id), "cards": sum(wanted.values()),
            "valid": sum(wanted.values()) >= MINIMUM_DECK_SIZE}


def validate_deck(body: dict) -> dict:
    """Check a deck against the rules without saving it (``services/deck_validation``). The copies counted are the
    owner's; the card rules come from the game data the catalog reads."""
    from services import deck_validation
    from web_client import catalog
    with _connection() as conn:
        user_id = profile_user_id(conn)
        owned = ({card["guid"]: card["quantity"] for card in _owned_cards(user_id, conn)}
                 if user_id is not None else {})
        guids = [card.get("guid") for card in body.get("cards") or []]
        rules = deck_validation.load_card_rules(
            conn, guids, catalog.STANDARD_SETS,
            lambda guid: bool((catalog.card(guid) or {}).get("unlimited")))
        champion = str(body.get("champion_guid") or "").lower()
        playable = bool(champion) and conn.execute(_PLAYABLE_CHAMPION, (champion,)).fetchone() is not None
    return deck_validation.validate_deck(body, rules, owned, playable)


def delete_deck(template_id: int) -> bool:
    with _connection() as conn:
        user_id = profile_user_id(conn)
        if user_id is None:
            return False
        if profile_db.db_profile_deck_template(user_id, template_id, conn=conn) is None:
            return False
        profile_db.db_delete_profile_deck_template(user_id, template_id, conn=conn)
        return True
