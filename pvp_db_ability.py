import json
from domain.constants import DEFAULT_STARTING_HEALTH
from domain.enums import ECardStates, ECardTypes, ETurnPhases
import pvp_db as _pvp_db


def _cached_static_row(connection, key, sql, params):
    return _pvp_db._cached_static_value(
        connection, key,
        lambda: connection.execute(sql, params).fetchone())


def _cached_static_rows(connection, key, sql, params):
    value = _pvp_db._cached_static_value(
        connection, key,
        lambda: tuple(connection.execute(sql, params).fetchall()))
    shared_connection = _pvp_db._shared_db_connection()
    return (value if shared_connection is not None and
            connection is shared_connection else list(value))


def db_discard_card(session_id, card_uid, owner_user_id=None,
                    extra_set=None, extra_params=None, connection=None,
                    conn=None):
    """Move a card to its owner's discard pile.

    This keeps the legacy return value and trusted extra-update contract, but
    leaves explicit transactions for the caller to commit.
    """
    explicit = connection is not None or conn is not None
    connection = connection or conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT user_id FROM game_cards WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).fetchone()
    if not row:
        return None
    owner = int(row[0] if owner_user_id is None else owner_user_id)
    position = connection.execute(
        "SELECT COALESCE(MAX(position) + 1, 1) FROM game_cards "
        "WHERE session_id=? AND user_id=? AND location='discard'",
        (session_id, owner)).fetchone()[0]
    sql = "UPDATE game_cards SET user_id=?, location='discard', position=?"
    params = [owner, int(position or 1)]
    if extra_set:
        sql += ", " + extra_set
        params.extend(extra_params or [])
    sql += " WHERE session_id=? AND card_uid=?"
    params.extend([session_id, int(card_uid)])
    connection.execute(sql, params)
    if not explicit:
        connection.commit()
    return owner


def db_champion_template_health(guid, conn=None):
    """Return a champion template's starting health."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT starting_health FROM champion_template_data WHERE guid=?",
        (guid,)).fetchone()
    return row[0] if row else DEFAULT_STARTING_HEALTH


def db_champion_template_health_by_class(race_name, cls_name, conn=None):
    """Return starting health from champion class data."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT starting_health FROM champion_class_data "
        "WHERE race=? AND champion_class=?", (race_name, cls_name)).fetchone()
    return row[0] if row else DEFAULT_STARTING_HEALTH


def db_apply_replica_mods(session_id, card_uid, card_type, buffs_json,
                          conn=None):
    """Persist the client's ``HandleReplicaMods`` projection on a card.

    The Replica modification adds Artifact to the card type, adds the Replica
    subtype (and Robot for troops), clears thresholds, deletes Unique and sets
    the permanent-data IsReplica markers.  Type/subtype/threshold views read
    the columns and permanent-buffs payload this writes.
    """
    return (conn or _pvp_db._db_layer._db).execute(
        "UPDATE game_cards SET card_type=?, "
        "card_attributes=(COALESCE(card_attributes,0) & ~?), "
        "permanent_buffs=? WHERE session_id=? AND card_uid=?",
        (card_type, 256, buffs_json, session_id, int(card_uid)))


def db_bulk_blocker_state(session_id, blocker_uids, conn=None):
    """Mark each declared blocker as Blocking within the caller's transaction."""
    if not blocker_uids:
        return
    connection = conn or _pvp_db._db_layer._db
    connection.executemany(
        "UPDATE game_cards SET card_state = (card_state | ?) "
        "WHERE session_id=? AND card_uid=?",
        [(ECardStates.Blocking, session_id, int(uid))
         for uid in blocker_uids],
    )
    if conn is None:
        connection.commit()


def db_apply_tournament_main_deck(session_id, user_id, main_card_uids,
                                  conn=None):
    """Move a saved tournament main deck into the playable deck zone."""
    connection = conn or _pvp_db._db_layer._db
    ids = [int(uid) for uid in main_card_uids]
    connection.execute(
        "UPDATE game_cards SET location='sideboard', position=0 "
        "WHERE session_id=? AND user_id=? AND card_type!='Champion'",
        (session_id, user_id))
    connection.executemany(
        "UPDATE game_cards SET location='deck', position=? "
        "WHERE session_id=? AND user_id=? AND card_uid=?",
        [(index, session_id, user_id, card_uid)
         for index, card_uid in enumerate(ids)])
    if conn is None:
        connection.commit()


