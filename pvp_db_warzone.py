import json
from contextlib import contextmanager
from domain.constants import PLAYED_CARD_POSITION
import pvp_db as _pvp_db
from pvp_db_ability import (
    _cached_static_row,
)
from pvp_db_card import (
    db_card_uses,
    flush_pending_session_saves,
)
from pvp_db_game import (
    _target_projection_columns,
)
from pvp_db_move import (
    _session_row_params,
)


@contextmanager
def session_save_batch():
    """Defer this thread's game-session saves until the block exits.

    Each transaction would otherwise write the session row after every phase
    step.  Queued rows are written, in one commit, when the block exits, and
    before any read of ``game_sessions`` on this thread, so readers still see
    their own writes.  Nested batches join the outermost one.
    """
    if getattr(_pvp_db._session_save_queue, "pending", None) is not None:
        yield
        return
    _pvp_db._session_save_queue.pending = {}
    try:
        yield
    finally:
        try:
            flush_pending_session_saves(commit=True)
        finally:
            _pvp_db._session_save_queue.pending = None


def queue_session_save(session):
    """Queue ``session``'s row if a batch is open on this thread."""
    pending = getattr(_pvp_db._session_save_queue, "pending", None)
    if pending is None:
        return False
    params = _session_row_params(session)
    pending[params[0]] = params
    return True


def db_warzone_troop_count(session_id, user_id, conn=None):
    """Count a player's warzone troops, with zero representing the AI."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT COUNT(*) FROM game_cards WHERE session_id=? AND user_id=? "
        "AND location='warzone' AND card_type LIKE '%Troop%'",
        (session_id, user_id)).fetchone()
    return int(row[0]) if row else 0


def db_set_card_state_or(session_id, card_uid, state_bits, conn=None):
    """OR state bits onto a card without committing explicit transactions."""
    connection = conn or _pvp_db._db_layer._db
    connection.execute(
        "UPDATE game_cards SET card_state = (card_state | ?) "
        "WHERE session_id=? AND card_uid=?",
        (int(state_bits), session_id, int(card_uid)))
    if conn is None:
        connection.commit()


def db_set_card_location(session_id, card_uid, location, extra_set=None,
                         extra_params=None, conn=None):
    """Move a card to a zone and leave explicit transactions uncommitted."""
    connection = conn or _pvp_db._db_layer._db
    sql = "UPDATE game_cards SET location=?"
    params = [location]
    if extra_set:
        sql += ", " + extra_set
        params.extend(extra_params or [])
    params.extend([session_id, int(card_uid)])
    connection.execute(sql + " WHERE session_id=? AND card_uid=?", params)
    if conn is None:
        connection.commit()


def db_warzone_troops_basic(session_id, user_id=None, conn=None):
    """Return ``(card_uid, card_type, card_state)`` for warzone troops."""
    connection = conn or _pvp_db._db_layer._db
    sql = ("SELECT card_uid, card_type, card_state FROM game_cards "
           "WHERE session_id=? AND location='warzone' "
           "AND card_type LIKE '%Troop%'")
    params = [session_id]
    if user_id is not None:
        # This query has no ``gc`` alias; using one here makes every caller
        # that asks for one player's attack options fail at DeclareAttack.
        sql += " AND user_id=?"
        params.append(user_id)
    return connection.execute(sql, params).fetchall()


def db_warzone_troops_with_state(session_id, user_id=None, conn=None):
    """Return ``(uid, template_guid, state, user_id)`` warzone troops."""
    connection = conn or _pvp_db._db_layer._db
    sql = ("SELECT card_uid, template_guid, card_state, user_id "
           "FROM game_cards WHERE session_id=? AND location='warzone' "
           "AND card_type LIKE '%Troop%'")
    params = [session_id]
    if user_id is not None:
        sql += " AND user_id=?"
        params.append(user_id)
    return connection.execute(sql, params).fetchall()


def db_set_card_cooldown(session_id, card_uid, ability_guid, turns,
                         conn=None):
    """Set one authored ability cooldown without disturbing use counters."""
    connection = conn or _pvp_db._db_layer._db
    with _pvp_db._CARD_COOLDOWN_LOCK:
        uses = db_card_uses(session_id, card_uid, conn=connection)
        counts = uses.get(_pvp_db._CARD_COOLDOWNS_KEY)
        counts = dict(counts) if isinstance(counts, dict) else {}
        key = str(ability_guid or "").lower()
        if not key:
            return 0
        remaining = max(0, int(turns or 0))
        if remaining:
            counts[key] = remaining
        else:
            counts.pop(key, None)
        if counts:
            uses[_pvp_db._CARD_COOLDOWNS_KEY] = counts
        else:
            uses.pop(_pvp_db._CARD_COOLDOWNS_KEY, None)
        connection.execute(
            "UPDATE game_cards SET card_uses=? WHERE session_id=? AND card_uid=?",
            (json.dumps(uses, separators=(",", ":")),
             session_id, int(card_uid)))
        if conn is None:
            connection.commit()
        return remaining


def db_set_card_resolved_at(session_id, card_uid, resolved_at, conn=None):
    """Stamp the authoritative resolve-order counter on a game card."""
    connection = conn or _pvp_db._db_layer._db
    connection.execute(
        "UPDATE game_cards SET resolved_at=? WHERE session_id=? AND card_uid=?",
        (int(resolved_at), session_id, int(card_uid)))
    if conn is None:
        connection.commit()


def db_warzone_troop_attributes(session_id, user_id, conn=None):
    """Return the instance/template attributes used by attack eligibility."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT gc.card_uid, gc.card_state, gc.card_attributes, "
        "gc.temporary_attributes, ct.attributes, gc.card_abilities "
        "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.user_id=? AND gc.location='warzone' "
        "AND gc.card_type LIKE '%Troop%'",
        (session_id, user_id),
    ).fetchall()


