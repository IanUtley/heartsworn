import json
import sqlite3
import pvp_db as _pvp_db
from pvp_db_ability import (
    _cached_static_row,
)
from pvp_db_card import (
    flush_pending_session_saves,
)


def db_cleanup_stale_sessions(age_days=7):
    """Remove abandoned non-tournament game sessions and their card rows.

    Practice/FRA/PvE sessions are not owned by the tournament replay pipeline,
    so nothing else removes them once their client is gone: the
    ``game_sessions`` row and a full deck copy of ``game_cards`` would live
    forever.  A session is eligible only when it is older than ``age_days``,
    is not referenced by ``tournaments`` or ``tournament_matches``, and has no
    replay awaiting indexing (a ready replay is retained and lets the session
    go).  Tournament state keeps its own replay gate in
    ``db.db_tournament_cleanup_old``.

    Runs in its own short-lived write transaction, like the tournament
    cleanup, so a handler's open transaction cannot be inherited or blocked.
    """
    # A queued session save must land before this statement, or it would
    # re-insert the row after the delete.
    flush_pending_session_saves()
    try:
        days = max(1, int(age_days))
    except (TypeError, ValueError):
        days = 7
    cutoff = f"-{days} days"
    owns_connection = True
    # Keep an in-memory/shared test connection usable; production always uses
    # a short-lived connection instead of the process-wide RetryingConnection.
    if isinstance(_pvp_db._db_layer._db, sqlite3.Connection) and not isinstance(
            _pvp_db._db_layer._db, _pvp_db._db_layer.RetryingConnection):
        connection = _pvp_db._db_layer._db
        owns_connection = False
    else:
        connection = sqlite3.connect(_pvp_db._db_layer.DB_PATH, timeout=0.5)
    try:
        connection.execute("PRAGMA busy_timeout=500")
        connection.execute("BEGIN IMMEDIATE")

        def _has(table):
            return bool(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,)).fetchone())

        optional = {
            name for name in ("tournaments", "tournament_matches",
                              "session_events", "session_packets",
                              "session_transactions",
                              "game_replays")
            if _has(name)
        }
        clauses = []
        if "tournaments" in optional:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM tournaments t WHERE "
                "CAST(t.session_id AS TEXT)=CAST(gs.session_id AS TEXT))")
        if "tournament_matches" in optional:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM tournament_matches tm WHERE "
                "CAST(tm.session_id AS TEXT)=CAST(gs.session_id AS TEXT))")
        if "game_replays" in optional:
            clauses.append(
                "(NOT EXISTS (SELECT 1 FROM game_replays gr WHERE "
                "CAST(gr.session_id AS TEXT)=CAST(gs.session_id AS TEXT)) "
                "OR EXISTS (SELECT 1 FROM game_replays gr WHERE "
                "CAST(gr.session_id AS TEXT)=CAST(gs.session_id AS TEXT) "
                "AND gr.status='ready'))")

        query = ("SELECT gs.session_id FROM game_sessions gs "
                 "WHERE gs.created_at IS NOT NULL "
                 "AND datetime(gs.created_at) <= datetime('now', ?)")
        if clauses:
            query += " AND " + " AND ".join(clauses)
        stale = [str(row[0]) for row in connection.execute(
            query, (cutoff,)).fetchall() if row[0] is not None]

        result = dict(_pvp_db._STALE_SESSION_ZERO)
        for session_id in stale:
            if "session_events" in optional:
                result["events_removed"] += int(connection.execute(
                    "DELETE FROM session_events "
                    "WHERE CAST(session_id AS TEXT)=?",
                    (session_id,)).rowcount or 0)
            if "session_packets" in optional:
                connection.execute(
                    "DELETE FROM session_packets "
                    "WHERE CAST(session_id AS TEXT)=?", (session_id,))
            if "session_transactions" in optional:
                result["transactions_removed"] += int(connection.execute(
                    "DELETE FROM session_transactions "
                    "WHERE CAST(session_id AS TEXT)=?",
                    (session_id,)).rowcount or 0)
            result["cards_removed"] += int(connection.execute(
                "DELETE FROM game_cards WHERE CAST(session_id AS TEXT)=?",
                (session_id,)).rowcount or 0)
            result["sessions_removed"] += int(connection.execute(
                "DELETE FROM game_sessions WHERE CAST(session_id AS TEXT)=?",
                (session_id,)).rowcount or 0)
        # Source rows whose session and replay are both gone can never be read
        # again (the replay worker expires rows that still have a game_replays
        # record).  Reclaim them in the same pass.
        def _orphan_sql(table):
            guard = ""
            if "game_replays" in optional:
                guard = (" AND NOT EXISTS (SELECT 1 FROM game_replays gr "
                         f"WHERE CAST(gr.session_id AS TEXT)="
                         f"CAST({table}.session_id AS TEXT))")
            return ("DELETE FROM " + table + " WHERE NOT EXISTS ("
                    "SELECT 1 FROM game_sessions gs WHERE "
                    f"CAST(gs.session_id AS TEXT)="
                    f"CAST({table}.session_id AS TEXT))" + guard)

        if "session_events" in optional:
            result["events_removed"] += int(connection.execute(
                _orphan_sql("session_events")).rowcount or 0)
        if "session_transactions" in optional:
            result["transactions_removed"] += int(connection.execute(
                _orphan_sql("session_transactions")).rowcount or 0)
        connection.commit()
        return result
    except sqlite3.OperationalError as exc:
        try:
            connection.rollback()
        except sqlite3.Error:
            pass
        # A busy database is a normal scheduler condition; leave this pass for
        # the next interval instead of killing the scheduler thread.
        if _pvp_db._db_layer._is_sqlite_lock_error(exc):
            return dict(_pvp_db._STALE_SESSION_ZERO)
        raise
    finally:
        if owns_connection:
            connection.close()