def db_champion_ability_guids(champion_guid, conn=None):
    """Return the authored ability GUIDs for one champion template."""
    rows = (conn or _pvp_db._db_layer._db).execute(
        "SELECT ability_guid FROM champion_abilities WHERE champion_guid=?",
        (champion_guid,)).fetchall()
    return [row[0] for row in rows]


def db_champion_ability_costs(ability_guid, conn=None):
    """Return normalized charge-power costs and phase restrictions."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT charge_cost, spell_cost, casting_behavior "
        "FROM champion_abilities WHERE ability_guid=? LIMIT 1",
        (str(ability_guid),)).fetchone()
    if not row:
        return None
    casting = row[2] or 0
    if casting == ECardTypes.QuickAction:
        phases = 0
    elif casting == ECardTypes.BasicAction:
        phases = ((1 << ETurnPhases.FirstMainPhase)
                  | (1 << ETurnPhases.SecondMainPhase))
    else:
        phases = 0
        casting = ECardTypes.QuickAction
    return (row[0] or 0, row[1] or 0, phases, casting)


def db_champion_ability_thresholds(ability_guid, conn=None):
    """Return ``(color, quantity)`` threshold requirements for a power."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT thresholds_json FROM champion_abilities "
        "WHERE ability_guid=? LIMIT 1", (str(ability_guid),)).fetchone()
    if not row or not row[0]:
        return []
    try:
        data = json.loads(row[0])
    except Exception:
        return []
    return [(str(item.get("color", "")),
             int(item.get("quantity", 0) or 0))
            for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def db_ability_option_cards(session_id, user_id, conn=None):
    """Return owned hand/warzone cards and fields used for activation options."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT gc.card_uid, gc.template_guid, gc.card_state, "
        "(ct.attributes | gc.card_attributes), ct.card_type, gc.location "
        "FROM game_cards gc JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.user_id=? "
        "AND gc.location IN ('warzone','hand')",
        (session_id, user_id),
    ).fetchall()


def db_all_cards_in_zones_with_abilities(session_id, zones, conn=None):
    """Return all players' card UIDs and controllers for static aura scans."""
    if not zones:
        return []
    marks = ",".join("?" for _ in zones)
    cursor = (conn or _pvp_db._db_layer._db).execute(
        "SELECT card_uid, user_id, card_abilities FROM game_cards "
        "WHERE session_id=? AND location IN (" + marks + ") "
        "AND card_abilities IS NOT NULL AND card_abilities != ''",
        [session_id, *zones])
    return cursor.fetchall() if hasattr(cursor, "fetchall") else []


def db_ai_hand_template_rows(session_id, conn=None):
    """Return AI hand UIDs and template GUIDs in display order."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT gc.card_uid, gc.template_guid FROM game_cards gc "
        "WHERE gc.session_id=? AND gc.user_id=0 AND gc.location='hand' "
        "ORDER BY gc.position", (session_id,)).fetchall()


def db_gem_templates(conn=None):
    """Return authored gem names, types, and granted abilities."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT gem_type_name, gem_type, abilities_json FROM gem_templates"
    ).fetchall()


