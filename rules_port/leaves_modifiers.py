"""Native modifier effect leaves.

Each function takes the EffectContext as ``ctx`` first. They were the methods of ModifierEffectsMixin (moved here so the native leaf table and the IR interpreter call them directly)."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from rules_port.damage_effects import assign_damage_shield_order
from rules_port.damage_effects import serialized_damage
from rules_port.runtime_helpers import as_int
import rules_port.leaves_abilities as _leaves_abilities
import rules_port.leaves_players as _leaves_players


def emit_gain_charge_triggers(ctx, amount, owner_id=None):
    """Emit one ``GainChargeEvent`` for every gained charge point.

    The client publishes one aggregate counter update, but queues one
    singular trigger event per point. Keeping that cardinality here lets
    every native charge modifier use the same metadata-driven path.
    """
    try:
        amount = max(0, int(amount or 0))
    except (TypeError, ValueError):
        amount = 0
    if not amount:
        return []
    if owner_id is None:
        owner_id = ctx.bstate.get("resolving_owner_id", 0)
    try:
        owner_id = int(owner_id or 0)
    except (TypeError, ValueError):
        owner_id = 0
    source_uid = ctx.champion_card_uid(owner_id)
    if source_uid is None:
        return []
    return [_leaves_abilities._emit_trigger(ctx,
        "GainChargeEvent", int(source_uid), owner_id)
        for _ in range(amount)]


def emit_int_attribute_gained(ctx, target, attribute, previous, current):
    """Publish C# ``Card.SendIntAttrEvents`` zero-to-positive edges."""
    if target is None or int(previous or 0) != 0 or int(current or 0) <= 0:
        return None
    owner = ctx.target_owner(
        int(target), default=ctx.bstate.get("resolving_owner_id", 0))
    return _leaves_abilities._emit_trigger(ctx,
        "CardGainedIntAttrEvent", int(target), int(owner or 0),
        event_int_attribute=str(attribute or ""))


def modifier_target(ctx, default: int | None = None):
    """Return the target selected for a typed card modifier.

    The resolver's current target is authoritative. The two older
    aliases are retained only for direct legacy callers and fixtures that
    invoke a leaf outside the normal resolution loop.
    """
    target = ctx.target(default=None)
    if target is not None:
        return target
    for key in ("player_mod_target", "player_spell_target"):
        value = ctx.bstate.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return default


def _push_champion_intattrs(ctx, owner, champion_uid, cleared=()):
    """Refresh champion context fields without a Champions zone move."""
    import game_engine

    attrs = dict((ctx.bstate.get("champion_int_attrs") or {}).get(
        str(int(champion_uid)), {}) or {})
    # Static intattrs (Armor from the champion's own ability) show on the
    # portrait too, beside the stored runtime values.
    from rules_port.static_rules import champion_static_intattrs
    for name, value in champion_static_intattrs(
            ctx.db, ctx.session.session_id, ctx.bstate, owner).items():
        attrs[name] = attrs.get(name, 0) + value
    scid = game_engine.SessionCardId(game_engine.UID(int(champion_uid)))
    card_defs = getattr(ctx.game, "card_defs", None)

    def _champion_def():
        # SessionCardId hashes by identity, so match the raw UID.
        if not isinstance(card_defs, dict):
            return None, scid
        for key, value in card_defs.items():
            try:
                if int(key.uid.uid64) == int(champion_uid):
                    return value, key
            except (AttributeError, TypeError, ValueError):
                continue
        return None, scid

    card_def, scid = _champion_def()
    if card_def is None and isinstance(card_defs, dict):
        # A fresh per-packet Game has no champion CardDef yet.  Rebuild
        # it (abilities, counters, spell-point mods, int attrs) before
        # the CardUpdated below, which replaces every cached field.
        warm = getattr(ctx.handler, "_push_champions_warm", None)
        if callable(warm) and not ctx.bstate.get("pvp"):
            try:
                warm(ctx.session, ctx.player_uid, ctx.ai_uid,
                     ctx.bstate, ctx.game)
            except Exception:  # pragma: no cover - projection only
                pass
            card_def, scid = _champion_def()
    if card_def is not None:
        current = dict(getattr(card_def, "int_attrs", {}) or {})
        for key, value in attrs.items():
            if value in (None, 0):
                current.pop(key, None)
            else:
                current[key] = int(value)
        # An attribute removed from the runtime map (Set to 0, Remove)
        # must leave the client cache as well.
        for key in cleared or ():
            if key not in attrs:
                current.pop(key, None)
        card_def.int_attrs = current
    from .runtime_helpers import owner_uid
    recipient = owner_uid(int(owner), ctx.player_uid, ctx.ai_uid,
                          ctx.bstate)
    if card_def is not None:
        # Unity's champion HUD (GoChampionEffects.SetEffectsFromChampionCard)
        # derives its IntAttr state icons (Toxified) from the cached
        # champion CardRepresentation, which only a CardUpdated stores;
        # PlayerUpdated never touches it.  Keep the collection the client
        # already has (None) so it is neither suppressed nor read as a
        # card move -- the same projection champion counters use.
        template = ctx._champion_template_guid(int(champion_uid))
        health = getattr(card_def, "defense", None)
        _side, health_key = ctx._side_keys(int(owner))
        try:
            health = as_int(ctx.bstate.get(
                health_key, getattr(ctx.game, health_key, health)))
        except (TypeError, ValueError):
            pass
        kwargs = {"int_attrs": card_def.int_attrs}
        if template:
            kwargs["template_id"] = template
        if health is not None:
            kwargs["defense"] = health
        ctx.game.push_card_updated(
            scid, recipient, game_engine.ECardCollections.None_,
            game_engine.ECardTypes.Champion, **kwargs)
    ctx.game.push_player_updated(recipient, champ_id=scid)