def db_warzone_attack_candidates(session_id, user_id, conn=None):
    """Return the joined fields needed by the AI attack evaluator."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT gc.card_uid, gc.template_guid, ct.attributes, "
        "gc.card_attributes, gc.card_state, ct.attack "
        "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.user_id=? AND gc.location='warzone' "
        "AND gc.card_type LIKE '%Troop%'",
        (session_id, user_id),
    ).fetchall()


def db_warzone_attack_option_rows(session_id, user_id, conn=None):
    """Return the joined fields used to publish PvP attack options."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT gc.card_uid, gc.card_state, gc.card_type, "
        "(ct.attributes | gc.card_attributes | "
        "COALESCE(gc.temporary_attributes, 0)) "
        "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.user_id=? AND gc.location='warzone' "
        "AND gc.card_type LIKE '%Troop%'",
        (session_id, user_id),
    ).fetchall()


def db_warzone_blocker_uids(session_id, user_id, tapped_mask, conn=None):
    """Return untapped defender troop UIDs for a PvP block window."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT gc.card_uid FROM game_cards gc "
        "JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.user_id=? AND gc.location='warzone' "
        "AND gc.card_type LIKE '%Troop%' AND (gc.card_state & ?) = 0",
        (session_id, user_id, tapped_mask),
    ).fetchall()


def db_template_in_zones(session_id, template_guid, zones, conn=None):
    """Whether a template is present in any requested session zone."""
    if not zones:
        return False
    marks = ",".join("?" for _ in zones)
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT 1 FROM game_cards WHERE session_id=? AND template_guid=? "
        "AND location IN (" + marks + ") LIMIT 1",
        [session_id, template_guid] + list(zones),
    ).fetchone()
    return bool(row)


def db_template_ability_payload(template_guid, conn=None):
    """Return a template's raw authored ability payload."""
    connection = conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT abilities_json FROM card_templates WHERE guid=?",
        (template_guid,)).fetchone()
    return row[0] if row else None


def db_template_ability_data(template_guid, conn=None):
    """Return authored abilities and variable-cost flags for a template."""
    connection = conn or _pvp_db._db_layer._db
    columns = {row[1] for row in connection.execute(
        "PRAGMA table_info(card_templates)")}
    variable_cost_double = (
        "variable_cost_double" if "variable_cost_double" in columns else "0")
    return connection.execute(
        "SELECT abilities_json, variable_cost, " + variable_cost_double + " "
        "FROM card_templates WHERE guid=?",
        (template_guid,)).fetchone()


def db_set_card_mutation_field(session_id, card_uid, field, value, conn=None):
    """Persist one approved mutable JSON field without forcing a commit."""
    if field not in {"permanent_buffs", "temporary_buffs"}:
        raise ValueError("unsupported card mutation field")
    return (conn or _pvp_db._db_layer._db).execute(
        "UPDATE game_cards SET " + field + "=? WHERE session_id=? AND card_uid=?",
        (value, session_id, int(card_uid))).rowcount


