import json
import pvp_db as _pvp_db
from pvp_db_card import (
    db_card_uses,
    flush_pending_session_saves,
)
from pvp_db_game import (
    db_empty_socket_gems,
    deck_rng_index,
)


def db_next_session_instance(conn=None):
    """Atomically allocate the next persisted game-session instance."""
    connection = conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT value FROM meta WHERE key='next_session_inst'").fetchone()
    nxt = (row[0] + 1) if row else 1
    connection.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('next_session_inst', ?)",
        (nxt,))
    return nxt


def _session_row_params(session):
    return (str(session.session_id), str(session.server_id), session.session_name,
            str(session.owner_uid), session.state, json.dumps(session.encounter_data),
            json.dumps(session.players), json.dumps(session.turn_order), session.seed_z,
            session.seed_w, session.deck_template_id, str(session.session_id))


def db_save_session(session, conn=None):
    """Persist a game-session object in the caller's transaction."""
    connection = conn or _pvp_db._db_layer._db
    connection.execute(_pvp_db._SESSION_UPSERT_SQL, _session_row_params(session))


def db_get_session(session_id, conn=None):
    """Return one persisted game session by protocol UID."""
    flush_pending_session_saves()
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT * FROM game_sessions WHERE session_id=?", (str(session_id),)
    ).fetchone()


def db_get_session_by_name(session_name, conn=None):
    """Return one persisted game session by client-visible name."""
    flush_pending_session_saves()
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT * FROM game_sessions WHERE session_name=?", (session_name,)
    ).fetchone()


def db_get_sessions(conn=None):
    """Return persisted sessions newest first."""
    flush_pending_session_saves()
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT * FROM game_sessions ORDER BY created_at DESC").fetchall()


def db_remove_session(session_name, conn=None):
    """Remove a game session in the caller's transaction."""
    # A queued session save must land before this statement, or it would
    # re-insert the row after the delete.
    flush_pending_session_saves()
    (conn or _pvp_db._db_layer._db).execute(
        "DELETE FROM game_sessions WHERE session_name=?", (session_name,))


def db_insert_game_card(session_id, user_id, card_uid, template_guid, location,
                        card_type="Troop", position=0, abilities_json=None,
                        attributes=0, is_champion=0, resolved_at=0, conn=None):
    """Insert a materialized game card and preserve its original owner."""
    connection = conn or _pvp_db._db_layer._db
    cursor = connection.execute(
        "INSERT INTO game_cards (session_id, user_id, card_uid, template_guid, "
        "card_template_id, location, position, card_type, card_abilities, "
        "card_attributes, owner_user_id, is_champion, resolved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, user_id, card_uid, template_guid, template_guid,
         location, position, card_type, abilities_json or "[]",
         attributes or 0, user_id or 0, is_champion, resolved_at))
    if conn is None:
        connection.commit()
    return cursor.lastrowid


def db_get_charge_power(champion_guid, conn=None):
    """Return a champion template's charge-power ability GUID."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT charge_power FROM champion_templates WHERE guid=?",
        (champion_guid,)).fetchone()
    return row[0] if row and row[0] else None


def db_get_champion_ability_guids(champion_guid, conn=None):
    """Return all ability GUIDs attached to a champion template."""
    return [row[0] for row in (conn or _pvp_db._db_layer._db).execute(
        "SELECT ability_guid FROM champion_abilities WHERE champion_guid=?",
        (champion_guid,)).fetchall()]


def db_owner_card_uids_with_counters(session_id, user_id, conn=None):
    """Return ``(card_uid,)`` for one player's cards carrying any counter.

    A coarse text prefilter over ``permanent_buffs``; callers decode the
    JSON and test the specific counter template.
    """
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT card_uid FROM game_cards WHERE session_id=? AND user_id=? "
        "AND permanent_buffs LIKE '%\"counters\"%'",
        (session_id, user_id)).fetchall()


def db_hand_card_count(session_id, user_id, conn=None):
    """Return the number of cards in a player's hand."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT COUNT(*) FROM game_cards WHERE session_id=? AND user_id=? "
        "AND location='hand'", (session_id, user_id)).fetchone()
    return row[0] if row else 0