def db_game_card_effective_cost(session_id, card_uid, battle_state=None,
                                conn=None):
    """Return the payable cost including instance/static cost modifiers."""
    connection = conn or _pvp_db._db_layer._db
    try:
        from rules_port.static_rules import effective_cost
        return max(0, int(effective_cost(
            connection, session_id, battle_state or {}, int(card_uid))))
    except Exception:
        if (battle_state or {}).get("_rules_port_attached"):
            raise
        try:
            row = connection.execute(
                "SELECT ct.cost, gc.card_cost_mod "
                "FROM game_cards gc JOIN card_templates ct "
                "ON ct.guid=gc.template_guid "
                "WHERE gc.session_id=? AND gc.card_uid=?",
                (session_id, int(card_uid))).fetchone()
        except Exception:
            row = connection.execute(
                "SELECT ct.cost FROM game_cards gc "
                "JOIN card_templates ct ON ct.guid=gc.template_guid "
                "WHERE gc.session_id=? AND gc.card_uid=?",
                (session_id, int(card_uid))).fetchone()
        if not row:
            return 0
        return max(0, int(row[0] or 0) +
                   (int(row[1] or 0) if len(row) > 1 else 0))


def db_game_card_effective_attributes(session_id, card_uid, battle_state=None,
                                      conn=None):
    """Return the attribute bits that gate a card's combat legality.

    ``game_cards.card_attributes`` holds only the attributes written directly
    on the instance.  Template attributes, temporary grants (a troop that
    surfaced this turn carries Speed here) and static modifiers project on top
    of it, so a legality predicate that reads the instance column alone
    rejects an attack the option list already offered the client.
    """
    connection = conn or _pvp_db._db_layer._db
    from rules_port.static_rules import effective_attributes
    return int(effective_attributes(
        connection, session_id, battle_state or {}, int(card_uid)) or 0)


