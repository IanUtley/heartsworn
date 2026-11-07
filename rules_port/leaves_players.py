"""Native player effect leaves.

Each function takes the EffectContext as ``ctx`` first. They were the methods of PlayerEffectsMixin (moved here so the native leaf table and the IR interpreter call them directly)."""

from __future__ import annotations

import json

import rules_port.leaves_abilities as _leaves_abilities


def draw(ctx, count: int, owner: int | None = None) -> str:
    """Draw cards through the existing PvE/PvP handler boundary."""
    count = max(0, int(count))
    target_owner = ctx.target_owner(default=None)
    if owner is None:
        owner = target_owner
    from rules_port.draw_effects import draw_cards
    return draw_cards(ctx, count, owner=owner)


def draw_effect(ctx) -> str:
    """Draw using the effect's typed count and resolved target owner."""
    count = ctx.value("m_InputValue", default=1)
    # Synthetic compatibility fixtures may provide only the compact
    # adapter payload. Live Records-backed effects always use the typed
    # field above.
    if ctx.template_value("m_InputValue") is None and ctx.param:
        try:
            payload = json.loads(ctx.param)
            if isinstance(payload, dict) and "count" in payload:
                count = int(payload["count"])
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return draw(ctx, 
        count, owner=ctx.target_owner(default=None))


def damage(ctx, target: int | None, amount: int) -> str:
    """Apply damage using the shared damage/death/replacement path."""
    if target is None:
        return "damage: no target"
    from rules_port.damage_effects import deal_damage
    return deal_damage(ctx, target, int(amount))


def lose_life(ctx, target: int | None, amount: int,
              metadata: dict | None = None) -> str:
    """Apply a typed LoseLifeModifier directly to champion health.

    Hex distinguishes life loss from damage. Life loss updates health
    directly and does not raise CardWouldBeDamagedEvent, which prevents a
    replacement ability from replacing its own life-loss consequence.
    """
    if target is None:
        return "lose life: no target"
    import game_engine
    from rules_port.runtime_helpers import owner_uid

    owner = ctx.target_owner(
        target, default=ctx.bstate.get("resolving_owner_id", 0))
    owner = int(owner or 0)
    if ctx.bstate.get("pvp"):
        health_key = (ctx.bstate.get("pvp_health_map") or {}).get(
            owner, f"hp_{owner}")
    else:
        health_key = "player_health" if owner else "ai_health"
    current = int(ctx.bstate.get(
        health_key, getattr(ctx.game, health_key, 20)) or 0)
    if metadata and metadata.get("lose_half_health"):
        amount = current // 2
    amount = max(0, int(amount or 0))
    if not amount:
        return "lose life: 0"
    try:
        from rules_port.static_rules import global_flags
        if "cant_lose_health" in global_flags(
                ctx.db, ctx.session.session_id, ctx.bstate):
            return "prevented: can't lose health"
    except Exception:
        pass
    new_value = max(0, current - amount)
    ctx.bstate[health_key] = new_value
    from .runtime_helpers import set_game_champion_health
    set_game_champion_health(
        ctx.game, ctx.bstate, owner, health_key, new_value)
    event = game_engine.ChampionHealthChangedSessionEventArgs()
    event.player_id = owner_uid(owner, ctx.player_uid, ctx.ai_uid,
                                ctx.bstate)
    event.old_damage_value = current
    event.new_damage_value = new_value
    ctx.game._push(event)
    return f"lose {amount} health -> {new_value}"