def db_ability_metadata(ability_guid, conn=None):
    """Return setup-relevant manual/trigger metadata for an ability."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT is_manual, trigger_event_type FROM card_abilities_meta "
        "WHERE ability_guid=?", (str(ability_guid).lower(),)
    ).fetchone()


def db_ability_raw_json(ability_guid, conn=None):
    """Return the raw authored metadata for one ability."""
    connection = conn or _pvp_db._db_layer._db
    row = _cached_static_row(
        connection, ("ability_raw", str(ability_guid).lower()),
        "SELECT raw_json FROM card_abilities_meta WHERE ability_guid=?",
        (str(ability_guid).lower(),))
    return row[0] if row else None


def db_any_ability_raw_json(ability_guid, conn=None):
    """Return raw metadata for a card or champion ability."""
    connection = conn or _pvp_db._db_layer._db
    guid = str(ability_guid).lower()

    def load():
        try:
            value = db_ability_raw_json(guid, conn=connection)
        except Exception:
            value = None
        if value:
            return value
        try:
            row = connection.execute(
                "SELECT raw_json FROM champion_abilities "
                "WHERE ability_guid=? LIMIT 1", (guid,)).fetchone()
        except Exception:
            row = None
        return row[0] if row else None

    return _pvp_db._cached_static_value(connection, ("any_ability_raw", guid), load)


def db_ability_trigger_metadata(ability_guid, conn=None):
    """Return trigger/manual classification for one ability."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT is_triggered, is_manual, trigger_event_type "
        "FROM card_abilities_meta WHERE ability_guid=? LIMIT 1",
        (str(ability_guid).lower(),)).fetchone()


def db_ability_activation_metadata(ability_guid, conn=None):
    """Return activation limits, manual flag, and raw metadata."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT activation_cost, uses_per_game, uses_per_turn, "
        "exhausts_on_use, is_manual, raw_json FROM card_abilities_meta "
        "WHERE ability_guid=?", (str(ability_guid).lower(),)).fetchone()


def db_ability_effect_rows(ability_guid, conn=None):
    """Return BOM effect IDs, types, and parameters for an ability."""
    connection = conn or _pvp_db._db_layer._db
    return _cached_static_rows(
        connection, ("ability_effect_rows", str(ability_guid).lower()),
        "SELECT effect_guid, effect_type, param FROM ability_effects "
        "WHERE ability_guid=?", (str(ability_guid).lower(),)
    )


def db_ability_effect_type_params(ability_guid, conn=None):
    """Return ordered effect types and serialized parameters."""
    connection = conn or _pvp_db._db_layer._db
    return _cached_static_rows(
        connection, ("ability_effect_type_params", str(ability_guid).lower()),
        "SELECT effect_type, param FROM ability_effects "
        "WHERE ability_guid=? ORDER BY effect_order",
        (str(ability_guid).lower(),))


def db_ability_effect_target_index(ability_guid, effect_guid, conn=None):
    """Return the authored target index for one ability effect."""
    row = (conn or _pvp_db._db_layer._db).execute(
        "SELECT target_index FROM ability_effects WHERE ability_guid=? "
        "AND effect_guid=? LIMIT 1",
        (ability_guid, effect_guid)).fetchone()
    return row[0] if row else None


def db_ability_has_effect(ability_guid, conn=None):
    """Whether an ability has at least one materialized BOM effect."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT 1 FROM ability_effects WHERE ability_guid=? LIMIT 1",
        (str(ability_guid).lower(),)).fetchone()


def db_ability_metadata_exists(ability_guid, conn=None):
    """Whether a card ability is present in the materialized metadata."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT 1 FROM card_abilities_meta WHERE ability_guid=? LIMIT 1",
        (str(ability_guid).lower(),)).fetchone() is not None


def db_ability_target_template_ids(ability_guid, conn=None):
    """Return the serialized target-template IDs for an ability."""
    connection = conn or _pvp_db._db_layer._db
    row = _cached_static_row(
        connection, ("ability_target_ids", str(ability_guid).lower()),
        "SELECT target_template_ids FROM card_abilities_meta "
        "WHERE ability_guid=?", (str(ability_guid).lower(),)
    )
    return row[0] if row else None


def db_champion_ability_target_template_ids(ability_guid, conn=None):
    """Return target-template IDs authored for a champion ability."""
    connection = conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT target_template_ids FROM champion_abilities "
        "WHERE ability_guid=? LIMIT 1", (str(ability_guid).lower(),)
    ).fetchone()
    return row[0] if row else None


def db_talent_ability_condition(ability_guid, conn=None):
    """Return the talent and condition attached to an ability."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT talent_guid, condition FROM talent_abilities "
        "WHERE ability_guid=? LIMIT 1", (str(ability_guid),)).fetchone()


