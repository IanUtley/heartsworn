"""PvP helpers that do not call one another within the tournament game module.

Moved verbatim from services/tournament_game.py. The database handle and the
behaviour-table names they read are looked up on the bound tournament game module
at call time, so a rebinding there (the tests do this) is seen here too.
"""

import json
import re
import struct
from typing import Any
import game_engine as _ge
from rules_port.pvp_lifecycle import phase_is_stop as port_phase_is_stop, player_auto_passes as port_player_auto_passes, default_pvp_state as port_default_pvp_state, turn_phase_list as port_turn_phase_list
from gamedata import DEFAULT_RECORD_STORE
from db import log_req
from pvp_db import db_card_owner_id, db_game_cards_at_location, db_card_ability_list, db_ability_effect_type_params, db_card_basic, db_card_attribute_rows, db_target_template_info

_ECardCollections = _ge.ECardCollections
_ECardTypes = _ge.ECardTypes
_RECORD_STORE = DEFAULT_RECORD_STORE

_namespace = None


from services.pvp_state import _pvp_locked


def bind_namespace(module):
    """Bind the tournament game module whose names the moved functions resolve."""
    global _namespace
    _namespace = module


def _resolve(name):
    """Return a name from the bound tournament game module, or this module's own."""
    if _namespace is not None:
        return getattr(_namespace, name)
    return globals()[name]


def _uid_int(value: Any) -> int:
    """Normalize an integer or protocol UID at dynamic payload boundaries."""
    return int(getattr(value, "uid64", value))


def _pvp_dispatch_triggers(handler, game, session, state, player_uid,
                           ai_uid, event_type, source_card_id,
                           source_owner_id=None, target_card_id=None,
                           target_player_id=None,
                           force_ignores_chain=False, **event_data):
    """Dispatch one PvP event through the native RulesPort trigger path.

    Tournament PvP still owns its two-player packet projection, but trigger
    discovery and ability ordering must be the same native implementation as
    Practice.  Keeping this adapter here also prevents a new PvP call site
    from quietly importing the historical trigger scanner.
    """
    from rules_port.triggers import dispatch_native_trigger
    return dispatch_native_trigger(
        db=_resolve("_db"), handler=handler, game=game, session=session,
        player_uid=player_uid, ai_uid=ai_uid, battle_state=state,
        event_type=event_type, source_card_id=source_card_id,
        source_player_id=source_owner_id, target_card_id=target_card_id,
        target_player_id=target_player_id,
        force_ignores_chain=force_ignores_chain, data=event_data)


def _pvp_defer_trigger_event(state, *, event_type, source_card_id,
                            source_player_id, target_card_id=None,
                            target_player_id=None, phase=None, data=None):
    """Keep queued PvP events until the current phase allows chain work.

    The client DrawState queues draw and phase events but has
    ``ChainCanResolve() == false``. They are dispatched after Draw, when the
    native scheduler enters FirstMainPhase, so an event cannot strand the
    no-priority Draw phase behind a projected chain response.
    """
    pending = state.setdefault("_pvp_deferred_trigger_events", [])
    pending.append({
        "event_type": str(event_type),
        "source_card_id": source_card_id,
        "source_player_id": source_player_id,
        "target_card_id": target_card_id,
        "target_player_id": target_player_id,
        "phase": phase,
        "data": dict(data or {}),
    })


def _pvp_resolve_ability(handler, game, session, state, player_uid, ai_uid,
                         ability_guid, source_uid, owner_id, *,
                         target_map=None, variables=None,
                         resume_from_order=None, instance_id=1,
                         event_tac=None):
    """Resolve a PvP continuation through the native RulesPort lifecycle."""
    from rules_port.resolution import resolve_port_ability
    return resolve_port_ability(
        handler, game, session, _resolve("_db"), player_uid, ai_uid, state,
        ability_guid, source_uid, owner_id, target_map=target_map,
        variables=variables, resume_from_order=resume_from_order,
        instance_id=instance_id, event_tac=event_tac)


def _pvp_resource_charge_points(session, card_uid):
    """Return charge points granted by a resource's current ability BOM.

    Resource charge generation is card data, not a second universal rule.
    Set 1 shards each have a BOM ``chargepoints = 1`` effect, so adding a
    hard-coded base charge as well would double-count them.
    """
    abilities = db_card_ability_list(session.session_id, card_uid)
    total = 0
    for ability_guid in abilities if isinstance(abilities, list) else []:
        if not isinstance(ability_guid, str):
            continue
        effects = db_ability_effect_type_params(ability_guid.lower())
        for effect_type, param in effects:
            if effect_type != "CardModifierAbilityEffectTemplate":
                continue
            try:
                modifier = json.loads(param or "{}")
            except Exception:
                continue
            if modifier.get("property") != "chargepoints":
                continue
            try:
                amount = int(modifier.get("amount", 0) or 0)
            except (TypeError, ValueError):
                amount = 0
            if amount > 0:
                total += amount
    return total


def _take_pending_gain_charge_amount(state, owner_id):
    """Consume a deferred resource charge count after its picker closes."""
    pending_owner = state.get("pending_gain_charge_pid")
    try:
        matches = pending_owner is not None and int(pending_owner) == int(owner_id)
    except (TypeError, ValueError):
        matches = False
    if not matches:
        return 0
    state.pop("pending_gain_charge_pid", None)
    try:
        return max(0, int(state.pop("pending_gain_charge_amount", 1) or 0))
    except (TypeError, ValueError):
        return 0


def pvp_default_state(turn_pid, goes_first_pid):
    return port_default_pvp_state(turn_pid, goes_first_pid)


def _pvp_push_turn_phase_with_elapsed(game, phase, active_uid, priority_uid,
                                      elapsed_seconds):
    """Push TurnPhaseUpdated with the cumulative clock value for reconnect."""
    game.push_turn_phase(phase, active_uid, priority_uid)
    if (game.events and
            isinstance(game.events[-1], _ge.TurnPhaseUpdatedSessionEventArgs)):
        game.events[-1].priority_timer_elapsed = max(0, int(elapsed_seconds))


def _pvp_state_thresholds(state, pid):
    """Return a PlayerUpdated-compatible threshold map from PvP state."""
    out = {}
    for key, value in (state.get(f"thresh_{pid}") or {}).items():
        try:
            out[int(key)] = int(value or 0)
        except (TypeError, ValueError):
            continue
    return out


def _pvp_emit_paid_cost_events(game, state, *, handler, session, db,
                                player_uid, ai_uid):
    """Project deferred activation-cost pool changes onto the event stream.

    ``native_activation_cost_payer`` pays the cost while the transaction is
    still being classified, before the mode creates the resolution Game.  The
    deltas are parked in the checkpoint and emitted here so both clients get
    the ChampionChargePointsChanged / resource / spell-point HUD events.
    """
    pending = state.pop("_pvp_paid_costs", None)
    for paid in pending or ():
        uid = _ge.UID.make(244, int(paid.get("owner_id") or 0))
        if paid.get("resource"):
            ev = _ge.PlayerCurrentResourcePoolChangedSessionEventArgs()
            ev.player_id = uid
            ev.operation = 2
            ev.delta = int(paid["resource"])
            ev.new_value = int(paid.get("res_new", 0) or 0)
            game._push(ev)
        if paid.get("charge"):
            ev = _ge.ChampionChargePointsChangedSessionEventArgs()
            ev.player_id = uid
            ev.operation = 2
            ev.delta = int(paid["charge"])
            ev.new_value = int(paid.get("chg_new", 0) or 0)
            game._push(ev)
        if paid.get("spell"):
            ev = _ge.ChampionSpellPointsChangedSessionEventArgs()
            ev.player_id = uid
            ev.operation = 2
            ev.delta = int(paid["spell"])
            ev.new_value = int(paid.get("sp_new", 0) or 0)
            game._push(ev)
        if paid.get("life"):
            ev = _ge.ChampionHealthChangedSessionEventArgs()
            ev.player_id = uid
            ev.operation = 2
            ev.delta = int(paid["life"])
            ev.new_value = int(paid.get("hp_new", 0) or 0)
            game._push(ev)
    counter_costs = state.pop("_pvp_paid_counter_costs", None)
    if counter_costs:
        from rules_port.context import EffectContext
        from rules_port.counter_effects import project_counter_cost_change
        context = EffectContext.from_rules_port(
            game, session, db, handler, player_uid, ai_uid, state,
            "counter_cost_payment")
        for change in counter_costs:
            project_counter_cost_change(
                context, int(change["source_uid"]),
                str(change["counter_guid"]), int(change["old"]),
                int(change["new"]))


def _pvp_sync_view_to_state(state, view, player_pid, opponent_pid):
    """Persist per-player values changed through a FRA-shaped PvP view.

    Ability resolution uses the same FRA-shaped dictionary in Practice and
    PvP.  It is a view, not a live alias, so resource/threshold/charge changes
    made by a BOM must be copied back before the next legality/options check.
    """
    from rules_port.pvp_view import apply_effect_view
    apply_effect_view(state, view, player_pid, opponent_pid)


def _pvp_log_stack(state, label):
    """Log the current chain/stack size + which players have passed it, so the
    server log can be correlated with the CLIENT's resolve requests:
    every chain item on the stack needs BOTH players to pass, and each phase
    transition that happens while the chain is non-empty also re-announces it.
    Expect roughly: plays/passes/resolves ≈ 2 * items-on-chain + 2 * phase."""
    try:
        st = state.get("stack") or []
        sp = state.get("stack_passed") or []
        ph = state.get("phase", "?")
        turn = state.get("turn_pid", "?")
        log_req(f"    PvP stack[{label}]: {len(st)} item(s) phase={ph} "
                f"turn={turn} passed={sp}")
    except Exception as _e:
        log_req(f"    PvP stack[{label}] log error: {_e}")


def _pvp_chain_active(session, state=None):
    """Read chain activity from the active RulesPort authority.

    Tournament checkpoints retain ``stack`` for wire/reconnect compatibility,
    but an attached native session has a separate typed Chain instance. Using
    the mirror for UI or priority decisions can expose a stale Resolve window
    after the native chain has already emptied.
    """
    port = getattr(session, "_rules_port_session", None)
    if port is not None:
        return not getattr(port.chain, "is_empty", True)
    return bool((state or {}).get("stack"))


def _pvp_apply_visibility(game, state):
    """Attach persisted player-level visibility to a fresh PvP Game."""
    from rules_port.visibility import \
        apply_player_visibility_to_game
    apply_player_visibility_to_game(game, state or {})


def pvp_turn_has_attackers(session, turn_pid):
    """True if the turn player controls a warzone troop ELIGIBLE to attack:
    a troop, untapped, no Can't-Attack attribute, and not summoning sick
    (StartedATurnOnYourSide OR Speed/haste).  Mirrors ai.player_can_attack_troops
    and drives whether the turn enters the combat phase list — with no eligible
    attackers the combat steps are skipped entirely (FirstMain -> SecondMain),
    exactly like the PvE turn."""
    from rules_port.combat_rules import player_has_eligible_attackers
    return player_has_eligible_attackers(
        _resolve("_db"), session.session_id,
        battle_state=_resolve("pvp_load_state")(session) or {},
        player_id=turn_pid)


def pvp_phase_is_stop(state, phase, turn_pid, opp_pid):
    """True if EITHER player wants to stop at `phase` during the turn player's
    turn: the turn player's self-stops (their own turn) OR the opponent's
    opponent-stops (the opponent's turn), falling back to the client defaults
    when a player hasn't configured stops.  Mirrors rules_port.lifecycle.is_self_stop
    / is_opp_stop."""
    return port_phase_is_stop(state, phase, turn_pid, opp_pid)


def pvp_player_auto_passes(state, pid):
    """Whether *pid* has enabled the client's F10 auto-pass mode."""
    return port_player_auto_passes(state, pid)


def _pvp_turn_phase_list(state, turn_pid, has_ready):
    """Build the active player's phase cycle, including authored extra
    combats scheduled in ThisTurnsData."""
    return port_turn_phase_list(state, turn_pid, has_ready)


def _pvp_thresholds_met(thresh_json, player_threshold):
    """Check the PvP player's threshold counts (state thresh_<pid>, a dict of
    shard-flag -> count) against a card's threshold_json requirement
    ({"list":[2,2]} = TWO Ruby, indices 0=Colorless 1=Blood 2=Ruby 3=Sapphire
    4=Wild 5=Diamond -> flags 0/4/8/16/32/64).  Mirrors the PvE
    _thresholds_met (hconnect_server) using the PvP state's threshold dict."""
    if not thresh_json:
        return True
    try:
        import json as _j
        req = _j.loads(thresh_json)
        req_list = req.get("list", []) if isinstance(req, dict) else []
        if not req_list:
            return True
        shard_fmt = {0: 0, 1: 4, 2: 8, 3: 16, 4: 32, 5: 64}
        need = {}
        for s in req_list:
            flag = shard_fmt.get(int(s), int(s))
            need[flag] = need.get(flag, 0) + 1
        for flag, count in need.items():
            val = player_threshold.get(flag)
            if val is None:
                val = player_threshold.get(str(flag), 0)
            if int(val or 0) < count:
                return False
        return True
    except Exception:
        return True


def _pvp_card_playable(session, state, card_uid, tpl_guid, ct_name, cost,
                       ability_guids, resources, threshold):
    """A PvP hand card is playable iff affordable + thresholds met + every
    explicit target template of its non-manual abilities has a legal candidate
    (mirrors PvE _hand_card_playable + _card_target_requirements_met — makes
    Countermagic unplayable with nothing on the chain)."""
    from gamedata import PlayPlan
    from rules_port.costs import card_cost_targets
    from rules_port.targeting import legal_targets_for
    pids = _resolve("db_game_session_pids")(session.session_id)
    if len(pids) < 2:
        return True
    turn_pid = state.get("priority_pid") or state.get("turn_pid")
    champ_map = state.get("champ_map") or {}
    champ_targets = []
    for cpid in (state.get("pids") or []):
        ccu = int(champ_map.get(str(cpid), 0))
        if ccu:
            champ_targets.append((ccu, cpid, "Champ",
                                  int(state.get(f"hp_{cpid}", 20))))
    store = _RECORD_STORE
    try:
        play_plan = PlayPlan.from_card(store, tpl_guid, source_uid=card_uid,
                                       owner_id=turn_pid)
    except KeyError:
        return False
    if play_plan.cost_instances:
        for cost_spec in card_cost_targets(
                play_plan, _resolve("_db"), session.session_id, turn_pid, int(card_uid),
                champions=champ_targets, battle_state=state):
            if (not cost_spec.is_source_auto_target and
                    len(cost_spec.candidates) < int(cost_spec.minimum)):
                return False
    for ag in (ability_guids or []):
        graph = _resolve("ability_graph")(store, str(ag).lower())
        if graph is None:
            # A card whose current Records definition is unavailable is not
            # playable; do not silently substitute a stale DB ability shape.
            return False
        if graph.manual or graph.trigger_event_type:
            continue
        instance = next((ability for ability in play_plan.abilities
                         if ability.ability_guid.lower() == str(ag).lower()),
                        None)
        if instance is None:
            return False
        for index in instance.referenced_target_indexes:
            if index < 0 or index >= len(graph.targets):
                continue
            target = graph.targets[index]
            if not target.requires_input or target.minimum < 1:
                continue
            try:
                candidates = legal_targets_for(
                    _resolve("_db"), session.session_id, turn_pid, target, 0,
                    both_players=True, champions=champ_targets,
                    battle_state=state)
            except Exception:
                continue
            if not candidates:
                log_req(f"    PvP options: {ct_name} not playable "
                        f"(target template {target.guid[:8]} "
                        f"has no legal target)")
                return False
    return True


def _pvp_card_source_location(session, state, player_id, card_uid):
    """Return an authoritative playable zone for this owner's card intent."""
    from pvp_db import (db_card_owner_location_position,
                        db_deck_top_card_details)
    row = db_card_owner_location_position(
        session.session_id, int(card_uid), conn=_resolve("_db"))
    if not row or int(row[0] or 0) != int(player_id):
        return None
    location = str(row[1] or "").lower()
    if location == "hand":
        return location
    if location != "deck":
        return None
    from rules_port.static_rules import player_int_attributes
    attrs = player_int_attributes(
        _resolve("_db"), session.session_id, state, player_id)
    if int(attrs.get("CanPlayTopOfDeck", 0) or 0) <= 0:
        return None
    top = db_deck_top_card_details(
        session.session_id, int(player_id), conn=_resolve("_db"))
    return location if top and int(top[1]) == int(card_uid) else None


def _pvp_add_play_target_options(g, session, state, pl_t, opp_t, turn_pid):
    """Attach targeting TargetInstances to the most recent PlayerOptionList so
    the client opens the target picker for played spells — mirrors PvE
    _add_play_target_options.  Without this a targeted spell (e.g. Bravery
    "+1/+1 target troop") is played with no target and no effect."""
    if not g.events:
        return
    # CardUpdated/PlayerUpdated events may be appended while the packet is
    # being assembled.  The option list is the owning event; relying on the
    # final event silently drops manual abilities on those refresh paths.
    last_ev = next(
        (event for event in reversed(g.events)
         if isinstance(event, _ge.PlayerOptionListSessionEventArgs)), None)
    if last_ev is None:
        return
    from gamedata import PlayPlan
    from rules_port.targeting import legal_targets as _lt
    champ_map = state.get("champ_map") or {}
    champ_targets = []
    for cpid in (state.get("pids") or []):
        cu = int(champ_map.get(str(cpid), 0))
        if cu:
            champ_targets.append((cu, cpid, "Champ",
                                  int(state.get(f"hp_{cpid}", 20))))
    for opt in last_ev.options:
        card_uid = int(opt.card.uid.uid64)
        row = db_card_basic(session.session_id, card_uid, conn=_resolve("_db"))
        if not row:
            continue
        store = _RECORD_STORE
        try:
            plan = PlayPlan.from_card(store, row[0], source_uid=card_uid,
                                      owner_id=turn_pid)
        except KeyError:
            continue
        for ability in plan.abilities:
            graph = ability.graph
            if graph is None or graph.manual or ability.is_triggered:
                continue
            # PlayerOptions.Update keys target instances by (card, ability) and
            # CLEARS the stored list each time it sees another OptionInstance
            # with the same opt_id (PlayerOptions.cs:117-129).  Emitting one
            # OptionInstance per template dropped every target but the last
            # (Survival of the Fittest prompted only for the opposing troop and
            # then hung on the chain).  Collect every template of this ability
            # into one OptionInstance, mirroring the PvE builder.
            entries = []
            for i in ability.referenced_target_indexes:
                if i >= len(graph.targets):
                    continue
                target = graph.targets[i]
                if not target.requires_input:
                    continue
                tid = target.guid
                trow = db_target_template_info(tid, conn=_resolve("_db"))
                if not trow:
                    continue
                kind = trow[1] or ""
                auto = int(trow[2] or 0)
                if auto:
                    continue
                targets = []
                if kind == "PlayerTargetTemplate":
                    cu = int(champ_map.get(str(turn_pid), 0))
                    targets = [_ge.SessionCardId(_ge.UID(cu))] if cu else []
                else:
                    fj = trow[0] or "{}"
                    if not fj or fj.strip() == "{}":
                        continue
                    try:
                        cands = _lt(_resolve("_db"), session.session_id, turn_pid, tid, 0,
                                    both_players=True, champions=champ_targets)
                        targets = [_ge.SessionCardId(_ge.UID(int(u)))
                                   for u in cands]
                    except Exception:
                        targets = []
                if not targets:
                    continue
                minimum = max(0, int(target.minimum))
                maximum = max(minimum, int(target.maximum or minimum or 1))
                entries.append((i, tid, targets, minimum, maximum))
            try:
                from rules_port.costs import (
                    ability_cost_targets as _ability_cost_targets,
                    cost_type_for_kind as _cost_type_for_kind)
                cost_entries = _ability_cost_targets(
                    graph, _resolve("_db"), session.session_id, turn_pid, int(card_uid),
                    champions=champ_targets, battle_state=state)
            except ValueError:
                cost_entries = ()
            if any(not cost.is_source_auto_target and
                   len(cost.candidates) < cost.minimum
                   for cost in cost_entries):
                continue
            if not entries and not cost_entries:
                continue
            inst = g._make_event(_ge.OptionInstanceSessionEventArgs)
            inst.opt_id = _ge.ResourceId.from_str(ability.ability_guid)
            for i, tid, targets, minimum, maximum in entries:
                inst.min_target_counts.append(minimum)
                inst.max_target_counts.append(maximum)
                inst.target_ids.append(_ge.ResourceId.from_str(tid))
                tgt = g._make_event(_ge.TargetInstanceSessionEventArgs)
                tgt.target_index = i
                tgt.target_id = _ge.ResourceId.from_str(tid)
                tgt.targets = list(targets)
                inst.target_instances.append(tgt)
            for cost in cost_entries:
                if cost.is_source_auto_target or not cost.candidates:
                    continue
                ci = g._make_event(_ge.CostInstanceSessionEventArgs)
                minimum = int(cost.minimum)
                maximum = (len(cost.candidates) if cost.maximum < 0
                           else int(cost.maximum))
                if hasattr(ci, "min_target_count"):
                    ci.min_target_count = minimum
                    ci.max_target_count = maximum
                else:
                    ci.min = minimum
                    ci.max = maximum
                ci.cost_type = _cost_type_for_kind(cost.kind)
                ci.target_template_id = _ge.ResourceId.from_str(cost.guid)
                ci.targets = [_ge.SessionCardId(_ge.UID(int(uid)))
                              for uid in cost.candidates]
                inst.target_instances.append(ci)
            opt.instances.append(inst)
            # Also attach the picker to the PlayCard option so the client's
            # CanUseAbility finds the target on the built-in PlayCard
            # ability (the play-card flow keys on PlayCardAbilityTemplateId).
            for inst2 in opt.instances:
                if str(inst2.opt_id.guid) != _ge.PLAY_CARD_ABILITY_TEMPLATE_ID:
                    continue
                for i, tid, targets, minimum, maximum in entries:
                    inst2.target_ids.append(_ge.ResourceId.from_str(tid))
                    inst2.min_target_counts.append(minimum)
                    inst2.max_target_counts.append(maximum)
                    tgt2 = g._make_event(_ge.TargetInstanceSessionEventArgs)
                    tgt2.target_index = i
                    tgt2.target_id = _ge.ResourceId.from_str(tid)
                    tgt2.targets = list(targets)
                    inst2.target_instances.append(tgt2)
                break
        # Card-level target costs use the same CostInstance contract as
        # activated abilities.  Preserve their authored order so the client
        # assigns them into the matching XCostData collection.
        try:
            from rules_port.costs import card_cost_targets
            cost_candidates = card_cost_targets(
                plan, _resolve("_db"), session.session_id, turn_pid, int(card_uid),
                champions=champ_targets, battle_state=state)
        except ValueError:
            cost_candidates = ()
        for cost_spec in cost_candidates:
            if cost_spec.is_source_auto_target:
                continue
            cost_guid = cost_spec.guid
            cost_uids = list(cost_spec.candidates)
            if not cost_uids:
                continue
            minimum = int(cost_spec.minimum)
            maximum = int(cost_spec.maximum)
            if maximum < 0:
                maximum = len(cost_uids)
            for inst in opt.instances:
                if str(inst.opt_id.guid) == _ge.PLAY_CARD_ABILITY_TEMPLATE_ID:
                    ci = g._make_event(_ge.CostInstanceSessionEventArgs)
                    ci.min = minimum
                    ci.max = maximum
                    from rules_port.costs import cost_type_for_kind
                    ci.cost_type = cost_type_for_kind(cost_spec.kind)
                    ci.target_template_id = _ge.ResourceId.from_str(cost_guid)
                    ci.targets = [_ge.SessionCardId(_ge.UID(int(uid)))
                                  for uid in cost_uids]
                    inst.target_instances.append(ci)
                    break
        # Variable X cost: attach an XCost CostInstance to the PlayCard option
        # so the client's BattleStateAssignXCost pushes the X slider — only for
        # templates with variable_cost (mirrors PvE _template_has_x_cost).
        if plan.cost.variable:
            for inst in opt.instances:
                if str(inst.opt_id.guid) == _ge.PLAY_CARD_ABILITY_TEMPLATE_ID:
                    ci = g._make_event(_ge.CostInstanceSessionEventArgs)
                    ci.min = plan.cost.variable_minimum
                    ci.max = 0
                    ci.cost_type = 256  # EAbilityCostType.XCostAbilityCostType
                    ci.target_template_id = _ge.ResourceId.invalid()
                    ci.targets = []
                    inst.target_instances.append(ci)
                    break