def db_game_champion(session_id, user_id, conn=None):
    """Return ``(card_uid, template_guid)`` for a session champion."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid, template_guid FROM game_cards "
        "WHERE session_id=? AND user_id=? AND is_champion=1 LIMIT 1",
        (session_id, user_id)).fetchone()


def db_clear_session_cards(session_id, conn=None):
    """Delete all mutable game-card rows for a session."""
    connection = conn or _pvp_db._db_layer._db
    connection.execute("DELETE FROM game_cards WHERE session_id=?", (session_id,))
    if conn is None:
        connection.commit()


def db_delete_game_session(session_id, conn=None):
    """Delete all database-owned rows for a completed game session."""
    # A queued session save must land before this statement, or it would
    # re-insert the row after the delete.
    flush_pending_session_saves()
    connection = conn or _pvp_db._db_layer._db
    sid = str(session_id)
    for table in ("game_cards", "session_events", "session_transactions",
                  "game_sessions"):
        connection.execute(f"DELETE FROM {table} WHERE session_id=?", (sid,))
    if conn is None:
        connection.commit()


def db_game_session_pids(session_id, conn=None):
    """Return the actual player IDs participating in a game session."""
    connection = conn or _pvp_db._db_layer._db
    tournament_rows = connection.execute(
        "SELECT DISTINCT ts.player_uid FROM tournaments t "
        "JOIN tournament_signups ts ON ts.tournament_id=t.id "
        "WHERE t.session_id=? ORDER BY ts.player_uid", (session_id,)
    ).fetchall()
    if len(tournament_rows) >= 2:
        return [row[0] for row in tournament_rows]
    rows = connection.execute(
        "SELECT DISTINCT user_id FROM game_cards WHERE session_id=?",
        (session_id,)).fetchall()
    return [row[0] for row in rows]


def db_game_cards_at_location(session_id, location, card_type=None,
                              user_id=None, conn=None):
    """Return ordered full card rows for one game zone."""
    connection = conn or _pvp_db._db_layer._db
    sql = ("SELECT card_uid, template_guid, user_id, card_type, card_state, "
           "card_abilities, card_attributes FROM game_cards "
           "WHERE session_id=? AND location=?")
    params = [session_id, location]
    if card_type is not None:
        sql += " AND card_type=?"
        params.append(card_type)
    if user_id is not None:
        sql += " AND user_id=?"
        params.append(user_id)
    sql += " ORDER BY position, id"
    return connection.execute(sql, params).fetchall()


def db_game_cards_at_location_scalar(session_id, location, user_id=None,
                                     conn=None):
    """Return ordered card UIDs for one game zone."""
    connection = conn or _pvp_db._db_layer._db
    sql = "SELECT card_uid FROM game_cards WHERE session_id=? AND location=?"
    params = [session_id, location]
    if user_id is not None:
        sql += " AND user_id=?"
        params.append(user_id)
    sql += " ORDER BY position, id"
    return [row[0] for row in connection.execute(sql, params).fetchall()]


def db_game_deck_cards(session_id, user_id, conn=None):
    """Return ordered ``(card_uid, template_guid)`` deck rows."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid, template_guid FROM game_cards "
        "WHERE session_id=? AND user_id=? AND location='deck' "
        "ORDER BY position", (session_id, user_id)).fetchall()


def db_game_draw_cards(session_id, user_id, count=7, conn=None):
    """Move the first ordered deck cards into hand and return their rows."""
    connection = conn or _pvp_db._db_layer._db
    rows = connection.execute(
        "SELECT card_uid, template_guid FROM game_cards "
        "WHERE session_id=? AND user_id=? AND location='deck' "
        "ORDER BY position LIMIT ?", (session_id, user_id, count)
    ).fetchall()
    for card_uid, _template_guid in rows:
        connection.execute(
            "UPDATE game_cards SET location='hand' "
            "WHERE card_uid=? AND session_id=?", (int(card_uid), session_id))
    if conn is None:
        connection.commit()
    return rows


def deck_rng_index(rng, bound):
    """Return a uniform index in ``[0, bound)`` from the session RNG.

    Falls back to the stdlib RNG when no session RNG is bound.  ``bound <= 0``
    returns 0, matching C# ``Random.Next`` callers that guard on a positive
    count.
    """
    if bound <= 0:
        return 0
    if rng is not None and hasattr(rng, "next"):
        # Next(1) still advances the C# RNG, so draw even for one slot.
        return int(rng.next(bound)) % bound
    import random as _shuf_rnd
    return _shuf_rnd.randrange(bound)


def db_game_shuffle_deck(session_id, user_id, conn=None, rng=None):
    """Shuffle one player's deck positions within the caller's transaction.

    ``rng`` is the battle's session RNG (``bstate["_rules_rng"]``); it is
    optional so legacy callers keep the stdlib behaviour.
    """
    connection = conn or _pvp_db._db_layer._db
    rows = connection.execute(
        "SELECT card_uid FROM game_cards "
        "WHERE session_id=? AND user_id=? AND location='deck' "
        "ORDER BY position", (session_id, user_id)).fetchall()
    remaining = [int(row[0]) for row in rows]
    # C# CardCollection.Shuffle repeatedly takes Next(count) from the
    # remaining cards.
    deck = [remaining.pop(deck_rng_index(rng, len(remaining)))
            for _ in range(len(remaining))]
    connection.executemany(
        "UPDATE game_cards SET position=? "
        "WHERE card_uid=? AND session_id=?",
        [(position, card_uid, session_id)
         for position, card_uid in enumerate(deck)])
    if conn is None:
        connection.commit()


