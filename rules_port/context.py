"""Context and small operations shared by metadata effect executors.

The resolver still accepts the historical leaf ABI internally.  New leaves can
use :class:`EffectContext` instead, which keeps the protocol/session plumbing,
typed fields, target ownership, and common operations in one place.
"""

from __future__ import annotations

from .runtime_helpers import as_int

import json

from dataclasses import dataclass, replace
from typing import Any
from .damage_effects import assign_damage_shield_order, serialized_damage
from rules_port.card_view import CardView
from rules_port.context_zones import ZoneEffectsMixin
from rules_port.context_transforms import TransformEffectsMixin
from rules_port.context_modifiers import ModifierEffectsMixin
from rules_port.context_variables import VariableEffectsMixin
from rules_port.context_players import PlayerEffectsMixin
from rules_port.context_abilities import AbilityEffectsMixin


def _opposing_owner_in(state, owner, handler):
    """The other participant owner for PvP checkpoints or Practice sides."""
    owner = int(owner or 0)
    if state.get("pvp"):
        pids = [int(pid) for pid in (state.get("pids") or ())]
        for pid in pids:
            if pid != owner:
                return pid
        for participant in (state.get("champ_map") or {}):
            try:
                pid = int(participant)
            except (TypeError, ValueError):
                continue
            if pid != owner:
                return pid
        return owner
    if owner == 0:
        profile = getattr(handler, "user_profile", None) or {}
        return int(profile.get("id", 0) or 0)
    return 0