def _pvp_ability_cost_targets(session, state, pid, source_uid,
                              ability_guid, champ_targets):
    """Return legal cost cards, or None when a required cost is unpayable."""
    from rules_port.costs import ability_cost_targets, cost_type_for_kind
    graph = _resolve("ability_graph")(_RECORD_STORE, str(ability_guid).lower())
    if graph is None:
        return None
    costs = ability_cost_targets(
        graph, _resolve("_db"), session.session_id, pid, int(source_uid),
        champions=champ_targets, battle_state=state)
    if not costs:
        return []
    out = []
    for cost in costs:
        tid = cost.guid
        cost_type = cost_type_for_kind(cost.kind)
        if not cost.guid:
            return None
        minimum = int(cost.minimum)
        maximum = int(cost.maximum)
        # Gamedata represents "sacrifice this" as an automatic source-card
        # target.  It is a payment target for the option contract, but the
        # client does not repeat the source UID in the submitted TargetMap.
        # Advertise the source as the sole legal candidate and let activation
        # satisfy this automatic payment without requiring it in the wire
        # transaction.
        if cost.is_source_auto_target:
            out.append((tid, cost_type, [int(source_uid)], minimum, maximum))
            continue
        candidates = list(cost.candidates)
        if len(candidates) < minimum:
            return None
        # Gamedata uses Int32.MaxValue for an open-ended "one or more"
        # payment.  The client needs the effective maximum for this choice,
        # not an unbounded value that can leave its picker waiting forever.
        maximum = min(maximum, len(candidates))
        out.append((tid, cost_type, [int(uid) for uid in candidates],
                    minimum, maximum))
    return out


def _pvp_discard_prompt_data(ability_guid):
    """Return the child/target pair for a controller discard prompt.

    The prompt is derived from the current effect graph.  Missing effect data
    is invalid Records data and must not be inferred from localized text.
    """
    from rules_port.metadata import ability_effect_prompt
    prompt = ability_effect_prompt(
        ability_guid, "DiscardCardAbilityEffectTemplate")
    if prompt and prompt[1]:
        return prompt
    return None


def _pvp_discard_prompt_pid(session, state, ability_guid, owner_id, opp_pid,
                            source_uid, target_uid=None, target_map=None):
    """Return the player who answers a nested "discard a card" prompt.

    The DiscardCard leaf lives in a child that the parent invokes through
    ActivateAbility, and C# activates that child for the controller of the
    ActivateAbility effect's target: "You" (Blue Sparrow) is the activator,
    while "target champion" (Concubunny Yuka) and "each opposing champion"
    (The Nameless Knight) are that champion's controller.
    """
    from rules_port.metadata import ability_effect_prompt
    owner_id, opp_pid = int(owner_id), int(opp_pid)
    graph = _resolve("ability_graph")(_RECORD_STORE, str(ability_guid or "").lower())
    if graph is None:
        return owner_id
    champ_map = state.get("champ_map") or {}
    champion_pids = {int(uid): int(pid) for pid, uid in champ_map.items()
                     if uid}
    for effect in graph.effects:
        if effect.concrete_type != "ActivateAbilityEffectTemplate":
            continue
        invoked = getattr(effect.template, "m_AbilityToInvoke", None)
        child_guid = (invoked.get("m_Guid") if isinstance(invoked, dict)
                      else getattr(invoked, "m_Guid", None))
        if not child_guid or not ability_effect_prompt(
                str(child_guid).lower(), "DiscardCardAbilityEffectTemplate"):
            continue
        index = int(effect.target_index)
        if not 0 <= index < len(graph.targets):
            return owner_id
        spec = graph.targets[index]
        if spec.target_kind == "PlayerTargetTemplate":
            player_filter = str(spec.player_filter or "").lower()
            return opp_pid if player_filter.startswith("opp") else owner_id
        selected = (target_map or {}).get(index, (target_map or {}).get(
            str(index)))
        if selected is None and spec.requires_input:
            selected = target_uid
        if selected is None:
            from rules_port.targeting import legal_targets_for
            champions = [(uid, pid, "Champion",
                          int(state.get(f"hp_{pid}", 20)))
                         for uid, pid in champion_pids.items()]
            selected = legal_targets_for(
                _resolve("_db"), session.session_id, owner_id, spec, int(source_uid),
                champions=champions, battle_state=state)
        if not isinstance(selected, (list, tuple, set)):
            selected = (selected,)
        for value in selected:
            try:
                uid = int(getattr(value, "uid64", value))
            except (TypeError, ValueError):
                continue
            if uid in champion_pids:
                return champion_pids[uid]
            card_owner = db_card_owner_id(session.session_id, uid, conn=_resolve("_db"))
            if card_owner is not None:
                return int(card_owner)
        return owner_id
    return owner_id


def _pvp_set_handler_champions(handler, player_champ, ai_champ,
                               player_guid, ai_guid):
    """Record champion identity and authored ability catalogs on the handler.

    Champion cards are synthetic SessionCardIds with no mutable ``game_cards``
    row, so ``AbilityCanBeActivatedRequirement`` gates a charge power on the
    ability catalog projected on the champion.  HConnect's own champion setup
    records that catalog next to the champion ids; the tournament service
    builds its own champion projection and must record it there too, otherwise
    the native facts bridge reads an empty catalog and rejects every champion
    power (Corinth's charge power among them).
    """
    handler._player_champ_scid = player_champ
    handler._ai_champ_scid = ai_champ
    handler._player_champ_guid = player_guid
    handler._ai_champ_guid = ai_guid
    from pvp_db import db_get_champion_ability_guids

    def catalog(guid):
        if not guid:
            return []
        return [str(value).lower()
                for value in db_get_champion_ability_guids(guid)]

    handler._player_champ_abilities = [
        _ge.ResourceId.from_str(value) for value in catalog(player_guid)]
    handler._ai_champ_ability_guids = catalog(ai_guid)


def _pvp_transaction_card_uids(inner_bytes):
    """Extract Card SessionCardIds from a client transaction."""
    if not isinstance(inner_bytes, bytes):
        return []
    import struct
    out = []
    for match in re.finditer(
            rb'm_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});',
            inner_bytes):
        try:
            uid = struct.unpack('<Q', bytes.fromhex(match.group(1).decode()))[0]
            if (uid & 0xFF) == 1:
                out.append(int(uid))
        except (TypeError, ValueError, struct.error):
            continue
    return out


def _ability_has_effects_from(ability_guid, order):
    """Whether an authored ability still has effects at or after ``order``."""
    from gamedata import DEFAULT_RECORD_STORE
    graph = _resolve("ability_graph")(DEFAULT_RECORD_STORE, ability_guid)
    return graph is not None and int(order or 0) < len(graph.effects)


def _send_pvp_packet(h, session, g, pl_uid, label):
    """Serialize a Game's queued events and send the 3055 packet to one
    player's handler.  Returns True when the send succeeded (or there was
    nothing to send), False when the client is disconnected."""
    if not g.events:
        return True
    if not getattr(g, "_visibility_by_uid", None):
        from rules_port.visibility import \
            apply_player_visibility_to_game
        apply_player_visibility_to_game(g, _resolve("pvp_load_state")(session) or {})
    pkt = g.make_network_packet(pl_uid)
    dw = _resolve("encode_datawrapper")(0, 3055, _resolve("compress_gzip")(_resolve("encode_sync_event")(pkt)), 1,
                            _resolve("client_session_guid")(h))
    h.scnt += 1
    try:
        h.send({
            "issuer": f"0.0.0.0.ServiceGameSession.246.{session.session_id}.{h.scnt}",
            "target": "ServiceGameSession", "instance": str(session.server_id),
            "reqid": 0, "c": 0, "conh": 0, "sid": h.sid,
        }, dw)
        log_req(f"    PvP {label}: pushed to pid {int(pl_uid.uid64) >> 8}")
        return True
    except OSError:
        log_req(f"    PvP {label}: failed to push to pid {int(pl_uid.uid64) >> 8} (disconnected)")
        return False


def _pvp_push_reconnect_underground(game, session, handler, viewer_pid,
                                   player_uid, opponent_uid, state):
    """Restore hidden Underground membership and authorized reveal rows."""
    for card_uid, template_guid, owner, card_type, card_state, _abilities, _attrs \
            in db_game_cards_at_location(session.session_id, "underground"):
        scid = _ge.SessionCardId(_ge.UID(int(card_uid)))
        owner_uid = _ge.UID.make(244, int(owner))
        try:
            handler._card_full_data(game, scid, template_guid)
        except Exception:
            pass
        game.push_card_updated(
            scid, owner_uid, _ECardCollections.Underground,
            _ge.card_type_from_db(card_type), template_id=template_guid,
            state=int(card_state or 0), nulling=int(owner) != int(viewer_pid))
    from rules_port.visibility import project_visible_underground
    project_visible_underground(
        _resolve("_db"), session, handler, game, player_uid, opponent_uid, state)


def _pvp_raw_player_id(player_uid):
    """Return the persisted Reckoning id from a raw or typed player UID."""
    value = int(player_uid or 0)
    return (value >> 8) if (value & 0xff) == 244 else value


def _objfmt_uid_field(inner_obj, inner_bytes, field_name):
    """Read a typed UID field from decoded ObjFmt data or its wire bytes."""
    value = inner_obj.get(field_name) if isinstance(inner_obj, dict) else None
    if isinstance(value, dict):
        value = value.get("m_UID64", value.get("uid64", value.get("value__")))
    if value is not None:
        try:
            return int(getattr(value, "uid64", value))
        except (TypeError, ValueError):
            pass

    if not isinstance(inner_bytes, bytes):
        return 0
    pos = inner_bytes.find(str(field_name).encode("ascii"))
    if pos < 0:
        return 0
    rest = inner_bytes[pos:]
    index = rest.find(b"m_UID64")
    if index < 0:
        return 0
    parts = rest[index + 7:].split(b";", 6)
    if len(parts) < 5:
        return 0
    try:
        from binascii import unhexlify
        return struct.unpack("<Q", unhexlify(parts[4].decode("ascii")))[0]
    except (ValueError, UnicodeDecodeError, struct.error):
        return 0


def _session_has_reconnect_player(session, raw_player_id):
    """Check a persisted game's participant list against a Reckoning ID."""
    try:
        raw_player_id = int(raw_player_id)
    except (TypeError, ValueError):
        return False
    if raw_player_id <= 0:
        return False
    expected = {raw_player_id, _ge.UID.make(244, raw_player_id).uid64}
    for player in getattr(session, "players", ()) or ():
        if not isinstance(player, (tuple, list)) or not player:
            continue
        try:
            candidate = int(getattr(player[0], "uid64", player[0]))
        except (TypeError, ValueError):
            continue
        if candidate in expected:
            return True
    try:
        owner_uid = int(getattr(session, "owner_uid", 0) or 0)
    except (TypeError, ValueError):
        owner_uid = 0
    return owner_uid in expected


def _pvp_session_pids(session, state):
    """Two-participant list for a tourney session.

    ``game_cards`` rows are written with the opening hands, so the very first
    gameplay transaction of a resumed match can arrive before they exist.
    The PvP checkpoint and the transport metadata both name the participants;
    reading them keeps such a transaction on the native host instead of
    dropping the session onto the legacy stack.
    """
    def _pid(value):
        # ``session.players`` carries typed UIDs; the checkpoint uses raw ids.
        raw = getattr(value, "uid64", value)
        try:
            raw = int(raw)
        except (TypeError, ValueError):
            return 0
        return raw >> 8 if (raw & 0xFF) == 244 else raw

    candidates = (
        (state or {}).get("pids"),
        list((state or {}).get("champ_map") or {}),
        [uid for uid, _position in
         (getattr(session, "players", ()) or ())],
    )
    for source in candidates:
        found = []
        for value in source or ():
            pid = _pid(value)
            if pid and pid not in found:
                found.append(pid)
        if len(found) >= 2:
            return found[:2]
    return []


def _pvp_fra_view(state, attacker_pid, defender_pid):
    """Translate the PvP battle state into the FRA-shaped view resolve_combat
    understands (attacker = 'player', defender = 'ai').  ``pvp`` stays set so
    the owner mappings use pid-based UIDs, and pvp_health_map lets heal
    triggers write to the right health key.  The chain/stack is ALIASED to the
    persisted PvP state (not a transient copy), so triggers pushed onto the
    stack survive the function call and can be resolved later by a pass."""
    from rules_port.pvp_view import to_effect_view
    return to_effect_view(state, attacker_pid, defender_pid)


def _pvp_typed_card_targets(payload, source_uid):
    """Flatten decoded card-play TargetMap values in client order."""
    values = []
    for activation in (payload or {}).get("ability_data") or ():
        if not isinstance(activation, dict):
            continue
        for selected in (activation.get("target_map", {}) or {}).values():
            selected = selected if isinstance(
                selected, (list, tuple, set)) else (selected,)
            for value in selected:
                try:
                    uid = int(getattr(value, "uid64", value))
                except (TypeError, ValueError):
                    continue
                if uid != int(source_uid):
                    values.append(uid)
    return values


def _pvp_select_card_play_costs(handler, session, state, plan, source_uid,
                                pid, selected_uids, champions):
    """Bind the client TargetMap to the card's authored cost targets."""
    from rules_port.costs import (
        card_cost_targets, cost_type_for_kind,
        validate_cost_target_selection,
    )

    selected_uids = [int(uid) for uid in (selected_uids or [])]
    used = set()
    selections = []
    if not plan.cost_instances:
        return selections, used
    for native in card_cost_targets(
            plan, _resolve("_db"), session.session_id, pid, int(source_uid),
            champions=champions, battle_state=state):
        spec = {"kind": native.kind, "target_guid": native.guid,
                "cost_type": cost_type_for_kind(native.kind),
                "minimum": native.minimum, "maximum": native.maximum,
                "auto": native.is_source_auto_target}
        candidates = list(native.candidates)
        if native.is_source_auto_target:
            selections.append((spec, candidates))
            continue
        candidate_set = {int(uid) for uid in candidates}
        available = [uid for uid in selected_uids
                     if uid in candidate_set and uid not in used]
        minimum = int(spec["minimum"])
        maximum = int(spec["maximum"])
        if maximum < 0:
            maximum = len(available)
        if len(available) < minimum:
            return None
        chosen = tuple(available[:maximum])
        chosen = validate_cost_target_selection(
            _resolve("_db"), session.session_id, pid, int(source_uid), native, chosen,
            champions=champions, battle_state=state)
        if chosen is None:
            return None
        used.update(chosen)
        selections.append((spec, chosen))
    return selections, used


def pvp_combat_has_swiftstrike(session, state):
    """Return whether the current PVP combat has a FirstStrike/DualStrike
    combatant.

    The client checks both sides of every combat, not only the attackers.  Use
    the live joined attributes so a Quick Action's temporary Swiftstrike grant
    is visible when this is called after the blocker response window.
    """
    uids = set()
    for uid in (state.get("attackers") or {}):
        try:
            uids.add(int(uid))
        except (TypeError, ValueError):
            continue
    for blockers in (state.get("blockers") or {}).values():
        for uid in (blockers or []):
            try:
                uids.add(int(uid))
            except (TypeError, ValueError):
                continue
    if not uids:
        return False
    rows = db_card_attribute_rows(
        session.session_id, uids, conn=_resolve("_db"))
    swiftstrike = (_ge.ECardAttributes.FirstStrike |
                   _ge.ECardAttributes.DualStrike)
    return any(int(row[0] or 0) & swiftstrike for row in rows)


def _push_to_both_players(session, handler, events_fn, log_req=log_req):
    """Push game events to BOTH players in a tourney session using *events_fn*."""
    pids = _resolve("db_game_session_pids")(session.session_id)
    for pid in pids:
        h = _resolve('player_handlers').get(pid)
        if not h:
            continue
        pl_t = _ge.UID.make(244, pid)
        opp_t = _ge.UID.make(244, pids[1] if pid == pids[0] else pids[0])
        g = _ge.Game(int(session.session_id), pl_t, opp_t)
        _pvp_apply_visibility(g, _resolve("pvp_load_state")(session) or {})
        events_fn(g, pl_t)
        if g.events:
            pkt = g.make_network_packet(pl_t)
            dw = _resolve("encode_datawrapper")(0, 3055, _resolve("compress_gzip")(_resolve("encode_sync_event")(pkt)), 1, _resolve("client_session_guid")(h))
            h.scnt += 1
            try:
                h.send({'issuer': f'0.0.0.0.ServiceGameSession.246.{session.session_id}.{h.scnt}', 'target': 'ServiceGameSession', 'instance': str(session.server_id), 'reqid': 0, 'c': 0, 'conh': 0, 'sid': h.sid}, dw)
                log_req(f'    PvP: pushed to pid {pid}')
            except OSError:
                log_req(f'    PvP: failed to push to pid {pid} (disconnected)')


