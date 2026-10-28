"""RulesPort-owned transform effect operations."""

from __future__ import annotations

import json
from types import SimpleNamespace
import game_engine


def linked_template_guids(db, ability_guid):
    """Return card ResourceIds linked by the typed Records ability graph."""
    from gamedata import DEFAULT_RECORD_STORE, ability_graph
    from pvp_db import db_template_exists
    graph = ability_graph(DEFAULT_RECORD_STORE, str(ability_guid or "").lower())
    if graph is None:
        return []
    found = []
    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"m_Guid", "guid"} and isinstance(child, str):
                    value = child.lower()
                    if db_template_exists(value, conn=db) and value not in found:
                        found.append(value)
                else:
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(graph.source.to_dict())
    return found


def _transform_event_viewers(context, card_uid, owner_id, old_zone,
                             old_type, template_guid):
    """Snapshot C# ``CanPlayerSeeCard`` recipients before the mutation."""
    from domain.game import _card_update_visible_to_viewer
    from rules_port.runtime_helpers import (
        card_collection_for_location, owner_uid, raw_uid)

    game = context.game
    controller = owner_uid(owner_id, context.player_uid, context.ai_uid,
                           context.bstate)
    collection = card_collection_for_location(old_zone)
    try:
        transformed_identity = game_engine.ResourceId.from_str(template_guid)
    except (TypeError, ValueError):
        transformed_identity = game_engine.ResourceId.invalid()
    projection = SimpleNamespace(
        card_type=old_type, collection=collection, controller=controller,
        player_id=controller,
        card_id=transformed_identity)
    visibility_by_uid = getattr(game, "_visibility_by_uid", {}) or {}
    participants = [getattr(game, "player_uid", None),
                    getattr(game, "ai_uid", None)]

    # Card.CanPlayerSeeCard also permits a viewer with the appropriate
    # authored top-of-deck flag to see only the first deck card.
    is_top_deck_card = False
    participant_owners = []
    if (context.bstate.get("pvp") and context.bstate.get("pids")):
        participant_owners = [int(owner) for owner in
                              context.bstate.get("pids", ())]
    else:
        profile = getattr(context.handler, "user_profile", None) or {}
        participant_owners = [int(profile.get("id", 0) or 0), 0]
    if str(old_zone or "").lower() == "deck":
        try:
            from pvp_db import db_deck_top_card_details
            top = db_deck_top_card_details(
                context.session.session_id, int(owner_id), conn=context.db)
            is_top_deck_card = bool(top and int(top[1]) == int(card_uid))
        except (TypeError, ValueError, AttributeError):
            is_top_deck_card = False

    visible = set()
    for viewer in participants:
        if viewer is None:
            continue
        try:
            viewer_id = raw_uid(viewer)
        except (TypeError, ValueError):
            continue
        flags = visibility_by_uid.get(viewer_id, {}) or {}
        deck_reveal = False
        if is_top_deck_card:
            viewer_owner = next((
                participant_owner for participant_owner in participant_owners
                if raw_uid(owner_uid(
                    participant_owner, context.player_uid, context.ai_uid,
                    context.bstate)) == viewer_id), None)
            if viewer_owner is not None:
                try:
                    from .static_rules import player_int_attributes
                    attrs = player_int_attributes(
                        context.db, context.session.session_id,
                        context.bstate, int(viewer_owner))
                    permission = ("CanSeeTopOfDeck" if
                                  int(viewer_owner) == int(owner_id) else
                                  "CanSeeOpponentsTopOfDeck")
                    deck_reveal = int(attrs.get(permission, 0) or 0) > 0
                except (TypeError, ValueError, AttributeError):
                    deck_reveal = False
        if _card_update_visible_to_viewer(
                projection, viewer, flags, viewer_reveal=deck_reveal):
            visible.add(viewer_id)
    return visible


def _requires_transform_revert(old_type, new_type):
    """Mirror the category gates in Session.TransformCard/TransformIntoCopy."""
    old_type = int(old_type or 0)
    new_type = int(new_type or 0)
    action_or_resource = int(
        game_engine.ECardTypes.BasicAction |
        game_engine.ECardTypes.QuickAction |
        game_engine.ECardTypes.Resource)
    return bool(
        old_type & int(game_engine.ECardTypes.Troop) and
        not new_type & int(game_engine.ECardTypes.Troop) or
        bool(old_type & action_or_resource) !=
        bool(new_type & action_or_resource))