def db_get_card_abilities(template_guid, conn=None):
    """Return ``(abilities_json, attributes)`` for a card template."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT abilities_json, attributes FROM card_templates WHERE guid=?",
        (template_guid,)).fetchone()
    if row:
        return row[0] or "[]", int(row[1] or 0)
    return "[]", 0


def db_reset_card_uses(session_id, card_uid, *,
                       preserve_turn_and_cooldowns=True, conn=None):
    """Reset instance usage like C# TransformCardBase/Card.Revert.

    A direct transform clears only per-game counts. Reverting first runs
    ``Card.Revert``, which clears per-turn counts and cooldowns too.
    """
    connection = conn or _pvp_db._db_layer._db
    with _pvp_db._CARD_COOLDOWN_LOCK:
        uses = db_card_uses(session_id, card_uid, conn=connection)
        if preserve_turn_and_cooldowns:
            uses = {key: value for key, value in uses.items()
                    if key in {"__turn_uses__", _pvp_db._CARD_COOLDOWNS_KEY}}
        else:
            uses = {}
        return connection.execute(
            "UPDATE game_cards SET card_uses=? "
            "WHERE session_id=? AND card_uid=?",
            (json.dumps(uses, separators=(",", ":")),
             session_id, int(card_uid))).rowcount


def db_record_card_ability_use(session_id, card_uid, ability_guid,
                               turn_number, conn=None, *,
                               uses_per_game=True, uses_per_turn=True):
    """Record one activation in independent game and turn counters."""
    connection = conn or _pvp_db._db_layer._db
    key = str(ability_guid or "").lower()
    if not key or not (uses_per_game or uses_per_turn):
        return 0, 0
    with _pvp_db._CARD_COOLDOWN_LOCK:
        uses = db_card_uses(session_id, card_uid, conn=connection)
        game_count = 0
        turn_count = 0
        if uses_per_game:
            try:
                game_count = int(uses.get(key, 0) or 0) + 1
            except (TypeError, ValueError):
                game_count = 1
            uses[key] = game_count
        if uses_per_turn:
            turn_key = f"{int(turn_number or 1)}:{key}"
            per_turn = uses.get("__turn_uses__", {})
            per_turn = dict(per_turn) if isinstance(per_turn, dict) else {}
            try:
                turn_count = int(per_turn.get(turn_key, 0) or 0) + 1
            except (TypeError, ValueError):
                turn_count = 1
            per_turn[turn_key] = turn_count
            uses["__turn_uses__"] = per_turn
        connection.execute(
            "UPDATE game_cards SET card_uses=? WHERE session_id=? AND card_uid=?",
            (json.dumps(uses, separators=(",", ":")),
             session_id, int(card_uid)))
        if conn is None:
            connection.commit()
        return game_count, turn_count


def db_is_champion_template(template_guid, conn=None):
    """Whether a template is present in either champion catalog."""
    connection = conn or _pvp_db._db_layer._db
    try:
        row = connection.execute(
            "SELECT guid FROM champion_templates_extended WHERE guid=? "
            "UNION ALL SELECT guid FROM champion_templates WHERE guid=? LIMIT 1",
            (template_guid, template_guid)).fetchone()
    except Exception as exc:
        if "champion_templates_extended" not in str(exc):
            raise
        row = connection.execute(
            "SELECT guid FROM champion_templates WHERE guid=? LIMIT 1",
            (template_guid,)).fetchone()
    return row is not None


def db_hand_cards_for_discard(session_id, user_id, conn=None):
    """Return stable hand rows used by discard selection."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT gc.id, gc.card_uid, gc.template_guid, ct.card_type "
        "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.user_id=? AND gc.location='hand' "
        "ORDER BY gc.position",
        (session_id, user_id),
    ).fetchall()


def db_move_card_for_effect(session_id, card_uid, location, position, state,
                            clear_dead=False, clear_bits=0, conn=None):
    """Move a card using the effect-zone state transition contract."""
    if location not in {"deck", "hand", "warzone", "underground", "discard", "void"}:
        raise ValueError("unsupported effect destination")
    connection = conn or _pvp_db._db_layer._db
    state_expr = "card_state & ~?" if clear_dead else "?"
    params = ((location, int(position), int(clear_bits), session_id, int(card_uid))
              if clear_dead else
              (location, int(position), int(state), session_id, int(card_uid)))
    return connection.execute(
        "UPDATE game_cards SET location=?, position=?, card_state = "
        + state_expr + " WHERE session_id=? AND card_uid=?", params)


def db_reset_card_modifiers(session_id, card_uid, permanent_buffs, conn=None):
    """Clear stat/cost modifiers while retaining serialized card buffs."""
    return (conn or _pvp_db._db_layer._db).execute(
        "UPDATE game_cards SET card_attack_mod=0, card_defense_mod=0, "
        "card_cost_mod=0, permanent_buffs=? WHERE session_id=? AND card_uid=?",
        (permanent_buffs, session_id, int(card_uid))).rowcount