def db_warzone_display_rows(session_id, conn=None):
    """Return all warzone fields needed for a PvP CardUpdated projection."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT card_uid, template_guid, user_id, card_state, card_type "
        "FROM game_cards WHERE session_id=? AND location='warzone'",
        (session_id,)).fetchall()


def db_template_name(template_guid, conn=None):
    """Return a card template's display name, or None."""
    connection = conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT name FROM card_templates WHERE guid=?", (template_guid,)
    ).fetchone()
    return row[0] if row else None


def db_zone_display_rows(session_id, user_id, conn=None):
    """Return owned cards ordered for the debug zone listing."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT gc.card_uid, gc.location, ct.name, ct.guid "
        "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.user_id=? "
        "ORDER BY gc.location, gc.position",
        (session_id, int(user_id))).fetchall()


def db_template_card_info(template_guid, conn=None):
    """Return display/type/stat fields used to materialize a card definition."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT card_type, name, cost, attack, defense FROM card_templates "
        "WHERE guid=?", (template_guid,)).fetchone()


def db_template_by_guid(template_guid, conn=None):
    """Return the legacy six-field card-template projection."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT guid, card_type, name, cost, attack, defense "
        "FROM card_templates WHERE guid=?", (template_guid,)).fetchone()
    if not row:
        return None
    return (row[0], row[1], row[2], row[3] or 0, row[4] or 0, row[5] or 0)


def db_set_resource_guids(set_guid, conn=None):
    """Return resource template GUIDs from a set for a PvP fixture."""
    return [row[0] for row in (conn or _pvp_db._db_layer._db).execute(
        "SELECT DISTINCT guid FROM card_templates WHERE set_guid=? "
        "AND card_type='Resource'", (set_guid,)).fetchall()]


def db_set_constructed_guids(set_guid, conn=None):
    """Return collectible non-resource PvP template GUIDs from a set."""
    return [row[0] for row in (conn or _pvp_db._db_layer._db).execute(
        "SELECT guid FROM card_templates WHERE set_guid=? "
        "AND card_type!='Resource' AND is_pve=0 AND no_pvp=0",
        (set_guid,)).fetchall()]


def db_template_exists(template_guid, conn=None):
    """Whether a card template exists in the static card catalog."""
    connection = conn or _pvp_db._db_layer._db
    return bool(connection.execute(
        "SELECT 1 FROM card_templates WHERE guid=?", (template_guid,)
    ).fetchone())


def db_scene_mods(scene_guid, conn=None):
    """Return the serialized encounter-scene modifier payload (campaign_content).

    ``conn`` is unused and kept so callers do not change.
    """
    import campaign_source
    campaign_source.ensure_campaign_path()
    import campaign_content
    row = campaign_content.scene(scene_guid)
    return row.get("mods_json") if row else None


def db_template_ability_guids(template_guid, conn=None):
    """Return the authored ability GUID list for one card template."""
    connection = conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT abilities_json FROM card_templates WHERE guid=?",
        (template_guid,)).fetchone()
    return row[0] if row else None


def db_target_template_filter(template_id, conn=None):
    """Return one target template's serialized filter."""
    connection = conn or _pvp_db._db_layer._db
    row = _cached_static_row(
        connection, ("target_filter", str(template_id)),
        "SELECT filter_json FROM target_templates WHERE template_id=?",
        (template_id,))
    return row[0] if row else None


def db_template_subtype(template_guid, conn=None):
    """Return a card template's authored subtype."""
    connection = conn or _pvp_db._db_layer._db
    row = _cached_static_row(
        connection, ("card_subtype", str(template_guid).lower()),
        "SELECT subtype FROM card_templates WHERE guid=?", (template_guid,))
    return row[0] if row else None


def db_session_user_ids(session_id, conn=None):
    """Return distinct controllers represented by session cards."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT DISTINCT user_id FROM game_cards WHERE session_id=?",
        (session_id,)).fetchall()


def db_static_card_rows(session_id, zones, conn=None):
    """Return card fields needed by static filter evaluation."""
    if not zones:
        return []
    marks = ",".join("?" for _ in zones)
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT gc.card_uid, gc.card_type, gc.location, gc.user_id, "
        "gc.card_state, COALESCE(ct.attack,0), COALESCE(ct.defense,0), "
        "ct.name, COALESCE(ct.cost,0), ct.subtype, ct.threshold_json, "
        "gc.card_attributes, ct.attributes FROM game_cards gc "
        "JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.location IN (" + marks + ")",
        [session_id, *zones]).fetchall()


def db_warzone_card_uids(session_id, owner_id=None, conn=None):
    """Return warzone card UIDs, optionally restricted to one controller."""
    connection = conn or _pvp_db._db_layer._db
    if owner_id is None:
        return connection.execute(
            "SELECT card_uid FROM game_cards WHERE session_id=? "
            "AND location='warzone'", (session_id,)).fetchall()
    return connection.execute(
        "SELECT card_uid FROM game_cards WHERE session_id=? AND user_id=? "
        "AND location='warzone'", (session_id, int(owner_id))).fetchall()


def db_warzone_owner_ids(session_id, conn=None):
    """Return controllers with cards in the warzone."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT DISTINCT user_id FROM game_cards "
        "WHERE session_id=? AND location='warzone'", (session_id,)).fetchall()


