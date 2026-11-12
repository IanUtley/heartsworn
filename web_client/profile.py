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
        yield dbmod.conn()
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
             "defense": defense, "type": str(card_type or ""), "sockets": int(sockets or 0),
             "quantity": int(quantity or 0)}
            for guid, name, cost, attack, defense, quantity, card_type, sockets in rows if int(quantity or 0) > 0]


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


# A champion a player may put in a deck: selectable in the client, and with abilities the engine can seed (only
# champions with ability rows are playable, so the list is the same set the matches use).
_PLAYABLE_CHAMPION = ("SELECT 1 FROM champion_templates_extended e "
                      "WHERE LOWER(e.guid)=? AND e.is_selectable=1 AND EXISTS("
                      "SELECT 1 FROM champion_abilities ca WHERE LOWER(ca.champion_guid)=LOWER(e.guid))")


def champions(format=None) -> list[dict]:
    """The champions a player can take into a deck: player champions the client lets the player select.

    ``format`` is validated and accepted, but the list is the same for every format for now: set legality per format
    needs card-set data this view does not yet read.
    """
    _format(format)
    with _connection() as conn:
        return [{"guid": str(row[0]).lower()} for row in conn.execute(
            "SELECT LOWER(e.guid) FROM champion_templates_extended e "
            "WHERE e.is_selectable=1 AND EXISTS(SELECT 1 FROM champion_abilities ca "
            "WHERE LOWER(ca.champion_guid)=LOWER(e.guid)) ORDER BY LOWER(e.guid)").fetchall()]


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


