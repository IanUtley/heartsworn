import json
import sqlite3
from domain.enums import ECardStates
import pvp_db as _pvp_db
from pvp_db_ability import (
    _cached_static_row,
    db_gem_ability_guids,
)


def flush_pending_session_saves(commit=False):
    """Write this thread's queued session rows on the shared connection."""
    pending = getattr(_pvp_db._session_save_queue, "pending", None)
    if not pending:
        return
    rows = list(pending.values())
    pending.clear()
    for params in rows:
        _pvp_db._db_layer._db.execute(_pvp_db._SESSION_UPSERT_SQL, params)
    if commit:
        _pvp_db._db_layer._db.commit()


def db_card_template_field(template_guid, field, conn=None):
    """Return one approved printed template field by name."""
    valid = {"abilities_json", "attributes", "card_type", "sacrifice_target",
             "cost", "attack", "defense", "name", "threshold_json", "lethal"}
    if field not in valid:
        return None
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT " + field + " FROM card_templates WHERE guid=?",
        (template_guid,)).fetchone()
    return row[0] if row else None


def db_card_save_player_stops(user_id, self_stops_json, opp_stops_json,
                              conn=None):
    """Persist phase-stop preferences in the caller's transaction."""
    connection = conn or _pvp_db._db_layer._db
    connection.execute(
        "INSERT INTO user_prefs (user_id, self_stops, opp_stops) VALUES (?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET self_stops=excluded.self_stops, "
        "opp_stops=excluded.opp_stops",
        (user_id, self_stops_json, opp_stops_json))
    if conn is None:
        connection.commit()


def db_card_load_player_stops(user_id, conn=None):
    """Return persisted phase-stop preferences."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT self_stops, opp_stops FROM user_prefs WHERE user_id=?",
        (user_id,)).fetchone()
    if not row or not row[0]:
        return None, None
    return row[0], row[1] if row[1] else None


def db_card_sync_abilities(session_id, card_uid, abilities_json, attributes,
                           template_guid, commit=True, conn=None):
    """Synchronize an instance's authored abilities and reset usage state."""
    connection = conn or _pvp_db._db_layer._db
    connection.execute(
        "UPDATE game_cards SET card_abilities=?, card_attributes=?, "
        "card_uses='{}', original_template_guid = CASE "
        "WHEN original_template_guid IS NULL OR original_template_guid='' "
        "THEN ? ELSE original_template_guid END "
        "WHERE session_id=? AND card_uid=?",
        (abilities_json, attributes, template_guid, session_id, int(card_uid)),
    )
    if commit:
        connection.commit()


def db_card_ability_list(session_id, card_uid, conn=None):
    """Return the current normalized ability GUIDs on a game-card instance."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_abilities FROM game_cards WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).fetchone()
    if not row or not row[0]:
        return []
    try:
        return [guid.lower() for guid in json.loads(row[0])]
    except Exception:
        return []


def db_card_uses(session_id, card_uid, conn=None):
    """Return per-instance ability usage counts."""
    with _pvp_db._CARD_COOLDOWN_LOCK:
        row = (conn or _pvp_db._db_layer._db).execute(
            "SELECT card_uses FROM game_cards WHERE session_id=? AND card_uid=?",
            (session_id, int(card_uid))).fetchone()
    if not row or not row[0]:
        return {}
    try:
        return dict(json.loads(row[0]))
    except Exception:
        return {}


def db_bump_card_use(session_id, card_uid, ability_guid, conn=None):
    """Increment and return one instance ability's usage count."""
    connection = conn or _pvp_db._db_layer._db
    with _pvp_db._CARD_COOLDOWN_LOCK:
        uses = db_card_uses(session_id, card_uid, conn=connection)
        uses[ability_guid] = int(uses.get(ability_guid, 0)) + 1
        connection.execute(
            "UPDATE game_cards SET card_uses=? WHERE session_id=? AND card_uid=?",
            (json.dumps(uses), session_id, int(card_uid)))
        if conn is None:
            connection.commit()
        return uses[ability_guid]