def db_ability_static_metadata(ability_guid, conn=None):
    """Return trigger/manual/raw metadata for static ability filtering.

    Card abilities carry it in ``card_abilities_meta``. A champion or talent ability has none there, so its trigger and
    manual flag come from its Records template (the same fields the card rows are extracted from).
    """
    connection = conn or _pvp_db._db_layer._db
    row = _cached_static_row(
        connection, ("ability_static", str(ability_guid).lower()),
        "SELECT trigger_event_type, is_manual, raw_json "
        "FROM card_abilities_meta WHERE ability_guid=?",
        (str(ability_guid).lower(),))
    if row is not None:
        return row
    if not (db_talent_has_ability_guid(ability_guid, conn=connection)
            or db_champion_ability_guid_exists(ability_guid, conn=connection)):
        return None
    from gamedata import DEFAULT_RECORD_STORE
    record = DEFAULT_RECORD_STORE.get("AbilityTemplate", str(ability_guid).lower())
    if record is None:
        return None
    trigger = record.field("m_TriggerEventType", None) or {}
    trigger_type = str(trigger.get("m_InternalType") or "").rsplit(".", 1)[-1] if isinstance(trigger, dict) else ""
    return (trigger_type or None, 1 if record.field("m_Manual", 0) else 0, "")


def db_talent_has_ability_guid(ability_guid, conn=None):
    """Whether a talent authors this ability."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT 1 FROM talent_data WHERE ability_guid=? AND has_ability=1 "
        "UNION SELECT 1 FROM talent_abilities WHERE ability_guid=? LIMIT 1",
        (str(ability_guid).lower(), str(ability_guid).lower())).fetchone() is not None


def db_champion_ability_guid_exists(ability_guid, conn=None):
    """Whether any champion template authors this ability."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT 1 FROM champion_abilities WHERE ability_guid=? LIMIT 1",
        (str(ability_guid).lower(),)).fetchone() is not None


def db_talent_ability_guids(talent_guids, conn=None):
    """The abilities the given talents author, in talent order."""
    connection = conn or _pvp_db._db_layer._db
    guids = []
    for talent in talent_guids or ():
        talent_guid = str(talent).lower()
        for row in connection.execute(
                "SELECT ability_guid FROM talent_data WHERE talent_guid=? AND has_ability=1 "
                "ORDER BY rowid", (talent_guid,)).fetchall():
            if row[0] and str(row[0]).lower() not in guids:
                guids.append(str(row[0]).lower())
        for row in connection.execute(
                "SELECT ability_guid FROM talent_abilities WHERE talent_guid=? ORDER BY rowid",
                (talent_guid,)).fetchall():
            if row[0] and str(row[0]).lower() not in guids:
                guids.append(str(row[0]).lower())
    return guids


def db_gem_abilities(gem_type, conn=None):
    """Return the serialized ability list for a gem type."""
    connection = conn or _pvp_db._db_layer._db
    row = _cached_static_row(
        connection, ("gem_abilities", int(gem_type or 0)),
        "SELECT abilities_json FROM gem_templates WHERE gem_type=?",
        (gem_type,))
    return row[0] if row else None