def _card_buffs(ctx, target: int, column: str = "permanent_buffs"):
    from pvp_db import db_card_mutation_field
    raw = db_card_mutation_field(
        ctx.session.session_id, int(target), column, conn=ctx.db)
    try:
        value = json.loads(raw or "{}") if raw else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _save_card_buffs(ctx, target: int, buffs: dict,
                     column: str = "permanent_buffs", *, commit=True):
    from pvp_db import db_set_card_mutation_field
    db_set_card_mutation_field(
        ctx.session.session_id, int(target), column,
        json.dumps(buffs), conn=ctx.db)
    if commit:
        ctx.db.commit()


def _card_thresholds(ctx, target: int) -> list[int]:
    buffs = _card_buffs(ctx, target)
    if isinstance(buffs.get("thresholds"), list):
        return [int(value) for value in buffs["thresholds"]]
    from pvp_db import db_card_template_threshold_subtype
    row = db_card_template_threshold_subtype(
        ctx.session.session_id, int(target), conn=ctx.db)
    from rules_port.targeting import shards_from_threshold
    return list(shards_from_threshold(row[0] if row else "{}"))


def _push_modifier_card(ctx, target: int, **kwargs):
    """Refresh the client representation after an instance modifier."""
    import game_engine
    from rules_port.runtime_helpers import card_collection_for_location, owner_uid

    from pvp_db import db_card_zone_details
    details = db_card_zone_details(
        ctx.session.session_id, int(target), conn=ctx.db)
    row = ((details[0], details[2], details[3]) if details else None)
    if not row:
        return
    scid = game_engine.SessionCardId(game_engine.UID(int(target)))
    tpl, ctype, _name, cost, attack, defense, gem = \
        ctx.handler._card_full_data(ctx.game, scid, row[0])
    ctx.game.push_card_updated(
        scid, owner_uid(row[1], ctx.player_uid, ctx.ai_uid, ctx.bstate),
        card_collection_for_location(row[2]), ctype, template_id=tpl,
        cost=cost, attack=attack, defense=defense, gems=gem,
        # Instance modifiers can target cards still in a deck (Prophecy
        # is one example).  Those updates refresh the modifier client-
        # side but must not disclose the card's representation.
        nulling=str(row[2] or "").lower() == "deck", **kwargs)