def db_temporary_attribute_rows(session_id, conn=None):
    """Return cards carrying temporary attributes or temporary buffs."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid, user_id, temporary_attributes, temporary_buffs "
        "FROM game_cards WHERE session_id=? AND "
        "(temporary_attributes != 0 OR "
        "(temporary_buffs IS NOT NULL AND temporary_buffs != '{}'))",
        (session_id,)).fetchall()


def db_set_temporary_card_state(session_id, card_uid, attributes, buffs,
                                conn=None):
    """Persist temporary attributes and their expiration metadata."""
    return (conn or _pvp_db._db_layer._db).execute(
        "UPDATE game_cards SET temporary_attributes=?, temporary_buffs=? "
        "WHERE session_id=? AND card_uid=?",
        (int(attributes), buffs, session_id, int(card_uid)))


def db_set_card_attribute_value(session_id, card_uid, field, value, conn=None):
    """Persist one approved integer card-attribute field."""
    if field not in {"card_attributes", "temporary_attributes"}:
        raise ValueError("unsupported card attribute field")
    return (conn or _pvp_db._db_layer._db).execute(
        "UPDATE game_cards SET " + field + "=? WHERE session_id=? AND card_uid=?",
        (int(value), session_id, int(card_uid)))


def db_static_target_template(template_id, conn=None):
    """Return target fields used by continuous static evaluation."""
    connection = conn or _pvp_db._db_layer._db
    return _cached_static_row(
        connection, ("static_target", str(template_id)),
        "SELECT collection_flags, player_filter, filter_json, game_text, "
        "target_kind "
        "FROM target_templates WHERE template_id=?", (template_id,))


def db_set_card_abilities(session_id, card_uid, abilities_json, conn=None):
    """Persist the effective ability list for one materialized card."""
    connection = conn or _pvp_db._db_layer._db
    connection.execute(
        "UPDATE game_cards SET card_abilities=? "
        "WHERE session_id=? AND card_uid=?",
        (abilities_json, session_id, int(card_uid)),
    )


def db_set_card_abilities_and_attributes(session_id, card_uid, abilities_json,
                                         attributes, conn=None):
    """Persist effective abilities and attributes for one session card."""
    connection = conn or _pvp_db._db_layer._db
    connection.execute(
        "UPDATE game_cards SET card_abilities=?, card_attributes=? "
        "WHERE session_id=? AND card_uid=?",
        (abilities_json, int(attributes), session_id, int(card_uid)),
    )


def db_session_turn_order(session_id, conn=None):
    """Return the decoded session battle state, if one was persisted."""
    flush_pending_session_saves()
    connection = conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT turn_order_json FROM game_sessions WHERE session_id=?",
        (str(session_id),)).fetchone()
    return row[0] if row else None
    if conn is None:
        connection.commit()


def db_target_template_info(template_id, conn=None):
    """Return filter/kind/auto fields for one target template."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT filter_json, target_kind, is_auto_target "
        "FROM target_templates WHERE template_id=?", (template_id,)
    ).fetchone()


def db_target_template_row(template_id, conn=None):
    """Return the complete target-template projection for resolution."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT template_id, game_text, is_auto_target, is_random_target, "
        "optional, explicit, player_filter, collection_flags, "
        "min_target_count, max_target_count, filter_json, target_kind "
        "FROM target_templates WHERE template_id=?", (template_id,)
    ).fetchone()


def db_session_card_owners(session_id, conn=None):
    """Return card UID/controller pairs for a session."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid, user_id FROM game_cards WHERE session_id=?",
        (session_id,)).fetchall()


