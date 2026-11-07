"""Native transform effect leaves.

Each function takes the EffectContext as ``ctx`` first. They were the methods of TransformEffectsMixin (moved here so the native leaf table and the IR interpreter call them directly)."""

from __future__ import annotations

import json


def create_matching_token(ctx) -> str:
    """Create copies of the resolved target using typed effect fields."""
    target = ctx.resolved_target()
    if target is None:
        return "matching token: no target"
    count = ctx.value("m_InputValue", default=1)
    collection = ctx.template_value("m_CardCollection", "Warzone")
    # The authored location decides where in the collection the copies
    # land.  Every matching-token effect currently authors Unknown, which
    # the client implements as a random slot (shuffled into the deck).
    deck_location = str(ctx.template_value(
        "m_CardLocation", "") or "").rsplit(".", 1)[-1].lower()
    from rules_port.token_effects import create_matching_target
    # C# CreateTokenMatchingTarget: a typed zero creates nothing.
    made = create_matching_target(
        ctx, int(target), int(count), collection, deck_location)
    return f"created {made} matching token(s)"


def transform_replica(ctx) -> str:
    """Transform a card into a replica while retaining its zone."""
    import game_engine

    target = ctx.resolved_target()
    if target is None:
        return "transform replica: no target"
    from pvp_db import db_card_zone_details, db_card_source_info
    details = db_card_zone_details(
        ctx.session.session_id, int(target), conn=ctx.db)
    if not details:
        return "transform replica: target not found"
    copy_from = int(target)
    serialized = ctx.template_value("m_SerializedTAC", None)
    if isinstance(serialized, dict):
        serialized = serialized.get("data", "")
    from rules_port.tac import tac_int
    if tac_int(serialized, "UseStoredTarget", 0):
        stored = ctx.bstate.get("stored_targets") or {}
        targets = (stored.get(ctx.ability_guid) or
                   stored.get(str(ctx.bstate.get(
                       "resolving_ability") or "")) or ())
        try:
            targets = [int(value) for value in targets]
        except (TypeError, ValueError):
            targets = []
        if len(targets) != 1:
            return "transform replica: expected one stored target"
        copy_from = targets[0]
        details = db_card_source_info(
            ctx.session.session_id, copy_from, conn=ctx.db)
        if not details or not details[0]:
            return "transform replica: stored copy source not found"
    from pvp_db import db_card_gem_type
    event_gems = db_card_gem_type(
        ctx.session.session_id, copy_from, conn=ctx.db)
    from rules_port.transform_effects import transform_instance
    transform_instance(
        ctx, int(target), details[0], keep_zone=True,
        copy_from_uid=copy_from, is_replica=True,
        event_gems=(event_gems if event_gems else
                    game_engine.EGemTypesNew.Unknown))
    from .replica import apply_replica_mods, project_replica
    apply_replica_mods(ctx, int(target), details[0])
    ctx.db.commit()
    project_replica(ctx, int(target))
    return f"replicated {hex(int(target))}"


def transform_self(ctx) -> str:
    """Port of ``TransformSelfAbilityEffectTemplate.Apply``.

    ``m_PlantGarden`` rolls the authored 60/30/10 Venus Fly
    Gorger/Spore Stalks/Vine Goliath replacement.  Otherwise the card
    becomes a copy of the resolved target (``TransformIntoCopy``), with
    ``m_IsReplica`` applying the replica modification.
    """
    import random
    import game_engine

    source = ctx.bstate.get("resolving_source_uid")
    if source is None:
        return "transform self: no source"
    from rules_port.transform_effects import transform_instance
    if ctx.template_value("m_PlantGarden", False):
        rng = ctx.bstate.get("_rules_rng")
        roll = (int(rng.next(100)) if rng is not None and
                hasattr(rng, "next") else random.randrange(100))
        name = ("Venus Fly Gorger" if roll < 60 else
                "Spore Stalks" if roll < 90 else "Vine Goliath")
        from pvp_db import db_gencard_template
        row = db_gencard_template(name, conn=ctx.db)
        if not row:
            return f"transform self: {name} template missing"
        transform_instance(ctx, int(source), str(row[0]).lower(),
                           keep_zone=True)
        return f"transformed self -> {name}"
    target = ctx.resolved_target()
    if target is None:
        return "transform self: no target"
    from pvp_db import db_card_source_info
    info = db_card_source_info(
        ctx.session.session_id, int(target), conn=ctx.db)
    if not info:
        return "transform self: target not found"
    template = str(info[0]).lower()
    is_replica = bool(ctx.template_value("m_IsReplica", False))
    from pvp_db import db_card_gem_type
    source_gems = db_card_gem_type(
        ctx.session.session_id, int(target), conn=ctx.db)
    transform_instance(
        ctx, int(source), template, keep_zone=True,
        copy_from_uid=int(target), is_replica=is_replica,
        event_gems=(source_gems if source_gems else
                    game_engine.EGemTypesNew.Unknown))
    if is_replica:
        from .replica import apply_replica_mods
        apply_replica_mods(ctx, int(source), template)
        ctx.db.commit()
    return f"transformed self into {template[:8]}"