def card_threshold(ctx, target: int | None,
                   metadata: dict | None = None) -> str:
    """Apply CardThresholdModifier using the client's threshold rules."""
    if target is None:
        return "card threshold: no target"
    target = int(target)
    metadata = metadata or {}
    current = _card_thresholds(ctx, target)
    if metadata.get("copysourcecard"):
        source = ctx.bstate.get("resolving_source_uid")
        new_thresholds = _card_thresholds(ctx, int(source)) \
            if source is not None else current
    else:
        shard = ctx._shard_flag(metadata.get("shard"))
        if not shard:
            new_thresholds = []
        elif metadata.get("setthresholds"):
            new_thresholds = [shard]
        elif current:
            new_thresholds = [shard] * len(current)
        else:
            new_thresholds = current
    buffs = _card_buffs(ctx, target)
    buffs["thresholds"] = [int(value) for value in new_thresholds]
    _save_card_buffs(ctx, target, buffs)
    _push_modifier_card(ctx, 
        target, thresholds=[int(value) for value in new_thresholds])
    return f"thresholds {hex(target)} -> {new_thresholds}"


def subtype_modifier(ctx, target: int | None,
                     metadata: dict | None = None) -> str:
    """Apply SubTypeModifier and persist the current subtype string."""
    if target is None:
        return "subtype: no target"
    target = int(target)
    metadata = metadata or {}
    subtype = str(metadata.get("subtype") or "").strip()
    if not subtype:
        return "subtype: empty"
    buffs = _card_buffs(ctx, target)
    current = buffs.get("subtype")
    if current is None:
        from pvp_db import db_card_template_threshold_subtype
        row = db_card_template_threshold_subtype(
            ctx.session.session_id, target, conn=ctx.db)
        current = row[1] if row else ""
    values = [value for value in str(current or "").split() if value]
    operation = str(metadata.get("operation") or "Set").lower()
    if operation == "add" and subtype not in values:
        values.append(subtype)
    elif operation == "remove":
        values = [value for value in values
                  if value.lower() != subtype.lower()]
    else:
        values = [subtype]
    buffs["subtype"] = " ".join(values)
    _save_card_buffs(ctx, target, buffs)
    _push_modifier_card(ctx, target, sub_type=buffs["subtype"])
    return f"subtype {hex(target)} -> {buffs['subtype']}"


@serialized_damage


def damage_shield(ctx, target: int | None, amount: int,
                  metadata: dict | None = None) -> str:
    """Attach the client's one-shot/amount damage shield to a card."""
    if target is None:
        return "damage shield: no target"
    metadata = metadata or {}
    entry = {
        "amount": max(0, int(amount or 0)),
        "only_combat": bool(metadata.get("onlycombatdamage")),
        "one_shot": bool(metadata.get("oneshot")),
        "lasts_indefinitely": bool(metadata.get("lastsindefinitely")),
    }
    if metadata.get("onlypreventfromdamagedealer"):
        dealer = ctx.bstate.get("resolving_secondary_target_uid")
        if dealer is None and int(metadata.get(
                "damagedealeradditionaltarget", 0) or 0) == -2:
            dealer = ctx.bstate.get("resolving_source_uid")
        if dealer is not None:
            entry["dealer"] = int(dealer)
    target = int(target)
    column = ("permanent_buffs" if entry["lasts_indefinitely"]
              else "temporary_buffs")
    from pvp_db import db_card_mutation_field
    raw = db_card_mutation_field(
        ctx.session.session_id, target, column, conn=ctx.db)
    if raw is not None:
        columns = ("permanent_buffs", "temporary_buffs")
        buff_stores = [_card_buffs(ctx, target, name)
                       for name in columns]
        changed = assign_damage_shield_order(buff_stores, entry)
        destination = columns.index(column)
        buff_stores[destination].setdefault(
            "damage_shields", []).append(entry)
        changed.add(destination)
        for index in sorted(changed):
            _save_card_buffs(ctx, 
                target, buff_stores[index], columns[index], commit=False)
        ctx.db.commit()
        _push_modifier_card(ctx, target, damage_shield=True)
    else:
        shields = ctx.bstate.setdefault("damage_shields", {})
        target_shields = shields.setdefault(str(target), [])
        assign_damage_shield_order(
            [{"damage_shields": target_shields}], entry)
        target_shields.append(entry)
    return f"damage shield {hex(target)} +{entry['amount']}"