def _pvp_add_champion_options(g, session, state, pid, pl_t):
    """Append the pid's champion to the most recent PlayerOptionList with its
    AFFORDABLE charge/spell abilities, so the client's champion ability buttons
    appear and light up (CanActivateAbility -> State.CanUseAbility requires the
    champion in PlayerOptions.m_Targets).  Affordability mirrors PvE
    _filter_affordable_abilities but reads charges/thresholds from the PvP
    state (chg_<pid> / thresh_<pid>) instead of a PvE battle-state checkpoint."""
    champ_map = state.get('champ_map') or {}
    cu = int(champ_map.get(str(pid), 0))
    if not cu:
        return
    champ_scid = _ge.SessionCardId(_ge.UID(cu))
    crow = db_card_basic(session.session_id, cu, conn=_resolve("_db"))
    if not crow:
        return
    tpl_guid = crow[0]
    from pve_db import db_talent_ability_costs
    all_guids = _resolve('db_champion_ability_guids')(tpl_guid)
    if not all_guids:
        return
    charges = int(state.get(f'chg_{pid}', 0))
    spell_points = int(state.get(f'sp_{pid}', 0))
    spell_uses = dict(state.get(f'sp_uses_{pid}') or {})
    threshold = dict(state.get(f'thresh_{pid}') or {})
    phase = int(state.get('phase', 0))
    afford = []
    all_rids = []
    for ag in all_guids:
        all_rids.append(_ge.ResourceId.from_str(ag))
        graph = _resolve("ability_graph")(_RECORD_STORE, str(ag).lower())
        if graph is not None and (not graph.manual):
            continue
        if graph is not None:
            from rules_port.cooldowns import champion_cooldown, champion_cooldown_key
            if champion_cooldown(state, cu, ag) > 0:
                continue
            costs = graph.costs
            use_key = champion_cooldown_key(cu, ag)
            used = int((state.get('champion_ability_uses') or {}).get(use_key, (state.get('champion_ability_uses') or {}).get(str(ag).lower(), 0)) or 0)
            if costs.uses_per_game and used >= int(costs.uses_per_game):
                continue
            if costs.uses_per_turn:
                from rules_port.runtime_helpers import champion_ability_use_key, champion_ability_uses_this_turn
                turn_key = champion_ability_use_key(cu, ag)
                if champion_ability_uses_this_turn(state, turn_key) >= int(costs.uses_per_turn):
                    continue
        row = _resolve('db_champion_ability_costs')(str(ag))
        if row is None:
            row = db_talent_ability_costs(str(ag))
        if row is None:
            continue
        cc = int(row[0] or 0)
        sc = int(row[1] or 0)
        effective_sc = sc + int(spell_uses.get(str(ag), 0) or 0) if sc else 0
        activatable_phases = int(row[2] or 0) if len(row) > 2 else 0
        casting = int(row[3] or 0) if len(row) > 3 else 64
        if charges < cc or spell_points < effective_sc:
            continue
        if _pvp_chain_active(session, state) and casting != 64:
            continue
        if casting != 64 and state.get('turn_pid') != pid:
            continue
        if activatable_phases and (not activatable_phases & 1 << phase):
            continue
        reqs = _resolve('db_champion_ability_thresholds')(str(ag))
        if reqs:
            from game_engine import SHARD_TO_FLAG
            ok = True
            for color, qty in reqs:
                flag = SHARD_TO_FLAG.get(str(color).lower(), 0)
                if flag:
                    _tv = threshold.get(flag)
                    if _tv is None:
                        _tv = threshold.get(str(flag), 0)
                    if int(_tv or 0) < qty:
                        ok = False
                        break
            if not ok:
                continue
        afford.append(_ge.ResourceId.from_str(ag))
    if _resolve('db_is_champion_template')(tpl_guid):
        hp = int(state.get(f'hp_{pid}', 20))
        g.card_defs[champ_scid] = _ge.CardDef('Champion', _ge.ECardTypes.Champion, 0, hp, hp, [], list(all_rids))
    cdef = g.card_defs.get(champ_scid)
    if cdef is not None:
        from rules_port.ability_usage import remaining_uses_per_game
        cdef.uses_per_game_counts = {_ge.ResourceId.from_str(guid): int(value) for guid, value in remaining_uses_per_game(_resolve("_db"), session.session_id, cu, [str(value) for value in all_rids], state, champion=True).items()}
        prefix = f'{cu}:'
        cdef.cooldown_counts = {_ge.ResourceId.from_str(str(key).split(':', 1)[1]): int(value) for key, value in (state.get('champion_ability_cooldowns') or {}).items() if str(key).startswith(prefix) and int(value or 0) > 0}
    from rules_port.targeting import legal_targets_for
    champ_map = state.get('champ_map') or {}
    champ_targets = []
    for cpid in state.get('pids') or []:
        ccu = int(champ_map.get(str(cpid), 0))
        if ccu:
            champ_targets.append((ccu, cpid, 'Champ', int(state.get(f'hp_{cpid}', 20))))
    target_data = {}
    blocked = set()
    for rid in all_rids:
        ag = str(rid.guid)
        graph = _resolve("ability_graph")(_RECORD_STORE, ag.lower())
        if graph is None:
            continue
        entries = []
        for target in graph.targets:
            if not target.requires_input:
                continue
            tid = target.guid
            if target.is_auto or target.target_kind == 'PlayerTargetTemplate':
                continue
            try:
                cands = legal_targets_for(_resolve("_db"), session.session_id, pid, target, int(cu), both_players=True, champions=champ_targets, battle_state=state)
            except Exception:
                cands = []
            if len(cands) < int(getattr(target, 'minimum', 0) or 0) and (not (getattr(target, 'optional', False) or getattr(target, 'allow_best_effort_minimum', False))):
                blocked.add(ag)
                continue
            if not cands:
                continue
            entries.append((tid, cands, target.minimum or 1, target.maximum if target.maximum > 0 else 1))
        if entries:
            target_data[ag] = entries
    from rules_port.costs import ability_cost_targets, cost_type_for_kind
    cost_data = {}
    for rid in all_rids:
        ag = str(rid.guid)
        graph = _resolve("ability_graph")(_RECORD_STORE, ag.lower())
        if graph is None or not hasattr(graph, 'additional_cost_targets'):
            continue
        entries = []
        for cost in ability_cost_targets(graph, _resolve("_db"), session.session_id, pid, int(cu), champions=champ_targets, battle_state=state):
            if cost.is_source_auto_target:
                continue
            if len(cost.candidates) < int(cost.minimum or 0):
                blocked.add(ag)
            entries.append((cost.guid, cost_type_for_kind(cost.kind), tuple(cost.candidates), cost.minimum, cost.maximum))
        if entries:
            cost_data[ag] = entries
    if blocked:
        afford = [rid for rid in afford if str(rid.guid) not in blocked]
    if _resolve('db_is_champion_template')(tpl_guid):
        hp = int(state.get(f'hp_{pid}', 20))
        cdef = g.card_defs.get(champ_scid)
        counters = dict((state.get('champion_counters') or {}).get(str(int(champ_scid.uid.uid64)), {}) or {})
        if cdef is not None:
            cdef.counters = counters
        g.push_card_updated(champ_scid, _ge.UID.make(244, pid), _ge.ECardCollections.None_, _ge.ECardTypes.Champion, template_id=tpl_guid, defense=hp, counters=counters)
        champion_update = g.events.pop()
        option_index = next((index for index, event in enumerate(g.events) if isinstance(event, _ge.PlayerOptionListSessionEventArgs)), len(g.events))
        g.events.insert(option_index, champion_update)
    g.add_champion_to_options(pl_t, champ_scid, afford, target_data=target_data or None, cost_data=cost_data or None)
    log_req(f'    PvP champion options added for {pid}: {[str(a.guid)[:8] for a in all_rids]} (charges {charges}, affordable {len(afford)})')


def _pvp_add_hand_card_updates(g, session, state, pid, player_uid):
    """Refresh the priority player's private hand card representations.

    Dynamic all-zone modifiers (notably Pterobot) change when Dwarves or
    Robots enter/leave the warzone.  The client only changes the displayed
    cost after a CardUpdated, so rebuilding PlayerOptionList alone leaves a
    stale hand cost even when server affordability is already correct.
    """
    h = _resolve('player_handlers').get(int(pid))
    if h is None or not hasattr(h, '_card_full_data'):
        return
    _pvp_apply_visibility(g, state)
    from pvp_db import db_game_get_hand
    h._current_bstate = state
    for card_uid, template_guid in db_game_get_hand(session.session_id, int(pid)):
        scid = _ge.SessionCardId(_ge.UID(int(card_uid)))
        try:
            _tpl, ct, name, cost, attack, defense, gem = h._card_full_data(g, scid, template_guid)
        except Exception:
            continue
        g.push_card_updated(scid, player_uid, _ge.ECardCollections.Hand, ct, template_id=template_guid, cost=cost, attack=attack, defense=defense, gems=gem, card_name=name)


def _pvp_add_troop_ability_options(g, session, state, pl_t, opp_t, pid, affordable):
    """Append card ability options (ECardUsage.Activate) to the most
    recent PlayerOptionList, one OptionInstance per affordable ability with
    target instances per target template — mirrors PvE _add_troop_ability_options."""
    if not g.events:
        return
    last_ev = next((event for event in reversed(g.events) if isinstance(event, _ge.PlayerOptionListSessionEventArgs)), None)
    if last_ev is None:
        return
    from rules_port.targeting import legal_targets_for
    champ_map = state.get('champ_map') or {}
    champ_targets = []
    for cpid in state.get('pids') or []:
        ccu = int(champ_map.get(str(cpid), 0))
        if ccu:
            champ_targets.append((ccu, cpid, 'Champ', int(state.get(f'hp_{cpid}', 20))))
    for (card_uid, tpl_guid), abilities in affordable.items():
        scid = _ge.SessionCardId(_ge.UID(int(card_uid)))
        opt = g.get_or_add_card_option(last_ev, scid, _ge.ECardUsage.Activate)
        for ag in abilities:
            inst = g._make_event(_ge.OptionInstanceSessionEventArgs)
            inst.opt_id = _ge.ResourceId.from_str(ag)
            graph = _resolve("ability_graph")(_RECORD_STORE, str(ag).lower())
            target_refs = tuple(((index, target) for index, target in enumerate(graph.targets) if target.requires_input)) if graph is not None else ()
            if target_refs:
                built = []
                for i, target in target_refs:
                    tid = target.guid
                    if target.is_auto or target.target_kind in ('PlayerTargetTemplate', 'AbilitySourceCardTargetTemplate', 'AbilityCreatedTargetTemplate'):
                        continue
                    built.append(i)
                    others = legal_targets_for(_resolve("_db"), session.session_id, pid, target, int(card_uid), champions=champ_targets, battle_state=state)
                    if not others:
                        filt = target.card_filter
                        if hasattr(filt, 'to_dict'):
                            filt = filt.to_dict()
                        if not filt:
                            others = [r[0] for r in _resolve('db_card_uids_in_zone')(session.session_id, pid, 'warzone', conn=_resolve("_db"))]
                    tgt = g._make_event(_ge.TargetInstanceSessionEventArgs)
                    tgt.target_index = i
                    tgt.target_id = _ge.ResourceId.from_str(tid)
                    tgt.targets = [_ge.SessionCardId(_ge.UID(int(u))) for u in others]
                    inst.target_ids.append(_ge.ResourceId.from_str(tid))
                    inst.target_instances.append(tgt)
                if built:
                    inst.min_target_counts = [1] * len(built)
                    inst.max_target_counts = [1] * len(built)
                else:
                    inst.min_target_counts = []
                    inst.max_target_counts = []
            discard_prompt = _pvp_discard_prompt_data(ag)
            child_instance = None
            if discard_prompt and discard_prompt[1]:
                child_ability, discard_target = discard_prompt
                hand = [_ge.SessionCardId(_ge.UID(int(r[0]))) for r in _resolve('db_game_get_hand')(session.session_id, int(pid))]
                if hand:
                    child = g._make_event(_ge.OptionInstanceSessionEventArgs)
                    child.opt_id = _ge.ResourceId.from_str(child_ability)
                    child.target_ids.append(_ge.ResourceId.from_str(discard_target))
                    child.min_target_counts = [1]
                    child.max_target_counts = [1]
                    child_target = g._make_event(_ge.TargetInstanceSessionEventArgs)
                    child_target.target_index = 0
                    child_target.target_id = _ge.ResourceId.from_str(discard_target)
                    child_target.targets = hand
                    child.target_instances.append(child_target)
                    child_instance = child
            cost_targets = _resolve("_pvp_ability_cost_targets")(session, state, pid, int(card_uid), ag, champ_targets)
            for tid, cost_type, candidates, minimum, maximum in cost_targets or []:
                cost_ev = g._make_event(_ge.CostInstanceSessionEventArgs)
                cost_ev.min_target_count = minimum
                cost_ev.max_target_count = maximum
                cost_ev.cost_type = cost_type
                cost_ev.targets = [_ge.SessionCardId(_ge.UID(int(uid))) for uid in candidates]
                cost_ev.target_template_id = _ge.ResourceId.from_str(tid)
                inst.target_instances.append(cost_ev)
            opt.instances.append(inst)
            if child_instance is not None:
                opt.instances.append(child_instance)


def _pvp_affordable_troop_abilities(session, state, pid=None):
    """Return {(card_uid, tpl_guid): [ability_guid, ...]} for the priority
    player's cards whose MANUAL abilities are activatable — mirrors PvE
    _affordable_troop_abilities (collection/phase gating, cost, uses limits,
    exhaust-as-cost, legal targets, ability condition)."""
    import json as _js
    from rules_port.conditions import ConditionContext, trigger_condition_met
    from rules_port.triggers import trigger_collection_allows
    from rules_port.targeting import legal_targets_for
    from rules_port.lifecycle import COMBAT_STEPS
    pids = _resolve("db_game_session_pids")(session.session_id)
    if len(pids) < 2:
        return {}
    turn_pid = int(state.get('turn_pid') or 0)
    ability_pid = int(pid if pid is not None else turn_pid)
    phase = int(state.get('phase', 0))
    resources = int(state.get(f'res_{ability_pid}', 0))
    champ_map = state.get('champ_map') or {}
    champ_targets = []
    for cpid in state.get('pids') or []:
        ccu = int(champ_map.get(str(cpid), 0))
        if ccu:
            champ_targets.append((ccu, cpid, 'Champ', int(state.get(f'hp_{cpid}', 20))))
    rows = _resolve('db_ability_option_cards')(session.session_id, ability_pid, conn=_resolve("_db"))
    result = {}
    for card_uid, tpl_guid, card_state, attrs, card_type, card_location in rows:
        card_ability_payload = _resolve('db_card_ability_payload')(session.session_id, int(card_uid), conn=_resolve("_db"))
        ab_list = []
        if card_ability_payload:
            try:
                ab_list = _js.loads(card_ability_payload)
            except Exception:
                ab_list = []
        if card_ability_payload is None:
            template_ability_payload = _resolve('db_template_ability_payload')(tpl_guid, conn=_resolve("_db"))
            if template_ability_payload:
                try:
                    ab_list = _js.loads(template_ability_payload)
                except Exception:
                    ab_list = []
        if not ab_list:
            continue
        affordable = []
        for ag in ab_list:
            ag = str(ag)
            graph = _resolve("ability_graph")(_RECORD_STORE, ag.lower())
            if graph is None:
                continue
            casting = 64 if graph.casting_behavior == 'QuickAction' else 8
            if not graph.manual:
                continue
            if not trigger_collection_allows(getattr(graph, 'trigger_collection_flags', ''), card_location):
                continue
            cost = graph.costs.activation
            upg = graph.costs.uses_per_game
            upt = graph.costs.uses_per_turn
            exh = graph.costs.exhausts_card_on_use
            cond_ctx = ConditionContext(_resolve("_db"), session, state, ability_source_uid=int(card_uid), ability_source_owner_id=ability_pid)
            if not trigger_condition_met(graph.source.to_dict(), cond_ctx):
                continue
            target_refs = graph.targets
            if target_refs:
                wants_attacking = False
                has_target = False
                for target in target_refs:
                    target_filter = target.card_filter
                    if hasattr(target_filter, 'to_dict'):
                        target_filter = target_filter.to_dict()
                    if 'IsAttacking' in json.dumps(target_filter or {}):
                        wants_attacking = True
                    if target.is_auto or target.target_kind in ('PlayerTargetTemplate', 'AbilitySourceCardTargetTemplate', 'AbilityCreatedTargetTemplate'):
                        has_target = True
                        continue
                    cands = legal_targets_for(_resolve("_db"), session.session_id, ability_pid, target, int(card_uid), champions=champ_targets, battle_state=state)
                    if cands:
                        has_target = True
                if wants_attacking and phase not in COMBAT_STEPS:
                    continue
                if not has_target:
                    continue
            if exh:
                cstate = card_state or 0
                if cstate & _ge.ECardStates.Tapped:
                    continue
                is_troop = 'Troop' in str(card_type or '').split('|')
                if is_troop and (not cstate & _ge.ECardStates.StartedATurnOnYourSide) and (not (attrs or 0) & _ge.ECardAttributes.Speed):
                    continue
            if casting != 64:
                if ability_pid != turn_pid or phase not in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
                    continue
            if (cost or 0) > resources:
                continue
            cost_targets = _resolve("_pvp_ability_cost_targets")(session, state, ability_pid, int(card_uid), ag, champ_targets)
            if cost_targets is None:
                continue
            from pvp_db import db_card_ability_use_counts, db_card_cooldown_counts
            used_game, used_turn = db_card_ability_use_counts(session.session_id, int(card_uid), ag, state.get('turn_number', 1), conn=_resolve("_db"))
            if graph.costs.cooldown and int(db_card_cooldown_counts(session.session_id, int(card_uid), conn=_resolve("_db")).get(ag.lower(), 0) or 0) > 0:
                continue
            if upg and used_game >= upg:
                continue
            if upt and used_turn >= upt:
                continue
            affordable.append(ag)
        if affordable:
            result[int(card_uid), tpl_guid] = affordable
    return result


def _pvp_defender_blockable_count(session, state):
    """Return how many of the defender's warzone troops can actually block at
    least one of the current attackers (mirrors the eligibility logic in
    pvp_push_blocker_options, without pushing any options — used to decide
    whether to auto-pass the defender through DeclareDefense)."""
    pids = _resolve("db_game_session_pids")(session.session_id)
    if len(pids) < 2:
        return 0
    turn_pid = state.get('turn_pid')
    defender_pid = pids[0] if pids[1] == turn_pid else pids[1]
    attackers = {int(k): int(v) for k, v in (state.get('attackers') or {}).items()}
    if not attackers:
        return 0
    from rules_port.combat_rules import can_block
    rows = _resolve('db_warzone_blocker_uids')(session.session_id, defender_pid, _ge.ECardStates.Tapped, conn=_resolve("_db"))
    count = 0
    view = _resolve("_pvp_fra_view")(state, defender_pid, turn_pid)
    for uid, in rows:
        for u in attackers:
            if can_block(_resolve("_db"), session.session_id, view, int(u), int(uid)):
                count += 1
                break
    return count


def _pvp_drive_f10_priority(session, state=None, *, max_steps=32):
    """Drain native priority windows for the player who pressed F10.

    ``SetAutoPass`` is a preference that lasts through the current turn.  A
    native phase transition can create a fresh ``PriorityWindowAction`` after
    the original pass has completed, so consuming only that first pass leaves
    the game waiting at the next main/end phase.  Mandatory input phases are
    represented by non-priority actions and therefore remain untouched.
    """
    from rules_port.kernel import PriorityWindowAction
    live = state if isinstance(state, dict) else _resolve("pvp_load_state")(session)
    if not isinstance(live, dict):
        return False
    autopass_pid = int(live.get('autopass_pid', 0) or 0)
    if not autopass_pid:
        return False
    port = getattr(session, '_rules_port_session', None)
    if port is None:
        return False
    progressed = False
    for _ in range(max(1, int(max_steps))):
        action = port.action_stack.peek()
        if not isinstance(action, PriorityWindowAction):
            break
        priority = action.priority_player_id
        if priority is None:
            break
        raw_priority = _uid_int(priority)
        priority_pid = raw_priority >> 8 if raw_priority & 255 == 244 else raw_priority
        if priority_pid != autopass_pid:
            break
        if not port.pass_priority_and_drive(priority):
            break
        progressed = True
        port.sync_to_pvp_state(live)
        _resolve('pvp_save_state')(session, live)
        live = _resolve("pvp_load_state")(session) or live
    return progressed


def _pvp_end_game(session, state, winner_pid, loser_pid, reason=''):
    """Publish the tournament result before ending the client game.

    The clients can transition to the tournament lobby as soon as they receive
    GameEnded.  Persist and publish the match first so that the lobby's cached
    TournamentInfo already contains the completed match when that transition
    starts.  The result publisher also sends the delayed rdata refresh used to
    cover the separate ServicePlayer/chat delivery paths.
    """
    pids = _resolve("db_game_session_pids")(session.session_id)
    if len(pids) < 2:
        return
    winner_uid = _ge.UID.make(244, winner_pid)
    loser_uid = _ge.UID.make(244, loser_pid)
    tournament_complete = False
    try:
        _resolve('record_tournament_game_result')(session, winner_pid, loser_pid)
        try:
            tid = _resolve('tournament_id_from_session_name')(session.session_name)
            tournament_complete = str((_resolve('db_tournament_by_id')(tid) or {}).get('status', '')).lower() == 'complete'
        except (TypeError, ValueError):
            tournament_complete = False
    except Exception as exc:
        log_req(f'    WARN: tournament result recording failed: {exc}')
    for pid in pids:
        h = _resolve('player_handlers').get(pid)
        if not h:
            continue
        my_uid = _ge.UID.make(244, pid)
        try:
            enable = _ge.Game(int(session.session_id), my_uid, _ge.UID.make(244, loser_pid if pid == winner_pid else winner_pid))
            enable.push_disable_interface(False)
            _resolve("_send_pvp_packet")(h, session, enable, my_uid, 'game-end-enable-input')
            import commands as _cmd
            _cmd.push_battle_game_end(h, session, [winner_uid], [loser_uid])
        except Exception:
            from domain.events import make_game_ended_packet
            nw = make_game_ended_packet(int(session.session_id), my_uid, [winner_uid], [loser_uid])
            dw = _resolve("encode_datawrapper")(0, 3055, _resolve("compress_gzip")(_resolve("encode_sync_event")(nw)), 1, _resolve("client_session_guid")(h))
            h.scnt += 1
            try:
                h.send({'issuer': f'0.0.0.0.ServiceGameSession.246.{session.session_id}.{h.scnt}', 'target': 'ServiceGameSession', 'instance': str(session.server_id), 'reqid': 0, 'c': 0, 'conh': 0, 'sid': h.sid}, dw)
            except OSError:
                log_req(f'    PvP game end: failed to push to pid {pid} (disconnected)')
            log_req(f'    PvP game end fallback sent to pid {pid}: winner {winner_pid} loser {loser_pid} ({reason})')
    try:
        session.set_state('ended')
    except Exception:
        pass
    try:
        _resolve('pvp_discard_session_lock')(session)
    except Exception:
        pass
    try:
        _resolve('pvp_discard_shared_port')(session)
    except Exception:
        pass
    if tournament_complete:
        _resolve('db_delete_game_session')(session.session_id)
        log_req(f'    Tournament complete: cleaned PvP session {session.session_id}')
    log_req(f'    PvP GAME OVER: pid {winner_pid} beats {loser_pid} ({reason}) — session ended')


