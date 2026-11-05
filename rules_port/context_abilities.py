from __future__ import annotations

from .runtime_helpers import as_int
import json
from dataclasses import dataclass, replace
from .damage_effects import assign_damage_shield_order, serialized_damage
from rules_port.card_view import CardView
import rules_port.context as _context
from rules_port.context_host import HostBase


class AbilityEffectsMixin(HostBase):
    """Methods of EffectContext grouped by abilities.

    Mixed into EffectContext; attribute access goes through self, so the
    class composition is unchanged.
    """

    def _emit_trigger(self, event_type, source_card_id, source_owner_id=None,
                      target_card_id=None, target_player_id=None, **data):
        """Emit one event through the active RulesPort trigger boundary."""
        from rules_port.triggers import dispatch_trigger
        return dispatch_trigger(
            self, str(event_type).rsplit(".", 1)[-1], source_card_id,
            source_owner_id, target_card_id,
            target_player_id=target_player_id, data=data)

    def _resolving_raw_json(self) -> str:
        """Return the active ability's authored raw JSON, preferring Records.

        Talent abilities (for example Fury's "gain two charges" PreGame power)
        and champion powers are authored in gamedata but are not materialized
        as rows in ``card_abilities_meta``.  Their typed variables
        (``m_Variables``) must therefore be read from the live Records graph,
        not from the DB raw-json fallback that only covers card abilities.
        """
        graph = self._ability_graph()
        source = getattr(graph, "source", None)
        to_dict = getattr(source, "to_dict", None)
        if callable(to_dict):
            try:
                return json.dumps(to_dict())
            except (TypeError, ValueError):
                pass
        raw_attr = getattr(source, "raw", None)
        if isinstance(raw_attr, dict):
            try:
                return json.dumps(raw_attr)
            except (TypeError, ValueError):
                pass
        return ""

    def _ability_graph(self):
        """Return the Records graph for the resolving ability, if available.

        Runtime ability instances carry their graph in metadata. Projected
        chain abilities carry only an ability GUID, so resolve those through
        the shared Records store as well. This keeps typed AbilityConstants
        available for both projected PvE and PvP activations.
        """
        graph = getattr(getattr(self.ability, "metadata", None), "graph", None)
        if graph is not None:
            return graph
        guid = self.ability_guid
        if not guid:
            return None
        from gamedata import DEFAULT_RECORD_STORE, ability_graph
        return ability_graph(DEFAULT_RECORD_STORE, guid)

    @staticmethod
    def _ability_has_keyword(ability_guid, keyword) -> bool:
        """Whether one authored ability's TAC carries the named keyword."""
        from gamedata import DEFAULT_RECORD_STORE, ability_graph
        graph = ability_graph(
            DEFAULT_RECORD_STORE, str(ability_guid or "").lower())
        if graph is None:
            return False
        try:
            from .tac import _tac_attr_hash, decode_tac
            data = str(getattr(graph, "serialized_tac", "") or "")
            return bool(data and _tac_attr_hash(str(keyword))
                        in decode_tac(data))
        except (TypeError, ValueError, json.JSONDecodeError):
            return False

    def register_trigger(self) -> str:
        """Register a typed trigger template for this battle instance."""
        target = self.resolved_target()
        template_id = self.template_value(
            "m_TriggerAbilityTemplateId", "") or ""
        if target is None or not template_id:
            return "register trigger: missing target or template"
        values = self.bstate.setdefault("registered_triggers", {}).setdefault(
            str(int(target)), [])
        if template_id not in values:
            values.append(template_id)
        return f"registered trigger {template_id[:8]} on {hex(int(target))}"

    def revoke_ability(self) -> str:
        """Remove one granted ability and publish the resulting card state."""
        target = self.resolved_target()
        if target is None:
            return "revoke: no target"
        # The authoritative id lives on the effect template
        # (``m_RevokedAbilityTemplateId``); older rows carried it only in the
        # adapter payload, and several abilities revoke themselves.
        revoked = str(self.template_value(
            "m_RevokedAbilityTemplateId", "") or "").strip().lower()
        if not revoked or revoked == "0" * 36:
            revoked = (self.param or self.ability_guid or "").strip().lower()
        if not revoked:
            return "revoke: no ability guid in param"
        from pvp_db import db_card_abilities_state, db_set_card_abilities
        row = db_card_abilities_state(
            self.session.session_id, int(target), conn=self.db)
        if not row:
            return "revoke: target not found"
        try:
            abilities = json.loads(row[0] or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            abilities = []
        if revoked in abilities:
            abilities.remove(revoked)
            db_set_card_abilities(
                self.session.session_id, int(target), json.dumps(abilities),
                conn=self.db)
            self.db.commit()
        from rules_port.card_projection import push_card_state
        push_card_state(self.game, self.session, self.db, self.handler,
                        self.player_uid, self.ai_uid, int(target),
                        int(row[1] or 0), self.bstate)
        return f"revoked {revoked[:8]} from {hex(int(target))}"

    def queue_bonus_turn(self) -> str:
        """Queue the resolving controller's next turn."""
        owner = int(self.bstate.get("resolving_owner_id", 0) or 0)
        side = "player" if owner else "ai"
        self.bstate["bonus_turn"] = side
        # PvP consumes the owning player id at the turn boundary; Practice
        # still uses the side marker above as its authoritative value.
        self.bstate["bonus_turn_pid"] = owner
        return f"bonus turn queued for {side}"

    def activate_triggered(self) -> str:
        """Activate a typed keyword trigger on the resolved target."""
        keyword = self.template_value("m_Keyword", "")
        target = self.resolved_target()
        if target is None:
            return "activate triggered: no target"
        from pvp_db import db_card_ability_payload, db_card_owner_id
        from rules_port.resolution import resolve_port_ability
        from rules_port.trigger_discovery import ability_matches_keyword
        try:
            abilities = json.loads(db_card_ability_payload(
                self.session.session_id, int(target), conn=self.db) or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            abilities = []
        owner = int(db_card_owner_id(
            self.session.session_id, int(target), conn=self.db) or
            self.target_owner(target, default=0) or 0)
        # Session.ManuallyTriggerAbilities guards: an opposing
        # OpposingDeploysCantTrigger/OpposingDeathcriesCantTrigger lockout
        # suppresses the manual keyword activation, and a Deathcry that
        # also carries Rebirth is skipped (the card is returning, not
        # dying).  Darkspire Enforcer/Elite is the FRA user of this leaf.
        from .triggers import (_opponents_block_deathcries,
                               opponents_have_int_attr)
        keyword_key = str(keyword or "").rsplit(".", 1)[-1].lower()
        if (keyword_key == "deathcry" and
                _opponents_block_deathcries(self.bstate, owner)):
            return "activated deathcry: blocked by opposing champion"
        if (keyword_key == "deploy" and
                opponents_have_int_attr(
                    self.bstate, owner, "OpposingDeploysCantTrigger")):
            return "activated deploy: blocked by opposing champion"
        results = []
        for ability_guid in abilities:
            if not ability_matches_keyword(ability_guid, keyword):
                continue
            if (keyword_key == "deathcry" and
                    self._ability_has_keyword(ability_guid, "Rebirth")):
                continue
            results.append(resolve_port_ability(
                self.handler, self.game, self.session, self.db,
                self.player_uid, self.ai_uid, self.bstate,
                str(ability_guid).lower(), int(target), owner,
                target_map={}))
        result = "; ".join(str(value) for value in results if value)
        return (f"activated {keyword} on {hex(int(target))}: {result}"
                if result else
                f"activated {keyword}: no matching ability")

    def counter_spell(self) -> str:
        """Resolve a counter-spell operation through the trigger boundary."""
        from rules_port.counter_effects import counter_spell
        return counter_spell(self)

    def _emit_authored_event(self, event_type: str,
                             target: int | None = None) -> str:
        """Dispatch one metadata-defined ability event.

        ``FireEventEffectTemplate`` is the authored bridge for keyword-like
        events such as Fateweave, Illuminate, Verdict, and Prophecy.  Keep
        the dispatch here so the effect uses the same trigger/condition/
        target machinery as ordinary game events in both PVE and PVP.
        """
        source = self.bstate.get("resolving_source_uid")
        if source is None:
            source = target
        if source is None:
            return f"{event_type}: no source"
        source = int(source)
        owner = self.target_owner(source, default=None)
        if owner is None:
            owner = int(self.bstate.get("resolving_owner_id", 0) or 0)
        result = self._emit_trigger(
            str(event_type).rsplit(".", 1)[-1], source, int(owner),
            target_card_id=(int(target) if target is not None else None))
        return f"fired {str(event_type).rsplit('.', 1)[-1]}" + (
            f": {result}" if result else "")

    def create_and_cast_spell(self):
        from rules_port.effects import create_and_cast_spell
        return create_and_cast_spell(self)

    def verdict(self) -> str:
        """VerdictAbilityEffectTemplate: build the good/bad choice tokens.

        The built-in Verdict card templates are real Records cards; the
        C# built-in pool GUIDs select which ones are eligible from the
        current board, the session RNG picks one of each, and the authored
        choose/play abilities drive the reveal.
        """
        import random

        import game_engine
        from pvp_db import (db_copy_template_payload, db_deck_card_count,
                            db_insert_generated_card,
                            db_next_game_card_row_id, db_template_exists)
        from .resolution import resolve_port_ability
        from .runtime_helpers import next_game_card_uid, owner_uid
        from .static_rules import champion_int_attribute

        sid = self.session.session_id
        responsible = int(self.bstate.get("resolving_owner_id", 0) or 0)
        target = self.resolved_target()
        owner = self.target_owner(target, default=None) if target is not None else None
        opponent = int(owner) if owner is not None else None
        if opponent is None or opponent == responsible:
            opponent = _context._opposing_owner_in(self.bstate, responsible,
                                          self.handler)
        ancient = champion_int_attribute(
            self.bstate, responsible, "VerdictAncient") > 0
        choice = champion_int_attribute(
            self.bstate, responsible, "VerdictChoice") > 0

        def count(owner, location, *likes):
            sql = ("SELECT COUNT(*) FROM game_cards WHERE session_id=? "
                   "AND user_id=? AND location=?")
            params = [sid, int(owner or 0), location]
            if likes:
                sql += " AND (" + " OR ".join(
                    "card_type LIKE ?" for _ in likes) + ")"
                params.extend(likes)
            return int(self.db.execute(sql, params).fetchone()[0] or 0)

        bad: list[str] = []
        good: list[str] = []
        if ancient:
            if count(opponent, "hand") > 0:
                bad.append("0c0f56cc-7e46-45ef-91a1-f7bc31b0f69a")
            if count(opponent, "warzone", "%Troop%") > 1:
                bad.append("19bce192-bc62-4887-b323-823b9a508a4e")
            bad.append("79c00493-c67f-4477-963a-b7690cad1170")
            if count(opponent, "warzone", "%Troop%") > 0:
                bad.append("2b746199-bdcd-4f92-972c-704a151caf46")
            if count(opponent, "warzone", "%Artifact%", "%Constant%") > 0:
                bad.append("3e068728-a452-4d04-be78-636a1c6ef9cb")
            if int(db_deck_card_count(sid, responsible,
                                      conn=self.db) or 0) > 2:
                good.append("f3f44752-0cb7-41e2-af5d-0f3c7b88d063")
            good += ["510548c7-33f8-4df7-9722-82fede018b9c",
                     "f5961355-4c7d-43df-bc36-2068b493214d"]
            if count(responsible, "warzone", "%Troop%") > 0:
                good.append("2034338d-ae06-46d8-80d5-a1e7479b3104")
            if count(responsible, "discard", "%Troop%") > 0:
                good.append("28688e83-57e9-41c2-8e16-45108f7216be")
        else:
            if count(opponent, "hand") > 0:
                bad.append("b0d107d8-5f8f-452d-b1fe-483aeb2c990a")
            if count(opponent, "warzone", "%Troop%") > 0:
                bad += ["a4f887e5-aa6c-4700-9e92-ec62530d7db7",
                        "15f91ee2-c839-4ba3-8d1e-df452ed734a8"]
            bad.append("1304d693-ba27-4bb2-9ccb-c3798bdb150b")
            if count(opponent, "warzone", "%Artifact%", "%Constant%") > 0:
                bad.append("4d7d9258-d574-4391-af6c-0631d9557d9d")
            if int(db_deck_card_count(sid, responsible,
                                      conn=self.db) or 0) > 0:
                good.append("29c9ada1-34aa-433f-89b6-20d7e94bea04")
            good += ["09846dd3-f9de-43bd-a342-9e7791724d3e",
                     "11c86363-860d-4f3f-9a0a-48173413bd1f"]
            if count(responsible, "warzone", "%Troop%") > 0:
                good.append("d9c898c8-116b-499a-b6bd-fb42e15ba0d3")
            if count(responsible, "discard", "%Troop%") > 0:
                good.append("ead003e9-c32d-4335-820b-3207f47d3df1")
        if not bad or not good:
            return "verdict: no choice cards"
        rng = self.bstate.get("_rules_rng")

        def pick(pool):
            if rng is not None and hasattr(rng, "next"):
                return pool[int(rng.next(0, len(pool))) % len(pool)]
            return random.choice(pool)

        templates = {"good": pick(good), "bad": pick(bad)}
        created: dict[str, int] = {}
        for key, guid in templates.items():
            if not db_template_exists(guid, conn=self.db):
                return f"verdict: missing template {guid}"
            payload = db_copy_template_payload(guid, conn=self.db)
            if not payload:
                return f"verdict: missing payload {guid}"
            uid = next_game_card_uid(self.db, sid)
            db_insert_generated_card(
                sid, opponent, uid, guid, "choosing", payload[0], payload[1],
                payload[2], db_next_game_card_row_id(sid, conn=self.db),
                conn=self.db, owner_user_id=opponent,
                original_template_guid=guid)
            created[key] = int(uid)
        self.db.commit()
        for key, guid in templates.items():
            uid = int(created[key])
            scid = game_engine.SessionCardId(game_engine.UID(uid))
            _tpl, ctype, _name, cost, atk, dfn, gems = \
                self.handler._card_full_data(self.game, scid, guid)
            recipient = owner_uid(
                opponent, self.player_uid, self.ai_uid, self.bstate)
            from .choice_effects import choice_art_related_cards
            self.game.push_card_updated(
                scid, recipient, game_engine.ECardCollections.Choosing,
                ctype, template_id=guid, cost=cost, attack=atk,
                defense=dfn, gems=gems,
                related_cards=choice_art_related_cards(
                    self.bstate.get("resolving_source_uid"), uid))
            # ClientStateCache creates the UI CardMoved from None -> Choosing
            # while applying CardUpdated; do not send a duplicate class 50.
        source_uid = self.bstate.get("resolving_source_uid")
        if choice:
            resolve_port_ability(
                self.handler, self.game, self.session, self.db,
                self.player_uid, self.ai_uid, self.bstate,
                "a38c4b86-e95f-1dec-60e0-d6d90f578ed8",
                source_uid, responsible, target_map={})
            return "verdict: opponent chooses"
        resolve_port_ability(
            self.handler, self.game, self.session, self.db,
            self.player_uid, self.ai_uid, self.bstate,
            "5f28daa1-7500-9f85-e688-fe542c1d04bf",
            source_uid, opponent, target_map={})
        return "verdict: reveal and play"

    def grant_ability(self):
        target = self.bstate.get("grant_target")
        if target is None:
            return "grant: no target"
        from rules_port.effects import grant_ability
        return grant_ability(self)

    def copy_ability(self):
        from rules_port.effects import _copy_ability
        return _copy_ability(self)

    def play_card(self):
        target = self.resolved_target()
        if target is None:
            target = self.bstate.get("resolving_source_uid")
        if target is None:
            return "play for free: no source"
        from pvp_db import db_card_zone_details, db_card_with_template
        row = db_card_zone_details(
            self.session.session_id, int(target), conn=self.db)
        if not row:
            return "play for free: target not found"
        card_row = db_card_with_template(
            self.session.session_id, int(target), conn=self.db)
        card_type = card_row[1] if card_row else ""
        if "choice" in str(card_type or "").lower():
            # A choice token is not a spell/permanent to put on the chain.
            # Playing it consumes the temporary token and resolves its
            # authored ability (for example Choose Blood's threshold gain).
            from rules_port.choice_effects import (
                _play_choice_card, _resolve_choice_card_abilities)
            owner_id = int(self.target_owner(
                target, default=self.bstate.get("resolving_owner_id", 0)) or 0)
            _play_choice_card(self, int(target), owner_id)
            _resolve_choice_card_abilities(
                self, int(target), self.bstate.get("resolving_source_uid"),
                owner_id)
            return f"played choice {int(target)} for free"
        if "resource" in str(card_type or "").lower():
            from rules_port.host_mutations import play_free_resource_card
            return play_free_resource_card(
                self.handler, self.game, self.session, self.db,
                self.player_uid, self.ai_uid, self.bstate, int(target))
        # The card being played becomes the play's source.  For the built-in
        # play-card ability the target already IS the source; for a free-play
        # effect (e.g. Nerissa's "play revealed troops for free") the revealed
        # troop is the source, not the champion that authored the ability.
        old_source = self.bstate.get("resolving_source_uid")
        self.bstate["resolving_source_uid"] = int(target)
        try:
            if int(target) != int(self.bstate.get(
                    "resolving_source_uid", target) or target):
                raise RuntimeError(
                    "RulesPort free-play target is not the ability source")
            from rules_port.host_mutations import queue_free_played_card
            return queue_free_played_card(
                self.handler, self.game, self.session, self.db,
                self.player_uid, self.ai_uid, self.bstate, int(target),
                int(self.target_owner(target,
                                      default=self.bstate.get("resolving_owner_id", 0))
                    or 0), row[0], card_type)
        finally:
            if old_source is None:
                self.bstate.pop("resolving_source_uid", None)
            else:
                self.bstate["resolving_source_uid"] = old_source

    def fire_event(self):
        event_type = self.template_value("m_TriggerType", "") or ""
        event_type = str(event_type).rsplit(".", 1)[-1]
        if event_type in ("", "GenericEvent"):
            # Older records use GenericEvent for the named Prophesied
            # operation.  The effect template name is the typed fallback.
            name = str(self.template_value("m_Name", "") or "")
            if name.lower().startswith("fire") and name.endswith("Event"):
                event_type = name[4:]
        if not event_type:
            return "fire event: no authored event type"
        # FireEventEffectTemplate.Apply receives the resolved effect target;
        # an untargeted effect passes null. Other leaves may use the source as
        # their implicit target, but this event's TargetCardId must preserve
        # the exact C# target identity (or the absence of one).
        return self._emit_authored_event(
            event_type, self.resolved_target(source_fallback=False))

    def inherit_child_lists(self, child_guid: str) -> None:
        """Seed an invoked child ability with the current instance's lists.

        C# ``Session.CreateAbility`` appends the parent ``AbilityInstance``
        into the new ability instance, so a child reached through
        ``ActivateAbility`` reads the authored lists (most importantly
        ``StoredTargets``) the parent recorded earlier in the activation.
        The child starts from that snapshot, exactly as a fresh instance
        built with ``Append(parent)`` would.
        """
        parent = str(self.ability_guid or "").lower()
        child = str(child_guid or "").lower()
        if not parent or not child or parent == child:
            return
        for key in ("stored_targets", "stored_targets_this_turn"):
            store = self.bstate.setdefault(key, {})
            store[child] = list(store.get(parent) or ())
        lists = self.bstate.setdefault("list_attrs", {})
        lists[child] = {
            str(name): list(entries or ())
            for name, entries in (lists.get(parent) or {}).items()
        }

    def _random_sack_ability(self):
        """Pick a Lucky/Unlucky ability for ``m_RandomlyLuckyOrUnlucky``.

        Port of ``TemplateManager``'s Lucky/Unlucky pools: the abilities of
        the cards named ``Luck Sack ...`` (or ``Unlucky Sack...`` when the
        source card carries the typed ``Unlucky`` IntAttr).  ``Luck Sack``
        itself is not in the pool because C# matches the space-suffixed
        prefix.
        """
        from .combat_rules import card_int_attr
        source = self.bstate.get("resolving_source_uid")
        unlucky = bool(source is not None and card_int_attr(
            self.db, self.session.session_id, int(source), "Unlucky",
            battle_state=self.bstate) > 0)
        prefix = "Unlucky Sack" if unlucky else "Luck Sack "
        from pvp_db import db_card_template_abilities_by_name_prefix
        pool = []
        for _guid, abilities_json in db_card_template_abilities_by_name_prefix(
                prefix, conn=self.db):
            try:
                values = json.loads(abilities_json or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            pool.extend(str(ability).lower() for ability in values if ability)
        if not pool:
            return ""
        rng = self.bstate.get("_rules_rng")
        if rng is not None and hasattr(rng, "next"):
            return pool[int(rng.next(len(pool))) % len(pool)]
        import random
        return pool[random.randrange(len(pool))]

    def _resolve_child_ability(self, child_guid, source_uid, owner_id,
                               target_map):
        """Resolve an activated child with the enclosing continuation exposed.

        The client's ``ActivateAbilityFromEffect`` keeps the invoking
        instance in the child's continuation chain.  The legacy resolver
        published the parent's continuation in ``_choice_parent`` so a nested
        conversation or choice could resume the enclosing ability; mirror that
        around the nested port lifecycle call and restore the caller's value.
        """
        from rules_port.resolution import resolve_port_ability
        previous = self.bstate.get("_choice_parent")
        self.bstate["_choice_parent"] = self.continuation()
        try:
            return resolve_port_ability(
                self.handler, self.game, self.session, self.db,
                self.player_uid, self.ai_uid, self.bstate, child_guid,
                source_uid, owner_id, target_map=target_map,
                # A child binds its own variables, as the client's ActivateAbility does.
                variables={})
        finally:
            if previous is None:
                self.bstate.pop("_choice_parent", None)
            else:
                self.bstate["_choice_parent"] = previous

    def _mark_paused_target(self, parent):
        """Note on the parent continuation that a picker interrupted an effect with targets still to run.

        The effect loop publishes the target it is running in ``rules_port_current_target``.  The picker's
        continuation is saved with the pick, so the parent can resume at the paused effect and skip the
        targets that already resolved.
        """
        current = self.bstate.get("rules_port_current_target")
        if not isinstance(current, dict) or not isinstance(parent, dict):
            return parent
        if str(current.get("ability_guid") or "").lower() != str(
                parent.get("ability_guid") or "").lower():
            return parent
        following = int(current.get("index", 0) or 0) + 1
        if following < int(current.get("count", 0) or 0):
            parent["rules_port_resume_target"] = {
                "ability_guid": str(current["ability_guid"]),
                "position": int(current["position"]), "index": following}
        return parent

    def activate_ability(self):
        """Enter a child ability through the common typed resolver.

        ``resolve_ability`` normally handles this effect before a leaf is
        invoked.  Retaining this path for direct/legacy callers is important,
        but flat-walking child BOM rows here would bypass child targets,
        conditions, continuations, and EffectContext injection.
        """
        child_guid = str(self.param or "").lower()
        try:
            lucky_or_unlucky = bool(
                self.template_value("m_RandomlyLuckyOrUnlucky", False))
        except AttributeError:
            # Lightweight resolver stubs do not implement template_value.
            lucky_or_unlucky = False
        if (lucky_or_unlucky or not child_guid or
                child_guid == "00000000-0000-0000-0000-000000000000"):
            chosen = self._random_sack_ability()
            if chosen:
                child_guid = chosen
        if not child_guid or child_guid == "00000000-0000-0000-0000-000000000000":
            return "activate ability: no child"
        source_uid = self.bstate.get("resolving_source_uid")
        owner_id = self.target_owner(
            self.resolved_target(),
            default=self.bstate.get("resolving_owner_id", 0))
        from db import log_req   # one line per child activation, so a child that did not run can be traced
        log_req(f"    ACTIVATE child {child_guid[:8]} owner={owner_id} "
                f"target={self.resolved_target()} source={source_uid} "
                f"parent={self.bstate.get('resolving_ability') or '-'}")
        self.inherit_child_lists(child_guid)
        # A number of Records abilities materialize several temporary
        # cards in Choosing and then ActivateAbility a typed child whose
        # target is "a card in the choice zone".  The summons are not
        # individual pauses; the child target is the single client picker
        # boundary.  Detect that contract from the child graph so every
        # such effect gets one picker containing all authored options.
        from gamedata import DEFAULT_RECORD_STORE, ability_graph
        from rules_port.targeting import (filter_restricts_to_zone,
                                          legal_targets)
        child = ability_graph(DEFAULT_RECORD_STORE, child_guid)
        choice_target = None
        if child is not None:
            for index, target in enumerate(child.targets):
                card_filter = getattr(target, "card_filter", None)
                to_dict = getattr(card_filter, "to_dict", None)
                if callable(to_dict):
                    card_filter = to_dict()
                # Only an authored ``InZone`` filter identifies a picker
                # target: the child's cards come from the choice zone, or
                # from the deck (the class-39 search prompt).  The
                # ``m_CollectionFlags`` visibility mask is a union of
                # every collection a card could occupy, so testing it
                # made an ordinary "a card from your hand" child
                # (Bloatcap's Deathcry, Giant Corpse Fly's Deploy) look
                # like a choice-zone picker and the discard never reached
                # its class-23 hand picker.
                from_choosing = filter_restricts_to_zone(
                    card_filter, "Choosing")
                from_deck = filter_restricts_to_zone(card_filter, "Deck")
                # A match-secondary target ("search target opponent's deck for a card") names its zones on the
                # template, not on a card filter: a chosen pick from a deck is the same deck pick.
                match_secondary = str(target.target_kind or "").endswith(
                    "MatchSecondaryTargetTemplate")
                if match_secondary:
                    from_choosing = False
                    from_deck = "deck" in str(target.collection_flags or "").lower()
                if (not target.requires_input or
                        not (match_secondary or str(target.target_kind or "").endswith(
                            "AbilityTargetTemplate")) or
                        not (from_choosing or from_deck)):
                    continue
                candidates = legal_targets(
                    self.db, self.session.session_id, int(owner_id or 0),
                    target.guid, source_uid,
                    both_players=str(target.player_filter or "").lower()
                    not in {"self", "you", "controller"},
                    champions=(getattr(
                        self.handler, "_champion_targets", lambda: [])()
                               or []),
                    battle_state=self.bstate)
                if match_secondary and candidates:
                    # "Search target opposing champion's deck": only the deck of the champion the ability stored
                    # (its owner), not every deck the template's player filter names.
                    stored = set()
                    for key in ("stored_targets", "stored_targets_this_turn", "stored_targets_by_card",
                                "stored_targets_by_card_this_turn"):
                        for values in (self.bstate.get(key) or {}).values():
                            for uid in (values if isinstance(values, (list, tuple)) else [values]):
                                if uid is not None:
                                    stored.add(int(uid))
                    champion_owner = {int(uid): int(owner) for owner, uid in
                                      (self.bstate.get("champ_map") or {}).items()}
                    owners = {champion_owner[uid] for uid in stored if uid in champion_owner}
                    if owners:
                        owner_rows = dict(self.db.execute(
                            "SELECT card_uid, user_id FROM game_cards WHERE session_id=? AND card_uid IN (%s)"
                            % ",".join("?" * len(candidates)),
                            (self.session.session_id, *[int(uid) for uid in candidates])).fetchall())
                        candidates = [uid for uid in candidates
                                      if int(owner_rows.get(int(uid), -1) or 0) in owners]
                if candidates:
                    choice_target = (
                        index, [int(uid) for uid in candidates], from_deck)
                break
        # A human player's child that picks a card ("target an action in your hand", "sacrifice a troop") is the
        # player's choice, not an automatic pick: prompt them with the same target prompt a triggered ability uses.
        # A choice zone or deck pick has its own picker below. A single candidate is not a question.
        hand_pick = None
        prompt_targets = getattr(self.handler, "_prompt_trigger_targets", None)
        if child is not None and int(owner_id or 0) != 0 and callable(prompt_targets):
            for target in child.targets:
                card_filter = getattr(target, "card_filter", None)
                to_dict = getattr(card_filter, "to_dict", None)
                if callable(to_dict):
                    card_filter = to_dict()
                if (target.requires_input and
                        str(target.target_kind or "").endswith("AbilityTargetTemplate") and
                        not filter_restricts_to_zone(card_filter, "Choosing") and
                        not filter_restricts_to_zone(card_filter, "Deck")):
                    hand_candidates = legal_targets(
                        self.db, self.session.session_id, int(owner_id or 0),
                        target.guid, source_uid,
                        both_players=str(target.player_filter or "").lower()
                        not in {"self", "you", "controller"},
                        champions=(getattr(
                            self.handler, "_champion_targets", lambda: [])()
                                   or []),
                        battle_state=self.bstate)
                    forced = (len(hand_candidates) == 1 and not target.optional and
                              not filter_restricts_to_zone(card_filter, "Hand"))
                    if hand_candidates and not forced:
                        hand_pick = (target.guid, [int(uid) for uid in hand_candidates])
                    break
        if hand_pick is not None and callable(prompt_targets):
            template_guid, hand_uids = hand_pick
            self.bstate["resolution_paused"] = True
            prompt_targets(
                self.game, self.player_uid, self.ai_uid, self.session,
                self.bstate, int(source_uid or 0), child_guid,
                (template_guid,), hand_uids, owner_id=int(owner_id or 0))
            return "activate ability: awaiting target from hand"
        if choice_target is not None and int(owner_id or 0) != 0:
            target_index, choice_uids, from_deck = choice_target
            # The chosen card can live in a hidden collection rather than
            # in the choice zone the parent materialized: Scheme's "choose
            # an action in your deck" keeps the card in the deck.  The
            # ChooseAndPlay picker only lists cards the client already
            # holds in Choosing, which left that prompt empty.  The deck
            # search prompt projects the candidates into Choosing for the
            # pick and puts them back untouched afterwards.
            deck_prompt = getattr(self.handler, "_prompt_deck_search", None)
            if from_deck and callable(deck_prompt):
                continuation = {
                    "ability_guid": child_guid,
                    "source_uid": int(source_uid or 0),
                    "owner_id": int(owner_id or 0),
                    "target_map": {},
                    "variables": {},
                    "resume_effect_order": 0,
                    "target_index": int(target_index),
                    "parent": self._mark_paused_target(self.continuation()),
                }
                # The deck is hidden, so its current order must not leak
                # through the picker arrangement.
                import random as _rnd
                _rnd.shuffle(choice_uids)
                self.bstate["resolution_paused"] = True
                deck_prompt(
                    self.game, self.session, self.player_uid, self.ai_uid,
                    self.bstate, child_guid, int(source_uid or 0),
                    int(owner_id or 0), choice_uids, kind="matching_target",
                    continuation=continuation)
                return (f"activate ability: awaiting choice of "
                        f"{len(choice_uids)} card(s)")
            parent = self._mark_paused_target(self.continuation())
            pending = {
                "kind": "choice_zone_target",
                "choice_uids": choice_uids,
                "source_uid": int(source_uid or 0),
                "owner_id": int(owner_id or 0),
                "instance_id": int(getattr(
                    self.ability, "instance_id", 1) or 1),
                "ability_guid": child_guid,
                "continuation": {
                    "ability_guid": child_guid,
                    "source_uid": int(source_uid or 0),
                    "owner_id": int(owner_id or 0),
                    "target_map": {},
                    "variables": {},
                    "resume_effect_order": 0,
                    "target_index": int(target_index),
                },
                "parent": parent,
            }
            self.bstate["pending_choice"] = pending
            self.bstate["resolution_paused"] = True
            prompt = getattr(self.handler, "_prompt_choice_cards", None)
            if callable(prompt):
                prompt(self.game, self.session, self.player_uid,
                       self.ai_uid, self.bstate, pending)
            return (f"activate ability: awaiting choice of "
                    f"{len(choice_uids)} card(s)")
        if choice_target is not None and int(owner_id or 0) == 0:
            # AI choice is still resolved through the same child graph;
            # only the client-facing picker is omitted.
            target_index, choice_uids, _from_deck = choice_target
            target_map = {int(target_index): (int(choice_uids[0]),)}
            return self._resolve_child_ability(
                child_guid, source_uid, owner_id, target_map)
        # Child abilities must re-enter the RulesPort lifecycle so their
        # instance/continuation state and native effect dispatch are not
        # lost at this nested boundary.
        target_map = {}
        selected = self.bstate.get("selected_choice_uid")
        if selected is not None:
            target_map[0] = (int(selected),)
        return self._resolve_child_ability(
            child_guid, source_uid, owner_id, target_map)

    def tac(self):
        """Apply one metadata-defined TAC operation.

        TAC operations are the client's generic escape hatch for mechanics
        that do not need a dedicated effect-template class.  Keep the
        operation name and arguments in Records, while using the same typed
        target, owner, persistence, and event helpers as ordinary effects.
        """
        import game_engine
        from rules_port.tac import (tac_function, tac_guid, tac_int, tac_string)

        serialized = self.template_value("m_SerializedTAC", None)
        if isinstance(serialized, dict):
            serialized = serialized.get("data")
        serialized = serialized or self.param
        function = tac_function(serialized)
        target = self.resolved_target()

        if function == "AppendToList":
            list_name = tac_string(serialized, "ListName")
            where = tac_string(serialized, "Where", "ThisTurnsData")
            data = {"source_uid": self.bstate.get("resolving_source_uid")}
            from rules_port.tac import decode_tac_tree, _tac_attr_hash
            tree = decode_tac_tree(serialized)
            nested = tree.get(_tac_attr_hash("DataToAppend"))
            ready = tac_int(serialized, "ReadyYourTroops", 0)
            if isinstance(nested, dict):
                ready = nested.get(_tac_attr_hash("ReadyYourTroops"), ready)
            if ready:
                data["ReadyYourTroops"] = 1
            # The extracted typed template is authoritative for nested TAC
            # data.  Keep unknown fields rather than making a card-specific
            # interpretation of a future list entry.
            if isinstance(nested, dict):
                data.update({str(k): v for k, v in nested.items()})
            lists = self.bstate.setdefault("list_attrs", {})
            ability_lists = lists.setdefault(self.ability_guid, {})
            entries = ability_lists.setdefault(list_name, [])
            entries.append({"where": where, **data})
            if list_name == "ExtraCombatsThisTurn":
                owner = self.target_owner(
                    target, self.bstate.get("resolving_owner_id", 0))
                owner_key = (str(owner) if self.bstate.get("pvp") else
                             ("ai" if int(owner or 0) == 0 else "player"))
                extra = self.bstate.setdefault(
                    "extra_combats_this_turn", {}).setdefault(owner_key, [])
                extra.append({
                    "ready_your_troops": bool(ready),
                    "source_uid": data.get("source_uid"),
                })
            return f"appended {list_name}"

        if function == "CycleCardArt":
            if target is None:
                return "cycle card art: no target"
            from domain.events import CycleCardArtSessionEventArgs
            event = CycleCardArtSessionEventArgs()
            event.session_card_id = game_engine.SessionCardId(
                game_engine.UID(int(target)))
            self.game._push(event)
            return f"cycled art {hex(int(target))}"

        if function == "DepleteResources":
            owner = self.target_owner(target, self.bstate.get(
                "resolving_owner_id", 0))
            owner = int(owner or 0)
            if self.bstate.get("pvp"):
                key = f"res_{owner}"
            else:
                key = "player_resources" if owner else "ai_resources"
            current = int(self.bstate.get(key, 0) or 0)
            self.bstate[key] = 0
            if owner:
                self.game.player_resources = 0
            else:
                self.game.ai_resources = 0
            if current:
                from rules_port.runtime_helpers import owner_uid
                current_event = game_engine.PlayerCurrentResourcePoolChangedSessionEventArgs()
                current_event.player_id = owner_uid(owner, self.player_uid,
                                            self.ai_uid, self.bstate)
                current_event.operation = 2
                current_event.delta = current
                current_event.new_value = 0
                self.game._push(current_event)
            return f"depleted {owner} resources ({current})"

        if function == "Escalate":
            if target is None:
                return "escalate: no target"
            from rules_port.statistics import increment_card_escalation
            value = increment_card_escalation(
                self.db, self.session.session_id, self.bstate, int(target))
            from pvp_db import db_card_zone_details
            details = db_card_zone_details(
                self.session.session_id, int(target), conn=self.db)
            if details:
                self._push_modifier_card(int(target), escalation=value)
            else:
                # Champions have no game_cards row, but still have a valid
                # client SessionCardId and an in-memory CardDef.
                from rules_port.runtime_helpers import owner_uid
                scid = game_engine.SessionCardId(game_engine.UID(int(target)))
                cdef = self.game.card_defs.get(scid)
                if cdef is not None:
                    cdef.escalation = value
                    champ_owner = next((int(owner) for owner, uid in
                                        (self.bstate.get("champ_map") or {}).items()
                                        if int(uid or 0) == int(target)), 0)
                    self.game.push_card_updated(
                        scid, owner_uid(champ_owner, self.player_uid,
                                        self.ai_uid, self.bstate),
                        game_engine.ECardCollections.None_, cdef.card_type,
                        escalation=value)
            return f"escalate {hex(int(target))} ({value})"

        if function == "ForgetAllCards":
            self.bstate.setdefault("stored_targets", {}).pop(
                self.ability_guid, None)
            self.bstate.setdefault("ability_lists", {}).pop(
                self.ability_guid, None)
            return "forgot stored cards"

        if function in ("GainTargetsThresholds", "GainTargetsProvidedThresholds"):
            if target is None:
                return "gain thresholds: no target"
            owner = int(self.bstate.get("resolving_owner_id", 0) or 0)
            amounts = {}
            if function == "GainTargetsProvidedThresholds":
                from domain.enums import SHARD_TO_FLAG
                from rules_port.resources import card_template_threshold_flags
                provided = card_template_threshold_flags(
                    self.db, self.session.session_id, int(target))
                colors = {int(flag) for flag in SHARD_TO_FLAG.values()}
                amounts = {color: 1 for color in colors
                           if provided & color}
            else:
                from pvp_db import db_card_template_threshold_subtype
                row = db_card_template_threshold_subtype(
                    self.session.session_id, int(target), conn=self.db)
                from rules_port.targeting import shards_from_threshold
                for color in shards_from_threshold(row[0] if row else ""):
                    amounts[color] = amounts.get(color, 0) + 1
            from rules_port.runtime_helpers import owner_uid
            for color, amount in amounts.items():
                key = (f"thresh_{owner}" if self.bstate.get("pvp") else
                       ("player_threshold" if owner else "ai_threshold"))
                values = self.bstate.setdefault(key, {})
                old = int(values.get(color, values.get(str(color), 0)) or 0)
                values[color] = old + int(amount)
                side = "player" if owner else "ai"
                setattr(self.game, f"{side}_threshold", dict(values))
                threshold_event = game_engine.PlayerResourceThresholdChangedSessionEventArgs()
                threshold_event.player_id = owner_uid(owner, self.player_uid,
                                            self.ai_uid, self.bstate)
                threshold_event.color = color
                threshold_event.operation = 1
                threshold_event.delta = int(amount)
                threshold_event.new_value = values[color]
                self.game._push(threshold_event)
                from rules_port.runtime_helpers import champion_uid_for_owner
                champion = champion_uid_for_owner(
                    self.handler, self.bstate, owner)
                if champion is not None:
                    from rules_port.triggers import dispatch_native_trigger
                    dispatch_native_trigger(
                        db=self.db, handler=self.handler, game=self.game,
                        session=self.session, player_uid=self.player_uid,
                        ai_uid=self.ai_uid, battle_state=self.bstate,
                        event_type="GainThresholdEvent",
                        source_card_id=int(champion),
                        source_player_id=owner,
                        data={"gain_threshold_color": int(color)})
            return f"gained {sum(amounts.values())} threshold(s)"

        if function == "MoveInDeck":
            if target is None:
                return "move in deck: no target"
            from pvp_db import db_card_owner_location_position, db_ordered_zone_uids
            row = db_card_owner_location_position(
                self.session.session_id, int(target), conn=self.db)
            if not row or row[1] != "deck":
                return "move in deck: target not in deck"
            offset = tac_int(serialized, "Offset", 0)
            deck = [int(value[0]) for value in db_ordered_zone_uids(
                self.session.session_id, int(row[0]), "deck", conn=self.db)]
            if int(target) not in deck or not deck:
                return "move in deck: target missing"
            old = deck.index(int(target))
            new = max(0, min(len(deck) - 1, old - int(offset)))
            deck.insert(new, deck.pop(old))
            from pvp_db import db_set_card_positions
            db_set_card_positions([
                (self.session.session_id, uid, index)
                for index, uid in enumerate(deck)], conn=self.db)
            self.db.commit()
            return f"moved {hex(int(target))} in deck {old}->{new}"

        if function == "RemoveFromStoredTargetsAbility":
            if target is None:
                return "remove stored target: no target"
            stored = self.bstate.setdefault("stored_targets", {}).setdefault(
                self.ability_guid, [])
            try:
                stored.remove(int(target))
            except ValueError:
                pass
            return f"removed {hex(int(target))} from stored targets"

        if function == "ReplaceCardInCollection":
            if target is None:
                target = self.bstate.get("resolving_source_uid")
            new_template = tac_guid(serialized)
            if target is None or not new_template:
                return "replace collection: missing target/template"
            from pvp_db import db_card_collection_info
            row = db_card_collection_info(
                self.session.session_id, int(target), conn=self.db)
            original = row[1] if row and row[1] else row[2] if row else ""
            user_id = int(row[0]) if row else int(
                self.bstate.get("resolving_owner_id", 0) or 0)
            if original:
                from profile_db import db_remove_collection
                db_remove_collection(user_id, original, conn=self.db)
            from profile_db import db_add_collection
            db_add_collection(user_id, new_template, conn=self.db)
            self.db.commit()
            return f"replaced collection card with {new_template[:8]}"

        if function == "RevokeBaseAbilities":
            if target is None:
                return "revoke base abilities: no target"
            from rules_port.ability_mutations import revoke_base_abilities
            return revoke_base_abilities(self, int(target))

        if function == "ShiftAbility":
            # The shifted power leaves the Shift ability's own source card and
            # goes to the effect's typed target.  A chain-resolved activation
            # (the RulesPort transaction path) never sets the legacy
            # ``player_shift_*`` aliases, so they are only a fallback.
            source = (self.bstate.get("resolving_source_uid") or
                      self.bstate.get("player_shift_source"))
            destination = (self.resolved_target(source_fallback=False) or
                           self.bstate.get("player_shift_target"))
            guid = tac_guid(serialized)
            if not source or not destination or not guid:
                return "shift: missing source/target/ability"
            if int(source) == int(destination):
                return "shift: target is the source card"
            from rules_port.ability_mutations import shift_ability
            return shift_ability(self, int(source), int(destination), guid)

        if function == "Tame":
            if target is None:
                return "tame: no target"
            # Tame is a typed capture operation: the shared IntAttrModifier
            # path owns voiding, capture records, and zone-exit triggers.
            self.bstate["player_mod_target"] = int(target)
            payload = {"property": "intattr", "attribute": "Tamed",
                       "amount": 1}
            from rules_port.effects import _card_modifier
            return _card_modifier(self, {"param": json.dumps(payload)})

        raise RuntimeError(
            "RulesPort TAC has no native handler: " + str(function))

    def block(self):
        from rules_port.combat_effects import block
        return block(self)

    def double_choice(self):
        from rules_port.choice_effects import double_choice
        return double_choice(self)