def db_hand_exists(session_id, user_id, conn=None):
    """Whether a player has at least one card in hand."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT 1 FROM game_cards WHERE session_id=? AND user_id=? "
        "AND location='hand' LIMIT 1", (session_id, int(user_id))).fetchone()


def db_hand_card_for_discard(session_id, card_uid, user_id, conn=None):
    """Return a submitted hand card only when controlled by the player."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT user_id, COALESCE(owner_user_id, user_id), "
        "template_guid, card_template_id FROM game_cards "
        "WHERE session_id=? AND card_uid=? AND user_id=? "
        "AND location='hand'",
        (session_id, int(card_uid), int(user_id))).fetchone()


def db_hand_count(session_id, user_id, conn=None):
    """Return the number of cards in a player's hand."""
    connection = conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT COUNT(*) FROM game_cards WHERE session_id=? AND user_id=? "
        "AND location='hand'", (session_id, int(user_id))).fetchone()
    return int(row[0] or 0) if row else 0


def db_hand_card_by_uid(session_id, user_id, card_uid, conn=None):
    """Return a specific controlled hand card with display identity."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT gc.card_uid, gc.template_guid, gc.card_template_id, ct.name "
        "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.user_id=? AND gc.location='hand' "
        "AND gc.card_uid=? LIMIT 1",
        (session_id, int(user_id), int(card_uid))).fetchone()


def db_hand_card_by_name(session_id, user_id, name, exact=True, conn=None):
    """Return the first controlled hand card matching an authored name."""
    operator = "=" if exact else "LIKE"
    value = str(name) if exact else "%" + str(name) + "%"
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT gc.card_uid, gc.template_guid, gc.card_template_id, ct.name "
        "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.user_id=? AND gc.location='hand' "
        "AND LOWER(ct.name) " + operator + " LOWER(?) "
        "ORDER BY gc.position, gc.card_uid LIMIT 1",
        (session_id, int(user_id), value)).fetchone()


def db_move_hand_card_to_deck_top(session_id, user_id, card_uid, conn=None):
    """Move a controlled hand card to deck position zero, preserving order."""
    connection = conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT COUNT(*) FROM game_cards WHERE session_id=? AND user_id=? "
        "AND location='deck'", (session_id, int(user_id))).fetchone()
    deck_count = int(row[0] if row else 0)
    connection.execute(
        "UPDATE game_cards SET position=position+? WHERE session_id=? "
        "AND user_id=? AND location='deck'",
        (deck_count + 1, session_id, int(user_id)))
    connection.execute(
        "UPDATE game_cards SET location='deck', position=0, card_state=0 "
        "WHERE session_id=? AND user_id=? AND card_uid=? AND location='hand'",
        (session_id, int(user_id), int(card_uid)))
    connection.execute(
        "UPDATE game_cards SET position=position-? WHERE session_id=? "
        "AND user_id=? AND location='deck' AND card_uid<>?",
        (deck_count, session_id, int(user_id), int(card_uid)))
    return deck_count


def db_hand_display_rows(session_id, user_id, conn=None):
    """Return hand fields used by the debug hand command."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT gc.card_uid, ct.name, ct.cost, ct.card_type "
        "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.user_id=? AND gc.location='hand' "
        "ORDER BY gc.position", (session_id, int(user_id))).fetchall()


def db_playable_hand_rows(session_id, user_id, limit=7, conn=None):
    """Return the first hand cards eligible for debug outline display."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT gc.card_uid, ct.name FROM game_cards gc "
        "JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.user_id=? AND gc.position < 100 "
        "ORDER BY gc.position LIMIT ?",
        (session_id, int(user_id), int(limit))).fetchall()


def db_move_debug_card(session_id, card_uid, location, conn=None):
    """Move a debug-selected card to an explicitly requested zone."""
    if not location:
        raise ValueError("missing card location")
    return (conn or _pvp_db._db_layer._db).execute(
        "UPDATE game_cards SET location=? WHERE session_id=? AND card_uid=?",
        (str(location), session_id, int(card_uid)))


def db_move_card_to_deck_top(session_id, card_uid, card_state, conn=None):
    """Place a session card at deck position zero and shift existing cards."""
    connection = conn or _pvp_db._db_layer._db
    connection.execute(
        "UPDATE game_cards SET position=COALESCE(position, 0)+1 "
        "WHERE session_id=? AND location='deck' AND card_uid<>?",
        (session_id, int(card_uid)))
    connection.execute(
        "UPDATE game_cards SET location='deck', position=0, card_state=? "
        "WHERE session_id=? AND card_uid=?",
        (card_state, session_id, int(card_uid)))
    if conn is None:
        connection.commit()


def db_move_card_to_deck(session_id, card_uid, card_state, conn=None):
    """Return a card to its deck zone without changing its position."""
    connection = conn or _pvp_db._db_layer._db
    connection.execute(
        "UPDATE game_cards SET location='deck', card_state=? "
        "WHERE session_id=? AND card_uid=?",
        (card_state, session_id, int(card_uid)))
    if conn is None:
        connection.commit()


def db_latest_deck_id_for_user(user_id, conn=None):
    """Return a user's most recently saved deck ID."""
    connection = conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT id FROM decks WHERE user_id=? "
        "ORDER BY COALESCE(last_saved, created_at, 0) DESC, id DESC LIMIT 1",
        (user_id,)).fetchone()
    return row[0] if row else None
    return row[0] if row else None