def _refresh_champion_portrait(context, card_uid, template_guid,
                               before_announce=None):
    """Re-send ChampionCardPlayed for a transformed champion.

    ``before_announce(champion_scid, is_ai)`` runs after the handler's
    champion identity is retargeted and before ChampionCardPlayed, so a
    caller can refresh the client's cached champion CardRepresentation that
    OnChampionCardPlayed reads its template, name and health from.

    The client sets the HUD portrait, name and sleeves only in
    OnChampionCardPlayed; CardTransformed and CardUpdated refresh the card
    view alone.  Re-announce the champion with its new template so the
    portrait follows the transform.  The board tuple keeps the template
    GUID for later re-announcements.
    """
    handler = context.handler
    # Statics read the champion's authored abilities from champ_guid_map, so
    # a transformed champion must carry its new template there. Otherwise the
    # old champion's "can't draw cards" keeps applying (Chained Goliath ->
    # Inferno Goliath still failed to draw).
    guid_map = context.bstate.get("champ_guid_map")
    champ_map = context.bstate.get("champ_map") or {}
    if isinstance(guid_map, dict):
        for owner_key, uid in champ_map.items():
            try:
                if int(uid) == int(card_uid):
                    guid_map[str(owner_key)] = template_guid
            except (TypeError, ValueError):
                continue
    for attr, board_attr, guid_attr, is_ai in (
            ("_player_champ_scid", "_player_champ_board",
             "_player_champ_guid", False),
            ("_ai_champ_scid", "_ai_champ_board", "_ai_champ_guid", True)):
        champion = getattr(handler, attr, None)
        if champion is None or int(champion.uid.uid64) != int(card_uid):
            continue
        setattr(handler, guid_attr, template_guid)
        # champ_guid_map is read by controller (the statics, and the interpreter's champion subtype). The champion that
        # transformed carries its new template there, even when no champ_map names it.
        if isinstance(guid_map, dict):
            from .runtime_helpers import champion_owner_id
            owner = 0 if is_ai else champion_owner_id(handler, context.bstate, card_uid)
            if owner is not None:
                guid_map[str(owner)] = template_guid
        # The champion CardDef's abilities come from these lists, and the client
        # shows the charge-power button only when one of them is a charge
        # power (GoHUDPlayer.SetupAbilityButtons). Refresh them so Inferno
        # Goliath's charge power appears after the transform.
        from pvp_db import db_get_champion_ability_guids
        ability_guids = db_get_champion_ability_guids(
            template_guid, conn=context.db)
        if is_ai:
            handler._ai_champ_ability_guids = list(ability_guids)
        else:
            handler._player_champ_abilities = [
                game_engine.ResourceId.from_str(guid)
                for guid in ability_guids]
        board = getattr(handler, board_attr, None)
        name = board[2] if board else ""
        if board:
            setattr(handler, board_attr,
                    (board[0], board[1], board[2], template_guid))
        if callable(before_announce):
            before_announce(champion, is_ai)
        owner = context.ai_uid if is_ai else context.player_uid
        context.game.push_champion_card_played(owner, is_ai, name, champion)
        return True
    return False