def db_card_ability_use_counts(session_id, card_uid, ability_guid,
                              turn_number, conn=None):
    """Return ``(game, this-turn)`` uses without conflating their limits."""
    with _pvp_db._CARD_COOLDOWN_LOCK:
        uses = db_card_uses(session_id, card_uid, conn=conn)
        guid = str(ability_guid or "").lower()
        try:
            game_count = int(uses.get(guid, 0) or 0)
        except (TypeError, ValueError):
            game_count = 0
        per_turn = uses.get("__turn_uses__", {})
        key = f"{int(turn_number or 1)}:{guid}"
        try:
            turn_count = int(per_turn.get(key, 0) or 0) if isinstance(
                per_turn, dict) else 0
        except (TypeError, ValueError):
            turn_count = 0
        return game_count, turn_count


def db_card_cooldown_counts(session_id, card_uid, conn=None):
    """Return positive, per-ability cooldowns for a card instance."""
    with _pvp_db._CARD_COOLDOWN_LOCK:
        raw = db_card_uses(session_id, card_uid, conn=conn)
        counts = raw.get(_pvp_db._CARD_COOLDOWNS_KEY, {})
        if not isinstance(counts, dict):
            return {}
        result = {}
        for guid, value in counts.items():
            try:
                remaining = int(value or 0)
            except (TypeError, ValueError):
                continue
            if remaining > 0:
                result[str(guid).lower()] = remaining
        return result


def db_card_template_thresholds(template_guid, conn=None):
    """Return threshold, ability, and attribute fields for a template."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT threshold_json, abilities_json, attributes "
        "FROM card_templates WHERE guid=?", (template_guid,)).fetchone()


def db_card_basic(session_id, card_uid, conn=None):
    """Return ``(template_guid, user_id)`` for one game card."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT template_guid, user_id FROM game_cards "
        "WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).fetchone()