def db_gem_ability_guids(gem_value, conn=None):
    """Return ability GUIDs for a legacy gem or packed socket value.

    The client stores all socket positions in one UInt64.  Bit 62 marks that
    packed representation; each of the six sockets uses a ten-bit gem type.
    Older persisted rows can still contain a single gem type, so accept both
    forms at this boundary.
    """
    try:
        raw = int(gem_value or 0)
    except (TypeError, ValueError):
        return []
    if raw <= 0:
        return []
    if raw & _pvp_db.EMPTY_SOCKET_GEMS or raw > 0x3FF:
        # Older server builds truncated the UInt64 marker off at deck-save
        # time. A value above the ten-bit EGemTypesNew slot range is therefore
        # still recognizable as the low socket payload (for example 1056 is
        # slot 0 = 32 and slot 1 = 1).
        values = [(raw >> (slot * 10)) & 0x3FF for slot in range(6)]
    else:
        values = [raw]
    guids = []
    for gem_type in dict.fromkeys(value for value in values if value):
        payload = db_gem_abilities(gem_type, conn=conn)
        if not payload:
            continue
        try:
            abilities = json.loads(payload or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        for ability in abilities:
            if ability:
                guid = str(ability).lower()
                if guid not in guids:
                    guids.append(guid)
    return guids


def db_champion_charge_power_guids(conn=None):
    """Return the distinct authored champion charge-power ability GUIDs."""
    return [str(row[0]).lower() for row in (
        conn or _pvp_db._db_layer._db).execute(
            "SELECT DISTINCT ability_guid FROM champion_abilities "
            "WHERE ability_guid IS NOT NULL AND ability_guid<>''")]


def db_ai_hand_cards(session_id, conn=None):
    """Return AI hand cards in the client-visible position order."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT card_uid, template_guid FROM game_cards "
        "WHERE session_id=? AND user_id=0 AND location='hand' "
        "ORDER BY position", (session_id,)).fetchall()


def db_ai_deck_top_card(session_id, conn=None):
    """Return the complete persisted row needed for an AI draw."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT id, card_uid, card_template_id, template_guid "
        "FROM game_cards WHERE session_id=? AND user_id=0 "
        "AND location='deck' ORDER BY position LIMIT 1", (session_id,)
    ).fetchone()


def db_ai_hand_tunneling_cards(session_id, conn=None, owner_id=0):
    """Return non-resource AI hand cards and their tunneling metadata."""
    connection = conn or _pvp_db._db_layer._db
    return connection.execute(
        "SELECT gc.card_uid, gc.template_guid, gc.card_state, "
        "gc.permanent_buffs, COALESCE(ct.cost, 0), ct.threshold_json, "
        "gc.card_abilities FROM game_cards gc "
        "JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.user_id=? AND gc.location='hand' "
        "AND ct.card_type NOT LIKE '%Resource%' "
        "ORDER BY gc.position, gc.card_uid",
        (session_id, int(owner_id))).fetchall()


def db_ai_evaluator_card_rows(session_id, user_id, zone, conn=None):
    """Return the joined card snapshot consumed by the AI evaluator."""
    if zone not in {"hand", "warzone"}:
        raise ValueError("unsupported evaluator zone")
    connection = conn or _pvp_db._db_layer._db
    ct_cols = {row[1] for row in connection.execute(
        "PRAGMA table_info(card_templates)").fetchall()}
    current = ("ct.current_resources_granted" if
               "current_resources_granted" in ct_cols else "0")
    maximum = ("ct.max_resources_granted" if
               "max_resources_granted" in ct_cols else "0")
    # ``card_abilities``/``card_attributes`` are the live instance values
    # (granted/revoked abilities and attribute flags).  The AI evaluator must
    # see them or a troop carrying a granted curse/keyword is scored as its
    # printed template (Spider Nest re-targeting the same trooper).
    extra = ", gc.card_attack_mod, gc.card_defense_mod" if zone == "warzone" else ""
    return connection.execute(
        "SELECT gc.card_uid, gc.template_guid, gc.location, ct.card_type, "
        "ct.name, ct.rarity, ct.cost, ct.attack, ct.defense, "
        "ct.threshold_json, ct.abilities_json, ct.attributes, ct.subtype, "
        "ct.variable_cost, ct.variable_cost_double, " + current + ", " +
        maximum + ", gc.card_state, "
        "gc.card_damage, gc.permanent_buffs, gc.temporary_buffs, "
        "gc.temporary_attributes" + extra +
        ", gc.card_abilities, gc.card_attributes FROM game_cards gc "
        "JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.user_id=? AND gc.location=? "
        "ORDER BY gc.position", (session_id, int(user_id), zone)).fetchall()


