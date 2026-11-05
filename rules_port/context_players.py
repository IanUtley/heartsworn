from __future__ import annotations

from .runtime_helpers import as_int
import json
from dataclasses import dataclass, replace
from typing import Any
from .damage_effects import assign_damage_shield_order, serialized_damage
from rules_port.card_view import CardView
import rules_port.context as _context
from rules_port.context_host import HostBase


class PlayerEffectsMixin(HostBase):
    """Methods of EffectContext grouped by players.

    Mixed into EffectContext; attribute access goes through self, so the
    class composition is unchanged.
    """

    def draw(self, count: int, owner: int | None = None) -> str:
        """Draw cards through the existing PvE/PvP handler boundary."""
        count = max(0, int(count))
        target_owner = self.target_owner(default=None)
        if owner is None:
            owner = target_owner
        from rules_port.draw_effects import draw_cards
        return draw_cards(self, count, owner=owner)

    def draw_effect(self) -> str:
        """Draw using the effect's typed count and resolved target owner."""
        count = self.value("m_InputValue", default=1)
        # Synthetic compatibility fixtures may provide only the compact
        # adapter payload. Live Records-backed effects always use the typed
        # field above.
        if self.template_value("m_InputValue") is None and self.param:
            try:
                payload = json.loads(self.param)
                if isinstance(payload, dict) and "count" in payload:
                    count = int(payload["count"])
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        return self.draw(
            count, owner=self.target_owner(default=None))

    def damage(self, target: int | None, amount: int) -> str:
        """Apply damage using the shared damage/death/replacement path."""
        if target is None:
            return "damage: no target"
        from rules_port.damage_effects import deal_damage
        return deal_damage(self, target, int(amount))

    def lose_life(self, target: int | None, amount: int,
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

        owner = self.target_owner(
            target, default=self.bstate.get("resolving_owner_id", 0))
        owner = int(owner or 0)
        if self.bstate.get("pvp"):
            health_key = (self.bstate.get("pvp_health_map") or {}).get(
                owner, f"hp_{owner}")
        else:
            health_key = "player_health" if owner else "ai_health"
        current = int(self.bstate.get(
            health_key, getattr(self.game, health_key, 20)) or 0)
        if metadata and metadata.get("lose_half_health"):
            amount = current // 2
        amount = max(0, int(amount or 0))
        if not amount:
            return "lose life: 0"
        try:
            from rules_port.static_rules import global_flags
            if "cant_lose_health" in global_flags(
                    self.db, self.session.session_id, self.bstate):
                return "prevented: can't lose health"
        except Exception:
            pass
        new_value = max(0, current - amount)
        self.bstate[health_key] = new_value
        from .runtime_helpers import set_game_champion_health
        set_game_champion_health(
            self.game, self.bstate, owner, health_key, new_value)
        event = game_engine.ChampionHealthChangedSessionEventArgs()
        event.player_id = owner_uid(owner, self.player_uid, self.ai_uid,
                                    self.bstate)
        event.old_damage_value = current
        event.new_damage_value = new_value
        self.game._push(event)
        return f"lose {amount} health -> {new_value}"

    def gain_health(self, owner: int, amount: int) -> str:
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
        if self.bstate.get("pvp"):
            # PvP resolution works on the FRA-shaped view, which names the
            # health keys through pvp_health_map.  A raw checkpoint without
            # the view keeps its hp_<pid> keys.
            health_key = (self.bstate.get("pvp_health_map") or {}).get(
                owner) or f"hp_{owner}"
        else:
            health_key = "player_health" if owner else "ai_health"
        side = "ai" if health_key == "ai_health" else "player"
        amount = max(0, int(amount or 0))
        try:
            from .static_rules import global_flags
            if "cant_gain_health" in global_flags(
                    self.db, self.session.session_id, self.bstate):
                return "prevented: champions can't gain health"
        except Exception:
            pass
        current = int(self.bstate.get(
            health_key, getattr(self.game, health_key, 20)) or 0)
        # Champion health has no clamp in either direction: Eternal Youth,
        # Lifedrain, and the gain-health statics push current health above the
        # starting value, and a same-step lifelink must heal onto an already
        # negative total (C# sets ``CurrentDefenseValue - amount`` and lets the
        # state-based ``<= 0`` check decide afterwards).
        new_value = current + amount
        self.bstate[health_key] = new_value
        from .runtime_helpers import set_game_champion_health
        set_game_champion_health(
            self.game, self.bstate, owner, health_key, new_value)
        if new_value != current:
            event = game_engine.ChampionHealthChangedSessionEventArgs()
            event.player_id = owner_uid(owner, self.player_uid, self.ai_uid,
                                        self.bstate)
            event.old_damage_value = current
            event.new_damage_value = new_value
            self.game._push(event)
            from rules_port.statistics import add_card_stat
            gained = int(new_value) - int(current)
            champion_uid = self.champion_card_uid(owner)
            add_card_stat(self.bstate, champion_uid, owner,
                          "HealthGained", gained)
            add_card_stat(self.bstate, champion_uid, owner,
                          "TimesHealthGained", 1)
            self.emit_champion_healed(owner, current, new_value)
        return f"healed {side} {current}->{new_value}"

    def set_hero_health(self, target: int | None, value: int) -> str:
        """Apply the typed SetHeroHealthModifier operation."""
        import game_engine
        from rules_port.runtime_helpers import owner_uid

        owner = self._effect_owner(target)
        _side, health_key = self._side_keys(owner)
        old_value = int(self.bstate.get(
            health_key, getattr(self.game, health_key, 20)) or 0)
        new_value = max(0, int(value or 0))
        self.bstate[health_key] = new_value
        from .runtime_helpers import set_game_champion_health
        set_game_champion_health(
            self.game, self.bstate, owner, health_key, new_value)
        if old_value == new_value:
            return f"set health {new_value} (unchanged)"
        event = game_engine.ChampionHealthChangedSessionEventArgs()
        event.player_id = owner_uid(owner, self.player_uid, self.ai_uid,
                                    self.bstate)
        event.old_damage_value = old_value
        event.new_damage_value = new_value
        self.game._push(event)
        # C# Session.SetChampionHealth only publishes ChampionHealthChanged; ChampionHealedEvent is enqueued by
        # Session.HealChampion alone (after CantGainHealth and the life-gain modifiers).  A SetHeroHealthModifier that
        # raises a champion's health is therefore not a "gain health" event, so no trigger fires from here.
        return f"set health {old_value}->{new_value}"

    def emit_champion_healed(self, owner: int, old_value: int,
                             new_value: int) -> None:
        """Dispatch the authored gain-health trigger after health changes."""
        champion_uid = self.champion_card_uid(owner)
        if champion_uid is None:
            return
        self._emit_trigger("ChampionHealedEvent", champion_uid, int(owner),
                           event_tac={"old_health": int(old_value),
                                      "new_health": int(new_value)})

    def spell_points(self, target: int | None, amount: int) -> str:
        """Apply the typed SpellPointsModifier to the target controller."""
        import game_engine
        from rules_port.runtime_helpers import owner_uid

        owner = self._effect_owner(target)
        side, _health_key = self._side_keys(owner)
        key = f"{side}_spell_points"
        current = int(self.bstate.get(key, getattr(self.game, key, 0)) or 0)
        new_value = max(0, current + int(amount or 0))
        self.bstate[key] = new_value
        setattr(self.game, key, new_value)
        delta = new_value - current
        if delta:
            event = game_engine.ChampionSpellPointsChangedSessionEventArgs()
            event.player_id = owner_uid(owner, self.player_uid, self.ai_uid,
                                        self.bstate)
            event.operation = 1 if delta > 0 else 2
            event.delta = abs(delta)
            event.new_value = new_value
            self.game._push(event)
        return f"spell points {current}->{new_value}"

    def lose_game(self) -> str:
        """Eliminate the responsible player through the session boundary."""
        player = int(self.bstate.get("resolving_responsible_player_id",
                                    self.bstate.get("resolving_owner_id", 0)) or 0)
        for method_name in ("eliminate_player", "EliminatePlayer"):
            method = getattr(self.session, method_name, None)
            if callable(method):
                method(player, "ScriptedEvent")
                return f"eliminated player {player}"
        self.bstate.setdefault("eliminated_players", []).append(player)
        self.bstate["eliminated_players"] = list(dict.fromkeys(
            self.bstate["eliminated_players"]))
        return f"eliminated player {player}"

    def player_attribute(self) -> str:
        """Apply typed EPlayerAttributes bits to the target controller."""
        from rules_port.attribute_effects import attribute_bits_from_flags
        flags = self.template_value("m_AttributeFlags", 0)
        bits = attribute_bits_from_flags(flags)
        if not bits:
            return "player attribute: none"
        target = self.resolved_target()
        owner = self.target_owner(target,
                                  default=self.bstate.get("resolving_owner_id", 0))
        if owner is None:
            return "player attribute: no owner"
        key = str(int(owner))
        values = self.bstate.setdefault("player_attribute_flags", {})
        current = int(values.get(key, 0) or 0)
        operation = str(self.template_value("m_Operation", "Add")).rsplit(".", 1)[-1].lower()
        values[key] = (current | bits) if operation == "add" else (current & ~bits)
        return f"player attributes {key}={values[key]}"

    def replenish_resources(self) -> str:
        """Refill one controller's current resource pool."""
        import game_engine
        from rules_port.runtime_helpers import owner_uid

        owner = int(self.bstate.get("resolving_owner_id", 0) or 0)
        if self.bstate.get("pvp"):
            current_key = f"res_{owner}"
            total_key = f"res_total_{owner}"
        else:
            side = "player" if owner else "ai"
            current_key = f"{side}_resources"
            total_key = f"{side}_total_resources"
        current = int(self.bstate.get(current_key, 0) or 0)
        total = int(self.bstate.get(total_key, current) or 0)
        delta = max(0, total - current)
        self.bstate[current_key] = total
        if owner:
            self.game.player_resources = total
        else:
            self.game.ai_resources = total
        if delta:
            event = game_engine.PlayerCurrentResourcePoolChangedSessionEventArgs()
            event.player_id = owner_uid(
                owner, self.player_uid, self.ai_uid, self.bstate)
            event.operation = 1
            event.delta = delta
            event.new_value = total
            self.game._push(event)
        return f"replenish resources {current}->{total}"

    def swap_health(self) -> str:
        """Exchange the resolving controller's champion health values."""
        import game_engine

        from rules_port.runtime_helpers import owner_uid, pvp_opponent_pid

        owner = int(self.bstate.get("resolving_owner_id", 0) or 0)
        if self.bstate.get("pvp"):
            target = self.resolved_target()
            target_pid = pvp_opponent_pid(self.bstate, owner)
            if target is not None:
                for pid, cuid in (self.bstate.get("champ_map") or {}).items():
                    if int(cuid) == int(target):
                        target_pid = int(pid)
                        break
            if target_pid is None:
                return "swap health: no opposing champion"
            health_map = self.bstate.get("pvp_health_map") or {}
            key_a = health_map.get(owner, f"hp_{owner}")
            key_b = health_map.get(target_pid, f"hp_{target_pid}")
            a = int(self.bstate.get(key_a, 20))
            b = int(self.bstate.get(key_b, 20))
            self.bstate[key_a], self.bstate[key_b] = b, a
            self.game.player_health = self.bstate[key_a]
            self.game.ai_health = self.bstate[key_b]
            for pid, old, new in ((owner, a, b), (target_pid, b, a)):
                event = game_engine.ChampionHealthChangedSessionEventArgs()
                event.player_id = owner_uid(
                    pid, self.player_uid, self.ai_uid, self.bstate)
                event.old_damage_value = old
                event.new_damage_value = new
                self.game._push(event)
            return f"swapped health {owner}<->{target_pid}"

        profile = getattr(self.handler, "user_profile", None) or {}
        other = 0 if owner else int(profile.get("id", 0) or 0)
        key_a, key_b = (("player_health", "ai_health") if owner else
                        ("ai_health", "player_health"))
        a = int(self.bstate.get(key_a, getattr(self.game, key_a, 20)))
        b = int(self.bstate.get(key_b, getattr(self.game, key_b, 20)))
        self.bstate[key_a], self.bstate[key_b] = b, a
        setattr(self.game, key_a, b)
        setattr(self.game, key_b, a)
        for owner_id, old, new in ((owner, a, b), (other, b, a)):
            event = game_engine.ChampionHealthChangedSessionEventArgs()
            event.player_id = owner_uid(
                owner_id, self.player_uid, self.ai_uid, self.bstate)
            event.old_damage_value = old
            event.new_damage_value = new
            self.game._push(event)
        return "swapped champion health"