def _pvp_populate_game_state(game, state, player_pid, opponent_pid):
    """Copy the persisted PvP HUD state onto a newly-created Game.

    PlayerUpdated reads its values from Game, whose defaults are 20 health and
    zero resources/charges.  Every PvP event stream that creates a fresh Game
    must populate it before pushing a PlayerUpdated event.
    """
    game.player_health = int(state.get(f'hp_{player_pid}', 20))
    game.ai_health = int(state.get(f'hp_{opponent_pid}', 20))
    game.player_resources = int(state.get(f'res_{player_pid}', 0))
    game.player_total_resources = int(state.get(f'res_total_{player_pid}', 0))
    game.ai_resources = int(state.get(f'res_{opponent_pid}', 0))
    game.ai_total_resources = int(state.get(f'res_total_{opponent_pid}', 0))
    game.player_charges = int(state.get(f'chg_{player_pid}', 0))
    game.ai_charges = int(state.get(f'chg_{opponent_pid}', 0))
    game.player_spell_points = int(state.get(f'sp_{player_pid}', 0))
    game.ai_spell_points = int(state.get(f'sp_{opponent_pid}', 0))
    game.player_threshold = _pvp_state_thresholds(state, player_pid)
    game.ai_threshold = _pvp_state_thresholds(state, opponent_pid)
    game.turn_number = int(state.get('turn_number', 1))


def _pvp_push_discard_prompt(session, state, my_pid, opp_pid, source_uid):
    """Send Stargazer's nested DiscardACard prompt to only its controller.

    The client predictively clears PlayerOptions when Stargazer is activated,
    so the child option advertised in the normal main-phase list no longer
    exists by the time the draw has completed.  Republish only the child
    option, with the post-draw hand as its target list, immediately before the
    class-23 activation request.  This is the same contract used by the PvE
    triggered-ability path: class 23 starts configuration, while the option's
    TargetInstance makes configuration open the hand-card picker.  Do not send
    class 39 here; that is the triggered-ability chooser and is a different UI
    path.
    """
    h = _resolve('player_handlers').get(int(my_pid))
    if not h:
        return False
    my_uid = _ge.UID.make(244, int(my_pid))
    opp_uid = _ge.UID.make(244, int(opp_pid))
    g = _ge.Game(int(session.session_id), my_uid, opp_uid)
    child = state.get('pending_discard_ability')
    target_id = state.get('pending_discard_target_template')
    if not child or not target_id:
        log_req('    PvP discard prompt: missing metadata target')
        return False
    hand = [_ge.SessionCardId(_ge.UID(int(row[0]))) for row in _resolve('db_game_get_hand')(session.session_id, int(my_pid))]
    if not hand:
        log_req('    PvP discard prompt: no hand target after draw')
        return False
    from pickers import PickRequest, PickTarget, present_pick
    present_pick(g, PickRequest(player_id=my_uid, prompt_ability=child, source_uid=int(source_uid), instance_id=1, targets=(PickTarget(index=0, template_id=target_id, candidates=tuple((int(scid.uid.uid64) for scid in hand)), minimum=1, maximum=1),), prompt_kind='ability'))
    _resolve("_send_pvp_packet")(h, session, g, my_uid, 'discard-prompt')
    return True


def _pvp_push_waiting_on(session, waiting_pid):
    """Tell each client who the game is waiting on (class 79).

    The acting player receives an invalid id (which clears their own waiting
    state and must not cover their open dialog), while the other client is
    told to wait on the acting player.  Pass ``None`` to clear both.
    """
    pids = [int(pid) for pid in _resolve("db_game_session_pids")(session.session_id) or []]
    if len(pids) < 2:
        return
    waiting_uid = _ge.UID.make(244, int(waiting_pid)) if waiting_pid else _ge.UID.invalid()
    for pid in pids:
        h = _resolve('player_handlers').get(pid)
        if not h:
            continue
        opp = next((value for value in pids if value != pid), pid)
        g = _ge.Game(int(session.session_id), _ge.UID.make(244, pid), _ge.UID.make(244, opp))
        if waiting_pid is not None and int(pid) == int(waiting_pid):
            g.push_waiting_on_player(None)
            event_desc = 'clear'
        else:
            g.push_waiting_on_player(waiting_uid)
            event_desc = str(waiting_pid) if waiting_pid else 'clear'
        _resolve("_send_pvp_packet")(h, session, g, _ge.UID.make(244, pid), f'waiting-on-player(to={pid},event={event_desc})')


def _pvp_ready_step(session, state, turn_pid, game=None, controller_uid=None, handler=None):
    """Run this turn's ready step for ``turn_pid`` once (C# ReadyState).

    Called at StartTurn before TurnStartedEvent and again at Prep, where it is
    a no-op if the side was already readied this turn.  When ``game`` is given
    each changed card is projected to it with the turn player as controller.
    Returns the ``ready_cards_for_turn`` change list, or None when skipped.
    """
    from rules_port.lifecycle import ready_step_for_turn
    changes = ready_step_for_turn(_resolve("_db"), session.session_id, turn_pid, state, str(turn_pid), handler=handler)
    tapped = int(_ge.ECardStates.Tapped)
    for uid, _template_guid, card_owner, previous, new_state in changes or ():
        if int(previous) & tapped and (not int(new_state) & tapped):
            _pvp_defer_trigger_event(state, event_type='CardReadiedEvent', source_card_id=int(uid), source_player_id=card_owner, data={'event_previous_state': int(previous)})
    if changes and game is not None:
        for uid, template_guid, _owner, _previous, new_state in changes:
            scid = _ge.SessionCardId(_ge.UID(int(uid)))
            if handler is not None:
                handler._card_full_data(game, scid, template_guid)
            ct_str = _resolve('db_game_card_type')(template_guid)
            game.push_card_updated(scid, controller_uid, _ECardCollections.Warzone, _ge.card_type_from_db(ct_str) if ct_str else _ECardTypes.Troop, template_id=template_guid, state=int(new_state))
    return changes


def _pvp_send_same_events(session, game, pl_t, ai_t):
    """Send the identical combat event stream to both players.  The events are
    objective — each card/player is referenced by its own UID — so the same
    payload renders correctly from either client's perspective.  The CardDefs
    registered on `game` (via _card_full_data) are ALSO carried to each
    player's fresh Game: CardUpdated events read the CardDef for cost/atk/def/
    abilities/attributes/gems, so without them a re-pushed card (e.g. a troop
    moving onto the opponent's chain) would render blank on the non-controller's
    client.  player_health/resources are copied too so any PlayerUpdated in the
    stream reports the real values, not the 20/0 defaults."""
    sink = getattr(getattr(session, '_rules_port_session', None), 'event_sink', None)
    if sink is not None:
        sink.drain_into(game)
    pids = [int(getattr(pl_t, 'uid64', pl_t)) >> 8, int(getattr(ai_t, 'uid64', ai_t)) >> 8]
    state = _resolve("pvp_load_state")(session) or {}
    visibility_handler = _resolve('player_handlers').get(pids[0]) or _resolve('player_handlers').get(pids[1])
    if visibility_handler is not None:
        previous_state = getattr(visibility_handler, '_current_bstate', None)
        visibility_handler._current_bstate = state
        try:
            from rules_port.visibility import project_visible_top_decks
            project_visible_top_decks(_resolve("_db"), session, visibility_handler, game, _ge.UID.make(244, pids[0]), _ge.UID.make(244, pids[1]), state)
        except Exception as exc:
            log_req(f'    PvP top-deck projection failed: {exc}')
        finally:
            if previous_state is None:
                try:
                    del visibility_handler._current_bstate
                except AttributeError:
                    pass
            else:
                visibility_handler._current_bstate = previous_state
    evs = list(game.events)
    game.events = []
    card_defs = dict(game.card_defs)
    for event in evs:
        if not isinstance(event, _ge.CardUpdatedSessionEventArgs):
            continue
        if event.collection != _ge.ECardCollections.Warzone:
            continue
        template_guid = getattr(getattr(event, 'card_id', None), 'guid', None)
        if template_guid is None or template_guid.int != 0:
            continue
        card_uid = int(event.session_card_id.uid.uid64)
        details = _resolve('db_card_zone_details')(session.session_id, card_uid, conn=_resolve("_db"))
        template_guid = details[0] if details else None
        if template_guid:
            event.card_id = _ge.ResourceId.from_str(template_guid)
            log_req(f'    PvP repaired invalid Warzone template for {hex(card_uid)} -> {template_guid}')
    try:
        _cls = []
        for _ev in evs:
            _c = getattr(type(_ev), 'CLASS_ID', 0)
            _cls.append(_c)

        def uid_val(u):
            u = getattr(u, 'uid', u)
            u = getattr(u, 'uid64', u)
            return int(u)
        _cards = [uid_val(c) for c in card_defs]
        log_req(f'    PvP send-audit: {len(evs)} events, classes={_cls} defs={[hex(x) for x in _cards]}')
    except Exception as _e:
        log_req(f'    PvP send-audit error: {_e}')
    health = game.player_health
    ai_health = game.ai_health
    p_res = game.player_resources
    p_tot = game.player_total_resources
    ai_res = game.ai_resources
    ai_tot = game.ai_total_resources
    p_chg = game.player_charges
    ai_chg = game.ai_charges
    for pid in pids:
        h = _resolve('player_handlers').get(pid)
        if not h:
            continue
        opp = pids[1] if pid == pids[0] else pids[0]
        g2 = _ge.Game(int(session.session_id), _ge.UID.make(244, pid), _ge.UID.make(244, opp))
        g2.events = [ev for ev in evs]
        g2.card_defs = dict(card_defs)
        g2._visibility_by_uid = dict(getattr(game, '_visibility_by_uid', {}) or {})
        g2.player_health = health
        g2.ai_health = ai_health
        g2.player_resources = p_res
        g2.player_total_resources = p_tot
        g2.ai_resources = ai_res
        g2.ai_total_resources = ai_tot
        g2.player_charges = p_chg
        g2.ai_charges = ai_chg
        _resolve("_send_pvp_packet")(h, session, g2, _ge.UID.make(244, pid), 'combat')


def _pvp_typed_picker_uid(typed_payload, target_index=None):
    """Read a selected card UID from a normalized activation TargetMap."""
    if not isinstance(typed_payload, dict):
        return None
    activation = typed_payload.get('activation_data', typed_payload.get('AbilityActivationData', {}))
    if not isinstance(activation, dict):
        return None
    target_map = activation.get('target_map', activation.get('TargetMap', {}))
    if not isinstance(target_map, dict):
        return None
    if target_index is None:
        selected_values = list(target_map.values())
    else:
        selected = target_map.get(target_index, target_map.get(str(target_index)))
        if selected is None:
            return None
        selected_values = [selected]
    for selected in selected_values:
        if isinstance(selected, dict):
            selected = next((selected[key] for key in ('session_card_ids', 'SessionCardIds', 'cards', 'Cards', 'targets', 'Targets') if key in selected), selected)
        values = selected if isinstance(selected, (list, tuple, set)) else (selected,)
        for value in values:
            while isinstance(value, dict):
                nested = next((value[key] for key in ('uid64', 'UID', 'm_UID64', 'value') if key in value), None)
                if nested is None or nested is value:
                    break
                value = nested
            try:
                uid = _uid_int(value)
            except (TypeError, ValueError):
                continue
            if uid > 0 and uid & 255 == 1:
                return uid
    return None


def _raw_player_id(value: Any) -> int:
    """Return the raw player id from either a raw id or ServicePlayer UID."""
    value = _uid_int(value)
    return value >> 8 if value & 255 == 244 else value


def campaign_session_reconnectable(session, raw_player_id=None):
    """Return whether a saved campaign battle (a ``camp_`` session with a live checkpoint) can be rejoined."""
    if not _resolve('CAMPAIGN_REJOIN_ENABLED'):
        return False
    if session is None or getattr(session, 'state', '') == 'ended' or (not str(getattr(session, 'session_name', '') or '').startswith('camp_')):
        return False
    checkpoint = getattr(session, 'turn_order', None)
    if not isinstance(checkpoint, dict) or 'turn_player' not in checkpoint or checkpoint.get('terminated'):
        return False
    if raw_player_id is not None and (not _session_has_reconnect_player(session, raw_player_id)):
        return False
    return True


def fra_session_reconnectable(session, raw_player_id=None):
    """Return whether an active saved FRA battle can be rejoined."""
    if session is None or getattr(session, 'state', '') == 'ended' or (not isinstance(getattr(session, 'encounter_data', None), dict)):
        return False
    flags = session.encounter_data.get('SessionFlags', 0)
    if isinstance(flags, dict):
        flags = flags.get('value__', 0)
    try:
        if not int(flags or 0) & int(_ge.ESessionFlags.IsPvEArena):
            return False
    except (TypeError, ValueError):
        return False
    checkpoint = getattr(session, 'turn_order', None)
    if not isinstance(checkpoint, dict) or 'turn_player' not in checkpoint or checkpoint.get('terminated'):
        return False
    saved_port = checkpoint.get('rules_port')
    if isinstance(saved_port, dict) and saved_port.get('terminated'):
        return False
    if raw_player_id is not None and (not _session_has_reconnect_player(session, raw_player_id)):
        return False
    return True


@_pvp_locked
def handle_ready_for_game_setup(handler, session, pvp_ready, player_handlers):
    """Post-process a 22027 response for tournament PvP sessions."""
    if not session or not (session.session_name or '').startswith('tourney-'):
        return (None, False)
    import struct, hashlib
    from binascii import hexlify as _hx
    pids = _resolve("db_game_session_pids")(session.session_id)
    my_pid = int(handler.client_reck_id) if hasattr(handler, 'client_reck_id') else 0
    wire_pid = my_pid
    player_uid_val = wire_pid << 8 | 244
    resp_inner = None
    if len(pids) >= 2:
        opp_pid = pids[0] if pids[1] == my_pid else pids[1]
        wire_opp_pid = opp_pid
        opp_uid_val = wire_opp_pid << 8 | 244
        sess_id = int(session.session_id) if isinstance(session.session_id, int) else 0
        h = hashlib.md5(f'{sess_id}:{pids[0]}:{pids[1]}'.encode()).digest()
        goes_first_pid = pids[0] if h[0] & 1 else pids[1]
        goes_first_wire_pid = goes_first_pid
        goes_first_uid = goes_first_wire_pid << 8 | 244
        goes_second_pid = pids[1] if goes_first_pid == pids[0] else pids[0]
        goes_second_wire_pid = goes_second_pid
        goes_second_uid = goes_second_wire_pid << 8 | 244
        from services.tournament_game import pvp_save_state
        state = _resolve("pvp_load_state")(session) or pvp_default_state(goes_first_pid, goes_first_pid)
        state['goes_first_pid'] = goes_first_pid
        pvp_save_state(session, state)
        turn_order_vals = [goes_first_uid, goes_second_uid]
        try:
            from binascii import hexlify as _hx
            opp_hex = _hx(struct.pack('<Q', opp_uid_val)).decode('ascii')
            from encoder import encode_objfmt_response
            resp_inner = encode_objfmt_response(['Game.Client.Network.LoadBalancer.ReadyForGameSetupResponse', 'Game.Shared.SessionState', 'Game.Shared.UID', 'Game.Shared.ResourceId', 'System.Guid', 'System.Collections.Generic.List`1#Game.Shared.PlayerState', 'Game.Shared.PlayerState', 'System.Collections.Generic.List`1#Game.Shared.UID', 'System.UInt64', 'System.Int32', 'Game.Shared.Network.LoadBalancer.EReadyForGameSetupError', 'System.String'], [('SessionState', 'struct', ('Game.Shared.SessionState', [('SessionId', 'uid', int(session.session_id)), ('SessionName', 'string', session.session_name or 'tourney-0'), ('MinimumPlayerCount', 'int', 2), ('MaximumPlayerCount', 'int', 2), ('EncounterData', 'class', 'Game.Shared.SessionStateEncounterData'), ('JoinInsteadOfReconnect', 'bool', False)])), ('DeckId', 'uid', player_uid_val), ('DeckTemplateId', 'struct', ('Game.Shared.ResourceId', [('guid', 'guid', '00000000-0000-0000-0000-000000000000')])), ('OpponentsInfo', 'playerstate_coll', ('System.Collections.Generic.List`1#Game.Shared.PlayerState', [(opp_hex, 1, b'')])), ('TurnOrder', 'uidlist', ('System.Collections.Generic.List`1#Game.Shared.UID', 0, turn_order_vals)), ('seedZ', 'ulong', 22222), ('seedW', 'ulong', 11111), ('Error', 'enum1', ('Game.Shared.Network.LoadBalancer.EReadyForGameSetupError', 0)), ('ErrorMessage', 'string', '')])
            log_req(f'    OpponentsInfo: my_pid={my_pid} opp_pid={opp_pid} opp_uid_val={opp_uid_val:#x} data={len(resp_inner)}b')
        except Exception as e:
            import traceback
            log_req(f'    OpponentsInfo encoding FAILED: {e}\n{traceback.format_exc()}')
            resp_inner = None
    game_started = False
    try:
        pvp_ready.setdefault(session.session_id, {'handlers': {}})
        pr = pvp_ready[session.session_id]
        puid = handler.client_reck_id if hasattr(handler, 'client_reck_id') else 0
        if puid not in pr['handlers']:
            pr['handlers'][puid] = []
        pr['handlers'][puid].append(handler)
        log_req(f"    PvP ready: {len(pr['handlers'])}/2 for tourney session {session.session_id}")
    except Exception as e:
        import traceback
        log_req(f'    PvP ready section FAILED: {e}\n{traceback.format_exc()}')
    log_req(f'    handle_ready: opp_resp={resp_inner is not None}')
    return (resp_inner, game_started)


def _pvp_apply_card_play_costs(handler, game, session, state, pl_t, opp_t, selections, source_uid, scrounge=False):
    """Apply card and ability CostInstances before a card enters the chain."""
    from pvp_db import db_discard_card, db_randomly_insert_deck_cards
    cost_list_names = {'sacrifice': 'SacrificedCards', 'discard': 'DiscardedCards', 'void': 'VoidedCards', 'put_into_deck': 'CardsPutIntoDeck', 'shuffle_into_deck': 'CardsPutIntoDeck', 'put_into_hand': 'CardsPutIntoHand', 'exhaust': 'ExhaustedCards'}
    voided_uids = []

    def record_cost_card(kind, uid):
        list_name = cost_list_names.get(str(kind).lower())
        if list_name:
            state.setdefault('ability_lists', {}).setdefault(list_name, []).append(int(uid))

    def push_zone(uid, owner_pid, location):
        row = _resolve('db_card_zone_projection')(session.session_id, uid, conn=_resolve('_db'))
        if not row:
            return
        scid = _ge.SessionCardId(_ge.UID(int(uid)))
        handler._card_full_data(game, scid, row[0])
        owner = _ge.UID.make(244, int(owner_pid))
        collection = {'hand': _ge.ECardCollections.Hand, 'deck': _ge.ECardCollections.Deck, 'discard': _ge.ECardCollections.Discard, 'void': _ge.ECardCollections.Void, 'warzone': _ge.ECardCollections.Warzone}.get(location, _ge.ECardCollections.Warzone)
        cdef = game.card_defs.get(scid)
        game.push_card_updated(scid, owner, collection, _ge.card_type_from_db(row[1]), template_id=row[0], state=int(row[2] or 0), cost=cdef.cost if cdef else 0, attack=cdef.attack if cdef else 0, defense=cdef.defense if cdef else 0, nulling=location == 'deck')
    for spec, selected in selections:
        kind = spec['kind']
        for uid in selected:
            uid = int(uid)
            row = _resolve('db_card_owner_zone_state')(session.session_id, uid, conn=_resolve('_db'))
            if not row:
                continue
            owner_pid = int(row[0] or 0)
            if kind == 'sacrifice':
                handler._sacrifice_troop(game, session, pl_t, opp_t, uid)
                record_cost_card(kind, uid)
                continue
            if kind == 'exhaust':
                _resolve('db_set_card_state_or')(session.session_id, uid, _ge.ECardStates.Tapped)
                _resolve('_db').commit()
                push_zone(uid, owner_pid, row[1])
                record_cost_card(kind, uid)
                continue
            destination = {'discard': 'discard', 'void': 'void', 'put_into_deck': 'deck', 'shuffle_into_deck': 'deck', 'put_into_hand': 'hand'}.get(kind)
            if destination is None:
                if kind == 'reveal':
                    scid = _ge.SessionCardId(_ge.UID(uid))
                    owner = _ge.UID.make(244, owner_pid)
                    event = game._make_event(_ge.CardsRevealedSessionEventArgs)
                    event.player_id = owner
                    event.session_card_ids = [scid]
                    event.collections = [_ge.ECardCollections.Warzone]
                    event.owning_players = [owner]
                    event.positions = [0]
                    game._push(event)
                continue
            if destination == 'discard':
                db_discard_card(session.session_id, uid, connection=_resolve('_db'))
            elif destination == 'hand':
                _resolve('db_set_card_location')(session.session_id, uid, 'hand', extra_set='position=?', extra_params=[100])
                _resolve('_db').commit()
            else:
                _resolve('db_set_card_location')(session.session_id, uid, destination, extra_set='position=?, card_state=?', extra_params=[0, 0])
                _resolve('_db').commit()
                if destination == 'deck':
                    db_randomly_insert_deck_cards(session.session_id, owner_pid, [uid], connection=_resolve('_db'))
                if destination == 'void':
                    voided_uids.append(uid)
            push_zone(uid, owner_pid, destination)
            record_cost_card(kind, uid)
            if destination in ('discard', 'void'):
                _resolve('_pvp_dispatch_triggers')(handler, game, session, state, pl_t, opp_t, 'CardEnteredZoneEvent', uid, owner_pid, event_source_collection=row[1], event_destination_collection=destination, event_previous_state=int(row[2] or 0))
    if scrounge and voided_uids:
        entries = state.setdefault('ability_lists', {}).setdefault('VoidedCards', [])
        entries.extend((uid for uid in voided_uids if uid not in entries))
        source_owner = db_card_owner_id(session.session_id, int(source_uid), conn=_resolve('_db'))
        _resolve('_pvp_dispatch_triggers')(handler, game, session, state, pl_t, opp_t, 'CardScroungedEvent', int(source_uid), source_owner_id=int(source_owner) if source_owner is not None else _resolve('_raw_player_id')(pl_t), target_card_id=int(source_uid), event_tac={'voided_cards': list(voided_uids)})


