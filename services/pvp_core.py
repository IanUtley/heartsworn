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
from services.pvp_state import pvp_load_state
from rules_port.pvp_lifecycle import phase_is_stop as port_phase_is_stop, player_auto_passes as port_player_auto_passes, default_pvp_state as port_default_pvp_state, turn_phase_list as port_turn_phase_list
from gamedata import DEFAULT_RECORD_STORE, ability_graph
from db import _db, log_req
from pvp_db import db_game_session_pids, db_card_owner_id, db_game_cards_at_location, db_card_ability_list, db_ability_effect_type_params, db_card_basic, db_card_attribute_rows, db_target_template_info
from encoder import encode_datawrapper, encode_sync_event, compress_gzip, client_session_guid

_ECardCollections = _ge.ECardCollections
_ECardTypes = _ge.ECardTypes
_RECORD_STORE = DEFAULT_RECORD_STORE

_namespace = None


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
        battle_state=pvp_load_state(session) or {},
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
    from gamedata import ability_graph
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
    from gamedata import DEFAULT_RECORD_STORE, ability_graph
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
        apply_player_visibility_to_game(g, pvp_load_state(session) or {})
    pkt = g.make_network_packet(pl_uid)
    dw = encode_datawrapper(0, 3055, compress_gzip(encode_sync_event(pkt)), 1,
                            client_session_guid(h))
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