def db_game_get_hand(session_id, user_id, conn=None):
    """Return ordered ``(card_uid, template_guid)`` hand rows."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid, template_guid FROM game_cards "
        "WHERE session_id=? AND user_id=? AND location='hand' "
        "ORDER BY position", (session_id, user_id)).fetchall()


def db_game_card_type(template_guid, conn=None):
    """Return a template's stored card type, defaulting legacy rows to Troop."""
    if not template_guid:
        return "Troop"
    connection = conn or _pvp_db._db_layer._db
    row = _cached_static_row(
        connection, ("card_type", str(template_guid).lower()),
        "SELECT card_type FROM card_templates WHERE guid=?",
        (template_guid,))
    return row[0] if row else "Troop"


def db_decrement_card_cooldowns_for_owner(session_id, owner_id, conn=None):
    """Decrement the active player's card cooldowns at the Ready boundary.

    C# processes Warzone, Champions, then Hand for the active player. The
    mutable session projection represents those regular cards in
    ``game_cards``; synthetic champion cooldowns live in battle state.
    """
    connection = conn or _pvp_db._db_layer._db
    owner = int(owner_id or 0)
    with _pvp_db._CARD_COOLDOWN_LOCK:
        rows = connection.execute(
            "SELECT card_uid, card_uses FROM game_cards "
            "WHERE session_id=? AND user_id=? "
            "AND location IN ('warzone','champions','hand')",
            (session_id, owner)).fetchall()
        changed = []
        for card_uid, raw in rows:
            try:
                uses = dict(json.loads(raw or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                uses = {}
            counts = uses.get(_pvp_db._CARD_COOLDOWNS_KEY)
            if not isinstance(counts, dict) or not counts:
                continue
            updated = {}
            for guid, value in counts.items():
                try:
                    remaining = int(value or 0) - 1
                except (TypeError, ValueError):
                    continue
                if remaining > 0:
                    updated[str(guid).lower()] = remaining
            if updated:
                uses[_pvp_db._CARD_COOLDOWNS_KEY] = updated
            else:
                uses.pop(_pvp_db._CARD_COOLDOWNS_KEY, None)
            connection.execute(
                "UPDATE game_cards SET card_uses=? "
                "WHERE session_id=? AND card_uid=?",
                (json.dumps(uses, separators=(",", ":")),
                 session_id, int(card_uid)))
            changed.append(int(card_uid))
        if conn is None:
            connection.commit()
        return changed


def db_condition_card_row(session_id, card_uid, conn=None):
    """Return the full mutable/template projection used by conditions. Index 22 is the template's socket count."""
    connection = conn or _pvp_db._db_layer._db
    sockets = _target_projection_columns(connection)[1]
    return connection.execute(
        "SELECT gc.card_uid, COALESCE(gc.card_type, ct.card_type), "
        "gc.location, gc.user_id, gc.card_state, "
        "COALESCE(ct.attack,0), COALESCE(ct.defense,0), gc.template_guid, "
        "ct.name, COALESCE(ct.cost,0), ct.subtype, ct.threshold_json, "
        "gc.card_attributes, ct.attributes, gc.card_attack_mod, "
        "gc.card_defense_mod, COALESCE(gc.permanent_buffs,'{}') "
        ", COALESCE(gc.card_abilities, '[]'), gc.position, "
        "COALESCE(gc.original_template_guid,''), COALESCE(gc.gems,0), "
        "COALESCE(ct.rarity,''), " + sockets + " "
        "FROM game_cards gc LEFT JOIN card_templates ct "
        "ON ct.guid = gc.template_guid "
        "WHERE gc.session_id=? AND gc.card_uid=?",
        (session_id, int(card_uid)),
    ).fetchone()


def db_condition_cards_in_zones(session_id, zones, user_id=None, conn=None):
    """Return card projections needed for zone-based condition filters."""
    if not zones:
        return []
    connection = conn or _pvp_db._db_layer._db
    marks = ",".join("?" for _ in zones)
    params = [session_id] + list(zones)
    sql = (
        "SELECT gc.card_uid, gc.card_type, gc.location, gc.user_id, "
        "gc.card_state, COALESCE(ct.attack,0), COALESCE(ct.defense,0), "
        "ct.name, COALESCE(ct.cost,0), ct.subtype, ct.threshold_json, "
        "gc.card_attributes, ct.attributes, gc.template_guid, "
        "COALESCE(gc.permanent_buffs,'{}') FROM game_cards gc "
        "JOIN card_templates ct ON ct.guid = gc.template_guid "
        "WHERE gc.session_id=? AND gc.location IN (" + marks + ")"
    )
    if user_id is not None:
        sql += " AND gc.user_id=?"
        params.append(user_id)
    return connection.execute(sql, params).fetchall()


def db_effect_condition_json(condition_id, conn=None):
    """Return serialized metadata for one BOM effect condition."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT condition_json FROM ability_effect_conditions "
        "WHERE condition_id=?", (condition_id,)).fetchone()
    return row[0] if row else None


def db_deck_top_card(session_id, user_id, conn=None):
    """Return the next card in a player's deck by authoritative position."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT card_uid, template_guid, card_template_id FROM game_cards "
        "WHERE session_id=? AND user_id=? AND location='deck' "
        "ORDER BY position LIMIT 1", (session_id, int(user_id))).fetchone()


def db_deck_card_by_uid(session_id, user_id, card_uid, conn=None):
    """Return a specific card instance while it remains in a deck."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid, template_guid, card_template_id FROM game_cards "
        "WHERE session_id=? AND user_id=? AND location='deck' "
        "AND card_uid=? LIMIT 1",
        (session_id, int(user_id), int(card_uid))).fetchone()


def db_deck_card_by_name(session_id, user_id, name_fragment, conn=None):
    """Return the first deck card matching an authored display name."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT gc.card_uid, gc.template_guid, gc.card_template_id "
        "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.user_id=? AND gc.location='deck' "
        "AND LOWER(ct.name) LIKE LOWER(?) ORDER BY gc.position LIMIT 1",
        (session_id, int(user_id), "%" + str(name_fragment) + "%")).fetchone()