def db_target_source_row(session_id, card_uid, conn=None):
    """Return the optional-column-safe source projection for targeting."""
    connection = conn or _pvp_db._db_layer._db
    rarity, sockets, gems, original, abilities = _target_projection_columns(connection)
    sql = ("SELECT gc.card_uid, gc.card_type, gc.location, gc.user_id, "
           + "gc.card_state, COALESCE(ct.attack,0), COALESCE(ct.defense,0), "
           + "gc.template_guid, ct.name, COALESCE(ct.cost,0), ct.subtype, "
           + "ct.threshold_json, COALESCE(gc.card_attributes,0) | COALESCE(ct.attributes,0), " + rarity + ", "
           + sockets + ", " + gems + ", " + original + ", " + abilities + ", "
           + "gc.permanent_buffs FROM game_cards gc LEFT JOIN card_templates ct "
           + "ON ct.guid=gc.template_guid WHERE gc.session_id=? AND gc.card_uid=?")
    return connection.execute(
        sql,
        (session_id, int(card_uid))).fetchone()


def db_target_candidate_rows(session_id, zones, controller_uid=None,
                             both_players=False, top_n=False, conn=None):
    """Return optional-column-safe card candidates for target evaluation."""
    if not zones:
        return []
    connection = conn or _pvp_db._db_layer._db
    rarity, sockets, gems, original, _abilities = _target_projection_columns(connection)
    marks = ",".join("?" for _ in zones)
    sql = (
        "SELECT gc.card_uid, gc.card_type, gc.location, gc.user_id, "
        "gc.template_guid, gc.card_state, COALESCE(ct.attack,0), "
        "COALESCE(ct.defense,0), ct.name, COALESCE(ct.cost,0), ct.subtype, "
        "ct.threshold_json, gc.card_abilities, gc.permanent_buffs, "
        + rarity + ", " + sockets + ", " + gems + ", " + original + ", "
        + "COALESCE(gc.card_attributes,0) | COALESCE(ct.attributes,0) "
        + "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
        + "WHERE gc.session_id=? AND gc.location IN (" + marks + ")"
    )
    params = [session_id] + list(zones)
    if not both_players:
        sql += " AND gc.user_id=?"
        params.append(controller_uid)
    sql += " ORDER BY gc.user_id, gc.position" if top_n else " ORDER BY gc.position"
    return connection.execute(sql, params).fetchall()


def db_trace_card_rows(session_id, columns, conn=None):
    """Return trace columns, substituting NULL for legacy missing fields."""
    connection = conn or _pvp_db._db_layer._db
    valid = {row[1] for row in connection.execute(
        "PRAGMA table_info(game_cards)").fetchall()}
    selected = [column if column in valid else "NULL AS " + column
                for column in columns]
    return connection.execute(
        "SELECT card_uid, " + ", ".join(selected) +
        " FROM game_cards WHERE session_id=?", (session_id,)).fetchall()


def db_target_template_targeting_info(template_id, conn=None):
    """Return display/filter fields used by legacy target classification."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT game_text, player_filter, filter_json FROM target_templates "
        "WHERE template_id=?", (template_id,)).fetchone()


def db_target_template_resolution_info(template_id, conn=None):
    """Return fields used by BOM target selection and randomization."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT filter_json, target_kind, is_random_target "
        "FROM target_templates WHERE template_id=?", (template_id,)).fetchone()


def db_template_attributes(template_guid, conn=None):
    """Return a card template's static attribute bitmask."""
    connection = conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT attributes FROM card_templates WHERE guid=?",
        (template_guid,)).fetchone()
    return row[0] if row else None