def rule_modifier(ctx, target: int | None, param: dict,
                  metadata: dict | None = None) -> str:
    """Persist typed damage/targeting/block rules on a card instance."""
    if target is None:
        return "rule modifier: no target"
    metadata = metadata or {}
    prop = str(metadata.get("property") or param.get("property") or "")
    duration = str(param.get("duration") or "")
    column = ("temporary_buffs" if duration in (
        "EndOfTurn", "AfterCardsReadyOnPlayersTurn") else "permanent_buffs")
    buffs = _card_buffs(ctx, int(target), column)
    rules = buffs.setdefault("rule_modifiers", [])
    rule: dict[str, Any] = {"property": prop}
    if metadata.get("cardfilter") is not None:
        rule["filter"] = metadata["cardfilter"]
    for key in ("iscombatdamage", "combatdamageonly",
                "noncombatdamageonly", "replaceexistingvalue"):
        if key in metadata:
            rule[key] = bool(metadata[key])
    if prop == "damagemultiplier":
        rule["value"] = max(0, int(modifier_value(ctx, 
            param, metadata, "damagemultiplier") or 0))
    if duration == "BeginningOfOwnersTurn":
        # Lasts until the start of the resolving player's next turn: tagged with that owner, and the card is listed
        # so the boundary pass (effect_lifetimes.expire_owner_turn_rules) removes the rule then.
        owner = int(ctx.bstate.get("resolving_owner_id", 0) or 0)
        rule["expires_for_owner"] = owner
        listed = ctx.bstate.setdefault("owner_turn_rule_cards", [])
        if int(target) not in listed:
            listed.append(int(target))
    rules.append(rule)
    _save_card_buffs(ctx, int(target), buffs, column)
    _push_modifier_card(ctx, int(target))
    return f"rule {prop} on {hex(int(target))}"


def modifier_value(ctx, param: dict | None, metadata: dict | None,
                   property_name: str) -> int:
    """Resolve a typed CardModifier operand from Records and live state."""
    payload = dict(param or {})
    metadata = metadata or {}
    if metadata.get("input_variable"):
        payload.setdefault("input_variable", metadata["input_variable"])
    if not payload.get("input_variable"):
        # Typed CardModifier operands live on different fields per class:
        # Charge/Resource/Damage styles use m_InputValue (``input_value``),
        # IntAttr/Attack-less styles use m_Value (``value``), and
        # LoseLife/Attribute literals can use either.  The adapter payload
        # always carries the ``amount: 0`` placeholder, so promote the
        # authored literal here or the native leaf sees only zero and
        # silently no-ops ("Lose 3 health", "Set Lethal 1").
        for source in (metadata, payload):
            if payload.get("amount"):
                break
            literal = source.get("input_value")
            if not literal:
                literal = source.get("value")
            if literal:
                payload["amount"] = literal
                break
    # Native effects must resolve operands through the RulesPort
    # variable evaluator.  Calling the historical leaf evaluator here
    # would make the effect state hybrid even when its outer resolver
    # is native.
    from rules_port.static_rules import _native_leaf_value
    raw = _leaves_abilities._resolving_raw_json(ctx)
    if not raw:
        from pvp_db import db_ability_raw_json
        raw = db_ability_raw_json(ctx.ability_guid, conn=ctx.db) or "{}"
    value = _native_leaf_value(
        ctx.db, ctx.session.session_id, ctx.bstate,
        int(ctx.bstate.get("resolving_source_uid") or 0),
        int(ctx.bstate.get("resolving_owner_id", 0) or 0),
        payload, raw,
        allow_life_loss_modifier=(property_name.lower() == "loselife"))
    if value is None or int(value[1] or 0) == 0:
        # AbilityConstants are immutable literals on the typed
        # ability graph. Some native effect paths have an empty or
        # incomplete raw-JSON view even though the graph still carries
        # the constant (for example ChargePointsModifier's input
        # variable ``A`` on Inductocopter Bot). Keep the graph as the
        # authority for that operand so a failed raw lookup does not
        # turn an authored +1 into a zero-delta resource event.
        graph = _leaves_abilities._ability_graph(ctx)
        wanted = str(payload.get("input_variable") or "")
        for variable in getattr(graph, "variables", ()) or ():
            to_dict = getattr(variable, "to_dict", None)
            record = to_dict() if callable(to_dict) else {}
            if not isinstance(record, dict):
                continue
            if (str(record.get("m_Name") or "") != wanted or
                    str(record.get("_t") or "").rsplit(".", 1)[-1]
                    != "AbilityConstant"):
                continue
            try:
                constant = int(record.get("m_DefaultValue", 0) or 0)
                multiplier = int(payload.get("amount") or 0)
            except (TypeError, ValueError):
                break
            value = ("", constant * multiplier if multiplier
                     else constant)
            break
    if value is None:
        return 0
    return int(value[1] or 0)