def db_rules_port_card_projection(session_id, card_uid, conn=None):
    """Return the typed play/resource card projection."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT gc.template_guid, gc.location, gc.user_id, ct.name, "
        "ct.card_type, ct.abilities_json, ct.current_resources_granted, "
        "ct.max_resources_granted, ct.attack, ct.defense "
        "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.card_uid=?",
        (session_id, int(card_uid))).fetchone()


def db_rules_port_card_state_projection(session_id, card_uid, conn=None):
    """Return the typed ready-card state projection."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT template_guid, card_type, user_id, card_state "
        "FROM game_cards WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).fetchone()


def db_rules_port_hand_rows(session_id, user_id, conn=None):
    """Return opening-hand row IDs and card UIDs in display order."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT id, card_uid FROM game_cards "
        "WHERE session_id=? AND user_id=? AND location='hand' "
        "ORDER BY position", (session_id, int(user_id))).fetchall()


def db_rules_port_session_card_ids(session_id, user_id, conn=None):
    """Return stable game-card row IDs for an opening-hand shuffle."""
    return [row[0] for row in (conn or _pvp_db._db_layer._db).execute(
        "SELECT id FROM game_cards WHERE session_id=? AND user_id=?",
        (session_id, int(user_id))).fetchall()]


def db_rules_port_draw_rows(session_id, user_id, count, conn=None):
    """Return ordered deck rows and template fields for redraw projection."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT gc.card_uid, gc.card_template_id, gc.template_guid, "
        "ct.name, ct.card_type, ct.cost, ct.attack, ct.defense, "
        "ct.threshold_json, ct.abilities_json FROM game_cards gc "
        "JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.user_id=? AND gc.location='deck' "
        "ORDER BY gc.position LIMIT ?",
        (session_id, int(user_id), int(count))).fetchall()


def db_rules_port_redraw_hand(session_id, user_id, draw_count, conn=None):
    """Atomically replace an opening hand and return old/new projection rows."""
    import random

    connection = conn or _pvp_db._db_layer._db
    old_rows = db_rules_port_hand_rows(session_id, user_id, conn=connection)
    if old_rows:
        connection.executemany(
            "UPDATE game_cards SET location='deck', card_state=0 WHERE id=?",
            [(row[0],) for row in old_rows])
    ids = db_rules_port_session_card_ids(session_id, user_id, conn=connection)
    random.shuffle(ids)
    if ids:
        connection.executemany(
            "UPDATE game_cards SET position=? WHERE id=?",
            [(position, row_id) for position, row_id in enumerate(ids)])
    new_rows = db_rules_port_draw_rows(
        session_id, user_id, draw_count, conn=connection)
    if new_rows:
        connection.executemany(
            "UPDATE game_cards SET location='hand' "
            "WHERE session_id=? AND card_uid=?",
            [(session_id, row[0]) for row in new_rows])
    if conn is None:
        connection.commit()
    return old_rows, new_rows


def db_rules_port_warzone_projection(session_id, conn=None):
    """Return warzone UID, template, controller, and state rows."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid, template_guid, user_id, card_state FROM game_cards "
        "WHERE session_id=? AND location='warzone'", (session_id,)).fetchall()


def db_rules_port_hand_card(session_id, card_uid, user_id, conn=None):
    """Return a specific owned hand card's row ID and template GUID."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT id, template_guid FROM game_cards WHERE session_id=? "
        "AND card_uid=? AND user_id=? AND location='hand'",
        (session_id, int(card_uid), int(user_id))).fetchone()


