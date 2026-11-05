from __future__ import annotations
from .runtime_helpers import as_int
import json
from dataclasses import dataclass, replace
from typing import Any
from .damage_effects import assign_damage_shield_order, serialized_damage
from rules_port.card_view import CardView
import rules_port.context as _context


class ZoneEffectsMixin:
    """Methods of EffectContext grouped by zones.

    Mixed into EffectContext; attribute access goes through self, so the
    class composition is unchanged.
    """

    def put_top_into_hand(self, target: int | None = None) -> str:
        """Put typed-count deck cards into the caster's hand."""
        import game_engine

        from rules_port.runtime_helpers import owner_uid, raw_uid

        count = self.value("m_InputValue", default=1)
        target = self.resolved_target() if target is None else target
        deck_owner = self.target_owner(
            target, default=self.bstate.get("resolving_owner_id", 0))
        if deck_owner is None:
            deck_owner = 0
        # C# defaults the receiving player to the deck's controller; the
        # authored m_AbilityOwnerTakesControl sends the cards to the ability
        # owner instead (Thought Collector, Relentless Corruption).
        if self.template_value("m_AbilityOwnerTakesControl", False):
            hand_owner = int(self.bstate.get("resolving_owner_id", 0) or 0)
        else:
            hand_owner = int(deck_owner)
        moved = 0
        for _ in range(max(0, int(count))):
            from pvp_db import db_deck_top_card_details, db_draw_card_to_hand
            row = db_deck_top_card_details(
                self.session.session_id, deck_owner, conn=self.db)
            if not row:
                break
            scid = game_engine.SessionCardId(game_engine.UID(row[1]))
            db_draw_card_to_hand(
                self.session.session_id, row[0], hand_owner, conn=self.db)
            self.db.commit()
            # ``MovedCards`` is the typed list used by follow-up effect
            # targets such as ``SourceBuriedTargetTemplate(MovedCards)``.
            # PutTopOfDeckIntoHand is a zone move, not an ordinary DrawCard
            # operation, so it must record the moved instance explicitly;
            # otherwise a subsequent CardModifier silently resolves against
            # an empty target list.  Renner's "thresholds become Blood"
            # effect is one authored example of this continuation.
            from rules_port.statistics import record_ability_card_list
            record_ability_card_list(self.bstate, "MovedCards", int(row[1]))
            tpl_guid, ct, _name, cost, atk, defense, gem = \
                self.handler._card_full_data(
                    self.game, scid, row[3], row[2])
            owner = owner_uid(hand_owner, self.player_uid, self.ai_uid,
                              self.bstate)
            self.game.push_card_updated(
                scid, owner, game_engine.ECardCollections.Hand, ct,
                attack=atk, defense=defense, cost=cost,
                template_id=tpl_guid, gems=gem,
                nulling=raw_uid(owner) != raw_uid(self.player_uid))
            # The client derives CardMoved from the collection change in
            # CardUpdated; an explicit class-50 event duplicates that move.
            self.game.push_card_drawn(scid, owner, 1)
            if self.template_value("m_Bloodwash", False):
                # Session.ConvertThresholds(card, Blood): the moved card's
                # full threshold requirement becomes Blood.
                card_uid = int(row[1])
                thresholds = self._card_thresholds(card_uid)
                if thresholds:
                    blood = int(game_engine.SHARD_TO_FLAG["blood"])
                    buffs = self._card_buffs(card_uid)
                    buffs["thresholds"] = [blood] * len(thresholds)
                    self._save_card_buffs(card_uid, buffs)
                    self._push_modifier_card(
                        card_uid, thresholds=list(buffs["thresholds"]))
            moved += 1
        return f"put {moved} deck card(s) into hand"

    def bury(self, target: int | None = None) -> str:
        """Move the typed number of cards from a deck into its discard."""
        import game_engine

        from pvp_db import db_discard_card
        from rules_port.runtime_helpers import owner_uid
        target = self.resolved_target() if target is None else target
        deck_owner = self.target_owner(
            target, default=self.bstate.get("resolving_owner_id", 0))
        if deck_owner is None:
            deck_owner = 0
        discard_owner = owner_uid(deck_owner, self.player_uid, self.ai_uid,
                                  self.bstate)
        # C# BuryCardAbilityEffectTemplate: TopHalfOfDeck and Filter are
        # mutually exclusive authored count sources alongside m_Amount.
        count = self.value("m_Amount", default=1)
        if self.template_value("m_TopHalfOfDeck", False):
            from pvp_db import db_deck_card_count
            count = (int(db_deck_card_count(
                self.session.session_id, deck_owner, conn=self.db) or 0) + 1) // 2
        elif self.template_value("m_Filter", None):
            count = 0
            from rules_port.filters import records_filter_matches
            source = CardView.bury_source(
                self.bstate.get("resolving_source_uid"), deck_owner).as_shape(
                    "bury_source")
            spec = self.template_value("m_Filter", {})
            deck_rows = self.db.execute(
                "SELECT card_uid FROM game_cards WHERE session_id=? "
                "AND user_id=? AND location='deck' ORDER BY position",
                (self.session.session_id, int(deck_owner or 0))).fetchall()
            for (uid,) in deck_rows:
                count += 1
                from pvp_db import db_condition_card_row
                row = db_condition_card_row(
                    self.session.session_id, int(uid), conn=self.db)
                if not row:
                    continue
                card = CardView.from_bury_row(row).as_shape("bury_card")
                if records_filter_matches(
                        card, spec, source=source,
                        context=dict(self.bstate or {})):
                    break
        # Session.GetBuryBonusForPlayer: every other player's champion
        # BuryBonus adds to the buried count.
        from rules_port.static_rules import _champion_int_attrs
        bonus = 0
        for opponent in (self.bstate.get("champ_map") or {}):
            try:
                opponent_owner = int(opponent)
            except (TypeError, ValueError):
                continue
            if opponent_owner == int(deck_owner or 0):
                continue
            for name, value in _champion_int_attrs(
                    self.bstate, opponent_owner).items():
                if str(name).lower() == "burybonus":
                    bonus += int(value or 0)
        count = int(count or 0) + bonus
        total = 0
        for _ in range(max(0, int(count))):
            from pvp_db import db_deck_top_card_details
            top = db_deck_top_card_details(
                self.session.session_id, deck_owner, conn=self.db)
            row = (top[1], top[3]) if top else None
            if not row:
                break
            scid = game_engine.SessionCardId(game_engine.UID(row[0]))
            db_discard_card(self.session.session_id, row[0],
                            connection=self.db)
            _tpl, ct, _name, cost, atk, defense, gem = \
                self.handler._card_full_data(
                    self.game, scid, row[1])
            self.game.push_card_updated(
                scid, discard_owner, game_engine.ECardCollections.Discard,
                ct, template_id=row[1], attack=atk, defense=defense,
                cost=cost, gems=gem)
            # C# BuryCard is MoveCardWithDispatch to Discard: exit, enter and
            # hidden enter. It never calls DiscardCard, so no CardDiscardedEvent.
            self._emit_trigger(
                "CardExitedZoneEvent", int(row[0]), deck_owner,
                event_source_collection="deck",
                event_destination_collection="discard")
            self._emit_trigger(
                "CardEnteredZoneEvent", int(row[0]), deck_owner,
                event_source_collection="deck",
                event_destination_collection="discard",
                event_previous_state=0)
            self._emit_trigger(
                "HiddenCardEnteredZoneEvent", int(row[0]), deck_owner,
                event_source_collection="deck",
                event_destination_collection="discard",
                event_previous_state=0)
            from rules_port.statistics import record_ability_card_list
            record_ability_card_list(self.bstate, "BuriedCards", int(row[0]))
            total += 1
        return f"bury {total} cards"

    def void_card(self, target: int | None = None) -> str:
        """Move a resolved card to Void and publish its exit relationship."""
        import game_engine

        from rules_port.runtime_helpers import owner_uid

        target = self.target() if target is None else target
        if target is None:
            # The authored target is a player-chosen card ("up to three troops
            # in a crypt").  With no selection, do not fall back to the source:
            # Moldmass voided itself when the crypt choice was empty.
            target = self.resolved_target(source_fallback=False)
        if target is None:
            return "void: no target"
        target = int(target)
        from pvp_db import db_card_owner_id, db_card_zone_details, db_void_card
        card_owner = db_card_owner_id(
            self.session.session_id, target, conn=self.db)
        if card_owner is None:
            return "void: target not found"
        owner = owner_uid(card_owner, self.player_uid, self.ai_uid, self.bstate)
        # Read the source zone before the void overwrites it.
        details = db_card_zone_details(
            self.session.session_id, target, conn=self.db)
        source_zone = str(details[3] or "").lower() if details else None
        db_void_card(self.session.session_id, target, conn=self.db)
        self.db.commit()
        scid = game_engine.SessionCardId(game_engine.UID(target))
        tpl_guid = details[0] if details else None
        _tpl, ct, _name, cost, atk, defense, gem = \
            self.handler._card_full_data(self.game, scid, tpl_guid)
        self.game.push_card_updated(
            scid, owner, game_engine.ECardCollections.Void, ct,
            template_id=tpl_guid, attack=atk, defense=defense,
            cost=cost, gems=gem)
        # C# AuthoritativeSessionBase.VoidCard moves the card with
        # MoveCardWithDispatch (exit, enter, hidden enter), then sends the
        # client a CardVoided session event for the voiding player. Unlike the
        # base Session.VoidCard it does not enqueue a CardVoidedEvent trigger.
        self._emit_trigger("CardExitedZoneEvent", target, card_owner,
                           event_source_collection=source_zone,
                           event_destination_collection="void")
        self._emit_trigger("CardEnteredZoneEvent", target, card_owner,
                           event_source_collection=source_zone,
                           event_destination_collection="void")
        self._emit_trigger("HiddenCardEnteredZoneEvent", target, card_owner,
                           event_source_collection=source_zone,
                           event_destination_collection="void")

        # The client's ``VoidCardAbilityEffectTemplate`` records the voided
        # card on the ability's ``VoidedCards`` list attr.  Follow-up operands
        # read it back through ``SumVariableInListAttrCardsAbilityVariable``
        # (Mentor of the Grave's charge power: "Void target troop in a crypt.
        # Then, gain health equal to the voided troop's [DEF]").  Only the
        # champion-TAC void path recorded it, so the typed leaf's follow-up
        # operand resolved to 0 and the ability healed nothing.
        from rules_port.statistics import record_ability_card_list
        record_ability_card_list(self.bstate, "VoidedCards", target)
        voiding_owner = self.bstate.get("resolving_owner_id", card_owner)
        self.game.push_card_voided(
            scid, owner_uid(voiding_owner, self.player_uid, self.ai_uid,
                            self.bstate))

        source = self.bstate.get("resolving_source_uid")
        if source is not None:
            self.bstate.setdefault("voided_by", {}).setdefault(
                str(int(source)), []).append(target)
            source_details = db_card_zone_details(
                self.session.session_id, int(source), conn=self.db)
            source_row = ((source_details[0], source_details[2], source_details[3])
                          if source_details else None)
            if source_row and source_row[2] == "warzone":
                source_scid = game_engine.SessionCardId(
                    game_engine.UID(int(source)))
                _tpl2, ct2, _name2, cost2, atk2, defense2, gem2 = \
                    self.handler._card_full_data(
                        self.game, source_scid, source_row[0])
                source_owner = owner_uid(
                    source_row[1], self.player_uid, self.ai_uid, self.bstate)
                self.game.push_card_updated(
                    source_scid, source_owner,
                    game_engine.ECardCollections.Warzone, ct2,
                    template_id=source_row[0], attack=atk2,
                    defense=defense2, cost=cost2, gems=gem2,
                    related_cards=[scid])
        return f"voided {hex(target)}"

    def discard(self, target: int | None = None) -> str:
        """Move a hand/choosing card to discard and publish its projections."""
        import game_engine

        # The discarded card is always a card the resolver selected.  Never
        # fall back to the ability source: the source is what caused the
        # discard, not a card to discard, and the fallback silently discarded
        # nothing (or the wrong card) instead of opening the picker.
        inherited = target is None
        target = (self.resolved_target(source_fallback=False)
                  if target is None else target)
        discarder = None
        if inherited and target is not None:
            # A child "discard a card" inherits its parent's resolved target
            # aliases.  When the parent targeted a champion ("target champion
            # discards a card", Concubunny Yuka) that champion is who
            # discards, not the card to discard: it has no game_cards row, so
            # the leaf silently no-oped instead of asking the champion's
            # controller.  Only a hand/choosing card is a discard target.
            # PvP owns its controller prompt in the projection post-step.
            from pvp_db import db_card_location
            location = str(db_card_location(
                self.session.session_id, int(target), conn=self.db) or "")
            if location.lower() not in ("hand", "choosing"):
                # The champion is the player who discards: its hand holds the card, whoever controls the ability.
                from pvp_db import db_card_owner_id
                discarder = db_card_owner_id(
                    self.session.session_id, int(target), conn=self.db)
                target = None
        if target is None:
            # A hand-discard effect is an asynchronous target request, not a
            # successful no-op.  The old BOM walker recognized this before
            # dispatching the leaf; native resolution must create the same
            # typed continuation so the parent AbilityInstance can resume at
            # the following effect after the client picks a card.
            prompt = getattr(self.handler, "_push_discard_prompt", None)
            if callable(prompt):
                # Resume *at* this effect, not after it: the first pass
                # stopped before its mutation, so the continuation must
                # re-enter the same effect with the player's card now
                # bound as the resolved target.
                resume_order = int(
                    self.bstate.get("resolving_effect_order", 0) or 0)
                self.bstate["rules_port_resume_effect_order"] = resume_order
                pending = self.continuation(
                    resume_effect_order=resume_order)
                pending["instance_id"] = int(
                    getattr(self.ability, "instance_id", 1) or 1)
                self.bstate["pending_discard_continuation"] = pending
                if discarder is not None:
                    self.bstate["discard_controller"] = discarder
                try:
                    result = prompt(
                        self.game, self.session, self.player_uid,
                        self.ai_uid, self.bstate,
                        ability_guid=self.bstate.get("resolving_ability", ""))
                finally:
                    self.bstate.pop("discard_controller", None)
                # AI discard is completed synchronously by the host
                # projection; a human discard leaves the port paused.
                # A best-effort target with no legal cards is a resolved
                # no-op, however.  Do not leave the continuation marker
                # behind or pause the chain for a picker that has no
                # selectable target (Bloatcap with an empty hand).
                if ("ai_discarded_uid" not in self.bstate and
                        not self.bstate.get("pending_discard_ability")):
                    self.bstate.pop("pending_discard_continuation", None)
                    self.bstate.pop("pending_discard_continuation_instance_id",
                                    None)
                    self.bstate.pop("rules_port_resume_effect_order", None)
                    return "discard: no legal target"
                if "ai_discarded_uid" not in self.bstate:
                    self.bstate["resolution_paused"] = True
                return str(result or "discard: awaiting target")
            return "discard: no target"
        from pvp_db import db_card_zone_details, db_card_state_value
        details = db_card_zone_details(
            self.session.session_id, int(target), conn=self.db)
        row = ((details[0], details[1], details[2], details[3],
                db_card_state_value(self.session.session_id, int(target), conn=self.db))
               if details else None)
        if not row:
            return f"discard: target {hex(int(target))} not found"
        source_location = str(row[3]).lower()
        # C# Session.DiscardCard is a MoveCardWithDispatch to Discard from any
        # collection; only a card already in the crypt is a no-op.
        if source_location == "discard":
            return f"discard: target {hex(int(target))} is already in discard"
        if source_location == "warzone":
            # Leaving play drops the troop from combat, as C# DeactivateCard
            # does for any warzone exit.
            from rules_port.combat import remove_troop_from_combat
            remove_troop_from_combat(self.bstate, int(target))

        from db import db_discard_card
        from rules_port.runtime_helpers import owner_uid

        owner_id = db_discard_card(
            self.session.session_id, int(target), connection=self.db,
            extra_set="card_state=0, card_damage=0, temporary_buffs=?, "
                       "temporary_attributes=0",
            extra_params=["{}"])
        if owner_id is None:
            return f"discard: target {hex(int(target))} disappeared"
        scid = game_engine.SessionCardId(game_engine.UID(int(target)))
        owner = owner_uid(owner_id, self.player_uid, self.ai_uid, self.bstate)
        tpl_guid, ct, _name, cost, atk, defense, gem = \
            self.handler._card_full_data(
                self.game, scid, row[0], row[1])
        self.game.push_card_updated(
            scid, owner, game_engine.ECardCollections.Discard, ct,
            template_id=tpl_guid, attack=atk, defense=defense, cost=cost,
            gems=gem, state=0, nulling=(row[3] == "deck"))
        self.game.push_card_discarded(scid, owner)
        # C# AuthoritativeSessionBase.DiscardCard is MoveCardWithDispatch to
        # Discard, so the hand/choosing -> crypt transition raises the same
        # exit/enter triggers as any other zone move (Winter Moon, Artisanal
        # Cheesesmythe and Mentor of the Grave react to crypt entry).  Only
        # CardDiscardedEvent was published before, so those triggers never saw
        # an effect-driven discard.
        event_data = {
            "event_source_collection": row[3],
            "event_destination_collection": "discard",
            "event_previous_state": int(row[4] or 0),
        }
        self._emit_trigger("CardExitedZoneEvent", int(target), owner_id,
                           event_source_collection=row[3],
                           event_destination_collection="discard")
        self._emit_trigger("CardEnteredZoneEvent", int(target), owner_id,
                           **event_data)
        # Session.DiscardCard enqueues CardDiscardedEvent with the discarding
        # player's champion as source and the discarded card as target.
        from rules_port.runtime_helpers import champion_uid_for_owner
        dis_card = champion_uid_for_owner(
            self.handler, self.bstate, owner_id)
        from rules_port.tac import _tac_attr_hash
        event_tac = {
            _tac_attr_hash("DiscardedCards"): [{
                _tac_attr_hash("Id"): int(target) >> 8,
            }],
        }
        self._emit_trigger(
            "CardDiscardedEvent",
            int(dis_card) if dis_card is not None else int(target),
            owner_id, target_card_id=int(target), event_tac=event_tac,
            **event_data)
        from rules_port.statistics import record_ability_card_list
        record_ability_card_list(self.bstate, "DiscardedCards", int(target))
        return f"discarded {hex(int(target))}"

    def discard_or_sacrifice(self) -> str:
        """Apply the destination-dependent discard/sacrifice operation."""
        target = self.resolved_target()
        if target is None:
            return "discard or sacrifice: no target"
        from pvp_db import db_card_location
        location = db_card_location(
            self.session.session_id, int(target), conn=self.db)
        if location is None:
            return "discard or sacrifice: target not found"
        if location == "warzone":
            return self.sacrifice(target)
        if location == "hand":
            return self.discard(target)
        return f"discard or sacrifice: ignored {location}"

    def tunnel(self) -> str:
        """Move the resolved card underground and publish its representation.

        Tunnel is a real zone change in the original client, not a discard
        shortcut. Publish the normal exit/enter trigger pair as well as the
        destination card projection so Underground triggers remain metadata-
        driven.
        """
        # TunnelCard in the client always operates on the card resolved by
        # the active ability instance. Self-tunnel abilities (the common hand
        # activation) bind that target to ``resolving_source_uid``. The
        # legacy spell/modifier aliases can outlive a nested trigger, so they
        # must not redirect a tunnel into another player's card.
        source_uid = self.bstate.get("resolving_source_uid")
        target = self.target(default=source_uid)
        if target is None:
            return "tunnel: no target"
        from pvp_db import db_card_owner_zone_state, db_move_card_to_location
        row = db_card_owner_zone_state(
            self.session.session_id, int(target), conn=self.db)
        if not row:
            return "tunnel: target not found"
        source_owner = int(row[0] or 0)
        source_location = str(row[1] or "")
        db_move_card_to_location(
            self.session.session_id, int(target), "underground", conn=self.db)
        self.db.commit()
        from rules_port.zone_effects import project_card
        project_card(self, int(target), "underground")
        self._emit_trigger(
            "CardExitedZoneEvent", int(target), source_owner,
            event_source_collection=source_location,
            event_destination_collection="underground")
        self._emit_trigger(
            "CardEnteredZoneEvent", int(target), source_owner,
            event_source_collection=source_location,
            event_destination_collection="underground",
            event_previous_state=0)
        return f"tunneled {hex(int(target))}"

    def destroy_by_defense(self) -> str:
        """Destroy warzone troops that fail the authored defense survival roll."""
        from rules_port.destruction_effects import destroy_by_defense
        return destroy_by_defense(self)

    def return_to_hand(self) -> str:
        """Return the resolved card through the typed hand transition."""
        target = self.resolved_target()
        if target is None:
            target = self.bstate.get("resolving_source_uid")
        return self.move_card_to_hand(target)

    def move_card_to_hand(self, target: int | None = None) -> str:
        """Move one card to hand while retaining its current controller."""
        if target is None:
            return "return to hand: no target"
        target = int(target)
        from pvp_db import (db_card_owner_zone_state,
                            db_card_zone_details, db_move_card_for_effect,
                            db_card_state_value)
        old = db_card_owner_zone_state(
            self.session.session_id, target, conn=self.db)
        if not old:
            return "return to hand: target not found"
        old_location = str(old[1] or "").lower()
        old_state = int(old[2] or 0)
        db_move_card_for_effect(
            self.session.session_id, target, "hand", 100,
            0, conn=self.db)
        self.db.commit()
        details = db_card_zone_details(
            self.session.session_id, target, conn=self.db)
        if not details:
            return "return to hand: target disappeared"
        from rules_port.runtime_helpers import owner_uid, raw_uid
        import game_engine
        scid = game_engine.SessionCardId(game_engine.UID(target))
        tpl, ct, _name, cost, attack, defense, gem = self.handler._card_full_data(
            self.game, scid, details[0])
        owner = owner_uid(details[2], self.player_uid, self.ai_uid, self.bstate)
        self.game.push_card_updated(
            scid, owner, game_engine.ECardCollections.Hand, ct,
            template_id=tpl, cost=cost, attack=attack, defense=defense,
            gems=gem, state=int(db_card_state_value(
                self.session.session_id, target, conn=self.db) or 0),
            nulling=raw_uid(owner) != raw_uid(self.player_uid))
        self._emit_trigger(
            "CardEnteredZoneEvent", target, int(details[2] or 0),
            event_source_collection=old_location,
            event_destination_collection="hand", event_previous_state=old_state)
        return f"returned {hex(target)} to hand"

    def finish_moving_to_warzone(self) -> str:
        """Complete a deferred move into Warzone through the typed path."""
        target = self.resolved_target() or self.bstate.get(
            "resolving_source_uid")
        return self._move_simple_zone(target, "warzone")

    def finish_resolving_card(self) -> str:
        """Finish a spell in the zone required by its current card type."""
        target = self.resolved_target()
        if target is None:
            target = self.bstate.get("resolving_source_uid")
        if target is None:
            return "finish resolving: no card"
        from .chain_items import finish_resolving_card
        return finish_resolving_card(
            self.handler, self.session, self.db, self.game, self.bstate,
            self.player_uid, self.ai_uid, int(target))

    def _move_simple_zone(self, target: int | None, destination: str,
                          *, label="move",
                          previous_owner_id: int | None = None) -> str:
        """Apply one ordinary zone transition and its typed event contract."""
        if target is None:
            return f"{label}: no card"
        target = int(target)
        destination = str(destination).lower()
        import game_engine
        from pvp_db import (db_card_owner_zone_state, db_card_zone_details,
                            db_card_state_value, db_move_card_for_effect)
        old = db_card_owner_zone_state(
            self.session.session_id, target, conn=self.db)
        if not old:
            return f"{label}: card not found"
        old_location, old_state = str(old[1] or "").lower(), int(old[2] or 0)
        position = 100 if destination == "hand" else 0
        # C# MoveCardToZone.InternalApply reads the destination deck's
        # CardCount before the card moves (TopHalfOfDeck draws against it).
        deck_count_before = 0
        if destination == "deck" and bool(self.template_value(
                "m_TopHalfOfDeck", False)):
            from pvp_db import db_deck_card_count
            deck_count_before = db_deck_card_count(
                self.session.session_id, int(old[0] or 0), conn=self.db)
        clear_dead = destination == "warzone"
        clear_bits = game_engine.ECardStates.Dead if clear_dead else 0
        new_state = old_state if destination == "warzone" else 0
        if destination == "warzone":
            # C# MoveCardToZone honors EntersPlayExhausted / EntersPlayAttacking.
            new_state |= game_engine.ECardStates.CameOutThisTurn
            from .static_rules import card_enters_play_exhausted
            card_row = db_card_zone_details(
                self.session.session_id, target, conn=self.db)
            if (self.template_value("m_EntersPlayExhausted", False) or
                    card_enters_play_exhausted(
                        self.db, self.session.session_id, self.bstate,
                        target, int(old[0] or 0),
                        card_row[0] if card_row else None)):
                new_state |= game_engine.ECardStates.Tapped
            if self.template_value("m_EntersPlayAttacking", False):
                new_state |= (game_engine.ECardStates.Attacking |
                              game_engine.ECardStates.HasAttacked)
        db_move_card_for_effect(
            self.session.session_id, target, destination, position,
            new_state,
            clear_dead=clear_dead, clear_bits=clear_bits, conn=self.db)
        if old_location == "warzone" and destination != "warzone":
            # C# ``Session.DeactivateCard`` also drops the troop from its combat
            # when an effect moves it out of play, so it cannot be treated as a
            # blocker (or attacker) after it returns.
            from rules_port.combat import remove_troop_from_combat
            remove_troop_from_combat(self.bstate, target)
        if destination == "deck":
            # Mirrors C# ``MoveCardToZoneEffectTemplate.InternalApply``:
            # - TopHalfOfDeck draws Next(deck count) and uses that as the
            #   random bound (the authored name says "top half", but the C#
            #   code bounds by the whole deck count).
            # - A positive bound draws Next(bound) for the index, which
            #   ``AuthoritativeSessionBase.MoveCardWithDispatch`` applies.
            # - Top/Bottom insert at that index counted from the chosen end
            #   (``CardCollection.AddCardAtIndexAndLocation``); Bottom with no
            #   bound is the last card.
            # - Unknown/other locations ignore the index and go to a uniform
            #   slot over the whole deck (``Session.FinishMovingCard``).
            # Leaving every deck move at position 0 put it on TOP, so a
            # following "draw N" returned the same cards.
            from pvp_db import (db_insert_deck_card_at,
                                db_randomly_insert_deck_cards, deck_rng_index)
            rng = self.bstate.get("_rules_rng")
            owner_id = int(old[0] or 0)
            dest_location = str(self.template_value(
                "m_DestinationLocation", "") or "").rsplit(".", 1)[-1].lower()
            random_location = int(self.template_value(
                "m_RandomLocation", -1) or -1)
            top_half = bool(self.template_value("m_TopHalfOfDeck", False))
            if dest_location in ("top", "bottom"):
                bound = random_location
                if top_half:
                    bound = deck_rng_index(rng, deck_count_before)
                index = deck_rng_index(rng, bound) if bound > 0 else 0
                db_insert_deck_card_at(
                    self.session.session_id, owner_id, target, index,
                    from_bottom=dest_location == "bottom", conn=self.db)
            else:
                db_randomly_insert_deck_cards(
                    self.session.session_id, owner_id, [target],
                    conn=self.db, rng=rng)
        self.db.commit()
        details = db_card_zone_details(
            self.session.session_id, target, conn=self.db)
        if not details:
            return f"{label}: card disappeared"
        from rules_port.runtime_helpers import owner_uid, card_collection_for_location
        scid = game_engine.SessionCardId(game_engine.UID(target))
        tpl, ct, _name, cost, attack, defense, gem = self.handler._card_full_data(
            self.game, scid, details[0])
        owner = owner_uid(details[2], self.player_uid, self.ai_uid, self.bstate)
        collection = card_collection_for_location(destination)
        self.game.push_card_updated(
            scid, owner, collection, ct, template_id=tpl, cost=cost,
            attack=attack, defense=defense, gems=gem,
            # A card put back into the deck is hidden (nulled) like any other
            # deck card; only the destination determines this.
            nulling=destination == "deck",
            state=int(db_card_state_value(
                self.session.session_id, target, conn=self.db) or 0))
        # The C# cache synthesizes class 50 only when collection/controller
        # changes. UIBattle.OnCardUpdated also moves the card view home, so a
        # positional-only reorder does not add a standalone class-50 event.
        if destination == "discard":
            self.game.push_card_discarded(scid, owner)
        event = "CardEnteredZoneEvent"
        self._emit_trigger(
            event, target, int(details[2] or 0),
            event_source_collection=old_location,
            event_destination_collection=destination,
            event_previous_state=old_state,
            # The authored conditions distinguish a card that was already
            # yours from one this move just took from another player.
            event_previous_owner_id=(int(old[0] or 0)
                                     if previous_owner_id is None
                                     else int(previous_owner_id)))
        from rules_port.statistics import record_ability_card_list
        record_ability_card_list(self.bstate, "MovedCards", target)
        if destination == "discard":
            self._emit_trigger(
                "CardDiscardedEvent", target, int(details[2] or 0),
                event_source_collection=old_location,
                event_destination_collection=destination,
                event_previous_state=old_state)
        return f"{label}: moved {hex(target)} to {destination}"

    def move_card_to_zone(self, target: int | None = None) -> str | None:
        """Handle ordinary typed MoveCardToZone destinations. ``target`` names the card explicitly (the interpreter's
        chosen or automatic target); without it the card is the resolver's target.

        Deck reinsertion, choice-zone cleanup, and authored multi-card moves
        are rejected as unsupported until their ordering contracts are ported.
        The native RulesPort resolver turns a ``None`` result into an explicit
        error; it never falls through to the legacy leaf dispatcher.
        """
        destination = self.template_value("m_DestinationCollection", "")
        if not destination and self.param:
            try:
                destination = json.loads(self.param).get("destination", "")
            except (TypeError, ValueError, json.JSONDecodeError):
                destination = ""
        destination = str(destination or "").rsplit(".", 1)[-1].lower()
        # Bane's generated move effect deliberately has no fixed destination:
        # "put the top card of your deck into #DESTINATION_ZONE#" means the
        # zone the Bane currently entered (Hand or Discard).  Resolve that
        # contract from the source card's authoritative zone and controller.
        if destination in ("", "none"):
            typed_name = str(
                self.template_value("m_Name", "") or "").lower()
            if typed_name == "putthetopcardofyourdeckintodestinationzone":
                source_uid = self.bstate.get("resolving_source_uid")
                if source_uid is None:
                    return "bane move: no source"
                from pvp_db import (db_card_owner_zone_state,
                                    db_deck_top_card)
                source_row = db_card_owner_zone_state(
                    self.session.session_id, int(source_uid), conn=self.db)
                if (not source_row or str(source_row[1]).lower()
                        not in ("hand", "discard")):
                    return "bane move: source is not in hand or discard"
                top_row = db_deck_top_card(
                    self.session.session_id, int(source_row[0]), conn=self.db)
                if not top_row:
                    return "bane move: deck empty"
                destination = str(source_row[1]).lower()
                self.bstate["resolving_target_uid"] = int(top_row[0])
        if destination == "playedresources":
            # Choice effects use a typed bulk transition: temporary cards in
            # the Choosing collection are consumed into PlayedResources
            # before the next choice prompt is created.  Keep this operation
            # native and metadata-driven; it must not enter the legacy BOM.
            from rules_port.choice_effects import _clear_choice_zone
            _clear_choice_zone(self)
            return "cleared choice zone"
        if destination not in {"hand", "discard", "void", "warzone",
                               "underground", "deck"}:
            return None
        if target is None:
            target = self.resolved_target()
        if target is None and destination == "underground":
            target = self.bstate.get("resolving_source_uid")
        previous_owner_id = None
        # Authored control transfer (m_AbilityOwnerTakesControl / etc.).  The
        # port previously ignored these, so "move to an opponent's zone" left
        # the card under its original controller.
        if target is not None:
            new_owner = self._move_zone_new_owner(int(target))
            if new_owner is not None:
                try:
                    from pvp_db import db_card_owner_id, db_set_card_owner
                    current = db_card_owner_id(
                        self.session.session_id, int(target), conn=self.db)
                    if current != new_owner:
                        # Remember the controller the card had before this
                        # move so the entry event can report it.
                        if current is not None:
                            previous_owner_id = int(current)
                        db_set_card_owner(
                            self.session.session_id, int(target), new_owner,
                            conn=self.db)
                        self.db.commit()
                except (TypeError, ValueError):
                    pass
        # m_AllCardsOfTargetInZone: move every card the target's controller
        # holds in the authored zone, not just the single resolved target.
        all_zone = self.template_value("m_AllCardsOfTargetInZone", "")
        all_zone = str(all_zone or "").rsplit(".", 1)[-1].lower()
        zone_map = {"hand": "hand", "warzone": "warzone", "deck": "deck",
                    "discard": "discard", "crypt": "discard",
                    "void": "void", "underground": "underground"}
        if all_zone in zone_map and target is not None:
            owner = self.target_owner(
                target, default=self.bstate.get("resolving_owner_id", 0))
            rows = self.db.execute(
                "SELECT card_uid FROM game_cards WHERE session_id=? "
                "AND user_id=? AND location=? ORDER BY position",
                (self.session.session_id, int(owner or 0),
                 zone_map[all_zone])).fetchall()
            count = 0
            for (uid,) in rows:
                if uid is None:
                    continue
                self._move_simple_zone(int(uid), destination)
                count += 1
            return f"moved all {count} {all_zone} cards to {destination}"
        moved = self._move_simple_zone(target, destination,
                                       previous_owner_id=previous_owner_id)
        # A card the owner looked at, then put into hand, is revealed to the
        # opponent (C# keeps a revealed card public while it stays in hand).
        if (destination == "hand" and target is not None and
                int(target) in {int(uid) for uid in
                                self.bstate.get("source_revealed_chosen") or ()}):
            from rules_port.reveal_effects import publish_revealed_card
            publish_revealed_card(self, int(target))
            self.bstate["source_revealed_chosen"] = [
                uid for uid in self.bstate.get("source_revealed_chosen") or ()
                if int(uid) != int(target)]
        return moved

    def _move_zone_new_owner(self, target: int) -> int | None:
        """Resolve the C# MoveCardToZone control-transfer flags to an owner."""
        resolving = int(self.bstate.get("resolving_owner_id", 0) or 0)
        if self.template_value("m_AbilityOwnerTakesControl", False):
            return resolving
        if self.template_value("m_AbilityOpponentTakesControl", False):
            profile = getattr(self.handler, "user_profile", None) or {}
            player_owner = int(profile.get("id", 0) or 0)
            return 0 if resolving else player_owner
        if self.template_value("m_ArenaChampionTakesControl", False):
            return 0
        if self.template_value("m_PreviousControllerTakesControl", False):
            # The card's persisted original owner is the controller it had
            # before any previous transfer ("return this to its previous
            # controller").
            from pvp_db import db_card_original_owner_id
            owner = db_card_original_owner_id(
                self.session.session_id, int(target), conn=self.db)
            return int(owner) if owner is not None else None
        # m_ControlGivenToTargetIndex: give control to the controller of the
        # card selected for that authored target index.
        control_index = int(self.template_value(
            "m_ControlGivenToTargetIndex", -1) or -1)
        if control_index >= 0:
            ability = getattr(self, "ability", None)
            target_map = getattr(getattr(ability, "activation", None),
                                 "target_map", {}) or {}
            uid = target_map.get(control_index, target_map.get(
                str(control_index)))
            values = uid if isinstance(uid, (list, tuple, set)) else (uid,)
            from pvp_db import db_card_owner_id
            for value in values or ():
                if value is None:
                    continue
                try:
                    owner = db_card_owner_id(
                        self.session.session_id, int(value), conn=self.db)
                    if owner is not None:
                        return int(owner)
                except (TypeError, ValueError):
                    continue
        return None

    def target_player_takes_control(self) -> str:
        """Transfer the resolving source to the target player's control."""
        source = self.bstate.get("resolving_source_uid")
        target = self.resolved_target()
        owner = self.target_owner(target, default=None)
        if source is None or owner is None:
            return "take control: missing source/target owner"
        from pvp_db import db_card_owner_zone_state, db_set_card_owner
        row = db_card_owner_zone_state(
            self.session.session_id, int(source), conn=self.db)
        if not row:
            return "take control: source not found"
        columns = {row[1] for row in self.db.execute(
            "PRAGMA table_info(game_cards)").fetchall()}
        if "owner_user_id" in columns:
            self.db.execute(
                "UPDATE game_cards SET user_id=?, owner_user_id=? "
                "WHERE session_id=? AND card_uid=?",
                (int(owner), int(owner), self.session.session_id, int(source)))
        else:
            db_set_card_owner(
                self.session.session_id, int(source), owner, conn=self.db)
        self.db.commit()
        from rules_port.zone_effects import project_card
        project_card(self, int(source), str(row[1]))
        return f"source control -> {int(owner)}"

    def steal_card(self) -> str:
        """Steal the top card of the target player's deck into responsibility hand."""
        target = self.resolved_target()
        target_owner = self.target_owner(target, default=None)
        thief = int(self.bstate.get("resolving_responsible_player_id",
                                    self.bstate.get("resolving_owner_id", 0)) or 0)
        if target_owner is None:
            return "steal: missing target owner"
        from pvp_db import db_deck_top_card_details, db_steal_card_to_hand
        top = db_deck_top_card_details(
            self.session.session_id, int(target_owner), conn=self.db)
        if not top:
            return "steal: target deck empty"
        card_uid = int(top[1])
        columns = {item[1] for item in self.db.execute(
            "PRAGMA table_info(game_cards)").fetchall()}
        if "owner_user_id" in columns:
            self.db.execute(
                "UPDATE game_cards SET user_id=?, owner_user_id=?, location='hand', "
                "position=100, card_state=0 WHERE session_id=? AND card_uid=?",
                (thief, thief, self.session.session_id, card_uid))
        else:
            db_steal_card_to_hand(
                self.session.session_id, card_uid, thief, conn=self.db)
        self.db.commit()
        from rules_port.zone_effects import project_card
        project_card(self, card_uid, "hand")
        return f"stole {hex(card_uid)}"

    def steal_effects(self) -> str:
        """Move runtime effect records from the target card to the source."""
        source = self.bstate.get("resolving_source_uid")
        target = self.resolved_target()
        if source is None or target is None:
            return "steal effects: missing source/target"
        moved = 0
        for column in ("permanent_buffs", "temporary_buffs"):
            source_buffs = self._card_buffs(int(source), column)
            target_buffs = self._card_buffs(int(target), column)
            for key in ("stat_modifiers", "rule_modifiers", "attribute_modifiers",
                        "cost_modifiers", "damage_shields"):
                values = target_buffs.pop(key, [])
                if not isinstance(values, list) or not values:
                    continue
                source_buffs.setdefault(key, []).extend(values)
                moved += len(values)
            self._save_card_buffs(int(target), target_buffs, column)
            self._save_card_buffs(int(source), source_buffs, column)
        self._push_modifier_card(int(source))
        self._push_modifier_card(int(target))
        return f"stole {moved} effect(s)"

    def revert_transformed_card(self) -> str:
        """Restore a transformed card's original template in its current zone."""
        target = self.resolved_target()
        if target is None:
            return "revert transform: no target"
        if self._cannot_revert(int(target)):
            return "revert transform: CantBeReverted"
        from rules_port.transform_effects import revert_instance
        return revert_instance(self, int(target))

    def _cannot_revert(self, target: int) -> bool:
        from .combat_rules import card_int_attr
        return card_int_attr(
            self.db, self.session.session_id, int(target),
            "CantRevert", battle_state=self.bstate) > 0

    def exchange_cards(self) -> str:
        """Exchange the typed number of deck cards between two controllers."""
        target_owner = self.target_owner(self.resolved_target(), default=None)
        player_one = int(self.bstate.get("resolving_responsible_player_id",
                                         self.bstate.get("resolving_owner_id", 0)) or 0)
        if target_owner is None:
            return "exchange: no target owner"
        amount = max(0, int(self.value("m_Amount", 0) or 0))
        if amount <= 0:
            return "exchange: zero cards"
        from pvp_db import db_deck_card_uids, db_set_card_owners
        def top(owner):
            return [int(row[0]) for row in db_deck_card_uids(
                self.session.session_id, int(owner), amount, conn=self.db)]
        first, second = top(player_one), top(int(target_owner))
        count = min(len(first), len(second))
        db_set_card_owners([
            (self.session.session_id, left, target_owner)
            for left, right in zip(first[:count], second[:count])
        ] + [
            (self.session.session_id, right, player_one)
            for left, right in zip(first[:count], second[:count])
        ], conn=self.db)
        self.db.commit()
        return f"exchanged {count} card(s)"

    def merge_card_collections(self) -> str:
        """Merge typed source collection into destination for target owner."""
        owner = self.target_owner(self.resolved_target(), default=
                                  self.bstate.get("resolving_owner_id", 0))
        source = str(self.template_value("m_SourceCollection", "")).rsplit(".", 1)[-1].lower()
        destination = str(self.template_value("m_DestinationCollection", "")).rsplit(".", 1)[-1].lower()
        loc = {"deck": "deck", "hand": "hand", "discard": "discard",
               "void": "void", "warzone": "warzone"}
        if owner is None or source not in loc or destination not in loc:
            return "merge collections: invalid metadata"
        from pvp_db import db_ordered_zone_uids, db_move_card_to_location
        rows = db_ordered_zone_uids(
            self.session.session_id, int(owner), loc[source], conn=self.db)
        for index, row in enumerate(rows):
            db_move_card_to_location(
                self.session.session_id, int(row[0]), loc[destination],
                position=index, conn=self.db)
        self.db.commit()
        return f"merged {len(rows)} card(s) {source}->{destination}"

    def shuffle_collection(self) -> str:
        """Shuffle the typed target controller collection and renumber slots."""
        target = self.resolved_target()
        owner = self.target_owner(target, default=self.bstate.get(
            "resolving_owner_id", 0))
        collection = self.template_value("m_Collection", "Deck")
        collection = str(collection).rsplit(".", 1)[-1].lower()
        location = {"deck": "deck", "hand": "hand", "discard": "discard",
                    "void": "void", "warzone": "warzone"}.get(collection)
        if owner is None or not location:
            return "shuffle: invalid collection"
        from pvp_db import db_ordered_zone_uids, db_set_card_positions
        rows = db_ordered_zone_uids(
            self.session.session_id, int(owner), location, conn=self.db)
        uids = [int(row[0]) for row in rows]
        from .runtime_helpers import rng_shuffle
        rng_shuffle(self.bstate, uids)
        db_set_card_positions(
            [(self.session.session_id, uid, index)
             for index, uid in enumerate(uids)], conn=self.db)
        self.db.commit()
        return f"shuffled {location} ({len(uids)})"

    def revert_modifications(self, target: int | None = None) -> str:
        """Restore a transformed target or clear its permanent modifiers."""
        target = self.resolved_target() if target is None else target
        if target is None:
            return "revert: no target"
        if self._cannot_revert(int(target)):
            return "revert: CantBeReverted"
        from pvp_db import db_card_original_and_template
        template_row = db_card_original_and_template(
            self.session.session_id, int(target), conn=self.db)
        if (template_row and template_row[0] and template_row[1] and
                str(template_row[0]).lower() != str(template_row[1]).lower()):
            from rules_port.transform_effects import revert_instance
            return revert_instance(self, int(target))
        from pvp_db import db_card_mutation_field, db_card_state_value
        raw_buffs = db_card_mutation_field(
            self.session.session_id, int(target), "permanent_buffs", conn=self.db)
        card_state = db_card_state_value(
            self.session.session_id, int(target), conn=self.db)
        row = (raw_buffs, card_state) if raw_buffs is not None else None
        try:
            data = json.loads((row[0] if row else "{}") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        if "atk" in data:
            data["atk"] = 0
        if "def" in data:
            data["def"] = 0
        # C# Card.Revert: counters, escalation, card variables, damage shields.
        for key in ("counters", "counter_guids", "escalation_count",
                    "card_integer_variables", "damage_shields"):
            data.pop(key, None)
        import game_engine
        from pvp_db import (
            db_card_ability_state, db_card_mutation_field,
            db_reset_card_modifiers, db_reset_card_uses,
            db_set_card_abilities, db_set_card_mutation_field,
            db_update_card_state)
        db_reset_card_modifiers(
            self.session.session_id, int(target), json.dumps(data), conn=self.db)
        # Keyword grants (Defensive, Flight ...) are written to card_attributes
        # rather than to the modifiers above, so restore the template's own
        # attributes as a transform revert does.
        if template_row and template_row[1]:
            from pvp_db import db_copy_template_payload
            base = db_copy_template_payload(str(template_row[1]).lower(), conn=self.db)
            if base:
                self.db.execute(
                    "UPDATE game_cards SET card_attributes=?, temporary_attributes=0 "
                    "WHERE session_id=? AND card_uid=?",
                    (int(base[2] or 0), self.session.session_id, int(target)))
        raw_temp = db_card_mutation_field(
            self.session.session_id, int(target), "temporary_buffs", conn=self.db)
        try:
            temp = json.loads(raw_temp or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            temp = {}
        if isinstance(temp, dict) and temp.pop("damage_shields", None) is not None:
            db_set_card_mutation_field(
                self.session.session_id, int(target), "temporary_buffs",
                json.dumps(temp), conn=self.db)
        # Champion counters and shields live in battle state, not game_cards.
        for key in ("champion_counters", "damage_shields"):
            store = self.bstate.get(key)
            if isinstance(store, dict):
                store.pop(str(int(target)), None)
        # Damage, the Damaged state bit, per-game/per-turn uses and cooldowns.
        db_update_card_state(
            self.session.session_id, int(target),
            clear_bits=int(game_engine.ECardStates.Damaged),
            reset_damage=True, conn=self.db)
        db_reset_card_uses(
            self.session.session_id, int(target),
            preserve_turn_and_cooldowns=False, conn=self.db)
        # Dynamic, revoked and shifted abilities: restore the authored list.
        ability_row = db_card_ability_state(
            self.session.session_id, int(target), conn=self.db)
        if ability_row:
            try:
                authored = json.loads(ability_row[1] or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                authored = []
            if isinstance(authored, list):
                db_set_card_abilities(
                    self.session.session_id, int(target),
                    json.dumps([str(guid).lower() for guid in authored]),
                    conn=self.db)
        self.db.commit()
        if template_row and template_row[1]:
            # C# RevertCard follows Card.Revert with TransformCardBase, which
            # sends CardReverted even when the template is unchanged.
            self.game.push_card_reverted(
                game_engine.SessionCardId(game_engine.UID(int(target))),
                str(template_row[1]))
        from rules_port.card_projection import push_card_state
        push_card_state(self.game, self.session, self.db, self.handler,
                        self.player_uid, self.ai_uid, int(target),
                        int(row[1] or 0) if row else 0, self.bstate)
        return f"reverted {hex(int(target))}"

    def remove_from_combat(self, target: int | None = None) -> str:
        """Clear combat participation flags and publish the new card state."""
        target = self.target(
            default=(self.bstate.get("player_mod_target") or
                     self.bstate.get("resolving_source_uid"))) \
            if target is None else target
        if target is None:
            return "remove from combat: no target"
        import game_engine

        target = int(target)
        clear = (game_engine.ECardStates.Attacking |
                 game_engine.ECardStates.Blocking |
                 game_engine.ECardStates.HasAttacked |
                 game_engine.ECardStates.HasBlocked)
        from pvp_db import db_card_state_value, db_set_card_state_exact
        current_state = db_card_state_value(
            self.session.session_id, target, conn=self.db)
        if current_state is None:
            return "remove from combat: card not found"
        state = int(current_state or 0) & ~int(clear)
        db_set_card_state_exact(
            self.session.session_id, target, state, conn=self.db)
        self.db.commit()
        from rules_port.card_projection import push_card_state
        push_card_state(self.game, self.session, self.db, self.handler,
                        self.player_uid, self.ai_uid, target, state,
                        self.bstate)
        return f"removed {hex(target)} from combat"

    def sacrifice(self, target: int | None = None) -> str:
        """Sacrifice one resolved card through the normal death/trigger path."""
        # An optional sacrifice with no legal card must not fall back to the
        # effect source: "sacrifice another troop" would otherwise sacrifice
        # its own source when the target pool is empty.
        target = (self.resolved_target(source_fallback=False)
                  if target is None else target)
        if target is None:
            return "sacrifice: no target"
        from rules_port.death_effects import kill_troop
        return kill_troop(self, target, cause="sacrifice")

    def destroy(self, target: int | None = None) -> str:
        """Destroy a resolved card or champion through the normal death path."""
        target = self.resolved_target() if target is None else target
        import db as db_layer
        db_layer.log_req(
            "    Destroy: target=%s keys=%s" % (
                hex(int(target)) if target is not None else None,
                {key: self.bstate.get(key) for key in (
                    "player_spell_target", "player_mod_target", "resolving_target_uid",
                    "resolving_source_uid")}))
        if target is None:
            return "destroy: no target"
        target = int(target)
        from pvp_db import db_card_location
        card_row = db_card_location(self.session.session_id, target, conn=self.db)
        target_owner = None
        is_champion = False
        for entry in (getattr(self.handler, "_champion_targets", lambda: [])()
                      or ()):
            try:
                if int(entry[0]) == target:
                    target_owner = int(entry[1])
                    is_champion = True
                    break
            except (IndexError, TypeError, ValueError):
                continue
        if not is_champion:
            row = self.db.execute(
                "SELECT user_id FROM game_cards WHERE session_id=? "
                "AND card_uid=? AND (is_champion=1 OR card_type='Champion')",
                (self.session.session_id, target)).fetchone()
            if row is not None:
                target_owner = int(row[0] or 0)
                is_champion = True
        if is_champion and self.bstate.get("pvp"):
            # HCPHandler._champion_targets labels the opposing champion with
            # the PvE synthetic owner 0. PvP must use persisted participant IDs
            # from champ_map/player order instead.
            from .runtime_helpers import controller_id_for_target

            target_owner = controller_id_for_target(
                self.db, self.session, self.handler, self.bstate, target)
        if is_champion or card_row is None:
            from .runtime_helpers import controller_id_for_target

            if target_owner is None:
                target_owner = controller_id_for_target(
                    self.db, self.session, self.handler, self.bstate, target)
            if target_owner is not None:
                from .death_effects import champion_would_lose

                if champion_would_lose(
                        self, target_owner, deduplicate=False):
                    # C# Session.DestroyCard delegates champions to
                    # Session.LoseGame, whose replacement check can preserve
                    # the player. The destroy effect still resolves, but the
                    # match stays active when a replacement is accepted.
                    return f"champion defeat replaced {hex(target)}"
                if self.bstate.get("pvp"):
                    from services.tournament_game import _pvp_end_game

                    pids = [int(pid) for pid in
                            (self.bstate.get("pids") or [])]
                    winner = next(
                        (pid for pid in pids if pid != target_owner), None)
                    if winner is not None:
                        _pvp_end_game(
                            self.session, self.bstate, winner,
                            target_owner, "champion destroyed by card effect")
                        return f"destroyed champion {hex(target)}"
                else:
                    winner_uid = (self.player_uid if target_owner == 0
                                  else self.ai_uid)
                    loser_uid = (self.ai_uid if target_owner == 0
                                 else self.player_uid)
                    self.handler._finish_battle_gameend(
                        self.session, target_owner == 0,
                        winners=[winner_uid], losers=[loser_uid])
                    return f"destroyed champion {hex(target)}"

        from rules_port.death_effects import kill_troop
        return kill_troop(self, target, cause="effect")