def gain_health(ctx, owner: int, amount: int) -> str:
    """Apply champion health gain through the shared heal operation.

    The client funnels every champion health gain — HealHeroModifier
    effects such as Dragon Guard Stalwart's charge power and Daybreak's
    start-of-turn constant, SpiritDrain lifelink and encounter modifiers —
    through ``Session.HealChampion``, which refuses the gain while the
    champion carries ``CantGainHealth`` (Emberspire Witch: "Champions
    can't gain health.").  Keep that invariant on the operation itself so
    every gain path observes the same authored constraint.
    """
    import game_engine
    from rules_port.runtime_helpers import owner_uid

    owner = int(owner or 0)
    if ctx.bstate.get("pvp"):
        # PvP resolution works on the FRA-shaped view, which names the
        # health keys through pvp_health_map.  A raw checkpoint without
        # the view keeps its hp_<pid> keys.
        health_key = (ctx.bstate.get("pvp_health_map") or {}).get(
            owner) or f"hp_{owner}"
    else:
        health_key = "player_health" if owner else "ai_health"
    side = "ai" if health_key == "ai_health" else "player"
    amount = max(0, int(amount or 0))
    try:
        from .static_rules import global_flags
        if "cant_gain_health" in global_flags(
                ctx.db, ctx.session.session_id, ctx.bstate):
            return "prevented: champions can't gain health"
    except Exception:
        pass
    current = int(ctx.bstate.get(
        health_key, getattr(ctx.game, health_key, 20)) or 0)
    # Champion health has no clamp in either direction: Eternal Youth,
    # Lifedrain, and the gain-health statics push current health above the
    # starting value, and a same-step lifelink must heal onto an already
    # negative total (C# sets ``CurrentDefenseValue - amount`` and lets the
    # state-based ``<= 0`` check decide afterwards).
    new_value = current + amount
    ctx.bstate[health_key] = new_value
    from .runtime_helpers import set_game_champion_health
    set_game_champion_health(
        ctx.game, ctx.bstate, owner, health_key, new_value)
    if new_value != current:
        event = game_engine.ChampionHealthChangedSessionEventArgs()
        event.player_id = owner_uid(owner, ctx.player_uid, ctx.ai_uid,
                                    ctx.bstate)
        event.old_damage_value = current
        event.new_damage_value = new_value
        ctx.game._push(event)
        from rules_port.statistics import add_card_stat
        gained = int(new_value) - int(current)
        champion_uid = ctx.champion_card_uid(owner)
        add_card_stat(ctx.bstate, champion_uid, owner,
                      "HealthGained", gained)
        add_card_stat(ctx.bstate, champion_uid, owner,
                      "TimesHealthGained", 1)
        emit_champion_healed(ctx, owner, current, new_value)
    return f"healed {side} {current}->{new_value}"


def set_hero_health(ctx, target: int | None, value: int) -> str:
    """Apply the typed SetHeroHealthModifier operation."""
    import game_engine
    from rules_port.runtime_helpers import owner_uid

    owner = ctx._effect_owner(target)
    _side, health_key = ctx._side_keys(owner)
    old_value = int(ctx.bstate.get(
        health_key, getattr(ctx.game, health_key, 20)) or 0)
    new_value = max(0, int(value or 0))
    ctx.bstate[health_key] = new_value
    from .runtime_helpers import set_game_champion_health
    set_game_champion_health(
        ctx.game, ctx.bstate, owner, health_key, new_value)
    if old_value == new_value:
        return f"set health {new_value} (unchanged)"
    event = game_engine.ChampionHealthChangedSessionEventArgs()
    event.player_id = owner_uid(owner, ctx.player_uid, ctx.ai_uid,
                                ctx.bstate)
    event.old_damage_value = old_value
    event.new_damage_value = new_value
    ctx.game._push(event)
    # C# Session.SetChampionHealth only publishes ChampionHealthChanged; ChampionHealedEvent is enqueued by
    # Session.HealChampion alone (after CantGainHealth and the life-gain modifiers).  A SetHeroHealthModifier that
    # raises a champion's health is therefore not a "gain health" event, so no trigger fires from here.
    return f"set health {old_value}->{new_value}"


def emit_champion_healed(ctx, owner: int, old_value: int,
                         new_value: int) -> None:
    """Dispatch the authored gain-health trigger after health changes."""
    champion_uid = ctx.champion_card_uid(owner)
    if champion_uid is None:
        return
    _leaves_abilities._emit_trigger(ctx, "ChampionHealedEvent", champion_uid, int(owner),
                       event_tac={"old_health": int(old_value),
                                  "new_health": int(new_value)})


def spell_points(ctx, target: int | None, amount: int) -> str:
    """Apply the typed SpellPointsModifier to the target controller."""
    import game_engine
    from rules_port.runtime_helpers import owner_uid

    owner = ctx._effect_owner(target)
    side, _health_key = ctx._side_keys(owner)
    key = f"{side}_spell_points"
    current = int(ctx.bstate.get(key, getattr(ctx.game, key, 0)) or 0)
    new_value = max(0, current + int(amount or 0))
    ctx.bstate[key] = new_value
    setattr(ctx.game, key, new_value)
    delta = new_value - current
    if delta:
        event = game_engine.ChampionSpellPointsChangedSessionEventArgs()
        event.player_id = owner_uid(owner, ctx.player_uid, ctx.ai_uid,
                                    ctx.bstate)
        event.operation = 1 if delta > 0 else 2
        event.delta = abs(delta)
        event.new_value = new_value
        ctx.game._push(event)
    return f"spell points {current}->{new_value}"


def lose_game(ctx) -> str:
    """Eliminate the responsible player through the session boundary."""
    player = int(ctx.bstate.get("resolving_responsible_player_id",
                                ctx.bstate.get("resolving_owner_id", 0)) or 0)
    for method_name in ("eliminate_player", "EliminatePlayer"):
        method = getattr(ctx.session, method_name, None)
        if callable(method):
            method(player, "ScriptedEvent")
            return f"eliminated player {player}"
    ctx.bstate.setdefault("eliminated_players", []).append(player)
    ctx.bstate["eliminated_players"] = list(dict.fromkeys(
        ctx.bstate["eliminated_players"]))
    return f"eliminated player {player}"