def db_pvp_set_guids(conn=None):
    """Return collectible set IDs that contain eligible PVP cards."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT set_guid FROM card_templates "
        "WHERE is_pve=0 AND no_pvp=0 "
        "AND rarity IN ('Common','Uncommon','Rare','Legendary') "
        "GROUP BY set_guid"
    ).fetchall()


def db_pvp_booster_card_guids(conn=None):
    """Return standard collectible PvP card templates eligible for boosters.

    Non-basic resources use the normal card rarities and belong in this pool;
    basic resources use the ``Land`` rarity and remain excluded.  Equipment-
    modified/alternate-art printings are excluded from the standard pool.
    """
    connection = conn or _pvp_db._db_layer._db
    rows = connection.execute(
        "SELECT guid FROM card_templates "
        "WHERE is_pve=0 AND no_pvp=0 "
        "AND rarity IN ('Common','Uncommon','Rare','Legendary') "
        "AND COALESCE(equipment_modified,0)=0 "
        "ORDER BY guid"
    ).fetchall()
    return [row[0] for row in rows]


def db_next_card_uid(session_id, conn=None):
    """Allocate a collision-free client SessionCard UID."""
    connection = conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT COALESCE(MAX(card_uid >> 8), 10000) + 1 "
        "FROM game_cards WHERE session_id=?", (session_id,)).fetchone()
    instance = max(10001, int(row[0] or 10001))
    from game_engine import UID
    while connection.execute(
            "SELECT 1 FROM game_cards WHERE session_id=? AND card_uid=? LIMIT 1",
            (session_id, UID.make(1, instance).uid64)).fetchone():
        instance += 1
    return UID.make(1, instance).uid64


def db_insert_debug_card(session_id, owner_id, card_uid, template, location,
                         conn=None):
    """Insert a card created by the client debug-cheat transaction."""
    connection = conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM game_cards "
        "WHERE session_id=? AND user_id=? AND location=?",
        (session_id, int(owner_id), location)).fetchone()
    position = int(row[0] or 0)
    if location == "deck":
        connection.execute(
            "UPDATE game_cards SET position=position+1 "
            "WHERE session_id=? AND user_id=? AND location='deck'",
            (session_id, int(owner_id)))
        position = 0
    row_id = _pvp_db.db_next_game_card_row_id(session_id, conn=connection)
    connection.execute(
        "INSERT INTO game_cards (id, user_id, session_id, card_uid, "
        "card_template_id, location, position, is_champion, card_type, "
        "template_guid, owner_user_id, original_template_guid) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
        (row_id, int(owner_id), session_id, int(card_uid), 0, location,
         position, template[1], template[0], int(owner_id), template[0]),
    )
    if conn is None:
        connection.commit()
    return int(card_uid), template


def db_pregame_talent_rows(ability_guids, conn=None):
    """Return selected abilities authored for the pregame phase."""
    if not ability_guids:
        return []
    marks = ",".join("?" for _ in ability_guids)
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT DISTINCT ability_guid, condition FROM talent_abilities "
        "WHERE ability_guid IN (" + marks + ") AND (activatable_phases & 4) != 0",
        tuple(str(guid) for guid in ability_guids)).fetchall()


def db_inspire_ability_guids(conn=None):
    """Return ability GUIDs whose game text starts with the Inspire prefix."""
    return [str(row[0]).lower() for row in (
        conn or _pvp_db._db_layer._db).execute(
            "SELECT ability_guid FROM card_abilities_meta "
            "WHERE game_text LIKE '<b>Inspire</b>%'")]


def db_next_game_card_row_id(session_id, conn=None):
    """Allocate the historical per-session generated-card row ID."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT COALESCE(MAX(id), 10000) + 1 FROM game_cards "
        "WHERE session_id=?", (session_id,)).fetchone()
    return int(row[0])


def db_insert_deck_card_at(session_id, user_id, card_uid, index,
                           from_bottom=False, conn=None):
    """Place one card in a deck at a C# ``AddCardAtIndexAndLocation`` slot.

    ``index`` counts from the top (index 0 is the top card) or, when
    ``from_bottom`` is set, from the bottom (index 0 is the last card), as
    C# does for ``ECardLocations.Bottom``.  The card's own row is excluded
    from the ordering and every other deck card keeps its relative order.
    """
    connection = conn or _pvp_db._db_layer._db
    rows = connection.execute(
        "SELECT card_uid FROM game_cards "
        "WHERE session_id=? AND user_id=? AND location='deck' "
        "ORDER BY position", (session_id, int(user_id))).fetchall()
    others = [int(row[0]) for row in rows if int(row[0]) != int(card_uid)]
    index = min(max(int(index), 0), len(others))
    split = len(others) - index if from_bottom else index
    ordered = others[:split] + [int(card_uid)] + others[split:]
    assignments = " ".join("WHEN ? THEN ?" for _uid in ordered)
    params = []
    for position, uid in enumerate(ordered):
        params.extend((uid, position))
    marks = ",".join("?" for _uid in ordered)
    connection.execute(
        "UPDATE game_cards SET position=CASE card_uid "
        + assignments + " ELSE position END "
        "WHERE session_id=? AND user_id=? AND card_uid IN (" + marks + ")",
        (*params, session_id, int(user_id), *ordered))
    if connection is _pvp_db._db_layer._db:
        connection.commit()
    return ordered.index(int(card_uid))