def _pvp_check_game_end(session, state):
    """After combat/chain resolution, if either champion's health is <= 0 the
    game ends (mirrors PvE _check_champion_health).  Returns True when ended."""
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return False
    for pid in pids:
        if int(state.get(f'hp_{pid}', 20)) > 0:
            continue
        other = pids[1] if pid == pids[0] else pids[0]
        event_handler = _resolve('player_handlers').get(pid) or _resolve('player_handlers').get(other)
        if not event_handler:
            continue
        pl_uid = _ge.UID.make(244, pid)
        opp_uid = _ge.UID.make(244, other)
        game = _ge.Game(int(session.session_id), pl_uid, opp_uid)
        game.player_health = int(state.get(f'hp_{pid}', 20))
        game.ai_health = int(state.get(f'hp_{other}', 20))
        view = _resolve('_pvp_fra_view')(state, pid, other)
        _resolve('_pvp_dispatch_triggers')(event_handler, game, session, view, pl_uid, opp_uid, 'ChampionWouldLoseEvent', int((state.get('champ_map') or {}).get(str(pid), 0) or 0), pid)
        if game.events:
            _resolve('_pvp_send_same_events')(session, game, pl_uid, opp_uid)
        _resolve('_pvp_sync_view_to_state')(state, view, pid, other)
        if view.get('stack'):
            state['stack'] = view['stack']
        _resolve('pvp_save_state')(session, state)
    for pid in pids:
        if int(state.get(f'hp_{pid}', 20)) <= 0:
            other = pids[1] if pid == pids[0] else pids[0]
            _resolve('_pvp_end_game')(session, state, other, pid, 'champion at 0 health')
            return True
    return False


def _pvp_gain_charge_trigger_game(handler, session, state, owner_id, amount=1):
    """Build the objective event stream for a PvP charge-gain trigger."""
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return None
    owner_id = int(owner_id)
    opp_pid = pids[0] if pids[1] == owner_id else pids[1]
    owner_handler = _resolve('player_handlers').get(owner_id) or handler
    owner_handler._current_bstate = state
    pl_uid = _ge.UID.make(244, owner_id)
    opp_uid = _ge.UID.make(244, opp_pid)
    game = _ge.Game(int(session.session_id), pl_uid, opp_uid)
    _resolve('_pvp_populate_game_state')(game, state, owner_id, opp_pid)
    source_uid = int((state.get('champ_map') or {}).get(str(owner_id), 0) or 0)
    if not source_uid:
        champion = getattr(owner_handler, '_player_champ_scid', None) or getattr(owner_handler, '_ai_champ_scid', None)
        source_uid = int(champion.uid.uid64) if champion is not None else 0
    try:
        amount = max(0, int(amount or 0))
    except (TypeError, ValueError):
        amount = 0
    for _ in range(amount):
        _resolve('_pvp_dispatch_triggers')(owner_handler, game, session, state, pl_uid, opp_uid, 'GainChargeEvent', source_uid, owner_id)
    return game


def _pvp_gain_threshold_trigger_game(handler, session, state, owner_id, color):
    """Build the shared event stream for a PvP threshold gain."""
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return None
    owner_id = int(owner_id)
    opp_pid = pids[0] if pids[1] == owner_id else pids[1]
    owner_handler = _resolve('player_handlers').get(owner_id) or handler
    owner_handler._current_bstate = state
    pl_uid = _ge.UID.make(244, owner_id)
    opp_uid = _ge.UID.make(244, opp_pid)
    game = _ge.Game(int(session.session_id), pl_uid, opp_uid)
    _resolve('_pvp_populate_game_state')(game, state, owner_id, opp_pid)
    source_uid = int((state.get('champ_map') or {}).get(str(owner_id), 0) or 0)
    if not source_uid:
        champion = getattr(owner_handler, '_player_champ_scid', None) or getattr(owner_handler, '_ai_champ_scid', None)
        source_uid = int(champion.uid.uid64) if champion is not None else 0
    _resolve('_pvp_dispatch_triggers')(owner_handler, game, session, state, pl_uid, opp_uid, 'GainThresholdEvent', source_uid, owner_id, gain_threshold_color=int(color))
    return game


def _pvp_resolve_granted_resource_abilities(handler, session, state, card_uid, owner_pid, *, printed_skip=None):
    """Resolve instance-only resource abilities on a shared PvP event view.

    When ``printed_skip`` is given, the resource's printed non-triggered
    abilities are resolved too (as the PvE play path does), skipping the GUIDs
    in that set.
    """
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return []
    owner_pid = int(owner_pid)
    opponent_pid = next((pid for pid in pids if int(pid) != owner_pid))
    owner_handler = _resolve('player_handlers').get(owner_pid) or handler
    pl_uid = _ge.UID.make(244, owner_pid)
    opp_uid = _ge.UID.make(244, opponent_pid)
    view = dict(state)
    view.update({'pvp': True, 'player_resources': int(state.get(f'res_{owner_pid}', 0)), 'player_total_resources': int(state.get(f'res_total_{owner_pid}', 0)), 'player_charges': int(state.get(f'chg_{owner_pid}', 0)), 'player_threshold': dict(state.get(f'thresh_{owner_pid}') or {}), 'ai_resources': int(state.get(f'res_{opponent_pid}', 0)), 'ai_total_resources': int(state.get(f'res_total_{opponent_pid}', 0)), 'ai_charges': int(state.get(f'chg_{opponent_pid}', 0)), 'ai_threshold': dict(state.get(f'thresh_{opponent_pid}') or {})})
    owner_handler._current_bstate = view
    game = _ge.Game(int(session.session_id), pl_uid, opp_uid)
    _resolve('_pvp_populate_game_state')(game, state, owner_pid, opponent_pid)
    from rules_port.resources import resolve_granted_resource_abilities, resolve_printed_resource_abilities
    from rules_port.resolution import resolve_port_ability
    logs = resolve_granted_resource_abilities(game, session, _resolve('_db'), owner_handler, pl_uid, opp_uid, view, int(card_uid), owner_pid, resolver=lambda _handler, _game, _session, _db_conn, _pl_t, _ai_t, _bstate, guid, source, owner, target_map: resolve_port_ability(_handler, _game, _session, _db_conn, _pl_t, _ai_t, _bstate, guid, source, owner, target_map=target_map))
    if printed_skip is not None:
        logs += resolve_printed_resource_abilities(game, session, _resolve('_db'), owner_handler, pl_uid, opp_uid, view, int(card_uid), owner_pid, skip_guids=printed_skip)
    _resolve('_pvp_sync_view_to_state')(state, view, owner_pid, opponent_pid)
    _resolve('pvp_save_state')(session, state)
    if logs:
        log_req('    PvP resource granted abilities: ' + '; '.join(logs))
    return game.events


def _pvp_run_draw(session, state):
    """The Draw phase: the turn player draws one card (except the play-first
    player on turn 1), handles inline draw replacements, queues draw-related
    trigger events for FirstMainPhase, and returns (drawer_events, opp_events)
    for each client's packet — or None when nothing was drawn. The drawn card
    representation remains private to its owner."""
    if _resolve('port_skips_draw_phase')(state):
        return None
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return None
    turn_pid = state.get('turn_pid')
    opp_pid = pids[0] if pids[1] == turn_pid else pids[1]
    turn_num = int(state.get('turn_number', 1))
    if not (turn_num > 1 or state.get('draws_first_pid') == turn_pid):
        return None
    drawn = _resolve('db_game_draw_cards')(session.session_id, turn_pid, 1)
    if not drawn:
        _resolve('_pvp_end_game')(session, state, opp_pid, turn_pid, 'deck empty on draw')
        return None
    cu, tg = drawn[0]
    scid = _ge.SessionCardId(_ge.UID(int(cu)))
    ct_str = _resolve('db_game_card_type')(tg)
    ct = _ge.card_type_from_db(ct_str) if ct_str else _ECardTypes.Troop
    turn_uid_p = _ge.UID.make(244, turn_pid)
    opp_uid_p = _ge.UID.make(244, opp_pid)
    draw_h = _resolve('player_handlers').get(turn_pid)
    g = _ge.Game(int(session.session_id), turn_uid_p, opp_uid_p)
    g.player_health = int(state.get(f'hp_{turn_pid}', 20))
    g.ai_health = int(state.get(f'hp_{opp_pid}', 20))
    try:
        view = _resolve('_pvp_fra_view')(state, turn_pid, opp_pid)
        repl_draw = _resolve('_pvp_dispatch_triggers')(draw_h, g, session, view, turn_uid_p, opp_uid_p, 'CardWouldBeDrawnEvent', None, turn_pid) if draw_h else None
        repl_zone = _resolve('_pvp_dispatch_triggers')(draw_h, g, session, view, turn_uid_p, opp_uid_p, 'CardWouldEnterZoneEvent', int(cu), turn_pid) if draw_h else None
        if repl_draw or repl_zone:
            _resolve('pvp_save_state')(session, state)
            return (list(g.events), [])
    except Exception as e:
        log_req(f'    PvP draw replacement trigger error: {e}')
    _d_tpl, _d_ct, _d_nm, d_cost, d_atk, d_def, _d_gx = draw_h._card_full_data(g, scid, tg) if draw_h else (tg, ct, '', 0, 0, 0, 0)
    g.push_card_updated(scid, turn_uid_p, _ECardCollections.Hand, ct, template_id=tg, cost=d_cost, attack=d_atk, defense=d_def, gems=_d_gx)
    g.push_card_drawn(scid, turn_uid_p, 1)
    _pvp_defer_trigger_event(state, event_type='CardEnteredZoneEvent', source_card_id=int(cu), source_player_id=turn_pid, target_card_id=int(cu), target_player_id=turn_pid, phase=int(_ge.ETurnPhases.Draw), data={'event_source_collection': 'Deck', 'event_destination_collection': 'Hand'})
    champ_map = state.get('champ_map') or {}
    champ_uid = int(champ_map.get(str(turn_pid), 0)) or None
    _pvp_defer_trigger_event(state, event_type='CardDrawnEvent', source_card_id=champ_uid, source_player_id=turn_pid, target_card_id=int(cu), target_player_id=turn_pid, phase=int(_ge.ETurnPhases.Draw))
    state['stack_passed'] = []
    _resolve('pvp_save_state')(session, state)
    g2 = _ge.Game(int(session.session_id), turn_uid_p, opp_uid_p)
    g2.events = []
    g2.card_defs = dict(g.card_defs)
    g2.push_card_updated(scid, turn_uid_p, _ECardCollections.Hand, ct, template_id=tg, nulling=True)
    for event in g.events:
        if getattr(event, 'session_card_id', None) == scid and event.__class__.__name__ in ('CardMovedSessionEventArgs', 'CardDrawnSessionEventArgs', 'CardUpdatedSessionEventArgs'):
            continue
        g2._push(event)
    log_req(f"    PvP draw: pid {turn_pid} drew card {cu} ({_d_nm or ''})")
    return (list(g.events), list(g2.events))


def _pvp_sync_game_state(session):
    """Push PlayerUpdated to both players after a state change so each
    client sees current health / charges / champion."""
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return
    state = _resolve('pvp_load_state')(session)
    if not state:
        return
    champ_map = state.get('champ_map', {})
    for pid in pids:
        h = _resolve('player_handlers').get(pid)
        if not h:
            continue
        pl_uid = _ge.UID.make(244, pid)
        opp = pids[1] if pid == pids[0] else pids[0]
        opp_uid = _ge.UID.make(244, opp)
        g = _ge.Game(int(session.session_id), pl_uid, opp_uid)
        _pvp_apply_visibility(g, state)
        g.player_health = int(state.get(f'hp_{pid}', 20))
        g.ai_health = int(state.get(f'hp_{opp}', 20))
        g.player_resources = int(state.get(f'res_{pid}', 0))
        g.player_total_resources = int(state.get(f'res_total_{pid}', 0))
        g.ai_resources = int(state.get(f'res_{opp}', 0))
        g.ai_total_resources = int(state.get(f'res_total_{opp}', 0))
        _resolve('_pvp_populate_game_state')(g, state, pid, opp)
        for target_pid in pids:
            target_uid = _ge.UID.make(244, target_pid)
            cu = int(champ_map.get(str(target_pid), 0))
            champ_scid = _ge.SessionCardId(_ge.UID(cu)) if cu else None
            g.push_player_updated(target_uid, champ_id=champ_scid)
        if g.events:
            pkt = g.make_network_packet(pl_uid)
            dw = _resolve('encode_datawrapper')(0, 3055, _resolve('compress_gzip')(_resolve('encode_sync_event')(pkt)), 1, _resolve('client_session_guid')(h))
            h.scnt += 1
            h.send({'issuer': f'0.0.0.0.ServiceGameSession.246.{session.session_id}.{h.scnt}', 'target': 'ServiceGameSession', 'instance': str(session.server_id), 'reqid': 0, 'c': 0, 'conh': 0, 'sid': h.sid}, dw)
            log_req(f'    PvP sync: PlayerUpdated pushed to pid {pid}')


def handle_join_disconnected_game(handler, target, instance, reqid, comp, session_id, conh, inner_obj, inner_bytes, log_req=log_req, **_kw):
    """Handle DT 22023 — JoinDisconnectedGame for saved battle sessions.
    
    The client sends this after accepting TryReconnectionToDisconnectedGame
    and transitioning to Battle. Return the requested active session so the
    client adds its local player and sends ReadyToContinueGame.
    """
    log_req('>>> JoinDisconnectedGame (dt=22023)')
    player_uid_val = _objfmt_uid_field(inner_obj, inner_bytes, 'PlayerId')
    player_uid = player_uid_val
    player_pid = _pvp_raw_player_id(player_uid_val)
    requested_session_id = _objfmt_uid_field(inner_obj, inner_bytes, 'SessionId')
    log_req(f'    PlayerId UID={player_uid:#x} raw_pid={player_pid} SessionId={requested_session_id:#x}')
    import game_session as gs
    session = gs.find_session_by_id(requested_session_id) if requested_session_id else None
    if session is None and (not requested_session_id):
        session = gs.find_session_by_player(player_pid)
    if session and (getattr(session, 'state', '') == 'ended' or not _session_has_reconnect_player(session, player_pid)):
        session = None
    is_tournament = bool(session and str(getattr(session, 'session_name', '') or '').startswith('tourney-'))
    is_fra = bool(session and _resolve('fra_session_reconnectable')(session, player_pid))
    is_campaign = bool(session and _resolve('campaign_session_reconnectable')(session, player_pid))
    if not (is_tournament or is_fra or is_campaign):
        session = None
    if not session:
        log_req(f'    No session found for player {player_uid:#x}')
        resp_inner = _resolve('encode_objfmt_response')(['Game.Client.Network.LoadBalancer.JoinDisconnectedGameResponse', 'Game.Shared.UID'], [('RoutingPlayerId', 'uid', 0)])
    else:
        if is_tournament:
            with _resolve('player_handler_lock'):
                _resolve('player_handlers')[player_pid] = handler
        resp_inner = _resolve('encode_objfmt_response')(['Game.Client.Network.LoadBalancer.JoinDisconnectedGameResponse', 'Game.Shared.UID'], [('RoutingPlayerId', 'uid', player_uid)])
    resp_body = _resolve('compress_gzip')(resp_inner) if comp else resp_inner
    resp_reqid = reqid | 1
    dw_bytes = _resolve('encode_datawrapper')(resp_reqid, 22023, resp_body, comp, session_id)
    issuer_str = f'0.0.0.0.ServiceLoadBalancer.252.ServicePlayer.{handler.client_uid}.{resp_reqid}'
    handler.scnt += 1
    handler.send({'issuer': issuer_str, 'target': target, 'instance': instance, 'reqid': resp_reqid, 'c': comp, 'conh': conh, 'sid': handler.sid}, dw_bytes)
    log_req(f'    Sent JoinDisconnectedGameResponse ({len(dw_bytes)}b)')


def push_pvp_game_start(handler, session, log_req=log_req):
    """Push initial battle events for a PvP tournament session (tourney-N)."""
    player_pid = int(handler.client_reck_id) if hasattr(handler, 'client_reck_id') else 0
    wire_pid = player_pid
    player_uid = player_pid
    sess_id = session.session_id.uid64 if hasattr(session.session_id, 'uid64') else int(session.session_id)
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        log_req('    PvP: need 2 players in session')
        return
    log_req(f'    push_pvp: player_uid={player_uid} pids={pids}')
    wire_opp_pid = pids[1] if pids[0] == player_pid else pids[0]
    if wire_opp_pid == wire_pid:
        wire_opp_pid = player_pid
    if pids[0] == player_pid:
        pl_t = _ge.UID.make(244, wire_pid)
        opp_t = _ge.UID.make(244, wire_opp_pid)
    else:
        pl_t = _ge.UID.make(244, wire_pid)
        opp_t = _ge.UID.make(244, wire_opp_pid)
    for pid in pids:
        _resolve('db_game_shuffle_deck')(session.session_id, pid)
    champ_health = {}
    pchamp = None
    achamp = None
    for pid in pids:
        champ_row = _resolve('db_game_champion')(session.session_id, pid)
        if champ_row:
            cid = _ge.SessionCardId(_ge.UID(int(champ_row[0])))
            hp = _resolve('db_champion_template_health')(champ_row[1]) or 20
            champ_health[pid] = hp
            ct = _ge.CardDef('Champion', _ECardTypes.Champion, 0, hp, hp, [], [])
            if pid == player_pid:
                pchamp = cid
            else:
                achamp = cid
    if not pchamp:
        pchamp = _ge.SessionCardId(_ge.UID.make(1, 0))
    if not achamp:
        achamp = _ge.SessionCardId(_ge.UID.make(1, 0))
    state = _resolve('pvp_load_state')(session)
    goes_first_pid = (state or {}).get('goes_first_pid', pids[0])
    if state is None:
        state = pvp_default_state(pids[0], goes_first_pid)
        state['pids'] = list(pids)
    mode_data = getattr(session, 'encounter_data', {}) or {}
    if mode_data.get('tournament_mode') == 'corinth_merry_melee':
        state['corinth_mode'] = True
        state['skip_draw_phase'] = True
        state['starting_hand_size'] = int(mode_data.get('starting_hand_size', 4) or 4)
    from rules_port.resources import begin_turn_resources_for_player
    for _pid in pids:
        begin_turn_resources_for_player(state, _pid)
    _resolve('pvp_save_state')(session, state)
    goes_first_wire_pid = wire_pid if goes_first_pid == player_pid else wire_opp_pid
    goes_first_uid = goes_first_wire_pid << 8 | 244
    log_req(f'    Coin flip: {hex(goes_first_uid)} goes first')
    champ_guids: list[str | None] = [None, None]
    champ_names = ['Player 1', 'Player 2']
    for pid in pids:
        idx = 0 if pid == player_pid else 1
        cr = _resolve('db_game_champion')(session.session_id, pid)
        if cr:
            champ_guids[idx] = cr[1]
        signup_name = _resolve('db_tournament_player_name_for_session')(session.session_id, pid)
        if signup_name:
            champ_names[idx] = signup_name
    if champ_guids[0] is None:
        champ_guids[0] = '00000000-0000-0000-0000-000000000000'
    if champ_guids[1] is None:
        champ_guids[1] = '00000000-0000-0000-0000-000000000000'
    game1 = _ge.Game(sess_id, pl_t, opp_t)
    game1.player_champion_card_id = pchamp
    game1.ai_champion_card_id = achamp
    _resolve('_pvp_populate_game_state')(game1, state or {}, player_pid, pids[1] if player_pid == pids[0] else pids[0])
    game1.push_game_started(champion_names=champ_names, champion_template_ids=champ_guids, player_first=goes_first_pid == player_pid)
    game1.push_first_player_dictated(_ge.UID.make(244, goes_first_wire_pid))
    log_req(f'    PvP start: pushed GameStarted + FirstPlayerDictated to pid {player_uid} (winner {goes_first_pid})')
    game1.push_player_updated(pl_t, champ_id=pchamp)
    game1.push_player_updated(opp_t, champ_id=achamp)
    for pid in pids:
        is_pl = pid == player_pid
        pt = pl_t if is_pl else opp_t
        ch_id = pchamp if is_pl else achamp
        cr = _resolve('db_game_champion')(session.session_id, pid)
        tg = cr[1] if cr else '00000000-0000-0000-0000-000000000000'
        hp = champ_health.get(pid, 20)
        handler._card_full_data(game1, ch_id, tg)
        game1.push_card_updated(ch_id, pt, _ECardCollections.None_, _ECardTypes.Champion, attack=0, defense=hp, template_id=tg)
        try:
            _cd = game1.card_defs.get(ch_id)
            _ab = list(_cd.abilities) if _cd else []
            log_req(f'    PvP champ CardUpdated {ch_id}: abilities={[str(a.guid)[:8] for a in _ab]}')
        except Exception:
            pass
    for pid in pids:
        is_pl = pid == player_pid
        pt = pl_t if is_pl else opp_t
        ch_id = pchamp if is_pl else achamp
        pn = champ_names[0] if is_pl else champ_names[1]
        game1.push_champion_card_played(pt, False, pn, ch_id)
    state = _resolve('pvp_load_state')(session) or state
    if state:
        champ_map = state.get('champ_map', {})
        for pid in pids:
            is_pl = pid == player_pid
            ch = pchamp if is_pl else achamp
            champ_map[str(pid)] = ch.uid.uid64
            state[f'hp_{pid}'] = champ_health.get(pid, 20)
        state['champ_map'] = champ_map
        guid_map = state.get('champ_guid_map', {})
        for pid in pids:
            idx = 0 if pid == player_pid else 1
            if idx < len(champ_guids) and champ_guids[idx]:
                guid_map[str(pid)] = champ_guids[idx]
        state['champ_guid_map'] = guid_map
        from rules_port.statistics import set_tac_stat, tac_stat
        for pid in pids:
            champ_uid = champ_map.get(str(pid))
            if champ_uid is None or tac_stat(state, 'cards', champ_uid, 'PlayerGameStats', 'StartingDeckSize', default=-1) != -1:
                continue
            set_tac_stat(state, 'cards', champ_uid, 'PlayerGameStats', 'StartingDeckSize', len(_resolve('db_game_deck_cards')(session.session_id, int(pid))))
        _resolve('pvp_save_state')(session, state)
        try:
            _resolve('_pvp_set_handler_champions')(handler, pchamp, achamp, champ_guids[0] if champ_guids else None, champ_guids[1] if len(champ_guids) > 1 else None)
        except Exception:
            pass
    pkt1 = game1.make_network_packet(pl_t)
    dw1 = _resolve('encode_datawrapper')(0, 3055, _resolve('compress_gzip')(_resolve('encode_sync_event')(pkt1)), 1, _resolve('client_session_guid')(handler))
    handler.scnt += 1
    handler.send({'issuer': f'0.0.0.0.ServiceGameSession.246.{session.session_id}.{handler.scnt}', 'target': 'ServiceGameSession', 'instance': str(session.server_id), 'reqid': 0, 'c': 0, 'conh': 0, 'sid': handler.sid}, dw1)
    game2 = _ge.Game(sess_id, pl_t, opp_t)
    game2.player_champion_card_id = pchamp
    game2.ai_champion_card_id = achamp
    game2.turn_number = 1
    deck_cards_by_pid = {int(pid): _resolve('db_game_deck_cards')(session.session_id, int(pid)) for pid in pids}
    deck_projections = _resolve('db_card_instance_dynamic_projections')(session.session_id, [int(card_uid) for cards in deck_cards_by_pid.values() for card_uid, _template_guid in cards], conn=_resolve('_db'))
    for pid in pids:
        is_me = pid == player_pid
        player_t = pl_t if is_me else opp_t
        deck_cards = deck_cards_by_pid[int(pid)]
        for cu, tg in deck_cards:
            scid = _ge.SessionCardId(_ge.UID(int(cu)))
            handler._card_full_data(game2, scid, tg, instance_projection=deck_projections.get(int(cu)))
            ct_str = _resolve('db_game_card_type')(tg)
            ct = _ge.card_type_from_db(ct_str) if ct_str else _ECardTypes.Troop
            game2.push_card_updated(scid, player_t, _ECardCollections.Deck, ct, template_id=tg, nulling=True)
    for pid in pids:
        is_me = pid == player_pid
        player_t = pl_t if is_me else opp_t
        game2.push_deck_created(player_t)
    if not state.get('pvp_pregame_done'):
        for owner_pid in pids:
            owner_handler = _resolve('player_handlers').get(int(owner_pid)) or handler
            owner_handler._current_bstate = state
            _resolve('_pvp_dispatch_triggers')(owner_handler, game2, session, state, pl_t, opp_t, 'PreGameEvent', None, int(owner_pid), zones=('deck',))
        state['pvp_pregame_done'] = True
    if not state.get('pvp_game_started_done'):
        for owner_pid in pids:
            owner_handler = _resolve('player_handlers').get(int(owner_pid)) or handler
            owner_handler._current_bstate = state
            _resolve('_pvp_dispatch_triggers')(owner_handler, game2, session, state, pl_t, opp_t, 'GameStartedEvent', None, int(owner_pid), zones=('hand', 'champions', 'warzone'))
        state['pvp_game_started_done'] = True
        _resolve('pvp_save_state')(session, state)
    turn_uid = _ge.UID.make(244, goes_first_pid)
    if player_pid == goes_first_pid:
        game2.push_green_light(turn_uid, _ge.EPriorityContext.Normal)
    game2.push_turn_phase(_ge.ETurnPhases.PickGoesFirst, turn_uid, turn_uid)
    pkt2 = game2.make_network_packet(pl_t)
    dw2 = _resolve('encode_datawrapper')(0, 3055, _resolve('compress_gzip')(_resolve('encode_sync_event')(pkt2)), 1, _resolve('client_session_guid')(handler))
    handler.scnt += 1
    handler.send({'issuer': f'0.0.0.0.ServiceGameSession.246.{session.session_id}.{handler.scnt}', 'target': 'ServiceGameSession', 'instance': str(session.server_id), 'reqid': 0, 'c': 0, 'conh': 0, 'sid': handler.sid}, dw2)
    log_req(f'    Pushed PvP game setup ({len(dw1)}+{len(dw2)}b)')
    log_req(f'    Pushed PickGoesFirst phase ({len(dw2)}b)')