def player_attribute(ctx) -> str:
    """Apply typed EPlayerAttributes bits to the target controller."""
    from rules_port.attribute_effects import attribute_bits_from_flags
    flags = ctx.template_value("m_AttributeFlags", 0)
    bits = attribute_bits_from_flags(flags)
    if not bits:
        return "player attribute: none"
    target = ctx.resolved_target()
    owner = ctx.target_owner(target,
                              default=ctx.bstate.get("resolving_owner_id", 0))
    if owner is None:
        return "player attribute: no owner"
    key = str(int(owner))
    values = ctx.bstate.setdefault("player_attribute_flags", {})
    current = int(values.get(key, 0) or 0)
    operation = str(ctx.template_value("m_Operation", "Add")).rsplit(".", 1)[-1].lower()
    values[key] = (current | bits) if operation == "add" else (current & ~bits)
    return f"player attributes {key}={values[key]}"


def replenish_resources(ctx) -> str:
    """Refill one controller's current resource pool."""
    import game_engine
    from rules_port.runtime_helpers import owner_uid

    owner = int(ctx.bstate.get("resolving_owner_id", 0) or 0)
    if ctx.bstate.get("pvp"):
        current_key = f"res_{owner}"
        total_key = f"res_total_{owner}"
    else:
        side = "player" if owner else "ai"
        current_key = f"{side}_resources"
        total_key = f"{side}_total_resources"
    current = int(ctx.bstate.get(current_key, 0) or 0)
    total = int(ctx.bstate.get(total_key, current) or 0)
    delta = max(0, total - current)
    ctx.bstate[current_key] = total
    if owner:
        ctx.game.player_resources = total
    else:
        ctx.game.ai_resources = total
    if delta:
        event = game_engine.PlayerCurrentResourcePoolChangedSessionEventArgs()
        event.player_id = owner_uid(
            owner, ctx.player_uid, ctx.ai_uid, ctx.bstate)
        event.operation = 1
        event.delta = delta
        event.new_value = total
        ctx.game._push(event)
    return f"replenish resources {current}->{total}"


def swap_health(ctx) -> str:
    """Exchange the resolving controller's champion health values."""
    import game_engine

    from rules_port.runtime_helpers import owner_uid, pvp_opponent_pid

    owner = int(ctx.bstate.get("resolving_owner_id", 0) or 0)
    if ctx.bstate.get("pvp"):
        target = ctx.resolved_target()
        target_pid = pvp_opponent_pid(ctx.bstate, owner)
        if target is not None:
            for pid, cuid in (ctx.bstate.get("champ_map") or {}).items():
                if int(cuid) == int(target):
                    target_pid = int(pid)
                    break
        if target_pid is None:
            return "swap health: no opposing champion"
        health_map = ctx.bstate.get("pvp_health_map") or {}
        key_a = health_map.get(owner, f"hp_{owner}")
        key_b = health_map.get(target_pid, f"hp_{target_pid}")
        a = int(ctx.bstate.get(key_a, 20))
        b = int(ctx.bstate.get(key_b, 20))
        ctx.bstate[key_a], ctx.bstate[key_b] = b, a
        ctx.game.player_health = ctx.bstate[key_a]
        ctx.game.ai_health = ctx.bstate[key_b]
        for pid, old, new in ((owner, a, b), (target_pid, b, a)):
            event = game_engine.ChampionHealthChangedSessionEventArgs()
            event.player_id = owner_uid(
                pid, ctx.player_uid, ctx.ai_uid, ctx.bstate)
            event.old_damage_value = old
            event.new_damage_value = new
            ctx.game._push(event)
        return f"swapped health {owner}<->{target_pid}"

    profile = getattr(ctx.handler, "user_profile", None) or {}
    other = 0 if owner else int(profile.get("id", 0) or 0)
    key_a, key_b = (("player_health", "ai_health") if owner else
                    ("ai_health", "player_health"))
    a = int(ctx.bstate.get(key_a, getattr(ctx.game, key_a, 20)))
    b = int(ctx.bstate.get(key_b, getattr(ctx.game, key_b, 20)))
    ctx.bstate[key_a], ctx.bstate[key_b] = b, a
    setattr(ctx.game, key_a, b)
    setattr(ctx.game, key_b, a)
    for owner_id, old, new in ((owner, a, b), (other, b, a)):
        event = game_engine.ChampionHealthChangedSessionEventArgs()
        event.player_id = owner_uid(
            owner_id, ctx.player_uid, ctx.ai_uid, ctx.bstate)
        event.old_damage_value = old
        event.new_damage_value = new
        ctx.game._push(event)
    return "swapped champion health"