def damage_modifier(ctx, param: dict | None,
                    metadata: dict | None = None) -> str:
    """Resolve and apply a metadata-defined damage modifier.

    X-cost is activation state; ESC reads the source card's persistent
    ``EscalationCount``. Keeping those inputs here lets the leaf stay a
    one-line adapter without treating either value as printed damage.
    """
    import re

    param = dict(param or {})
    text = str(param.get("text") or "")
    target = modifier_target(ctx)
    if target is None:
        from rules_port.targeting import implicit_champion_target
        target = implicit_champion_target(
            ctx.db, ctx.session, ctx.handler, ctx.bstate)
    if target is None:
        from rules_port.targeting import implicit_champion_target
        target = implicit_champion_target(
            ctx.db, ctx.session, ctx.handler, ctx.bstate,
            opposing=True)
    if target is None:
        return "damage: no target"

    lower = text.lower()
    esc = re.search(r"esc:(\d+)", lower)
    if esc:
        base = int(esc.group(1))
        from rules_port.statistics import card_escalation_count
        source_uid = ctx.bstate.get("resolving_source_uid")
        amount = base * card_escalation_count(
            ctx.db, ctx.session.session_id, ctx.bstate, source_uid)
    elif "x damage" in lower:
        amount = int(ctx.bstate.get("x_cost", 0) or 0)
    else:
        amount = modifier_value(ctx, param, metadata, "damage")
    # A typed amount that evaluates to 0 is the card's answer (for example, 1 damage for each troop in
    # the crypt with none there). The printed "deal N damage" fallback is only for an untyped amount.
    typed = bool((metadata or {}).get("input_variable") or param.get("input_variable"))
    if amount <= 0 and not typed:
        match = re.search(r"deal\s+(\d+)\s+damage", lower)
        if match:
            amount = int(match.group(1))
    if amount <= 0:
        return "damage: amount 0"
    return _leaves_players.damage(ctx, int(target), amount)


def stat_modifier(ctx, param: dict | None,
                  metadata: dict | None = None) -> str:
    """Resolve and apply a typed attack or defense modifier."""
    import re

    param = dict(param or {})
    metadata = metadata or {}
    property_name = str(
        metadata.get("property") or param.get("property") or "")
    target = modifier_target(ctx)
    amount = modifier_value(ctx, param, metadata, property_name)
    attack = amount if property_name == "attack" else 0
    defense = amount if property_name == "defense" else 0
    text = str(param.get("text") or "").lower()

    # These are typed stat effects whose operand is derived from a prior
    # card, not a static EffectInputVariable. Keep the derivation here so
    # every stat leaf still shares the same persistence/event operation.
    if attack == 0 and defense == 0 and \
            "equal to this troop's [def]" in text:
        source_uid = ctx.bstate.get("resolving_source_uid")
        if source_uid is not None:
            from pvp_db import db_card_effective_defense
            row = db_card_effective_defense(
                ctx.session.session_id, int(source_uid), conn=ctx.db)
            if row:
                value = int(row[0] or 0) + int(row[1] or 0)
                if property_name == "attack":
                    attack = value
                else:
                    defense = value
    if attack == 0 and defense == 0 and "voided troop's" in text:
        stats = ctx.bstate.get("champion_voided_stats") or {}
        match = re.search(r"plus\s+(\d+)", text)
        value = int(stats.get(
            "atk" if property_name == "attack" else "def", 0) or 0)
        value += int(match.group(1)) if match else 0
        if property_name == "attack":
            attack = value
        else:
            defense = value
        created = ctx.bstate.get("created_token_uids") or []
        if created:
            target = int(created[0])
            ctx.bstate["player_mod_target"] = target

    stat_property = str(metadata.get("stat_property") or param.get("stat_property") or "")
    replace = bool(metadata.get("replaceexistingvalue") or param.get("replaceexistingvalue"))
    if target is not None and (stat_property in ("CurrentAttackValue", "CurrentDefenseValue") or replace):
        attack, defense = _stat_modifier_per_target(ctx, 
            int(target), property_name, stat_property, replace, amount)
    if target is None:
        return f"stat mod: {property_name} {amount:+}"
    duration = str(param.get("duration") or "")
    if duration in ("BeginningOfOwnersTurn",
                    "AfterCardsReadyOnPlayersTurn"):
        # "Until the start of your next turn" must survive the end-turn
        # wipe of temporary_buffs (Fiona Honeyfinch's -1 ATK matters on
        # the opponent's turn).  Apply it as a reversible grant that the
        # owner's start_turn/prep boundary removes.  BeginningOfOwnersTurn
        # belongs to the ability's responsible player; the ready-step
        # duration to the affected card's controller (as for attributes).
        from .effect_lifetimes import record_temporary_stat
        resolving_owner = int(
            ctx.bstate.get("resolving_owner_id", 0) or 0)
        expiration_owner = (
            resolving_owner if duration == "BeginningOfOwnersTurn"
            else int(ctx.target_owner(target, default=resolving_owner)
                     or 0))
        stat_mod(ctx, target, attack, defense, this_turn=False)
        record_temporary_stat(
            ctx.bstate, int(target), attack, defense,
            duration=duration, owner_id=expiration_owner,
            source_uid=ctx.bstate.get("resolving_source_uid"))
        return f"mod {hex(int(target))} {attack:+}/{defense:+} ({duration})"
    this_turn = duration == "EndOfTurn"
    stat_mod(ctx, target, attack, defense, this_turn=this_turn)
    return f"mod {hex(int(target))} {attack:+}/{defense:+}"


