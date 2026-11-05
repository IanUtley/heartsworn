from __future__ import annotations
from .runtime_helpers import as_int
import json
from dataclasses import dataclass, replace
from typing import Any
from .damage_effects import assign_damage_shield_order, serialized_damage
from rules_port.card_view import CardView
import rules_port.context as _context


class TransformEffectsMixin:
    """Methods of EffectContext grouped by transforms.

    Mixed into EffectContext; attribute access goes through self, so the
    class composition is unchanged.
    """

    def create_matching_token(self) -> str:
        """Create copies of the resolved target using typed effect fields."""
        target = self.resolved_target()
        if target is None:
            return "matching token: no target"
        count = self.value("m_InputValue", default=1)
        collection = self.template_value("m_CardCollection", "Warzone")
        # The authored location decides where in the collection the copies
        # land.  Every matching-token effect currently authors Unknown, which
        # the client implements as a random slot (shuffled into the deck).
        deck_location = str(self.template_value(
            "m_CardLocation", "") or "").rsplit(".", 1)[-1].lower()
        from rules_port.token_effects import create_matching_target
        # C# CreateTokenMatchingTarget: a typed zero creates nothing.
        made = create_matching_target(
            self, int(target), int(count), collection, deck_location)
        return f"created {made} matching token(s)"

    def transform_replica(self) -> str:
        """Transform a card into a replica while retaining its zone."""
        import game_engine

        target = self.resolved_target()
        if target is None:
            return "transform replica: no target"
        from pvp_db import db_card_zone_details, db_card_source_info
        details = db_card_zone_details(
            self.session.session_id, int(target), conn=self.db)
        if not details:
            return "transform replica: target not found"
        copy_from = int(target)
        serialized = self.template_value("m_SerializedTAC", None)
        if isinstance(serialized, dict):
            serialized = serialized.get("data", "")
        from rules_port.tac import tac_int
        if tac_int(serialized, "UseStoredTarget", 0):
            stored = self.bstate.get("stored_targets") or {}
            targets = (stored.get(self.ability_guid) or
                       stored.get(str(self.bstate.get(
                           "resolving_ability") or "")) or ())
            try:
                targets = [int(value) for value in targets]
            except (TypeError, ValueError):
                targets = []
            if len(targets) != 1:
                return "transform replica: expected one stored target"
            copy_from = targets[0]
            details = db_card_source_info(
                self.session.session_id, copy_from, conn=self.db)
            if not details or not details[0]:
                return "transform replica: stored copy source not found"
        from pvp_db import db_card_gem_type
        event_gems = db_card_gem_type(
            self.session.session_id, copy_from, conn=self.db)
        from rules_port.transform_effects import transform_instance
        transform_instance(
            self, int(target), details[0], keep_zone=True,
            copy_from_uid=copy_from, is_replica=True,
            event_gems=(event_gems if event_gems else
                        game_engine.EGemTypesNew.Unknown))
        from .replica import apply_replica_mods, project_replica
        apply_replica_mods(self, int(target), details[0])
        self.db.commit()
        project_replica(self, int(target))
        return f"replicated {hex(int(target))}"

    def transform_self(self) -> str:
        """Port of ``TransformSelfAbilityEffectTemplate.Apply``.

        ``m_PlantGarden`` rolls the authored 60/30/10 Venus Fly
        Gorger/Spore Stalks/Vine Goliath replacement.  Otherwise the card
        becomes a copy of the resolved target (``TransformIntoCopy``), with
        ``m_IsReplica`` applying the replica modification.
        """
        import random
        import game_engine

        source = self.bstate.get("resolving_source_uid")
        if source is None:
            return "transform self: no source"
        from rules_port.transform_effects import transform_instance
        if self.template_value("m_PlantGarden", False):
            rng = self.bstate.get("_rules_rng")
            roll = (int(rng.next(100)) if rng is not None and
                    hasattr(rng, "next") else random.randrange(100))
            name = ("Venus Fly Gorger" if roll < 60 else
                    "Spore Stalks" if roll < 90 else "Vine Goliath")
            from pvp_db import db_gencard_template
            row = db_gencard_template(name, conn=self.db)
            if not row:
                return f"transform self: {name} template missing"
            transform_instance(self, int(source), str(row[0]).lower(),
                               keep_zone=True)
            return f"transformed self -> {name}"
        target = self.resolved_target()
        if target is None:
            return "transform self: no target"
        from pvp_db import db_card_source_info
        info = db_card_source_info(
            self.session.session_id, int(target), conn=self.db)
        if not info:
            return "transform self: target not found"
        template = str(info[0]).lower()
        is_replica = bool(self.template_value("m_IsReplica", False))
        from pvp_db import db_card_gem_type
        source_gems = db_card_gem_type(
            self.session.session_id, int(target), conn=self.db)
        transform_instance(
            self, int(source), template, keep_zone=True,
            copy_from_uid=int(target), is_replica=is_replica,
            event_gems=(source_gems if source_gems else
                        game_engine.EGemTypesNew.Unknown))
        if is_replica:
            from .replica import apply_replica_mods
            apply_replica_mods(self, int(source), template)
            self.db.commit()
        return f"transformed self into {template[:8]}"

    def transform_card_random(self) -> str:
        """Run the authored random-filter transform through RulesPort."""
        from rules_port.transform_effects import transform_card_at_random
        return transform_card_at_random(self)

    def _portal_template_for(self, target) -> str:
        """Return the Portal template matching one card's rarity.

        Port of ``TransformCardAbilityEffectTemplate.GetPortalForCard``.
        """
        from pvp_db import db_gencard_template, db_transform_target_info
        row = db_transform_target_info(
            self.session.session_id, int(target), conn=self.db)
        rarity = str(row[8] if row else "").strip().lower()
        name = {"uncommon": "Uncommon Portal", "rare": "Rare Portal",
                "legendary": "Legendary Portal"}.get(rarity, "Common Portal")
        portal = db_gencard_template(name, conn=self.db)
        return str(portal[0]).lower() if portal else ""

    def transform_card(self) -> str:
        """Run the authored direct-template transform operation."""
        import game_engine

        if self.bstate.get("_skip_transform"):
            return "transform skipped (gate not met)"
        from rules_port.transform_effects import irreversible_original, transform_instance
        portal = bool(self.template_value("m_Portal", False))
        template = self.template_value("m_CardTemplateId", "")
        serialized = self.template_value("m_SerializedTAC", None)
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
            return transform_into_stored_copy(self, serialized)
        event_gems = game_engine.EGemTypesNew.Unknown
        source_uid = self.bstate.get("resolving_source_uid")
        if tac_int(serialized, "CopyGems", 0) and source_uid is not None:
            from pvp_db import db_card_gem_type
            event_gems = db_card_gem_type(
                self.session.session_id, int(source_uid),
                conn=self.db) or game_engine.EGemTypesNew.Unknown
        if not portal and (not template or
                           str(template).lower() == "0" * 36):
            raise RuntimeError(
                "RulesPort TransformCard effect is missing typed target template")
        pending = list(self.bstate.pop("pending_transform_cards", []) or [])
        if pending:
            for value in pending:
                target = value[0] if isinstance(value, (tuple, list)) else value
                new_template = (self._portal_template_for(target)
                                if portal else str(template))
                if not new_template:
                    continue
                transform_instance(self, int(target), new_template,
                                   keep_zone=True, event_gems=event_gems,
                                   original_template_guid=irreversible_original(
                                       serialized, template))
            return f"transform {len(pending)} card(s)"
        # The effect's own resolved target (e.g. a TriggerSource card) comes
        # before the ability source, which is only the fallback.
        target = (self.bstate.get("player_transform_target") or
                  self.bstate.get("player_mod_target") or
                  self.bstate.get("player_shift_source") or
                  self.target(default=None) or
                  self.bstate.get("resolving_source_uid"))
        if target is None:
            return "transform: no target"
        if portal:
            new_template = self._portal_template_for(target)
            if not new_template:
                return "transform: no portal template"
        else:
            new_template = str(template)
        result = transform_instance(self, int(target), new_template,
                                    keep_zone=True, event_gems=event_gems,
                                    original_template_guid=irreversible_original(
                                        serialized, template))
        if isinstance(result, str):
            return result
        return f"transformed {hex(int(target))} -> {new_template[:8]}"

    def create_token_copy(self) -> str:
        """Run the authored token-copy operation through RulesPort."""
        from rules_port.token_effects import create_token_copy
        return create_token_copy(self)

    def transform_card_to_target(self) -> str:
        """Transform the source card into a copy of the resolved target."""
        source = self.bstate.get("resolving_source_uid")
        target = self.resolved_target()
        if source is None or target is None:
            return "transform to target: missing source/target"
        from pvp_db import db_card_zone_details
        details = db_card_zone_details(
            self.session.session_id, int(target), conn=self.db)
        if not details:
            return "transform to target: target not found"
        from rules_port.transform_effects import transform_instance
        # TransformIntoCopy changes the source's template in place; the C#
        # Session method preserves its collection and position.
        transform_instance(
            self, int(source), details[0], keep_zone=True,
            copy_from_uid=int(target), is_replica=False)
        return f"transformed {hex(int(source))} to target copy"

    def summon_token(self) -> str:
        """Create tokens through the metadata token-operation helper."""
        from rules_port.token_effects import summon_token
        return summon_token(self)

    def summon_x_tokens(self) -> str:
        """Port SummonXTokenTroops: base amount plus source counters."""
        from rules_port.token_effects import summon_token as native_summon_token
        count = int(self.template_value("m_BaseAmount", 0) or 0)
        source = self.bstate.get("resolving_source_uid")
        if source is not None:
            from pvp_db import db_card_mutation_field
            try:
                buffs = json.loads(db_card_mutation_field(
                    self.session.session_id, int(source),
                    "permanent_buffs", conn=self.db) or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                buffs = {}
            count += sum(int(value or 0) for value in
                         (buffs.get("counters", {}) if isinstance(buffs, dict) else {}).values())
        payload = {"amount": max(0, count),
                   "token_guid": self.template_value("m_CardTemplateId", ""),
                   "collection": self.template_value("m_CardCollection", "Warzone")}
        return native_summon_token(self, payload)

    def conscript(self) -> str:
        """Conscript cards through the shared token/zone helper."""
        from rules_port.token_effects import summon_token
        try:
            amount = int(self.value("m_Amount", 1) or 0)
        except (TypeError, ValueError):
            amount = 1
        faction = self.template_value("m_Faction", "None")
        target = self.resolved_target(source_fallback=False)
        if (target is not None and
                str(faction).rsplit(".", 1)[-1].lower() == "underworld"):
            owner = int(self.target_owner(
                int(target), default=self.bstate.get(
                    "resolving_owner_id", 0)) or 0)
            try:
                from rules_port.static_rules import player_int_attributes
                amount += int(player_int_attributes(
                    self.db, self.session.session_id, self.bstate,
                    owner).get("ConscriptUnderworldBonus", 0) or 0)
            except (ImportError, TypeError, ValueError):
                pass
        return summon_token(self, {
            "collection": "Hand", "amount": amount,
            "card_filter": self.template_value("m_CardFilter", {}),
            "random_with_replacement": True,
            "conscript_event": True,
            "conscript_faction": str(faction).rsplit(".", 1)[-1]})

    def load_player_deck(self) -> str:
        """Load authored player-deck cards through the shared helper."""
        from rules_port.deck_effects import load_player_deck
        return load_player_deck(self)