def db_transform_target_info(session_id, card_uid, conn=None):
    """Return the complete card/template projection used by random transforms."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT gc.template_guid, gc.card_type, gc.location, gc.user_id, "
        "gc.card_state, gc.permanent_buffs, ct.name, ct.cost, ct.rarity, "
        "ct.threshold_json, ct.subtype, ct.attributes, gc.card_attributes "
        "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.card_uid=?",
        (session_id, int(card_uid))).fetchone()


def db_transform_candidate_templates(conn=None):
    """Return eligible PVP transform candidate templates.

    Equipment-modified printings are not valid generated-card choices.  They
    are encounter/deck equipment variants, even when their base card type and
    PvP flags look collectible.

    Non-ownable cards are excluded as well.  ``CardTemplate.IsOwnable`` is
    ``CardRarity > ERarity.Land || IsBasicResource()``, so a Land-rarity card
    that is not a basic shard is a token/encounter card that only ever exists
    because another card creates it (Valor, Vine Goliath).  Offering those as
    random generated cards handed the client cards the collection never
    contains.
    """
    connection = conn or _pvp_db._db_layer._db
    columns = {row[1] for row in connection.execute(
        "PRAGMA table_info(card_templates)").fetchall()}
    rarity_expr = "rarity" if "rarity" in columns else "''"
    threshold_expr = ("threshold_json" if "threshold_json" in columns
                      else "'[]'")
    socket_expr = "socket_count" if "socket_count" in columns else "0"
    is_pve_expr = "is_pve" if "is_pve" in columns else "0"
    no_pvp_expr = "no_pvp" if "no_pvp" in columns else "0"
    equipment_expr = ("equipment_modified" if "equipment_modified" in columns
                      else "0")
    attack_expr = "attack" if "attack" in columns else "0"
    defense_expr = "defense" if "defense" in columns else "0"
    # Columns 9/10 (printed attack/defense) are appended so existing
    # index-based consumers keep their layout.
    return connection.execute(
        "SELECT guid, name, card_type, cost, " + rarity_expr + ", "
        + threshold_expr + ", subtype, attributes, " + socket_expr
        + ", " + attack_expr + ", " + defense_expr
        + " FROM card_templates WHERE COALESCE(" + is_pve_expr + ", 0)=0 "
        + "AND COALESCE(" + no_pvp_expr + ", 0)=0 "
        + "AND COALESCE(" + equipment_expr + ", 0)=0 "
        + "AND (COALESCE(" + rarity_expr + ", '')<>'Land' "
        + "OR (card_type LIKE '%Resource%' "
        + "AND COALESCE(subtype, '') LIKE '%Standard%')) "
        "AND card_type NOT LIKE '%Choice%'"
    ).fetchall()


def db_set_card_played_to_zone(session_id, card_uid, location, conn=None):
    """Move a played card to its resolved zone at the client sentinel slot."""
    connection = conn or _pvp_db._db_layer._db
    cursor = connection.execute(
        "UPDATE game_cards SET location=?, position=? "
        "WHERE session_id=? AND card_uid=?",
        (location, PLAYED_CARD_POSITION, session_id, int(card_uid)),
    )
    if conn is None:
        connection.commit()
    return cursor.rowcount


def db_template_catalog_for_filter(conn=None):
    """Return typed card-template fields used by random-card filters."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT guid, name, card_type, cost, attack, defense, attributes, "
        "subtype, rarity, socket_count, threshold_json, is_pve, no_pvp "
        "FROM card_templates").fetchall()


def db_tame_card(session_id, card_uid, permanent_buffs, card_state, conn=None):
    """Persist capture markers and move the captured card to the void."""
    return (conn or _pvp_db._db_layer._db).execute(
        "UPDATE game_cards SET permanent_buffs=?, location='void', "
        "position=0, card_state=? WHERE session_id=? AND card_uid=?",
        (permanent_buffs, int(card_state), session_id, int(card_uid)))


def db_warzone_cards_with_state(session_id, conn=None):
    """Return all warzone cards with template, owner, and state."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid, template_guid, user_id, card_state "
        "FROM game_cards WHERE session_id=? AND location='warzone'",
        (session_id,)).fetchall()


def db_warzone_troop_state_rows(session_id, conn=None):
    """Return mutable combat fields for warzone troops."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT gc.card_uid, gc.template_guid, ct.defense, "
        "gc.card_defense_mod, gc.card_damage, gc.permanent_buffs, "
        "gc.temporary_buffs FROM game_cards gc "
        "JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.location='warzone' "
        "AND gc.card_type LIKE '%Troop%'", (session_id,)).fetchall()


def db_warzone_card_heal_rows(session_id, conn=None):
    """Return owner, type and buffs for every warzone card (end-of-turn heal)."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid, user_id, template_guid, card_type, "
        "permanent_buffs, temporary_buffs FROM game_cards "
        "WHERE session_id=? AND location='warzone'", (session_id,)).fetchall()


def db_transform_card_instance(session_id, card_uid, template_guid, card_type,
                               abilities_json, attributes, location, position,
                               card_state, conn=None,
                               preserve_instance_state=False, gems=None):
    """Apply the canonical template projection for an existing card instance."""
    connection = conn or _pvp_db._db_layer._db
    if preserve_instance_state:
        return connection.execute(
            "UPDATE game_cards SET template_guid=?, card_template_id=?, "
            "card_type=?, card_abilities=?, card_attributes=?, location=?, "
            "position=?, card_state=?, gems=COALESCE(?,gems) "
            "WHERE session_id=? AND card_uid=?",
            (template_guid, template_guid, card_type, abilities_json,
             int(attributes or 0), location, int(position), int(card_state),
             gems, session_id, int(card_uid)))
    return (conn or _pvp_db._db_layer._db).execute(
        "UPDATE game_cards SET template_guid=?, card_template_id=?, "
        "card_type=?, card_abilities=?, card_attributes=?, "
        "temporary_attributes=0, temporary_buffs='{}', location=?, "
        "position=?, card_state=?, gems=COALESCE(?,gems) "
        "WHERE session_id=? AND card_uid=?",
        (template_guid, template_guid, card_type, abilities_json,
         int(attributes or 0), location, int(position), int(card_state),
         gems, session_id, int(card_uid)))


def db_warzone_card_state_attributes(session_id, card_uid, conn=None):
    """Return persisted state and temporary attributes for one warzone card."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_state, temporary_attributes FROM game_cards "
        "WHERE session_id=? AND card_uid=?", (session_id, int(card_uid))
    ).fetchone()


