"""Card templates, instance data, ability uses, draws and deck-out for the live game."""

from __future__ import annotations

import rules_port.leaves_players as _leaves_players
from typing import TYPE_CHECKING
import json
from dataclasses import replace
from typing import Any, cast

import game_engine
from rules_port.card_view import CardView
from db import log_req
import db
from pvp_db import db_ability_effect_rows
from pvp_db import db_gem_ability_guids
from application.card_play import _NO_PERSISTED_GEM
from pvp_db import db_template_by_guid
from application.records_projection import _records_card_projection
from pvp_db import db_card_template_thresholds
from pvp_db import db_move_card_to_location
from pvp_db import EMPTY_SOCKET_GEMS
from pvp_db import db_set_card_abilities
from pvp_db import db_target_template_filter
from pvp_db import db_champion_template_health
from profile_db import db_card_instance_display
from pvp_db import db_card_instance_dynamic_projection
from pvp_db import db_is_champion_template
from pvp_db import db_card_template_lethal
from pvp_db import db_template_subtype
from pvp_db import db_session_turn_order
from pvp_db import db_card_ability_list
from pvp_db import db_get_card_abilities
from pvp_db import db_card_sync_abilities
from pvp_db import db_card_uses
from pvp_db import db_bump_card_use
from pvp_db import db_ability_activation_metadata
from pvp_db import db_card_mutation_snapshot
from pvp_db import db_template_attributes
from pvp_db import db_set_card_abilities_and_attributes
from pvp_db import db_deck_top_card_details
from pvp_db import db_move_card_row_to_hand


if TYPE_CHECKING:
    from application._host_surface import HostSurface as _HostBase
else:
    _HostBase = object