def _transform_champion_without_row(context, card_uid, owner_id,
                                    new_template_guid, event_gems=None):
    """Transform a live champion that has no ``game_cards`` row.

    PvE champions are synthetic SessionCardIds held on the handler
    (``_player_champ_scid`` / ``_ai_champ_scid``); their template identity
    lives in ``_*_champ_guid``, ``champ_guid_map`` and the champion ability
    lists rather than in ``game_cards``.  Chained Goliath's "you transform
    into Inferno Goliath" targets the AI champion this way, so the
    row-backed path below returned "target not found" and the champion
    never changed.
    """
    from pvp_db import db_copy_template_payload
    from .runtime_helpers import owner_uid
    from .triggers import dispatch_trigger
    if not db_copy_template_payload(new_template_guid, conn=context.db):
        return "transform: template not found"
    dispatch_trigger(context, "CardTransformsEvent", int(card_uid), owner_id)
    recipient = owner_uid(owner_id, context.player_uid, context.ai_uid,
                          context.bstate)

    def refresh_cached_champion(champion, is_ai):
        # Champions are public: the transform is visible to every viewer.
        context.game.push_card_transformed(
            champion, new_template_guid, gems=event_gems)
        # Re-register both champion CardDefs from the retargeted ability
        # lists, then refresh the cached champion representation (template,
        # abilities, health) before ChampionCardPlayed re-reads it.  Keep
        # collection None: a Champions collection is suppressed for champions.
        warm = getattr(context.handler, "_push_champions_warm", None)
        if callable(warm) and not context.bstate.get("pvp"):
            warm(context.session, context.player_uid, context.ai_uid,
                 context.bstate, context.game)
        health_key = "ai_health" if is_ai else "player_health"
        kwargs = {"template_id": new_template_guid}
        health = context.bstate.get(health_key)
        if health is not None:
            kwargs["defense"] = int(health)
        context.game.push_card_updated(
            champion, recipient, game_engine.ECardCollections.None_,
            game_engine.ECardTypes.Champion, **kwargs)

    if not _refresh_champion_portrait(
            context, int(card_uid), new_template_guid,
            before_announce=refresh_cached_champion):
        return "transform: target not found"
    dispatch_trigger(context, "CardTransformedEvent", int(card_uid), owner_id)
    return int(card_uid)