@_pvp_locked
def pvp_concede(handler, session):
    """End a tournament PvP game when one player explicitly concedes."""
    if not session or not (session.session_name or '').startswith('tourney-'):
        return False
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return False
    try:
        loser_pid = int(handler.client_reck_id)
    except (AttributeError, TypeError, ValueError):
        return False
    if loser_pid not in pids:
        return False
    winner_pid = pids[0] if pids[1] == loser_pid else pids[1]
    state = _resolve('pvp_load_state')(session)
    _resolve('_pvp_end_game')(session, state, winner_pid, loser_pid, 'player conceded')
    return True


def pvp_push_blocker_options(session, state):
    """Push a PlayerOptionList enabling the DEFENDER (the non-turn player) to
    declare blockers during DeclareDefense.  The client's
    BattleStateDeclareBlockers only lets a troop block when
    State.HasUsage(troop, ECardUsage.Defend) AND
    State.GetTargetsFor(troop, ResourceId.Blocking) lists the attackers —
    mirrors PvE _push_blocker_options (hconnect ~2375).  Returns the number of
    defender troops that can block this attack (0 => the defender has nothing
    to block with and the phase can auto-advance)."""
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return
    turn_pid = state.get('turn_pid')
    defender_pid = pids[0] if pids[1] == turn_pid else pids[1]
    h = _resolve('player_handlers').get(defender_pid)
    if not h:
        return
    pl_t = _ge.UID.make(244, defender_pid)
    opp_t = _ge.UID.make(244, turn_pid)
    attackers = {int(k): int(v) for k, v in (state.get('attackers') or {}).items()}
    if not attackers:
        return
    from rules_port.combat_rules import can_block
    rows = _resolve('db_warzone_blocker_uids')(session.session_id, defender_pid, _ge.ECardStates.Tapped, conn=_resolve('_db'))
    g = _ge.Game(int(session.session_id), pl_t, opp_t)
    _pvp_apply_visibility(g, state)
    _resolve('_pvp_populate_game_state')(g, state, defender_pid, turn_pid)
    g.player_health = int(state.get(f'hp_{defender_pid}', 20))
    g.ai_health = int(state.get(f'hp_{turn_pid}', 20))
    g.player_resources = int(state.get(f'res_{defender_pid}', 0))
    g.ai_resources = int(state.get(f'res_{turn_pid}', 0))
    g.player_total_resources = int(state.get(f'res_total_{defender_pid}', 0))
    g.ai_total_resources = int(state.get(f'res_total_{turn_pid}', 0))
    g.player_charges = int(state.get(f'chg_{defender_pid}', 0))
    g.ai_charges = int(state.get(f'chg_{turn_pid}', 0))
    from pickers import BLOCK_DECLARATION_TEMPLATE, PickOption, PickTarget, present_options
    options = []
    for uid, in rows:
        blockable = tuple((int(u) for u in attackers if can_block(_resolve('_db'), session.session_id, _resolve('_pvp_fra_view')(state, turn_pid, defender_pid), int(u), int(uid))))
        if not blockable:
            continue
        options.append(PickOption(card_uid=int(uid), state='Defend', prompt_ability=BLOCK_DECLARATION_TEMPLATE, targets=(PickTarget(index=0, template_id=BLOCK_DECLARATION_TEMPLATE, candidates=blockable, minimum=0, maximum=len(blockable)),)))
    blockable_count = len(options)
    present_options(g, pl_t, options)
    g.push_player_updated(pl_t, champ_id=_ge.SessionCardId(_ge.UID(int(state.get('champ_map', {}).get(str(defender_pid), 0)))))
    g.push_player_updated(opp_t, champ_id=_ge.SessionCardId(_ge.UID(int(state.get('champ_map', {}).get(str(turn_pid), 0)))))
    pkt = g.make_network_packet(pl_t)
    dw = _resolve('encode_datawrapper')(0, 3055, _resolve('compress_gzip')(_resolve('encode_sync_event')(pkt)), 1, _resolve('client_session_guid')(h))
    h.scnt += 1
    h.send({'issuer': f'0.0.0.0.ServiceGameSession.246.{session.session_id}.{h.scnt}', 'target': 'ServiceGameSession', 'instance': str(session.server_id), 'reqid': 0, 'c': 0, 'conh': 0, 'sid': h.sid}, dw)
    log_req(f'    PvP blocker options pushed to {defender_pid}: {len(rows)} defender troop(s) for {len(attackers)} attacker(s), {blockable_count} blockable')
    return blockable_count


def pvp_push_empty_blockers(session, state):
    """Push an empty BlockersAssigned for each attacker (the defender declines
    to block) to BOTH players — mirrors PvE ai_pass_declare_defense."""
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return
    turn_pid = state.get('turn_pid')
    opp_pid = pids[0] if pids[1] == turn_pid else pids[1]
    attackers = {int(k): int(v) for k, v in (state.get('attackers') or {}).items()}
    if not attackers:
        return
    my_uid = _ge.UID.make(244, turn_pid)
    opp_uid = _ge.UID.make(244, opp_pid)
    opp_champ = int((state.get('champ_map') or {}).get(str(opp_pid), 0))
    g = _ge.Game(int(session.session_id), my_uid, opp_uid)
    g.player_health = int(state.get(f'hp_{turn_pid}', 20))
    g.ai_health = int(state.get(f'hp_{opp_pid}', 20))
    for i, u in enumerate(attackers):
        cid = _ge.CombatId(my_uid, i + 1)
        g.push_blockers_assigned(cid, _ge.SessionCardId(_ge.UID(int(u))), _ge.SessionCardId(_ge.UID(opp_champ)) if opp_champ else _ge.SessionCardId(opp_uid), [])
    _resolve('_pvp_send_same_events')(session, g, my_uid, opp_uid)
    log_req(f'    PvP empty blockers assigned for {len(attackers)} attacker(s)')


def pvp_push_phase_options(session, state, pid=None):
    """Push a PlayerOptionList for a NON-main priority window: hand QuickActions
    (instant-speed — castable in ANY priority window) + champion charge powers,
    so the holding player can respond with quick actions mid-combat.  Mirrors
    PvE _push_phase_options_empty (hconnect ~2180)."""
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return
    turn_pid = pid or state.get('priority_pid') or state.get('turn_pid')
    h = _resolve('player_handlers').get(turn_pid)
    if not h:
        return
    opp_pid = pids[0] if pids[1] == turn_pid else pids[1]
    pl_t = _ge.UID.make(244, turn_pid)
    opp_t = _ge.UID.make(244, opp_pid)
    resources = int(state.get(f'res_{turn_pid}', 0))
    threshold = dict(state.get(f'thresh_{turn_pid}') or {})
    from rules_port.static_rules import effective_attributes
    playable = []
    for cu, cost, ct_name, thresh_json, _ab in _resolve('db_hand_cards_with_templates')(session.session_id, turn_pid):
        try:
            effective_attrs = int(effective_attributes(_resolve('_db'), session.session_id, state, int(cu)) or 0)
        except Exception:
            effective_attrs = 0
        if 'QuickAction' not in (ct_name or '') and (not effective_attrs & _ge.ECardAttributes.QuickAction):
            continue
        if (cost or 0) > resources:
            continue
        if not _pvp_thresholds_met(thresh_json, threshold):
            continue
        try:
            ability_guids = [x.lower() for x in json.loads(_ab or '[]')]
        except Exception:
            ability_guids = []
        trow = db_card_basic(session.session_id, cu, conn=_resolve('_db'))
        if not trow or not _resolve('_pvp_card_playable')(session, state, int(cu), trow[0], ct_name, cost or 0, ability_guids, resources, threshold):
            continue
        playable.append(_ge.SessionCardId(_ge.UID(int(cu))))
    g = _ge.Game(int(session.session_id), pl_t, opp_t)
    _resolve('_pvp_populate_game_state')(g, state, turn_pid, opp_pid)
    h._current_bstate = state
    g.player_health = int(state.get(f'hp_{turn_pid}', 20))
    g.ai_health = int(state.get(f'hp_{opp_pid}', 20))
    g.player_resources = resources
    g.player_total_resources = int(state.get(f'res_total_{turn_pid}', 0))
    g.ai_resources = int(state.get(f'res_{opp_pid}', 0))
    g.ai_total_resources = int(state.get(f'res_total_{opp_pid}', 0))
    g.player_charges = int(state.get(f'chg_{turn_pid}', 0))
    g.ai_charges = int(state.get(f'chg_{opp_pid}', 0))
    g.push_options(pl_t, playable)
    _resolve('_pvp_add_play_target_options')(g, session, state, pl_t, opp_t, turn_pid)
    priority_pid = int(turn_pid)
    affordable = _resolve('_pvp_affordable_troop_abilities')(session, state, pid=priority_pid)
    if affordable:
        _resolve('_pvp_add_troop_ability_options')(g, session, state, pl_t, opp_t, priority_pid, affordable)
    _resolve('_pvp_add_champion_options')(g, session, state, turn_pid, pl_t)
    _resolve('_pvp_add_hand_card_updates')(g, session, state, turn_pid, pl_t)
    g.push_player_updated(pl_t, champ_id=_ge.SessionCardId(_ge.UID(int(state.get('champ_map', {}).get(str(turn_pid), 0)))))
    g.push_player_updated(opp_t, champ_id=_ge.SessionCardId(_ge.UID(int(state.get('champ_map', {}).get(str(opp_pid), 0)))))
    pkt = g.make_network_packet(pl_t)
    dw = _resolve('encode_datawrapper')(0, 3055, _resolve('compress_gzip')(_resolve('encode_sync_event')(pkt)), 1, _resolve('client_session_guid')(h))
    h.scnt += 1
    h.send({'issuer': f'0.0.0.0.ServiceGameSession.246.{session.session_id}.{h.scnt}', 'target': 'ServiceGameSession', 'instance': str(session.server_id), 'reqid': 0, 'c': 0, 'conh': 0, 'sid': h.sid}, dw)
    log_req(f'    PvP phase options pushed to {turn_pid}: {len(playable)} quick action(s), {resources} resources')


def pvp_push_warzone_updates(session, state, game=None):
    """Re-push CardUpdateds for ALL warzone cards (both players) so the
    client's card icons/abilities/state always match the DB — mirrors PvE
    _push_warzone_card_updates.  When `game` is given the updates are appended
    to that Game's event stream (to be sent with it); otherwise a fresh stream
    is sent to both players."""
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return
    g = game
    if g is None:
        g = _ge.Game(int(session.session_id), _ge.UID.make(244, pids[0]), _ge.UID.make(244, pids[1]))
    wz_handler = _resolve('player_handlers').get(pids[0]) or _resolve('player_handlers').get(pids[1])
    if wz_handler is not None:
        wz_handler._current_bstate = state
    rows = _resolve('db_warzone_display_rows')(session.session_id, conn=_resolve('_db'))
    for card_uid, tpl_guid, user_id, cstate, db_ct in rows:
        scid = _ge.SessionCardId(_ge.UID(int(card_uid)))
        if wz_handler is not None and hasattr(wz_handler, '_card_full_data'):
            wz_handler._card_full_data(g, scid, tpl_guid)
        cdef = g.card_defs.get(scid)
        attrs = cdef.attributes if cdef else 0
        gems = cdef.gems if cdef else 0
        owner = _ge.UID.make(244, user_id)
        ct = _ge.card_type_from_db(db_ct)
        g.push_card_updated(scid, owner, _ge.ECardCollections.Warzone, ct, template_id=tpl_guid, attributes=attrs, state=int(cstate or 0), gems=gems)
    if game is None and g.events:
        _resolve('_pvp_send_same_events')(session, g, _ge.UID.make(244, pids[0]), _ge.UID.make(244, pids[1]))
        log_req(f'    PvP warzone re-pushed: {len(rows)} troop(s)')


def _pvp_offer_opponent_response(session, state, caster_pid):
    """After the turn player (caster_pid) plays a card in a main phase, offer
    the OPPONENT a priority window to respond with a quick action (e.g. Burn).
    Hands ResolveTopOfChain + quick-action options to the opponent and marks
    state so route_pvp_pass returns priority to the caster when the opponent
    passes (or after the opponent's response chain resolves)."""
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return
    opp_pid = pids[0] if pids[1] == caster_pid else pids[1]
    opp_h = _resolve('player_handlers').get(opp_pid)
    caster_uid = _ge.UID.make(244, caster_pid)
    opp_uid = _ge.UID.make(244, opp_pid)
    state['response_waiting_pid'] = opp_pid
    state['response_caster_pid'] = caster_pid
    state['priority_pid'] = opp_pid
    _resolve('pvp_save_state')(session, state)
    if opp_h:
        g = _ge.Game(int(session.session_id), opp_uid, caster_uid)
        g.push_green_light(opp_uid, _ge.EPriorityContext.ResolveTopOfChain)
        _resolve('_send_pvp_packet')(opp_h, session, g, opp_uid, 'response-window')
        _resolve('pvp_push_phase_options')(session, state, pid=opp_pid)
    log_req(f"    PvP response window: offering {opp_pid} priority to respond to {caster_pid}'s play")


def pvp_push_attack_options(session, state):
    """Push a PlayerOptionList marking the turn player's READY warzone troops
    as attackable (ECardUsage.Attack) during DeclareAttack, mirroring PvE
    _push_attack_options: a troop may attack if it has StartedATurnOnYourSide
    (survived to this turn, i.e. not summoning sick) OR Speed, is not tapped,
    not already attacking, and lacks CantAttack."""
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return
    turn_pid = state.get('turn_pid')
    h = _resolve('player_handlers').get(turn_pid)
    if not h:
        return
    opp_pid = pids[0] if pids[1] == turn_pid else pids[1]
    pl_t = _ge.UID.make(244, turn_pid)
    opp_t = _ge.UID.make(244, opp_pid)
    ready = []
    rows = _resolve('db_warzone_attack_option_rows')(session.session_id, turn_pid, conn=_resolve('_db'))
    from rules_port.static_rules import effective_attributes
    for uid, cstate, _card_type, attrs in rows:
        cstate = cstate or 0
        attrs = int(attrs or 0) | int(effective_attributes(_resolve('_db'), session.session_id, state, int(uid)) or 0)
        if (cstate & _ge.ECardStates.StartedATurnOnYourSide or attrs & _ge.ECardAttributes.Speed) and (not cstate & _ge.ECardStates.Tapped) and (not cstate & _ge.ECardStates.Attacking) and (not attrs & (_ge.ECardAttributes.CantAttack | _ge.ECardAttributes.Defensive)):
            ready.append(_ge.SessionCardId(_ge.UID(int(uid))))
    champ_map = state.get('champ_map') or {}
    opp_champ = int(champ_map.get(str(opp_pid), 0))
    attackers = {int(k): int(v) for k, v in (state.get('attackers') or {}).items()}
    forced = []
    for uid, cstate, _card_type, attrs in rows:
        cstate = cstate or 0
        attrs = int(attrs or 0) | int(effective_attributes(_resolve('_db'), session.session_id, state, int(uid)) or 0)
        if not attrs & _ge.ECardAttributes.ForceAttack:
            continue
        if cstate & (_ge.ECardStates.Attacking | _ge.ECardStates.Tapped):
            continue
        if not cstate & _ge.ECardStates.StartedATurnOnYourSide and (not attrs & _ge.ECardAttributes.Speed):
            continue
        if attrs & (_ge.ECardAttributes.CantAttack | _ge.ECardAttributes.Defensive):
            continue
        u = int(uid)
        if u in attackers:
            continue
        attackers[u] = opp_champ
        state_bits = _ge.ECardStates.Attacking | _ge.ECardStates.HasAttacked
        if not attrs & _ge.ECardAttributes.Steadfast:
            state_bits |= _ge.ECardStates.Tapped
        _resolve('db_card_set_attacking_state')(session.session_id, u, state_bits)
        forced.append((u, state_bits))
    _resolve('_db').commit()
    if forced:
        state['attackers'] = {str(k): str(v) for k, v in attackers.items()}
        _resolve('pvp_save_state')(session, state)
    native_port = _resolve('pvp_shared_port')(session)
    if native_port is not None:
        native_port.has_forced_attackers = bool(attackers)
        for uid, _state_bits in forced:
            attacker_card = native_port.get_card(int(uid))
            if attacker_card is None:
                continue
            if native_port.combat_manager.combat_for_attacker(attacker_card) is None:
                native_port.declare_attack(pl_t, opp_champ, attacker_card)
    g = _ge.Game(int(session.session_id), pl_t, opp_t)
    _pvp_apply_visibility(g, state)
    _resolve('_pvp_populate_game_state')(g, state, turn_pid, opp_pid)
    g.player_health = int(state.get(f'hp_{turn_pid}', 20))
    g.ai_health = int(state.get(f'hp_{opp_pid}', 20))
    from pickers import PickOption, present_options
    present_options(g, pl_t, [PickOption(card_uid=int(scid.uid.uid64), state='Attack') for scid in ready])
    combats = []
    for i, (u, state_bits) in enumerate(forced):
        scid = _ge.SessionCardId(_ge.UID(u))
        cid = _ge.CombatId(pl_t, i + 1)
        g.push_attack_declared(cid, pl_t, _ge.SessionCardId(_ge.UID(opp_champ)) if opp_champ else _ge.SessionCardId(opp_t), scid)
        trow = db_card_basic(session.session_id, u, conn=_resolve('_db'))
        tpl_guid = trow[0] if trow else None
        h_card = h
        if tpl_guid:
            h_card._card_full_data(g, scid, tpl_guid)
        pushed_state = _resolve('db_card_state_value')(session.session_id, u) or state_bits
        g.push_card_updated(scid, pl_t, _ge.ECardCollections.Warzone, _ge.ECardTypes.Troop, template_id=tpl_guid, state=pushed_state)
        cs = _ge.CombatSessionEventArgs()
        cs.player_id = pl_t
        cs.id = cid
        cs.attacker = scid
        cs.blockers = []
        combats.append(cs)
        view = _resolve('_pvp_fra_view')(state, turn_pid, opp_pid)
        _resolve('_pvp_dispatch_triggers')(h_card, g, session, view, pl_t, opp_t, 'CardAttackedEvent', int(u), turn_pid)
        _resolve('_pvp_dispatch_triggers')(h_card, g, session, view, pl_t, opp_t, 'CardAttackedOrBlockedEvent', int(u), turn_pid)
        from rules_port.context import EffectContext
        from rules_port.combat_effects import apply_rage
        apply_rage(EffectContext.from_rules_port(g, session, _resolve('_db'), h_card, pl_t, opp_t, view, '', ability=None), int(u))
        if view.get('player_health') is not None:
            state[f'hp_{turn_pid}'] = int(view['player_health'])
        if view.get('ai_health') is not None:
            state[f'hp_{opp_pid}'] = int(view['ai_health'])
        _resolve('_pvp_sync_view_to_state')(state, view, turn_pid, opp_pid)
        _resolve('pvp_save_state')(session, state)
    if combats:
        g.push_combat_listing(pl_t, combats)
    g.push_player_updated(pl_t, champ_id=_ge.SessionCardId(_ge.UID(int(state.get('champ_map', {}).get(str(turn_pid), 0)))))
    g.push_player_updated(opp_t, champ_id=_ge.SessionCardId(_ge.UID(int(state.get('champ_map', {}).get(str(opp_pid), 0)))))
    _resolve('pvp_push_warzone_updates')(session, state, game=g)
    if forced:
        _resolve('_pvp_send_same_events')(session, g, pl_t, opp_t)
    else:
        pkt = g.make_network_packet(pl_t)
        dw = _resolve('encode_datawrapper')(0, 3055, _resolve('compress_gzip')(_resolve('encode_sync_event')(pkt)), 1, _resolve('client_session_guid')(h))
        h.scnt += 1
        h.send({'issuer': f'0.0.0.0.ServiceGameSession.246.{session.session_id}.{h.scnt}', 'target': 'ServiceGameSession', 'instance': str(session.server_id), 'reqid': 0, 'c': 0, 'conh': 0, 'sid': h.sid}, dw)
    log_req(f'    PvP attack options pushed to {turn_pid}: {len(ready)} ready troop(s), {len(forced)} forced attacker(s)')