def db_gencard_template(name, conn=None):
    """Return the preferred authored template for a debug card grant."""
    connection = conn or _pvp_db._db_layer._db
    try:
        return connection.execute(
            "SELECT guid, card_type, cost, attack, defense, abilities_json, attributes "
            "FROM card_templates WHERE LOWER(name) LIKE ? "
            "ORDER BY CASE WHEN LOWER(name)=? THEN 0 ELSE 1 END, "
            "COALESCE(equipment_modified,0) ASC, no_pvp ASC, is_pve ASC, guid ASC LIMIT 1",
            ("%" + str(name) + "%", str(name))).fetchone()
    except Exception:
        return connection.execute(
            "SELECT guid, card_type, cost, attack, defense, abilities_json, attributes "
            "FROM card_templates WHERE LOWER(name) LIKE ? "
            "ORDER BY CASE WHEN LOWER(name)=? THEN 0 ELSE 1 END, "
            "no_pvp ASC, is_pve ASC, guid ASC LIMIT 1",
            ("%" + str(name) + "%", str(name))).fetchone()


def db_deck_cards_json(deck_id, user_id, conn=None):
    """Return a deck's serialized card list when owned by the user."""
    connection = conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT cards FROM decks WHERE id=? AND user_id=?",
        (int(deck_id), int(user_id))).fetchone()
    return row[0] if row else None


def db_deck_active_gems(deck_id, conn=None):
    """Return the serialized active-gem map for a deck."""
    connection = conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT active_gems FROM decks WHERE id=?", (int(deck_id),)
    ).fetchone()
    return row[0] if row else None


def db_empty_socket_gems(template_guid, conn=None):
    """Return the client value for an unfilled socketable card.

    Generated cards do not inherit a gem unless their authored effect says
    to copy one. Socketable cards still need the new gem-format marker so
    the client renders their empty sockets and permits socketing. Keep the
    lookup tolerant of older focused test schemas that predate
    ``card_templates.socket_count``.
    """
    connection = conn or _pvp_db._db_layer._db
    try:
        row = _cached_static_row(
            connection, ("card_socket_count", str(template_guid).lower()),
            "SELECT socket_count FROM card_templates WHERE guid=?",
            (str(template_guid).lower(),))
    except sqlite3.OperationalError as exc:
        if "no such column" not in str(exc).lower():
            raise
        return 0
    return (_pvp_db.EMPTY_SOCKET_GEMS if row and int(row[0] or 0) > 0 else 0)


def db_first_template_guid(conn=None):
    """Return a deterministic catalog fallback template, if available."""
    connection = conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT guid FROM card_templates ORDER BY guid LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def db_encounter_deck_cards(deck_guid, conn=None):
    """Return ``(card_guid, quantity, gem_types_new_list_json)`` rows for an encounter deck.

    Served from campaign_content (Records and campaign/campaign_data): scene opponent
    decks and Arena decks, with unresolved GUIDs mapped by the card map. Rows keep
    their order and count, since callers index each row's gem slots. ``conn`` is
    unused and kept so callers do not change.
    """
    import campaign_source
    campaign_source.ensure_campaign_path()
    import campaign_content

    return [(row["card_guid"], row["quantity"], row["gem_types_new_list_json"])
            for row in campaign_content.deck_cards(deck_guid)]