def db_template_projection(template_guid, conn=None):
    """Return template fields required for a CardUpdated projection."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_type, cost, attack, defense, threshold_json, abilities_json "
        "FROM card_templates WHERE guid=?", (template_guid,)).fetchone()


def db_warzone_ability_cards(session_id, include_non_troops=False, conn=None,
                            owner_id=0):
    """Return AI-eligible warzone cards and their authored ability payload."""
    sql = ("SELECT gc.card_uid, gc.template_guid, gc.card_state, ct.attributes, "
           "gc.card_attributes, gc.card_abilities FROM game_cards gc "
           "JOIN card_templates ct ON ct.guid=gc.template_guid "
           "WHERE gc.session_id=? AND gc.user_id=? AND gc.location='warzone'")
    if not include_non_troops:
        sql += " AND gc.card_type LIKE '%Troop%'"
    return (conn or _pvp_db._db_layer._db).execute(
        sql, (session_id, int(owner_id))).fetchall()


def db_warzone_troop_stats(session_id, user_id, conn=None):
    """Return UID and mutable/base combat stats for a player's troops."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT gc.card_uid, ct.attack, ct.defense, gc.card_defense_mod, "
        "gc.card_damage, gc.card_state, gc.position FROM game_cards gc "
        "JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.user_id=? AND gc.location='warzone' "
        "AND gc.card_type LIKE '%Troop%'",
        (session_id, user_id)).fetchall()


def db_warzone_card_stats(session_id, user_id=None, conn=None):
    """Return UID and combat stats for warzone permanents."""
    sql = ("SELECT gc.card_uid, ct.attack, ct.defense, gc.card_defense_mod, "
           "gc.card_damage, gc.card_state, gc.position, gc.card_type "
           "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
           "WHERE gc.session_id=? AND gc.location='warzone'")
    params = [session_id]
    if user_id is not None:
        sql += " AND gc.user_id=?"
        params.append(user_id)
    return (conn or _pvp_db._db_layer._db).execute(sql, params).fetchall()


def db_void_card(session_id, card_uid, conn=None):
    """Move a session card to void without forcing a commit."""
    return (conn or _pvp_db._db_layer._db).execute(
        "UPDATE game_cards SET location='void', position=0 "
        "WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).rowcount


def db_set_card_owner(session_id, card_uid, owner_user_id, conn=None):
    """Change current control of a session card without changing provenance."""
    return (conn or _pvp_db._db_layer._db).execute(
        "UPDATE game_cards SET user_id=? WHERE session_id=? AND card_uid=?",
        (int(owner_user_id), session_id, int(card_uid))).rowcount


def db_steal_card_to_hand(session_id, card_uid, owner_user_id, conn=None):
    """Take a deck card into hand, clearing transient state."""
    return (conn or _pvp_db._db_layer._db).execute(
        "UPDATE game_cards SET user_id=?, location='hand', position=100, "
        "card_state=0 WHERE session_id=? AND card_uid=?",
        (int(owner_user_id), session_id, int(card_uid))).rowcount


def db_zone_card_count(session_id, user_id, zone, card_type=None, conn=None):
    """Count cards in a player's session zone, optionally by exact type."""
    connection = conn or _pvp_db._db_layer._db
    sql = "SELECT COUNT(*) FROM game_cards WHERE session_id=? AND user_id=? AND location=?"
    params = [session_id, user_id, zone]
    if card_type is not None:
        sql += " AND card_type=?"
        params.append(card_type)
    row = connection.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def db_warzone_defender_rows(session_id, user_id, tapped_mask, conn=None):
    """Return untapped troop UIDs and effective attributes for blocking."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT gc.card_uid, (ct.attributes | gc.card_attributes | "
        "COALESCE(gc.temporary_attributes, 0)) "
        "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.user_id=? AND gc.location='warzone' "
        "AND gc.card_type LIKE '%Troop%' AND (gc.card_state & ?) = 0",
        (session_id, user_id, tapped_mask)).fetchall()


def db_warzone_troop_uids(session_id, conn=None):
    """Return warzone troop UIDs in stable database order."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid FROM game_cards WHERE session_id=? "
        "AND location='warzone' AND card_type LIKE '%Troop%'",
        (session_id,)).fetchall()