def pvp_push_main_phase_options(session, state):
    """Push the PlayerOptionList (golden playable-card outlines) for the turn
    player at a main phase, so the client lets them click cards.  Affordability
    is computed from the PvP state's resource count (res_<pid>) and each card's
    template cost, plus THRESHOLD requirements (thresh_<pid>) — a card like
    Emberspire Witch (2 Ruby) is not highlighted until the player has the
    thresholds."""
    if _pvp_chain_active(session, state):
        if any(((state or {}).get(key) for key in ('pending_choice', 'pending_trigger', 'pending_deck_search', 'pending_conversation', 'pending_discard_ability', 'resolution_paused'))):
            return
        priority_pid = (state or {}).get('priority_pid')
        if priority_pid:
            _resolve('pvp_push_phase_options')(session, state, pid=priority_pid)
        return
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return
    turn_pid = state.get('turn_pid')
    h = _resolve('player_handlers').get(turn_pid)
    if not h:
        return
    opp_pid = pids[0] if pids[1] == turn_pid else pids[1]
    pl_t = _ge.UID.make(244, turn_pid)
    opp_t = _ge.UID.make(244, opp_pid)
    resources = int(state.get(f'res_{turn_pid}', 0))
    threshold = dict(state.get(f'thresh_{turn_pid}') or {})
    from rules_port.resources import can_play_resource, resource_play_limit
    resource_limit = resource_play_limit(_resolve('_db'), session.session_id, state, int(turn_pid))
    resource_played = not can_play_resource(state, 'player', limit=resource_limit, player_id=int(turn_pid))
    from pvp_db import db_game_get_hand, db_game_card_type, db_card_template_thresholds, db_deck_top_card_details, db_card_play_info, db_template_ability_payload
    playable = []
    for cu, tg in db_game_get_hand(session.session_id, turn_pid):
        scid = _ge.SessionCardId(_ge.UID(int(cu)))
        ct_name = db_game_card_type(tg)
        if str(ct_name or '').split('|', 1)[0] == 'Resource':
            if not resource_played:
                playable.append(scid)
            continue
        t = _resolve('db_template_by_guid')(tg)
        cost = t[3] if t else 0
        try:
            from rules_port.static_rules import effective_cost as _ec
            cost = _ec(_resolve('_db'), session.session_id, _resolve('_pvp_fra_view')(state, turn_pid, opp_pid), int(cu))
        except Exception:
            pass
        if cost > resources:
            continue
        srow = db_card_template_thresholds(tg)
        thresh_json = srow[0] if srow else None
        if not _pvp_thresholds_met(thresh_json, threshold):
            log_req(f'    PvP options: {ct_name} {cu} not playable (thresholds unmet: {thresh_json})')
            continue
        import json as _js
        ab_json = None
        ab_json = db_template_ability_payload(tg, conn=_resolve('_db'))
        ability_guids = []
        if ab_json:
            try:
                ability_guids = [x.lower() for x in _js.loads(ab_json)]
            except Exception:
                ability_guids = []
        if not _resolve('_pvp_card_playable')(session, state, int(cu), tg, ct_name, cost, ability_guids, resources, threshold):
            continue
        playable.append(scid)
    from rules_port.static_rules import player_int_attributes
    player_attrs = player_int_attributes(_resolve('_db'), session.session_id, state, int(turn_pid))
    if int(player_attrs.get('CanPlayTopOfDeck', 0) or 0) > 0:
        top = db_deck_top_card_details(session.session_id, int(turn_pid), conn=_resolve('_db'))
        if top:
            top_uid = int(top[1])
            info = db_card_play_info(session.session_id, top_uid, conn=_resolve('_db'))
            if info:
                _top_guid, top_type, _top_name = info[:3]
                if str(top_type or '').split('|', 1)[0] == 'Resource':
                    if not resource_played:
                        playable.append(_ge.SessionCardId(_ge.UID(top_uid)))
                else:
                    top_template = _resolve('db_template_by_guid')(_top_guid)
                    top_cost = top_template[3] if top_template else 0
                    try:
                        from rules_port.static_rules import effective_cost as _ec
                        top_cost = _ec(_resolve('_db'), session.session_id, _resolve('_pvp_fra_view')(state, turn_pid, opp_pid), top_uid)
                    except Exception:
                        pass
                    top_threshold = db_card_template_thresholds(_top_guid, conn=_resolve('_db'))
                    top_ability_json = db_template_ability_payload(_top_guid, conn=_resolve('_db'))
                    try:
                        top_abilities = [str(value).lower() for value in json.loads(top_ability_json or '[]')]
                    except (TypeError, ValueError, json.JSONDecodeError):
                        top_abilities = []
                    top_type_name = str(top_type or '')
                    if top_cost <= resources and _pvp_thresholds_met(top_threshold[0] if top_threshold else None, threshold) and _resolve('_pvp_card_playable')(session, state, top_uid, _top_guid, top_type_name, top_cost, top_abilities, resources, threshold):
                        playable.append(_ge.SessionCardId(_ge.UID(top_uid)))
    g = _ge.Game(int(session.session_id), pl_t, opp_t)
    _resolve('_pvp_populate_game_state')(g, state, turn_pid, opp_pid)
    from rules_port.visibility import project_visible_top_decks
    project_visible_top_decks(_resolve('_db'), session, h, g, pl_t, opp_t, state)
    champ_map = state.get('champ_map') or {}
    g.player_champion_card_id = _ge.SessionCardId(_ge.UID(int(champ_map.get(str(turn_pid), 0)))) if champ_map.get(str(turn_pid)) else None
    g.ai_champion_card_id = _ge.SessionCardId(_ge.UID(int(champ_map.get(str(opp_pid), 0)))) if champ_map.get(str(opp_pid)) else None
    h._current_bstate = state
    _resolve('_pvp_add_hand_card_updates')(g, session, state, turn_pid, pl_t)
    _resolve('pvp_push_warzone_updates')(session, state, game=g)
    g.push_options(pl_t, playable)
    _resolve('_pvp_add_play_target_options')(g, session, state, pl_t, opp_t, turn_pid)
    affordable = _resolve('_pvp_affordable_troop_abilities')(session, state, pid=turn_pid)
    if affordable:
        _resolve('_pvp_add_troop_ability_options')(g, session, state, pl_t, opp_t, turn_pid, affordable)
    _resolve('_pvp_add_champion_options')(g, session, state, turn_pid, pl_t)
    for event in g.events:
        if not isinstance(event, _ge.PlayerOptionListSessionEventArgs):
            continue
        option_parts = []
        for option in event.options:
            if not isinstance(option, _ge.PlayerOptionSessionEventArgs):
                continue
            card_uid = getattr(getattr(option.card, 'uid', None), 'uid64', option.card)
            instance_parts = []
            for instance in option.instances:
                if not isinstance(instance, _ge.OptionInstanceSessionEventArgs):
                    continue
                targets = getattr(instance, 'target_instances', ()) or ()
                target_count = len(targets)
                cost_count = sum((1 for target in targets if isinstance(target, _ge.CostInstanceSessionEventArgs)))
                instance_parts.append(f'{str(instance.opt_id.guid)[:8]}/targets={target_count}/costs={cost_count}')
            option_parts.append(f"card={card_uid}/state={int(option.state)}/instances=[{','.join(instance_parts)}]")
        log_req(f"    PvP final-options pid={event.player_id} options=[{' ; '.join(option_parts)}]")
    pkt = g.make_network_packet(pl_t)
    dw = _resolve('encode_datawrapper')(0, 3055, _resolve('compress_gzip')(_resolve('encode_sync_event')(pkt)), 1, _resolve('client_session_guid')(h))
    h.scnt += 1
    h.send({'issuer': f'0.0.0.0.ServiceGameSession.246.{session.session_id}.{h.scnt}', 'target': 'ServiceGameSession', 'instance': str(session.server_id), 'reqid': 0, 'c': 0, 'conh': 0, 'sid': h.sid}, dw)
    try:
        offered_uids = {int(getattr(card.uid, 'uid64', card.uid)) for card in playable}
        offered_names = []
        for _cu, _tg in db_game_get_hand(session.session_id, turn_pid):
            if int(_cu) not in offered_uids:
                continue
            offered_names.append(_resolve('db_template_name')(_tg, conn=_resolve('_db')) or _tg)
    except Exception:
        offered_names = []
    log_req(f'    PvP main-phase options pushed to {turn_pid} ({len(playable)} playable, {resources} resources; offered={offered_names})')


def _pvp_resolve_conversation(handler, session, inner_bytes, my_pid):
    """Resume a metadata BOM after a class-55 encounter conversation."""
    from application.player_transactions import extract_resource_guid
    from rules_port import lifecycle as _be
    state = _resolve('pvp_load_state')(session) or {}
    pending = state.get('pending_conversation')
    if not pending:
        handler._push_transaction_ack(session)
        return True
    conversation_id = extract_resource_guid(inner_bytes, 'ConversationId')
    expected = str(pending.get('conversation_id', '')).lower()
    if conversation_id and conversation_id != expected:
        log_req(f'    PvP conversation answer rejected: got {conversation_id}, expected {expected}')
        handler._push_transaction_ack(session)
        return True
    owner_id = int(pending.get('owner_id', 0) or 0)
    if int(my_pid) != owner_id:
        log_req(f'    PvP conversation answer rejected: pid {my_pid} is not owner {owner_id}')
        handler._push_transaction_ack(session)
        return True
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        handler._push_transaction_ack(session)
        return True
    opp_pid = pids[0] if pids[1] == owner_id else pids[1]
    state.pop('pending_conversation', None)
    state.pop('resolution_paused', None)
    pl_t = _ge.UID.make(244, owner_id)
    ai_t = _ge.UID.make(244, opp_pid)
    view = _resolve('_pvp_fra_view')(state, owner_id, opp_pid)
    view.pop('pending_conversation', None)
    view.pop('resolution_paused', None)
    game = _ge.Game(int(session.session_id), pl_t, ai_t)
    _resolve('_pvp_populate_game_state')(game, state, owner_id, opp_pid)
    ability_owner_id = int(pending.get('ability_owner_id', owner_id) or owner_id)
    _resolve('_pvp_resolve_ability')(handler, game, session, view, pl_t, ai_t, pending.get('ability_guid', ''), pending.get('source_uid'), ability_owner_id, target_map={int(k): v for k, v in (pending.get('target_map') or {}).items()}, variables=pending.get('variables') or {}, resume_from_order=int(pending.get('resume_effect_order', 0)))
    state['stack'] = view.get('stack') or []
    state['stack_player_passed'] = False
    state['stack_ai_passed'] = False
    state['stack_passed'] = []
    _resolve('_pvp_sync_view_to_state')(state, view, owner_id, opp_pid)
    persisted = _resolve('pvp_load_state')(session) or {}
    for key in ('pending_trigger', 'pending_deck_search', 'pending_choice', 'pending_conversation'):
        if persisted.get(key):
            state[key] = persisted[key]
    _resolve('pvp_save_state')(session, state)
    _resolve('_pvp_send_same_events')(session, game, pl_t, ai_t)
    pending_input = state.get('pending_conversation') or state.get('pending_choice') or state.get('pending_trigger') or state.get('pending_deck_search')
    if pending_input:
        log_req('    PvP conversation resumed into another pending input')
        return True
    if _be.stack_empty(state):
        turn_pid = int(state.get('turn_pid') or owner_id)
        state['priority_pid'] = turn_pid
        _resolve('pvp_save_state')(session, state)
        turn_h = _resolve('player_handlers').get(turn_pid)
        if turn_h:
            turn_uid = _ge.UID.make(244, turn_pid)
            other_uid = _ge.UID.make(244, pids[1] if turn_pid == pids[0] else pids[0])
            resume = _ge.Game(int(session.session_id), turn_uid, other_uid)
            resume.push_chain_empty()
            resume.push_green_light(turn_uid, _ge.EPriorityContext.Normal)
            _resolve('_send_pvp_packet')(turn_h, session, resume, turn_uid, 'conversation-chain-empty')
        phase = int(state.get('phase', 0))
        if phase in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
            _resolve('pvp_push_main_phase_options')(session, state)
    else:
        resume_pid = int(state.get('conversation_resume_priority_pid', owner_id) or owner_id)
        next_pid = pids[1] if resume_pid == pids[0] else pids[0]
        state['priority_pid'] = next_pid
        _resolve('pvp_save_state')(session, state)
        next_h = _resolve('player_handlers').get(next_pid)
        if next_h:
            next_uid = _ge.UID.make(244, next_pid)
            other_uid = _ge.UID.make(244, pids[1] if next_pid == pids[0] else pids[0])
            resume = _ge.Game(int(session.session_id), next_uid, other_uid)
            resume.push_green_light(next_uid, _ge.EPriorityContext.ResolveTopOfChain)
            _resolve('_send_pvp_packet')(next_h, session, resume, next_uid, 'conversation-chain-next')
    handler._push_transaction_ack(session)
    log_req(f'    PvP conversation resolved: {expected[:8]}')
    return True