def transform_instance(context, card_uid, new_template_guid, *, keep_zone=True,
                       reverted=False, copy_from_uid=None, is_replica=False,
                       event_gems=None, trigger_already_dispatched=False):
    """Transform one live card while preserving its SessionCardId/state."""
    from pvp_db import (db_card_owner_location_position,
                        db_card_owner_zone_state, db_copy_template_payload,
                        db_transform_card_instance, db_card_attribute_value,
                        db_card_owner_id, db_reset_card_modifiers,
                        db_card_sacrifice_info, db_reset_card_uses,
                        db_card_source_info, db_card_original_and_template,
                        db_copy_transform_instance_state,
                        db_capture_original_gems, db_card_original_gems)
    row = db_card_owner_location_position(
        context.session.session_id, int(card_uid), conn=context.db)
    if not row:
        from .runtime_helpers import champion_owner_id
        champion_owner = (None if reverted or copy_from_uid is not None else
                          champion_owner_id(context.handler, context.bstate,
                                            int(card_uid)))
        if champion_owner is None:
            return "transform: target not found"
        return _transform_champion_without_row(
            context, int(card_uid), int(champion_owner), new_template_guid,
            event_gems=event_gems)
    owner_id, old_zone, old_position = int(row[0] or 0), row[1], int(row[2] or 0)
    state_row = db_card_owner_zone_state(
        context.session.session_id, int(card_uid), conn=context.db)
    old_state = int(state_row[2] or 0) if state_row else 0
    if not reverted and not trigger_already_dispatched:
        # Session.TransformCard enqueues CardTransformsEvent and immediately
        # handles it before it changes the card template. Listeners therefore
        # inspect the pre-transform instance and may queue their chain items
        # before the transform's own client events.
        from .triggers import dispatch_trigger
        dispatch_trigger(context, "CardTransformsEvent", int(card_uid), owner_id)
        # Card.SetActiveGems(setOriginal=True) keeps the first active-gem
        # value for Session.RevertCard. Persist it before changing this card.
        db_capture_original_gems(
            context.session.session_id, int(card_uid), conn=context.db)

    if copy_from_uid is not None:
        source_info = db_card_source_info(
            context.session.session_id, int(copy_from_uid), conn=context.db)
        if not source_info or not source_info[0]:
            return "transform: copy source not found"
        new_template_guid = str(source_info[0]).lower()
    payload = db_copy_template_payload(new_template_guid, conn=context.db)
    if not payload:
        return "transform: template not found"
    card_type = payload[0] or "Troop"
    old_card = db_card_sacrifice_info(
        context.session.session_id, int(card_uid), conn=context.db)
    old_type = game_engine.card_type_from_db(
        old_card[2] if old_card else "Troop")
    new_type = game_engine.card_type_from_db(
        db_card_sacrifice_info(
            context.session.session_id, int(copy_from_uid), conn=context.db)[2]
        if copy_from_uid is not None else card_type)
    if not reverted and _requires_transform_revert(old_type, new_type):
        original, _current = db_card_original_and_template(
            context.session.session_id, int(card_uid), conn=context.db) or (None, None)
        if original:
            # C# calls RevertCard after CardTransformsEvent and before the
            # class-44 transform. Reuse the same path so class 45 and its
            # reset state precede class 44 in the outgoing packet.
            transform_instance(
                context, int(card_uid), str(original).lower(), keep_zone=True,
                reverted=True, trigger_already_dispatched=True)
            row = db_card_owner_location_position(
                context.session.session_id, int(card_uid), conn=context.db)
            if row:
                owner_id, old_zone, old_position = (
                    int(row[0] or 0), row[1], int(row[2] or 0))
            state_row = db_card_owner_zone_state(
                context.session.session_id, int(card_uid), conn=context.db)
            old_state = int(state_row[2] or 0) if state_row else 0
            old_card = db_card_sacrifice_info(
                context.session.session_id, int(card_uid), conn=context.db)
            old_type = game_engine.card_type_from_db(
                old_card[2] if old_card else "Troop")
            if copy_from_uid is not None:
                source_info = db_card_source_info(
                    context.session.session_id, int(copy_from_uid),
                    conn=context.db)
                if not source_info or not source_info[0]:
                    return "transform: copy source not found"
                new_template_guid = str(source_info[0]).lower()
                payload = db_copy_template_payload(
                    new_template_guid, conn=context.db)
                if not payload:
                    return "transform: template not found"
                card_type = payload[0] or "Troop"
    # C# TransformCardBase dispatches class 44/45 before replacing the
    # template. Capture its recipients after CardTransformsEvent has run, but
    # before changing the card's type, collection projection, or identity.
    transform_event_viewers = _transform_event_viewers(
        context, int(card_uid), owner_id, old_zone, old_type,
        new_template_guid)
    if reverted:
        old_state &= ~int(game_engine.ECardStates.Damaged)
        original_gems = db_card_original_gems(
            context.session.session_id, int(card_uid), conn=context.db)
        db_reset_card_modifiers(
            context.session.session_id, int(card_uid), "{}", conn=context.db)
        context.db.execute(
            "UPDATE game_cards SET card_damage=0, cost_mod_json='[]', "
            "card_state=? WHERE session_id=? AND card_uid=?",
            (old_state, context.session.session_id, int(card_uid)))
    new_zone, new_position = (old_zone, old_position) if keep_zone else ("warzone", 0)
    db_transform_card_instance(
        context.session.session_id, int(card_uid), new_template_guid, card_type,
        payload[1] or "[]", int(payload[2] or 0), new_zone, new_position,
        old_state, conn=context.db,
        preserve_instance_state=copy_from_uid is not None,
        gems=original_gems if reverted else None)
    if copy_from_uid is None:
        db_reset_card_uses(
            context.session.session_id, int(card_uid),
            preserve_turn_and_cooldowns=not reverted, conn=context.db)
    else:
        db_copy_transform_instance_state(
            context.session.session_id, int(card_uid), int(copy_from_uid),
            is_replica=bool(is_replica), conn=context.db)
    context.db.commit()
    sync = getattr(context.handler, "_sync_instance_card_data", None)
    if callable(sync):
        sync(context.session, int(card_uid), new_template_guid)
    scid = game_engine.SessionCardId(game_engine.UID(int(card_uid)))
    _tpl, rendered_type, _name, cost, attack, defense, rendered_gems = \
        context.handler._card_full_data(context.game, scid, new_template_guid)
    attrs = int(db_card_attribute_value(
        context.session.session_id, int(card_uid), "card_attributes",
        conn=context.db) or payload[2] or 0)
    from .runtime_helpers import card_collection_for_location, owner_uid
    recipient = owner_uid(owner_id, context.player_uid, context.ai_uid,
                          context.bstate)
    collection = card_collection_for_location(new_zone)
    if reverted:
        context.game.push_card_reverted(
            scid, new_template_guid,
            visible_to_viewer_uids=transform_event_viewers)
    else:
        context.game.push_card_transformed(
            scid, new_template_guid, is_replica=bool(is_replica),
            gems=event_gems,
            visible_to_viewer_uids=transform_event_viewers)
    context.game.push_card_updated(
        scid, recipient, collection, rendered_type, attack=attack,
        defense=defense, cost=cost, template_id=new_template_guid,
        gems=rendered_gems,
        state=old_state, attributes=attrs)
    if not reverted:
        _refresh_champion_portrait(context, int(card_uid), new_template_guid)
    # The C# cache derives class 50 only when collection/controller changes.
    # OnCardUpdated already returns the view home for same-collection updates;
    # the source client has no separate class-50 producer for reordering.
    if not reverted:
        # The C# event carries only TriggerEvent's source card/player fields.
        # Collection snapshots are not members of CardTransformedEvent.
        dispatch_trigger(context, "CardTransformedEvent", int(card_uid), owner_id)
    return int(card_uid)