def db_card_with_template(session_id, card_uid, conn=None):
    """Return the legacy joined game-card/template projection."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT gc.template_guid, ct.card_type, gc.card_state, gc.user_id, "
        "gc.card_attributes, gc.card_template_id, gc.card_abilities, "
        "gc.card_attack_mod, gc.card_defense_mod, gc.card_damage, "
        "gc.original_template_guid FROM game_cards gc "
        "JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.card_uid=?",
        (session_id, int(card_uid))).fetchone()


def db_card_instance_dynamic_projection(session_id, card_uid, conn=None):
    """Return mutable instance state and ownership in one indexed lookup.

    The first eleven fields intentionally retain ``db_card_instance_full``'s
    order. Printed template fields are resolved from the process-wide Records
    cache by the caller, avoiding a repeated SQLite join on this hot path.
    The final two fields are the persisted instance reference and socketed gem;
    they let card encoding avoid two more point lookups.
    """
    connection = conn or _pvp_db._db_layer._db
    sql = (
        "SELECT gc.card_abilities, gc.card_attack_mod, gc.card_defense_mod, "
        "gc.card_damage, gc.original_template_guid, gc.permanent_buffs, "
        "gc.temporary_buffs, gc.card_cost_mod, gc.cost_mod_json, "
        "gc.card_attributes, gc.temporary_attributes, "
        "gc.user_id, gc.location, gc.template_guid, "
        "gc.card_template_id, gc.gems "
        "FROM game_cards gc "
        "WHERE gc.session_id=? AND gc.card_uid=?"
    )
    try:
        return connection.execute(sql, (session_id, int(card_uid))).fetchone()
    except sqlite3.OperationalError as exc:
        # Keep older focused fixtures usable when the optional socket columns
        # have not been migrated yet. The normal production schema has both.
        if "no such column" not in str(exc).lower():
            raise
        return connection.execute(
            sql.replace(", gc.card_template_id, gc.gems ", " "),
            (session_id, int(card_uid))).fetchone()


def db_card_instance_dynamic_projections(session_id, card_uids, conn=None):
    """Return dynamic card projections for many UIDs using one ``IN`` read.

    The mapping values have the same field order as
    :func:`db_card_instance_dynamic_projection`, so callers can feed a
    prefetched row directly into the card encoder.  This is intentionally a
    read-only batch helper: location, buffs, and abilities remain authoritative
    mutable SQLite state and are never put in a process-wide cache.
    """
    ids = []
    seen = set()
    for raw_uid in card_uids or ():
        try:
            uid = int(raw_uid)
        except (TypeError, ValueError):
            continue
        if uid not in seen:
            seen.add(uid)
            ids.append(uid)
    if not ids:
        return {}
    connection = conn or _pvp_db._db_layer._db
    result = {}
    # SQLite's default host parameter limit is commonly 999. Keep the helper
    # correct for a large revealed/deck batch as well as the normal small HUD
    # batches.
    for start in range(0, len(ids), 900):
        chunk = ids[start:start + 900]
        marks = ",".join("?" for _ in chunk)
        sql = (
            "SELECT gc.card_uid, gc.card_abilities, gc.card_attack_mod, "
            "gc.card_defense_mod, gc.card_damage, gc.original_template_guid, "
            "gc.permanent_buffs, gc.temporary_buffs, gc.card_cost_mod, "
            "gc.cost_mod_json, gc.card_attributes, gc.temporary_attributes, "
            "gc.user_id, gc.location, gc.template_guid, "
            "gc.card_template_id, gc.gems FROM game_cards gc "
            "WHERE gc.session_id=? AND gc.card_uid IN (" + marks + ")")
        try:
            rows = connection.execute(sql, [session_id, *chunk]).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such column" not in str(exc).lower():
                raise
            old_sql = sql.replace(", gc.card_template_id, gc.gems ", " ")
            rows = connection.execute(
                old_sql, [session_id, *chunk]).fetchall()
        for row in rows:
            result[int(row[0])] = tuple(row[1:])
    return result


def db_card_set_attacking_state(session_id, card_uid, state_bits, conn=None):
    """OR attacking/tapped state onto a warzone card."""
    connection = conn or _pvp_db._db_layer._db
    connection.execute(
        "UPDATE game_cards SET card_state = (card_state | ?) "
        "WHERE session_id=? AND card_uid=?",
        (int(state_bits), session_id, int(card_uid)),
    )
    if conn is None:
        connection.commit()


def db_card_set_warzone_arrival(session_id, card_uid, conn=None):
    """Move a resolved troop to warzone and apply its arrival state bits."""
    connection = conn or _pvp_db._db_layer._db
    connection.execute(
        "UPDATE game_cards SET location='warzone', "
        "card_state = (card_state & ~?) | ? "
        "WHERE session_id=? AND card_uid=?",
        (ECardStates.StartedATurnOnYourSide, ECardStates.CameOutThisTurn,
         session_id, int(card_uid)),
    )
    if conn is None:
        connection.commit()


def db_card_discard_spell(session_id, card_uid, conn=None):
    """Discard a resolved spell through the shared discard mutation."""
    return _pvp_db.db_discard_card(session_id, card_uid, conn=conn)


def db_card_attribute_rows(session_id, card_uids, conn=None):
    """Return effective stored/template attributes for selected session cards."""
    if not card_uids:
        return []
    connection = conn or _pvp_db._db_layer._db
    marks = ",".join("?" for _ in card_uids)
    return connection.execute(
        "SELECT ct.attributes | gc.card_attributes | "
        "COALESCE(gc.temporary_attributes, 0) "
        "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.card_uid IN (" + marks + ")",
        [session_id] + [int(uid) for uid in card_uids],
    ).fetchall()


def db_card_uids_in_zone(session_id, user_id, zone, conn=None):
    """Return card UIDs owned by a player in one game zone."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT card_uid FROM game_cards WHERE session_id=? AND user_id=? "
        "AND location=?", (session_id, user_id, zone)).fetchall()


def db_card_location(session_id, card_uid, conn=None):
    """Return the current zone for one session card, or ``None``."""
    connection = conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT location FROM game_cards WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid)),
    ).fetchone()
    return row[0] if row else None


def db_card_ability_payload(session_id, card_uid, conn=None):
    """Return the raw per-instance ability payload, retaining NULL semantics."""
    connection = conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT card_abilities FROM game_cards WHERE session_id=? "
        "AND card_uid=?", (session_id, int(card_uid))).fetchone()
    return row[0] if row else None


def db_card_ability_state(session_id, card_uid, conn=None):
    """Return a card's serialized abilities, authored abilities, and state."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT gc.card_abilities, ct.abilities_json, gc.card_state "
        "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.card_uid=?",
        (session_id, int(card_uid)),
    ).fetchone()


def db_card_damage_shield_fields(session_id, card_uid, conn=None):
    """Return the two serialized modifier stores used by damage shields."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT permanent_buffs, temporary_buffs FROM game_cards "
        "WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid)),
    ).fetchone()