@dataclass
class EffectContext(ZoneEffectsMixin, TransformEffectsMixin, ModifierEffectsMixin, VariableEffectsMixin, PlayerEffectsMixin, AbilityEffectsMixin):
    """Execution context for one resolved effect target.

    ``bstate`` remains the authoritative short-lived resolver state.  This
    class is deliberately an adapter over it, not a replacement state store.
    That lets existing leaves and context-style leaves execute in the same
    resolver during the migration.
    """

    game: Any
    session: Any
    db: Any
    handler: Any
    player_uid: Any
    ai_uid: Any
    bstate: dict[str, Any]
    effect_guid: str
    param: str = ""
    # This is deliberately an execution-only dependency.  ``bstate`` may be
    # persisted by an effect (draw is one such path), so placing the builder
    # there makes a normal resolution non-serializable.
    runtime_ability: Any = None
    effect_template_override: Any = None
    effect_duration_override: str | None = None
    effect_targets: tuple = ()
    nested_effect_dispatch: Any = None

    @classmethod
    def from_rules_port(cls, game, session, db, handler, pl_t, ai_t,
                        bstate, effect_guid, param="", *, ability=None,
                        effect_targets=(), nested_effect_dispatch=None):
        """Construct the shared mutation context for native RulesPort work.

        The context is a host/projection seam, not a second rules resolver.
        Keeping a distinct constructor prevents native code from advertising
        that it is entering through the historical leaf ABI.
        """
        return cls(
            game=game, session=session, db=db, handler=handler,
            player_uid=pl_t, ai_uid=ai_t,
            bstate=bstate if bstate is not None else {},
            effect_guid=effect_guid, param=param or "", runtime_ability=ability,
            effect_targets=tuple(effect_targets or ()),
            nested_effect_dispatch=nested_effect_dispatch)

    @property
    def ability_guid(self) -> str:
        return str(self.bstate.get("resolving_ability") or "").lower()

    @property
    def ability(self):
        """Return the active metadata-backed builder, when resolving.

        The builder is intentionally carried by this transient context rather
        than ``bstate``.  Resolver state crosses persistence boundaries while
        an effect is running and must consequently remain JSON serializable.
        """
        return self.runtime_ability

    @property
    def effect_duration(self) -> str:
        """Duration authored on this effect's mapping, not its leaf template."""
        if self.effect_duration_override is not None:
            return str(self.effect_duration_override)
        from .effect_lifetimes import mapping_duration
        return mapping_duration(self)

    def target(self, name: str = "primary", default: int | None = None):
        """Return the current typed target without inventing one.

        The resolver invokes a leaf once per target and records the target in
        ``resolving_target_uid``.  Falling back to the source here would make
        an explicitly empty target accidentally affect the source card.
        """
        key = str(name or "primary").lower()
        if key in ("source", "self"):
            value = self.bstate.get("resolving_source_uid")
        elif key in ("secondary", "secondary_target"):
            value = self.bstate.get("resolving_secondary_target_uid")
        else:
            value = self.bstate.get("resolving_target_uid")
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def resolved_target(self, default: int | None = None, *,
                        source_fallback: bool = True):
        """Return the legacy BOM target precedence through one typed helper.

        Ordinary resolver leaves use ``resolving_target_uid``.  Older spell,
        modifier, and trigger paths still populate the two named aliases, and
        source-target effects intentionally fall back to the source card.  A
        simple leaf should not have to know those storage details.  A leaf
        whose target is a mandatory, player-chosen card passes
        ``source_fallback=False``: the ability's own source is then not a
        target, and the leaf can tell "nothing resolved yet" (ask the player)
        apart from "the source is the target".
        """
        keys: tuple[str, ...] = ("player_spell_target", "player_mod_target",
                                 "resolving_target_uid")
        if source_fallback:
            keys += ("resolving_source_uid",)
        for key in keys:
            value = self.bstate.get(key)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return default


    def value(self, field_name: str, default: int = 0) -> int:
        """Evaluate a typed effect field against the active ability state."""
        if self.effect_template_override is not None:
            from rules_port.fields import _as_dict, resolve_field
            from rules_port.fields import ability_variables
            template = _as_dict(self.effect_template_override)
            variables = ability_variables(self.ability)
            variables.update(self.bstate.get("ability_variables") or {})
            return resolve_field(
                template.get(field_name), variables,
                self.bstate.get("effect_outputs") or self.bstate,
                self.bstate, default)
        if self.ability is not None:
            return self.ability.value(
                self.db, self.bstate, field_name,
                effect=self.effect_guid, default=default)
        from rules_port.fields import effect_field
        return effect_field(
            self.ability, self.db, self.bstate, self.effect_guid,
            field_name, default)

    def template_value(self, field_name: str, default: Any = None) -> Any:
        """Read a non-numeric typed field from the effect template."""
        if self.effect_template_override is not None:
            from rules_port.fields import _as_dict
            value = _as_dict(self.effect_template_override).get(
                field_name, default)
            if isinstance(value, dict) and "m_Guid" in value:
                return str(value.get("m_Guid") or "").lower()
            return value
        if self.ability is not None:
            return self.ability.template_value(
                self.db, self.bstate, field_name,
                effect=self.effect_guid, default=default)
        from rules_port.fields import effect_template_value
        return effect_template_value(
            self.ability, self.effect_guid, field_name, default)

    def apply_repeating_effect(self, template, loop_count: int) -> str:
        """Apply a child template over the parent's target instance."""
        if not callable(self.nested_effect_dispatch):
            return "repeat: nested effect dispatcher unavailable"
        result = self.nested_effect_dispatch(
            self, template, int(loop_count))
        return str(result or "")

    def nested_effect_context(self, template, param, targets):
        """Return a lightweight child context with the parent's duration."""
        return replace(
            self, param=param or "", effect_template_override=template,
            effect_duration_override=self.effect_duration,
            effect_targets=tuple(targets or ()))


    def target_owner(self, target: int | None = None,
                     default: int | None = None) -> int | None:
        """Resolve a card/champion target to its controlling player."""
        target = self.target() if target is None else target
        if target is None:
            return default
        # PlayerTargetTemplate resolves to a typed player UID in the native
        # resolver, not to a game_cards card UID. Map both wire namespaces
        # before attempting the card/champion lookup so effects such as
        # ``put into your deck`` use the activating player's deck.
        try:
            target_id = int(getattr(target, "uid64", target))
            profile = getattr(self.handler, "user_profile", None)
            player_owner = (int(profile.get("id", 0))
                            if isinstance(profile, dict) else 0)
            for participant, owner in ((self.player_uid, player_owner),
                                       (self.ai_uid, 0)):
                if int(getattr(participant, "uid64", participant)) == target_id:
                    return owner
            if target_id == player_owner:
                return player_owner
            if target_id == 0:
                return 0
        except (TypeError, ValueError):
            pass
        from pvp_db import db_card_owner_id
        card_owner = db_card_owner_id(
            self.session.session_id, int(target), conn=self.db)
        if card_owner is not None:
            return int(card_owner)
        for pid, champion_uid in (self.bstate.get("champ_map") or {}).items():
            try:
                if int(champion_uid) == int(target):
                    return int(pid)
            except (TypeError, ValueError):
                continue
        profile = getattr(self.handler, "user_profile", None)
        player_owner = profile.get("id", 0) if isinstance(profile, dict) else 0
        for attr, owner in (("_player_champ_scid", player_owner),
                            ("_ai_champ_scid", 0)):
            champion = getattr(self.handler, attr, None)
            if champion is None:
                continue
            try:
                if int(champion.uid.uid64) == int(target):
                    return int(owner)
            except (AttributeError, TypeError, ValueError):
                continue
        return default

    def active_talent_guids(self):
        """Return the selected talents for the side resolving this effect."""
        owner = int(self.bstate.get("resolving_owner_id", 0) or 0)
        profile = getattr(self.handler, "user_profile", None)
        player = int((profile.get("id", 0) if isinstance(profile, dict)
                      else getattr(profile, "id", 0)) or 0)
        if owner and owner == player:
            return tuple(getattr(self.handler, "_player_talent_guids", ()))
        return tuple(getattr(self.handler, "_ai_talent_guids", ()))


    def conversation(self) -> str:
        """Queue the typed encounter conversation and pause this BOM.

        ConversationAbilityEffectTemplate is a protocol boundary: the
        original client opens a UI animation and sends a transaction before
        the remaining effects may resolve.  The battle handler owns the
        persisted continuation and wire event; this context only supplies the
        typed conversation ID and current ability state.
        """
        conversation_id = self.template_value("m_ConversationId", "")
        if isinstance(conversation_id, dict):
            conversation_id = conversation_id.get("m_Guid", "")
        conversation_id = str(conversation_id or "").lower()
        if not conversation_id or conversation_id == "0" * 8 + "-" + "0" * 4 + "-" + "0" * 4 + "-" + "0" * 4 + "-" + "0" * 12:
            return "conversation: invalid id"
        queue = getattr(self.handler, "_queue_conversation_prompt", None)
        if callable(queue):
            result = queue(
                self.game, self.session, self.player_uid, self.ai_uid,
                self.bstate, conversation_id)
            return str(result or "")
        # Headless adapters may not expose a wire handler. Preserve the same
        # continuation marker so tests can assert that the effect pauses
        # instead of silently succeeding.
        pending = self.continuation()
        pending.update({"kind": "conversation", "conversation_id": conversation_id})
        if self.bstate.get("_interpreter_continuation"):
            pending["interpreter"] = self.bstate["_interpreter_continuation"]
        self.bstate["pending_conversation"] = pending
        self.bstate["resolution_paused"] = True
        return f"conversation: awaiting {conversation_id}"

    def continuation(self, *, resume_effect_order: int | None = None,
                     **overrides) -> dict[str, Any]:
        """Return a JSON-safe snapshot for an authored pause/resume point."""
        from rules_port.abilities import AbilityContinuation

        if resume_effect_order is None:
            resume_effect_order = int(
                self.bstate.get("resolving_effect_order", 0) or 0) + 1
        if self.ability is not None:
            value = self.ability.continuation(
                resume_effect_order=resume_effect_order,
                source_uid=overrides.pop(
                    "source_uid", self.bstate.get("resolving_source_uid")),
                owner_id=overrides.pop(
                    "owner_id", self.bstate.get("resolving_owner_id", 0)),
                target_map=overrides.pop(
                    "target_map", self.bstate.get("ability_target_map") or {}),
                variables=overrides.pop(
                    "variables", self.bstate.get("ability_variables") or {}))
            # The session AbilityInstance stores its activation under an
            # ``activation`` object.  Effect continuations are consumed by
            # the host's generic target-map/variables boundary, so flatten
            # that wrapper here while keeping the persisted shape typed.
            activation = value.pop("activation", None)
            if isinstance(activation, dict):
                value["target_map"] = dict(
                    activation.get("target_map") or {})
                value["variables"] = dict(
                    activation.get("variables") or
                    value.get("variables") or {})
        else:
            value = AbilityContinuation.from_state(
                self.bstate, resume_effect_order=resume_effect_order,
                **{key: overrides.pop(key) for key in (
                    "ability_guid", "source_uid", "owner_id", "target_map",
                    "variables") if key in overrides}).to_dict()
        value.update(overrides)
        return value


    def _effect_owner(self, target: int | None = None) -> int:
        """Resolve the controller for a modifier, including untargeted ones."""
        owner = self.target_owner(target, default=None)
        if owner is not None:
            return int(owner)
        owner = self.bstate.get("resolving_owner_id")
        if owner is not None:
            return int(owner)
        source = self.bstate.get("resolving_source_uid")
        if source is not None:
            from pvp_db import db_card_owner_id
            card_owner = db_card_owner_id(
                self.session.session_id, int(source), conn=self.db)
            if card_owner is not None:
                return int(card_owner)
        profile = getattr(self.handler, "user_profile", None)
        return int(profile.get("id", 0)) if isinstance(profile, dict) else 0

    def _side_keys(self, owner: int) -> tuple[str, str]:
        """Return the FRA view keys for a controller's mutable resources."""
        if self.bstate.get("pvp"):
            health_map = self.bstate.get("pvp_health_map") or {}
            health_key = health_map.get(owner, health_map.get(str(owner)))
            if not health_key:
                health_key = f"hp_{int(owner)}"
            from .runtime_helpers import game_health_projection_attr
            projected = game_health_projection_attr(
                self.game, self.bstate, owner)
            return ("ai" if projected == "ai_health" else "player",
                    health_key)
        return ("ai", "ai_health") if int(owner) == 0 else (
            "player", "player_health")


    def champion_card_uid(self, owner: int) -> int | None:
        """Return the persisted champion card UID for a controller."""
        for participant, champion_uid in (self.bstate.get("champ_map") or {}).items():
            try:
                if int(participant) == int(owner):
                    return int(champion_uid)
            except (TypeError, ValueError):
                continue
        attr = "_ai_champ_scid" if int(owner) == 0 else "_player_champ_scid"
        champion = getattr(self.handler, attr, None)
        try:
            return int(champion.uid.uid64) if champion is not None else None
        except (AttributeError, TypeError, ValueError):
            return None

    def _champion_owner(self, target):
        """Return a champion controller only when target is that champion."""
        if target is None:
            return None
        try:
            owner = self.target_owner(int(target), default=None)
            return (owner if owner is not None and
                    self.champion_card_uid(int(owner)) == int(target) else None)
        except (TypeError, ValueError):
            return None


    def _champion_template_guid(self, champion_uid: int):
        """Return the champion template GUID for a champion card UID."""
        for scid_attr, guid_attr in (("_player_champ_scid", "_player_champ_guid"),
                                     ("_ai_champ_scid", "_ai_champ_guid")):
            scid = getattr(self.handler, scid_attr, None)
            try:
                if scid is not None and int(scid.uid.uid64) == int(champion_uid):
                    return getattr(self.handler, guid_attr, None)
            except (AttributeError, TypeError, ValueError):
                continue
        return None


    @staticmethod
    def _shard_flag(value) -> int:
        import game_engine

        if isinstance(value, int):
            return int(value)
        return int(game_engine.SHARD_TO_FLAG.get(
            str(value or "").lower(), 0) or 0)


    def zombie_plague(self) -> str:
        """Void a zero-defense troop and create the typed zombie token."""
        target = self.resolved_target()
        if target is None:
            return "zombie plague: no target"
        from pvp_db import db_card_type_effective_defense
        row = db_card_type_effective_defense(
            self.session.session_id, int(target), conn=self.db)
        if not row or "troop" not in str(row[0] or "").lower():
            return "zombie plague: target is not a troop"
        if int(row[1] or 0) + int(row[2] or 0) > 0:
            return "zombie plague: defense remains"
        self.void_card()
        guid = self.template_value("m_CardTemplateId", "")
        if not guid:
            return "zombie plague: voided (token metadata missing)"
        from rules_port.token_effects import summon_token
        return summon_token(self, {"token_guid": str(guid).lower(),
                                    "amount": 1, "collection": "Warzone"})

    def xarlox(self) -> str:
        """Mill three typed deck cards, summoning one token per milled troop."""
        owner = self.target_owner(self.resolved_target(), default=
                                  self.bstate.get("resolving_owner_id", 0))
        if owner is None:
            return "xarlox: no owner"
        token_guid = self.template_value("m_CardTemplateId", "")
        milled = troops = 0

        def summon(guid):
            payload = {"token_guid": str(guid).lower(), "amount": 1,
                       "collection": "Warzone"}
            from rules_port.token_effects import summon_token
            return summon_token(self, payload)
        from pvp_db import db_deck_top_card_type, db_move_card_to_discard_reset
        for _ in range(3):
            row = db_deck_top_card_type(
                self.session.session_id, int(owner), conn=self.db)
            if not row:
                break
            uid, card_type = int(row[0]), str(row[1] or "")
            db_move_card_to_discard_reset(
                self.session.session_id, uid, conn=self.db)
            self.db.commit()
            from rules_port.zone_effects import project_card
            project_card(self, uid, "discard")
            milled += 1
            if "troop" in card_type.lower():
                troops += 1
                if token_guid:
                    summon(token_guid)
        return f"xarlox milled {milled}, summoned {troops}"

    def plan_c(self) -> str:
        """Execute the temporary client Plan C resource-to-token effect."""
        owner = self.target_owner(self.resolved_target(), default=
                                  self.bstate.get("resolving_owner_id", 0))
        if owner is None:
            return "plan c: no owner"
        side = "player" if int(owner) else "ai"
        total_key, current_key = f"{side}_total_resources", f"{side}_resources"
        total = int(self.bstate.get(total_key, 0) or 0)
        amount = total // 2
        self.bstate[total_key] = 0
        self.bstate[current_key] = 0
        setattr(self.game, total_key, 0)
        setattr(self.game, current_key, 0)
        guid = self.template_value("m_CardTemplateId", "")
        if not guid or amount <= 0:
            return f"plan c: spent {total}, created 0"
        collection = self.template_value("m_CardCollection", "Warzone")
        payload = {"token_guid": str(guid).lower(), "amount": amount,
                   "collection": str(collection).rsplit(".", 1)[-1]}
        from rules_port.token_effects import summon_token
        return summon_token(self, payload)


    def reveal_cards(self) -> str:
        """Run the metadata reveal through RulesPort."""
        from rules_port.reveal_effects import reveal_cards
        return reveal_cards(self)

    def battle_cards(self, effect=None) -> str:
        """Resolve the metadata Battle2Cards operation through RulesPort."""
        from rules_port.battle_effects import battle_cards
        return battle_cards(self, effect)