def transform_card_random(ctx) -> str:
    """Run the authored random-filter transform through RulesPort."""
    from rules_port.transform_effects import transform_card_at_random
    return transform_card_at_random(ctx)


def _portal_template_for(ctx, target) -> str:
    """Return the Portal template matching one card's rarity.

    Port of ``TransformCardAbilityEffectTemplate.GetPortalForCard``.
    """
    from pvp_db import db_gencard_template, db_transform_target_info
    row = db_transform_target_info(
        ctx.session.session_id, int(target), conn=ctx.db)
    rarity = str(row[8] if row else "").strip().lower()
    name = {"uncommon": "Uncommon Portal", "rare": "Rare Portal",
            "legendary": "Legendary Portal"}.get(rarity, "Common Portal")
    portal = db_gencard_template(name, conn=ctx.db)
    return str(portal[0]).lower() if portal else ""


def transform_card(ctx) -> str:
    """Run the authored direct-template transform operation."""
    import game_engine

    if ctx.bstate.get("_skip_transform"):
        return "transform skipped (gate not met)"
    from rules_port.transform_effects import irreversible_original, transform_instance
    portal = bool(ctx.template_value("m_Portal", False))
    template = ctx.template_value("m_CardTemplateId", "")
    serialized = ctx.template_value("m_SerializedTAC", None)
    if isinstance(serialized, dict):
        serialized = serialized.get("data", "")
    from rules_port.tac import tac_int
    if tac_int(serialized, "UseStoredTarget", 0):
        # TransformCardAbilityEffectTemplate uses a zero
        # m_CardTemplateId for the authored "stored target becomes a
        # copy of [resolved target]" operation.  The destination is
        # the effect's resolved target; the source card(s) come from
        # the StoreTargets effect earlier in this ability instance.
        # Do not infer either side from localized game text.
        from rules_port.transform_effects import transform_into_stored_copy
        return transform_into_stored_copy(ctx, serialized)
    event_gems = game_engine.EGemTypesNew.Unknown
    source_uid = ctx.bstate.get("resolving_source_uid")
    if tac_int(serialized, "CopyGems", 0) and source_uid is not None:
        from pvp_db import db_card_gem_type
        event_gems = db_card_gem_type(
            ctx.session.session_id, int(source_uid),
            conn=ctx.db) or game_engine.EGemTypesNew.Unknown
    if not portal and (not template or
                       str(template).lower() == "0" * 36):
        raise RuntimeError(
            "RulesPort TransformCard effect is missing typed target template")
    pending = list(ctx.bstate.pop("pending_transform_cards", []) or [])
    if pending:
        for value in pending:
            target = value[0] if isinstance(value, (tuple, list)) else value
            new_template = (_portal_template_for(ctx, target)
                            if portal else str(template))
            if not new_template:
                continue
            transform_instance(ctx, int(target), new_template,
                               keep_zone=True, event_gems=event_gems,
                               original_template_guid=irreversible_original(
                                   serialized, template))
        return f"transform {len(pending)} card(s)"
    # The effect's own resolved target (e.g. a TriggerSource card) comes
    # before the ability source, which is only the fallback.
    target = (ctx.bstate.get("player_transform_target") or
              ctx.bstate.get("player_mod_target") or
              ctx.bstate.get("player_shift_source") or
              ctx.target(default=None) or
              ctx.bstate.get("resolving_source_uid"))
    if target is None:
        return "transform: no target"
    if portal:
        new_template = _portal_template_for(ctx, target)
        if not new_template:
            return "transform: no portal template"
    else:
        new_template = str(template)
    result = transform_instance(ctx, int(target), new_template,
                                keep_zone=True, event_gems=event_gems,
                                original_template_guid=irreversible_original(
                                    serialized, template))
    if isinstance(result, str):
        return result
    return f"transformed {hex(int(target))} -> {new_template[:8]}"