def db_card_permanent_buffs(session_id, card_uid, conn=None):
    """Return one card's persistent modifier payload."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT permanent_buffs FROM game_cards WHERE session_id=? "
        "AND card_uid=?", (session_id, int(card_uid))).fetchone()
    return row[0] if row else None


def db_charge_ability_cost(ability_guid, conn=None):
    """Return the authored charge cost for a champion/talent ability."""
    connection = conn or _pvp_db._db_layer._db
    for table in ("champion_abilities", "talent_abilities"):
        row = connection.execute(
            "SELECT charge_cost FROM " + table + " WHERE ability_guid=? "
            "LIMIT 1", (str(ability_guid).lower(),)).fetchone()
        if row is not None:
            return row[0]
    return None


def db_card_zone_details(session_id, card_uid, conn=None):
    """Return template, instance template ID, owner, and zone for one card."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT template_guid, card_template_id, user_id, location "
        "FROM game_cards WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).fetchone()


def db_card_owner_location_position(session_id, card_uid, conn=None):
    """Return owner, zone, and position for one session card."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT user_id, location, position FROM game_cards "
        "WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).fetchone()


def db_card_collection_info(session_id, card_uid, conn=None):
    """Return collection owner and original/current template IDs."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT user_id, original_template_guid, template_guid "
        "FROM game_cards WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).fetchone()


def db_card_grant_info(session_id, card_uid, conn=None):
    """Return effective abilities and projection fields for a grant."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_abilities, template_guid, user_id, location, card_state "
        "FROM game_cards WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).fetchone()


def db_card_mutation_field(session_id, card_uid, field, conn=None):
    """Read one approved mutable JSON field from a session card."""
    if field not in {"permanent_buffs", "temporary_buffs"}:
        raise ValueError("unsupported card mutation field")
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT " + field + " FROM game_cards WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).fetchone()
    return row[0] if row else None


def db_card_template_threshold_subtype(session_id, card_uid, conn=None):
    """Return printed threshold JSON and subtype for one session card."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT ct.threshold_json, ct.subtype FROM game_cards gc "
        "JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.card_uid=?",
        (session_id, int(card_uid))).fetchone()


def db_card_original_and_template(session_id, card_uid, conn=None):
    """Return original and current template identities for a session card."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT original_template_guid, template_guid FROM game_cards "
        "WHERE session_id=? AND card_uid=?", (session_id, int(card_uid))
    ).fetchone()


def db_card_abilities_state(session_id, card_uid, conn=None):
    """Return mutable ability JSON and state for a session card."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_abilities, card_state FROM game_cards "
        "WHERE session_id=? AND card_uid=?", (session_id, int(card_uid))
    ).fetchone()


def db_card_type_effective_defense(session_id, card_uid, conn=None):
    """Return card type and effective defense for one session card."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT gc.card_type, COALESCE(ct.defense,0) "
        "+ COALESCE(gc.card_defense_mod,0) "
        "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.card_uid=?",
        (session_id, int(card_uid)),
    ).fetchone()


def db_cards_for_filter(session_id, owners, locations, conn=None):
    """Return card fields consumed by metadata card-filter evaluation."""
    if not owners or not locations:
        return []
    connection = conn or _pvp_db._db_layer._db
    owner_marks = ",".join("?" for _ in owners)
    location_marks = ",".join("?" for _ in locations)
    return connection.execute(
        "SELECT gc.card_uid, gc.card_type, gc.location, gc.user_id, "
        "gc.card_state, COALESCE(ct.attack,0), COALESCE(ct.defense,0), "
        "ct.name, COALESCE(ct.cost,0), ct.subtype "
        "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
        f"WHERE gc.session_id=? AND gc.user_id IN ({owner_marks}) "
        f"AND lower(gc.location) IN ({location_marks})",
        [session_id, *[int(owner) for owner in owners], *locations],
    ).fetchall()


def db_card_play_info(session_id, card_uid, conn=None):
    """Return template/card-type/resource fields for a played card."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT gc.template_guid, ct.card_type, ct.name, "
        "ct.current_resources_granted, ct.max_resources_granted, "
        "ct.abilities_json FROM game_cards gc "
        "JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.card_uid=?",
        (session_id, int(card_uid))).fetchone()