def db_debug_template(template_guid, conn=None):
    """Return the template projection required by DebugCheatTransaction."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT guid, card_type, name, cost, attack, defense, "
        "abilities_json, attributes, threshold_json "
        "FROM card_templates WHERE guid=?", (str(template_guid).lower(),)
    ).fetchone()


def db_debug_nonchampion_cards(session_id, owner_id, conn=None):
    """Return non-champion cards that a debug nuke may move to the void."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid FROM game_cards WHERE session_id=? AND user_id=? "
        "AND is_champion=0 AND location!='void'",
        (session_id, int(owner_id))).fetchall()


def db_clear_warzone_damage(session_id, conn=None):
    """Clear marked damage from cards currently in the warzone.

    C# ``Card.ResetCardDamage`` also clears ``ECardStates.Damaged``.
    """
    return (conn or _pvp_db._db_layer._db).execute(
        "UPDATE game_cards SET card_damage=0, "
        "card_state = COALESCE(card_state, 0) & ? "
        "WHERE session_id=? AND location='warzone'",
        (~_pvp_db._DAMAGED_STATE_BIT, session_id))


def db_clear_warzone_damage_except(session_id, exempt_uids, conn=None):
    """Clear warzone damage except cards that cannot heal at end of turn."""
    uids = [int(uid) for uid in exempt_uids or ()]
    if not uids:
        return db_clear_warzone_damage(session_id, conn=conn)
    marks = ",".join("?" for _ in uids)
    return (conn or _pvp_db._db_layer._db).execute(
        "UPDATE game_cards SET card_damage=0, "
        "card_state = COALESCE(card_state, 0) & ? "
        "WHERE session_id=? AND location='warzone' "
        "AND card_uid NOT IN (" + marks + ")",
        (~_pvp_db._DAMAGED_STATE_BIT, session_id, *uids))