def db_randomly_insert_deck_cards(session_id, user_id, card_uids,
                                  connection=None, conn=None, rng=None):
    """Reinsert selected cards at random deck slots without shuffling others.

    This is the persistence operation behind ``shuffle into deck`` effects.
    The selected cards are randomized among random slots while the relative
    order of all unselected cards remains unchanged.  ``rng`` is the session
    RNG when available; the stdlib RNG is used otherwise.
    """
    connection = connection or conn or _pvp_db._db_layer._db
    wanted = {int(uid) for uid in (card_uids or [])}
    if not wanted:
        return []
    rows = connection.execute(
        "SELECT card_uid FROM game_cards "
        "WHERE session_id=? AND user_id=? AND location='deck' "
        "ORDER BY position", (session_id, user_id)).fetchall()
    if not rows:
        return []
    if len(wanted) == 1:
        card_uid = next(iter(wanted))
        exists = connection.execute(
            "SELECT 1 FROM game_cards WHERE session_id=? AND user_id=? "
            "AND card_uid=? LIMIT 1",
            (session_id, user_id, card_uid)).fetchone()
        if not exists:
            return []
        # Rebuild the deck order with the card at a uniformly random slot.
        # ``rows`` already contains the card when the caller moved it into the
        # deck before randomizing its slot (the "put into deck" leaf does
        # exactly this), so exclude it before choosing the slot.  Shifting the
        # in-place positions instead left a gap and could place the card at
        # ``deck_count`` (past the end).
        deck_uids = [int(row[0]) for row in rows]
        others = [uid for uid in deck_uids if uid != int(card_uid)]
        insert_position = deck_rng_index(rng, len(others) + 1)
        ordered = (others[:insert_position] + [int(card_uid)]
                   + others[insert_position:])
        assignments = " ".join("WHEN ? THEN ?" for _uid in ordered)
        params = []
        for position, uid in enumerate(ordered):
            params.extend((uid, position))
        marks = ",".join("?" for _ in ordered)
        connection.execute(
            "UPDATE game_cards SET location='deck', position=CASE card_uid "
            + assignments + " ELSE position END "
            "WHERE session_id=? AND user_id=? AND card_uid IN (" + marks + ")",
            (*params, session_id, user_id, *ordered))
        if connection is _pvp_db._shared_db_connection():
            connection.commit()
        return [card_uid]
    selected = [int(row[0]) for row in rows if int(row[0]) in wanted]
    if not selected:
        return []
    for i in range(len(selected) - 1, 0, -1):
        j = deck_rng_index(rng, i + 1)
        selected[i], selected[j] = selected[j], selected[i]
    slots = list(range(len(rows)))
    for i in range(len(slots) - 1, 0, -1):
        j = deck_rng_index(rng, i + 1)
        slots[i], slots[j] = slots[j], slots[i]
    selected_by_slot = dict(zip(sorted(slots[:len(selected)]), selected))
    remaining = [int(row[0]) for row in rows if int(row[0]) not in wanted]
    remaining_iter = iter(remaining)
    ordered = []
    for rank in range(len(rows)):
        ordered.append(selected_by_slot[rank] if rank in selected_by_slot
                       else next(remaining_iter))
    # Assign the complete permutation in one set-based statement.  The CASE
    # expression gives each card its final position without issuing one UPDATE
    # per card through executemany().
    assignments = " ".join(
        "WHEN ? THEN ?" for _card_uid in ordered)
    params = []
    for position, card_uid in enumerate(ordered):
        params.extend((card_uid, position))
    connection.execute(
        "UPDATE game_cards SET position=CASE card_uid "
        + assignments + " END "
        "WHERE session_id=? AND user_id=? AND location='deck'",
        (*params, session_id, user_id))
    if connection is _pvp_db._shared_db_connection():
        connection.commit()
    return [card_uid for card_uid in ordered if card_uid in wanted]