def db_card_chain_info(session_id, card_uid, conn=None):
    """Return template GUID, type, and cost for a chain card."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT gc.template_guid, ct.card_type, ct.cost "
        "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.card_uid=?",
        (session_id, int(card_uid))).fetchone()


def db_cards_in_zones_with_abilities(session_id, owner_id, zones, conn=None):
    """Return card UIDs and ability payloads for trigger-holder scanning."""
    if not zones:
        return []
    marks = ",".join("?" for _ in zones)
    cursor = (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid, card_abilities FROM game_cards "
        "WHERE session_id=? AND user_id=? AND location IN (" + marks + ") "
        "AND card_abilities IS NOT NULL AND card_abilities != ''",
        [session_id, int(owner_id), *zones])
    return cursor.fetchall() if hasattr(cursor, "fetchall") else []


def db_card_template_abilities_by_name_prefix(prefix, conn=None):
    """Return (guid, abilities_json) for templates whose name starts with prefix."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT guid, abilities_json FROM card_templates "
        "WHERE name LIKE ? ORDER BY name",
        (str(prefix) + "%",)).fetchall()


def db_card_positions(session_id, card_uids, conn=None):
    """Return selected session-card positions in authoritative order."""
    if not card_uids:
        return []
    connection = conn or _pvp_db._db_layer._db
    marks = ",".join("?" for _ in card_uids)
    return connection.execute(
        "SELECT card_uid, position FROM game_cards "
        "WHERE session_id=? AND card_uid IN (" + marks + ") "
        "ORDER BY position", [session_id] + [int(uid) for uid in card_uids]
    ).fetchall()


def db_card_template_lethal(template_guid, conn=None):
    """Return the optional printed Lethal flag for a template."""
    connection = conn or _pvp_db._db_layer._db
    try:
        row = _cached_static_row(
            connection, ("card_lethal", str(template_guid).lower()),
            "SELECT lethal FROM card_templates WHERE guid=?",
            (template_guid,))
    except Exception:
        return 0
    return int(row[0] or 0) if row else 0


def db_card_catalog(conn=None):
    """Return the card catalog fields used by pack/profile projections."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT guid, set_guid, name, rarity, cost, attack, defense, "
        "is_pve, no_pvp, card_type FROM card_templates"
    ).fetchall()


def db_card_static_fields(session_id, card_uid, conn=None):
    """Return every column the static cost and threshold views read, in one row.

    ``template_guid`` is NULL when the card's template is missing, so callers
    can tell "no card" from "card with no template" the way the separate
    inner-join helpers did.  Keep the columns in one query: a cost or
    threshold projection scans many candidate cards, and one round trip per
    helper dominated that scan.
    """
    cursor = (conn or _pvp_db._db_layer._db).execute(
        "SELECT gc.user_id AS user_id, gc.permanent_buffs AS permanent_buffs, "
        "gc.temporary_buffs AS temporary_buffs, "
        "gc.card_cost_mod AS card_cost_mod, gc.cost_mod_json AS cost_mod_json, "
        "gc.location AS location, ct.guid AS template_guid, ct.cost AS cost, "
        "ct.threshold_json AS threshold_json, ct.subtype AS subtype, "
        "COALESCE(gc.card_type, ct.card_type) AS card_type "
        "FROM game_cards gc LEFT JOIN card_templates ct "
        "ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.card_uid=?",
        (session_id, int(card_uid)))
    row = cursor.fetchone()
    if row is None:
        return None
    # Named access on any connection (plain tuples have no column names).
    return dict(zip([column[0] for column in cursor.description], row))


def db_card_static_row(session_id, card_uid, conn=None):
    """Return full card/template combat state for static evaluation."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT gc.card_attack_mod, gc.card_defense_mod, gc.card_damage, "
        "gc.card_attributes, ct.attack, ct.defense, ct.attributes, "
        "gc.permanent_buffs, gc.temporary_buffs, gc.temporary_attributes, "
        "ct.rage_value, ct.lethal FROM game_cards gc "
        "JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.card_uid=?",
        (session_id, int(card_uid))).fetchone()