def db_ai_evaluator_card_row(session_id, card_uid, conn=None):
    """Return one joined card snapshot for an ability source in any zone.

    Triggered and nested abilities can resolve after their source card has
    left the hand or Warzone.  The AI still needs that source's authored
    ability metadata to choose a fresh explicit target, so this deliberately
    does not restrict the card location or controller.
    """
    connection = conn or _pvp_db._db_layer._db
    ct_cols = {row[1] for row in connection.execute(
        "PRAGMA table_info(card_templates)").fetchall()}
    current = ("ct.current_resources_granted" if
               "current_resources_granted" in ct_cols else "0")
    maximum = ("ct.max_resources_granted" if
               "max_resources_granted" in ct_cols else "0")
    return connection.execute(
        "SELECT gc.card_uid, gc.template_guid, gc.location, ct.card_type, "
        "ct.name, ct.rarity, ct.cost, ct.attack, ct.defense, "
        "ct.threshold_json, ct.abilities_json, ct.attributes, ct.subtype, "
        "ct.variable_cost, ct.variable_cost_double, " + current + ", " +
        maximum + ", gc.card_state, "
        "gc.card_damage, gc.permanent_buffs, gc.temporary_buffs, "
        "gc.temporary_attributes, gc.card_abilities, gc.card_attributes "
        "FROM game_cards gc "
        "JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.card_uid=? LIMIT 1",
        (session_id, int(card_uid))).fetchone()


def db_ability_is_triggered(ability_guid, conn=None):
    """Return the authored trigger classification for an ability."""
    connection = conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT is_triggered FROM card_abilities_meta "
        "WHERE lower(ability_guid)=?", (str(ability_guid).lower(),)
    ).fetchone()
    return None if row is None else bool(row[0])


def db_ability_game_text(ability_guid, conn=None):
    """Return authored display text for one ability."""
    connection = conn or _pvp_db._db_layer._db
    row = connection.execute(
        "SELECT game_text FROM card_abilities_meta WHERE ability_guid=?",
        (str(ability_guid).lower(),)).fetchone()
    return row[0] if row else None


def db_ability_effect_metadata_rows(ability_guid, conn=None):
    """Return effect IDs/types for creation-replacement inspection."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT effect_guid, effect_type FROM ability_effects "
        "WHERE ability_guid=?", (str(ability_guid).lower(),)).fetchall()


def db_add_card_cost_modifier(session_id, card_uid, delta, conn=None):
    """Apply an additive cost modifier without forcing a commit."""
    return (conn or _pvp_db._db_layer._db).execute(
        "UPDATE game_cards SET card_cost_mod = "
        "COALESCE(card_cost_mod, 0) + ? WHERE session_id=? AND card_uid=?",
        (int(delta), session_id, int(card_uid)))


def db_add_temporary_attributes(session_id, card_uid, attributes, conn=None,
                                owner_id=None, boundary=None):
    """OR temporary card attributes and optionally record their expiry.

    A grant written without an expiry rule is cleared at the affected card's
    next owner boundary, which is wrong for a grant with an authored duration
    that is issued inside the same turn-start sequence it belongs to (a
    Tunneling Surface resolves at StartTurn and the Prep that follows would
    clear it before the turn it was granted for has ended).  Callers that know
    the duration pass ``owner_id``/``boundary`` so the shared
    ``clear_expired_temporary_attributes`` readers expire it at the right
    boundary; the metadata key and rule shape are theirs.
    """
    connection = conn or _pvp_db._db_layer._db
    connection.execute(
        "UPDATE game_cards SET temporary_attributes = "
        "COALESCE(temporary_attributes, 0) | ? "
        "WHERE session_id=? AND card_uid=?",
        (int(attributes), session_id, int(card_uid)))
    if not attributes or owner_id is None or not boundary:
        return
    row = connection.execute(
        "SELECT COALESCE(temporary_attributes, 0), temporary_buffs "
        "FROM game_cards WHERE session_id=? AND card_uid=?",
        (session_id, int(card_uid))).fetchone()
    if not row:
        return
    bits = int(row[0] or 0) & int(attributes)
    try:
        buffs = json.loads(row[1] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        buffs = {}
    if not isinstance(buffs, dict):
        buffs = {}
    metadata = buffs.setdefault("__attribute_expirations", {})
    for bit in (1 << index for index in range(bits.bit_length())
                if bits & (1 << index)):
        metadata[str(bit)] = {"owner": int(owner_id),
                              "boundary": str(boundary)}
    connection.execute(
        "UPDATE game_cards SET temporary_buffs=? "
        "WHERE session_id=? AND card_uid=?",
        (json.dumps(buffs, separators=(",", ":"), sort_keys=True), session_id,
         int(card_uid)))


def db_ability_target_effect_rows(ability_guid, conn=None):
    """Return target-indexed effects in authored order."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT target_index, effect_type FROM ability_effects "
        "WHERE ability_guid=? AND target_index>=0 ORDER BY effect_order",
        (str(ability_guid).lower(),)).fetchall()