def _pvp_resolve_discard_prompt(handler, session, inner_bytes, my_pid, typed_payload=None):
    """Resolve the card selected for a pending class-23 discard prompt."""
    state = _resolve('pvp_load_state')(session) or {}
    native = state.get('pending_discard_continuation')
    if isinstance(native, dict):
        from gamedata import DEFAULT_RECORD_STORE
        from rules_port.resolution import resolve_port_ability, resume_ability_continuation_parents
        from rules_port.wire import extract_session_card_uids
        from pvp_db import db_rules_port_hand_card
        child_guid = str(native.get('ability_guid') or '').lower()
        owner_id = int(native.get('owner_id', -1) or -1)
        if not child_guid or owner_id != int(my_pid) or int(state.get('pending_discard_pid', my_pid) or my_pid) != int(my_pid):
            return False
        graph = _resolve("ability_graph")(DEFAULT_RECORD_STORE, child_guid)
        if graph is None:
            log_req(f'    PvP RulesPort discard ability missing: {child_guid}')
            return True
        target_guid = str(state.get('pending_discard_target_template') or '').lower()
        target_index = next((index for index, target in enumerate(graph.targets) if str(target.guid).lower() == target_guid), None)
        if target_index is None:
            log_req('    PvP RulesPort discard target missing: ' + target_guid)
            return True
        activation = (typed_payload or {}).get('activation_data', {})
        selected_map = activation.get('target_map', {}) if isinstance(activation, dict) else {}
        selected = selected_map.get(target_index, selected_map.get(str(target_index), ())) if isinstance(selected_map, dict) else ()
        if isinstance(selected, dict):
            selected = selected.get('session_card_ids') or selected.get('SessionCardIds') or selected
        if not isinstance(selected, (list, tuple, set)):
            selected = (selected,) if selected is not None else ()

        def card_uids(values):
            result = []
            for value in values:
                while isinstance(value, dict):
                    nested = next((value[key] for key in ('uid64', 'UID', 'm_UID64', 'value') if key in value), None)
                    if nested is None or nested is value:
                        break
                    value = nested
                value = getattr(value, 'uid64', value)
                try:
                    uid = _uid_int(value)
                except (TypeError, ValueError):
                    continue
                if uid > 0 and uid & 255 == 1:
                    result.append(uid)
            return result
        submitted = card_uids(selected)
        if not submitted:
            submitted = list(extract_session_card_uids(inner_bytes if isinstance(inner_bytes, bytes) else b'', exclude=(native.get('source_uid', 0),)))
        valid = []
        for uid in dict.fromkeys(submitted):
            row = db_rules_port_hand_card(session.session_id, uid, owner_id)
            if row:
                valid.append(uid)
        if len(submitted) != 1 or len(valid) != 1:
            log_req(f'    PvP RulesPort discard continuation rejected: target_index={target_index} selected={submitted} valid_hand_cards={valid}')
            return True
        pids = [int(pid) for pid in _resolve('db_game_session_pids')(session.session_id)]
        if len(pids) < 2 or owner_id not in pids:
            return False
        opponent_id = next((pid for pid in pids if pid != owner_id))
        player_uid = _ge.UID.make(244, owner_id)
        opponent_uid = _ge.UID.make(244, opponent_id)
        target_map = {int(key): value for key, value in (native.get('target_map') or {}).items()}
        target_map[target_index] = (valid[0],)
        for key in ('pending_discard_continuation', 'pending_discard_continuation_instance_id', 'pending_discard_ability', 'pending_discard_ability_instance_id', 'pending_discard_target_template', 'pending_discard_source_uid', 'pending_discard_pid', 'pending_discard_scid'):
            state.pop(key, None)
        state.pop('resolution_paused', None)
        state['rules_port_resume_effect_order'] = int(native.get('resume_effect_order', 0) or 0)
        _resolve('pvp_save_state')(session, state)
        game = _ge.Game(int(session.session_id), player_uid, opponent_uid)
        _resolve('_pvp_populate_game_state')(game, state, owner_id, opponent_id)
        result = resolve_port_ability(handler, game, session, _resolve('_db'), player_uid, opponent_uid, state, child_guid, native.get('source_uid'), owner_id, target_map=target_map, variables=native.get('variables') or {}, resume_from_order=int(native.get('resume_effect_order', 0) or 0), instance_id=int(native.get('instance_id', native.get('ability_instance_id', 1)) or 1))
        resume_ability_continuation_parents(handler, game, session, _resolve('_db'), player_uid, opponent_uid, state, native)
        state = _resolve('pvp_load_state')(session) or state
        completed = 0
        if not state.get('resolution_paused'):
            completed = int(state.pop('paused_chain_instance_id', 0) or 0)
            if completed:
                state['completed_chain_instance_id'] = completed
        _resolve('pvp_save_state')(session, state)
        _resolve('_pvp_send_same_events')(session, game, player_uid, opponent_uid)
        port = getattr(session, '_rules_port_session', None)
        if completed and port is not None:
            from rules_port.actions import AbilityResolutionState
            if port.resolve_top_of_chain(completed) is not AbilityResolutionState.COMPLETED:
                log_req(f'    PvP RulesPort discard chain item remains pending: instance={completed}')
        elif not state.get('resolution_paused'):
            phase = int(state.get('phase', _ge.ETurnPhases.FirstMainPhase))
            priority_pid = int(state.get('turn_pid') or owner_id)
            state['priority_pid'] = priority_pid
            _resolve('pvp_save_state')(session, state)
            turn_uid = _ge.UID.make(244, int(state.get('turn_pid') or owner_id))
            priority_uid = _ge.UID.make(244, priority_pid)
            for pid in pids:
                recipient = _ge.UID.make(244, int(pid))
                other = _ge.UID.make(244, next((value for value in pids if int(value) != int(pid))))
                gp = _ge.Game(int(session.session_id), recipient, other)
                gp.push_green_light(priority_uid, _ge.EPriorityContext.Normal)
                _pvp_push_turn_phase_with_elapsed(gp, phase, turn_uid, priority_uid, _resolve('priority_elapsed_ticks')(state, priority_pid) // 10000000)
                _resolve('_send_pvp_packet')(_resolve('player_handlers').get(int(pid)) or handler, session, gp, recipient, 'discard-priority')
            if phase in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
                _resolve('pvp_push_main_phase_options')(session, state)
        log_req(f'    PvP RulesPort discard continuation: {valid[0]} -> {result}')
        return True
    child = state.get('pending_discard_ability')
    if not child or int(state.get('pending_discard_pid', -1)) != int(my_pid):
        return False
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return True
    selected_card_uids = _resolve('_pvp_transaction_card_uids')(inner_bytes)
    card_uid = selected_card_uids[-1] if selected_card_uids else None
    row = None
    if card_uid is None:
        return True
    row = _resolve('db_hand_card_for_discard')(session.session_id, card_uid, my_pid, conn=_resolve('_db'))
    if not row:
        log_req(f'    PvP discard prompt rejected: uid={card_uid} pid={my_pid}')
        return True
    owner_pid = int(row[1])
    if owner_pid not in pids:
        owner_pid = int(my_pid)
    _resolve('db_discard_card')(session.session_id, int(card_uid), owner_user_id=owner_pid, connection=_resolve('_db'))
    opp_pid = pids[0] if pids[1] == int(my_pid) else pids[1]
    my_uid = _ge.UID.make(244, int(my_pid))
    opp_uid = _ge.UID.make(244, int(opp_pid))
    g = _ge.Game(int(session.session_id), my_uid, opp_uid)
    _resolve('_pvp_populate_game_state')(g, state, int(my_pid), int(opp_pid))
    scid = _ge.SessionCardId(_ge.UID(int(card_uid)))
    card_handler = _resolve('player_handlers').get(int(my_pid)) or handler
    tpl_guid = row[2]
    _tpl, card_type, _name, cost, attack, defense, gems = card_handler._card_full_data(g, scid, tpl_guid, row[3])
    owner_uid = _ge.UID.make(244, owner_pid)
    g.push_card_updated(scid, owner_uid, _ge.ECardCollections.Discard, card_type, template_id=tpl_guid, cost=cost, attack=attack, defense=defense, gems=gems)
    for pid in pids:
        uid = _ge.UID.make(244, int(pid))
        cuid = int((state.get('champ_map') or {}).get(str(pid), 0))
        g.push_player_updated(uid, champ_id=_ge.SessionCardId(_ge.UID(cuid)) if cuid else None)
    state.pop('pending_discard_ability', None)
    state.pop('pending_discard_target_template', None)
    state.pop('pending_discard_source_uid', None)
    state.pop('pending_discard_pid', None)
    priority_pid = int(state.get('turn_pid') or my_pid)
    priority_uid = _ge.UID.make(244, priority_pid)
    state['priority_pid'] = priority_pid
    _resolve('pvp_save_state')(session, state)
    _resolve('_pvp_send_same_events')(session, g, my_uid, opp_uid)
    phase = int(state.get('phase', _ge.ETurnPhases.FirstMainPhase))
    turn_uid = _ge.UID.make(244, int(state.get('turn_pid', my_pid)))
    for pid in pids:
        recipient = _ge.UID.make(244, int(pid))
        other = _ge.UID.make(244, int(opp_pid if int(pid) == int(my_pid) else my_pid))
        h = _resolve('player_handlers').get(int(pid))
        if not h:
            continue
        gp = _ge.Game(int(session.session_id), recipient, other)
        gp.push_green_light(priority_uid, _ge.EPriorityContext.Normal)
        _pvp_push_turn_phase_with_elapsed(gp, phase, turn_uid, priority_uid, _resolve('priority_elapsed_ticks')(state, priority_pid) // 10000000)
        _resolve('_send_pvp_packet')(h, session, gp, recipient, 'discard-priority')
    if phase in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
        _resolve('pvp_push_main_phase_options')(session, state)
    log_req(f'    PvP discard prompt resolved: {card_uid} -> discard')
    return True


def _wire_pvp_state_based_handler(port, handler, session, game):
    """Run PvP state-based actions before each native priority handoff."""

    def state_based():
        live = _resolve('pvp_load_state')(session) or getattr(port, '_pvp_state', None)
        if not isinstance(live, dict) or not live.get('pvp'):
            return False
        pids = _resolve('db_game_session_pids')(session.session_id)
        if len(pids) < 2:
            pids = [int(pid) for pid in live.get('pids') or ()]
        if len(pids) < 2:
            return False
        try:
            owner_id = _uid_int(getattr(port, 'active_player_id', None))
        except (TypeError, ValueError):
            owner_id = 0
        if owner_id not in {int(pid) for pid in pids}:
            owner_id = int(live.get('turn_pid') or pids[0])
        opponent_id = next((int(pid) for pid in pids if int(pid) != int(owner_id)))
        current_handler = getattr(port, '_pvp_current_handler', None) or handler
        projection = getattr(getattr(port, 'event_sink', None), 'game', None) or game
        if current_handler is None or projection is None:
            return False
        host = _resolve('_PvpChainHost')(current_handler, session, live, owner_id, opponent_id)
        return host.chain_state_based(session, live, projection, _ge.UID.make(244, owner_id), _ge.UID.make(244, opponent_id))
    port.set_state_based_handler(state_based)


@_pvp_locked
def pvp_debug_draw(handler, session, count):
    """Draw debug cards in PvP and send private/objective variants to both clients."""
    if not session or not (session.session_name or '').startswith('tourney-'):
        return 0
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return 0
    owner_pid = int(handler.client_reck_id)
    if owner_pid not in pids:
        return 0
    opp_pid = pids[0] if pids[1] == owner_pid else pids[1]
    owner_uid = _ge.UID.make(244, owner_pid)
    opponent_uid = _ge.UID.make(244, opp_pid)
    drawn_count = 0
    for _ in range(max(0, int(count))):
        top = _resolve('db_deck_top_card')(session.session_id, owner_pid, conn=_resolve('_db'))
        if not top:
            break
        card_uid, tpl_guid, instance_id = top
        g = _ge.Game(int(session.session_id), owner_uid, opponent_uid)
        handler._player_draw_card(g, session, owner_uid, owner_pid)
        loc = _resolve('db_card_location')(session.session_id, card_uid)
        if loc == 'hand':
            drawn_count += 1
        if not g.events:
            continue
        g2 = _ge.Game(int(session.session_id), owner_uid, opponent_uid)
        g2.events = []
        g2.card_defs = dict(g.card_defs)
        scid = _ge.SessionCardId(_ge.UID(int(card_uid)))
        _tpl2, ct2, _name2, _cost2, _atk2, _def2, _gems2 = handler._card_full_data(g2, scid, tpl_guid, instance_id)
        g2.push_card_updated(scid, owner_uid, _ge.ECardCollections.Hand, ct2, template_id=_tpl2, nulling=True)
        for event in g.events:
            if getattr(event, 'session_card_id', None) == scid and event.__class__.__name__ in ('CardMovedSessionEventArgs', 'CardDrawnSessionEventArgs', 'CardUpdatedSessionEventArgs'):
                continue
            g2._push(event)
        _resolve('_send_pvp_packet')(handler, session, g, owner_uid, 'debug-draw')
        other_h = _resolve('player_handlers').get(opp_pid)
        if other_h:
            _resolve('_send_pvp_packet')(other_h, session, g2, opponent_uid, 'debug-draw-opponent')
    state = _resolve('pvp_load_state')(session) or {}
    if state.get('stack'):
        state['priority_pid'] = owner_pid
        state['stack_passed'] = []
        _resolve('pvp_save_state')(session, state)
        for pid in pids:
            h = _resolve('player_handlers').get(pid)
            if not h:
                continue
            recipient = _ge.UID.make(244, pid)
            other = _ge.UID.make(244, pids[0] if pids[1] == pid else pids[1])
            gp = _ge.Game(int(session.session_id), recipient, other)
            gp.push_green_light(owner_uid, _ge.EPriorityContext.ResolveTopOfChain)
            _resolve('_send_pvp_packet')(h, session, gp, recipient, 'debug-draw-priority')
        _resolve('pvp_push_phase_options')(session, state, pid=owner_pid)
    elif int(state.get('phase', -1)) in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
        _resolve('pvp_push_main_phase_options')(session, state)
    return drawn_count


@_pvp_locked
def handle_ready_to_continue_game(handler, target, instance, reqid, comp, session_id, conh, inner_obj, inner_bytes, log_req=log_req, **_kw):
    """Handle DT 22025 — ReadyToContinueGame after join."""
    log_req('>>> ReadyToContinueGame (dt=22025)')
    player_uid_val = _objfmt_uid_field(inner_obj, inner_bytes, 'PlayerId')
    player_pid = _pvp_raw_player_id(player_uid_val)
    import game_session as gs
    session = gs.find_session_by_player(player_pid)
    if _resolve('fra_session_reconnectable')(session, player_pid):
        try:
            with _resolve('pvp_session_lock')(session):
                restored = handler._push_fra_reconnect_snapshot(session, player_pid)
            if not restored:
                log_req(f'    FRA reconnect snapshot could not restore pid {player_pid}')
        except Exception as exc:
            import traceback
            log_req(f'    FRA reconnect snapshot failed for {player_pid}: {exc}\n{traceback.format_exc()}')
    elif _resolve('campaign_session_reconnectable')(session, player_pid):
        try:
            with _resolve('pvp_session_lock')(session):
                restored = handler._push_fra_reconnect_snapshot(session, player_pid)
            if not restored:
                log_req(f'    Campaign reconnect snapshot could not restore pid {player_pid}')
        except Exception as exc:
            import traceback
            log_req(f'    Campaign reconnect snapshot failed for {player_pid}: {exc}\n{traceback.format_exc()}')
    elif session and getattr(session, 'state', '') != 'ended' and str(getattr(session, 'session_name', '') or '').startswith('tourney-') and _session_has_reconnect_player(session, player_pid):
        try:
            _resolve('pvp_start_priority_watchdog')(session)
            _resolve('_pvp_push_reconnect_snapshot')(handler, session, player_pid)
        except Exception as exc:
            log_req(f'    PvP reconnect snapshot failed for {player_pid}: {exc}')
    else:
        log_req(f'    No active reconnectable session for pid {player_pid}')
    log_req(f'    Processed ReadyToContinueGame for raw pid {player_pid}')


def notify_pvp_player_disconnected(player_uid, disconnected_handler=None):
    """Reconcile a PvP game after one socket goes offline."""
    import game_session as gs
    try:
        player_uid = int(player_uid)
    except (TypeError, ValueError):
        return False
    if disconnected_handler is not None:
        with _resolve('player_handler_lock'):
            if _resolve('player_handlers').get(player_uid) is not disconnected_handler:
                log_req(f'    Ignoring stale PvP disconnect for {player_uid}')
                return False
    session = gs.find_session_by_player(player_uid)
    if not session or getattr(session, 'state', '') == 'ended' or (not str(getattr(session, 'session_name', '') or '').startswith('tourney-')):
        return False
    pvp_state = _resolve('pvp_load_state')(session)
    if not pvp_state or not pvp_state.get('pvp'):
        return False
    pids = _resolve('db_game_session_pids')(session.session_id)
    opponent = next((p for p in pids if int(p) != player_uid), None)
    opponent_handler = _resolve('player_handlers').get(opponent) if opponent is not None else None
    if not opponent_handler or opponent_handler is disconnected_handler:
        return False
    try:
        _resolve('_pvp_reassign_priority_after_disconnect')(session, player_uid, opponent)
        log_req(f'    PvP disconnect reconciled: {player_uid} -> {opponent}')
        return True
    except Exception as exc:
        log_req(f'    PvP disconnect handling failed: {exc}')
        return False


def _pvp_advance_past_declare_defense(session, state):
    """Advance the PvP phase from DeclareDefense (14) to
    DeclareDefensePriorityWindow (15), pushing the phase to both players and
    handing priority to the turn (attacker) player.  Returns True on success."""
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return False
    turn_pid = state.get('turn_pid')
    try:
        new_phase = _ge.ETurnPhases.DeclareDefensePriorityWindow
    except Exception:
        new_phase = 15
    _resolve('port_enter_phase')(state, new_phase)
    state['priority_pid'] = turn_pid
    _resolve('pvp_save_state')(session, state)
    log_req(f'    PvP post-blockers: phase {new_phase} to both (priority to turn player {turn_pid})')
    _resolve('_pvp_run_phase_start')(session, state, new_phase)
    return True


def pvp_skip_to_second_main(session, state):
    """Advance an empty attack through the client-safe path to SecondMain.

    DeclareAttackPriorityWindow is the only intermediate phase needed here:
    the client permits that priority window to transition directly to
    SecondMainPhase when CombatManager has no combats.  Do not push blocker or
    damage phases for an empty attack; those states can make the client emit
    spurious damage transactions for a combat that does not exist.
    """
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return
    attack_priority = _ge.ETurnPhases.DeclareAttackPriorityWindow
    second_main = _ge.ETurnPhases.SecondMainPhase
    phases_to_push = []
    if int(state.get('phase', 12)) < attack_priority:
        phases_to_push.append(attack_priority)
    phases_to_push.append(second_main)
    for new_phase in phases_to_push:
        _resolve('port_enter_phase')(state, new_phase)
        _resolve('pvp_save_state')(session, state)
        log_req(f'    PvP skip combat: phase {new_phase} to both')
        _resolve('_pvp_run_phase_start')(session, state, new_phase)


def _pvp_auto_pass_chain_priority(session, state, pid):
    """Consume a chain response for an F10-enabled player.

    The client normally submits the follow-up PassPriority itself.  PvP can
    hand a GreenLight to an auto-passing client while it is rebuilding its
    chain/priority UI, leaving the opponent's card waiting for a manual
    Resolve click.  The server owns the authoritative two-pass state, so
    consume this response here and use the normal pass route.
    """
    if not _pvp_chain_active(session, state) or not pvp_player_auto_passes(state, pid) or int(state.get('autopass_state', 2) or 2) != 2:
        return False
    if int(state.get('priority_pid') or 0) != int(pid):
        return False
    h = _resolve('player_handlers').get(int(pid))
    if h is None:
        return False
    log_req(f'    PvP F10: server auto-passing chain priority for {pid}')
    _resolve('route_pvp_pass')(h, session)
    return True


def _pvp_auto_pass_opponent_stop(session, state, turn_pid, opp_pid):
    """Hand an opponent-only configured stop to the opponent during F10.

    The normal phase walker pushes priority to the active player first.  When
    that player is auto-passing, an opponent stop must consume the active
    player's pass before the opponent can receive priority; otherwise the
    walker appears to stop on the active player's screen (notably at Second
    Main, which is in the opponent defaults too).
    """
    from rules_port import lifecycle as _be
    phase = int(state.get('phase', 0))
    if not pvp_player_auto_passes(state, turn_pid):
        return False
    if phase in _be.SELF_ALWAYS_STOPS or phase in _be.OPP_ALWAYS_STOPS:
        return False
    opp_stops = set(state.get(f'stops_opp_{opp_pid}') or _be.OPP_DEFAULT_STOPS)
    if phase not in opp_stops:
        return False
    h = _resolve('player_handlers').get(turn_pid)
    if not h:
        return False
    _resolve('route_pvp_pass')(h, session)
    log_req(f'    PvP F10: passed active priority at opponent stop phase {phase} to {opp_pid}')
    return True


def _pvp_resolve_stack_item(session, state, handler, my_pid):
    """Resolve the top persisted chain item when no native host is attached.

    Reached only when this tourney session has no RulesPort host — the setup
    hand-off, or a wrapper that could not build one.  Card, spell and
    triggered items go through the same shared seam the native path uses, so
    the two can no longer drift; a manual/champion ability keeps its
    PvP-specific projection.  Returns True when an item was resolved.
    """
    from rules_port import lifecycle as _be
    from rules_port.actions import AbilityResolutionState
    from rules_port.chain_items import resolve_chain_item
    from rules_port.session import projected_ability_ignores_chain
    stack = state.get('stack') or []
    if not stack:
        return False
    kind = str((stack[-1] or {}).get('kind') or '')
    if kind == 'ability':
        return bool(_resolve('_pvp_resolve_ability_item')(session, state, handler, my_pid, dict(stack[-1])))
    item = dict(_be.stack_pop(state) or {})
    if not item:
        return False
    ability = _resolve('SimpleNamespace')(instance_id=int(item.get('instance_id', 1) or 1), descriptor=item, ignores_chain=projected_ability_ignores_chain(item))
    owner_id = int(my_pid)
    source_uid = int(item.get('source_uid') or 0)
    if source_uid:
        owner_row = db_card_basic(session.session_id, source_uid, conn=_resolve('_db'))
        if owner_row:
            owner_id = int(owner_row[1])
    pids = _resolve('_pvp_session_pids')(session, state) or [owner_id, int(my_pid) if int(my_pid) != owner_id else owner_id]
    if len(pids) < 2:
        return False
    opponent_id = next((int(pid) for pid in pids if int(pid) != owner_id), owner_id)
    host = _resolve('_PvpChainHost')(handler, session, state, owner_id, opponent_id)
    resolution = resolve_chain_item(host, None, session, _resolve('_db'), ability, _ge.UID.make(244, owner_id), _ge.UID.make(244, opponent_id))
    return resolution is AbilityResolutionState.COMPLETED


@_pvp_locked
@_pvp_locked
def pvp_handle_discard(handler, session, inner_bytes):
    """Resolve one normal hand-discard transaction in tournament PvP.

    This must stay out of HCPHandler's human-vs-AI discard path: that path
    loads a PvE checkpoint and can replace the persisted PvP state with a
    Practice-style turn, which is how a discard previously jumped back to
    First Main and could produce a false deck-out victory.
    """
    if not session or not (session.session_name or '').startswith('tourney-'):
        return False
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return False
    my_pid = int(handler.client_reck_id)
    state = _resolve('pvp_load_state')(session) or {}
    if int(state.get('phase', -1)) != _ge.ETurnPhases.Discard or int(state.get('turn_pid', -1)) != my_pid:
        log_req(f"    PvP discard rejected: pid {my_pid} phase={state.get('phase')} turn={state.get('turn_pid')}")
        return True
    card_uids = _resolve('_pvp_transaction_card_uids')(inner_bytes)
    card_uid = card_uids[-1] if card_uids else None
    row = None
    if card_uid is not None:
        row = _resolve('db_hand_card_for_discard')(session.session_id, card_uid, my_pid, conn=_resolve('_db'))
    if not row:
        log_req(f'    PvP discard ignored: no hand card for pid {my_pid} uid={card_uid}')
        return True
    card_controller, card_owner, tpl_guid, instance_id = row
    owner_pid = int(card_owner)
    if owner_pid not in pids:
        owner_pid = my_pid
    _resolve('db_discard_card')(session.session_id, card_uid, owner_user_id=owner_pid, connection=_resolve('_db'))
    opp_pid = pids[0] if pids[1] == my_pid else pids[1]
    my_uid = _ge.UID.make(244, my_pid)
    opp_uid = _ge.UID.make(244, opp_pid)
    g = _ge.Game(int(session.session_id), my_uid, opp_uid)
    _resolve('_pvp_populate_game_state')(g, state, my_pid, opp_pid)
    scid = _ge.SessionCardId(_ge.UID(card_uid))
    owner_uid = _ge.UID.make(244, owner_pid)
    h_card = _resolve('player_handlers').get(my_pid) or handler
    if h_card:
        _tpl, ct, _name, cost, atk, defense, gems = h_card._card_full_data(g, scid, tpl_guid, instance_id)
        g.push_card_updated(scid, owner_uid, _ge.ECardCollections.Discard, ct, attack=atk, defense=defense, cost=cost, template_id=_tpl, gems=gems)
    view = _resolve('_pvp_fra_view')(state, owner_pid, opp_pid)
    try:
        _resolve('_pvp_dispatch_triggers')(h_card, g, session, view, _ge.UID.make(244, owner_pid), _ge.UID.make(244, opp_pid), 'CardEnteredZoneEvent', card_uid, owner_pid)
    except Exception as exc:
        log_req(f'    PvP discard trigger error: {exc}')
    _resolve('_pvp_sync_view_to_state')(state, view, owner_pid, opp_pid)
    state['stack'] = view.get('stack') or state.get('stack') or []
    state['stack_passed'] = []
    state['priority_pid'] = my_pid
    _resolve('pvp_save_state')(session, state)
    _resolve('_pvp_send_same_events')(session, g, my_uid, opp_uid)
    hand_count = _resolve('db_hand_count')(session.session_id, my_pid, conn=_resolve('_db'))
    log_req(f'    PvP discarded card {card_uid} (hand={hand_count})')
    if state.get('stack'):
        for pid in pids:
            h = _resolve('player_handlers').get(pid)
            if not h:
                continue
            recipient = _ge.UID.make(244, pid)
            other = _ge.UID.make(244, pids[0] if pids[1] == pid else pids[1])
            gp = _ge.Game(int(session.session_id), recipient, other)
            gp.push_green_light(my_uid, _ge.EPriorityContext.ResolveTopOfChain)
            _resolve('_send_pvp_packet')(h, session, gp, recipient, 'discard-chain')
        _resolve('pvp_push_phase_options')(session, state, pid=my_pid)
    elif hand_count > _resolve('DEFAULT_MAX_HAND_SIZE'):
        gp = _ge.Game(int(session.session_id), my_uid, opp_uid)
        gp.push_green_light(my_uid, _ge.EPriorityContext.Normal)
        _resolve('_send_pvp_packet')(handler, session, gp, my_uid, 'discard-more')
    else:
        _resolve('route_pvp_pass')(handler, session)
    return True