def db_insert_generated_card(session_id, owner_id, card_uid, template_guid,
                             location, card_type, abilities_json, attributes,
                             row_id, conn=None, position=0, card_state=0,
                             owner_user_id=None, original_template_guid=None,
                             gems=None, permanent_buffs=None):
    """Insert a generated copy in the caller's transaction."""
    connection = conn or _pvp_db._db_layer._db
    columns = ["id", "session_id", "user_id", "card_uid", "template_guid",
               "card_template_id", "location", "position", "card_state",
               "card_abilities", "card_type", "card_attributes"]
    values = [int(row_id), session_id, int(owner_id), int(card_uid),
              template_guid, template_guid, location, int(position),
              int(card_state), abilities_json or "[]", card_type,
              int(attributes or 0)]
    existing = {row[1] for row in connection.execute(
        "PRAGMA table_info(game_cards)").fetchall()}
    if "gems" in existing and gems is None:
        gems = db_empty_socket_gems(template_guid, conn=connection)
    optional = {
        "owner_user_id": owner_id if owner_user_id is None else owner_user_id,
        "original_template_guid": (template_guid if original_template_guid is None
                                    else original_template_guid),
        "gems": gems,
        "permanent_buffs": permanent_buffs,
    }
    for column, value in optional.items():
        if column in existing and value is not None:
            columns.append(column)
            values.append(value)
    marks = ",".join("?" for _ in columns)
    return connection.execute(
        "INSERT INTO game_cards (" + ",".join(columns) + ") VALUES (" +
        marks + ")", values)


def db_move_choice_to_played_resources(session_id, card_uid, conn=None):
    """Consume a temporary choice card without granting resources."""
    return (conn or _pvp_db._db_layer._db).execute(
        "UPDATE game_cards SET location='PlayedResources', position=0, "
        "card_state=0 WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid)))


def db_kill_card_to_discard(session_id, card_uid, clear_mask, dead_state,
                            conn=None):
    """Move a troop to its discard pile and apply the death state reset."""
    connection = conn or _pvp_db._db_layer._db
    owner_row = connection.execute(
        "SELECT user_id FROM game_cards WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).fetchone()
    if not owner_row:
        return None
    owner = int(owner_row[0])
    position = connection.execute(
        "SELECT COALESCE(MAX(position) + 1, 1) FROM game_cards "
        "WHERE session_id=? AND user_id=? AND location='discard'",
        (session_id, owner)).fetchone()[0]
    connection.execute(
        "UPDATE game_cards SET user_id=?, location='discard', position=?, "
        "card_state=(card_state & ~?) | ?, card_damage=0, "
        "temporary_buffs='{}', temporary_attributes=0 "
        "WHERE session_id=? AND card_uid=?",
        (owner, int(position or 1), int(clear_mask), int(dead_state),
         session_id, int(card_uid)))
    return owner


def db_move_card_row_to_hand(session_id, row_id, conn=None):
    """Move one identified session-card row into hand."""
    return (conn or _pvp_db._db_layer._db).execute(
        "UPDATE game_cards SET location='hand', position=100 "
        "WHERE session_id=? AND id=?", (session_id, int(row_id))).rowcount


def db_hand_card_uids(session_id, user_id, conn=None):
    """Return hand card UIDs in authoritative position order."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT card_uid FROM game_cards WHERE session_id=? AND user_id=? "
        "AND location='hand' ORDER BY position", (session_id, user_id)
    ).fetchall()


def db_ordered_zone_rows(session_id, user_id, zone, conn=None):
    """Return database ID and card UID rows in zone position order."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT id, card_uid FROM game_cards WHERE session_id=? AND user_id=? "
        "AND location=? ORDER BY position, id", (session_id, user_id, zone)
    ).fetchall()


def db_move_card_to_discard_reset(session_id, card_uid, conn=None):
    """Mill one card to discard and clear its transient state."""
    return (conn or _pvp_db._db_layer._db).execute(
        "UPDATE game_cards SET location='discard', position=0, card_state=0 "
        "WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).rowcount


def db_ordered_zone_uids(session_id, user_id, location, conn=None):
    """Return all card UIDs in an owned zone by authoritative order."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid FROM game_cards WHERE session_id=? AND user_id=? "
        "AND location=? ORDER BY position, id",
        (session_id, int(user_id), location)).fetchall()


def db_hand_resources_with_template(session_id, user_id, conn=None):
    """Return hand resources with their printed grants and authored abilities."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT gc.id, gc.card_uid, gc.template_guid, "
        "COALESCE(ct.current_resources_granted, 0), "
        "COALESCE(ct.max_resources_granted, 0), ct.abilities_json "
        "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.user_id=? AND gc.location='hand' "
        "AND gc.card_type='Resource' ORDER BY gc.position LIMIT 1",
        (session_id, user_id),
    ).fetchall()


def db_hand_cards_with_templates(session_id, user_id, conn=None):
    """Return ordered hand cards with the fields used by play-option logic."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT gc.card_uid, ct.cost, ct.card_type, ct.threshold_json, "
        "ct.abilities_json FROM game_cards gc "
        "JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.user_id=? AND gc.location='hand' "
        "ORDER BY gc.position", (session_id, user_id)).fetchall()


def db_hand_cards_raw(session_id, user_id, conn=None):
    """Return ordered ``(card_uid, card_template_id, template_guid)`` rows."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid, card_template_id, template_guid FROM game_cards "
        "WHERE session_id=? AND user_id=? AND location='hand' "
        "ORDER BY position", (session_id, user_id)).fetchall()