def db_card_combat_state(session_id, card_uid, conn=None):
    """Return combat stats and mutable payloads without optional columns."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT gc.card_attack_mod, gc.card_defense_mod, gc.card_damage, "
        "gc.card_attributes, ct.attack, ct.defense, ct.attributes, "
        "gc.permanent_buffs, gc.temporary_buffs, gc.temporary_attributes "
        "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.card_uid=?",
        (session_id, int(card_uid))).fetchone()


def db_card_attribute_value(session_id, card_uid, field, conn=None):
    """Read one approved integer card-attribute field."""
    if field not in {"card_attributes", "temporary_attributes"}:
        raise ValueError("unsupported card attribute field")
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT " + field + " FROM game_cards WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).fetchone()
    return row[0] if row else 0


def db_card_list_stat_row(session_id, card_uid, conn=None):
    """Return the card/template fields used by list-property variables."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT gc.card_uid, gc.card_type, gc.location, gc.user_id, "
        "gc.card_state, COALESCE(ct.attack,0), COALESCE(ct.defense,0), "
        "ct.name, COALESCE(ct.cost,0), ct.subtype, ct.threshold_json, "
        "gc.card_attributes, ct.attributes, gc.card_attack_mod, "
        "gc.card_defense_mod, gc.permanent_buffs, gc.temporary_buffs "
        "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.card_uid=?",
        (session_id, int(card_uid))).fetchone()


def db_card_gem_ability_guids(session_id, card_uid, conn=None):
    """Return the ability GUIDs granted by a card's socketed gems.

    Session gems are stored either as one legacy gem type or as the packed
    positional bitfield the client uses (ten bits per socket, bit 62 marks the
    packed form).  Both shapes decode against ``gem_templates``.
    """
    row = _pvp_db.db_card_gem_type(session_id, int(card_uid), conn=conn)
    try:
        raw = int(row or 0)
    except (TypeError, ValueError):
        return []
    if not raw:
        return []
    return db_gem_ability_guids(raw, conn=conn)


def db_card_template_creation_profile(template_guid, conn=None):
    """Return (cost, subtype, card_type) for a would-be created template."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT cost, subtype, card_type FROM card_templates WHERE guid=?",
        (str(template_guid).lower(),)).fetchone()
    if not row:
        return None
    return (int(row[0] or 0), str(row[1] or ""), str(row[2] or ""))


def db_card_manual_ability_guids(session_id, card_uid, conn=None):
    """Return the card's current abilities whose metadata marks them manual."""
    connection = conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT card_abilities FROM game_cards WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).fetchone()
    if not row:
        return []
    try:
        abilities = json.loads(row[0] or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    out = []
    for ability in abilities:
        guid = str(ability or "").lower()
        if not guid:
            continue
        manual = connection.execute(
            "SELECT is_manual FROM card_abilities_meta WHERE ability_guid=?",
            (guid,)).fetchone()
        if manual and int(manual[0] or 0):
            out.append(guid)
    return out


def db_card_mutation_info(session_id, card_uid, conn=None):
    """Return template, owner, and type for a mutable session card."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT template_guid, user_id, card_type FROM game_cards "
        "WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).fetchone()


def db_card_mutation_snapshot(session_id, card_uid, conn=None):
    """Return template, owner, zone, and state for card mutation paths."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT template_guid, user_id, location, card_state "
        "FROM game_cards WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).fetchone()


def db_card_modifier_state(session_id, card_uid, conn=None):
    """Return card identity, zone, state, and permanent buff payload."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT template_guid, user_id, location, card_state, permanent_buffs "
        "FROM game_cards WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).fetchone()


def db_card_template_ability_payload(template_guid, conn=None):
    """Return the serialized ability list for a card template."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT abilities_json FROM card_templates WHERE guid=?",
        (template_guid,)).fetchone()
    return row[0] if row else None


def db_choice_card_rows(session_id, conn=None):
    """Return temporary choice cards awaiting selection."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid, user_id, template_guid, card_type FROM game_cards "
        "WHERE session_id=? AND location='choosing'", (session_id,)).fetchall()


def db_card_owner_id(session_id, card_uid, conn=None):
    """Return the persisted controller of a session card."""
    connection = conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT user_id FROM game_cards WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).fetchone()
    return row[0] if row else None


def db_card_original_owner_id(session_id, card_uid, conn=None):
    """Return original owner, falling back to current controller."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT COALESCE(owner_user_id, user_id) FROM game_cards "
        "WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).fetchone()
    return row[0] if row else None


def db_card_state_value(session_id, card_uid, conn=None):
    """Return the persisted state bitmask for one session card."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_state FROM game_cards WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).fetchone()
    return row[0] if row else None


def db_card_death_info(session_id, card_uid, conn=None):
    """Return template, controller, and effective authored attributes."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT gc.template_guid, gc.user_id, "
        "(ct.attributes | gc.card_attributes) FROM game_cards gc "
        "JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.card_uid=?",
        (session_id, int(card_uid))).fetchone()