def _gems_per_copy(gems_json) -> list:
    """Each copy's gem names, from the stored per-copy list (reserve copies first). Empty when nothing is socketed."""
    try:
        gems = json.loads(gems_json or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(gems, list):
        return []
    return [[str(name) for name in (copy if isinstance(copy, list) else [copy]) if name] for copy in gems]


def gem_choices() -> list[dict]:
    """The gems a deck can socket: name and gem type, for the deck builder's pickers."""
    with _connection() as conn:
        return [{"name": str(name), "gem_type": int(gem_type or 0)} for name, gem_type in conn.execute(
            "SELECT name, gem_type FROM gem_templates ORDER BY name").fetchall()]


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
            "pve_champion_id": int(template.get("pve_champion_id") or 0),
            "champion_guid": str(template.get("champion_guid") or "").lower(),
            "sleeve_guid": str(template.get("sleeve_guid") or ""),
            "gameboard_guid": str(template.get("gameboard_guid") or ""),
            "cards": [{"guid": str(card["card_guid"]).lower(), "quantity": int(card["quantity"]),
                       "reserves": _reserve_copies(card.get("flags_json") or "{}", int(card["quantity"])),
                       "gems": _gems_per_copy(card.get("gem_types_list_json"))}
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
        # Gems per copy, by part: a card whose entries carry "gems" has its sockets set from the request.
        gem_parts: dict[str, dict[str, list]] = {}
        for card in body.get("cards") or []:
            guid = str(card.get("guid") or "").lower()
            quantity = int(card.get("quantity") or 0)
            if quantity <= 0:
                continue
            wanted[guid] = wanted.get(guid, 0) + quantity
            part = "reserve" if card.get("reserve") else "main"
            if card.get("reserve"):
                reserved[guid] = reserved.get(guid, 0) + quantity
            if "gems" in card:
                copies = card.get("gems") or []
                parts = gem_parts.setdefault(guid, {"main": [], "reserve": []})
                for copy in range(quantity):
                    names = copies[copy] if copy < len(copies) else []
                    parts[part].append([str(name) for name in names if name])
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
        pve_champion = (int(body["pve_champion_id"] or 0) if "pve_champion_id" in body
                        else int((existing or {}).get("pve_champion_id") or 0))
        if pve_champion:
            # A PvE deck belongs to one of the owner's campaign champions (the champions table).
            if deck_format != "pve":
                raise ValueError("only a PvE deck can be bound to a campaign champion")
            if not conn.execute("SELECT 1 FROM champions WHERE id=? AND user_id=? AND is_deleted=0",
                                (pve_champion, user_id)).fetchone():
                raise ValueError("that campaign champion is not yours")
        previous_cards = {str(card["card_guid"]).lower(): card for card in (existing or {}).get("cards") or []}
        columns = {"name": str(body.get("name") or "").strip()[:NAME_LIMIT] or "New deck",
                   "champion_guid": champion if "champion_guid" in body else _keep_or_set({}, existing, "champion_guid"),
                   "sleeve_guid": _keep_or_set(body, existing, "sleeve_guid"),
                   "gameboard_guid": _keep_or_set(body, existing, "gameboard_guid"),
                   "personality": _keep_or_set({}, existing, "personality"),
                   "equipment_json": _keep_or_set({}, existing, "equipment_json") or "{}",
                   "format": deck_format, "pve_champion_id": pve_champion}
        card_rows = []
        for guid, quantity in sorted(wanted.items()):
            gems, flags = _card_state(previous_cards.get(guid), quantity)
            if guid in gem_parts:
                # Reserve copies come first, as the reserve flags mark them.
                listed = gem_parts[guid]["reserve"] + gem_parts[guid]["main"]
                gems = json.dumps(listed) if any(listed) else "[]"
            if reserve_info:
                flags = _with_reserves(flags, quantity, reserved.get(guid, 0))
            card_rows.append((guid, quantity, gems, flags))
        template_id = profile_db.db_save_profile_deck_template(
            user_id, template_id, columns, card_rows, conn=conn)
    return {"id": int(template_id), "cards": sum(wanted.values()),
            "valid": sum(wanted.values()) >= MINIMUM_DECK_SIZE}


def _validate(connection, user_id, body: dict) -> dict:
    """The validator's result for a deck body, on ``connection`` with the owner's copies."""
    from services import deck_validation
    from web_client import catalog
    owned = ({card["guid"]: card["quantity"] for card in _owned_cards(user_id, connection)}
             if user_id is not None else {})
    guids = [card.get("guid") for card in body.get("cards") or []]
    rules = deck_validation.load_card_rules(
        connection, guids, catalog.STANDARD_SETS,
        lambda guid: bool((catalog.card(guid) or {}).get("unlimited")))
    champion = str(body.get("champion_guid") or "").lower()
    playable = bool(champion) and connection.execute(_PLAYABLE_CHAMPION, (champion,)).fetchone() is not None
    return deck_validation.validate_deck(body, rules, owned, playable,
                                         gem_names=deck_validation.load_gem_names(connection))


def validate_deck(body: dict) -> dict:
    """Check a deck against the rules without saving it (``services/deck_validation``). The copies counted are the
    owner's; the card rules come from the game data the catalog reads."""
    with _connection() as conn:
        return _validate(conn, profile_user_id(conn), body)


def _play_body(view: dict) -> dict:
    """A saved deck as a validator body: one entry for its main-deck copies and one for its reserves."""
    cards = []
    for card in view["cards"]:
        gems = card.get("gems") or []
        reserved = card["reserves"]
        main = card["quantity"] - reserved
        if main > 0:
            cards.append({"guid": card["guid"], "quantity": main, "reserve": False, "gems": gems[reserved:]})
        if reserved > 0:
            cards.append({"guid": card["guid"], "quantity": reserved, "reserve": True, "gems": gems[:reserved]})
    return {"format": view["format"], "champion_guid": view["champion_guid"], "cards": cards}


def _main_deck_rows(template: dict) -> list[tuple[str, int, str]]:
    """The main deck's (card GUID, copies, gem json) rows, leaving out each card's reserve copies."""
    rows = []
    for card in template.get("cards") or []:
        quantity = int(card["quantity"])
        reserved = _reserve_flags(card.get("flags_json") or "{}", quantity)
        gems = json.loads(card.get("gem_types_list_json") or "[]")
        main_gems = [gem for index, gem in enumerate(gems) if index < quantity and not reserved[index]] if gems else []
        main = sum(1 for flag in reserved if not flag)
        if main > 0:
            rows.append((str(card["card_guid"]).lower(), main, json.dumps(main_gems) if main_gems else "[]"))
    return rows


def _reserve_flags(flags_json: str, quantity: int) -> list[bool]:
    """Per copy, whether it is a reserve."""
    flags = json.loads(flags_json or "{}")
    copies = flags.get("copies")
    if isinstance(copies, list):
        return [bool(copy.get("reserves")) for copy in copies[:quantity]] + [False] * max(0, quantity - len(copies))
    return [bool(flags.get("reserves"))] * quantity


def play_decks() -> list[dict]:
    """Saved decks the server can play: valid under the rules, with a champion whose abilities it can seed."""
    out = []
    with _connection() as conn:
        user_id = profile_user_id(conn)
        if user_id is None:
            return []
        for template in profile_db.db_profile_deck_templates(user_id, conn=conn):
            view = _deck_view(template)
            if _playable(conn, user_id, view):
                out.append({"id": view["id"], "name": view["name"], "format": view["format"],
                            "champion_guid": view["champion_guid"]})
    return out


def _playable(conn, user_id, view) -> bool:
    if _validate(conn, user_id, _play_body(view))["errors"]:
        return False
    return conn.execute("SELECT 1 FROM champion_abilities WHERE LOWER(champion_guid)=?",
                        (view["champion_guid"],)).fetchone() is not None


def play_spec(template_id) -> dict:
    """The match spec for a saved deck: its main deck, its champion and the gem sockets of each copy. Raises
    ValueError when the deck cannot be played, saying why."""
    from pvp_autoplay import _deck_cards, _gem_templates
    with _connection() as conn:
        user_id = profile_user_id(conn)
        template = (profile_db.db_profile_deck_template(user_id, template_id, conn=conn)
                    if user_id is not None else None)
        if template is None:
            raise ValueError("no such saved deck")
        view = _deck_view(template)
        result = _validate(conn, user_id, _play_body(view))
        if result["errors"]:
            raise ValueError("this deck cannot be played yet: " +
                             ", ".join(error["code"] for error in result["errors"]))
        if conn.execute("SELECT 1 FROM champion_abilities WHERE LOWER(champion_guid)=?",
                        (view["champion_guid"],)).fetchone() is None:
            raise ValueError("this deck's champion has no abilities the server can play")
        cards = _deck_cards(_main_deck_rows(template), _gem_templates(conn))
    return {"deck_guid": f"saved:{view['id']}", "name": view["name"], "champion_guid": view["champion_guid"],
            "cards": cards, "ai_deck_personality": "Default"}


# The client's packed socket value: bit 62 marks the new gem format, and each socket holds one ten-bit gem type, socket 1
# in the lowest bits (GemHelper.cs, EGemTypesNew.cs). A socketed card with no gems is the marker alone, which is how the
# client reads its empty sockets (pvp_db.EMPTY_SOCKET_GEMS).
_GEM_FORMAT_BIT = 1 << 62
_SOCKET_BITS = 10
_MAX_SOCKETS = 6


def pack_socket_gems(gem_types) -> int:
    """The Unity packed value for one card copy's gem types, in socket order (0 is an empty socket)."""
    value = _GEM_FORMAT_BIT
    for index, gem_type in enumerate(list(gem_types)[:_MAX_SOCKETS]):
        value |= (int(gem_type or 0) & ((1 << _SOCKET_BITS) - 1)) << (_SOCKET_BITS * index)
    return value


def export_deck(template_id) -> dict:
    """Write a saved deck to the Unity deck store (decks), in the client's own format: each main-deck copy is one of
    the owner's card instance ids (the lowest first), each socketed copy's gems are packed into active_gems, and the
    gem abilities of those copies go to gem_abilities. Re-exporting updates the same Unity deck. Reserves are not
    exported."""
    from pvp_db import db_gem_ability_guids   # pvp_db re-exports it; importing pvp_db_ability first is circular
    with _connection() as conn:
        user_id = profile_user_id(conn)
        template = (profile_db.db_profile_deck_template(user_id, template_id, conn=conn)
                    if user_id is not None else None)
        if template is None:
            raise ValueError("no such saved deck")
        view = _deck_view(template)
        gem_types = {str(name): int(gem_type or 0) for name, gem_type in conn.execute(
            "SELECT name, gem_type FROM gem_templates").fetchall()}
        active: dict[str, int] = {}
        abilities: dict[str, list] = {}
        card_ids: list[int] = []
        for guid, main, gems_json in _main_deck_rows(template):
            owned = profile_db.db_card_instance_ids(user_id, guid, conn=conn)
            if len(owned) < main:
                raise ValueError(f"the deck needs {main} copies of {guid} in its main deck; "
                                 f"the owner has {len(owned)}")
            sockets = int(conn.execute("SELECT COALESCE(socket_count, 0) FROM card_templates WHERE LOWER(guid)=?",
                                       (guid,)).fetchone()[0] or 0)
            copies = _gems_per_copy(gems_json)
            for index, instance in enumerate(owned[:main]):
                card_ids.append(instance)
                if not sockets:
                    continue
                names = copies[index] if index < len(copies) else []
                packed = pack_socket_gems([gem_types.get(name, 0) for name in names])
                active[str(instance)] = packed
                found = db_gem_ability_guids(packed, conn=conn)
                if found:
                    abilities[str(instance)] = found
        cards_json = json.dumps(card_ids)
        active_json = json.dumps(active)
        abilities_json = json.dumps(abilities)
        unity_id = int(template.get("exported_deck_id") or 0)
        if unity_id and conn.execute("SELECT 1 FROM decks WHERE id=? AND user_id=?",
                                     (unity_id, user_id)).fetchone():
            profile_db.db_update_deck(unity_id, user_id, deck_name=view["name"], cards_json=cards_json,
                                      pve_champion_id=view["pve_champion_id"] or None,
                                      pvp_champion_guid=view["champion_guid"], active_gems_json=active_json,
                                      gem_abilities_json=abilities_json, conn=conn)
        else:
            unity_id = profile_db.db_save_deck(user_id, view["name"], cards_json,
                                               pve_champion_id=view["pve_champion_id"] or None,
                                               pvp_champion_guid=view["champion_guid"],
                                               active_gems_json=active_json,
                                               gem_abilities_json=abilities_json,
                                               deck_sleeve_guid=view["sleeve_guid"] or None,
                                               gameboard_guid=view["gameboard_guid"] or None, conn=conn)
            profile_db.db_set_exported_deck(template_id, unity_id, conn=conn)
    return {"deck_id": unity_id, "cards": len(card_ids), "gems": len(active)}


def delete_deck(template_id: int) -> bool:
    with _connection() as conn:
        user_id = profile_user_id(conn)
        if user_id is None:
            return False
        if profile_db.db_profile_deck_template(user_id, template_id, conn=conn) is None:
            return False
        profile_db.db_delete_profile_deck_template(user_id, template_id, conn=conn)
        return True