def db_reveal_owned_card(session_id, card_uid, owner_id, conn=None):
    """Return one owned card row and its current zone for source reveals."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid, position, template_guid, user_id, card_state, "
        "location FROM game_cards WHERE session_id=? AND card_uid=? "
        "AND user_id=?",
        (session_id, int(card_uid), int(owner_id))).fetchone()


def db_reveal_cards(session_id, owner_id, zone, limit, card_uids=None,
                    conn=None):
    """Return ordered reveal rows, optionally restricted to candidate UIDs."""
    connection = conn or _pvp_db._db_layer._db
    params = [session_id, int(owner_id), zone]
    sql = ("SELECT card_uid, position, template_guid, user_id, card_state "
           "FROM game_cards WHERE session_id=? AND user_id=? "
           "AND location=?")
    if card_uids is not None:
        if not card_uids:
            return []
        marks = ",".join("?" for _ in card_uids)
        sql += " AND card_uid IN (" + marks + ")"
        params.extend(int(uid) for uid in card_uids)
    sql += " ORDER BY position"
    if card_uids is None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return connection.execute(sql, params).fetchall()


def db_move_card_if_in_zone(session_id, card_uid, owner_user_id, source_zone,
                            destination_zone, position=0, card_state=None,
                            conn=None):
    """Move an owned card only if it is still in the expected source zone."""
    connection = conn or _pvp_db._db_layer._db
    if card_state is None:
        cursor = connection.execute(
            "UPDATE game_cards SET location=?, position=? "
            "WHERE session_id=? AND card_uid=? AND user_id=? AND location=?",
            (destination_zone, position, session_id, int(card_uid),
             owner_user_id, source_zone),
        )
    else:
        cursor = connection.execute(
            "UPDATE game_cards SET location=?, position=?, card_state=? "
            "WHERE session_id=? AND card_uid=? AND user_id=? AND location=?",
            (destination_zone, position, card_state, session_id, int(card_uid),
             owner_user_id, source_zone),
        )
    connection.commit()
    return int(cursor.rowcount or 0)


def db_reset_warzone_troop(session_id, card_uid, clear_state_bits, conn=None):
    """Clear a troop's turn/combat state bits.

    Damage is not touched here: C# ``Session.ReadyCard`` only clears Tapped,
    and marked damage is removed solely by the end-of-turn heal
    (``clear_combat_damage``), which keeps ``CantHealAtEndOfTurn`` troops.
    """
    connection = conn or _pvp_db._db_layer._db
    connection.execute(
        "UPDATE game_cards SET card_state = card_state & ~? "
        "WHERE session_id=? AND card_uid=?",
        (clear_state_bits, session_id, int(card_uid)),
    )


def db_move_card_to_location(session_id, card_uid, location, position=0,
                             reset_state=False, conn=None):
    """Move a session card to a zone, optionally clearing its state bits."""
    if location not in {"deck", "hand", "warzone", "discard", "void",
                        "underground", "resources", "playedresources"}:
        raise ValueError("unsupported card location")
    connection = conn or _pvp_db._db_layer._db
    state_sql = ", card_state=0" if reset_state else ""
    cur = connection.execute(
        "UPDATE game_cards SET location='" + location + "', position=?" + state_sql +
        " WHERE session_id=? AND card_uid=?",
        (int(position), session_id, int(card_uid)))
    return getattr(cur, "rowcount", None)


def db_move_card_rows(row_ids, destination_zone, position=0, conn=None):
    """Move cards identified by stable game-card row IDs without committing."""
    if not row_ids:
        return
    connection = conn or _pvp_db._db_layer._db
    connection.executemany(
        "UPDATE game_cards SET location=?, position=? WHERE id=?",
        [(destination_zone, position, int(row_id)) for row_id in row_ids],
    )


def db_insert_game_cards(rows, conn=None):
    """Insert prepared player or opponent cards with the same card shape."""
    if not rows:
        return
    connection = conn or _pvp_db._db_layer._db
    connection.executemany(
        "INSERT INTO game_cards (user_id, session_id, card_uid, "
        "card_template_id, card_type, template_guid, location, position, "
        "owner_user_id, original_template_guid, card_abilities, "
        "card_attributes, card_uses, gems) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