def revert_instance(context, card_uid):
    """Restore one transformed card to its recorded original template."""
    from pvp_db import db_card_original_and_template
    row = db_card_original_and_template(
        context.session.session_id, int(card_uid), conn=context.db)
    original = str(row[0] or "").lower() if row else ""
    current = str(row[1] or "").lower() if row else ""
    if not original or original == current:
        return "revert transform: no original template"
    result = transform_instance(
        context, int(card_uid), original, keep_zone=True, reverted=True)
    if isinstance(result, str):
        return result
    return f"reverted {hex(int(card_uid))}"


def transform_into_stored_copy(context, serialized):
    """TransformCardAbilityEffectTemplate with UseStoredTarget: the stored target becomes a copy of the effect's resolved
    target. The effect's template is zero; the destination is the resolved target, and the source card(s) come from the
    StoreTargets effect earlier in this ability instance. Shared by the native resolver and the interpreter."""
    from pvp_db import db_card_gem_type, db_card_source_info
    from rules_port.tac import tac_int
    copy_target = next((value for value in context.effect_targets
                        if value is not None), None)
    if copy_target is None:
        copy_target = context.target(default=None)
    if copy_target is None:
        return "transform: no copy target"
    target_info = db_card_source_info(
        context.session.session_id, int(copy_target), conn=context.db)
    new_template = (str(target_info[0]).lower()
                    if target_info and target_info[0] else "")
    if not new_template:
        return "transform: copy target not found"
    stored = context.bstate.get("stored_targets") or {}
    stored_targets = (stored.get(context.ability_guid) or
                      stored.get(str(context.bstate.get(
                          "resolving_ability") or "")) or ())
    targets = []
    for value in stored_targets:
        try:
            value = int(value)
        except (TypeError, ValueError):
            continue
        if value not in targets:
            targets.append(value)
    if not targets:
        return "transform: no stored target"
    if len(targets) != 1:
        return "transform: expected one stored target"
    source_uid = context.bstate.get("resolving_source_uid")
    event_gems = game_engine.EGemTypesNew.Unknown
    if tac_int(serialized, "CopyGems", 0) and source_uid is not None:
        event_gems = db_card_gem_type(
            context.session.session_id, int(source_uid),
            conn=context.db) or game_engine.EGemTypesNew.Unknown
    result = transform_instance(
        context, targets[0], new_template, keep_zone=True,
        copy_from_uid=int(copy_target), event_gems=event_gems)
    if isinstance(result, str):
        return result
    return f"transformed 1 stored card(s) -> {new_template[:8]}"