def db_capture_original_gems(session_id, card_uid, conn=None):
    """Save a card's pre-transform active gems once, as C# does in Card."""
    connection = conn or _pvp_db._db_layer._db
    cursor = connection.execute(
        "UPDATE game_cards SET original_gems=gems "
        "WHERE session_id=? AND card_uid=? AND original_gems IS NULL "
        "AND (original_template_guid IS NULL OR original_template_guid='' "
        "OR original_template_guid=template_guid)",
        (session_id, int(card_uid)))
    if conn is None:
        connection.commit()
    return cursor.rowcount


def db_card_original_gems(session_id, card_uid, conn=None):
    """Return the active gems recorded before the card's first transform."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT original_gems FROM game_cards "
        "WHERE session_id=? AND card_uid=?", (session_id, int(card_uid))
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def db_card_source_info(session_id, card_uid, conn=None):
    """Return template, type, zone, and owner for a public source card."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT template_guid, card_type, location, user_id FROM game_cards "
        "WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).fetchone()


def db_card_command_info(session_id, user_id, card_uid, conn=None):
    """Return instance/template IDs and zone for a commanded card."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_template_id, location, template_guid FROM game_cards "
        "WHERE session_id=? AND user_id=? AND card_uid=?",
        (session_id, int(user_id), int(card_uid))).fetchone()


def db_card_cost(session_id, card_uid, conn=None):
    """Return the printed cost for a session card."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT ct.cost FROM game_cards gc JOIN card_templates ct "
        "ON ct.guid=gc.template_guid WHERE gc.session_id=? AND gc.card_uid=?",
        (session_id, int(card_uid))).fetchone()
    return row[0] if row else None


def db_card_state_rows(session_id, card_uids, conn=None):
    """Return ``(card_uid, card_state)`` for selected session cards."""
    if not card_uids:
        return []
    marks = ",".join("?" for _ in card_uids)
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid, card_state FROM game_cards WHERE session_id=? "
        "AND card_uid IN (" + marks + ")",
        [session_id] + [int(uid) for uid in card_uids]).fetchall()


def db_card_zone_projection(session_id, card_uid, conn=None):
    """Return template/type/state for a client zone update."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT template_guid, card_type, card_state FROM game_cards "
        "WHERE session_id=? AND card_uid=?", (session_id, int(card_uid))
    ).fetchone()


def db_card_gem_type(session_id, card_uid, conn=None):
    """Return the socketed gem type persisted on a session card."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT gems FROM game_cards WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).fetchone()
    return row[0] if row else None


def db_card_owner_zone_state(session_id, card_uid, conn=None):
    """Return owner, zone, and state for a selected cost card."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT user_id, location, card_state FROM game_cards "
        "WHERE session_id=? AND card_uid=?", (session_id, int(card_uid))
    ).fetchone()


def db_card_sacrifice_info(session_id, card_uid, conn=None):
    """Return owner, zone, and type for a sacrifice validation."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT user_id, location, card_type FROM game_cards "
        "WHERE session_id=? AND card_uid=?", (session_id, int(card_uid))
    ).fetchone()


def db_card_combat_identity(session_id, card_uid, conn=None):
    """Return owner, zone, template, and mutable combat stats for a card."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT gc.user_id, gc.location, gc.template_guid, "
        "COALESCE(ct.attack,0)+COALESCE(gc.card_attack_mod,0), "
        "COALESCE(ct.defense,0)+COALESCE(gc.card_defense_mod,0) "
        "FROM game_cards gc LEFT JOIN card_templates ct "
        "ON ct.guid=gc.template_guid WHERE gc.session_id=? AND gc.card_uid=?",
        (session_id, int(card_uid))).fetchone()


def db_card_effective_defense(session_id, card_uid, conn=None):
    """Return printed plus mutable defense for one session card."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT ct.defense, gc.card_defense_mod FROM game_cards gc "
        "JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.card_uid=?",
        (session_id, int(card_uid))).fetchone()


def db_card_template_attrs_joined(session_id, card_uid, conn=None):
    """Return template and instance attributes for one game card."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT gc.template_guid, ct.attributes, gc.card_attributes "
        "FROM game_cards gc LEFT JOIN card_templates ct "
        "ON ct.guid=gc.template_guid WHERE gc.session_id=? AND gc.card_uid=?",
        (session_id, int(card_uid))).fetchone()