def db_deck_card_count(session_id, user_id, conn=None):
    """Count cards currently in one player's deck."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT COUNT(*) FROM game_cards WHERE session_id=? AND user_id=? "
        "AND location='deck'", (session_id, int(user_id))).fetchone()
    return int(row[0] if row else 0)


def _target_projection_columns(connection):
    """Return optional columns used by the generic target projection."""
    # Target candidate enumeration is repeated for each generated target
    # witness in the card contract sweep.  Reading both table schemas on every
    # call made a large catalogue pass spend most of its time in PRAGMA table
    # introspection.  Include SQLite's schema version so migrations and test
    # fixture DDL naturally invalidate the cached projection.
    version_row = connection.execute("PRAGMA schema_version").fetchone()
    cache_key = (connection, int(version_row[0] or 0)
                 if version_row else 0)
    with _pvp_db._TARGET_PROJECTION_LOCK:
        cached = _pvp_db._TARGET_PROJECTION_CACHE.get(cache_key)
        if cached is not None:
            _pvp_db._TARGET_PROJECTION_CACHE.move_to_end(cache_key)
            return cached
    gc = {row[1] for row in connection.execute(
        "PRAGMA table_info(game_cards)").fetchall()}
    ct = {row[1] for row in connection.execute(
        "PRAGMA table_info(card_templates)").fetchall()}
    result = (
        "ct.rarity" if "rarity" in ct else "''",
        "ct.socket_count" if "socket_count" in ct else "0",
        "gc.gems" if "gems" in gc else "0",
        "gc.original_template_guid" if "original_template_guid" in gc else "''",
        "gc.card_abilities" if "card_abilities" in gc else "'[]'",
    )
    with _pvp_db._TARGET_PROJECTION_LOCK:
        _pvp_db._TARGET_PROJECTION_CACHE[cache_key] = result
        _pvp_db._TARGET_PROJECTION_CACHE.move_to_end(cache_key)
        while len(_pvp_db._TARGET_PROJECTION_CACHE) > _pvp_db._TARGET_PROJECTION_CACHE_LIMIT:
            _pvp_db._TARGET_PROJECTION_CACHE.popitem(last=False)
    return result


def db_resolve_talent_modified_template(template_guid, talent_guids,
                                         conn=None):
    """Select the authored alternate card for the active champion talents.

    CardTemplate's serialized TAC is the client-owned source of the
    alternate-version relationship and its RequiredTalents conditions.  Do
    not select a variant by card name or by the ability text: the same rule is
    used for every talent-modified generated card.
    """
    active = {str(value).lower() for value in (talent_guids or [])}
    if not active:
        return str(template_guid).lower()
    from gamedata import DEFAULT_RECORD_STORE
    from rules_port.tac import (_tac_attr_hash, decode_tac_tree)
    record = DEFAULT_RECORD_STORE.get("CardTemplate", str(template_guid))
    if record is None:
        return str(template_guid).lower()
    serialized = record.field("m_SerializedTAC", {}) or {}
    data = serialized.get("data") if isinstance(serialized, dict) else serialized
    tree = decode_tac_tree(data)
    alternates = tree.get(_tac_attr_hash("AlternateVersions"), [])
    chosen = str(template_guid).lower()
    best = 0
    connection = conn or _pvp_db._db_layer._db
    for alternate in alternates if isinstance(alternates, list) else []:
        if not isinstance(alternate, dict):
            continue
        guid = alternate.get(_tac_attr_hash("Guid"))
        condition = alternate.get(_tac_attr_hash("Condition"), {})
        required = condition.get(_tac_attr_hash("RequiredTalents"), []) \
            if isinstance(condition, dict) else []
        required = {
            str(item.get(_tac_attr_hash("Guid"))).lower()
            for item in required if isinstance(item, dict)
        }
        if not guid or not required.issubset(active) or len(required) <= best:
            continue
        exists = connection.execute(
            "SELECT 1 FROM card_templates WHERE guid=? LIMIT 1", (str(guid),)
        ).fetchone()
        if exists:
            chosen, best = str(guid).lower(), len(required)
    return chosen


def db_copy_template_payload(template_guid, conn=None, talent_guids=None):
    """Return typed fields needed to materialize a generated copy."""
    resolved = db_resolve_talent_modified_template(
        template_guid, talent_guids, conn=conn)
    connection = conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT card_type, abilities_json, attributes FROM card_templates "
        "WHERE guid=?", (resolved,)).fetchone()
    if row is None:
        # A champion template (the target of a champion transform) has no card
        # row; its payload is its ability list, as the champion was built from.
        abilities = [str(value[0]).lower() for value in connection.execute(
            "SELECT ability_guid FROM champion_abilities WHERE champion_guid=? "
            "ORDER BY ability_guid", (str(resolved).lower(),)).fetchall()]
        if abilities:
            return ("Champion", json.dumps(abilities), 0)
    return row


def db_deck_next_position(session_id, owner_id, conn=None):
    """Return the next stable position for a generated deck card."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM game_cards "
        "WHERE session_id=? AND user_id=? AND location='deck'",
        (session_id, int(owner_id))).fetchone()
    return int(row[0])