def transform_card_at_random(context):
    """Apply the authored Records filter to the transform candidate pool."""
    target = context.resolved_target()
    if target is None:
        return "transform random: no target"
    filter_spec = context.template_value("m_Filter")
    if not isinstance(filter_spec, dict):
        return "transform random: no typed filter"

    from pvp_db import (db_transform_target_info,
                        db_transform_candidate_templates,
                        db_ability_raw_json)
    from rules_port.filters import records_filter_matches

    def shards_from_threshold(value):
        try:
            data = json.loads(value or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        values = data if isinstance(data, list) else (
            data.get("list") or data.get("values") or [])
        flags = {0: 0, 1: 4, 2: 8, 3: 16, 4: 32, 5: 64}
        return [flags.get(int(item), 0) for item in values]

    target_row = db_transform_target_info(
        context.session.session_id, int(target), conn=context.db)
    if not target_row:
        return "transform random: target not found"

    def card_record(row, uid):
        data = {
            "card_uid": int(uid), "template_guid": row[0],
            "name": row[6] or "", "card_type": row[1] or "",
            "location": row[2] or "", "user_id": int(row[3] or 0),
            "state": int(row[4] or 0), "cost": int(row[7] or 0),
            "rarity": row[8] or "", "shards": shards_from_threshold(row[9]),
            "subtype": row[10] or "", "attributes": int(row[11] or 0) |
            int(row[12] or 0),
        }
        try:
            saved = json.loads(row[5] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            saved = {}
        data["counters"] = saved.get("counters") or {}
        data["counter_guids"] = saved.get("counter_guids") or {}
        data["tags"] = saved.get("tags") or {}
        return data

    source_card = card_record(target_row, target)
    # HasSourceCastingCostFilter compares against the card being transformed.
    try:
        raw = json.loads(db_ability_raw_json(
            context.bstate.get("resolving_ability", ""), conn=context.db) or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        raw = {}
    source_card["ability_variables"] = {
        str(value.get("m_Name")): int(value.get("m_DefaultValue", 0) or 0)
        for value in raw.get("m_Variables", [])
        if isinstance(value, dict) and value.get("m_Name")
    }
    source_card["cost_delta"] = 0
    add_value = filter_spec.get("m_AddValue")
    if isinstance(add_value, dict):
        variable = (add_value.get("m_InputVariableName") or
                    add_value.get("m_VariableName"))
        if variable:
            source_card["cost_delta"] = source_card["ability_variables"].get(
                variable, 0)

    target_types = {part.strip() for part in source_card["card_type"].split("|")
                    if part.strip()}
    rows = db_transform_candidate_templates(conn=context.db)
    candidates = []
    cant_same = bool(context.template_value("m_CantBeSameCard", False))
    owner = source_card.get("user_id", 0)
    # Apply the source card's category only for the authored
    # Artifact|Constant|Troop union used by same-cost permanent transforms.
    # Other effects can intentionally transform between card types (e.g.
    # Morphology turns a troop into a BasicAction or QuickAction).
    type_union = None
    if str(filter_spec.get("_t", "")).rsplit(".", 1)[-1] == "AndCardFilter":
        for child in filter_spec.get("m_TargetFilters", []):
            if str(child.get("_t", "")).rsplit(".", 1)[-1] != "OrCardFilter":
                continue
            categories = set()
            for branch in child.get("m_TargetFilters", []):
                branch_type = str(branch.get("_t", "")).rsplit(".", 1)[-1]
                if branch_type == "IsArtifact":
                    categories.add("Artifact")
                elif branch_type == "IsTroop":
                    categories.add("Troop")
                elif (branch_type == "IsType" and
                      str(branch.get("m_CardType") or "") == "Constant"):
                    categories.add("Constant")
            if categories == {"Artifact", "Constant", "Troop"}:
                type_union = categories
                break
    for row in rows:
        candidate = {
            "card_uid": 0, "template_guid": row[0], "name": row[1] or "",
            "card_type": row[2] or "", "cost": int(row[3] or 0),
            "rarity": row[4] or "", "shards": shards_from_threshold(row[5]),
            "subtype": row[6] or "", "attributes": int(row[7] or 0),
            "location": "", "user_id": owner,
        }
        if cant_same and row[0].lower() == source_card["template_guid"].lower():
            continue
        candidate_types = {part.strip() for part in candidate["card_type"].split("|")
                           if part.strip()}
        if type_union and target_types and not target_types.intersection(
                candidate_types):
            continue
        if records_filter_matches(candidate, filter_spec,
                                  source=source_card, context=context):
            candidates.append(row[0])
    if not candidates:
        return "transform random: no candidates"
    from .runtime_helpers import rng_choice
    new_template = rng_choice(context.bstate, candidates)
    # TransformCard mutates the current card in place. In particular,
    # "put into hand, then transform" must leave the generated action in hand.
    transform_instance(context, int(target), new_template, keep_zone=True)
    return f"transformed {hex(int(target))} -> random {new_template[:8]}"