def db_champion_trigger_ability_guids(champion_guid, conn=None):
    """Return metadata-indexed triggered abilities for a champion."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT ca.ability_guid FROM champion_abilities ca "
        "JOIN card_abilities_meta cam ON cam.ability_guid=ca.ability_guid "
        "WHERE ca.champion_guid=? AND cam.trigger_event_type IS NOT NULL "
        "AND cam.trigger_event_type != '' ORDER BY ca.ability_guid",
        (str(champion_guid),)).fetchall()


def db_ai_hand_summary(session_id, conn=None):
    """Return AI hand count and resource count in position-independent form."""
    return (conn or _pvp_db._db_layer._db).execute(
        "SELECT COUNT(*), SUM(CASE WHEN COALESCE(ct.card_type, gc.card_type)="
        "'Resource' THEN 1 ELSE 0 END) FROM game_cards gc "
        "LEFT JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.user_id=0 AND gc.location='hand'",
        (session_id,)).fetchone()


def db_ai_zone_rows(session_id, zone, limit=None, conn=None):
    """Return AI game-card IDs/Uids ordered by authoritative deck position."""
    sql = ("SELECT id, card_uid FROM game_cards WHERE session_id=? "
           "AND user_id=0 AND location=? ORDER BY position")
    params = [session_id, zone]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return (conn or _pvp_db._db_layer._db).execute(sql, params).fetchall()


def db_apply_starting_hand_effect(session_id, card_uid, cost_mod=0,
                                  permanent_buffs=None, conn=None):
    """Apply an opening-hand cost/rage mutation without committing."""
    connection = conn or _pvp_db._db_layer._db
    if cost_mod:
        connection.execute(
            "UPDATE game_cards SET card_cost_mod=COALESCE(card_cost_mod, 0) + ? "
            "WHERE session_id=? AND card_uid=?",
            (int(cost_mod), session_id, int(card_uid)))
    if permanent_buffs is not None:
        connection.execute(
            "UPDATE game_cards SET permanent_buffs=? WHERE session_id=? "
            "AND card_uid=?", (permanent_buffs, session_id, int(card_uid)))


def db_ai_hand_playables(session_id, user_id, kind, conn=None):
    """Return AI hand cards eligible for the requested play family."""
    connection = conn or _pvp_db._db_layer._db
    if kind == "permanent":
        predicate = "(ct.card_type LIKE '%Troop%' OR ct.card_type LIKE '%Artifact%' OR ct.card_type LIKE '%Constant%')"
    elif kind == "spell":
        predicate = "ct.card_type IN ('BasicAction','QuickAction')"
    else:
        raise ValueError("unknown AI play family")
    return connection.execute(
        "SELECT gc.id, gc.card_uid, gc.template_guid, ct.cost, ct.card_type, "
        "ct.threshold_json, ct.attack, ct.defense FROM game_cards gc "
        "JOIN card_templates ct ON ct.guid=gc.template_guid "
        "WHERE gc.session_id=? AND gc.user_id=? AND gc.location='hand' AND "
        + predicate + " ORDER BY gc.position",
        (session_id, user_id),
    ).fetchall()


def db_add_card_damage(session_id, card_uid, amount, conn=None):
    """Add damage to one card; the caller owns the transaction.

    Like C# ``Session.DealDamage``, taking damage also sets
    ``ECardStates.Damaged``, which ``IsDamagedThisTurn`` filters read.
    """
    connection = conn or _pvp_db._db_layer._db
    damaged = _pvp_db._DAMAGED_STATE_BIT if int(amount) > 0 else 0
    connection.execute(
        "UPDATE game_cards SET card_damage = card_damage + ?, "
        "card_state = COALESCE(card_state, 0) | ? "
        "WHERE session_id=? AND card_uid=?",
        (int(amount), damaged, session_id, int(card_uid)),
    )
