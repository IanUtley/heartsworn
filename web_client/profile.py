"""The browser's collection and deck templates, read and written through ``profile_db``.

These are the Unity client's own tables (``collections`` for owned cards, ``player_deck_templates`` for decks), so a deck
saved here is the same deck the client shows. The profile is ``HEX_WEB_PROFILE_ID`` when set, else the account that owns
the most collection rows.
"""

from __future__ import annotations

import os

import profile_db
from db import _db

MINIMUM_DECK_SIZE = 60   # the client's c_MinimumDeckSize (UIPlayerDeckContentsViewModel)
NAME_LIMIT = 40


def profile_user_id():
    configured = os.environ.get("HEX_WEB_PROFILE_ID", "").strip()
    if configured.isdigit():
        return int(configured)
    row = _db.execute(
        "SELECT user_id FROM collections GROUP BY user_id "
        "ORDER BY COUNT(*) DESC, user_id LIMIT 1").fetchone()
    return int(row[0]) if row else None


def profile_summary() -> dict:
    user_id = profile_user_id()
    row = _db.execute("SELECT name FROM users WHERE id=?", (user_id,)).fetchone() if user_id else None
    return {"user_id": user_id, "name": str(row[0]) if row and row[0] else "", "minimum_deck_size": MINIMUM_DECK_SIZE}


def collection() -> list[dict]:
    """Owned cards, one entry per template, with the copies owned."""
    user_id = profile_user_id()
    if user_id is None:
        return []
    rows = profile_db.db_collection_card_list(user_id)
    return [{"guid": str(guid).lower(), "name": str(name or ""), "cost": cost, "attack": attack,
             "defense": defense, "quantity": int(quantity or 0)}
            for guid, name, cost, attack, defense, quantity in rows if int(quantity or 0) > 0]


def champions() -> list[dict]:
    """Every champion template, as card GUIDs (the catalog names them)."""
    return [{"guid": str(row[0]).lower()} for row in _db.execute(
        "SELECT guid FROM champion_templates ORDER BY guid").fetchall()]


def decks() -> list[dict]:
    user_id = profile_user_id()
    if user_id is None:
        return []
    return [_deck_view(template) for template in profile_db.db_profile_deck_templates(user_id)]


def _deck_view(template: dict) -> dict:
    return {"id": int(template["id"]), "name": str(template.get("name") or ""),
            "champion_guid": str(template.get("champion_guid") or "").lower(),
            "sleeve_guid": str(template.get("sleeve_guid") or ""),
            "gameboard_guid": str(template.get("gameboard_guid") or ""),
            "cards": [{"guid": str(card["card_guid"]).lower(), "quantity": int(card["quantity"])}
                      for card in template.get("cards") or []]}


def save_deck(body: dict) -> dict:
    """Create or update a deck from ``{id?, name, champion_guid, gameboard_guid, sleeve_guid, cards}``.

    Each card's copies must be owned. A deck below the minimum size is saved as a draft; the page warns about it.
    """
    user_id = profile_user_id()
    if user_id is None:
        raise ValueError("no profile to save the deck to")
    owned = {card["guid"]: card["quantity"] for card in collection()}
    wanted: dict[str, int] = {}
    for card in body.get("cards") or []:
        guid = str(card.get("guid") or "").lower()
        quantity = int(card.get("quantity") or 0)
        if quantity <= 0:
            continue
        wanted[guid] = wanted.get(guid, 0) + quantity
    for guid, quantity in wanted.items():
        if quantity > owned.get(guid, 0):
            raise ValueError(f"only {owned.get(guid, 0)} copies of {guid} are owned, not {quantity}")
    champion = str(body.get("champion_guid") or "").lower()
    if champion and not _db.execute("SELECT 1 FROM champion_templates WHERE guid=?", (champion,)).fetchone():
        raise ValueError("that champion is not in the game data")
    columns = {"name": str(body.get("name") or "").strip()[:NAME_LIMIT] or "New deck",
               "champion_guid": champion,
               "sleeve_guid": str(body.get("sleeve_guid") or ""),
               "gameboard_guid": str(body.get("gameboard_guid") or "")}
    template_id = profile_db.db_save_profile_deck_template(
        user_id, body.get("id"), columns,
        [(guid, quantity, "[]", "{}") for guid, quantity in sorted(wanted.items())])
    return {"id": int(template_id), "cards": sum(wanted.values()),
            "valid": sum(wanted.values()) >= MINIMUM_DECK_SIZE}


def delete_deck(template_id: int) -> bool:
    user_id = profile_user_id()
    if user_id is None:
        return False
    if profile_db.db_profile_deck_template(user_id, template_id) is None:
        return False
    profile_db.db_delete_profile_deck_template(user_id, template_id)
    return True