def _stat_modifier_per_target(ctx, target: int, property_name: str, stat_property: str,
                              replace: bool, amount: int) -> tuple[int, int]:
    """The buff one attack or defense modifier gives one target (C# AttackModifier.Apply). A named property is the
    target's own current value of it, in place of the amount. A replacing modifier sets the value, so its buff is
    the difference from the value now."""
    from rules_port.static_rules import effective_stats
    now_attack, now_defense = effective_stats(ctx.db, ctx.session.session_id, ctx.bstate, int(target))[:2]
    now = int(now_attack if property_name == "attack" else now_defense)
    value = now if stat_property in ("CurrentAttackValue", "CurrentDefenseValue") else int(amount)
    if replace:
        value -= now
    return (value, 0) if property_name == "attack" else (0, value)


def stat_mod(ctx, target: int, attack: int = 0, defense: int = 0,
             this_turn: bool = False):
    """Apply a card stat modifier through the shared event path."""
    from rules_port.stat_effects import apply_stat_mod
    return apply_stat_mod(
        ctx, int(target), int(attack), int(defense),
        this_turn=this_turn)


def card_modifier(ctx) -> str:
    """Run the metadata CardModifier operation behind the context ABI.

    CardModifier is a family of typed operations (health, resources,
    attributes, counters, cost changes, and stat/damage modifiers), not a
    single primitive. Keep its compatibility-heavy orchestration in one
    implementation while exposing only the context-shaped leaf boundary.
    """
    from rules_port.ir_modifiers import _card_modifier
    return _card_modifier(ctx, {"param": ctx.param})


def lose_thresholds(ctx, names) -> str:
    """Remove authored shard thresholds from the target controller."""
    import game_engine
    from rules_port.runtime_helpers import owner_uid

    target = ctx.target(default=(ctx.bstate.get("player_mod_target")
                                  or ctx.bstate.get("resolving_source_uid")))
    owner = ctx.target_owner(
        target, default=ctx.bstate.get("resolving_owner_id", 0))
    colors = {"Colorless": 1, "Blood": 4, "Ruby": 8,
              "Sapphire": 16, "Wild": 32, "Diamond": 64}
    changed = 0
    for name in names or ():
        color = colors.get(str(name).split(".")[-1], 0)
        if not color:
            continue
        key = (f"thresh_{owner}" if ctx.bstate.get("pvp") else
               ("player_threshold" if owner else "ai_threshold"))
        thresholds = ctx.bstate.setdefault(key, {})
        old = int(thresholds.get(color, thresholds.get(str(color), 0)) or 0)
        if old <= 0:
            continue
        thresholds[color] = 0
        thresholds.pop(str(color), None)
        changed += old
        event = game_engine.PlayerResourceThresholdChangedSessionEventArgs()
        event.player_id = owner_uid(
            owner, ctx.player_uid, ctx.ai_uid, ctx.bstate)
        event.color = color
        event.operation = 2
        event.delta = old
        event.new_value = 0
        ctx.game._push(event)
    return f"lost {changed} threshold(s)"