def db_underground_card_rows(session_id, owner_id, conn=None):
    """Return underground card UIDs and templates for one controller."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid, template_guid FROM game_cards "
        "WHERE session_id=? AND user_id=? AND location='underground'",
        (session_id, int(owner_id))).fetchall()


def db_visibility_underground_rows(session_id, conn=None):
    """Return underground cards and ability payloads for visibility scans."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid, user_id, card_abilities FROM game_cards "
        "WHERE session_id=? AND location='underground'", (session_id,)).fetchall()


def db_visibility_hand_rows(session_id, user_id, conn=None):
    """Return hand projections used when revealing an opponent's hand."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid, template_guid, user_id, card_type, card_state "
        "FROM game_cards WHERE session_id=? AND user_id=? AND location='hand' "
        "ORDER BY position, id", (session_id, int(user_id))).fetchall()


def db_set_card_owners(card_owners, conn=None):
    """Apply current-controller changes to multiple session cards."""
    if not card_owners:
        return
    (conn or _pvp_db._db_layer._db).executemany(
        "UPDATE game_cards SET user_id=? WHERE session_id=? AND card_uid=?",
        [(int(owner), session_id, int(uid))
         for session_id, uid, owner in card_owners])


def db_set_card_positions(card_positions, conn=None):
    """Persist ordered positions for session cards."""
    if not card_positions:
        return
    (conn or _pvp_db._db_layer._db).executemany(
        "UPDATE game_cards SET position=? WHERE session_id=? AND card_uid=?",
        [(int(position), session_id, int(uid))
         for session_id, uid, position in card_positions])


def db_starting_hand_candidates(session_id, user_id, card_types=None,
                                conn=None):
    """Return hand cards eligible for metadata-driven opening effects."""
    connection = conn or _pvp_db._db_layer._db
    if card_types:
        placeholders = ",".join("?" for _ in card_types)
        return connection.execute(
            "SELECT card_uid, template_guid, permanent_buffs FROM game_cards "
            "WHERE session_id=? AND user_id=? AND location='hand' "
            "AND card_type IN (" + placeholders + ")",
            (session_id, int(user_id), *card_types)).fetchall()
    return connection.execute(
        "SELECT card_uid, template_guid, permanent_buffs FROM game_cards "
        "WHERE session_id=? AND user_id=? AND location='hand' "
        "AND card_type LIKE '%Troop%'", (session_id, int(user_id))).fetchall()


def db_update_card_state(session_id, card_uid, set_bits=0, clear_bits=0,
                         reset_damage=False, conn=None):
    """Apply state bit changes to one card without forcing a commit."""
    connection = conn or _pvp_db._db_layer._db
    damage_sql = ", card_damage=0" if reset_damage else ""
    connection.execute(
        "UPDATE game_cards SET card_state = (card_state | ?) & ~?" + damage_sql
        + " WHERE session_id=? AND card_uid=?",
        (set_bits, clear_bits, session_id, int(card_uid)),
    )


def db_set_card_state_exact(session_id, card_uid, state, conn=None):
    """Persist an exact card-state bitmask without forcing a commit."""
    connection = conn or _pvp_db._db_layer._db
    cur = connection.execute(
        "UPDATE game_cards SET card_state=? WHERE session_id=? AND card_uid=?",
        (int(state), session_id, int(card_uid)))
    return getattr(cur, "rowcount", None)


def db_set_card_row_positions(row_positions, conn=None):
    """Set positions for card rows without committing the caller transaction."""
    if not row_positions:
        return
    connection = conn or _pvp_db._db_layer._db
    connection.executemany(
        "UPDATE game_cards SET position=? WHERE id=?",
        [(int(position), int(row_id)) for row_id, position in row_positions],
    )


def db_update_game_card_abilities(rows, conn=None):
    """Update prepared ability lists after player deck materialization."""
    if not rows:
        return
    connection = conn or _pvp_db._db_layer._db
    connection.executemany(
        "UPDATE game_cards SET card_abilities=? "
        "WHERE session_id=? AND card_uid=?", rows)