class CardStateMixin(_HostBase):
    """Methods of HCPHandler, mixed into it (see hconnect_server)."""

    # Set by the live handler's scene setup (CardPlayMixin); declared here for the type checker.
    _scene_targeted_ability_owners: dict[str, int]

    @staticmethod
    def _champion_health_by_guid(guid):
        """Look up a champion template's starting health by its GUID."""
        import hconnect_server as _hc
        if not guid:
            return 20
        return db_champion_template_health(guid)

    def _template_by_guid(self, template_guid):
        """Fetch card template data for a template GUID.
        Returns (guid, card_type, name, cost, attack, defense) or None."""
        import hconnect_server as _hc
        if not template_guid:
            return None
        return db_template_by_guid(template_guid)

    def _resolve_card_ref(self, card_ref, user_id=None):
        """Resolve a game_cards.card_template_id to card template info.

        card_template_id is either an integer card_instances.instance_id
        (player/FRA cards) or a template GUID string (AI encounter deck cards).
        Returns (template_guid, card_type_name, name, cost, attack, defense).
        """
        import hconnect_server as _hc
        if card_ref is None:
            return None, None, None, 0, 0, 0
        if isinstance(card_ref, str):
            t = self._template_by_guid(card_ref)
            if t:
                return t
            return None, None, None, 0, 0, 0
        if user_id is None and self.user_profile:
            user_id = self.user_profile.get("id")
        if user_id is None:
            return None, None, None, 0, 0, 0
        row = db_card_instance_display(
        user_id, card_ref, conn=db._db)
        if row:
            return row[0], row[1], row[2], row[3] or 0, row[4] or 0, row[5] or 0
        return None, None, None, 0, 0, 0

    def _card_full_data(self, game, scid: game_engine.SessionCardId,
                        template_guid, instance_id=None,
                        instance_projection=None):
        """Resolve full card data for a session card and prepare it for display.

        Fills game.card_defs[scid] (thresholds, abilities) so a subsequent
        push_card_updated renders the complete card (cost, atk/def, shards,
        abilities, gems). Returns (tpl_guid, card_type, name, cost, attack,
        defense, gem_type).

        Instance-aware: the card's CURRENT abilities come from game_cards
        (Shift moves abilities between cards, and PowerShifted triggers grant
        +atk/+def), not the template's printed list — so a Prep re-push keeps a
        shifted Lifedrain / granted ability / buff instead of reverting it.
        """
        import hconnect_server as _hc
        # This is the hot CardUpdated path. Keep the instance fields in the
        # historical ``db_card_instance_full`` order, but fetch the owner,
        # zone, and current template GUID in the same indexed lookup. Printed
        # fields come from the process-wide Records cache; a missing record is
        # expected for champion/generated/legacy rows and uses the DB fallback.
        joined = None
        irow = None
        if scid is not None:
            joined = (instance_projection if instance_projection is not None
                      else db_card_instance_dynamic_projection(
                      game.session_id.uid64, scid.uid.uid64, conn=db._db))
            irow = joined
        t = None
        records_template = None
        if joined:
            joined_guid = str(joined[13] or "")
            requested_guid = str(template_guid or "")
            if (joined_guid and
                    (not requested_guid or
                     joined_guid.lower() == requested_guid.lower())):
                records_template = _records_card_projection(joined_guid)
                if records_template:
                    t = records_template[:6]
        if t is None:
            records_template = _records_card_projection(template_guid)
            if records_template:
                t = records_template[:6]
        if t is None:
            t = self._template_by_guid(template_guid)
        if t:
            tpl_guid, ct_name, name, cost, atk, def_ = t
            ct = game_engine.card_type_from_db(ct_name)
        else:
            # Champion templates — PvP champs in champion_templates_extended,
            # PvE champs (and some PvP) in champion_templates.  Try both.
            from pvp_db import db_champion_ability_guids
            if db_is_champion_template(template_guid):
                tpl_guid, ct_name, name, cost, atk, def_ = (
                    template_guid, "Champion", "Champion", 0, 0, 20)
                ct = game_engine.ECardTypes.Champion
            else:
                tpl_guid = "00000000-0000-0000-0000-000000000000"
                ct_name = "Troop"
                ct, name, cost, atk, def_ = game_engine.ECardTypes.Troop, "Card", 0, 0, 0
        shards = []
        attributes = game_engine.ECardAttributes.Unknown
        lethal = False
        srow = None
        # Instance-persisted abilities + power/toughness buffs (Shift /
        # PowerShiftedEvent). A bare template read would drop them on every
        # re-push, so read them from the card's game_cards row.
        inst_abilities_json = None
        atk_mod = def_mod = dmg = 0
        perm_atk = perm_def = 0
        temp_atk = temp_def = 0
        inst_attrs = 0
        temp_attrs = 0
        persisted_int_attrs = {}
        threshold_override = None
        subtype_override = None
        instance_damage_shield = False
        orig_tpl = None
        card_location = None
        card_owner_id = None
        card_instance_ref = None
        persisted_gem = _NO_PERSISTED_GEM
        if scid is not None:
            if irow:
                inst_abilities_json = irow[0]
                atk_mod = irow[1] or 0
                def_mod = irow[2] or 0
                dmg = irow[3] or 0
                orig_tpl = irow[4] or None
                inst_attrs = int(irow[9] or 0)
                temp_attrs = int(irow[10] or 0)
                try:
                    pb = json.loads(irow[5] or "{}")
                    perm_atk = int(pb.get("atk", 0) or 0)
                    perm_def = int(pb.get("def", 0) or 0)
                    raw_int_attrs = pb.get("int_attrs", {})
                    if isinstance(raw_int_attrs, dict):
                        persisted_int_attrs = {
                            str(k): int(v or 0)
                            for k, v in raw_int_attrs.items()
                            if int(v or 0) != 0
                        }
                    if isinstance(pb.get("thresholds"), list):
                        threshold_override = [int(value) for value in
                                              pb["thresholds"]]
                    if "subtype" in pb:
                        subtype_override = str(pb.get("subtype") or "")
                    instance_damage_shield = bool(pb.get("damage_shields"))
                except Exception:
                    pass
                try:
                    tb = json.loads(irow[6] or "{}")
                    temp_atk = int(tb.get("atk", 0) or 0)
                    temp_def = int(tb.get("def", 0) or 0)
                    instance_damage_shield = instance_damage_shield or bool(
                        tb.get("damage_shields"))
                except Exception:
                    pass
            card_owner_id = irow[11] if irow else None
            card_location = irow[12] if irow else None
            if irow and len(irow) > 14:
                card_instance_ref = irow[14]
            if irow and len(irow) > 15:
                persisted_gem = irow[15]
        native_bstate = getattr(self, "_current_bstate", None)
        native_cost = None
        from rules_port.static_rules import effective_cost
        native_cost = effective_cost(
            db._db, game.session_id.uid64, native_bstate, scid.uid.uid64)
        cost_mod = 0
        if tpl_guid != "00000000-0000-0000-0000-000000000000":
            records_template = (records_template if records_template and
                                str(records_template[0]).lower() ==
                                str(tpl_guid).lower() else None)
            if records_template:
                lethal = bool(records_template[10])
                shards = list(records_template[6])
                srow = (None, json.dumps(list(records_template[7])),
                        records_template[8])
            else:
                lethal = bool(db_card_template_lethal(tpl_guid))
                srow = db_card_template_thresholds(tpl_guid)
            if srow:
                if srow[0]:
                    try:
                        td = json.loads(srow[0])
                        shard_flags = {0:0, 1:4, 2:8, 3:16, 4:32, 5:64}
                        shards = [shard_flags.get(s, s) for s in td.get('list', [])]
                    except Exception:
                        pass
                if srow[2]:
                    try:
                        attributes = int(srow[2])
                    except Exception:
                        pass
            elif ct == game_engine.ECardTypes.Champion:
                # Champion abilities live in champion_abilities, not card_templates.
                from pvp_db import db_champion_ability_guids
                carow = db_champion_ability_guids(tpl_guid)
                if carow:
                    srow = (None, json.dumps(carow), None)
        if threshold_override is not None:
            shards = list(threshold_override)
        try:
            if records_template:
                subtype = records_template[9] or ""
            else:
                subtype = db_template_subtype(tpl_guid, conn=db._db) or ""
        except Exception:
            subtype = ""
        if subtype_override is not None:
            subtype = subtype_override
        # Ability list: the instance's persisted list wins (Shift moves
        # abilities between cards).  An empty list is authoritative for
        # ordinary cards: a consumed ONE-SHOT must stay gone when the card is
        # rebuilt for CardUpdated.  Champions are the exception because their
        # game_cards rows are seeded with [] while their signature powers live
        # in champion_abilities rather than card_templates.
        ab_src = inst_abilities_json
        if (not ab_src or str(ab_src).strip() in ("null", "{}")
                or (str(ab_src).strip() == "[]"
                    and ct == game_engine.ECardTypes.Champion)):
            ab_src = srow[1] if srow else None
        ability_guids = []
        abilities = []
        if ab_src:
            try:
                parsed = json.loads(ab_src)
                abilities = [game_engine.ResourceId.from_str(g) for g in parsed]
                ability_guids = [g.lower() for g in parsed]
            except Exception:
                pass
        # Socketed-gem abilities come from the current deck socket, not from
        # the denormalized decks.gem_abilities cache.  The cache can predate a
        # deck edit (which was how a Speed gem retained an old Rage ability).
        # Call through the production class explicitly: focused test stubs
        # intentionally provide only the older helper surface, while the
        # live handler still receives the same method implementation.
        gem_instance_id = (instance_id if instance_id is not None
                           else card_instance_ref)
        gem_type = _hc.HCPHandler._card_gem_type(
            cast(Any, self), game, scid, gem_instance_id, persisted_gem=persisted_gem)
        display_gems = int(gem_type or 0)
        if (display_gems > 0x3FF and
                not (display_gems & EMPTY_SOCKET_GEMS)):
            # Older deck saves lost the packed-format flag but retained the
            # socket payload. Restore the marker in the client projection so
            # its socket renderer decodes every slot instead of treating the
            # combined value as one legacy gem enum.
            display_gems |= EMPTY_SOCKET_GEMS
        if gem_type:
            gem_guids = db_gem_ability_guids(gem_type, conn=db._db)
            for gem_guid in gem_guids:
                gem_guid = str(gem_guid).lower()
                if gem_guid not in ability_guids:
                    ability_guids.append(gem_guid)
                    abilities.append(game_engine.ResourceId.from_str(gem_guid))
            # Persist the merged list for the active game instance so combat
            # statics and subsequent pushes see the same gem ability.
            if scid is not None and gem_guids:
                try:
                    current = json.loads(inst_abilities_json or "[]")
                except Exception:
                    current = []
                current = [str(g).lower() for g in current]
                merged = list(current)
                if not merged:
                    merged = list(ability_guids)
                else:
                    merged.extend(str(gem_guid).lower() for gem_guid in gem_guids if str(gem_guid).lower() not in merged)
                if merged != current:
                    db_set_card_abilities(
                    game.session_id.uid64, scid.uid.uid64,
                    json.dumps(merged), conn=db._db)
                    # _card_full_data() is also called immediately before the
                    # battle state is persisted.  That save uses the separate
                    # game_session connection, so leave the shared connection
                    # transaction closed or it can deadlock on its own write
                    # (especially during the opening hand when socketed cards
                    # are re-pushed).
                    db._db.commit()
        # Encounter scene statics are continuous effects. Apply them silently
        # when a card is materialized in the relevant zone, using the granted
        # ability's typed target filter. Beast Crossing therefore affects only
        # Wild troops in the warzone, including troops created later, without
        # ever placing those grants on the chain.
        if scid is not None and card_location == "warzone":
            from rules_port.targeting import evaluate_card_filter
            card_subtype = subtype or ""
            # Scene target filters can inspect dynamic IntAttr markers (for
            # example, Taming Dire Toad only grants Untamed to non-Tamed
            # Dire Toads).  Reconstruct those markers from the card's current
            # ability list before evaluating the scene filter.
            scene_int_attrs = {}
            scene_int_attrs.update(persisted_int_attrs)
            for _ag in ability_guids:
                for _eg, _et, _ep in db_ability_effect_rows(_ag, conn=db._db):
                    if _et != "CardModifierAbilityEffectTemplate":
                        continue
                    try:
                        _pd = json.loads(_ep or "{}")
                    except (TypeError, ValueError, json.JSONDecodeError):
                        _pd = {}
                    if str(_pd.get("property", "")).lower() != "intattr":
                        continue
                    _attr = str(_pd.get("attribute") or "")
                    if not _attr and "untamed" in str(_pd.get("text") or "").lower():
                        _attr = "Untamed"
                    if _attr:
                        scene_int_attrs[_attr] = 1
            if scene_int_attrs.get("Tamed", 0) > 0:
                scene_int_attrs.pop("Untamed", None)
            scene_added = []
            for scene_ag in ((getattr(self, "_scene_global_ability_guids", []) or [])
                             + (getattr(self, "_scene_targeted_ability_guids", []) or [])):
                target_ids_json = _hc.db_ability_target_template_ids(
                    scene_ag, conn=db._db)
                try:
                    target_ids = json.loads(target_ids_json or "[]")
                except (TypeError, ValueError):
                    target_ids = []
                scene_ag = str(scene_ag).lower()
                # Targeted encounter passives retain the setup card's
                # controller.  This is important for filters such as
                # IsControlledBy (the Taming Dire Toad passive means the
                # controller's Dire Toads, not every Dire Toad in play).
                scene_view = CardView.scene_card(
                    scid.uid.uid64, ct_name, card_location, shards, name,
                    card_owner_id, card_subtype,
                    int(attributes | inst_attrs | temp_attrs), scene_int_attrs)
                if scene_ag in (getattr(self, "_scene_targeted_ability_owners", {}) or {}):
                    scene_view = replace(scene_view, src_owner_id=(
                        self._scene_targeted_ability_owners[scene_ag]))
                scene_card = scene_view.as_shape("scene_card")
                qualifies = False
                for target_id in target_ids:
                    filter_json = db_target_template_filter(
                    target_id, conn=db._db)
                    try:
                        filter_data = json.loads(filter_json or "{}")
                    except (TypeError, ValueError):
                        filter_data = {}
                    if evaluate_card_filter(scene_card, filter_data,
                                            scid.uid.uid64):
                        qualifies = True
                        break
                if qualifies and scene_ag not in ability_guids:
                    ability_guids.append(scene_ag)
                    abilities.append(game_engine.ResourceId.from_str(scene_ag))
                    scene_added.append(scene_ag)
            if scene_added:
                db_set_card_abilities(
                game.session_id.uid64, scid.uid.uid64,
                json.dumps(ability_guids), conn=db._db)
                db._db.commit()
        # Effective attributes = static template attributes + any attributes
        # granted by the card's CURRENT passives. Attributes render as icons on
        # the card and must be present the whole time (not computed only during
        # combat). E.g. Gemsoul Feeder's "This gets Lifedrain in all zones"
        # passive (44605164) grants SpiritDrain via a CardModifier
        # AttributeModifier.
        # Instance-persisted attributes (apply_attribute_grant writes the
        # template attrs + any granted keywords into game_cards.card_attributes,
        # e.g. Inner Conflict's permanent "can't attack or block"). Without
        # this OR the icons vanish on the next CardUpdated re-push.
        attributes |= inst_attrs | temp_attrs
        # Permanent + this-turn buffs (apply_card_stat_mod) also contribute.
        atk += atk_mod + perm_atk + temp_atk
        def_ += def_mod + perm_def + temp_def
        # Cost modifiers (static card_cost_mod + dynamic formula entries).
        cost = (int(native_cost) if native_cost is not None else
                max(0, cost + cost_mod))
        # Continuous static abilities (WhileCardInPlay / Permanent): dynamic
        # self-bonuses ("+2/+2 for each card in your hand"), auras ("Troops
        # you control have +2/+2") and zone-wide cost modifiers ("Your
        # artifacts in all zones have cost -1").  Computed from gamedata so the
        # displayed card and its playable cost always match the current board.
        if scid is not None and tpl_guid != "00000000-0000-0000-0000-000000000000":
            try:
                from rules_port.static_rules import effective_deltas
                bstate = getattr(self, "_current_bstate", None)
                if bstate is None:
                    try:
                        raw_state = db_session_turn_order(
                        game.session_id.uid64, conn=db._db)
                        data = json.loads(raw_state) if raw_state else {}
                        bstate = data if isinstance(data, dict) else {}
                    except Exception:
                        bstate = {}
                    self._current_bstate = bstate
                static = effective_deltas(db._db, game.session_id.uid64, bstate,
                                          scid.uid.uid64)
                atk += static["atk"]
                def_ += static["def"]
                if native_cost is None:
                    # The payable cost, as the AI and player payment use it
                    # (includes champion cost auras such as Great Wisdom).
                    try:
                        from rules_port.static_rules import effective_cost
                        cost = int(effective_cost(
                            db._db, game.session_id.uid64, bstate,
                            scid.uid.uid64))
                    except Exception:
                        cost = max(0, cost + static["cost_mod"])
                attributes |= static["attrs"]
            except Exception:
                pass
        # TEMPORARY combat damage: show the defense reduced by damage taken
        # (the client colors values below the template base red). The troop
        # "heals" when card_damage is cleared at its controller's Prep.
        def_ = max(0, def_ - dmg)
        shards = [int(shard) for shard in shards if shard is not None]
        game.card_defs[scid] = game_engine.CardDef(
            name, ct, cost, atk, def_, shards, abilities, attributes, lethal,
            subtype=subtype)
        if scid is not None:
            from rules_port.ability_usage import remaining_uses_per_game
            ability_uses = remaining_uses_per_game(
                db._db, game.session_id.uid64, scid.uid.uid64, ability_guids,
                getattr(self, "_current_bstate", None) or {},
                champion=ct == game_engine.ECardTypes.Champion)
            game.card_defs[scid].uses_per_game_counts = {
                game_engine.ResourceId.from_str(guid): int(value)
                for guid, value in ability_uses.items()
            }
        cooldowns = {}
        if scid is not None:
            try:
                from pvp_db import db_card_cooldown_counts
                cooldowns.update(db_card_cooldown_counts(
                    game.session_id.uid64, scid.uid.uid64, conn=db._db))
            except Exception:
                pass
            state = getattr(self, "_current_bstate", None) or {}
            prefix = f"{int(scid.uid.uid64)}:"
            for key, value in (state.get("champion_ability_cooldowns") or {}).items():
                if str(key).startswith(prefix):
                    guid = str(key).split(":", 1)[1].lower()
                    try:
                        remaining = int(value or 0)
                    except (TypeError, ValueError):
                        continue
                    if remaining > 0:
                        cooldowns[guid] = remaining
        game.card_defs[scid].cooldown_counts = {
            game_engine.ResourceId.from_str(guid): int(value)
            for guid, value in cooldowns.items() if int(value or 0) > 0
        }
        # Tunneling is a card IntAttr in the template TAC, not an
        # ECardAttributes bit. Keep it on CardDef so every CardUpdated carries
        # the same value the original client reads from CardTemplate.tac.
        try:
            from rules_port.tunneling import tunneling_value
            game.card_defs[scid].tunneling = tunneling_value(
                db._db, tpl_guid, persisted_int_attrs)
        except Exception:
            game.card_defs[scid].tunneling = int(
                persisted_int_attrs.get("Tunneling", 0) or 0)
        game.card_defs[scid].damage_shield = instance_damage_shield
        # IntAttr modifiers are transmitted separately from keyword
        # attributes. Preserve metadata-defined Untamed on the final card
        # definition (the definition above replaces any earlier placeholder)
        # so the Taming Sphere's IntAttrFilter can select this Dire Toad.
        if scid is not None:
            untamed = False
            for _ag in ability_guids:
                _has_untamed = any(
                    _et == "CardModifierAbilityEffectTemplate" and
                    "untamed" in str(_ep or "").lower()
                    for _eg, _et, _ep in db_ability_effect_rows(
                    _ag, conn=db._db))
                if _has_untamed:
                    untamed = True
                    break
            int_attrs = dict(persisted_int_attrs)
            if untamed:
                int_attrs.setdefault("Untamed", 1)
            # Taming is a persistent marker and overrides the scene's static
            # non-Tamed -> Untamed grant for this card instance.
            if int_attrs.get("Tamed", 0) > 0:
                int_attrs.pop("Untamed", None)
            game.card_defs[scid].int_attrs = int_attrs
        # Card.EscalationCount is per card instance (C# starts it at one and
        # Session.Escalate increments the selected card). Carry the persisted
        # count into CardUpdated for every card, regardless of its text.
        if scid is not None:
            from rules_port.statistics import card_escalation_count
            game.card_defs[scid].escalation = card_escalation_count(
                db._db, game.session_id.uid64,
                getattr(self, "_current_bstate", None) or {},
                scid.uid.uid64)
        # Counters + voided-card relationships must survive every re-push: the
        # client renders counter badges and the exile's voided-card link from
        # CardUpdated, so carry them on the CardDef (push_card_updated reads
        # them by default).
        try:
            from rules_port.counter_effects import card_counters_full
            c_counts, c_guids = card_counters_full(
                db._db, game.session_id.uid64, scid.uid.uid64)
            if c_counts:
                cc = {}
                for cname, cnum in c_counts.items():
                    g = c_guids.get(cname)
                    if g:
                        cc[str(g)] = int(cnum)
                game.card_defs[scid].counters = cc
        except Exception:
            pass
        try:
            cbstate = getattr(self, "_current_bstate", None) or {}
            vby = cbstate.get("voided_by") or {}
            linked = [int(u) for u in vby.get(str(scid.uid.uid64), [])]
            if linked:
                game.card_defs[scid].related_cards = [
                    game_engine.SessionCardId(game_engine.UID(u))
                    for u in linked]
        except Exception:
            pass
        # The card's ORIGINAL (pre-transform) template — read from the
        # game_cards row (per-instance, lasts the whole game), NOT derived here.
        # push_card_updated sends it as CardUpdated.OrigTemplate so the client's
        # examine panel can show the original card ("Transformed From <Name>").
        game.card_defs[scid].orig_template = orig_tpl
        # Carry the socketed gem on the CardDef so EVERY later push_card_updated
        # (warzone entry, trigger "shake", phase re-push) renders the gem even
        # when the caller omits gems= — e.g. Shamed Gladiator must keep its gem
        # once it resolves to the board.
        if scid is not None and game.card_defs.get(scid) is not None:
            game.card_defs[scid].gems = display_gems
            # Rage display: CardUpdated.rage drives the client's Rage icon, and
            # it was never populated (always 0).  Use the same authoritative
            # static-ability evaluation as combat (template rage_value + granted
            # "Rage 1 in all zones" gems, gated by the ability's threshold
            # condition) so the card shows the Rage it actually has.
            try:
                from rules_port.static_rules import effective_stats
                bstate = getattr(self, "_current_bstate", None) or {}
                _atk, _def, _attrs, _flags, rage = effective_stats(
                    db._db, game.session_id.uid64, bstate, scid.uid.uid64)
                game.card_defs[scid].rage = int(rage or 0)
            except Exception:
                game.card_defs[scid].rage = 0
        return tpl_guid, ct, name, cost, atk, def_, display_gems


    def _card_ability_list(self, session, card_uid):
        """Current ability GUID list for a card instance (from card_abilities)."""
        import hconnect_server as _hc
        return db_card_ability_list(session.session_id, card_uid)

    def _sync_instance_card_data(self, session, card_uid, template_guid,
                                 commit=True):
        """Populate a card instance's ability/attribute/uses data from its template.

        card_templates is the CANONICAL source; game_cards.card_abilities /
        card_attributes / card_uses are the per-instance working copy. This is
        called when a card instance is created (and by Reversion to reset it).
        Uses is reset to {} (a fresh instance has no usage history).
        """
        import hconnect_server as _hc
        if not template_guid:
            return
        ab_json, attrs = db_get_card_abilities(template_guid)
        ab_json = ab_json or "[]"
        attrs = int(attrs or 0)
        try:
            [g.lower() for g in json.loads(ab_json)]
        except Exception:
            pass
        db_card_sync_abilities(
        session.session_id, card_uid, ab_json, attrs, template_guid,
        commit=commit)
        return attrs

    def _battle_setup_template_data(self, template_guid, cache):
        """Return canonical setup data for a card template, using *cache*.

        Battle deck construction creates many copies of the same templates.  A
        fresh instance still needs the template's working ability/attribute
        snapshot, but resolving that snapshot once per copy causes a SELECT and
        an UPDATE for every card.  Keep the metadata local to one battle setup
        (rather than on the handler, which serves multiple sessions) and reuse
        it for every copy.

        Returns ``(abilities_json, effective_attributes, ability_guids,
        threshold_json)`` or ``None`` when the template is missing.
        """
        import hconnect_server as _hc
        if not template_guid:
            return None
        key = str(template_guid).lower()
        if key in cache:
            return cache[key]
        row = db_card_template_thresholds(template_guid)
        if not row:
            cache[key] = None
            return None
        threshold_json, abilities_json, attributes = row
        abilities_json = abilities_json or "[]"
        try:
            ability_guids = [str(g).lower() for g in json.loads(abilities_json)]
        except Exception:
            ability_guids = []
        effective_attributes = int(attributes or 0)
        result = (abilities_json, effective_attributes, ability_guids,
                  threshold_json)
        cache[key] = result
        return result

    def _card_uses(self, session, card_uid):
        """Return the per-ability usage dict for a card instance."""
        import hconnect_server as _hc
        return db_card_uses(session.session_id, card_uid)

    def _bump_card_use(self, session, card_uid, ability_guid):
        """Increment a card instance's usage of an ability (UsesPerGame/Turn)."""
        import hconnect_server as _hc
        return db_bump_card_use(session.session_id, card_uid, ability_guid)

    def _champion_ability_uses(self, bstate):
        """Per-ability activation counts for the champion's synthetic powers."""
        uses = (bstate or {}).get("champion_ability_uses")
        return uses if isinstance(uses, dict) else {}

    def _champion_ability_available(self, bstate, ability_guid, graph=None,
                                    source_uid=None):
        """Whether an authored ``m_UsesPerGame`` champion power still has a use.

        ONE-SHOT talent powers (``uses_per_game=1``) belong to the synthetic
        champion SessionCardId, which has no ``game_cards`` row to hold
        ``card_uses``; the count lives in the shared battle state instead.
        """
        import hconnect_server as _hc
        limit = int(getattr(getattr(graph, "costs", None),
                            "uses_per_game", 0) or 0)
        guid = str(ability_guid or "").lower()
        if source_uid is None:
            champion = getattr(self, "_player_champ_scid", None)
            if isinstance(champion, game_engine.SessionCardId):
                source_uid = int(champion.uid.uid64)
        from rules_port.cooldowns import champion_cooldown_key, champion_cooldown
        cooldown_key = champion_cooldown_key(source_uid, guid)
        if cooldown_key and champion_cooldown(bstate, source_uid, guid) > 0:
            return False
        uses = self._champion_ability_uses(bstate)
        used = int(uses.get(cooldown_key, uses.get(guid, 0)) or 0)
        if limit > 0 and used >= limit:
            return False
        per_turn = int(getattr(getattr(graph, "costs", None),
                               "uses_per_turn", 0) or 0)
        if per_turn > 0 and source_uid is not None:
            from rules_port.runtime_helpers import (
                champion_ability_use_key, champion_ability_uses_this_turn)
            turn_key = champion_ability_use_key(source_uid, guid)
            if champion_ability_uses_this_turn(bstate, turn_key) >= per_turn:
                return False
        return True


    def _remove_one_shot_ability(self, session, card_uid, ability_guid,
                                  game, pl_t, ai_t, bstate=None):
        """Consume a one-shot ability on this individual card instance.

        ``uses_per_game=1`` is the extracted representation of ONE-SHOT.
        Usage counters still gate activation, but the client also needs the
        current per-instance ability list updated or it will continue to show
        the used power on the card.  Recompute effective attributes and push a
        complete CardUpdated so both the server and client agree.
        """
        import hconnect_server as _hc
        meta = db_ability_activation_metadata(
        ability_guid, conn=db._db)
        if not meta or int(meta[1] or 0) != 1:
            return False
        row = db_card_mutation_snapshot(
        session.session_id, int(card_uid), conn=db._db)
        if not row:
            return False
        abilities = self._card_ability_list(session, card_uid)
        ag = str(ability_guid).lower()
        if ag not in abilities:
            return False
        abilities.remove(ag)
        base_attrs = int(db_template_attributes(row[0], conn=db._db) or 0)
        attributes = base_attrs
        db_set_card_abilities_and_attributes(
        session.session_id, int(card_uid), json.dumps(abilities),
        attributes, conn=db._db)
        db._db.commit()
        if game is not None:
            scid = game_engine.SessionCardId(game_engine.UID(int(card_uid)))
            _tpl, ct, _name, cost, atk, defense, gems = self._card_full_data(
                game, scid, row[0])
            if (bstate or {}).get("pvp"):
                owner = game_engine.UID.make(244, int(row[1]))
            else:
                owner = pl_t if int(row[1] or 0) != 0 else ai_t
            from rules_port.runtime_helpers import card_collection_for_location
            game.push_card_updated(
                scid, owner, card_collection_for_location(row[2]), ct,
                template_id=_tpl, cost=cost, attack=atk, defense=defense,
                gems=gems, attributes=attributes, state=int(row[3] or 0))
        log_req(f"    One-shot ability {ag[:8]} removed from {hex(int(card_uid))}")
        return True


    def _ai_draw_card(self, game, session, ai_t, battle_state):
        import hconnect_server as _hc
        import ai
        if battle_state.get("_rules_port_attached"):
            from rules_port.context import EffectContext
            battle_state.pop("_rules_port_deck_out_owner", None)
            context = EffectContext.from_rules_port(
                game, session, db._db, self, game.player_uid, ai_t,
                battle_state, "", ability=None)
            _leaves_players.draw(context, 1, owner=0)
            from rules_port.persistence import save_state
            save_state(session, battle_state)
            return bool(battle_state.pop("_rules_port_deck_out_owner", None))
        return ai.ai_draw_card(self, game, session, ai_t, battle_state)

    def _move_deck_to_hand(self, game, session, pl_t, card_uid, tpl_guid, instance_id=None):
        """Move a specific card from the deck to the player's hand (pushes into
        `game`). Used by !addcard."""
        import hconnect_server as _hc
        db_move_card_to_location(
        session.session_id, int(card_uid), "hand", position=100, conn=db._db)
        db._db.commit()
        scid = game_engine.SessionCardId(game_engine.UID(int(card_uid)))
        tpl_guid, ct, name, cost, atk, def_, gem_type = self._card_full_data(
            game, scid, tpl_guid, instance_id)
        game.push_card_drawn(scid, pl_t, 1)
        game.push_card_updated(scid, pl_t, game_engine.ECardCollections.Hand, ct,
                               attack=atk, defense=def_, cost=cost,
                               template_id=tpl_guid, gems=gem_type)
        log_req(f"    Added card {card_uid} ({name}) from deck to hand")

    def _player_draw_card(self, game, session, pl_t, owner_id=None):
        """Draw the top card of the human's deck into hand (pushes into `game`)."""
        import hconnect_server as _hc
        _be = self._checkpoint_engine(session)
        is_pvp = (session and (session.session_name or "").startswith("tourney-"))
        if is_pvp:
            from services.tournament_game import pvp_load_state, pvp_save_state
            bstate = pvp_load_state(session)
            if not bstate:
                log_req("    PvP debug draw ignored: no PvP state")
                return False
        else:
            bstate = _be.load_state(session)
        self._current_bstate = bstate
        # Practice/campaign uses the local profile id; tournament PvP uses the
        # ServicePlayer/reckoning pid stored in game_cards.  The latter cannot
        # be inferred from user_profile (it is a different identifier).
        if owner_id is None:
            if bstate.get("pvp"):
                owner_id = int(pl_t.uid64) >> 8
            else:
                owner_id = self.user_profile["id"]
        owner_id = int(owner_id)
        if bstate.get("pvp"):
            pids = [int(p) for p in (bstate.get("pids") or [])]
            opponent_id = next((p for p in pids if p != owner_id), None)
            ai_t = (game_engine.UID.make(244, opponent_id)
                    if opponent_id is not None else game_engine.UID.make(244, owner_id))
            draw_pl_t = game_engine.UID.make(244, owner_id)
        else:
            ai_t = game_engine.UID.make(game_engine.SERVICE_AI_UID_TYPE, 1000)
            draw_pl_t = pl_t
        if bstate.get("_rules_port_attached"):
            from rules_port.context import EffectContext
            bstate.pop("_rules_port_deck_out_owner", None)
            context = EffectContext.from_rules_port(
                game, session, db._db, self, draw_pl_t, ai_t, bstate,
                "", ability=None)
            _leaves_players.draw(context, 1, owner=owner_id)
            if is_pvp:
                from services.tournament_game import pvp_save_state
                pvp_save_state(session, bstate)
            else:
                _be.save_state(session, bstate)
            return bool(bstate.pop("_rules_port_deck_out_owner", None))
        turn = bstate.get("turn_number", 1)
        if bstate.get("player_draws_turn") != turn:
            bstate["player_draws_this_turn"] = 0
            bstate["player_draws_turn"] = turn
        bstate["player_draws_this_turn"] = int(bstate.get("player_draws_this_turn", 0)) + 1
        # Replacement: "If you would draw a card..." (The Transcended) and
        # "If this would enter a hand..." (Booby Trap) — a resolved trigger
        # replaces the draw with its own effect.
        repl_draw = self._dispatch_native_trigger(
            game, session, draw_pl_t, ai_t, bstate,
            "CardWouldBeDrawnEvent", None, owner_id)
        row = db_deck_top_card_details(
        session.session_id, owner_id, conn=db._db)
        if not row:
            if is_pvp:
                log_req(f"    PvP debug draw: pid {owner_id} deck empty")
                return False
            # Deck-out: a player who must draw with an empty deck loses.
            self._finish_battle_gameend(
                session, False, winners=[ai_t], losers=[pl_t])
            log_req("    Game over: player deck empty on draw (AI wins)")
            return True
        repl_zone = self._dispatch_native_trigger(
            game, session, draw_pl_t, ai_t, bstate,
            "CardWouldEnterZoneEvent", row[1], owner_id)
        if repl_draw or repl_zone:
            if is_pvp:
                from services.tournament_game import pvp_save_state
                pvp_save_state(session, bstate)
            else:
                _be.save_state(session, bstate)
            log_req(f"    Player draw replaced (would-draw/would-enter trigger)")
            return
        scid = game_engine.SessionCardId(game_engine.UID(row[1]))
        db_move_card_row_to_hand(session.session_id, row[0], conn=db._db)
        db._db.commit()
        tpl_guid, ct, name, cost, atk, def_, gem_type = self._card_full_data(game, scid, row[3], row[2])
        game.push_card_drawn(scid, draw_pl_t, 1)
        game.push_card_updated(scid, draw_pl_t, game_engine.ECardCollections.Hand, ct,
                               attack=atk, defense=def_, cost=cost,
                               template_id=tpl_guid, gems=gem_type)
        # Entering a hand is its own authoritative zone event.  This is
        # distinct from CardDrawnEvent: cards such as Reginald trigger from
        # entering Hand even when the card was put there by an effect rather
        # than a normal draw.  The source owner is the actual deck owner, so
        # the trigger follows the card if control of the deck changed.
        self._dispatch_native_trigger(
            game, session, draw_pl_t, ai_t, bstate,
            "CardEnteredZoneEvent", row[1], owner_id,
            event_source_collection="deck",
            event_destination_collection="hand")
        # Fire "when you draw" triggers.  The client's CardDrawnEvent carries
        # SourceCardId = the drawing CHAMPION and TargetCardId = the drawn
        # card, and the trigger conditions test them per m_TriggerTest (e.g.
        # Twisted Fate's "when you draw a card, bury..." checks IsHero on the
        # source and IsType on the target).
        champ_uid = None
        if bstate.get("pvp"):
            for pid, cuid in (bstate.get("champ_map") or {}).items():
                if int(pid) == owner_id:
                    champ_uid = int(cuid)
                    break
        else:
            champ_scid = (getattr(self, "_player_champ_scid", None)
                          if owner_id else getattr(self, "_ai_champ_scid", None))
            champ_uid = (int(champ_scid.uid.uid64)
                         if champ_scid is not None else None)
        self._dispatch_native_trigger(
            game, session, draw_pl_t, ai_t, bstate,
            "CardDrawnEvent", champ_uid, owner_id,
            target_card_id=row[1])
        if is_pvp:
            from services.tournament_game import pvp_save_state
            pvp_save_state(session, bstate)
        else:
            _be.save_state(session, bstate)
        log_req(f"    Player drew card {row[1]} ({name})")

    def _rules_port_deck_out(self, game, session, owner_id, pl_t, ai_t,
                             battle_state):
        """Publish the mode-specific result for a native deck-out rule."""
        import hconnect_server as _hc
        owner_id = int(owner_id or 0)
        if battle_state.get("pvp"):
            from services.tournament_game import _pvp_end_game
            pids = [int(pid) for pid in (battle_state.get("pids") or ())]
            if len(pids) >= 2:
                loser = owner_id
                winner = next((pid for pid in pids if pid != loser), None)
                if winner is not None:
                    _pvp_end_game(session, battle_state, winner, loser,
                                  reason="deck-out")
            return True
        if owner_id:
            self._finish_battle_gameend(
                session, False, winners=[ai_t], losers=[pl_t])
            log_req("    Game over: player deck empty on native draw")
        else:
            self._finish_battle_gameend(
                session, True, winners=[pl_t], losers=[ai_t])
            log_req("    Game over: AI deck empty on native draw")
        return True

    def _ai_play_resource(self, game, session, ai_t, battle_state):
        import ai
        return ai.ai_play_resource(self, game, session, ai_t, battle_state)


    def _resolve_ai_mulligan(self, session, game, ai_t):
        import ai
        return ai.resolve_ai_mulligan(self, session, game, ai_t)