def db_copy_transform_instance_state(session_id, target_uid, source_uid, *,
                                     is_replica=False, conn=None):
    """Copy persisted ``Card.CopyFrom`` fields for TransformIntoCopy.

    All copies take the source's active gems, card integer variables, and
    escalation count. Ordinary copies also take damage and use state and add
    the source's counters/shields. Active C# effect instances, activation data,
    extended/resource-spend fields, and session-only boon maps are not
    represented by these rows.
    """
    connection = conn or _pvp_db._db_layer._db
    session_id = int(session_id)
    target_uid = int(target_uid)
    source_uid = int(source_uid)
    with _pvp_db._CARD_COOLDOWN_LOCK:
        target = connection.execute(
            "SELECT gems, card_damage, card_uses, permanent_buffs, "
            "temporary_buffs FROM game_cards "
            "WHERE session_id=? AND card_uid=?", (session_id, target_uid)
        ).fetchone()
        source = connection.execute(
            "SELECT gems, card_damage, card_uses, permanent_buffs, "
            "temporary_buffs FROM game_cards "
            "WHERE session_id=? AND card_uid=?", (session_id, source_uid)
        ).fetchone()
        if not target or not source:
            return 0

        def read_payload(value):
            try:
                parsed = json.loads(value or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = {}
            return parsed if isinstance(parsed, dict) else {}

        permanent = read_payload(target[3])
        temporary = read_payload(target[4])
        source_permanent = read_payload(source[3])
        source_temporary = read_payload(source[4])

        # Card.CopyFrom copies these fields even when making a replica.
        source_variables = source_permanent.get("card_integer_variables", {})
        if isinstance(source_variables, dict) and source_variables:
            permanent["card_integer_variables"] = source_variables
        else:
            permanent.pop("card_integer_variables", None)
        try:
            permanent["escalation_count"] = int(
                source_permanent.get("escalation_count", 1))
        except (TypeError, ValueError):
            permanent["escalation_count"] = 1

        if is_replica:
            return connection.execute(
                "UPDATE game_cards SET gems=?, permanent_buffs=? "
                "WHERE session_id=? AND card_uid=?",
                (source[0], json.dumps(permanent, separators=(",", ":")),
                 session_id, target_uid)).rowcount

        target_counters = permanent.setdefault("counters", {})
        source_counters = source_permanent.get("counters", {})
        if isinstance(target_counters, dict) and isinstance(source_counters, dict):
            for name, value in source_counters.items():
                target_counters[name] = (
                    int(target_counters.get(name, 0) or 0) + int(value or 0))
        target_counter_guids = permanent.setdefault("counter_guids", {})
        source_counter_guids = source_permanent.get("counter_guids", {})
        if isinstance(target_counter_guids, dict) and isinstance(
                source_counter_guids, dict):
            target_counter_guids.update(source_counter_guids)

        for destination, copied in ((permanent, source_permanent),
                                    (temporary, source_temporary)):
            existing = destination.get("damage_shields", [])
            incoming = copied.get("damage_shields", [])
            if not isinstance(existing, list):
                existing = []
            if isinstance(incoming, list) and incoming:
                destination["damage_shields"] = existing + incoming
        try:
            source_uses = json.loads(source[2] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            source_uses = {}
        if not isinstance(source_uses, dict):
            source_uses = {}
        return connection.execute(
            "UPDATE game_cards SET gems=?, card_damage=?, card_uses=?, "
            "permanent_buffs=?, temporary_buffs=? "
            "WHERE session_id=? AND card_uid=?",
            (source[0], int(source[1] or 0),
             json.dumps(source_uses, separators=(",", ":")),
             json.dumps(permanent, separators=(",", ":")),
             json.dumps(temporary, separators=(",", ":")),
             session_id, target_uid)).rowcount


def db_deck_top_card_details(session_id, user_id, conn=None):
    """Return the top deck row needed for a draw transaction."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT id, card_uid, card_template_id, template_guid "
        "FROM game_cards WHERE session_id=? AND user_id=? AND location='deck' "
        "ORDER BY position LIMIT 1", (session_id, user_id)).fetchone()


def db_draw_card_to_hand(session_id, row_id, owner_user_id, conn=None):
    """Move one deck row to hand and assign its current controller."""
    return (conn or _pvp_db._db_layer._db).execute(
        "UPDATE game_cards SET user_id=?, location='hand', position=100 "
        "WHERE session_id=? AND id=?",
        (int(owner_user_id), session_id, int(row_id))).rowcount


def db_counter_template_name(template_id, conn=None):
    """Return the display name for a serialized counter template ID."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT name FROM card_counter_templates WHERE template_id=?",
        (template_id,)).fetchone()
    return row[0] if row else None


def db_counter_template_id(name, conn=None):
    """Return the counter-template ID matching a display name."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT template_id FROM card_counter_templates "
        "WHERE LOWER(name)=LOWER(?)", (name,)).fetchone()
    return row[0] if row else None


def db_deck_card_uids(session_id, user_id, limit, conn=None):
    """Return the top deck card UIDs for an owner."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid FROM game_cards WHERE session_id=? AND user_id=? "
        "AND location='deck' ORDER BY position, id LIMIT ?",
        (session_id, int(user_id), int(limit))).fetchall()


def db_deck_top_card_type(session_id, user_id, conn=None):
    """Return the top deck card UID and type."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid, card_type FROM game_cards WHERE session_id=? "
        "AND user_id=? AND location='deck' ORDER BY position, id LIMIT 1",
        (session_id, int(user_id))).fetchone()


def db_cleanup_game_sessions(conn=None):
    """Remove ended sessions and cards orphaned by session cleanup."""
    # A queued session save must land before this statement, or it would
    # re-insert the row after the delete.
    flush_pending_session_saves()
    connection = conn or _pvp_db._db_layer._db
    connection.execute("DELETE FROM game_sessions WHERE state='ended'")
    connection.execute(
        "DELETE FROM game_cards WHERE session_id NOT IN "
        "(SELECT session_id FROM game_sessions)"
    )
    if conn is None:
        connection.commit()