def create_token_copy(ctx) -> str:
    """Run the authored token-copy operation through RulesPort."""
    from rules_port.token_effects import create_token_copy
    return create_token_copy(ctx)


def transform_card_to_target(ctx) -> str:
    """Transform the source card into a copy of the resolved target."""
    source = ctx.bstate.get("resolving_source_uid")
    target = ctx.resolved_target()
    if source is None or target is None:
        return "transform to target: missing source/target"
    from pvp_db import db_card_zone_details
    details = db_card_zone_details(
        ctx.session.session_id, int(target), conn=ctx.db)
    if not details:
        return "transform to target: target not found"
    from rules_port.transform_effects import transform_instance
    # TransformIntoCopy changes the source's template in place; the C#
    # Session method preserves its collection and position.
    transform_instance(
        ctx, int(source), details[0], keep_zone=True,
        copy_from_uid=int(target), is_replica=False)
    return f"transformed {hex(int(source))} to target copy"


def summon_token(ctx) -> str:
    """Create tokens through the metadata token-operation helper."""
    from rules_port.token_effects import summon_token
    return summon_token(ctx)


def summon_x_tokens(ctx) -> str:
    """Port SummonXTokenTroops: base amount plus source counters."""
    from rules_port.token_effects import summon_token as native_summon_token
    count = int(ctx.template_value("m_BaseAmount", 0) or 0)
    source = ctx.bstate.get("resolving_source_uid")
    if source is not None:
        from pvp_db import db_card_mutation_field
        try:
            buffs = json.loads(db_card_mutation_field(
                ctx.session.session_id, int(source),
                "permanent_buffs", conn=ctx.db) or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            buffs = {}
        count += sum(int(value or 0) for value in
                     (buffs.get("counters", {}) if isinstance(buffs, dict) else {}).values())
    payload = {"amount": max(0, count),
               "token_guid": ctx.template_value("m_CardTemplateId", ""),
               "collection": ctx.template_value("m_CardCollection", "Warzone")}
    return native_summon_token(ctx, payload)


def conscript(ctx) -> str:
    """Conscript cards through the shared token/zone helper."""
    from rules_port.token_effects import summon_token
    try:
        amount = int(ctx.value("m_Amount", 1) or 0)
    except (TypeError, ValueError):
        amount = 1
    faction = ctx.template_value("m_Faction", "None")
    target = ctx.resolved_target(source_fallback=False)
    if (target is not None and
            str(faction).rsplit(".", 1)[-1].lower() == "underworld"):
        owner = int(ctx.target_owner(
            int(target), default=ctx.bstate.get(
                "resolving_owner_id", 0)) or 0)
        try:
            from rules_port.static_rules import player_int_attributes
            amount += int(player_int_attributes(
                ctx.db, ctx.session.session_id, ctx.bstate,
                owner).get("ConscriptUnderworldBonus", 0) or 0)
        except (ImportError, TypeError, ValueError):
            pass
    return summon_token(ctx, {
        "collection": "Hand", "amount": amount,
        "card_filter": ctx.template_value("m_CardFilter", {}),
        "random_with_replacement": True,
        "conscript_event": True,
        "conscript_faction": str(faction).rsplit(".", 1)[-1]})


def load_player_deck(ctx) -> str:
    """Load authored player-deck cards through the shared helper."""
    from rules_port.deck_effects import load_player_deck
    return load_player_deck(ctx)
