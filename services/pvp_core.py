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
        from services.tournament_game import pvp_load_state, pvp_save_state
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


def pvp_phase_after_blockers(session, state):
    """Select the first phase after the blocker response window.

    This must be evaluated at the end of DeclareDefensePriorityWindow, after
    both players had their Quick Action opportunity.  In particular, do not
    cache the Swiftstrike result when attackers are declared or blockers are
    assigned: a temporary keyword grant may arrive during that window.
    """
    return _resolve('port_phase_after_blockers')(bool(state.get('attackers') or {}), pvp_combat_has_swiftstrike(session, state))


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
            _resolve('_pvp_end_game')(session, state, other, pid, f'champion at 0 health')
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


def _pvp_push_mulligan_prompt(session, state, ask_pid):
    """Announce the active mulliganer and priority to both clients.

    C# emits TurnPhaseUpdated again when AcceptStartingHand passes priority.
    The unchanged Mulligan phase lets the incoming active player open their
    mulligan state while the previous player closes theirs. Redrawing does
    not call this helper because it does not pass priority.
    """
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return
    opp_pid = pids[0] if pids[1] == ask_pid else pids[1]
    active_uid = _ge.UID.make(244, int(ask_pid))
    for pid in pids:
        h = _resolve('player_handlers').get(pid)
        if not h:
            continue
        pt = _ge.UID.make(244, pid)
        opp = _ge.UID.make(244, pids[0] if pid == pids[1] else pids[1])
        g = _ge.Game(int(session.session_id), pt, opp)
        _resolve('_pvp_populate_game_state')(g, state, pid, pids[0] if pid == pids[1] else pids[1])
        g.push_player_updated(pt, champ_id=_ge.SessionCardId(_ge.UID(int((state.get('champ_map') or {}).get(str(pid), 0)))) if (state.get('champ_map') or {}).get(str(pid)) else None)
        g.push_disable_interface(pid != ask_pid)
        g.push_green_light(active_uid, _ge.EPriorityContext.Normal)
        g.push_turn_phase(_ge.ETurnPhases.Mulligan, active_uid, active_uid)
        _resolve('_send_pvp_packet')(h, session, g, pt, 'mulligan')
    state['priority_pid'] = ask_pid
    _resolve('pvp_save_state')(session, state)
    log_req(f'    PvP mulligan: active/priority to pid {ask_pid} (opponent {opp_pid} waiting)')
    _pvp_push_waiting_on(session, ask_pid)


def _pvp_resolve_deck_search(handler, session, inner_bytes, my_pid, typed_payload=None):
    """Resolve a PvP "search your deck" pick (Darkspire Priestess's Deathcry):
    move the player's chosen matching deck card into their hand and push the
    objective CardMoved / CardDrawn / CardUpdated stream to both players."""
    import struct
    state = _resolve('pvp_load_state')(session) or {}
    pend = state.pop('pending_deck_search', None)
    if not pend:
        return False
    state.pop('resolution_paused', None)
    chosen_uid = None
    if isinstance(inner_bytes, bytes):
        for m_du in re.finditer(b'm_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});', inner_bytes):
            try:
                uid64 = struct.unpack('<Q', bytes.fromhex(m_du.group(1).decode()))[0]
                if uid64 & 255 == 1:
                    chosen_uid = int(uid64)
            except Exception:
                continue
    typed_uid = _pvp_typed_picker_uid(typed_payload)
    if typed_uid is not None:
        chosen_uid = typed_uid
    if not chosen_uid or chosen_uid not in pend['candidates']:
        _resolve('pvp_save_state')(session, state)
        log_req(f"    PvP deck-search invalid choice: chosen={chosen_uid} candidates={pend['candidates']}")
        return True
    owner_id = int(pend['owner_id'])
    pids = _resolve('db_game_session_pids')(session.session_id)
    pl_t = _ge.UID.make(244, owner_id)
    opp_pid = [p for p in pids if p != owner_id][0]
    ai_t = _ge.UID.make(244, opp_pid)
    bstate = state
    g = _ge.Game(int(session.session_id), pl_t, ai_t)
    from rules_port.context import EffectContext
    from rules_port.deck_effects import move_deck_card_to_hand
    bstate['_rules_port_attached'] = True
    move_deck_card_to_hand(EffectContext.from_rules_port(g, session, _resolve('_db'), handler, pl_t, ai_t, bstate, 'move_deck_card_to_hand', ability=None), chosen_uid, owner_id)
    _resolve('pvp_save_state')(session, state)
    _resolve('_pvp_send_same_events')(session, g, pl_t, ai_t)
    log_req(f'    PvP deck-search resolved: {hex(chosen_uid)} -> hand (pid {owner_id})')
    return True


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
    log_req(f'>>> JoinDisconnectedGame (dt=22023)')
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


class _PvpChainHost:
    """Tournament PvP projection for one chain item.

    ``rules_port.chain_items`` owns the lifecycle: resolve the authored
    effects, hold the item while a client picker is open, consume the
    picker-continuation marker, and present the chain item.  This adapter
    supplies only what is specific to a two-human match — the side-oriented
    effect view the trigger backend evaluates against, the persisted PvP
    checkpoint round-trip, and the packet projection to both clients.
    Practice/PvE uses the same lifecycle through the hooks on
    ``hconnect_server.HCPHandler``.

    The lifecycle state is the persisted checkpoint itself, not the effect
    view: ``rules_port.persistence.load_state`` installs that checkpoint as
    the ability resolver's battle state, so a lifecycle that tracked a copy
    would miss the pause marker and pending-input keys the effects just wrote
    (the PvP half of the Darkspire Priestess deck-search loop).
    """

    def __init__(self, handler, session, state, owner_id, opponent_id):
        self.handler = handler
        self.session = session
        self.state = state if isinstance(state, dict) else {}
        self.owner_id = int(owner_id)
        self.opponent_id = int(opponent_id)

    def _view(self, state):
        return _resolve('_pvp_fra_view')(state, self.owner_id, self.opponent_id)

    def _champion_targets(self):
        """Both champions as (uid, participant id, name, health).

        The trigger resolver receives this host as its handler; without the
        champions, Champions-collection conditions ("you gained health three
        or more times this turn") counted no cards in PvP.
        """
        champ_map = self.state.get('champ_map') or {}
        out = []
        for pid in self.state.get('pids') or (self.owner_id, self.opponent_id):
            uid = int(champ_map.get(str(pid), 0) or 0)
            if uid:
                out.append((uid, int(pid), 'Champ', int(self.state.get(f'hp_{pid}', 20))))
        return out

    def chain_load(self, session):
        return self.state

    def chain_save(self, session, state):
        _resolve('pvp_save_state')(self.session, self.state)

    def chain_new_game(self, session, state, player_uid, ai_uid):
        game = _ge.Game(int(session.session_id), player_uid, ai_uid)
        _resolve('_pvp_populate_game_state')(game, self.state, self.owner_id, self.opponent_id)
        return game

    def chain_send(self, session, game, player_uid, ai_uid):
        _resolve('_pvp_send_same_events')(session, game, player_uid, ai_uid)

    def chain_resume_continuation(self, session, state, game, player_uid, ai_uid, continuation):
        from rules_port.resolution import resume_ability_continuation_parents
        view = self._view(state)
        view['_rules_port_attached'] = True
        previous = getattr(self.handler, '_current_bstate', None)
        self.handler._current_bstate = view
        try:
            result = resume_ability_continuation_parents(self.handler, game, session, _resolve('_db'), player_uid, ai_uid, view, continuation)
        finally:
            if previous is None:
                try:
                    del self.handler._current_bstate
                except AttributeError:
                    pass
            else:
                self.handler._current_bstate = previous
        _resolve('_pvp_sync_view_to_state')(state, view, self.owner_id, self.opponent_id)
        for key in ('resolution_paused', 'paused_chain_instance_id', 'completed_chain_instance_id', 'rules_port_suspended_chain_continuation'):
            if key in view:
                state[key] = view[key]
            else:
                state.pop(key, None)
        persisted = _resolve('pvp_load_state')(session) or {}
        for key in ('pending_trigger', 'pending_deck_search', 'pending_choice', 'pending_conversation'):
            if persisted.get(key):
                state[key] = persisted[key]
        _resolve('pvp_save_state')(session, state)
        return result

    def chain_card_data(self, game, scid, template_guid):
        return self.handler._card_full_data(game, scid, template_guid)

    def chain_mark_warzone_entry(self, session, source_uid):
        """Keep PvP arrival state and continuous-effect order in sync."""
        from pvp_db import db_card_set_warzone_arrival, db_set_card_resolved_at
        db_card_set_warzone_arrival(session.session_id, source_uid, conn=_resolve('_db'))
        counter = int(self.state.get('resolve_counter', 0) or 0) + 1
        self.state['resolve_counter'] = counter
        db_set_card_resolved_at(session.session_id, source_uid, counter, conn=_resolve('_db'))

    def _card_full_data(self, game, scid, template_guid, instance_id=None):
        """Expose the shared effect-context card projection hook."""
        return self.handler._card_full_data(game, scid, template_guid, instance_id)

    def chain_dispatch(self, session, game, state, player_uid, ai_uid, event_type, source_uid, owner_id, **event_data):
        view = self._view(state)
        result = _resolve('_pvp_dispatch_triggers')(self.handler, game, session, view, player_uid, ai_uid, event_type, source_uid, owner_id, **event_data)
        _resolve('_pvp_sync_view_to_state')(state, view, self.owner_id, self.opponent_id)
        return result

    def chain_state_based(self, session, state, game, player_uid, ai_uid):
        """Apply PvP state-based actions in the same chain packet."""
        from rules_port.context import EffectContext
        from rules_port.death_effects import state_based_deaths
        view = self._view(state)
        view['_rules_port_attached'] = True
        previous = getattr(self.handler, '_current_bstate', None)
        self.handler._current_bstate = view
        try:
            dead = state_based_deaths(EffectContext.from_rules_port(game, session, _resolve('_db'), self.handler, player_uid, ai_uid, view, 'state_based_death', ability=None))
        finally:
            if previous is None:
                try:
                    del self.handler._current_bstate
                except AttributeError:
                    pass
            else:
                self.handler._current_bstate = previous
        _resolve('_pvp_sync_view_to_state')(state, view, self.owner_id, self.opponent_id)
        _resolve('pvp_save_state')(session, state)
        return bool(dead)

    def chain_push_empty(self, port, state, item, pending):
        return not pending and (not state.get('stack'))

    def chain_finalize(self, session, state, game, item, pending, player_uid, ai_uid):
        """Priority rides in the same packet as the item's own events."""
        if pending:
            return
        state['stack_passed'] = []
        state['stack_player_passed'] = False
        state['stack_ai_passed'] = False
        if not state.get('stack'):
            turn_pid = int(state.get('turn_pid') or self.owner_id)
            state['priority_pid'] = turn_pid
            game.push_green_light(_ge.UID.make(244, turn_pid), _ge.EPriorityContext.Normal)
        else:
            state['priority_pid'] = self.opponent_id

    def chain_after_item(self, session, state, game, item, player_uid, ai_uid):
        _pvp_check_game_end(self.session, self.state)
        if not self.state.get('stack') or not self.state.get('priority_pid'):
            return
        if any((self.state.get(key) for key in ('pending_choice', 'pending_deck_search', 'pending_trigger', 'pending_conversation', 'pending_discard_ability'))):
            return
        priority_pid = int(self.state['priority_pid'])
        next_handler = _resolve('player_handlers').get(priority_pid)
        if not next_handler:
            return
        next_uid = _ge.UID.make(244, priority_pid)
        response = _ge.Game(int(self.session.session_id), next_uid, _ge.UID.make(244, self.owner_id))
        response.push_green_light(next_uid, _ge.EPriorityContext.ResolveTopOfChain)
        _resolve('_send_pvp_packet')(next_handler, self.session, response, next_uid, 'native-chain-next')


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


def _pvp_resolve_combat(session, state, first_strike=False):
    """Resolve the declared PvP combat through the SAME shared resolver the
    AI path uses (ai.resolve_combat), then push the identical event stream to
    both players.  ``first_strike=True`` is the Swiftstrike damage step (only
    FirstStrike/DualStrike combatants deal; casualties removed first)."""
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return
    attackers = {int(k): int(v) for k, v in (state.get('attackers') or {}).items()}
    if not attackers:
        state.pop('attackers', None)
        state.pop('blockers', None)
        return
    attacker_pid = state.get('turn_pid')
    defender_pid = pids[0] if pids[1] == attacker_pid else pids[1]
    blockers = {int(k): [int(b) for b in v] for k, v in (state.get('blockers') or {}).items()}
    view = _resolve('_pvp_fra_view')(state, attacker_pid, defender_pid)
    handler = _resolve('player_handlers').get(attacker_pid)
    if not handler:
        return
    pl_t = _ge.UID.make(244, attacker_pid)
    ai_t = _ge.UID.make(244, defender_pid)
    order_map = {int(k): [int(b) for b in v] for k, v in (state.get('damage_order') or {}).items()}
    try:
        view['player_attackers'] = attackers
        view['ai_blockers'] = blockers
        view['player_damage_order'] = order_map
        from rules_port.context import EffectContext
        from rules_port.combat_damage import resolve as resolve_native
        native_game = _ge.Game(int(session.session_id), pl_t, ai_t)
        view['_rules_port_attached'] = True
        context = EffectContext.from_rules_port(native_game, session, _resolve('_db'), handler, pl_t, ai_t, view, '', ability=None)
        view = resolve_native(context, first_strike=first_strike, attacker_key='player_attackers', blocker_key='ai_blockers')
        if native_game.events:
            _resolve('_pvp_send_same_events')(session, native_game, pl_t, ai_t)
    except Exception as e:
        log_req(f'    PvP combat resolve error: {e}')
        import traceback
        traceback.print_exc()
        return
    state[f'hp_{attacker_pid}'] = int(view.get('player_health', 20))
    state[f'hp_{defender_pid}'] = int(view.get('ai_health', 20))
    if first_strike:
        state['attackers'] = dict(view.get('player_attackers') or {})
        state['blockers'] = dict(view.get('ai_blockers') or {})
        if view.get('blocked_attackers'):
            state['blocked_attackers'] = dict(view['blocked_attackers'])
    if not first_strike:
        state.pop('attackers', None)
        state.pop('blockers', None)
        state.pop('blocked_attackers', None)
        state.pop('damage_order', None)
    _resolve('pvp_save_state')(session, state)
    log_req(f"    PvP combat resolved: {attacker_pid} hp {state[f'hp_{attacker_pid}']} / {defender_pid} hp {state[f'hp_{defender_pid}']}")
    try:
        g2 = _ge.Game(int(session.session_id), pl_t, ai_t)
        g2.player_health = int(state.get(f'hp_{attacker_pid}', 20))
        g2.ai_health = int(state.get(f'hp_{defender_pid}', 20))
        from rules_port.context import EffectContext
        from rules_port.death_effects import state_based_deaths
        view['_rules_port_attached'] = True
        state_based_deaths(EffectContext.from_rules_port(g2, session, _resolve('_db'), handler, pl_t, ai_t, view, 'state_based_death', ability=None))
        if g2.events:
            _resolve('_pvp_send_same_events')(session, g2, pl_t, ai_t)
    except Exception as e:
        log_req(f'    PvP post-combat state-based deaths error: {e}')
    if view.get('player_health') is not None:
        state[f'hp_{attacker_pid}'] = int(view['player_health'])
    if view.get('ai_health') is not None:
        state[f'hp_{defender_pid}'] = int(view['ai_health'])
    _resolve('_pvp_sync_view_to_state')(state, view, attacker_pid, defender_pid)
    _resolve('pvp_save_state')(session, state)
    _pvp_check_game_end(session, state)


@_pvp_locked
def handle_ready_for_game_events(handler, session, pvp_events_ready, log_req=log_req):
    """Called from 22029 handler. When both players are ready for events, pushes game start."""
    if not session or not (session.session_name or '').startswith('tourney-'):
        return False
    try:
        pvp_events_ready.setdefault(session.session_id, {'handlers': {}})
        pr = pvp_events_ready[session.session_id]
        puid = handler.client_reck_id if hasattr(handler, 'client_reck_id') else 0
        if puid not in pr['handlers']:
            pr['handlers'][puid] = []
        pr['handlers'][puid].append(handler)
        log_req(f"    Events ready: {len(pr['handlers'])}/2 for tourney session {session.session_id}")
        if len(pr['handlers']) >= 2:
            all_handlers = [h for hl in pr['handlers'].values() for h in hl]
            for h in all_handlers:
                try:
                    push_pvp_game_start(h, session)
                except Exception as e:
                    import traceback
                    log_req(f'    push_pvp_game_start FAILED: {e}\n{traceback.format_exc()}')
            del pvp_events_ready[session.session_id]
            try:
                state = _resolve('pvp_load_state')(session) or {}
                _pvp_push_waiting_on(session, int(state.get('goes_first_pid') or 0) or None)
            except Exception as e:
                log_req(f'    PvP pick-goes-first wait push failed: {e}')
            return True
    except Exception as e:
        import traceback
        log_req(f'    handle_events_ready FAILED: {e}\n{traceback.format_exc()}')
    return False


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


def _pvp_push_reconnect_snapshot(handler, session, pid):
    """Restore the persisted PvP view for a reconnecting client.

    The normal start packet cannot be reused here: it shuffles/deals the
    decks.  ``game_cards`` and ``turn_order_json`` are the authoritative
    mid-game state, so rebuild only client representations and current HUD /
    priority data from those rows.
    """
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2 or int(pid) not in pids:
        return False
    pid = int(pid)
    opp_pid = pids[1] if pids[0] == pid else pids[0]
    state = _resolve('pvp_load_state')(session) or {}
    with _resolve('pvp_session_lock')(session):
        latest_state = _resolve('pvp_load_state')(session)
        if latest_state:
            state = latest_state
            _resolve('pvp_save_state')(session, state)
    pl_uid = _ge.UID.make(244, pid)
    opp_uid = _ge.UID.make(244, opp_pid)
    champion_rows = {int(owner): row for owner, row in ((p, _resolve('db_game_champion')(session.session_id, p)) for p in (pid, opp_pid)) if row and row[0]}
    champ_map = state.setdefault('champ_map', {})
    guid_map = state.setdefault('champ_guid_map', {})
    for owner, row in champion_rows.items():
        champ_map[str(owner)] = int(row[0])
        if len(row) > 1 and row[1]:
            guid_map[str(owner)] = row[1]
    reconnect_champions = {owner: int(champ_map.get(str(owner), 0)) for owner in (pid, opp_pid)}
    invalid_champions = [owner for owner, card_uid in reconnect_champions.items() if card_uid <= 0 or card_uid & 255 == 0]
    if invalid_champions:
        log_req(f'    PvP reconnect aborted: missing valid champion SessionCardId for pid(s) {invalid_champions}')
        return False
    if champion_rows:
        _resolve('pvp_save_state')(session, state)
    g = _ge.Game(int(session.session_id), pl_uid, opp_uid)
    g.player_champion_card_id = _ge.SessionCardId(_ge.UID(reconnect_champions[pid]))
    g.ai_champion_card_id = _ge.SessionCardId(_ge.UID(reconnect_champions[opp_pid]))
    _resolve('_pvp_populate_game_state')(g, state, pid, opp_pid)
    from rules_port.visibility import apply_player_visibility_to_game, merge_underground_visibility_flags
    state['player_visibility'] = merge_underground_visibility_flags(_resolve('_db'), session, handler, state, state.get('player_visibility'), owners=pids)
    apply_player_visibility_to_game(g, state)
    handler._current_bstate = state
    player_added_inner = _resolve('encode_objfmt_response')(['Game.Shared.Network.GameSession.PlayerAddedEventArgs', 'Game.Shared.UID', 'Game.Shared.PlayerState', 'System.Int32'], [('RoutingPlayerId', 'uid', int(opp_uid.uid64)), ('PlayerState', 'struct', ('Game.Shared.PlayerState', [('PlayerId', 'uid', int(opp_uid.uid64)), ('PlayerPosition', 'int', 1)]))])
    player_added_dw = _resolve('encode_datawrapper')(0, 3050, _resolve('compress_gzip')(player_added_inner), 1, _resolve('client_session_guid')(handler))
    handler.scnt += 1
    handler.send({'issuer': f'0.0.0.0.ServiceGameSession.246.{session.session_id}.{handler.scnt}', 'target': 'ServiceGameSession', 'instance': str(session.server_id), 'reqid': 0, 'c': 0, 'conh': 0, 'sid': handler.sid}, player_added_dw)
    log_req(f'    PvP reconnect: added opponent pid {opp_pid}')
    goes_first_pid = int(state.get('goes_first_pid', state.get('turn_pid', pid)))
    if goes_first_pid not in pids:
        goes_first_pid = pid
    game_started_pids = [pid, opp_pid]
    champion_template_ids = []
    for game_pid in game_started_pids:
        champion_row = _resolve('db_game_champion')(session.session_id, game_pid)
        champion_template_ids.append(champion_row[1] if champion_row and champion_row[1] else '00000000-0000-0000-0000-000000000000')
    player_champion_row = _resolve('db_game_champion')(session.session_id, pid)
    opponent_champion_row = _resolve('db_game_champion')(session.session_id, opp_pid)
    _resolve('_pvp_set_handler_champions')(handler, g.player_champion_card_id, g.ai_champion_card_id, player_champion_row[1] if player_champion_row else None, opponent_champion_row[1] if opponent_champion_row else None)
    champion_names = [_resolve('db_tournament_player_name_for_session')(session.session_id, game_pid) or f'Player {index + 1}' for index, game_pid in enumerate(game_started_pids)]
    g.push_game_started(champion_names=champion_names, champion_template_ids=champion_template_ids, player_first=goes_first_pid == pid)
    g.push_first_player_dictated(_ge.UID.make(244, goes_first_pid))
    g.push_player_updated(pl_uid, champ_id=g.player_champion_card_id)
    g.push_player_updated(opp_uid, champ_id=g.ai_champion_card_id)
    for card_uid, template_guid, owner, card_type, card_state, _abilities, _attrs in db_game_cards_at_location(session.session_id, 'champion'):
        scid = _ge.SessionCardId(_ge.UID(int(card_uid)))
        try:
            handler._card_full_data(g, scid, template_guid)
        except Exception:
            pass
        g.push_card_updated(scid, _ge.UID.make(244, int(owner)), _ECardCollections.None_, _ge.card_type_from_db(card_type), template_id=template_guid, state=int(card_state or 0))
    g.push_champion_card_played(pl_uid, False, 'Player 1', g.player_champion_card_id)
    g.push_champion_card_played(opp_uid, False, 'Player 2', g.ai_champion_card_id)
    _resolve('pvp_push_warzone_updates')(session, state, game=g)
    for zone, collection in (('discard', _ECardCollections.Discard), ('void', _ECardCollections.Void)):
        for card_uid, template_guid, owner, card_type, card_state, _abilities, _attrs in db_game_cards_at_location(session.session_id, zone):
            scid = _ge.SessionCardId(_ge.UID(int(card_uid)))
            try:
                handler._card_full_data(g, scid, template_guid)
            except Exception:
                pass
            g.push_card_updated(scid, _ge.UID.make(244, int(owner)), collection, _ge.card_type_from_db(card_type), template_id=template_guid, state=int(card_state or 0))
    for owner_pid in pids:
        owner_uid = _ge.UID.make(244, owner_pid)
        for card_uid, template_guid, owner, card_type, card_state, _abilities, _attrs in db_game_cards_at_location(session.session_id, 'deck', user_id=owner_pid):
            scid = _ge.SessionCardId(_ge.UID(int(card_uid)))
            try:
                handler._card_full_data(g, scid, template_guid)
            except Exception:
                pass
            g.push_card_updated(scid, owner_uid, _ECardCollections.Deck, _ge.card_type_from_db(card_type), template_id=template_guid, nulling=True)
        g.push_deck_created(owner_uid)
        for card_uid, template_guid, owner, card_type, card_state, _abilities, _attrs in db_game_cards_at_location(session.session_id, 'hand', user_id=owner_pid):
            scid = _ge.SessionCardId(_ge.UID(int(card_uid)))
            try:
                handler._card_full_data(g, scid, template_guid)
            except Exception:
                pass
            g.push_card_updated(scid, owner_uid, _ECardCollections.Hand, _ge.card_type_from_db(card_type), template_id=template_guid, state=int(card_state or 0), nulling=owner_pid != pid)
    _resolve('_pvp_push_reconnect_underground')(g, session, handler, pid, pl_uid, opp_uid, state)
    turn_pid = int(state.get('turn_pid', pid))
    priority_pid = int(state.get('priority_pid', turn_pid))
    phase = int(state.get('phase', _ge.ETurnPhases.FirstMainPhase))
    priority_uid = _ge.UID.make(244, priority_pid)
    turn_uid = _ge.UID.make(244, turn_pid)
    priority_elapsed_seconds = _resolve('priority_elapsed_ticks')(state, priority_pid) // 10000000
    g.push_green_light(priority_uid, _ge.EPriorityContext.Normal)
    _pvp_push_turn_phase_with_elapsed(g, phase, turn_uid, priority_uid, priority_elapsed_seconds)
    g.push_reconnect_done()
    _resolve('_send_pvp_packet')(handler, session, g, pl_uid, 'reconnect-snapshot')
    if priority_pid == pid:
        if phase in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
            _resolve('pvp_push_main_phase_options')(session, state)
        elif phase == _ge.ETurnPhases.DeclareAttack:
            _resolve('pvp_push_attack_options')(session, state)
        elif phase == _ge.ETurnPhases.DeclareDefense:
            pvp_push_blocker_options(session, state)
        elif phase not in (3, 4, 5, 6, 7, 8, 9):
            _resolve('pvp_push_phase_options')(session, state, pid=pid)
    log_req(f'    PvP reconnect snapshot restored for pid {pid} (phase={phase}, priority={priority_pid})')
    return True


def _pvp_reassign_priority_after_disconnect(session, disconnected_pid, survivor_pid):
    """Make a live PvP checkpoint usable by the still-connected player.

    A socket disappearing must not leave the native RulesPort priority action
    owned by that socket.  If the disconnected player owned priority, hand the
    current window to the survivor and rebuild the private options packet.
    If the survivor already owned priority, leave it untouched so their next
    transaction remains valid.
    """
    try:
        disconnected_pid = int(disconnected_pid)
        survivor_pid = int(survivor_pid)
    except (TypeError, ValueError):
        return False
    if not session or survivor_pid <= 0:
        return False
    with _resolve('pvp_session_lock')(session):
        state = _resolve('pvp_load_state')(session) or {}
        if not state.get('pvp'):
            return False
        pids = [int(pid) for pid in _resolve('db_game_session_pids')(session.session_id)]
        if disconnected_pid not in pids or survivor_pid not in pids:
            return False
        old_priority = int(state.get('priority_pid') or 0)
        phase = int(state.get('phase', 0) or 0)
        if old_priority == disconnected_pid and phase >= int(_ge.ETurnPhases.FirstMainPhase):
            state['priority_pid'] = survivor_pid
            _resolve('port_reset_priority_interval')(state, survivor_pid)
            _resolve('pvp_save_state')(session, state)
            log_req(f'    PvP priority handed off: {disconnected_pid} -> {survivor_pid} phase={phase}')
        else:
            _resolve('flush_priority_clock')(state)
            _resolve('pvp_save_state')(session, state)
    survivor_handler = _resolve('player_handlers').get(survivor_pid)
    if not survivor_handler:
        return True
    state = _resolve('pvp_load_state')(session) or {}
    if int(state.get('priority_pid') or 0) != survivor_pid:
        return True
    opponent_pid = next((pid for pid in pids if pid != survivor_pid), disconnected_pid)
    pl_uid = _ge.UID.make(244, survivor_pid)
    opp_uid = _ge.UID.make(244, opponent_pid)
    game = _ge.Game(int(session.session_id), pl_uid, opp_uid)
    _resolve('_pvp_populate_game_state')(game, state, survivor_pid, opponent_pid)
    _pvp_apply_visibility(game, state)
    priority_uid = _ge.UID.make(244, survivor_pid)
    turn_uid = _ge.UID.make(244, int(state.get('turn_pid') or survivor_pid))
    game.push_green_light(priority_uid, _ge.EPriorityContext.Normal)
    _pvp_push_turn_phase_with_elapsed(game, phase, turn_uid, priority_uid, _resolve('priority_elapsed_ticks')(state, survivor_pid) // 10000000)
    _resolve('_send_pvp_packet')(survivor_handler, session, game, pl_uid, 'disconnect-priority')
    if phase in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
        _resolve('pvp_push_main_phase_options')(session, state)
    elif phase == _ge.ETurnPhases.DeclareAttack:
        _resolve('pvp_push_attack_options')(session, state)
    elif phase == _ge.ETurnPhases.DeclareDefense:
        pvp_push_blocker_options(session, state)
    elif phase not in (3, 4, 5, 6, 7, 8, 9):
        _resolve('pvp_push_phase_options')(session, state, pid=survivor_pid)
    return True


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
        from gamedata import DEFAULT_RECORD_STORE, ability_graph
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


def _pvp_resolve_trigger_target(handler, session, inner_bytes, my_pid, typed_payload=None):
    """Resolve a PvP triggered-ability continuation.

    The same path handles a target selection after an optional opt-in.  The
    optional marker is removed by ``_pvp_resolve_optional_trigger`` before it
    calls this function, so a no-target optional ability can resolve with an
    empty TargetMap.
    """
    import re as _re
    import struct as _st
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return False
    state = _resolve('pvp_load_state')(session) or {}
    pend = state.get('pending_trigger')
    if not pend:
        return False
    chosen_uid = None
    activation_data = pend.get('activation_data')
    activation_data = dict(activation_data) if isinstance(activation_data, dict) else {}
    pending_target_map = activation_data.get('target_map') or {}
    target_index = int(pend.get('target_index', 0) or 0)
    if isinstance(pending_target_map, dict):
        selected = pending_target_map.get(target_index, pending_target_map.get(str(target_index)))
        values = selected if isinstance(selected, (list, tuple, set)) else (selected,) if selected is not None else ()
        for value in values:
            try:
                uid = int(getattr(value, 'uid64', value))
            except (TypeError, ValueError):
                continue
            if uid & 255 == 1:
                chosen_uid = uid
    if isinstance(inner_bytes, bytes):
        for m_du in _re.finditer(b'm_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});', inner_bytes):
            try:
                uid64 = _st.unpack('<Q', bytes.fromhex(m_du.group(1).decode()))[0]
                if uid64 & 255 == 1:
                    chosen_uid = int(uid64)
            except Exception:
                continue
    if chosen_uid is None and isinstance(typed_payload, dict):
        raw_activation = typed_payload.get('activation_data')
        records = raw_activation if isinstance(raw_activation, (list, tuple)) else (raw_activation,)
        for record in records:
            if not isinstance(record, dict):
                continue
            target_map = record.get('target_map') or {}
            selected = target_map.get(target_index, target_map.get(str(target_index))) if isinstance(target_map, dict) else None
            values = selected if isinstance(selected, (list, tuple, set)) else (selected,) if selected is not None else ()
            for value in values:
                try:
                    uid = int(getattr(value, 'uid64', value))
                except (TypeError, ValueError):
                    continue
                if uid & 255 == 1:
                    chosen_uid = uid
    if isinstance(typed_payload, dict):
        raw_activation = typed_payload.get('activation_data')
        if isinstance(raw_activation, dict):
            activation_data.update(raw_activation)
            activation_data['target_map'] = dict(raw_activation.get('target_map') or activation_data.get('target_map') or {})
    state.pop('pending_trigger', None)
    _resolve('pvp_save_state')(session, state)
    ag = str(pend['ability_guid'])
    src = int(pend['source_uid'])
    owner_id = int(pend.get('owner_id', my_pid))
    opp_pid = pids[0] if pids[1] == owner_id else pids[1]
    pl_t = _ge.UID.make(244, owner_id)
    ai_t = _ge.UID.make(244, opp_pid)
    view = _resolve('_pvp_fra_view')(state, owner_id, opp_pid)
    view['pending_trigger'] = None
    view['resolving_owner_id'] = owner_id
    view['player_mod_target'] = chosen_uid
    view['player_spell_target'] = chosen_uid
    view['resolving_source_uid'] = src
    g = _ge.Game(int(session.session_id), pl_t, ai_t)
    _resolve('_pvp_populate_game_state')(g, state, owner_id, opp_pid)
    try:
        from rules_port.resolution import resolve_port_trigger, trigger_event_fields
        resolve_port_trigger(handler, g, session, _resolve('_db'), pl_t, ai_t, view, {'kind': 'trigger', 'ability_guid': ag, 'source_uid': src, 'source_owner_uid': owner_id, 'target_uid': chosen_uid, 'trigger_target_uid': pend.get('trigger_target_uid'), **trigger_event_fields(pend), 'activation_data': activation_data, 'instance_id': int(pend.get('instance_id', 1))})
    except Exception as e:
        import traceback
        log_req(f'    PvP trigger resolve error: {e}')
        traceback.print_exc()
    if view.get('player_health') is not None:
        state[f'hp_{owner_id}'] = int(view['player_health'])
    if view.get('ai_health') is not None:
        state[f'hp_{opp_pid}'] = int(view['ai_health'])
    _resolve('_pvp_sync_view_to_state')(state, view, owner_id, opp_pid)
    _resolve('pvp_save_state')(session, state)
    _resolve('_pvp_send_same_events')(session, g, pl_t, ai_t)
    if _pvp_check_game_end(session, state):
        return True
    turn_pid = int(state.get('turn_pid') or owner_id)
    state['priority_pid'] = turn_pid
    state['passes'] = []
    state['stack_passed'] = []
    _resolve('pvp_save_state')(session, state)
    turn_h = _resolve('player_handlers').get(turn_pid)
    if turn_h:
        turn_uid = _ge.UID.make(244, turn_pid)
        other_pid = pids[1] if pids[0] == turn_pid else pids[0]
        other_uid = _ge.UID.make(244, other_pid)
        resume = _ge.Game(int(session.session_id), turn_uid, other_uid)
        _resolve('_pvp_populate_game_state')(resume, state, turn_pid, other_pid)
        resume.push_chain_empty()
        resume.push_green_light(turn_uid, _ge.EPriorityContext.Normal)
        _pvp_push_turn_phase_with_elapsed(resume, int(state.get('phase', 0)), turn_uid, turn_uid, _resolve('priority_elapsed_ticks')(state, turn_pid) // 10000000)
        _resolve('_send_pvp_packet')(turn_h, session, resume, turn_uid, 'trigger-target-chain-empty')
    phase = int(state.get('phase', 0))
    if phase in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
        _resolve('pvp_push_main_phase_options')(session, state)
    elif phase == _ge.ETurnPhases.DeclareAttack:
        _resolve('pvp_push_attack_options')(session, state)
    elif phase == _ge.ETurnPhases.DeclareDefense:
        pvp_push_blocker_options(session, state)
    log_req(f"    PvP trigger resolved: {ag[:8]} -> {(hex(chosen_uid) if chosen_uid else 'none')}; priority -> {turn_pid}")
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


def pvp_push_current_phase_options(session, state, pid=None):
    """Push the PlayerOptionList the current phase requires (mirrors PvE
    ``_push_phase_options``).

    Single owner of the phase -> options contract: phase entry
    (``_pvp_run_phase_start``), a client ``RequestPrioritySync``, and debug
    state refreshes must all project the same list.  The client replaces its
    whole option set on every push, so a resync answered with the generic
    quick-action window during DeclareAttack/DeclareDefense clears
    ``ECardUsage.Attack``/``Defend`` and makes every ready troop unselectable.

    Main phases get the playable list, DeclareAttack the attack options,
    DeclareDefense the blocker options; every OTHER stop phase gets hand
    QuickActions + champion powers so instant-speed responses are possible in
    any priority window.  A live chain item outranks all of them: the holder
    responds to the chain instead of declaring combat.
    """
    if _pvp_chain_active(session, state):
        _resolve('pvp_push_phase_options')(session, state, pid=pid)
        return
    phase = int(state.get('phase', 0) or 0)
    if phase in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
        _resolve('pvp_push_main_phase_options')(session, state)
    elif phase == _ge.ETurnPhases.DeclareAttack:
        _resolve('pvp_push_attack_options')(session, state)
    elif phase == _ge.ETurnPhases.DeclareDefense:
        pvp_push_blocker_options(session, state)
    elif phase not in (3, 4, 5, 6, 7, 8, 9):
        _resolve('pvp_push_phase_options')(session, state, pid=pid)


def _pvp_resolve_optional_trigger(handler, session, inner_bytes, my_pid, typed_payload=None):
    """Consume a PvP optional-trigger opt-in/opt-out response."""
    state = _resolve('pvp_load_state')(session) or {}
    pend = state.get('pending_trigger')
    if not pend or not pend.get('optional'):
        return False
    raw = (typed_payload or {}).get('activation_data') if isinstance(typed_payload, dict) else None
    records = raw if isinstance(raw, (list, tuple)) else (raw,)
    activation = {}
    first = None
    wanted_instance = int(pend.get('instance_id', 0) or 0)
    wanted_guid = str(pend.get('ability_guid') or '').lower()
    for candidate in records:
        if not isinstance(candidate, dict):
            continue
        if first is None:
            first = candidate
        try:
            instance = int(candidate.get('ability_instance_id', candidate.get('instance_id', 0)) or 0)
        except (TypeError, ValueError):
            instance = 0
        guid = str(candidate.get('ability_template_id', candidate.get('ability_guid', '')) or '').lower()
        if wanted_instance and instance == wanted_instance or (wanted_guid and guid == wanted_guid):
            activation = dict(candidate)
            break
    if not activation:
        activation = dict(first or {})

    def flag(value, default=False):
        if value is None:
            return default
        if isinstance(value, str):
            return value.strip().lower() in ('1', 'true', 'yes', 'on')
        return bool(value)
    declined = flag(activation.get('disable')) or ('opted' in activation and (not flag(activation.get('opted'))))
    if not declined:
        pend['activation_data'] = activation
        pend.pop('optional', None)
        _resolve('pvp_save_state')(session, state)
        return _resolve('_pvp_resolve_trigger_target')(handler, session, b'', my_pid, typed_payload=None)
    owner_id = int(pend.get('owner_id', my_pid) or my_pid)
    instance_id = int(pend.get('instance_id', 1) or 1)
    ag = str(pend.get('ability_guid') or '')
    state.pop('pending_trigger', None)
    state['priority_pid'] = int(state.get('turn_pid') or owner_id)
    state['passes'] = []
    state['stack_passed'] = []
    _resolve('pvp_save_state')(session, state)
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return True
    opponent_id = next((pid for pid in pids if int(pid) != owner_id))
    pl_t = _ge.UID.make(244, owner_id)
    ai_t = _ge.UID.make(244, opponent_id)
    cancelled = _ge.Game(int(session.session_id), pl_t, ai_t)
    _resolve('_pvp_populate_game_state')(cancelled, state, owner_id, opponent_id)
    cancelled.push_ability_cancelled(instance_id, pl_t)
    _resolve('_pvp_send_same_events')(session, cancelled, pl_t, ai_t)
    turn_pid = int(state.get('priority_pid') or owner_id)
    turn_h = _resolve('player_handlers').get(turn_pid)
    if turn_h:
        turn_uid = _ge.UID.make(244, turn_pid)
        other_pid = pids[1] if pids[0] == turn_pid else pids[0]
        other_uid = _ge.UID.make(244, other_pid)
        resume = _ge.Game(int(session.session_id), turn_uid, other_uid)
        _resolve('_pvp_populate_game_state')(resume, state, turn_pid, other_pid)
        if state.get('stack'):
            resume.push_green_light(turn_uid, _ge.EPriorityContext.ResolveTopOfChain)
        else:
            resume.push_chain_empty()
            resume.push_green_light(turn_uid, _ge.EPriorityContext.Normal)
        _pvp_push_turn_phase_with_elapsed(resume, int(state.get('phase', 0)), turn_uid, turn_uid, _resolve('priority_elapsed_ticks')(state, turn_pid) // 10000000)
        _resolve('_send_pvp_packet')(turn_h, session, resume, turn_uid, 'optional-trigger-declined')
    phase = int(state.get('phase', 0))
    if not state.get('stack'):
        if phase in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
            _resolve('pvp_push_main_phase_options')(session, state)
        elif phase == _ge.ETurnPhases.DeclareAttack:
            _resolve('pvp_push_attack_options')(session, state)
        elif phase == _ge.ETurnPhases.DeclareDefense:
            pvp_push_blocker_options(session, state)
    log_req(f'    PvP optional trigger declined: {ag[:8]}')
    return True


def _pvp_run_phase_start(session, state, phase):
    """Push a phase transition to BOTH players in ONE packet each, with the
    GreenLight to the turn player FIRST.

    Order matters: the client's OnTurnPhaseUpdated fires a spurious
    RequestPrioritySync + state-stack churn (Killed/Popping states) when a
    phase event names it priority player while it does NOT yet hold the
    greenlight (HasPriority false).  Sending the greenlight BEFORE the phase
    in the same packet means the client already has priority when it processes
    the phase, so no sync is requested and the correct BattleState is pushed."""
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return
    turn_uid = state['turn_pid']
    defender_pid = pids[1] if turn_uid == pids[0] else pids[0]
    turn_uid_p = _ge.UID.make(244, turn_uid)
    champ_map = state.get('champ_map', {})
    if phase == _ge.ETurnPhases.StartTurn:
        turn_h = _resolve('player_handlers').get(turn_uid)
        if turn_h:
            try:
                opp_uid_st = _ge.UID.make(244, pids[1] if turn_uid == pids[0] else pids[0])
                warm = _ge.Game(int(session.session_id), turn_uid_p, opp_uid_st)
                warm.player_health = int(state.get(f'hp_{turn_uid}', 20))
                warm.ai_health = int(state.get(f'hp_{(pids[1] if turn_uid == pids[0] else pids[0])}', 20))
                _pvp_ready_step(session, state, turn_uid, warm, turn_uid_p, handler=turn_h)
                st_view = _resolve('_pvp_fra_view')(state, turn_uid, pids[1] if turn_uid == pids[0] else pids[0])
                st_view['phase'] = phase
                st_view['_last_turn_phase_event'] = state.get('_last_turn_phase_event')
                st_view['_rules_port_attached'] = True
                from rules_port.context import EffectContext
                from rules_port.tunneling import advance, queue_surfaces
                tunnel_context = EffectContext.from_rules_port(warm, session, _resolve('_db'), turn_h, turn_uid_p, opp_uid_st, st_view, '', ability=None)
                tunnel_changes = advance(tunnel_context, turn_uid)
                _resolve('_pvp_dispatch_triggers')(turn_h, warm, session, st_view, turn_uid_p, opp_uid_st, 'TurnPhaseEvent', None, turn_uid, phase=int(phase))
                state['_last_turn_phase_event'] = st_view.get('_last_turn_phase_event')
                _resolve('_pvp_dispatch_triggers')(turn_h, warm, session, st_view, turn_uid_p, opp_uid_st, 'TurnStartedEvent', None, turn_uid)
                tunnel_surfaces = queue_surfaces(tunnel_context, turn_uid)
                _resolve('_pvp_sync_view_to_state')(state, st_view, turn_uid, pids[1] if turn_uid == pids[0] else pids[0])
                state['_next_instance_id'] = st_view.get('_next_instance_id', state.get('_next_instance_id', 1))
                if st_view.get('player_health') is not None:
                    state[f'hp_{turn_uid}'] = int(st_view['player_health'])
                if st_view.get('ai_health') is not None:
                    state[f'hp_{(pids[1] if turn_uid == pids[0] else pids[0])}'] = int(st_view['ai_health'])
                for cpid in pids:
                    c_uid = _ge.UID.make(244, cpid)
                    cu64 = int(champ_map.get(str(cpid), 0))
                    if cu64:
                        c_scid = _ge.SessionCardId(_ge.UID(cu64))
                        c_basic = db_card_basic(session.session_id, cu64, conn=_resolve('_db'))
                        if c_basic:
                            turn_h._card_full_data(warm, c_scid, c_basic[0])
                            cdef = warm.card_defs.get(c_scid)
                            if cdef is not None:
                                cdef.counters = dict((state.get('champion_counters') or {}).get(str(cu64), {}) or {})
                            warm.push_card_updated(c_scid, c_uid, _ge.ECardCollections.Champions, _ge.ECardTypes.Champion, template_id=c_basic[0], defense=int(state.get(f'hp_{cpid}', 20)))
                _resolve('pvp_save_state')(session, state)
                if warm.events:
                    _resolve('_pvp_send_same_events')(session, warm, turn_uid_p, opp_uid_st)
                log_req(f'    PvP StartTurn: TurnStartedEvent fired + tunneling +{len(tunnel_changes)} / surface queued {len(tunnel_surfaces)} + champions re-pushed for {turn_uid}')
            except Exception as e:
                import traceback
                log_req(f'    PvP StartTurn trigger error: {e}')
                traceback.print_exc()
        if _pvp_check_game_end(session, state):
            return
    prep_wz = []
    prep_cooldown_uids = []
    prep_champion_cooldowns = {}
    if phase == 8:
        from rules_port.resources import begin_turn_resources_for_player
        resource_refill = begin_turn_resources_for_player(state, turn_uid)
        from rules_port.lifecycle import clear_expired_temporary_attributes
        ready_changes = _pvp_ready_step(session, state, turn_uid, handler=_resolve('player_handlers').get(turn_uid))
        wz_rows = _resolve('db_warzone_troops_with_state')(session.session_id, turn_uid, conn=_resolve('_db'))
        troop_uids = set()
        for wzr in wz_rows:
            wz_uid = int(wzr[0])
            troop_uids.add(wz_uid)
            _resolve('db_update_card_state')(session.session_id, wz_uid, set_bits=_ge.ECardStates.StartedATurnOnYourSide, clear_bits=_ge.ECardStates.CameOutThisTurn | _ge.ECardStates.Attacking | _ge.ECardStates.HasAttacked | _ge.ECardStates.Blocking | _ge.ECardStates.HasBlocked, reset_damage=True)
            pstate = _resolve('db_card_state_value')(session.session_id, wz_uid)
            if not pstate:
                pstate = _ge.ECardStates.StartedATurnOnYourSide
            ct_str = _resolve('db_game_card_type')(wzr[1])
            wz_ct = _ge.card_type_from_db(ct_str) if ct_str else _ECardTypes.Troop
            prep_wz.append((_ge.SessionCardId(_ge.UID(wz_uid)), wzr[1], wz_ct, pstate))
        for r_uid, r_tpl, _r_owner, _r_prev, r_state in ready_changes or ():
            if int(r_uid) in troop_uids:
                continue
            ct_str = _resolve('db_game_card_type')(r_tpl)
            prep_wz.append((_ge.SessionCardId(_ge.UID(int(r_uid))), r_tpl, _ge.card_type_from_db(ct_str) if ct_str else _ECardTypes.Troop, int(r_state)))
        _resolve('_db').commit()
        from pvp_db import db_decrement_card_cooldowns_for_owner
        prep_cooldown_uids = db_decrement_card_cooldowns_for_owner(session.session_id, turn_uid, conn=_resolve('_db'))
        from rules_port.cooldowns import decrement_champion_cooldowns
        prep_champion_cooldowns = decrement_champion_cooldowns(state, turn_uid)
        clear_expired_temporary_attributes(_resolve('_db'), session.session_id, turn_uid, 'prep', clear_stat_buffs=True, battle_state=state, handler=_resolve('player_handlers').get(turn_uid))
        log_req(f'    PvP Prep: refilled {turn_uid} to {resource_refill.new_value}, readied {len(prep_wz)} warzone troop(s)')
    pvp_draw_cache = {}
    if phase == 9:
        _pvp_defer_trigger_event(state, event_type='TurnPhaseEvent', source_card_id=None, source_player_id=turn_uid, phase=int(phase), data={'phase': int(phase)})
        dr = _resolve('_pvp_run_draw')(session, state)
        if dr is not None:
            for _pid in pids:
                pvp_draw_cache[_pid] = dr
            if _pvp_check_game_end(session, state):
                return
    if phase != _ge.ETurnPhases.StartTurn:
        phase_h = _resolve('player_handlers').get(turn_uid)
        if phase_h:
            phase_game = _ge.Game(int(session.session_id), _ge.UID.make(244, turn_uid), _ge.UID.make(244, defender_pid))
            _pvp_apply_visibility(phase_game, state)
            phase_view = _resolve('_pvp_fra_view')(state, turn_uid, defender_pid)
            phase_view['phase'] = phase
            phase_view['_last_turn_phase_event'] = state.get('_last_turn_phase_event')
            if phase == _ge.ETurnPhases.FirstMainPhase:
                deferred = list(state.pop('_pvp_deferred_trigger_events', ()) or ())
                for deferred_event in deferred:
                    event_phase = deferred_event.get('phase')
                    phase_view['phase'] = int(event_phase) if event_phase is not None else phase
                    _resolve('_pvp_dispatch_triggers')(phase_h, phase_game, session, phase_view, _ge.UID.make(244, turn_uid), _ge.UID.make(244, defender_pid), deferred_event.get('event_type'), deferred_event.get('source_card_id'), deferred_event.get('source_player_id'), target_card_id=deferred_event.get('target_card_id'), target_player_id=deferred_event.get('target_player_id'), **dict(deferred_event.get('data') or {}))
                if deferred:
                    phase_view['phase'] = phase
                    _resolve('_pvp_sync_view_to_state')(state, phase_view, turn_uid, defender_pid)
                    for key, value in phase_view.items():
                        if key.startswith('pending_') or key in ('resolution_paused', 'rules_port_resume_effect_order'):
                            state[key] = value
                    if phase_view.get('player_health') is not None:
                        state[f'hp_{turn_uid}'] = int(phase_view['player_health'])
                    if phase_view.get('ai_health') is not None:
                        state[f'hp_{defender_pid}'] = int(phase_view['ai_health'])
                    state['_next_instance_id'] = phase_view.get('_next_instance_id', state.get('_next_instance_id', 1))
                    state['stack_passed'] = []
            if phase != _ge.ETurnPhases.Draw:
                phase_view['phase'] = phase
                _resolve('_pvp_dispatch_triggers')(phase_h, phase_game, session, phase_view, _ge.UID.make(244, turn_uid), _ge.UID.make(244, defender_pid), 'TurnPhaseEvent', None, turn_uid, phase=phase)
            if phase == _ge.ETurnPhases.EndPhase and (not state.get('turn_end_trigger_fired')):
                end_champion_uid = int(champ_map.get(str(turn_uid), 0) or 0)
                _resolve('_pvp_dispatch_triggers')(phase_h, phase_game, session, phase_view, _ge.UID.make(244, turn_uid), _ge.UID.make(244, defender_pid), 'TurnEndedEvent', end_champion_uid or None, turn_uid, force_ignores_chain=bool(state.get('corinth_mode')))
                state['turn_end_trigger_fired'] = True
            state['_last_turn_phase_event'] = phase_view.get('_last_turn_phase_event')
            _resolve('pvp_save_state')(session, state)
            if phase_game.events:
                _resolve('_pvp_send_same_events')(session, phase_game, _ge.UID.make(244, turn_uid), _ge.UID.make(244, defender_pid))
    chain_from_phase_start = _pvp_chain_active(session, state)
    phase_priority_pid = defender_pid if phase == _ge.ETurnPhases.DeclareDefense else turn_uid
    if chain_from_phase_start:
        native_port = getattr(session, '_rules_port_session', None)
        native_action = native_port.action_stack.peek() if native_port is not None else None
        native_priority = getattr(native_action, 'priority_player_id', None)
        if native_priority is not None:
            try:
                raw_priority = int(getattr(native_priority, 'uid64', native_priority))
                phase_priority_pid = raw_priority >> 8 if raw_priority & 255 == 244 else raw_priority
            except (TypeError, ValueError):
                pass
    try:
        from rules_port.kernel import TurnPhasePlayers
        from rules_port.pvp_session import PvpAuthoritativeSession
        native_window = PvpAuthoritativeSession.priority_players_for_phase(state, int(phase), int(turn_uid), int(defender_pid))
        emits_priority = chain_from_phase_start or native_window is not TurnPhasePlayers.NONE
    except (ImportError, TypeError, ValueError):
        emits_priority = phase not in (_ge.ETurnPhases.StartGame, _ge.ETurnPhases.StartTurn, _ge.ETurnPhases.Ready, _ge.ETurnPhases.Prep, _ge.ETurnPhases.Draw)
    if phase not in (3, 4):
        if emits_priority:
            state['priority_pid'] = phase_priority_pid
        else:
            state.pop('priority_pid', None)
        _resolve('pvp_save_state')(session, state)
    for pid in pids:
        h = _resolve('player_handlers').get(pid)
        if not h:
            continue
        is_me = pid == turn_uid
        pl_t = _ge.UID.make(244, pid)
        opp_t = _ge.UID.make(244, pids[1] if pid == pids[0] else pids[0])
        g = _ge.Game(int(session.session_id), pl_t, opp_t)
        _pvp_apply_visibility(g, state)
        g.player_health = int(state.get(f'hp_{pid}', 20))
        g.ai_health = int(state.get(f'hp_{(pids[1] if pid == pids[0] else pids[0])}', 20))
        g.player_resources = int(state.get(f'res_{pid}', 0))
        g.player_total_resources = int(state.get(f'res_total_{pid}', 0))
        g.ai_resources = int(state.get(f'res_{(pids[1] if pid == pids[0] else pids[0])}', 0))
        g.ai_total_resources = int(state.get(f'res_total_{(pids[1] if pid == pids[0] else pids[0])}', 0))
        g.player_charges = int(state.get(f'chg_{pid}', 0))
        g.ai_charges = int(state.get(f'chg_{(pids[1] if pid == pids[0] else pids[0])}', 0))
        _resolve('_pvp_populate_game_state')(g, state, pid, pids[1] if pid == pids[0] else pids[0])
        if phase == 8 and is_me:
            g.player_resources = int(state.get(f'res_{turn_uid}', 0))
            g.player_total_resources = int(state.get(f'res_total_{turn_uid}', 0))
        if phase == 8 and prep_wz:
            wz_handler = _resolve('player_handlers').get(turn_uid)
            for wz_scid, wz_tpl, wz_ct, pstate in prep_wz:
                if wz_handler:
                    wz_handler._card_full_data(g, wz_scid, wz_tpl)
                g.push_card_updated(wz_scid, turn_uid_p, _ECardCollections.Warzone, wz_ct, template_id=wz_tpl, state=pstate)
        if phase == 8 and prep_cooldown_uids:
            from pvp_db import db_card_source_info
            from rules_port.runtime_helpers import card_collection_for_location
            cooldown_handler = _resolve('player_handlers').get(turn_uid)
            for cooldown_uid in prep_cooldown_uids:
                row = db_card_source_info(session.session_id, cooldown_uid, conn=_resolve('_db'))
                if not row:
                    continue
                template_guid, card_type, location, card_owner = row
                scid = _ge.SessionCardId(_ge.UID(int(cooldown_uid)))
                if cooldown_handler:
                    try:
                        _tpl, card_type, _name, cost, attack, defense, gems = cooldown_handler._card_full_data(g, scid, template_guid)
                    except Exception:
                        continue
                else:
                    cost = attack = defense = gems = 0
                g.push_card_updated(scid, _ge.UID.make(244, int(card_owner)), card_collection_for_location(location), card_type, template_id=template_guid, state=int(_resolve('db_card_state_value')(session.session_id, cooldown_uid, conn=_resolve('_db')) or 0), cost=cost, attack=attack, defense=defense, gems=gems)
        if phase == 8 and prep_champion_cooldowns:
            changed_champions = {int(str(key).split(':', 1)[0]) for key in prep_champion_cooldowns if ':' in str(key)}
            for champion_uid in changed_champions:
                scid = _ge.SessionCardId(_ge.UID(champion_uid))
                cdef = g.card_defs.get(scid)
                if cdef is None:
                    continue
                prefix = f'{champion_uid}:'
                cooldowns = {}
                for key, value in (state.get('champion_ability_cooldowns') or {}).items():
                    if str(key).startswith(prefix):
                        try:
                            remaining = int(value or 0)
                        except (TypeError, ValueError):
                            continue
                        if remaining > 0:
                            cooldowns[str(key).split(':', 1)[1]] = remaining
                cdef.cooldown_counts = {_ge.ResourceId.from_str(guid): value for guid, value in cooldowns.items()}
                g.push_card_updated(scid, _ge.UID.make(244, int(turn_uid)), _ECardCollections.None_, _ECardTypes.Champion, cooldown_counts=cdef.cooldown_counts)
        my_champ_uid = int(champ_map.get(str(pid), 0))
        my_champ = _ge.SessionCardId(_ge.UID(my_champ_uid)) if my_champ_uid else None
        priority_pid = phase_priority_pid
        prio_uid = _ge.UID.make(244, priority_pid)
        if emits_priority:
            g.push_green_light(prio_uid, _ge.EPriorityContext.ResolveTopOfChain if chain_from_phase_start else _ge.EPriorityContext.Normal)
        priority_elapsed_seconds = _resolve('priority_elapsed_ticks')(state, priority_pid) // 10000000 if phase not in (3, 4) else 0
        _pvp_push_turn_phase_with_elapsed(g, phase, turn_uid_p, prio_uid, priority_elapsed_seconds)
        _resolve('pvp_push_warzone_updates')(session, state, game=g)
        if phase == 8:
            g.push_player_updated(pl_t, champ_id=my_champ)
            opp_champ_uid = int(champ_map.get(str(pids[1] if pid == pids[0] else pids[0]), 0))
            g.push_player_updated(_ge.UID.make(244, pids[1] if pid == pids[0] else pids[0]), champ_id=_ge.SessionCardId(_ge.UID(opp_champ_uid)) if opp_champ_uid else None)
        elif phase == 9:
            dr = pvp_draw_cache.get(pid)
            if dr is not None:
                if is_me:
                    for ev in dr[0]:
                        g._push(ev)
                else:
                    for ev in dr[1]:
                        g._push(ev)
        if g.events:
            pkt = g.make_network_packet(pl_t)
            dw = _resolve('encode_datawrapper')(0, 3055, _resolve('compress_gzip')(_resolve('encode_sync_event')(pkt)), 1, _resolve('client_session_guid')(h))
            h.scnt += 1
            h.send({'issuer': f'0.0.0.0.ServiceGameSession.246.{session.session_id}.{h.scnt}', 'target': 'ServiceGameSession', 'instance': str(session.server_id), 'reqid': 0, 'c': 0, 'conh': 0, 'sid': h.sid}, dw)
            log_req(f'    PvP phase {phase} start pushed to pid {pid}')
    if phase == 8:
        _resolve('pvp_save_state')(session, state)
    _resolve('_pvp_log_stack')(state, f'phase-{phase}')
    if phase not in (3, 4):
        if phase == _ge.ETurnPhases.DeclareDefense:
            state['priority_pid'] = pids[1] if state.get('turn_pid') == pids[0] else pids[0]
        else:
            state['priority_pid'] = state.get('turn_pid')
        _resolve('pvp_save_state')(session, state)
    _resolve('pvp_push_current_phase_options')(session, state)


@_pvp_locked
def handle_ready_to_continue_game(handler, target, instance, reqid, comp, session_id, conh, inner_obj, inner_bytes, log_req=log_req, **_kw):
    """Handle DT 22025 — ReadyToContinueGame after join."""
    log_req(f'>>> ReadyToContinueGame (dt=22025)')
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


def _pvp_advance_from_damage_step(session, state, just_resolved):
    """After resolving a damage step (AssignFirstStrikeDamage=16 or
    AssignDamage=18), advance to the next phase and push it to both players.
    16 -> AssignDamage (18); 18 -> SecondMainPhase (19).  Resolves combat so
    the opponent doesn't get stuck in a dead BattleStateAssignDamage."""
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return
    turn_pid = state.get('turn_pid')
    if just_resolved == _ge.ETurnPhases.AssignFirstStrikeDamage:
        new_phase = _ge.ETurnPhases.AssignDamage
    elif just_resolved == _ge.ETurnPhases.AssignDamage:
        new_phase = _ge.ETurnPhases.SecondMainPhase
    else:
        return
    _resolve('port_enter_phase')(state, new_phase)
    state['priority_pid'] = turn_pid
    _resolve('pvp_save_state')(session, state)
    log_req(f'    PvP post-damage: phase {new_phase} to both (priority to turn player {turn_pid})')
    _resolve('_pvp_run_phase_start')(session, state, new_phase)
    _pvp_check_game_end(session, state)


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


def pvp_advance_to_declare_defense(session, state):
    """Advance the PvP phase from DeclareAttack (12) through
    DeclareAttackPriorityWindow (13) to DeclareDefense (14), handing priority
    to the DEFENDER at 14 (mirrors PvE: after attackers are declared the
    defender is the one who must act/block).  Returns True if it reached
    DeclareDefense."""
    from rules_port import lifecycle as _be
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return False
    turn_pid = state.get('turn_pid')
    opp_pid = pids[0] if pids[1] == turn_pid else pids[1]
    phase_list = _be.COMBAT_TURN_PHASES
    try:
        cur = phase_list.index(int(state.get('phase', 12)))
    except ValueError:
        cur = phase_list.index(12)
    while True:
        cur += 1
        if cur >= len(phase_list):
            return False
        new_phase = phase_list[cur]
        _resolve('port_enter_phase')(state, new_phase)
        _resolve('pvp_save_state')(session, state)
        log_req(f'    PvP post-attack: phase {new_phase} to both')
        _resolve('_pvp_run_phase_start')(session, state, new_phase)
        if new_phase == _ge.ETurnPhases.DeclareDefense:
            state['priority_pid'] = opp_pid
            _resolve('pvp_save_state')(session, state)
            blockable = _pvp_defender_blockable_count(session, state)
            if blockable <= 0:
                log_req(f'    PvP post-attack: defender {opp_pid} has {blockable} blocker(s) — auto-passing DeclareDefense')
                try:
                    pvp_push_empty_blockers(session, state)
                except Exception as e:
                    log_req(f'    PvP empty-blockers error: {e}')
                continue
            log_req(f'    PvP post-attack: at DeclareDefense, priority to defender {opp_pid} ({blockable} blocker(s) available)')
            return True
        if new_phase == _ge.ETurnPhases.DeclareDefensePriorityWindow:
            state['priority_pid'] = turn_pid
            _resolve('pvp_save_state')(session, state)
            log_req(f'    PvP post-attack: at DeclareDefensePriorityWindow, priority to turn player {turn_pid} (defender had no blockers)')
            return True
    return False


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


def _pvp_declare_attackers(handler, session, inner_bytes, my_pid, declarations=None):
    """Record the turn player's declared attackers and push the combat
    listing to both players."""
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return False
    opp_pid = pids[0] if pids[1] == my_pid else pids[1]
    state = _resolve('pvp_load_state')(session) or {}
    if state.get('turn_pid') != my_pid:
        return False
    attacker_uids = []
    if declarations is not None:
        for _defender, declared in declarations:
            for attacker in declared or ():
                try:
                    attacker_uids.append(int(attacker))
                except (TypeError, ValueError):
                    continue
    elif isinstance(inner_bytes, bytes):
        for m_du in re.finditer(b'm_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});', inner_bytes):
            try:
                import struct as _st
                uid64 = _st.unpack('<Q', bytes.fromhex(m_du.group(1).decode()))[0]
                if uid64 & 255 == 1:
                    attacker_uids.append(int(uid64))
            except Exception:
                continue
    if declarations is None:
        wz_rows = [(uid, card_state, attrs) for uid, card_state, _card_type, attrs in _resolve('db_warzone_attack_option_rows')(session.session_id, my_pid, conn=_resolve('_db'))]
        from rules_port.static_rules import effective_attributes
        wz = set()
        for uid, cstate, attrs in wz_rows:
            cstate = int(cstate or 0)
            attrs = int(attrs or 0) | int(effective_attributes(_resolve('_db'), session.session_id, state, int(uid)) or 0)
            if cstate & (_ge.ECardStates.Tapped | _ge.ECardStates.Attacking) or attrs & (_ge.ECardAttributes.CantAttack | _ge.ECardAttributes.Defensive) or (not (cstate & _ge.ECardStates.StartedATurnOnYourSide or attrs & _ge.ECardAttributes.Speed)):
                continue
            wz.add(int(uid))
        attacker_uids = [u for u in attacker_uids if u in wz]
    champ_map = state.get('champ_map') or {}
    my_champ = int(champ_map.get(str(my_pid), 0))
    opp_champ = int(champ_map.get(str(opp_pid), 0))
    existing = {int(k): int(v) for k, v in (state.get('attackers') or {}).items()}
    merged = dict(existing)
    for u in attacker_uids:
        merged[u] = opp_champ
    attackers = list(merged.keys())
    state['attackers'] = {str(u): str(v) for u, v in merged.items()}
    _resolve('pvp_save_state')(session, state)
    my_uid = _ge.UID.make(244, my_pid)
    opp_uid = _ge.UID.make(244, opp_pid)
    new_attackers = [u for u in attacker_uids if u not in existing]
    from pvp_db import db_card_set_attacking_state
    g = _ge.Game(int(session.session_id), my_uid, opp_uid)
    g.player_health = int(state.get(f'hp_{my_pid}', 20))
    g.ai_health = int(state.get(f'hp_{opp_pid}', 20))
    combats = []
    for i, u in enumerate(new_attackers):
        cid = _ge.CombatId(my_uid, i + 1)
        scid = _ge.SessionCardId(_ge.UID(u))
        g.push_attack_declared(cid, my_uid, _ge.SessionCardId(_ge.UID(opp_champ)) if opp_champ else _ge.SessionCardId(opp_uid), scid)
        trow = _resolve('db_card_template_attrs_joined')(session.session_id, int(u))
        tpl_guid = trow[0] if trow else None
        attrs = (trow[1] if trow and trow[1] else 0) | (trow[2] if trow and trow[2] else 0)
        cstate = _ge.ECardStates.Attacking | _ge.ECardStates.HasAttacked
        if not attrs & _ge.ECardAttributes.Steadfast:
            cstate |= _ge.ECardStates.Tapped
        db_card_set_attacking_state(session.session_id, int(u), cstate)
        pushed_state = _resolve('db_card_state_value')(session.session_id, int(u))
        if not pushed_state:
            pushed_state = cstate
        handler._card_full_data(g, scid, tpl_guid)
        g.push_card_updated(scid, my_uid, _ge.ECardCollections.Warzone, _ge.ECardTypes.Troop, template_id=tpl_guid, state=pushed_state)
        cs = _ge.CombatSessionEventArgs()
        cs.player_id = my_uid
        cs.id = cid
        cs.attacker = scid
        cs.blockers = []
        combats.append(cs)
        view = _resolve('_pvp_fra_view')(state, my_pid, opp_pid)
        _resolve('_pvp_dispatch_triggers')(handler, g, session, view, my_uid, opp_uid, 'CardAttackedEvent', int(u), my_pid)
        _resolve('_pvp_dispatch_triggers')(handler, g, session, view, my_uid, opp_uid, 'CardAttackedOrBlockedEvent', int(u), my_pid)
        from rules_port.context import EffectContext
        from rules_port.combat_effects import apply_rage
        apply_rage(EffectContext.from_rules_port(g, session, _resolve('_db'), handler, my_uid, opp_uid, view, '', ability=None), int(u))
        if view.get('player_health') is not None:
            state[f'hp_{my_pid}'] = int(view['player_health'])
        if view.get('ai_health') is not None:
            state[f'hp_{opp_pid}'] = int(view['ai_health'])
        _resolve('_pvp_sync_view_to_state')(state, view, my_pid, opp_pid)
        _resolve('pvp_save_state')(session, state)
    if attackers:
        from rules_port.tac import _tac_attr_hash
        _resolve('_pvp_dispatch_triggers')(handler, g, session, state, my_uid, opp_uid, 'CardsAttackedEvent', my_champ or int(my_uid.uid64), my_pid, event_tac={_tac_attr_hash('NumAttackers'): len(attackers)})
    _resolve('_db').commit()
    if combats:
        g.push_combat_listing(my_uid, combats)
    _resolve('_pvp_send_same_events')(session, g, my_uid, opp_uid)
    log_req(f'    PvP attack: pid {my_pid} declared {len(attackers)} attacker(s)')
    if not attackers:
        pvp_skip_to_second_main(session, state)
        return True
    if declarations is None:
        _resolve('pvp_advance_to_declare_defense')(session, state)
    return True


def _pvp_declare_blockers(handler, session, inner_bytes, my_pid, declarations=None):
    """Record the defender's declared blockers and push BlockersAssigned to
    both players."""
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return False
    opp_pid = pids[0] if pids[1] == my_pid else pids[1]
    state = _resolve('pvp_load_state')(session) or {}
    attackers = {int(k): int(v) for k, v in (state.get('attackers') or {}).items()}
    if not attackers:
        return False
    my_wz = set((r[0] for r in _resolve('db_card_uids_in_zone')(session.session_id, my_pid, 'warzone', conn=_resolve('_db'))))
    all_uids = []
    blockers_map = {}
    if declarations is not None:
        for attacker, blockers in declarations:
            try:
                attacker_uid = int(getattr(attacker, 'uid64', attacker))
            except (TypeError, ValueError):
                continue
            parsed = []
            for blocker in blockers or ():
                try:
                    parsed.append(int(getattr(blocker, 'uid64', blocker)))
                except (TypeError, ValueError):
                    continue
            blockers_map[attacker_uid] = parsed
    elif isinstance(inner_bytes, bytes):
        for m_du in re.finditer(b'm_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});', inner_bytes):
            try:
                import struct as _st
                uid64 = _st.unpack('<Q', bytes.fromhex(m_du.group(1).decode()))[0]
                if uid64 & 255 == 1:
                    all_uids.append(int(uid64))
            except Exception:
                continue
    if declarations is None:
        cur = None
        for u in all_uids:
            if u in attackers:
                cur = u
                blockers_map.setdefault(cur, [])
            elif cur is not None and u in my_wz:
                from rules_port.combat_rules import can_block
                if can_block(_resolve('_db'), session.session_id, _resolve('_pvp_fra_view')(state, opp_pid, my_pid), cur, u):
                    blockers_map[cur].append(u)
    state['blockers'] = {str(k): [str(b) for b in v] for k, v in blockers_map.items()}
    _resolve('db_bulk_blocker_state')(session.session_id, [int(b) for bs in blockers_map.values() for b in bs])
    _resolve('pvp_save_state')(session, state)
    champ_map = state.get('champ_map') or {}
    opp_champ = int(champ_map.get(str(opp_pid), 0))
    opp_uid = _ge.UID.make(244, opp_pid)
    _ge.UID.make(244, my_pid)
    for pid in pids:
        h = _resolve('player_handlers').get(pid)
        if not h:
            continue
        pl_uid = _ge.UID.make(244, pid)
        g = _ge.Game(int(session.session_id), pl_uid, _ge.UID.make(244, pids[1] if pid == pids[0] else pids[0]))
        player_champ = _ge.SessionCardId(_ge.UID(opp_champ)) if opp_champ else _ge.SessionCardId(opp_uid)
        combats = []
        for u in attackers:
            cid = _ge.CombatId(opp_uid, u & 65535)
            blockers = [_ge.SessionCardId(_ge.UID(int(b))) for b in blockers_map.get(u, [])]
            g.push_blockers_assigned(cid, _ge.SessionCardId(_ge.UID(u)), player_champ, blockers)
            cs = _ge.CombatSessionEventArgs()
            cs.player_id = opp_uid
            cs.id = cid
            cs.attacker = _ge.SessionCardId(_ge.UID(u))
            cs.blockers = blockers
            combats.append(cs)
        if combats:
            g.push_combat_listing(opp_uid, combats)
        _resolve('_send_pvp_packet')(h, session, g, pl_uid, 'defense')
    log_req(f'    PvP defense: pid {my_pid} declared {sum((len(v) for v in blockers_map.values()))} blocker(s)')
    if declarations is None:
        _pvp_advance_past_declare_defense(session, state)
    return True


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


def _pvp_offer_trigger_response(session, state, caster_pid):
    """Offer the opponent priority for a newly-created trigger chain."""
    _pvp_offer_opponent_response(session, state, caster_pid)
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return
    opp_pid = pids[0] if pids[1] == caster_pid else pids[1]
    caster_h = _resolve('player_handlers').get(caster_pid)
    if caster_h:
        caster_uid = _ge.UID.make(244, caster_pid)
        opp_uid = _ge.UID.make(244, opp_pid)
        g = _ge.Game(int(session.session_id), caster_uid, opp_uid)
        g.push_green_light(opp_uid, _ge.EPriorityContext.ResolveTopOfChain)
        _resolve('_send_pvp_packet')(caster_h, session, g, caster_uid, 'trigger-on-stack-caster-lost')
    _resolve('_pvp_auto_pass_chain_priority')(session, state, opp_pid)


def _pvp_play_spell(handler, session, played_card_uid, my_pid, inner_bytes, typed_payload=None, native_port=None):
    """Cast a BasicAction/QuickAction spell in PvP: hand -> CastSpells, push
    the spell onto the chain, then resolve its BOM when the chain resolves and
    send CastSpells -> Discard.  A player may cast a QuickAction any time they
    hold priority and can pay the cost.  Pushes events to BOTH players."""
    from gamedata import PlayPlan
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return False
    opp_pid = pids[0] if pids[1] == my_pid else pids[1]
    crow = _resolve('db_card_play_info')(session.session_id, played_card_uid, conn=_resolve('_db'))
    if not crow:
        return False
    tpl_guid, card_type, card_name, _current_grant, _max_grant, ab_json = crow
    try:
        play_plan = PlayPlan.from_card(_RECORD_STORE, tpl_guid, source_uid=int(played_card_uid), owner_id=my_pid)
    except KeyError as exc:
        log_req(f'    PvP REJECTED spell {card_name}: {exc}')
        return True
    scid = _ge.SessionCardId(_ge.UID(int(played_card_uid)))
    my_uid = _ge.UID.make(244, my_pid)
    opp_uid = _ge.UID.make(244, opp_pid)
    state = _resolve('pvp_load_state')(session) or {}
    source_location = _pvp_card_source_location(session, state, my_pid, int(played_card_uid))
    if source_location is None:
        log_req(f'    PvP REJECTED spell {card_name}: card is not in a playable zone')
        return True
    prior_card_zone = _resolve('db_card_owner_zone_state')(session.session_id, int(played_card_uid), conn=_resolve('_db'))
    targets = _pvp_typed_card_targets(typed_payload, played_card_uid)
    if typed_payload is None:
        try:
            targets = handler._extract_transaction_targets(inner_bytes, int(played_card_uid))
        except Exception:
            targets = []
    champ_map = state.get('champ_map') or {}
    champ_targets = []
    for cpid in state.get('pids') or pids:
        cuid = int(champ_map.get(str(cpid), 0))
        if cuid:
            champ_targets.append((cuid, int(cpid), 'Champion', int(state.get(f'hp_{cpid}', 20))))
    cost_selection = _pvp_select_card_play_costs(handler, session, state, play_plan, int(played_card_uid), my_pid, targets, champ_targets)
    if cost_selection is None:
        log_req(f'    PvP REJECTED spell {card_name}: incomplete/illegal card cost target')
        return True
    cost_selections, cost_uids = cost_selection
    _trow = _resolve('db_template_by_guid')(tpl_guid)
    cost = _trow[3] if _trow else 0
    try:
        from rules_port.static_rules import effective_cost as _ec
        cost = _ec(_resolve('_db'), session.session_id, _resolve('_pvp_fra_view')(state, my_pid, opp_pid), int(played_card_uid))
    except Exception:
        pass
    x_cost = max((int(item.get('x_cost', 0) or 0) for item in (typed_payload or {}).get('ability_data', ()) if isinstance(item, dict)), default=0)
    if typed_payload is None:
        try:
            x_cost = max(0, int(handler._extract_int32_field(inner_bytes, 'm_ResourceXCost') or 0))
        except Exception:
            x_cost = 0
    if play_plan.cost.variable:
        if x_cost < play_plan.cost.variable_minimum:
            log_req(f'    PvP REJECTED spell {card_name}: X={x_cost} below minimum {play_plan.cost.variable_minimum}')
            return True
    elif x_cost:
        log_req(f'    PvP REJECTED spell {card_name}: non-variable card has X={x_cost}')
        return True
    available = int(state.get(f'res_{my_pid}', 0))
    x_payment = x_cost * play_plan.cost.variable_multiplier
    if native_port is None and cost + x_payment > available:
        log_req(f'    PvP REJECTED spell {card_name}: cost {cost} > resources {available}')
        _resolve('pvp_push_main_phase_options')(session, state)
        return True
    ability_data = (typed_payload or {}).get('ability_data') or ()
    activations, _cost_target_map = play_plan.activation_bundle(targets, x_cost=x_cost, ability_data=ability_data)
    ability_cost_selection = handler._ability_card_play_cost_selections(session, play_plan, int(played_card_uid), activations, owner_id=my_pid, champions=champ_targets, battle_state=state)
    if ability_cost_selection is None:
        log_req(f'    PvP REJECTED spell {card_name}: invalid ability cost target')
        return True
    ability_cost_selections, ability_cost_uids = ability_cost_selection
    cost_selections.extend(ability_cost_selections)
    cost_uids.update(ability_cost_uids)
    selected_cost_map = {int(spec.get('index', index)): tuple(selected) for index, (spec, selected) in enumerate(cost_selections) if selected and (not spec.get('ability_guid'))}
    plan_errors = play_plan.validate(variable_cost=x_cost, activations=activations, cost_target_map=selected_cost_map) if native_port is None else ()
    if plan_errors:
        log_req(f"    PvP REJECTED spell {card_name}: activation validation: {'; '.join(plan_errors)}")
        _resolve('pvp_push_main_phase_options')(session, state)
        return True
    from rules_port.card_transactions import apply_card_play_for_player
    card_transition = apply_card_play_for_player(_resolve('_db'), session.session_id, state, int(played_card_uid), my_pid, cost + x_payment, expected_location=source_location)
    if card_transition is None:
        log_req(f'    PvP REJECTED spell {card_name}: card left its playable zone')
        return True
    from rules_port.cast_stats import record_card_cast
    record_card_cast(state, my_pid, resource=False, card_uid=int(played_card_uid), card_type=card_type, resource_cost=cost)
    if x_cost:
        log_req(f"    PvP spell {card_name}: X cost {x_cost} paid (resources left {state.get(f'res_{my_pid}')})")
    view = _resolve('_pvp_fra_view')(state, my_pid, opp_pid)
    effect_targets = [uid for uid in targets if int(uid) not in cost_uids]
    target_uid = effect_targets[-1] if effect_targets else None
    view['player_spell_target'] = target_uid
    view['resolving_owner_id'] = my_pid
    view['resolving_source_uid'] = int(played_card_uid)
    g = _ge.Game(int(session.session_id), my_uid, opp_uid)
    _resolve('_pvp_populate_game_state')(g, state, my_pid, opp_pid)
    _pvp_apply_card_play_costs(handler, g, session, state, my_uid, opp_uid, cost_selections, int(played_card_uid), scrounge=any((getattr(ability, 'is_scrounge', False) for ability in play_plan.cast_abilities)))
    _tpl, ct, _n, cost2, atk, def_, gems = handler._card_full_data(g, scid, tpl_guid)
    g.push_card_updated(scid, my_uid, _ge.ECardCollections.CastSpells, ct, template_id=tpl_guid, cost=cost2, attack=atk, defense=def_, gems=gems)
    g.push_spell_card_cast(scid, my_uid, free=False)
    from rules_port.card_transactions import automatic_instance_ability_guids
    ability_guids = automatic_instance_ability_guids(_resolve('_db'), session.session_id, int(played_card_uid), [ability.ability_guid for ability in play_plan.cast_abilities], in_play=False)
    played_from_hand = str(source_location).lower() == 'hand'
    inst_id = _resolve('port_queue_stack_item')(state, {'kind': 'spell', 'source_uid': int(played_card_uid), 'ability_guids': ability_guids, 'target_uid': target_uid, 'x_cost': x_cost, 'played_from_hand': played_from_hand, 'activations': {guid: activation.as_dict() for guid, activation in activations.items()}})
    _chain_tpl2 = _ge.PLAY_CARD_ABILITY_TEMPLATE_ID if not ability_guids else ability_guids[0]
    g.push_ability_on_chain(scid, _ge.ResourceId.from_str(_chain_tpl2), ability_instance_id=inst_id)
    state['stack'] = view.get('stack') or []
    state['stack_player_passed'] = False
    state['stack_ai_passed'] = False
    state['stack_passed'] = []
    from rules_port import chain_items as _chain_items
    trigger_host = _resolve('_PvpChainHost')(handler, session, state, my_pid, opp_pid)
    _chain_items.dispatch_card_zone_transition(trigger_host, session, g, state, my_uid, opp_uid, int(played_card_uid), my_pid, source_location, 'CastSpells', int(prior_card_zone[2] or 0) if prior_card_zone else 0)
    _chain_items.dispatch_card_cast(trigger_host, session, g, state, my_uid, opp_uid, int(played_card_uid), my_pid)
    _resolve('pvp_save_state')(session, state)
    g.player_health = int(state.get(f'hp_{my_pid}', 20))
    g.ai_health = int(state.get(f'hp_{opp_pid}', 20))
    g.player_resources = int(state.get(f'res_{my_pid}', 0))
    g.player_total_resources = int(state.get(f'res_total_{my_pid}', 0))
    g.ai_resources = int(state.get(f'res_{opp_pid}', 0))
    g.ai_total_resources = int(state.get(f'res_total_{opp_pid}', 0))
    ev_spent = _ge.PlayerCurrentResourcePoolChangedSessionEventArgs()
    ev_spent.player_id = my_uid
    ev_spent.operation = 2
    ev_spent.delta = cost + x_payment
    ev_spent.new_value = g.player_resources
    g._push(ev_spent)
    champ_map = state.get('champ_map') or {}
    for _tpid in pids:
        _tuid = _ge.UID.make(244, _tpid)
        cu = int(champ_map.get(str(_tpid), 0))
        champ_scid = _ge.SessionCardId(_ge.UID(cu)) if cu else None
        g.push_player_updated(_tuid, champ_id=champ_scid)
    _resolve('_pvp_send_same_events')(session, g, my_uid, opp_uid)
    if native_port is not None:
        native_port.queue_projected_chain({'kind': 'spell', 'source_uid': int(played_card_uid), 'ability_guids': ability_guids, 'target_uid': target_uid, 'instance_id': int(inst_id), 'x_cost': x_cost, 'played_from_hand': played_from_hand, 'activations': {guid: activation.as_dict() for guid, activation in activations.items()}}, my_pid, first_player_id=opp_uid)
        state['priority_pid'] = int(opp_pid)
        _resolve('pvp_save_state')(session, state)
        opp_h = _resolve('player_handlers').get(opp_pid)
        if opp_h:
            response = _ge.Game(int(session.session_id), opp_uid, my_uid)
            response.push_green_light(opp_uid, _ge.EPriorityContext.ResolveTopOfChain)
            _resolve('_send_pvp_packet')(opp_h, session, response, opp_uid, 'native-spell-chain-opp')
            _resolve('pvp_push_phase_options')(session, state, pid=opp_pid)
        caster_h = _resolve('player_handlers').get(my_pid)
        if caster_h:
            response = _ge.Game(int(session.session_id), my_uid, opp_uid)
            response.push_green_light(opp_uid, _ge.EPriorityContext.ResolveTopOfChain)
            _resolve('_send_pvp_packet')(caster_h, session, response, my_uid, 'native-spell-chain-caster')
        session._rules_port_mutation_emitted = True
        return True
    log_req(f"    PvP spell cast: {card_name} by pid {my_pid} (paid {cost}, target={(hex(target_uid) if target_uid else None)}, stack={len(state.get('stack') or [])})")
    _resolve('_pvp_log_stack')(state, f'spell-cast {card_name}')
    try:
        opp_h = _resolve('player_handlers').get(opp_pid)
        if opp_h and (not pvp_player_auto_passes(state, opp_pid)):
            gg = _ge.Game(int(session.session_id), opp_uid, my_uid)
            gg.push_green_light(opp_uid, _ge.EPriorityContext.ResolveTopOfChain)
            _resolve('_send_pvp_packet')(opp_h, session, gg, opp_uid, 'chain-priority-opp')
            try:
                _resolve('pvp_push_phase_options')(session, state, pid=opp_pid)
            except Exception:
                pass
        state['priority_pid'] = opp_pid
        _resolve('pvp_save_state')(session, state)
        _resolve('_pvp_log_stack')(state, f'spell-cast-{card_name}-opp-first')
        _resolve('_pvp_auto_pass_chain_priority')(session, state, opp_pid)
    except Exception as e:
        import traceback
        log_req(f'    PvP spell post-refresh error: {e}')
        traceback.print_exc()
    return True


def _pvp_play_troop(handler, session, played_card_uid, my_pid, inner_bytes, typed_payload=None, native_port=None):
    """Play a non-resource permanent in PvP: hand -> CastSpells -> Warzone,
    fire enters-play triggers, and push the events to BOTH players.  Returns
    True when handled."""
    from gamedata import PlayPlan
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return False
    opp_pid = pids[0] if pids[1] == my_pid else pids[1]
    crow = _resolve('db_card_play_info')(session.session_id, played_card_uid, conn=_resolve('_db'))
    if not crow:
        return False
    tpl_guid, card_type, card_name = (crow[0], crow[1], crow[2])
    card_abilities = crow[5]
    ctype_num = _ge.card_type_from_db(card_type)
    is_permanent = bool(ctype_num & (_ge.ECardTypes.Troop | _ge.ECardTypes.Artifact | _ge.ECardTypes.Constant))
    if not is_permanent:
        log_req(f'    PvP: {card_name} is an action — not yet resolved')
        return False
    scid = _ge.SessionCardId(_ge.UID(int(played_card_uid)))
    my_uid = _ge.UID.make(244, my_pid)
    opp_uid = _ge.UID.make(244, opp_pid)
    state = _resolve('pvp_load_state')(session) or {}
    source_location = _pvp_card_source_location(session, state, my_pid, int(played_card_uid))
    if source_location is None:
        log_req(f'    PvP REJECTED permanent {card_name}: card is not in a playable zone')
        return True
    prior_card_zone = _resolve('db_card_owner_zone_state')(session.session_id, int(played_card_uid), conn=_resolve('_db'))
    try:
        play_plan = PlayPlan.from_card(_RECORD_STORE, tpl_guid, source_uid=int(played_card_uid), owner_id=my_pid)
    except KeyError as exc:
        log_req(f'    PvP REJECTED permanent {card_name}: {exc}')
        return True
    targets = _pvp_typed_card_targets(typed_payload, played_card_uid)
    if typed_payload is None:
        try:
            targets = handler._extract_transaction_targets(inner_bytes, int(played_card_uid))
        except Exception:
            targets = []
    champ_map = state.get('champ_map') or {}
    champ_targets = []
    for cpid in state.get('pids') or pids:
        cuid = int(champ_map.get(str(cpid), 0))
        if cuid:
            champ_targets.append((cuid, int(cpid), 'Champion', int(state.get(f'hp_{cpid}', 20))))
    cost_selection = _pvp_select_card_play_costs(handler, session, state, play_plan, int(played_card_uid), my_pid, targets, champ_targets)
    if cost_selection is None:
        log_req(f'    PvP REJECTED spell {card_name}: incomplete/illegal card cost target')
        return True
    cost_selections, cost_uids = cost_selection
    ability_data = (typed_payload or {}).get('ability_data') or ()
    ability_data = (typed_payload or {}).get('ability_data') or ()
    activations, _cost_target_map = play_plan.activation_bundle(targets, ability_data=ability_data)
    ability_cost_selection = handler._ability_card_play_cost_selections(session, play_plan, int(played_card_uid), activations, owner_id=my_pid, champions=champ_targets, battle_state=state)
    if ability_cost_selection is None:
        log_req(f'    PvP REJECTED play {card_name}: invalid ability cost target')
        return True
    ability_cost_selections, ability_cost_uids = ability_cost_selection
    cost_selections.extend(ability_cost_selections)
    cost_uids.update(ability_cost_uids)
    _trow = _resolve('db_template_by_guid')(tpl_guid)
    cost = play_plan.cost.resource
    if not cost and _trow and (not play_plan.cost.variable):
        cost = _trow[3] or 0
    try:
        from rules_port.static_rules import effective_cost as _ec
        cost = _ec(_resolve('_db'), session.session_id, _resolve('_pvp_fra_view')(state, my_pid, opp_pid), int(played_card_uid))
    except Exception:
        pass
    available = int(state.get(f'res_{my_pid}', 0))
    if native_port is None and cost > available:
        log_req(f'    PvP REJECTED play {card_name}: cost {cost} > resources {available}')
        _resolve('pvp_push_main_phase_options')(session, state)
        return True
    selected_cost_map = {int(spec.get('index', index)): tuple(selected) for index, (spec, selected) in enumerate(cost_selections) if selected and (not spec.get('ability_guid'))}
    plan_errors = play_plan.validate(activations=activations, cost_target_map=selected_cost_map) if native_port is None else ()
    if plan_errors:
        log_req(f"    PvP REJECTED play {card_name}: activation validation: {'; '.join(plan_errors)}")
        _resolve('pvp_push_main_phase_options')(session, state)
        return True
    from rules_port.card_transactions import apply_card_play_for_player
    card_transition = apply_card_play_for_player(_resolve('_db'), session.session_id, state, int(played_card_uid), my_pid, cost, expected_location=source_location)
    if card_transition is None:
        log_req(f'    PvP REJECTED play {card_name}: card left its playable zone')
        return True
    from rules_port.cast_stats import record_card_cast
    record_card_cast(state, my_pid, resource=False, card_uid=int(played_card_uid), card_type=card_type, resource_cost=cost)
    view = _resolve('_pvp_fra_view')(state, my_pid, opp_pid)
    g = _ge.Game(int(session.session_id), my_uid, opp_uid)
    _resolve('_pvp_populate_game_state')(g, state, my_pid, opp_pid)
    _pvp_apply_card_play_costs(handler, g, session, state, my_uid, opp_uid, cost_selections, int(played_card_uid), scrounge=any((getattr(ability, 'is_scrounge', False) for ability in play_plan.cast_abilities)))
    _tpl, ct, _n, cost2, atk, def_, gems = handler._card_full_data(g, scid, tpl_guid)
    g.push_card_updated(scid, my_uid, _ge.ECardCollections.CastSpells, ctype_num, template_id=tpl_guid, cost=cost2, attack=atk, defense=def_, gems=gems)
    inst_id = _resolve('port_queue_stack_item')(state, {'kind': 'troop', 'source_uid': int(played_card_uid), 'ability_guids': [], 'target_uid': None, 'x_cost': 0})
    import json as _chj
    _chain_tpl = _ge.PLAY_CARD_ABILITY_TEMPLATE_ID
    try:
        _tabs = _chj.loads(card_abilities) if card_abilities else []
        if _tabs:
            _chain_tpl = str(_tabs[0]).lower()
    except Exception:
        pass
    g.push_ability_on_chain(scid, _ge.ResourceId.from_str(_chain_tpl), ability_instance_id=inst_id)
    state['stack'] = view.get('stack') or []
    state['stack_player_passed'] = False
    state['stack_ai_passed'] = False
    state['stack_passed'] = []
    from rules_port import chain_items as _chain_items
    trigger_host = _resolve('_PvpChainHost')(handler, session, state, my_pid, opp_pid)
    _chain_items.dispatch_card_zone_transition(trigger_host, session, g, state, my_uid, opp_uid, int(played_card_uid), my_pid, source_location, 'CastSpells', int(prior_card_zone[2] or 0) if prior_card_zone else 0)
    _chain_items.dispatch_card_cast(trigger_host, session, g, state, my_uid, opp_uid, int(played_card_uid), my_pid)
    _resolve('pvp_save_state')(session, state)
    g.player_resources = int(state.get(f'res_{my_pid}', 0))
    g.player_total_resources = int(state.get(f'res_total_{my_pid}', 0))
    g.ai_resources = int(state.get(f'res_{opp_pid}', 0))
    g.ai_total_resources = int(state.get(f'res_total_{opp_pid}', 0))
    ev_spent = _ge.PlayerCurrentResourcePoolChangedSessionEventArgs()
    ev_spent.player_id = my_uid
    ev_spent.operation = 2
    ev_spent.delta = cost
    ev_spent.new_value = g.player_resources
    g._push(ev_spent)
    champ_map = state.get('champ_map') or {}
    for target_pid in pids:
        target_uid = _ge.UID.make(244, target_pid)
        cu = int(champ_map.get(str(target_pid), 0))
        champ_scid = _ge.SessionCardId(_ge.UID(cu)) if cu else None
        g.push_player_updated(target_uid, champ_id=champ_scid)
    _resolve('_pvp_send_same_events')(session, g, my_uid, opp_uid)
    if native_port is not None:
        native_port.queue_projected_chain({'kind': 'troop', 'source_uid': int(played_card_uid), 'ability_guids': [], 'target_uid': None, 'instance_id': int(inst_id), 'x_cost': 0}, my_pid, first_player_id=opp_uid)
        state['priority_pid'] = int(opp_pid)
        _resolve('pvp_save_state')(session, state)
        opp_h = _resolve('player_handlers').get(opp_pid)
        if opp_h:
            response = _ge.Game(int(session.session_id), opp_uid, my_uid)
            response.push_green_light(opp_uid, _ge.EPriorityContext.ResolveTopOfChain)
            _resolve('_send_pvp_packet')(opp_h, session, response, opp_uid, 'native-troop-chain-opp')
            _resolve('pvp_push_phase_options')(session, state, pid=opp_pid)
        caster_h = _resolve('player_handlers').get(my_pid)
        if caster_h:
            response = _ge.Game(int(session.session_id), my_uid, opp_uid)
            response.push_green_light(opp_uid, _ge.EPriorityContext.ResolveTopOfChain)
            _resolve('_send_pvp_packet')(caster_h, session, response, my_uid, 'native-troop-chain-caster')
        session._rules_port_mutation_emitted = True
        return True
    log_req(f"    PvP troop play: {card_name} by pid {my_pid} (paid {cost}, stack={len(state.get('stack') or [])} item(s))")
    _resolve('_pvp_log_stack')(state, f'troop-play {card_name}')
    opp_h = _resolve('player_handlers').get(opp_pid)
    if opp_h and (not pvp_player_auto_passes(state, opp_pid)):
        gg = _ge.Game(int(session.session_id), opp_uid, my_uid)
        gg.push_green_light(opp_uid, _ge.EPriorityContext.ResolveTopOfChain)
        _resolve('_send_pvp_packet')(opp_h, session, gg, opp_uid, 'troop-on-stack-opp')
        try:
            _resolve('pvp_push_phase_options')(session, state, pid=opp_pid)
        except Exception as _e:
            log_req(f'    PvP troop-on-stack opp options error: {_e}')
    caster_h = _resolve('player_handlers').get(my_pid)
    if caster_h:
        caster_game = _ge.Game(int(session.session_id), my_uid, opp_uid)
        caster_game.push_green_light(opp_uid, _ge.EPriorityContext.ResolveTopOfChain)
        _resolve('_send_pvp_packet')(caster_h, session, caster_game, my_uid, 'troop-on-stack-caster-lost')
    state['priority_pid'] = opp_pid
    _resolve('pvp_save_state')(session, state)
    _resolve('_pvp_log_stack')(state, f'troop-play-{card_name}-opp-first')
    _resolve('_pvp_auto_pass_chain_priority')(session, state, opp_pid)
    return True


@_pvp_locked
@_pvp_locked
def _pvp_project_resource_play(handler, session, inner_bytes, my_pid, played_card_uid, crow, card_name, pids, my_uid, opp_uid):
    """Project an already RulesPort-validated PvP resource play."""
    opp_pid = pids[0] if pids[1] == my_pid else pids[1]
    state = _resolve('pvp_load_state')(session) or {}
    from rules_port.resources import can_play_resource, resource_play_limit
    resource_limit = resource_play_limit(_resolve('_db'), session.session_id, state, int(my_pid))
    if not can_play_resource(state, 'player', limit=resource_limit, player_id=int(my_pid)):
        log_req(f'    PvP REJECTED resource {card_name}: per-turn allowance used')
        return False
    source_location = _pvp_card_source_location(session, state, my_pid, int(played_card_uid))
    if source_location is None:
        log_req(f'    PvP REJECTED resource {card_name}: card is not in a playable zone')
        return False
    prior_card_zone = _resolve('db_card_owner_zone_state')(session.session_id, int(played_card_uid), conn=_resolve('_db'))
    from pvp_db import db_move_card_if_in_zone
    from domain.constants import PLAYED_CARD_POSITION
    if db_move_card_if_in_zone(session.session_id, int(played_card_uid), int(my_pid), source_location, 'PlayedResources', PLAYED_CARD_POSITION, conn=_resolve('_db')) <= 0:
        return False
    _resolve('_db').commit()
    log_req(f'    PvP resource play: {card_name} by pid {my_pid}')
    trigger_game = _ge.Game(int(session.session_id), my_uid, opp_uid)
    _resolve('_pvp_populate_game_state')(trigger_game, state, my_pid, opp_pid)
    trigger_host = _resolve('_PvpChainHost')(handler, session, state, my_pid, opp_pid)
    from rules_port import chain_items as _chain_items
    _chain_items.dispatch_card_cast(trigger_host, session, trigger_game, state, my_uid, opp_uid, int(played_card_uid), my_pid)
    _chain_items.dispatch_card_zone_transition(trigger_host, session, trigger_game, state, my_uid, opp_uid, int(played_card_uid), my_pid, source_location, 'PlayedResources', int(prior_card_zone[2] or 0) if prior_card_zone else 0)
    current_grant = int(crow[3] or 0)
    max_grant = int(crow[4] or 0)
    if not current_grant and (not max_grant):
        current_grant = max_grant = 1
    shard_ability = shard_tpl = None
    resource_choice_ability = None
    ability_guids = []
    if crow[5]:
        try:
            ability_guids = json.loads(crow[5])
        except Exception:
            ability_guids = []
        shard_ability, shard_tpl = handler._shards_of_fate_template(ability_guids)
        if not shard_tpl:
            from rules_port.resources import printed_resource_choice_ability
            resource_choice_ability = printed_resource_choice_ability(ability_guids)
    is_shards_of_fate = bool(shard_tpl)
    log_req(f"    PvP resource metadata: {card_name} abilities={[str(g)[:8] for g in ability_guids]} choice={str(resource_choice_ability or '')[:8] or 'none'} shards_of_fate={is_shards_of_fate}")
    charge_grant = _pvp_resource_charge_points(session, played_card_uid)
    from rules_port.resources import play_resource_for_player, resource_threshold_grants
    threshold_grants = []
    if not is_shards_of_fate and (not resource_choice_ability):
        threshold_grants = resource_threshold_grants(_resolve('_db'), session.session_id, my_pid, ability_guids, state, source_uid=played_card_uid)
    resource_play = play_resource_for_player(state, my_pid, current_grant, max_grant, charge_amount=charge_grant, additional_plays=max(0, resource_limit - 1))
    charge_delta = max(0, int(resource_play.charge.new_value) - int(resource_play.charge.old_value))
    charge_trigger_game = None
    if charge_delta and (not (is_shards_of_fate or resource_choice_ability)):
        charge_trigger_game = _resolve('_pvp_gain_charge_trigger_game')(handler, session, state, my_pid, amount=charge_delta)
    elif charge_delta:
        state['pending_gain_charge_pid'] = my_pid
        state['pending_gain_charge_amount'] = charge_delta
    thresholds = state.setdefault(f'thresh_{my_pid}', {})
    for flag, amount in threshold_grants:
        thresholds[flag] = int(thresholds.get(flag, 0) or 0) + int(amount)
    _resolve('pvp_save_state')(session, state)
    thresh = _pvp_state_thresholds(state, my_pid)
    threshold_trigger_games = []
    for flag, _amount in threshold_grants:
        trigger = _pvp_gain_threshold_trigger_game(handler, session, state, my_pid, flag)
        if trigger is not None:
            threshold_trigger_games.append(trigger)
    printed_skip = None
    if not is_shards_of_fate:
        printed_skip = {str(resource_choice_ability).lower()} if resource_choice_ability else set()
    resource_ability_events = _pvp_resolve_granted_resource_abilities(handler, session, state, int(played_card_uid), my_pid, printed_skip=printed_skip)
    if not is_shards_of_fate and (not resource_choice_ability) and (state.get('phase') in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase)):
        _resolve('pvp_push_main_phase_options')(session, state)
    champ_map = state.get('champ_map', {})
    trigger_events = list(trigger_game.events)
    trigger_card_defs = dict(trigger_game.card_defs)
    for pid in pids:
        h = _resolve('player_handlers').get(pid)
        if not h:
            continue
        is_me = pid == my_pid
        pl_uid = my_uid if is_me else opp_uid
        other_uid = opp_uid if is_me else my_uid
        g = _ge.Game(int(session.session_id), pl_uid, other_uid)
        _resolve('_pvp_populate_game_state')(g, state, pid, pids[1] if pid == pids[0] else pids[0])
        scid = _ge.SessionCardId(_ge.UID(int(played_card_uid)))
        g.player_health = int(state.get(f'hp_{pid}', 20))
        g.ai_health = int(state.get(f'hp_{(pids[1] if pid == pids[0] else pids[0])}', 20))
        g.player_resources = int(state.get(f'res_{pid}', 0))
        g.player_total_resources = int(state.get(f'res_total_{pid}', 0))
        g.ai_resources = int(state.get(f'res_{(pids[1] if pid == pids[0] else pids[0])}', 0))
        g.ai_total_resources = int(state.get(f'res_total_{(pids[1] if pid == pids[0] else pids[0])}', 0))
        g.player_charges = int(state.get(f'chg_{pid}', 0))
        g.ai_charges = int(state.get(f'chg_{(pids[1] if pid == pids[0] else pids[0])}', 0))
        _rtpl, rct, _rn, rcost, ratk, rdef, _rgem = handler._card_full_data(g, scid, crow[0])
        g.push_card_updated(scid, my_uid, _ECardCollections.PlayedResources, rct, template_id=_rtpl, cost=rcost, attack=ratk, defense=rdef, nulling=False)
        g.push_resource_card_played(scid, my_uid, free=False)
        my_uid_p = _ge.UID.make(244, my_pid)
        if current_grant:
            ev_cur = _ge.PlayerCurrentResourcePoolChangedSessionEventArgs()
            ev_cur.player_id = my_uid_p
            ev_cur.operation = 1
            ev_cur.delta = current_grant
            ev_cur.new_value = int(state.get(f'res_{my_pid}', 0))
            g._push(ev_cur)
        if max_grant:
            ev_tot = _ge.PlayerTotalResourcePoolChangedSessionEventArgs()
            ev_tot.player_id = my_uid_p
            ev_tot.operation = 1
            ev_tot.delta = max_grant
            ev_tot.new_value = int(state.get(f'res_total_{my_pid}', 0))
            g._push(ev_tot)
        for flag, amount in threshold_grants:
            ev_th = _ge.PlayerResourceThresholdChangedSessionEventArgs()
            ev_th.player_id = my_uid_p
            ev_th.color = flag
            ev_th.operation = 1
            ev_th.delta = int(amount)
            ev_th.new_value = int(thresh.get(flag, 0))
            g._push(ev_th)
        ev_chg = _ge.ChampionChargePointsChangedSessionEventArgs()
        ev_chg.player_id = my_uid_p
        ev_chg.operation = 1
        ev_chg.delta = charge_delta
        ev_chg.new_value = int(state.get(f'chg_{my_pid}', 0))
        g._push(ev_chg)
        if charge_trigger_game:
            for trigger_event in charge_trigger_game.events:
                g._push(trigger_event)
        for threshold_trigger_game in threshold_trigger_games:
            for trigger_event in threshold_trigger_game.events:
                g._push(trigger_event)
        for resource_event in resource_ability_events:
            g._push(resource_event)
        for trigger_event in trigger_events:
            g._push(trigger_event)
        g.card_defs.update(trigger_card_defs)
        for target_pid in pids:
            target_uid = _ge.UID.make(244, target_pid)
            cu = int(champ_map.get(str(target_pid), 0))
            champ_scid = _ge.SessionCardId(_ge.UID(cu)) if cu else None
            g.push_player_updated(target_uid, champ_id=champ_scid)
        if g.events:
            try:
                _cls2 = [getattr(type(_e), 'CLASS_ID', 0) for _e in g.events]
                log_req(f'    PvP resource-audit -> pid {pid}: classes={_cls2}')
            except Exception as _e2:
                log_req(f'    PvP resource-audit error: {_e2}')
            pkt = g.make_network_packet(pl_uid)
            dw = _resolve('encode_datawrapper')(0, 3055, _resolve('compress_gzip')(_resolve('encode_sync_event')(pkt)), 1, _resolve('client_session_guid')(h))
            h.scnt += 1
            h.send({'issuer': f'0.0.0.0.ServiceGameSession.246.{session.session_id}.{h.scnt}', 'target': 'ServiceGameSession', 'instance': str(session.server_id), 'reqid': 0, 'c': 0, 'conh': 0, 'sid': h.sid}, dw)
            log_req(f'    PvP resource: pushed to pid {pid}')
    champ_map = state.get('champ_map', {})
    for pid in pids:
        refresh_handler = _resolve('player_handlers').get(pid)
        if not refresh_handler:
            continue
        opponent_pid = pids[1] if pid == pids[0] else pids[0]
        player_uid = _ge.UID.make(244, int(pid))
        opponent_uid = _ge.UID.make(244, int(opponent_pid))
        refresh_game = _ge.Game(int(session.session_id), player_uid, opponent_uid)
        _resolve('_pvp_populate_game_state')(refresh_game, state, int(pid), int(opponent_pid))
        refresh_game.push_player_updated(player_uid, champ_id=_ge.SessionCardId(_ge.UID(int(champ_map.get(str(pid), 0)))))
        refresh_game.push_player_updated(opponent_uid, champ_id=_ge.SessionCardId(_ge.UID(int(champ_map.get(str(opponent_pid), 0)))))
        _resolve('_send_pvp_packet')(refresh_handler, session, refresh_game, player_uid, 'resource-state-refresh')
    log_req('    PvP resource: final player-state refresh pushed')
    if charge_trigger_game and charge_trigger_game.events and state.get('stack'):
        _resolve('_pvp_offer_trigger_response')(session, state, my_pid)
        return True
    if resource_choice_ability:
        state['priority_pid'] = my_pid
        _resolve('pvp_save_state')(session, state)
        owner_handler = _resolve('player_handlers').get(my_pid) or handler
        prompt_game = _ge.Game(int(session.session_id), my_uid, opp_uid)
        _resolve('_pvp_populate_game_state')(prompt_game, state, my_pid, opp_pid)
        prompt_view = _resolve('_pvp_fra_view')(state, my_pid, opp_pid)
        _resolve('_pvp_resolve_ability')(owner_handler, prompt_game, session, prompt_view, my_uid, opp_uid, resource_choice_ability, int(played_card_uid), my_pid, target_map={})
        state['stack'] = prompt_view.get('stack') or []
        state['stack_player_passed'] = False
        state['stack_ai_passed'] = False
        _resolve('_pvp_sync_view_to_state')(state, prompt_view, my_pid, opp_pid)
        persisted = _resolve('pvp_load_state')(session) or {}
        for pending_key in ('pending_choice', 'resolution_paused'):
            if persisted.get(pending_key):
                state[pending_key] = persisted[pending_key]
        _resolve('pvp_save_state')(session, state)
        if state.get('pending_choice'):
            log_req(f'    PvP resource choice: awaiting picker for pid {my_pid}')
            return True
        pending_charge_amount = _take_pending_gain_charge_amount(state, my_pid)
        if pending_charge_amount:
            charge_trigger_game = _resolve('_pvp_gain_charge_trigger_game')(owner_handler, session, state, my_pid, amount=pending_charge_amount)
            if charge_trigger_game and charge_trigger_game.events:
                _resolve('_pvp_send_same_events')(session, charge_trigger_game, my_uid, opp_uid)
                if state.get('stack'):
                    _resolve('_pvp_offer_trigger_response')(session, state, my_pid)
                    return True
    if is_shards_of_fate:
        state['priority_pid'] = my_pid
        _resolve('pvp_save_state')(session, state)
        prompt_game = _ge.Game(int(session.session_id), my_uid, opp_uid)
        _resolve('_pvp_populate_game_state')(prompt_game, state, my_pid, opp_pid)
        prompt_view = _resolve('_pvp_fra_view')(state, my_pid, opp_pid)
        owner_handler = _resolve('player_handlers').get(my_pid) or handler
        from rules_port.resources import resolve_printed_resource_abilities
        ability_logs = resolve_printed_resource_abilities(prompt_game, session, _resolve('_db'), owner_handler, my_uid, opp_uid, prompt_view, int(played_card_uid), my_pid)
        if ability_logs:
            log_req('    PvP Shards of Fate ability: ' + '; '.join(ability_logs))
        state['stack'] = prompt_view.get('stack') or []
        state['stack_player_passed'] = False
        state['stack_ai_passed'] = False
        _resolve('_pvp_sync_view_to_state')(state, prompt_view, my_pid, opp_pid)
        persisted = _resolve('pvp_load_state')(session) or {}
        for pending_key in ('pending_deck_search', 'pending_choice', 'pending_trigger', 'pending_conversation'):
            if persisted.get(pending_key):
                state[pending_key] = persisted[pending_key]
        if prompt_view.get('resolution_paused') or persisted.get('resolution_paused'):
            state['resolution_paused'] = True
        _resolve('pvp_save_state')(session, state)
        if state.get('pending_deck_search'):
            log_req(f'    PvP Shards of Fate: awaiting threshold choice for pid {my_pid}')
            return True
        pending_charge_amount = _take_pending_gain_charge_amount(state, my_pid)
        if pending_charge_amount:
            charge_trigger_game = _resolve('_pvp_gain_charge_trigger_game')(handler, session, state, my_pid, amount=pending_charge_amount)
            if charge_trigger_game and charge_trigger_game.events:
                _resolve('_pvp_send_same_events')(session, charge_trigger_game, my_uid, opp_uid)
                if state.get('stack'):
                    _resolve('_pvp_offer_trigger_response')(session, state, my_pid)
                    return True
    turn_h = _resolve('player_handlers').get(my_pid)
    if turn_h:
        gg = _ge.Game(int(session.session_id), my_uid, opp_uid)
        gg.push_green_light(my_uid, _ge.EPriorityContext.Normal)
        _resolve('_send_pvp_packet')(turn_h, session, gg, my_uid, 'greenlight-after-play')
    state['priority_pid'] = my_pid
    _resolve('pvp_save_state')(session, state)
    return True


def _pvp_resolve_ability_item(session, state, handler, my_pid, item):
    """Resolve one manual/champion ability chain item in PvP.

    Card, spell and triggered item resolution is shared with Practice/PvE
    through ``rules_port.chain_items``; a manual/champion ability carries
    PvP-only projections (charge/spell-power cost events before its picker
    opens, the champion-health delta after it resolves, and the deferred
    class-23 discard prompt for a nested discard leaf), so its projection
    stays here.  The item, its mirror and the chain presentation are owned by
    the caller; this function only projects the effect and the priority
    hand-off.
    """
    if not item:
        return False
    from rules_port import lifecycle as _be
    item = dict(item)
    stack = state.get('stack') or []
    if stack and int(stack[-1].get('instance_id', -1)) == int(item.get('instance_id', -2)):
        stack.pop()
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        pids = _resolve('_pvp_session_pids')(session, state)
    if len(pids) < 2:
        return False
    kind = 'ability'
    ag = str(item.get('ability_guid') or '')
    instance_id = int(item.get('instance_id', 1))
    src_uid = int(item.get('source_uid') or 0)
    owner_id = my_pid
    if src_uid:
        orow = db_card_basic(session.session_id, src_uid)
        if orow:
            owner_id = orow[1]
    opp_pid = pids[0] if pids[1] == owner_id else pids[1]
    pl_t = _ge.UID.make(244, owner_id)
    ai_t = _ge.UID.make(244, opp_pid)
    view = _resolve('_pvp_fra_view')(state, owner_id, opp_pid)
    view['resolving_owner_id'] = owner_id
    view['resolving_source_uid'] = src_uid
    view['player_mod_target'] = item.get('target_uid')
    view['player_spell_target'] = item.get('target_uid')
    g = _ge.Game(int(session.session_id), pl_t, ai_t)
    _resolve('_pvp_populate_game_state')(g, state, owner_id, opp_pid)
    if kind == 'ability':
        ag = str(item.get('ability_guid') or '')
        if ag.lower() == 'f2d6797b-1a24-4c3d-9239-a27a2e0de0ff':
            from rules_port.tunneling import surface_source_is_underground
            if not surface_source_is_underground(_resolve('_db'), session, item.get('source_uid')):
                log_req(f"    Ignoring stale PvP tunneling Surface source={item.get('source_uid')}")
                ag = ''
        if ag:
            try:
                from rules_port.resolution import resolve_port_ability
                view['player_mod_target'] = item.get('target_uid')
                view['player_spell_target'] = item.get('target_uid')
                view['resolving_ability'] = ag
                view['resolving_source_uid'] = src_uid
                view['resolving_owner_id'] = owner_id
                cost_game = _ge.Game(int(session.session_id), pl_t, ai_t)
                _resolve('_pvp_populate_game_state')(cost_game, state, owner_id, opp_pid)
                _pvp_emit_paid_cost_events(cost_game, state, handler=handler, session=session, db=_resolve('_db'), player_uid=pl_t, ai_uid=ai_t)
                if cost_game.events:
                    _resolve('_pvp_send_same_events')(session, cost_game, pl_t, ai_t)
                ability_event_start = len(g.events)
                ability_player_health_before = int(view.get('player_health', 20))
                ability_ai_health_before = int(view.get('ai_health', 20))
                activation_data = item.get('activation_data') or {}
                target_map = dict(activation_data.get('target_map') or {}) if isinstance(activation_data, dict) else {}
                if not target_map and item.get('target_uid') is not None:
                    from gamedata import ability_graph, DEFAULT_RECORD_STORE
                    graph = _resolve("ability_graph")(DEFAULT_RECORD_STORE, ag)
                    if graph is not None:
                        for index, spec in enumerate(graph.targets):
                            if spec.requires_input:
                                target_map[index] = int(item['target_uid'])
                                break
                resolve_port_ability(handler, g, session, _resolve('_db'), pl_t, ai_t, view, ag, src_uid, owner_id, target_map=target_map, variables=activation_data.get('variables') or {} if isinstance(activation_data, dict) else {}, cost_target_map=activation_data.get('cost_target_map') or {} if isinstance(activation_data, dict) else {}, instance_id=instance_id)
                resolved_state = _resolve('pvp_load_state')(session)
                if isinstance(resolved_state, dict) and resolved_state:
                    if resolved_state is not state:
                        state.clear()
                        state.update(resolved_state)
                    view = _resolve('_pvp_fra_view')(state, owner_id, opp_pid)
                ability_player_health_after = int(view.get('player_health', ability_player_health_before))
                ability_ai_health_after = int(view.get('ai_health', ability_ai_health_before))
                g.player_health = ability_player_health_after
                g.ai_health = ability_ai_health_after
                g.push_champion_health_changed_if_missing(pl_t, ability_player_health_before, ability_player_health_after, since=ability_event_start)
                g.push_champion_health_changed_if_missing(ai_t, ability_ai_health_before, ability_ai_health_after, since=ability_event_start)
            except Exception as e:
                import traceback
                log_req(f'    PvP chain ability resolve error: {e}')
                traceback.print_exc()
            view.pop('player_mod_target', None)
            view.pop('player_spell_target', None)
            view.pop('resolving_ability', None)
            view.pop('resolving_source_uid', None)
    state['stack'] = view.get('stack') or []
    state['stack_player_passed'] = False
    state['stack_ai_passed'] = False
    state['stack_passed'] = []
    state['_next_instance_id'] = view.get('_next_instance_id', 1)
    if view.get('player_health') is not None:
        state[f'hp_{owner_id}'] = int(view['player_health'])
    if view.get('ai_health') is not None:
        state[f'hp_{opp_pid}'] = int(view['ai_health'])
    _resolve('_pvp_sync_view_to_state')(state, view, owner_id, opp_pid)
    persisted = _resolve('pvp_load_state')(session) or {}
    for _pending_key in ('pending_trigger', 'pending_deck_search', 'pending_choice', 'pending_conversation'):
        if persisted.get(_pending_key):
            state[_pending_key] = persisted[_pending_key]
    pending_discard = False
    discard_pid = int(owner_id)
    if ag:
        discard_prompt = _pvp_discard_prompt_data(ag)
        if discard_prompt:
            item_activation = item.get('activation_data') or {}
            discard_pid = _pvp_discard_prompt_pid(session, state, ag, owner_id, opp_pid, src_uid, target_uid=item.get('target_uid'), target_map=item_activation.get('target_map') if isinstance(item_activation, dict) else None)
        hand_exists = _resolve('db_hand_exists')(session.session_id, discard_pid, conn=_resolve('_db'))
        if discard_prompt and hand_exists:
            state['pending_discard_ability'] = discard_prompt[0]
            state['pending_discard_target_template'] = discard_prompt[1]
            state['pending_discard_source_uid'] = int(src_uid)
            state['pending_discard_pid'] = int(discard_pid)
            state['priority_pid'] = int(discard_pid)
            pending_discard = True
    chain_empty = _be.stack_empty(state)
    pending_revealed_choice = (state.get('pending_deck_search') or {}).get('kind') in ('revealed_troop', 'revealed_card')
    pending_trigger = bool(state.get('pending_trigger'))
    pending_choice = bool(state.get('pending_choice'))
    pending_conversation = bool(state.get('pending_conversation'))
    pending_deck_search = bool(state.get('pending_deck_search'))
    if chain_empty and (not (pending_revealed_choice or pending_deck_search or pending_trigger or pending_choice or pending_conversation or pending_discard)):
        from rules_port.tunneling import queue_surfaces
        turn_pid = int(state.get('turn_pid') or 0)
        tunnel_handler = _resolve('player_handlers').get(turn_pid) or handler
        turn_pt = _ge.UID.make(244, turn_pid)
        turn_opp = _ge.UID.make(244, pids[1] if turn_pid == pids[0] else pids[0])
        state['_rules_port_attached'] = True
        from rules_port.context import EffectContext
        tunnel_context = EffectContext.from_rules_port(g, session, _resolve('_db'), tunnel_handler, turn_pt, turn_opp, state, '', ability=None)
        tunnel_surfaces = queue_surfaces(tunnel_context, turn_pid)
        if tunnel_surfaces:
            chain_empty = False
            _resolve('pvp_save_state')(session, state)
            log_req(f'    PvP state-based tunneling: queued surfaces {tunnel_surfaces}')
    _resolve('_pvp_log_stack')(state, 'resolve')
    if chain_empty and (not (pending_revealed_choice or pending_deck_search or pending_trigger or pending_choice or pending_conversation)):
        g.push_chain_empty()
    _resolve('pvp_save_state')(session, state)
    if chain_empty:
        try:
            from rules_port.context import EffectContext
            from rules_port.death_effects import state_based_deaths
            view['_rules_port_attached'] = True
            state_based_deaths(EffectContext.from_rules_port(g, session, _resolve('_db'), handler, pl_t, ai_t, view, 'state_based_death', ability=None))
        except Exception as e:
            log_req(f'    PvP state-based deaths error: {e}')
        if view.get('player_health') is not None:
            state[f'hp_{owner_id}'] = int(view['player_health'])
        if view.get('ai_health') is not None:
            state[f'hp_{opp_pid}'] = int(view['ai_health'])
        state['stack'] = view.get('stack') or []
        _resolve('_pvp_sync_view_to_state')(state, view, owner_id, opp_pid)
        _resolve('pvp_save_state')(session, state)
    ignores_chain = False
    if kind == 'ability':
        try:
            from rules_port.session import projected_ability_ignores_chain
            ignores_chain = projected_ability_ignores_chain(item)
        except Exception:
            ignores_chain = False
    if not ignores_chain and (not any((state.get(key) for key in ('pending_choice', 'pending_trigger', 'pending_deck_search', 'pending_conversation', 'pending_discard_ability', 'resolution_paused')))):
        g.push_top_of_chain_resolved(instance_id)
        g.push_removed_top_of_chain(instance_id)
    _pvp_emit_paid_cost_events(g, state, handler=handler, session=session, db=_resolve('_db'), player_uid=pl_t, ai_uid=ai_t)
    _resolve('_pvp_send_same_events')(session, g, pl_t, ai_t)
    if chain_empty and pending_discard:
        discard_other = int(opp_pid) if discard_pid == int(owner_id) else int(owner_id)
        _pvp_push_discard_prompt(session, state, discard_pid, discard_other, int(src_uid))
        _resolve('pvp_save_state')(session, state)
        log_req(f'    PvP chain paused for discard choice: {ag[:8]} source={hex(int(src_uid))}')
        return True
    if chain_empty and pending_revealed_choice:
        _resolve('pvp_save_state')(session, state)
        log_req('    PvP chain paused for revealed-card choice')
        return True
    if chain_empty and pending_deck_search:
        _resolve('pvp_save_state')(session, state)
        log_req('    PvP chain paused for deck-search choice')
        return True
    if chain_empty and pending_trigger:
        _resolve('pvp_save_state')(session, state)
        log_req('    PvP chain paused for triggered target choice')
        return True
    if chain_empty and pending_choice:
        _resolve('pvp_save_state')(session, state)
        log_req('    PvP chain paused for card choice')
        return True
    if chain_empty and pending_conversation:
        _resolve('pvp_save_state')(session, state)
        log_req('    PvP chain paused for encounter conversation')
        return True
    if _pvp_check_game_end(session, state):
        return True
    if chain_empty:
        turn_pid = state.get('turn_pid')
        turn_h = _resolve('player_handlers').get(turn_pid)
        state['priority_pid'] = turn_pid
        _resolve('pvp_save_state')(session, state)
        if turn_h:
            turn_pt = _ge.UID.make(244, turn_pid)
            turn_opp = _ge.UID.make(244, pids[1] if turn_pid == pids[0] else pids[0])
            gg = _ge.Game(int(session.session_id), turn_pt, turn_opp)
            gg.push_green_light(turn_pt, _ge.EPriorityContext.Normal)
            _pvp_push_turn_phase_with_elapsed(gg, int(state.get('phase', 0)), turn_pt, turn_pt, _resolve('priority_elapsed_ticks')(state, turn_pid) // 10000000)
            _resolve('_send_pvp_packet')(turn_h, session, gg, turn_pt, 'chain-empty')
        _cur_phase = int(state.get('phase', 0))
        if _cur_phase in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
            _resolve('pvp_push_main_phase_options')(session, state)
        elif _cur_phase == _ge.ETurnPhases.DeclareAttack:
            _resolve('pvp_push_attack_options')(session, state)
        elif _cur_phase == _ge.ETurnPhases.DeclareDefense:
            pvp_push_blocker_options(session, state)
        elif _cur_phase not in (3, 4, 5, 6, 7, 8, 9):
            _resolve('pvp_push_phase_options')(session, state, pid=turn_pid)
        log_req(f'    PvP chain resolved ({kind}) — empty, priority to turn player {turn_pid}, phase {_cur_phase} options re-pushed')
    else:
        other_pid = pids[1] if my_pid == pids[0] else pids[0]
        state['priority_pid'] = other_pid
        _resolve('pvp_save_state')(session, state)
        if _resolve('_pvp_auto_pass_chain_priority')(session, state, other_pid):
            return True
        other_h = _resolve('player_handlers').get(other_pid)
        if other_h:
            other_pt = _ge.UID.make(244, other_pid)
            other_opp = _ge.UID.make(244, pids[1] if other_pid == pids[0] else pids[0])
            gg = _ge.Game(int(session.session_id), other_pt, other_opp)
            gg.push_green_light(other_pt, _ge.EPriorityContext.ResolveTopOfChain)
            _resolve('_send_pvp_packet')(other_h, session, gg, other_pt, 'chain-next')
        log_req(f"    PvP chain resolved ({kind}) — {len(state.get('stack') or [])} item(s) left, priority to {other_pid}")
    return True


def _pvp_resolve_choice(handler, session, inner_bytes, my_pid, typed_payload=None):
    """Resolve a private ChooseAndPlay choice and resume its parent BOM."""
    from rules_port.choice_effects import extract_card_uids, _play_choice_card, _resolve_choice_card_abilities
    state = _resolve('pvp_load_state')(session) or {}
    pending = state.get('pending_choice')
    if not pending:
        return False
    selected = extract_card_uids(inner_bytes)
    if not selected and isinstance(typed_payload, dict):
        activation = typed_payload.get('activation_data')
        target_map = activation.get('target_map') if isinstance(activation, dict) else None
        if isinstance(target_map, dict):
            for target in target_map.values():
                values = target if isinstance(target, (list, tuple, set)) else (target,)
                for value in values:
                    try:
                        uid = int(value)
                    except (TypeError, ValueError):
                        continue
                    if uid & 255 == 1:
                        selected.append(uid)
    legal = {int(uid) for uid in pending.get('choice_uids', [])}
    chosen_uid = next((uid for uid in reversed(selected) if int(uid) in legal), None)
    owner_id = int(pending.get('owner_id', 0))
    log_req(f'    PvP choice parse: selected={[hex(int(u)) for u in selected]} legal={[hex(int(u)) for u in legal]} chosen={(hex(int(chosen_uid)) if chosen_uid else None)}')
    if owner_id != int(my_pid) or chosen_uid is None:
        log_req(f'    PvP choice answer invalid: pid={my_pid} chosen={chosen_uid} owner={owner_id}')
        return True
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return True
    opponent_id = next((pid for pid in pids if int(pid) != owner_id))
    pl_t = _ge.UID.make(244, owner_id)
    ai_t = _ge.UID.make(244, opponent_id)
    state.pop('pending_choice', None)
    state.pop('resolution_paused', None)
    choice_zone_target = pending.get('kind') == 'choice_zone_target'
    choice_zone_copy = pending.get('kind') == 'choice_zone_copy'
    if choice_zone_copy:
        state['selected_choice_uid'] = int(chosen_uid)
    view = _resolve('_pvp_fra_view')(state, owner_id, opponent_id)
    view.pop('pending_choice', None)
    view.pop('resolution_paused', None)
    if choice_zone_copy:
        view['choice_copy_to_hand'] = True
    g = _ge.Game(int(session.session_id), pl_t, ai_t)
    _resolve('_pvp_populate_game_state')(g, state, owner_id, opponent_id)
    from rules_port.context import EffectContext
    choice_context = EffectContext.from_rules_port(g, session, _resolve('_db'), handler, pl_t, ai_t, view, 'choice', ability=None)
    if choice_zone_target:
        continuation = pending.get('continuation') or {}
        child_guid = str(continuation.get('ability_guid') or pending.get('ability_guid') or '').lower()
        child_source = int(continuation.get('source_uid', pending.get('source_uid', 0)) or 0)
        child_owner = int(continuation.get('owner_id', owner_id) or owner_id)
        child_targets = {int(key): value for key, value in (continuation.get('target_map') or {}).items()}
        child_targets[int(continuation.get('target_index', 0) or 0)] = int(chosen_uid)
        log_req(f"    PvP choice_zone_target DEBUG: child={child_guid} child_resume={continuation.get('resume_effect_order')} parent={pending.get('parent', {}).get('ability_guid')} parent_resume={pending.get('parent', {}).get('resume_effect_order')}", level='DEBUG')
        _resolve('_pvp_resolve_ability')(handler, g, session, view, pl_t, ai_t, child_guid, child_source, child_owner, target_map=child_targets, variables=continuation.get('variables') or {}, resume_from_order=int(continuation.get('resume_effect_order', 0) or 0))
        parent = pending.get('parent') or {}
        parent_guid = str(parent.get('ability_guid') or '').lower()
        if parent_guid and (not _ability_has_effects_from(parent_guid, parent.get('resume_effect_order', 0))):
            parent_guid = ''
        if parent_guid and (not state.get('pending_choice')):
            _resolve('_pvp_resolve_ability')(handler, g, session, view, pl_t, ai_t, parent_guid, parent.get('source_uid'), int(parent.get('owner_id', owner_id) or owner_id), target_map={int(key): value for key, value in (parent.get('target_map') or {}).items()}, variables=parent.get('variables') or {}, resume_from_order=int(parent.get('resume_effect_order', 0) or 0))
    else:
        if not choice_zone_copy and (not _play_choice_card(choice_context, chosen_uid, owner_id)):
            log_req(f'    PvP choice card no longer selectable: {chosen_uid}')
            state['pending_choice'] = pending
            state['resolution_paused'] = True
            _resolve('pvp_save_state')(session, state)
            return True
        if not choice_zone_copy:
            _resolve_choice_card_abilities(choice_context, chosen_uid, pending.get('source_uid'), owner_id)
        target_map = {int(key): value for key, value in (pending.get('target_map') or {}).items()}
        _resolve('_pvp_resolve_ability')(handler, g, session, view, pl_t, ai_t, pending['ability_guid'], pending.get('source_uid'), owner_id, target_map=target_map, variables=pending.get('variables') or {}, resume_from_order=int(pending.get('resume_effect_order', 0)))
    if choice_zone_copy:
        view.pop('choice_copy_to_hand', None)
        from rules_port.choice_effects import _clear_choice_zone
        _clear_choice_zone(choice_context)
        state.pop('selected_choice_uid', None)
    if not state.get('pending_choice') and (not state.get('resolution_paused')):
        completed = int(state.pop('paused_chain_instance_id', 0) or 0)
        if completed:
            state['completed_chain_instance_id'] = completed
    state['stack'] = view.get('stack') or []
    state['stack_player_passed'] = False
    state['stack_ai_passed'] = False
    _resolve('_pvp_sync_view_to_state')(state, view, owner_id, opponent_id)
    charge_trigger_game = None
    pending_charge_amount = _take_pending_gain_charge_amount(state, owner_id) if not view.get('pending_choice') else 0
    if pending_charge_amount:
        charge_trigger_game = _resolve('_pvp_gain_charge_trigger_game')(handler, session, state, owner_id, amount=pending_charge_amount)
        if charge_trigger_game:
            for trigger_event in charge_trigger_game.events:
                g._push(trigger_event)
    _resolve('pvp_save_state')(session, state)
    _resolve('_pvp_send_same_events')(session, g, pl_t, ai_t)
    if state.get('pending_choice'):
        log_req(f'    PvP choice selected: {hex(int(chosen_uid))}; second choice pending')
        return True
    _pvp_push_waiting_on(session, None)
    if charge_trigger_game and state.get('stack'):
        _resolve('_pvp_offer_trigger_response')(session, state, owner_id)
        return True
    g2 = _ge.Game(int(session.session_id), pl_t, ai_t)
    g2.push_chain_empty()
    state['priority_pid'] = int(state.get('turn_pid') or owner_id)
    _resolve('pvp_save_state')(session, state)
    turn_pid = int(state['priority_pid'])
    turn_handler = _resolve('player_handlers').get(turn_pid)
    if turn_handler is not None:
        turn_uid = _ge.UID.make(244, turn_pid)
        other_uid = _ge.UID.make(244, opponent_id if turn_pid == owner_id else owner_id)
        g2 = _ge.Game(int(session.session_id), turn_uid, other_uid)
        g2.push_green_light(turn_uid, _ge.EPriorityContext.Normal)
        _resolve('_send_pvp_packet')(turn_handler, session, g2, turn_uid, 'greenlight-after-choice')
    if state.get('phase') in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
        _resolve('pvp_push_main_phase_options')(session, state)
    log_req(f'    PvP choice selected: {hex(int(chosen_uid))}; choice sequence complete')
    return True


def _pvp_resolve_matching_target(handler, session, inner_bytes, my_pid, typed_payload=None):
    """Resolve a PvP deck target whose typed effect keeps it in the deck.

    This is the PvP counterpart of the FRA continuation: Scheme's selected
    action is never moved to hand, all picker candidates are hidden again,
    and the child BOM is resumed with the selected TargetMap before priority
    is returned to the active player.  A SourceRevealed picker (Oakhenge)
    shares the same continuation boundary; its child effect owns the move of
    the chosen card and the return of every other revealed card.
    """
    import struct
    state = _resolve('pvp_load_state')(session) or {}
    pend = state.pop('pending_deck_search', None)
    if not pend:
        return False
    state.pop('resolution_paused', None)
    chosen_uid = None
    if isinstance(inner_bytes, bytes):
        for m_du in re.finditer(b'm_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});', inner_bytes):
            try:
                uid64 = struct.unpack('<Q', bytes.fromhex(m_du.group(1).decode()))[0]
                if uid64 & 255 == 1:
                    chosen_uid = int(uid64)
            except Exception:
                continue
    continuation = pend.get('continuation') or {}
    typed_uid = _pvp_typed_picker_uid(typed_payload, continuation.get('target_index', 0))
    if typed_uid is not None:
        chosen_uid = typed_uid
    candidates = [int(uid) for uid in pend.get('candidates') or []]
    owner_id = int(pend.get('owner_id', my_pid) or my_pid)
    pids = _resolve('db_game_session_pids')(session.session_id)
    if not chosen_uid or chosen_uid not in candidates or owner_id not in pids or (int(my_pid) != owner_id) or (len(pids) < 2):
        _resolve('pvp_save_state')(session, state)
        log_req(f'    PvP matching-target invalid choice: chosen={chosen_uid} candidates={candidates}')
        handler._push_transaction_ack(session)
        return True
    opp_pid = next((pid for pid in pids if int(pid) != owner_id))
    pl_t = _ge.UID.make(244, owner_id)
    ai_t = _ge.UID.make(244, opp_pid)
    view = _resolve('_pvp_fra_view')(state, owner_id, opp_pid)
    view.pop('pending_deck_search', None)
    view.pop('resolution_paused', None)
    g = _ge.Game(int(session.session_id), pl_t, ai_t)
    _resolve('_pvp_populate_game_state')(g, state, owner_id, opp_pid)
    handler._hide_candidates_to_deck(g, session, pl_t, ai_t, [int(uid) for uid in pend.get('revealed_cards') or candidates])
    child_guid = str(continuation.get('ability_guid') or '').lower()
    child_source = int(continuation.get('source_uid') or 0)
    child_owner = int(continuation.get('owner_id', owner_id) or owner_id)
    child_targets = {int(key): value for key, value in (continuation.get('target_map') or {}).items()}
    child_targets[int(continuation.get('target_index', 0))] = int(chosen_uid)
    _resolve('_pvp_resolve_ability')(handler, g, session, view, pl_t, ai_t, child_guid, child_source, child_owner, target_map=child_targets, variables=continuation.get('variables') or {}, event_tac=continuation.get('event_tac'))
    from rules_port.resolution import resume_ability_continuation_parents
    resume_ability_continuation_parents(handler, g, session, _resolve('_db'), pl_t, ai_t, view, continuation)
    state['stack'] = view.get('stack') or []
    state['stack_player_passed'] = False
    state['stack_ai_passed'] = False
    _resolve('_pvp_sync_view_to_state')(state, view, owner_id, opp_pid)
    for key in ('resolution_paused', 'paused_chain_instance_id', 'completed_chain_instance_id', 'rules_port_suspended_chain_continuation'):
        if key in view:
            state[key] = view[key]
        else:
            state.pop(key, None)
    persisted = _resolve('pvp_load_state')(session) or {}
    for key in ('pending_trigger', 'pending_deck_search', 'pending_choice', 'pending_conversation'):
        if persisted.get(key):
            state[key] = persisted[key]
    pending_charge_amount = _take_pending_gain_charge_amount(state, owner_id)
    if pending_charge_amount:
        charge_trigger_game = _resolve('_pvp_gain_charge_trigger_game')(handler, session, state, owner_id, amount=pending_charge_amount)
        if charge_trigger_game:
            for trigger_event in charge_trigger_game.events:
                g._push(trigger_event)
    _resolve('pvp_save_state')(session, state)
    _resolve('_pvp_send_same_events')(session, g, pl_t, ai_t)
    if state.get('pending_trigger') or state.get('pending_deck_search') or state.get('pending_choice') or state.get('pending_conversation'):
        handler._push_transaction_ack(session)
        return True
    if state.get('stack'):
        _resolve('_pvp_offer_trigger_response')(session, state, owner_id)
    else:
        state['priority_pid'] = int(state.get('turn_pid') or owner_id)
        state['stack_passed'] = []
        _resolve('pvp_save_state')(session, state)
        priority_pid = int(state['priority_pid'])
        priority_handler = _resolve('player_handlers').get(priority_pid)
        if priority_handler:
            priority_uid = _ge.UID.make(244, priority_pid)
            other_uid = _ge.UID.make(244, next((pid for pid in pids if int(pid) != priority_pid)))
            resume = _ge.Game(int(session.session_id), priority_uid, other_uid)
            resume.push_chain_empty()
            resume.push_green_light(priority_uid, _ge.EPriorityContext.Normal)
            _resolve('_send_pvp_packet')(priority_handler, session, resume, priority_uid, 'greenlight-after-matching-target')
        if state.get('phase') in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
            _resolve('pvp_push_main_phase_options')(session, state)
    handler._push_transaction_ack(session)
    log_req(f'    PvP matching target chosen: {hex(int(chosen_uid))}; created matching cards and restored priority')
    return True


def _pvp_resolve_revealed_choice(handler, session, inner_bytes, my_pid, typed_payload=None):
    """Resolve an explicit choice from a SourceRevealed prompt.

    A typed continuation resumes the child ability that owns the move
    (Oakhenge's "put a revealed troop into your hand, then put the remaining
    cards into your deck") through the shared deck-target continuation; a
    legacy prompt without one applies the pick directly.
    """
    import re as _re
    import struct as _st
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return False
    state = _resolve('pvp_load_state')(session) or {}
    pend = state.get('pending_deck_search') or {}
    if pend.get('kind') not in ('revealed_troop', 'revealed_card'):
        return False
    if pend.get('continuation'):
        return _resolve('_pvp_resolve_matching_target')(handler, session, inner_bytes, my_pid, typed_payload=typed_payload)
    state.pop('pending_deck_search', None)
    chosen_uid = None
    if isinstance(inner_bytes, bytes):
        for m_du in _re.finditer(b'm_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});', inner_bytes):
            try:
                uid64 = _st.unpack('<Q', bytes.fromhex(m_du.group(1).decode()))[0]
                if uid64 & 255 == 1:
                    chosen_uid = int(uid64)
            except Exception:
                continue
    continuation = pend.get('continuation') or {}
    typed_uid = _pvp_typed_picker_uid(typed_payload, continuation.get('target_index', 0))
    if typed_uid is not None:
        chosen_uid = typed_uid
    candidates = [int(c) for c in pend.get('candidates') or []]
    revealed = [int(c) for c in pend.get('revealed_cards') or []]
    owner_id = int(pend.get('owner_id', my_pid))
    if not chosen_uid or chosen_uid not in candidates:
        _resolve('pvp_save_state')(session, state)
        log_req(f'    PvP revealed choice invalid: chosen={chosen_uid} candidates={candidates}')
        return True
    opp_pid = pids[0] if pids[1] == owner_id else pids[1]
    pl_t = _ge.UID.make(244, owner_id)
    ai_t = _ge.UID.make(244, opp_pid)
    g = _ge.Game(int(session.session_id), pl_t, ai_t)
    _resolve('_pvp_populate_game_state')(g, state, owner_id, opp_pid)
    from rules_port.context import EffectContext
    from rules_port.deck_effects import move_deck_card_to_hand
    state['_rules_port_attached'] = True
    move_deck_card_to_hand(EffectContext.from_rules_port(g, session, _resolve('_db'), handler, pl_t, ai_t, state, 'move_deck_card_to_hand', ability=None), chosen_uid, owner_id)
    remaining = [cu for cu in revealed if cu != chosen_uid]
    if remaining:
        from pvp_db import db_randomly_insert_deck_cards
        db_randomly_insert_deck_cards(session.session_id, owner_id, remaining, connection=_resolve('_db'))
    handler._hide_candidates_to_deck(g, session, pl_t, ai_t, remaining)
    _resolve('_pvp_send_same_events')(session, g, pl_t, ai_t)
    _resolve('pvp_save_state')(session, state)
    log_req(f'    PvP revealed choice resolved: {hex(chosen_uid)} -> hand (pid {owner_id})')
    state['priority_pid'] = owner_id
    _resolve('pvp_save_state')(session, state)
    chooser = _resolve('player_handlers').get(owner_id)
    if chooser:
        g2 = _ge.Game(int(session.session_id), pl_t, ai_t)
        _resolve('_pvp_populate_game_state')(g2, state, owner_id, opp_pid)
        g2.push_green_light(pl_t, _ge.EPriorityContext.Normal)
        _resolve('_send_pvp_packet')(chooser, session, g2, pl_t, 'greenlight-after-revealed-choice')
    if state.get('phase') in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
        _resolve('pvp_push_main_phase_options')(session, state)
    return True


def _pvp_resolve_shard_choice(handler, session, inner_bytes, my_pid, typed_payload=None):
    """Resolve the PvP Shards of Fate deck choice.

    The selected Standard resource remains in the deck; only its threshold is
    granted.  The candidate cards are then hidden back into the deck for both
    clients and the turn player's normal priority is restored.
    """
    import struct
    state = _resolve('pvp_load_state')(session) or {}
    pend = state.pop('pending_deck_search', None)
    if not pend:
        return False
    chosen_uid = None
    if isinstance(inner_bytes, bytes):
        for m_du in re.finditer(b'm_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});', inner_bytes):
            try:
                uid64 = struct.unpack('<Q', bytes.fromhex(m_du.group(1).decode()))[0]
                if uid64 & 255 == 1:
                    chosen_uid = int(uid64)
            except Exception:
                continue
    typed_uid = _pvp_typed_picker_uid(typed_payload)
    if typed_uid is not None:
        chosen_uid = typed_uid
    candidates = [int(c) for c in pend.get('candidates') or []]
    if not chosen_uid or chosen_uid not in candidates:
        _resolve('pvp_save_state')(session, state)
        log_req(f'    PvP Shards of Fate invalid choice: chosen={chosen_uid} candidates={candidates}')
        return True
    owner_id = int(pend['owner_id'])
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2 or owner_id not in pids:
        _resolve('pvp_save_state')(session, state)
        return True
    opp_pid = [p for p in pids if p != owner_id][0]
    pl_t = _ge.UID.make(244, owner_id)
    ai_t = _ge.UID.make(244, opp_pid)
    g = _ge.Game(int(session.session_id), pl_t, ai_t)
    _resolve('_pvp_populate_game_state')(g, state, owner_id, opp_pid)
    from rules_port.resources import card_template_threshold_flags
    provided = card_template_threshold_flags(_resolve('_db'), session.session_id, int(chosen_uid))
    threshold_colors = sorted({int(flag) for flag in _ge.SHARD_TO_FLAG.values() if provided & int(flag)})
    from pvp_db import db_randomly_insert_deck_cards
    db_randomly_insert_deck_cards(session.session_id, owner_id, pend.get('candidates') or [])
    thresh_key = f'thresh_{owner_id}'
    thresh = dict(state.get(thresh_key) or {})
    for flag in threshold_colors:
        cur = thresh.get(flag)
        if cur is None:
            cur = thresh.get(str(flag), 0)
        thresh[flag] = int(cur or 0) + 1
        state[thresh_key] = thresh
        g.player_threshold = dict(thresh)
        ev_th = _ge.PlayerResourceThresholdChangedSessionEventArgs()
        ev_th.player_id = pl_t
        ev_th.color = flag
        ev_th.operation = 1
        ev_th.delta = 1
        ev_th.new_value = int(thresh[flag])
        g._push(ev_th)
        threshold_trigger_game = _pvp_gain_threshold_trigger_game(handler, session, state, owner_id, flag)
        if threshold_trigger_game:
            for trigger_event in threshold_trigger_game.events:
                g._push(trigger_event)
    if threshold_colors:
        state[thresh_key] = thresh
        g.player_threshold = dict(thresh)
    handler._hide_candidates_to_deck(g, session, pl_t, ai_t, candidates)
    champ_map = state.get('champ_map', {})
    for target_pid in pids:
        target_uid = _ge.UID.make(244, target_pid)
        cu = int(champ_map.get(str(target_pid), 0))
        champ_scid = _ge.SessionCardId(_ge.UID(cu)) if cu else None
        g.push_player_updated(target_uid, champ_id=champ_scid)
    pending_charge_amount = _take_pending_gain_charge_amount(state, owner_id)
    if pending_charge_amount:
        charge_trigger_game = _resolve('_pvp_gain_charge_trigger_game')(handler, session, state, owner_id, amount=pending_charge_amount)
        if charge_trigger_game:
            for trigger_event in charge_trigger_game.events:
                g._push(trigger_event)
    _resolve('_pvp_send_same_events')(session, g, pl_t, ai_t)
    _resolve('pvp_save_state')(session, state)
    log_req(f'    PvP Shards of Fate resolved: gained threshold flags {threshold_colors} (chosen {hex(int(chosen_uid))}, pid {owner_id})')
    if state.get('stack'):
        _resolve('_pvp_offer_trigger_response')(session, state, owner_id)
        return True
    state['priority_pid'] = owner_id
    _resolve('pvp_save_state')(session, state)
    turn_h = _resolve('player_handlers').get(owner_id)
    if turn_h:
        g2 = _ge.Game(int(session.session_id), pl_t, ai_t)
        _resolve('_pvp_populate_game_state')(g2, state, owner_id, opp_pid)
        g2.push_green_light(pl_t, _ge.EPriorityContext.Normal)
        _resolve('_send_pvp_packet')(turn_h, session, g2, pl_t, 'greenlight-after-shard')
    if state.get('phase') in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
        _resolve('pvp_push_main_phase_options')(session, state)
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


def attach_pvp_rules_port(handler, session, game, state):
    """Attach the generic RulesPort scheduler to a two-human PvP session.

    The tournament service remains a projection adapter for the established
    PvP wire/database shape.  Transaction classification, requirements, and
    scheduler ordering are supplied by ``PvpAuthoritativeSession``; the
    adapter callbacks below only apply accepted mutations and publish events.
    """
    if not session:
        return None
    authoritative = _resolve('pvp_load_state')(session)
    if isinstance(authoritative, dict) and authoritative.get('pvp'):
        state = authoritative
    if not isinstance(state, dict) or not state.get('pvp'):
        return None
    cached = _resolve('pvp_shared_port')(session) or getattr(session, '_rules_port_session', None)
    if cached is not None:
        session._rules_port_session = cached
        _resolve('set_pvp_shared_port')(session, cached)
        live_state = _resolve('pvp_load_state')(session) or state
        live_state['_rules_port_attached'] = True
        session._rules_port_battle_state = live_state
        cached._pvp_state = live_state
        cached._pvp_current_handler = handler
        cached._pvp_current_session = session
        sync_out = getattr(cached, 'sync_to_pvp_state', None)
        if callable(sync_out):
            sync_out(live_state)
        sink = getattr(cached, 'event_sink', None)
        if sink is not None:
            sink.game = game
        _wire_pvp_state_based_handler(cached, handler, session, game)
        facts = getattr(cached, 'runtime_facts', None)
        if facts is not None:
            facts.battle_state = live_state
            facts.client_player_uid = game.player_uid
            try:
                facts.player_owner_id = int(handler.client_reck_id)
                facts.ai_owner_id = int(next((pid for pid in _resolve('db_game_session_pids')(session.session_id) if int(pid) != int(handler.client_reck_id))))
            except (AttributeError, StopIteration, TypeError, ValueError):
                pass
        snapshot = getattr(cached, 'snapshot_store', None)
        if snapshot is not None:
            snapshot.game_session = session
        return cached
    from rules_port import GameEngineEventSink, PvpAuthoritativeSession, SQLiteRulesSnapshot, attach_pvp_runtime_facts
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        pids = _resolve('_pvp_session_pids')(session, state)
    if len(pids) < 2:
        return None
    player_ids = tuple((_ge.UID.make(244, pid) for pid in pids[:2]))
    state['_rules_port_attached'] = True
    session._rules_port_battle_state = state
    port = PvpAuthoritativeSession(session.session_id, player_ids, seed_z=int(getattr(session, 'seed_z', 22222)), seed_w=int(getattr(session, 'seed_w', 11111)), event_sink=GameEngineEventSink(game), snapshot=SQLiteRulesSnapshot(session))
    port.restore_snapshot(port.snapshot_store.load())
    port.sync_from_pvp_state(state)
    priority_uid = port._uid_for_raw_player(state.get('priority_pid'))
    active_uid = port._uid_for_raw_player(state.get('turn_pid'))
    if priority_uid is not None and active_uid is not None:
        port.sync_checkpoint(phases=[port.current_turn_phase], phase_idx=0, active_player_id=active_uid, client_player_id=priority_uid, ensure_main_priority=True, ensure_current_priority=True)
    facts = attach_pvp_runtime_facts(port, session, state, player_uid=game.player_uid, ai_uid=game.ai_uid)
    facts.client_player_uid = game.player_uid
    facts.player_owner_id = int(handler.client_reck_id)
    facts.ai_owner_id = int(next((pid for pid in pids if int(pid) != int(handler.client_reck_id))))

    def current_handler():
        return getattr(port, '_pvp_current_handler', None) or getattr(session, '_rules_port_dispatch_handler', None) or handler

    def raw_transaction():
        command = getattr(session, '_rules_port_dispatch_command', None)
        return getattr(command, 'inner_bytes', b'')

    def pvp_projection(_kind, _transaction):
        return _resolve('project_accepted_pvp_transaction')(current_handler(), session, _kind, _transaction, port=port)

    def native_activation_resolver(ability):
        """Resolve simple manual abilities through the native port lifecycle.

        The PvP checkpoint uses raw participant ids, while the RulesPort
        transaction uses typed ServicePlayer ids.  Keep that translation at
        this mode boundary and let the shared native effect dispatcher own the
        actual BOM traversal.
        """
        from rules_port.resolution import resolve_port_ability
        owner_id = int(getattr(ability, 'metadata', ability).owner_id)
        other_id = next((int(pid) for pid in pids if int(pid) != owner_id), owner_id)
        live = _resolve('pvp_load_state')(session) or {}
        view = _resolve('_pvp_fra_view')(live, owner_id, other_id)
        player_uid = _ge.UID.make(244, owner_id)
        opponent_uid = _ge.UID.make(244, other_id)
        game = _ge.Game(int(session.session_id), player_uid, opponent_uid)
        _resolve('_pvp_populate_game_state')(game, live, owner_id, other_id)
        cost_selections = live.pop('_rules_port_pvp_cost_selections', ())
        if cost_selections:
            _pvp_apply_card_play_costs(current_handler(), game, session, live, player_uid, opponent_uid, cost_selections, int(ability.source_uid))
        exhausted = live.pop('_rules_port_pvp_exhausted', ())
        if exhausted:
            source_uid = int(ability.source_uid)
            row = db_card_basic(session.session_id, source_uid, conn=_resolve('_db'))
            if row:
                scid = _ge.SessionCardId(_ge.UID(source_uid))
                _tpl, ctype, _name, cost, attack, defense, gems = current_handler()._card_full_data(game, scid, row[0])
                game.push_card_updated(scid, player_uid, _ge.ECardCollections.Warzone, ctype, template_id=_tpl, state=int(_resolve('db_card_state_value')(session.session_id, source_uid, conn=_resolve('_db')) or _ge.ECardStates.Tapped), cost=cost, attack=attack, defense=defense, gems=gems)
        event_sink = port.event_sink
        if event_sink is not None:
            event_sink.game = game
        result = resolve_port_ability(current_handler(), game, session, _resolve('_db'), player_uid, opponent_uid, view, ability.ability_template_id, int(ability.source_uid), owner_id, target_map=dict(getattr(ability.activation, 'target_map', {}) or {}), variables=dict(getattr(ability.activation, 'variables', {}) or {}), cost_target_map=dict(getattr(ability.activation, 'cost_target_map', {}) or {}), instance_id=int(ability.instance_id))
        live['stack'] = view.get('stack') or []
        live['stack_passed'] = []
        _resolve('_pvp_sync_view_to_state')(live, view, owner_id, other_id)
        _resolve('pvp_save_state')(session, live)
        _resolve('_pvp_send_same_events')(session, game, player_uid, opponent_uid)
        session._rules_port_mutation_emitted = True
        return result

    def native_chain_resolver(ability):
        from rules_port.pvp_session import ProjectedChainAbility
        if not isinstance(ability, ProjectedChainAbility):
            return native_activation_resolver(ability)
        state = _resolve('pvp_load_state')(session) or {}
        descriptor = dict(ability.descriptor)
        owner_id = _resolve('_raw_player_id')(ability.owner_id)
        if int(state.get('completed_chain_instance_id', 0) or 0) == int(ability.instance_id):
            from rules_port.actions import AbilityResolutionState
            from rules_port.chain_items import resolve_chain_item
            pids = _resolve('db_game_session_pids')(session.session_id)
            if len(pids) < 2:
                pids = _resolve('_pvp_session_pids')(session, state)
            if len(pids) < 2:
                return AbilityResolutionState.COMPLETED
            opponent_id = next((pid for pid in pids if int(pid) != int(owner_id)))
            host = _resolve('_PvpChainHost')(current_handler(), session, state, owner_id, opponent_id)
            resolution = resolve_chain_item(host, port, session, _resolve('_db'), ability, _ge.UID.make(244, int(owner_id)), _ge.UID.make(244, int(opponent_id)))
            _resolve('pvp_save_state')(session, state)
            session._rules_port_mutation_emitted = True
            return resolution
        stack = state.setdefault('stack', [])
        if not stack or int(stack[-1].get('instance_id', -1)) != int(descriptor.get('instance_id', -2)):
            stack.append(descriptor)
        if descriptor.get('kind') == 'ability':
            _resolve('_pvp_resolve_ability_item')(session, state, current_handler(), owner_id, descriptor)
        else:
            from rules_port.actions import AbilityResolutionState
            from rules_port.chain_items import resolve_chain_item
            pids = _resolve('db_game_session_pids')(session.session_id)
            if len(pids) < 2:
                pids = _resolve('_pvp_session_pids')(session, state)
            if len(pids) < 2:
                session._rules_port_mutation_emitted = True
                return AbilityResolutionState.COMPLETED
            opponent_id = next((pid for pid in pids if int(pid) != int(owner_id)))
            host = _resolve('_PvpChainHost')(current_handler(), session, state, owner_id, opponent_id)
            resolution = resolve_chain_item(host, port, session, _resolve('_db'), ability, _ge.UID.make(244, int(owner_id)), _ge.UID.make(244, int(opponent_id)))
            session._rules_port_mutation_emitted = True
            if resolution is not AbilityResolutionState.COMPLETED:
                return resolution
            state = host.state
        _resolve('pvp_save_state')(session, state)
        from rules_port.actions import AbilityResolutionState
        if not bool(getattr(ability, 'ignores_chain', False)) and any((state.get(key) for key in ('pending_choice', 'pending_trigger', 'pending_deck_search', 'pending_conversation', 'pending_discard_ability', 'resolution_paused'))):
            if str(descriptor.get('kind') or '') == 'ability':
                state['paused_chain_instance_id'] = int(ability.instance_id)
                _resolve('pvp_save_state')(session, state)
            session._rules_port_mutation_emitted = True
            return AbilityResolutionState.WAITING_FOR_INPUT
        port.forget_projected_chain(int(ability.instance_id))
        remaining = state.get('stack') or []
        if remaining and (not any((state.get(key) for key in ('pending_choice', 'pending_deck_search', 'pending_trigger', 'pending_discard_ability')))):
            pids = _resolve('db_game_session_pids')(session.session_id)
            if len(pids) < 2:
                pids = _resolve('_pvp_session_pids')(session, state)
            next_item = remaining[-1]
            next_source = int(next_item.get('source_uid') or 0)
            next_owner = owner_id
            if next_source:
                next_row = db_card_basic(session.session_id, next_source, conn=_resolve('_db'))
                if next_row:
                    next_owner = int(next_row[1])
            next_other = next((int(pid) for pid in pids if int(pid) != next_owner), next_owner)
            port.queue_projected_chain(next_item, next_owner, first_player_id=_ge.UID.make(244, next_other))
        session._rules_port_mutation_emitted = True
        return AbilityResolutionState.COMPLETED

    def native_activation_cost_payer(ability):
        """Apply the numeric portion of a simple native ability cost."""
        from rules_port.costs import ability_cost_targets, plan_ability_cost, validate_cost_target_selection
        from rules_port.resources import pay_resource_for_player
        metadata = getattr(ability, 'metadata', ability)
        ability_guid = getattr(metadata, 'ability_template_id', None) or getattr(metadata, 'ability_guid', None) or getattr(metadata, 'runtime_ability_guid', '')
        owner_id = int(getattr(metadata, 'owner_id', 0) or 0)
        state = _resolve('pvp_load_state')(session) or {}
        graph = getattr(metadata, 'graph', None)
        cost_selections = []
        if graph is not None:
            costs_by_index = {int(cost.index): cost for cost in ability_cost_targets(graph, _resolve('_db'), session.session_id, owner_id, int(getattr(metadata, 'source_uid', 0) or 0), battle_state=state)}
            activation_data = getattr(metadata, 'activation', None)
            cost_map = getattr(activation_data, 'cost_target_map', {}) or {}
            for index, cost in costs_by_index.items():
                selected = cost_map.get(index, cost_map.get(str(index), ()))
                if cost.is_source_auto_target and (not selected):
                    selected = (int(metadata.source_uid),)
                selected = validate_cost_target_selection(_resolve('_db'), session.session_id, owner_id, int(getattr(metadata, 'source_uid', 0) or 0), cost, selected, battle_state=state)
                if selected is None:
                    return False
                if selected:
                    from rules_port.costs import cost_type_for_kind
                    cost_selections.append(({'kind': cost.kind, 'minimum': cost.minimum, 'maximum': cost.maximum, 'cost_type': cost_type_for_kind(cost.kind), 'auto': cost.is_source_auto_target}, selected))
        costs = getattr(metadata, 'costs', None)
        activation = getattr(metadata, 'activation', None)
        current = int(state.get(f'res_{owner_id}', 0) or 0)
        plan = plan_ability_cost(costs, activation, current_resource=current, charges=int(state.get(f'chg_{owner_id}', 0) or 0), spell_points=int(state.get(f'sp_{owner_id}', 0) or 0), health=int(state.get(f'hp_{owner_id}', 20) or 0), spell_uses=state.get(f'sp_uses_{owner_id}', {}) or {}, ability_key=str(ability_guid))
        if plan is None:
            return False
        from rules_port.costs import apply_source_counter_cost_plan, plan_source_counter_costs
        source_uid = getattr(metadata, 'source_uid', None)
        counter_cost_plan = plan_source_counter_costs(_resolve('_db'), session.session_id, source_uid, costs, state)
        if counter_cost_plan is None:
            return False
        if plan.resource:
            pay_resource_for_player(state, owner_id, int(plan.resource))
        state[f'chg_{owner_id}'] = max(0, int(state.get(f'chg_{owner_id}', 0) or 0) - int(plan.charge_points))
        state[f'sp_{owner_id}'] = max(0, int(state.get(f'sp_{owner_id}', 0) or 0) - int(plan.spell_points))
        if plan.life:
            state[f'hp_{owner_id}'] = max(0, int(state.get(f'hp_{owner_id}', 20) or 0) - int(plan.life))
        if plan.resource or plan.charge_points or plan.spell_points or plan.life:
            state.setdefault('_pvp_paid_costs', []).append({'owner_id': int(owner_id), 'resource': int(plan.resource or 0), 'charge': int(plan.charge_points or 0), 'spell': int(plan.spell_points or 0), 'life': int(plan.life or 0), 'res_new': int(state.get(f'res_{owner_id}', 0) or 0), 'chg_new': int(state.get(f'chg_{owner_id}', 0) or 0), 'sp_new': int(state.get(f'sp_{owner_id}', 0) or 0), 'hp_new': int(state.get(f'hp_{owner_id}', 20) or 0)})
        if bool(getattr(costs, 'exhausts_card_on_use', False)):
            exhaust_source_uid = int(getattr(metadata, 'source_uid', 0) or 0)
            _resolve('db_set_card_state_or')(session.session_id, exhaust_source_uid, _ge.ECardStates.Tapped)
            state.setdefault('_rules_port_pvp_exhausted', []).append(exhaust_source_uid)
        if counter_cost_plan:
            counter_changes = apply_source_counter_cost_plan(_resolve('_db'), session.session_id, int(source_uid), counter_cost_plan, state, ability_instance_id=getattr(ability, 'instance_id', None))
            if counter_changes:
                state.setdefault('_pvp_paid_counter_costs', []).extend([{'source_uid': int(change['source_uid']), 'counter_guid': str(change['counter_guid']), 'old': int(change['old']), 'new': int(change['new']), 'owner_id': int(owner_id)} for change in counter_changes])
        _resolve('pvp_save_state')(session, state)
        if cost_selections:
            other_id = next((int(pid) for pid in pids if int(pid) != owner_id), owner_id)
            payer_uid = _ge.UID.make(244, owner_id)
            other_uid = _ge.UID.make(244, other_id)
            cost_game = _ge.Game(int(session.session_id), payer_uid, other_uid)
            _resolve('_pvp_populate_game_state')(cost_game, state, owner_id, other_id)
            _pvp_apply_card_play_costs(current_handler(), cost_game, session, state, payer_uid, other_uid, cost_selections, int(getattr(metadata, 'source_uid', 0) or 0))
            _resolve('pvp_save_state')(session, state)
            if cost_game.events:
                _resolve('_pvp_send_same_events')(session, cost_game, payer_uid, other_uid)
        return True

    def native_card_projection(kind, transaction):
        if kind != 'activate_ability':
            return pvp_projection(kind, transaction)
        return False

    def publish_card_activated(ability, transaction):
        sink = getattr(port, 'event_sink', None)
        if sink is None:
            return False
        raw_player = getattr(transaction, 'player_id', 0)
        raw_player = int(getattr(raw_player, 'uid64', raw_player) or 0)
        owner_id = raw_player >> 8 if raw_player & 255 == 244 else raw_player
        pids = _resolve('db_game_session_pids')(session.session_id)
        opponent_id = next((int(pid) for pid in pids if int(pid) != int(owner_id)), None)
        if opponent_id is None:
            return False
        state = _resolve('pvp_load_state')(session) or {}
        from rules_port.triggers import dispatch_card_activated

        def publish(**event):
            data = dict(event.pop('data', {}) or {})
            return _resolve('_pvp_dispatch_triggers')(handler, event['game'], session, state, event['player_uid'], event['ai_uid'], event['event_type'], event['source_card_id'], source_owner_id=event['source_player_id'], target_card_id=event.get('target_card_id'), **data)
        try:
            result = dispatch_card_activated(db=_resolve('_db'), handler=handler, game=sink.game, session=session, player_uid=sink.game.player_uid, ai_uid=sink.game.ai_uid, battle_state=state, ability_guid=getattr(ability, 'ability_template_id', ''), source_card_id=int(getattr(ability, 'source_uid', 0) or 0), source_player_id=int(owner_id), ability_instance_id=int(ability.instance_id), activation_data=ability.activation.as_dict(), dispatcher=publish)
            _resolve('pvp_save_state')(session, state)
            return result is not False
        except Exception as exc:
            log_req(f'    PvP CardActivatedEvent failed: {exc}')
            return False
    from rules_port.card_transactions import MetadataCardTransactionExecutor
    from gamedata import ability_graph as _ability_graph
    native_executor = MetadataCardTransactionExecutor(port, graph_loader=lambda guid: _ability_graph(_RECORD_STORE, str(guid).lower()), owner_id=0, owner_id_resolver=lambda tx: int(getattr(tx.player_id, 'uid64', tx.player_id)) >> 8 if int(getattr(tx.player_id, 'uid64', tx.player_id)) & 255 == 244 else int(getattr(tx.player_id, 'uid64', tx.player_id)), projection=native_card_projection, activation_event=publish_card_activated, play_plan_loader=lambda template_guid, source_uid, owner_id: _resolve('PlayPlan').from_card(_RECORD_STORE, template_guid, source_uid=source_uid, owner_id=owner_id))
    port.set_ability_resolver(native_chain_resolver)
    port.set_ability_cost_payer(native_activation_cost_payer)

    def pvp_pass(_transaction):
        from rules_port.kernel import PriorityWindowAction
        live = _resolve('pvp_load_state')(session) or {}
        if any((live.get(key) for key in ('pending_choice', 'pending_deck_search', 'pending_trigger', 'pending_conversation', 'pending_discard_ability'))):
            log_req('    PvP pass ignored while an interactive prompt is pending')
            return True
        previous_phase = int(live.get('phase', 0) or 0)
        previous_priority = int(live.get('priority_pid', 0) or 0)
        if port.action_stack.peek() is None and live.get('stack') and getattr(port, 'rehydrate_projected_chain', None):
            port.rehydrate_projected_chain()
        if isinstance(port.action_stack.peek(), PriorityWindowAction):
            native_priority_action = port.action_stack.peek()
            player_id = getattr(_transaction, 'player_id', None)
            live_phase = int(live.get('phase', 0) or 0)
            if live_phase == int(_ge.ETurnPhases.FirstMainPhase):
                has_attackers = pvp_turn_has_attackers(session, int(live.get('turn_pid', 0) or 0))
                port.has_legal_attackers = bool(has_attackers)
                port.active_player_skips_attack = not bool(has_attackers)
            raw_player = _uid_int(player_id)
            pass_pid = raw_player >> 8 if raw_player & 255 == 244 else raw_player
            responding_to_chain = getattr(native_priority_action, 'ability_responding_to', None) is not None
            pass_key = 'stack_passed' if responding_to_chain else 'passes'
            passed = set((int(value) for value in live.get(pass_key) or ()))
            passed.add(pass_pid)
            live[pass_key] = sorted(passed)
            if not port.pass_priority_and_drive(player_id):
                return False
            port.sync_to_pvp_state(live)
            _resolve('pvp_save_state')(session, live)
            try:
                port.persist()
            except Exception as exc:
                log_req(f'    PvP RulesPort post-pass persistence failed: {exc}')
            if any((live.get(key) for key in ('pending_choice', 'pending_deck_search', 'pending_trigger', 'pending_conversation', 'pending_discard_ability'))):
                _resolve('pvp_save_state')(session, live)
                log_req('    PvP pass paused for pending input; priority handoff skipped')
                return True
            next_priority = int(live.get('priority_pid', 0) or 0)
            current_phase = int(live.get('phase', 0) or 0)
            if next_priority and current_phase == previous_phase and (next_priority != previous_priority):
                pids_live = _resolve('db_game_session_pids')(session.session_id)
                for target_pid in pids_live:
                    target_h = _resolve('player_handlers').get(int(target_pid))
                    if not target_h:
                        continue
                    other_pid = next((int(value) for value in pids_live if int(value) != int(target_pid)), int(target_pid))
                    target_uid = _ge.UID.make(244, int(target_pid))
                    other_uid = _ge.UID.make(244, other_pid)
                    handoff = _ge.Game(int(session.session_id), target_uid, other_uid)
                    _resolve('_pvp_populate_game_state')(handoff, live, int(target_pid), other_pid)
                    handoff.push_green_light(_ge.UID.make(244, next_priority), _ge.EPriorityContext.Normal)
                    _pvp_push_turn_phase_with_elapsed(handoff, current_phase, _ge.UID.make(244, int(live.get('turn_pid') or next_priority)), _ge.UID.make(244, next_priority), _resolve('priority_elapsed_ticks')(live, next_priority) // 10000000)
                    _resolve('_send_pvp_packet')(target_h, session, handoff, target_uid, 'rules-port-priority-handoff')
                _resolve('pvp_push_phase_options')(session, live, pid=next_priority)
                log_req(f'    PvP RulesPort priority handoff: {previous_priority} -> {next_priority} phase={current_phase}')
            return True
        log_req('    PvP RulesPort rejected pass: native priority action missing')
        return False

    def native_phase_entry(phase):
        """Project a native RulesPort phase entry to the PvP wire state.

        ``_pvp_run_phase_start`` remains a packet/state projection: the
        native phase state has already selected the transition and will create
        the priority action immediately after this callback returns.  It no
        longer owns phase progression or pass handling.
        """
        live = _resolve('pvp_load_state')(session) or state
        turn_pid = int(live.get('turn_pid') or 0)
        if not turn_pid:
            return False
        pids_live = _resolve('db_game_session_pids')(session.session_id)
        if len(pids_live) < 2:
            return False
        _resolve('port_enter_phase')(live, phase)
        defender = next((int(pid) for pid in pids_live if int(pid) != turn_pid), turn_pid)
        phase_priority = port.priority_players_for_phase(live, int(phase), turn_pid, defender)
        if phase_priority.name == 'NONE':
            live.pop('priority_pid', None)
        else:
            live['priority_pid'] = defender if phase == _ge.ETurnPhases.DeclareDefense else turn_pid
        if phase == _ge.ETurnPhases.DeclareDefensePriorityWindow and live.get('attackers') and (not live.get('blockers')) and (_pvp_defender_blockable_count(session, live) <= 0):
            pvp_push_empty_blockers(session, live)
            live['blockers'] = {str(attacker): [] for attacker in live.get('attackers') or {}}
        if phase == _ge.ETurnPhases.Discard:
            try:
                live['discard_required'] = _resolve('db_hand_count')(session.session_id, turn_pid, conn=_resolve('_db')) > _resolve('DEFAULT_MAX_HAND_SIZE')
            except (TypeError, ValueError):
                live['discard_required'] = False
        else:
            live.pop('discard_required', None)
        live['_rules_port_attached'] = True
        try:
            port.active_player_skips_attack = not pvp_turn_has_attackers(session, turn_pid)
        except Exception:
            port.active_player_skips_attack = False
        port.active_player_skips_draw = _resolve('port_skips_draw_phase')(live)
        _resolve('pvp_save_state')(session, live)
        _resolve('_pvp_run_phase_start')(session, live, phase)
        session._rules_port_mutation_emitted = True
        return True

    def native_phase_exit(phase):
        """Publish C# combat completion when its native phase exits."""
        if phase == _ge.ETurnPhases.FirstStrikePriorityWindow:
            if port.combat_has_standard_damage:
                return None
        elif phase != _ge.ETurnPhases.AssignDamage:
            return None
        live = _resolve('pvp_load_state')(session) or state
        turn_pid = int(live.get('turn_pid') or 0)
        pids_live = _resolve('db_game_session_pids')(session.session_id)
        other_pid = next((int(pid) for pid in pids_live if int(pid) != turn_pid), None)
        if not turn_pid or other_pid is None:
            return None
        sink = getattr(port, 'event_sink', None)
        projection = getattr(sink, 'game', None)
        if projection is None:
            projection = _ge.Game(int(session.session_id), _ge.UID.make(244, turn_pid), _ge.UID.make(244, other_pid))
            _resolve('_pvp_populate_game_state')(projection, live, turn_pid, other_pid)
            if sink is not None:
                sink.game = projection
        view = _resolve('_pvp_fra_view')(live, turn_pid, other_pid)
        _resolve('_pvp_dispatch_triggers')(current_handler(), projection, session, view, projection.player_uid, projection.ai_uid, 'CombatEndedEvent', None, turn_pid)
        _resolve('_pvp_sync_view_to_state')(live, view, turn_pid, other_pid)
        _resolve('pvp_save_state')(session, live)
        _resolve('_pvp_send_same_events')(session, projection, projection.player_uid, projection.ai_uid)
        session._rules_port_mutation_emitted = True
        return None

    def native_turn_boundary(active_player_id):
        """Persist the RulesPort's completed EndTurn rotation for PvP."""
        live = _resolve('pvp_load_state')(session) or state
        try:
            raw = int(getattr(active_player_id, 'uid64', active_player_id))
            next_pid = raw >> 8 if raw & 255 == 244 else raw
        except (TypeError, ValueError):
            return False
        if not next_pid:
            return False
        pids_live = _resolve('db_game_session_pids')(session.session_id)
        bonus_pid = int(live.get('bonus_turn_pid', 0) or 0)
        if bonus_pid in {int(pid) for pid in pids_live}:
            next_pid = bonus_pid
        boundary = _resolve('port_advance_turn_state')(live, pids_live, incoming_player_id=next_pid)
        _resolve('pvp_save_state')(session, live)
        log_req(f"    PvP RulesPort turn boundary: next turn player {boundary['turn_pid']}" + (' (bonus)' if boundary['bonus_used'] else ''))
        return _ge.UID.make(244, int(boundary['turn_pid']))

    def pvp_hand(kind, transaction):
        current = current_handler()
        if kind == 'accept_starting_hand':
            return bool(current._handle_mulligan_keep_transaction(session, getattr(session, '_rules_port_dispatch_command', None)))
        if kind == 'mulligan':
            return bool(current._handle_mulligan_redraw_transaction(session, getattr(session, '_rules_port_dispatch_command', None)))
        return False

    def setup_pick(transaction):
        """Project the typed Play/Draw choice into the PvP setup state."""
        original = getattr(session, '_rules_port_dispatch_command', None)
        if original is None:
            return False
        try:
            handled = bool(current_handler()._handle_choose_pick_transaction(session, original))
            if handled:
                session._rules_port_mutation_emitted = True
                _pvp_push_waiting_on(session, None)
            return handled
        except Exception as exc:
            log_req(f'    PvP setup mutation failed: {exc}')
            return False

    def cancel_auto_pass(_transaction):
        live = _resolve('pvp_load_state')(session) or {}
        pid = int(current_handler().client_reck_id)
        if int(live.get('autopass_pid', 0) or 0) != pid:
            return True
        live.pop('autopass_pid', None)
        live.pop('autopass_state', None)
        _resolve('pvp_save_state')(session, live)
        return True

    def set_stops(transaction):
        live = _resolve('pvp_load_state')(session) or state
        pid = int(current_handler().client_reck_id)
        payload = getattr(transaction, 'payload', {}) or {}
        live[f'stops_self_{pid}'] = list(payload.get('self_phases', ()))
        other = next((int(value) for value in pids if int(value) != pid), None)
        if other is not None:
            live[f'stops_opp_{other}'] = list(payload.get('opponent_phases', ()))
        _resolve('pvp_save_state')(session, live)
        return True

    def options(_transaction):
        live = _resolve('pvp_load_state')(session) or state
        phase = int(getattr(port, 'current_turn_phase', live.get('phase', 0)) or 0)
        live['phase'] = phase
        priority = getattr(port.action_stack, 'priority_player_id', None)
        if priority is not None:
            raw_priority = int(getattr(priority, 'uid64', priority))
            live['priority_pid'] = raw_priority >> 8 if raw_priority & 255 == 244 else raw_priority
        _resolve('pvp_save_state')(session, live)
        _resolve('pvp_push_current_phase_options')(session, live)
        return True
    port.set_card_transaction_resolver(native_executor)
    port.set_resource_transaction_resolver(lambda tx: pvp_projection('play_resource', tx))
    port.set_setup_transaction_resolver(setup_pick)
    port.set_priority_transaction_resolver(pvp_pass)
    port.set_hand_transaction_resolver(pvp_hand)
    port.set_auto_pass_transaction_resolver(lambda tx: _resolve('set_pvp_auto_pass')(current_handler(), session, (getattr(tx, 'payload', {}) or {}).get('passing_state', 2)))
    port.set_cancel_auto_pass_transaction_resolver(cancel_auto_pass)
    port.set_priority_sync_resolver(options)
    port.set_turn_phase_resolver(set_stops)
    port.set_turn_boundary_resolver(native_turn_boundary)
    port.set_turn_phase_entry_resolver(native_phase_entry)
    port.set_turn_phase_exit_resolver(native_phase_exit)
    port.set_discard_transaction_resolver(lambda tx: pvp_projection('discard', tx))
    port.set_discard_continuation_resolver(lambda tx: pvp_projection('discard_continuation', tx))
    port.set_triggered_ability_transaction_resolver(lambda tx: pvp_projection('triggered', tx))
    port.set_attack_transaction_resolver(lambda tx: pvp_projection('attack', tx))
    port.set_defense_transaction_resolver(lambda tx: pvp_projection('defense', tx))
    port.set_damage_transaction_resolver(lambda tx: pvp_projection('damage', tx))
    port.set_choice_transaction_resolver(lambda tx: pvp_projection('choice', tx))
    port.set_player_options_resolver(options)
    port.set_ready_card_transaction_resolver(lambda tx: pvp_projection('ready', tx))
    port.set_encounter_mod_resolver(lambda tx: pvp_projection('conversation', tx))
    port.set_quit_game_resolver(lambda _tx: bool(_resolve('pvp_concede')(current_handler(), session)))
    port.assert_projection_wiring()
    port.rehydrate_combats()
    port.rehydrate_projected_chain()
    setattr(port, '_pvp_current_handler', handler)
    setattr(port, '_pvp_current_session', session)
    setattr(port, '_pvp_state', state)
    _wire_pvp_state_based_handler(port, handler, session, game)
    session._rules_port_session = port
    _resolve('set_pvp_shared_port')(session, port)
    port.persist()
    log_req(f'    PvP RulesPort attached session={session.session_id}', level='DEBUG')
    return port


def project_accepted_pvp_transaction(handler, session, kind, transaction, *, port=None):
    """Apply the PvP wire/database projection for an accepted intent.

    RulesPort is the sole owner of transaction classification, legality,
    costs, and ordering.  This function is deliberately the only compatibility
    boundary used by the live PvP adapter; it receives a typed, already
    accepted intent and exists only to emit the historical PvP event stream.

    The implementation is kept separate from attachment so the remaining
    legacy projection can be replaced one transaction family at a time
    without reintroducing a second ingress or validation path.
    """
    payload = getattr(transaction, 'payload', {}) or {}
    raw = getattr(transaction, 'raw_transaction', None)
    if raw is None:
        raw = getattr(getattr(session, '_rules_port_dispatch_command', None), 'inner_bytes', b'')
    current = getattr(session, '_rules_port_dispatch_handler', None) or handler
    try:
        my_pid = int(current.client_reck_id)
    except (AttributeError, TypeError, ValueError):
        return False
    if kind == 'conversation':
        return bool(_resolve('_pvp_resolve_conversation')(current, session, raw, my_pid))
    if kind == 'choice':
        if (_resolve('pvp_load_state')(session) or {}).get('pending_choice'):
            return bool(_resolve('_pvp_resolve_choice')(current, session, raw, my_pid, typed_payload=payload))
        return True
    if kind == 'discard':
        live = _resolve('pvp_load_state')(session) or {}
        if live.get('pending_discard_ability'):
            return bool(_resolve('_pvp_resolve_discard_prompt')(current, session, raw, my_pid, typed_payload=payload))
        try:
            card_uid = _uid_int(payload.get('card_id'))
        except (TypeError, ValueError):
            return False
        pids = tuple((int(pid) for pid in _resolve('db_game_session_pids')(session.session_id)))
        if my_pid not in pids:
            return False
        opponent = next((pid for pid in pids if pid != my_pid), my_pid)
        from rules_port.host_mutations import discard_card_to_owner
        projected, _owner = discard_card_to_owner(current, session, _ge.UID.make(244, my_pid), _ge.UID.make(244, opponent), card_uid)
        if projected is None:
            return False
        _resolve('_pvp_send_same_events')(session, projected, _ge.UID.make(244, my_pid), _ge.UID.make(244, opponent))
        if int(live.get('phase', 0) or 0) == int(_ge.ETurnPhases.Discard):
            hand_count = _resolve('db_hand_count')(session.session_id, my_pid, conn=_resolve('_db'))
            live['discard_required'] = hand_count > _resolve('DEFAULT_MAX_HAND_SIZE')
            _resolve('pvp_save_state')(session, live)
            if live['discard_required']:
                discard_uid = _ge.UID.make(244, my_pid)
                game = _ge.Game(int(session.session_id), discard_uid, _ge.UID.make(244, opponent))
                game.push_green_light(discard_uid)
                _resolve('_send_pvp_packet')(current, session, game, discard_uid, 'discard-continue')
            elif port is not None:
                if not port.pass_priority_and_drive(transaction.player_id):
                    log_req(f'    PvP discard could not consume native priority for pid {my_pid}')
                    return False
                live = _resolve('pvp_load_state')(session) or live
                port.sync_to_pvp_state(live)
                _resolve('pvp_save_state')(session, live)
                try:
                    port.persist()
                except Exception as exc:
                    log_req(f'    PvP RulesPort post-discard persistence failed: {exc}')
        return True
    if kind == 'discard_continuation':
        return bool(_resolve('_pvp_resolve_discard_prompt')(current, session, raw, my_pid, typed_payload=payload))
    if kind == 'triggered':
        pending = _resolve('pvp_load_state')(session) or {}
        if pending.get('pending_trigger'):
            if (pending.get('pending_trigger') or {}).get('optional'):
                return bool(_resolve('_pvp_resolve_optional_trigger')(current, session, raw, my_pid, typed_payload=payload))
            return bool(_resolve('_pvp_resolve_trigger_target')(current, session, raw, my_pid, typed_payload=payload))
        search = pending.get('pending_deck_search') or {}
        search_kind = str(search.get('kind') or '')
        if search_kind in ('revealed_troop', 'revealed_card'):
            return bool(_resolve('_pvp_resolve_revealed_choice')(current, session, raw, my_pid, typed_payload=payload))
        if search_kind == 'shard':
            return bool(_resolve('_pvp_resolve_shard_choice')(current, session, raw, my_pid, typed_payload=payload))
        if search_kind == 'matching_target':
            return bool(_resolve('_pvp_resolve_matching_target')(current, session, raw, my_pid, typed_payload=payload))
        if search:
            return bool(_pvp_resolve_deck_search(current, session, raw, my_pid, typed_payload=payload))
        return True
    if kind == 'attack':
        return bool(_resolve('_pvp_declare_attackers')(current, session, raw, my_pid, declarations=payload.get('declarations')))
    if kind == 'defense':
        return bool(_resolve('_pvp_declare_blockers')(current, session, raw, my_pid, declarations=payload.get('declarations')))
    if kind == 'ready':
        return True
    if kind == 'damage':
        live = _resolve('pvp_load_state')(session) or {}
        try:
            selected = []
            for match in re.finditer(b'm_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});', raw):
                value = struct.unpack('<Q', bytes.fromhex(match.group(1).decode()))[0]
                if value & 255 == 1:
                    selected.append(int(value))
            blockers = {int(key): {int(value) for value in values} for key, values in (live.get('blockers') or {}).items()}
            order_map = {}
            for attacker, blocker_set in blockers.items():
                ordered = [uid for uid in selected if uid in blocker_set]
                ordered.extend((uid for uid in blocker_set if uid not in ordered))
                if ordered:
                    order_map[attacker] = ordered
            if order_map:
                live['damage_order'] = {str(key): [str(value) for value in values] for key, values in order_map.items()}
            phase = int(live.get('phase', 0) or 0)
            if phase in (_ge.ETurnPhases.AssignFirstStrikeDamage, _ge.ETurnPhases.AssignDamage):
                _pvp_resolve_combat(session, live, first_strike=phase == _ge.ETurnPhases.AssignFirstStrikeDamage)
                if str(getattr(session, 'state', '') or '').lower() != 'ended':
                    if port is not None:
                        player_id = getattr(transaction, 'player_id', None)
                        if not port.pass_priority_and_drive(player_id):
                            log_req(f'    PvP AssignDamageOrder could not consume native priority: phase={phase} player={player_id!r}')
                            return False
                        live = _resolve('pvp_load_state')(session) or live
                        port.sync_to_pvp_state(live)
                        _resolve('pvp_save_state')(session, live)
                        try:
                            port.persist()
                        except Exception as exc:
                            log_req(f'    PvP RulesPort post-damage persistence failed: {exc}')
                    else:
                        _pvp_advance_from_damage_step(session, live, phase)
            _resolve('pvp_save_state')(session, live)
            return True
        except (TypeError, ValueError, struct.error) as exc:
            log_req(f'    PvP AssignDamageOrder projection error: {exc}')
            return False
    if kind == 'play_resource':
        try:
            card_uid = _uid_int(payload.get('card_id'))
        except (TypeError, ValueError):
            return False
        pids = _resolve('db_game_session_pids')(session.session_id)
        if len(pids) < 2 or my_pid not in [int(pid) for pid in pids]:
            return False
        crow = _resolve('db_card_play_info')(session.session_id, card_uid, conn=_resolve('_db'))
        if not crow or str(crow[1] or '') != 'Resource':
            return False
        opponent = next((int(pid) for pid in pids if int(pid) != my_pid))
        return bool(_resolve('_pvp_project_resource_play')(current, session, raw, my_pid, card_uid, crow, crow[2], tuple((int(pid) for pid in pids)), _ge.UID.make(244, my_pid), _ge.UID.make(244, opponent)))
    card_uid = payload.get('card_id')
    if kind in {'play_troop', 'play_artifact', 'play_spell', 'play_champion'} and card_uid is not None:
        try:
            card_uid = int(getattr(card_uid, 'uid64', card_uid))
            row = _resolve('db_card_play_info')(session.session_id, card_uid, conn=_resolve('_db'))
            if row:
                card_type = str(row[1] or '')
                if kind == 'play_spell' or any((name in card_type for name in ('BasicAction', 'QuickAction'))):
                    return bool(_resolve('_pvp_play_spell')(current, session, card_uid, int(current.client_reck_id), raw, typed_payload=payload, native_port=port))
                return bool(_resolve('_pvp_play_troop')(current, session, card_uid, int(current.client_reck_id), raw, typed_payload=payload, native_port=port))
        except (TypeError, ValueError):
            return False
    log_req(f'    PvP RulesPort projection: unsupported kind={kind}')
    return False


def pvp_advance_past_non_stops(session, state):
    """Auto-advance the PvP phase one step at a time (pushing each phase and
    running its start-of-phase logic — Prep resource, Draw) until the next
    phase that is a STOP for either player, at which point the both-pass
    cycle takes over.

    Non-interactive phases like Ready/Prep/Draw are marched through
    server-side (neither client auto-passes them and the opponent can't pass
    without a priority handoff), but a phase is NEVER auto-passed if the turn
    player has a self-stop on it or the opponent has an opponent-stop on it —
    the player's configured stops are respected.  Returns True if it advanced
    at least one phase."""
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return False
    turn_pid = state.get('turn_pid')
    opp_pid = pids[0] if pids[1] == turn_pid else pids[1]
    if _resolve('_pvp_auto_pass_opponent_stop')(session, state, turn_pid, opp_pid):
        return True
    if pvp_phase_is_stop(state, int(state.get('phase', 6)), turn_pid, opp_pid):
        log_req(f"    PvP auto-advance: phase {state.get('phase')} is a player stop — not auto-passing")
        return False
    has_ready = pvp_turn_has_attackers(session, turn_pid)
    phase_list = _resolve('_pvp_turn_phase_list')(state, turn_pid, has_ready)
    try:
        cur = phase_list.index(int(state.get('phase', 6)))
    except ValueError:
        cur = 0
    advanced = False
    while True:
        cur += 1
        if cur >= len(phase_list):
            log_req('    PvP advance: reached the end of the phase list')
            return advanced
        new_phase = phase_list[cur]
        _resolve('port_enter_phase')(state, new_phase)
        _resolve('pvp_save_state')(session, state)
        log_req(f'    PvP auto-advance: phase {new_phase} to both')
        _resolve('_pvp_run_phase_start')(session, state, new_phase)
        advanced = True
        if _resolve('_pvp_auto_pass_opponent_stop')(session, state, turn_pid, opp_pid):
            return advanced
        if pvp_phase_is_stop(state, new_phase, turn_pid, opp_pid):
            log_req(f'    PvP auto-advance: stopped at phase {new_phase} (player stop)')
            return advanced
        if new_phase == _ge.ETurnPhases.Discard:
            hand_count = _resolve('db_hand_count')(session.session_id, turn_pid, conn=_resolve('_db'))
            try:
                from rules_port.static_rules import hand_size_unlimited
                guid_map = state.setdefault('champ_guid_map', {})
                for _pid in (turn_pid, opp_pid):
                    if str(_pid) in guid_map:
                        continue
                    _row = _resolve('db_game_champion')(session.session_id, _pid)
                    if _row and _row[1]:
                        guid_map[str(_pid)] = _row[1]
                unlimited = hand_size_unlimited(_resolve('_db'), session.session_id, state)
            except Exception:
                unlimited = False
            if hand_count > _resolve('DEFAULT_MAX_HAND_SIZE') and (not unlimited):
                log_req(f"    PvP auto-advance: stopped at Discard (hand {hand_count} > {_resolve('DEFAULT_MAX_HAND_SIZE')})")
                return advanced


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


def pvp_mulligan_next(session, state, just_acted_pid):
    """After a keep, pass the active Mulligan phase to the other player.

    Redraws retain priority and do not call this function. Once both players
    have kept, the mulligan ends and RulesPort advances into the first turn.
    Returns True when the other player was prompted, False when mulligan ended.
    """
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return False
    mulligan = _resolve('port_mulligan_transition')(state, pids, just_acted_pid)
    if mulligan['action'] == 'start_turn':
        state['phase'] = int(_ge.ETurnPhases.Mulligan)
        state.pop('priority_pid', None)
        state['passes'] = []
        state.pop('mulligan_pid', None)
        _resolve('pvp_save_state')(session, state)
        native_handler = _resolve('player_handlers').get(int(pids[0]))
        if native_handler is None:
            log_req('    PvP mulligan complete but no player handler is available to attach RulesPort')
            return False
        turn_uid = _ge.UID.make(244, int(state['turn_pid']))
        opponent_pid = next((int(pid) for pid in pids if int(pid) != int(state['turn_pid'])))
        native_game = _ge.Game(int(session.session_id), turn_uid, _ge.UID.make(244, opponent_pid))
        port = _resolve('attach_pvp_rules_port')(native_handler, session, native_game, state)
        if port is None:
            log_req('    PvP mulligan complete but RulesPort attachment failed')
            return False
        port.begin_pvp_turn()
        live = _resolve('pvp_load_state')(session) or state
        log_req(f"    PvP mulligan complete — native scheduler reached phase {live.get('phase')} for turn player {live.get('turn_pid')}")
        _resolve('pvp_start_priority_watchdog')(session)
        _pvp_push_waiting_on(session, None)
        for pid in pids:
            h = _resolve('player_handlers').get(int(pid))
            if not h:
                continue
            pt = _ge.UID.make(244, int(pid))
            opp = _ge.UID.make(244, int(pids[0]) if int(pid) == int(pids[1]) else int(pids[1]))
            enable = _ge.Game(int(session.session_id), pt, opp)
            enable.push_disable_interface(False)
            _resolve('_send_pvp_packet')(h, session, enable, pt, 'mulligan-end-enable-input')
        return False
    next_pid = mulligan['next_player']
    state['mulligan_pid'] = next_pid
    _resolve('pvp_save_state')(session, state)
    _resolve('_pvp_push_mulligan_prompt')(session, state, next_pid)
    return True


@_pvp_locked
def route_pvp_pass(handler, session):
    """Handle a PassPriority in a tournament PvP session.

    When both players pass the current phase, the server atomically
    advances: pushes the new phase to BOTH players, runs start-of-phase
    logic, then gives GreenLight to the turn player.  No intermediate
    green-light passthrough — that broke client state.

    Phase progression reuses the battle engine's per-turn phase lists
    (BASE_TURN_PHASES / COMBAT_TURN_PHASES), so PvP wraps at EndTurn, switches
    the turn player and runs the same start-of-phase logic (Prep resources,
    Draw, GreenLight) as the AI path.
    """
    if not (session.session_name or '').startswith('tourney-'):
        return False
    native_port = getattr(session, '_rules_port_session', None)
    if native_port is not None:
        from rules_port.kernel import PriorityWindowAction
        live_native = _resolve('pvp_load_state')(session) or {}
        if native_port.action_stack.peek() is None and live_native.get('stack') and getattr(native_port, 'rehydrate_projected_chain', None):
            native_port.rehydrate_projected_chain()
        action = native_port.action_stack.peek()
        if not isinstance(action, PriorityWindowAction):
            log_req('    PvP RulesPort rejected internal pass: native priority action missing')
            return False
        player_id = _ge.UID.make(244, int(handler.client_reck_id if hasattr(handler, 'client_reck_id') else 0))
        if not native_port.pass_priority_and_drive(player_id):
            return False
        next_uid = native_port.action_stack.priority_player_id
        if next_uid is not None:
            live_after_pass = _resolve('pvp_load_state')(session) or live_native
            phase = int(live_after_pass.get('phase', 0) or 0)
            raw_waiting = int(getattr(next_uid, 'uid64', next_uid))
            waiting_pid = raw_waiting >> 8 if raw_waiting & 255 == 244 else raw_waiting
            if phase in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
                try:
                    has_quick_action = bool(_resolve('_pvp_affordable_troop_abilities')(session, live_after_pass, pid=waiting_pid))
                except Exception as exc:
                    has_quick_action = False
                    log_req(f'    PvP native auto-pass quick-action check failed for {waiting_pid}: {exc}')
                if native_port.auto_pass_waiting_player(live_after_pass, next_uid, has_quick_action=has_quick_action):
                    next_uid = native_port.action_stack.priority_player_id
                    log_req(f'    PvP RulesPort auto-completed priority for {waiting_pid} on phase {phase} (no opponent stop or quick action)')
        native_port.sync_to_pvp_state(live_native)
        _resolve('pvp_save_state')(session, live_native)
        _pvp_drive_f10_priority(session, live_native)
        return True
    my_pid = int(handler.client_reck_id) if hasattr(handler, 'client_reck_id') else 0
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return False
    state = _resolve('pvp_load_state')(session)
    if state is None:
        state = pvp_default_state(my_pid, my_pid)
        _resolve('port_enter_phase')(state, 10)
        _resolve('pvp_save_state')(session, state)
    if state['phase'] in (3, 4):
        log_req(f"    PvP: ignoring pass in phase {state['phase']} (mulligan/setup)")
        return True
    resp_wait = state.get('response_waiting_pid')
    if resp_wait == my_pid:
        caster_pid = state.get('response_caster_pid') or state.get('turn_pid')
        if caster_pid is None:
            return True
        caster_pid = int(caster_pid)
        state.pop('response_waiting_pid', None)
        state.pop('response_caster_pid', None)
        _resolve('port_set_priority')(state, caster_pid)
        _resolve('pvp_save_state')(session, state)
        if state.get('stack'):
            sp = set(state.get('stack_passed') or [])
            sp.add(my_pid)
            state['stack_passed'] = sorted(sp)
            _resolve('pvp_save_state')(session, state)
        caster_h = _resolve('player_handlers').get(caster_pid)
        if caster_h:
            caster_uid = _ge.UID.make(244, caster_pid)
            opp_uid = _ge.UID.make(244, my_pid)
            gg = _ge.Game(int(session.session_id), caster_uid, opp_uid)
            ctx = _ge.EPriorityContext.ResolveTopOfChain if state.get('stack') else _ge.EPriorityContext.Normal
            gg.push_green_light(caster_uid, ctx)
            _resolve('_send_pvp_packet')(caster_h, session, gg, caster_uid, 'response-window-close')
        _st2 = _resolve('pvp_load_state')(session) or {}
        if _st2.get('phase') in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase) and (not state.get('stack')):
            _resolve('pvp_push_main_phase_options')(session, _st2)
        log_req(f'    PvP response window: {my_pid} passed — priority back to caster {caster_pid}' + (', caster 1 pass from resolving chain' if state.get('stack') else ''))
        return True
    if state.get('stack'):
        stack_pass = _resolve('port_stack_pass_transition')(state.get('stack_passed'), my_pid, pids)
        if stack_pass['action'] == 'duplicate':
            return True
        sp = set(stack_pass['passed'])
        other_pid = stack_pass['other_player']
        if stack_pass['action'] == 'handoff':
            state['stack_passed'] = sorted(sp)
            _resolve('port_set_priority')(state, other_pid)
            _resolve('pvp_save_state')(session, state)
            _resolve('_pvp_log_stack')(state, f'pass-1/2 by {my_pid}')
            if _resolve('_pvp_auto_pass_chain_priority')(session, state, other_pid):
                return True
            other_h = _resolve('player_handlers').get(other_pid)
            if other_h:
                my_uid = _ge.UID.make(244, my_pid)
                other_uid = _ge.UID.make(244, other_pid)
                gg = _ge.Game(int(session.session_id), other_uid, my_uid)
                gg.push_green_light(other_uid, _ge.EPriorityContext.ResolveTopOfChain)
                _resolve('_send_pvp_packet')(other_h, session, gg, other_uid, 'chain-respond')
                try:
                    _resolve('pvp_push_phase_options')(session, state, pid=other_pid)
                except Exception as e:
                    log_req(f'    PvP chain-respond options error: {e}')
                log_req(f'    PvP chain: {my_pid} passed 1/2 — priority to {other_pid} to respond')
            return True
        state['stack_passed'] = []
        _resolve('pvp_save_state')(session, state)
        _resolve('_pvp_log_stack')(state, f'pass-2/2 by {my_pid}')
        return _resolve('_pvp_resolve_stack_item')(session, state, handler, my_pid)
    passes = _resolve('port_record_phase_pass')(state, my_pid)
    _resolve('pvp_save_state')(session, state)
    if len(passes) < 2:
        waiting_pid = pids[0] if pids[1] in passes else pids[1]
        quick_action_wait = False
        if int(state.get('phase', -1)) in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
            try:
                quick_action_wait = bool(_resolve('_pvp_affordable_troop_abilities')(session, state, pid=waiting_pid))
            except Exception:
                quick_action_wait = False
        if _resolve('port_waiting_player_requires_priority')(state, int(state['phase']), waiting_pid, has_quick_action=quick_action_wait):
            waiting_h = _resolve('player_handlers').get(waiting_pid)
            if waiting_h:
                waiting_uid = _ge.UID.make(244, waiting_pid)
                other_pid = pids[1] if waiting_pid == pids[0] else pids[0]
                other_uid = _ge.UID.make(244, other_pid)
                g = _ge.Game(int(session.session_id), waiting_uid, other_uid)
                g.push_green_light(waiting_uid, _ge.EPriorityContext.Normal)
                _resolve('port_set_priority')(state, waiting_pid)
                _resolve('pvp_save_state')(session, state)
                _pvp_push_turn_phase_with_elapsed(g, int(state['phase']), _ge.UID.make(244, int(state.get('turn_pid') or waiting_pid)), waiting_uid, _resolve('priority_elapsed_ticks')(state, waiting_pid) // 10000000)
                g.push_options(waiting_uid, [])
                affordable_wait = _resolve('_pvp_affordable_troop_abilities')(session, state, pid=waiting_pid)
                if affordable_wait:
                    _resolve('_pvp_add_troop_ability_options')(g, session, state, waiting_uid, other_uid, waiting_pid, affordable_wait)
                _resolve('_pvp_add_champion_options')(g, session, state, waiting_pid, waiting_uid)
                _resolve('_send_pvp_packet')(waiting_h, session, g, waiting_uid, 'pass-handoff')
                passed_h = _resolve('player_handlers').get(my_pid)
                if passed_h:
                    passed_uid = _ge.UID.make(244, my_pid)
                    passed_game = _ge.Game(int(session.session_id), passed_uid, waiting_uid)
                    passed_game.push_green_light(waiting_uid, _ge.EPriorityContext.Normal)
                    _pvp_push_turn_phase_with_elapsed(passed_game, int(state['phase']), _ge.UID.make(244, int(state.get('turn_pid') or waiting_pid)), waiting_uid, _resolve('priority_elapsed_ticks')(state, waiting_pid) // 10000000)
                    _resolve('_send_pvp_packet')(passed_h, session, passed_game, passed_uid, 'pass-handoff-lost')
                log_req(f"    PvP pass: {len(passes)}/2 — priority handed to {waiting_pid} ({('quick action' if quick_action_wait else 'opponent stop')} on phase {state['phase']})")
            else:
                log_req(f'    PvP pass: {len(passes)}/2 — waiting for opponent')
            return True
        passes = _resolve('port_record_phase_pass')(state, waiting_pid)
        _resolve('pvp_save_state')(session, state)
        log_req(f"    PvP pass: auto-completed opponent {waiting_pid}'s pass (no opponent stop on phase {state['phase']})")
    old_phase = state['phase']
    turn_pid_value = state.get('turn_pid')
    if turn_pid_value is None:
        return
    turn_pid = int(turn_pid_value)
    old_phase = int(old_phase)
    if old_phase >= _ge.ETurnPhases.DeclareAttack and old_phase != _ge.ETurnPhases.SecondMainPhase:
        has_ready = True
    else:
        has_ready = pvp_turn_has_attackers(session, turn_pid)
    phase_list = _resolve('_pvp_turn_phase_list')(state, turn_pid, has_ready)
    if old_phase == _ge.ETurnPhases.DeclareDefensePriorityWindow:
        new_phase = _resolve('pvp_phase_after_blockers')(session, state)
        transition = _resolve('port_phase_transition')(phase_list, old_phase, after_blockers=new_phase)
    else:
        transition = _resolve('port_phase_transition')(phase_list, old_phase)
    cur_idx = transition['current_index']
    next_idx = transition['next_index']
    new_phase = transition['new_phase']
    if transition['wrapped']:
        pids_ = _resolve('db_game_session_pids')(session.session_id)
        try:
            end_h = _resolve('player_handlers').get(turn_pid)
            if end_h:
                end_opp = pids_[0] if pids_[1] == turn_pid else pids_[1]
                end_opp_uid = _ge.UID.make(244, int(end_opp))
                end_uid = _ge.UID.make(244, turn_pid)
                eg = _ge.Game(int(session.session_id), end_uid, end_opp_uid)
                eg.player_health = int(state.get(f'hp_{turn_pid}', 20))
                eg.ai_health = int(state.get(f'hp_{end_opp}', 20))
                if not state.get('turn_end_trigger_fired'):
                    _resolve('_pvp_dispatch_triggers')(end_h, eg, session, _resolve('_pvp_fra_view')(state, turn_pid, end_opp), end_uid, end_opp_uid, 'TurnEndedEvent', None, turn_pid)
                    if state.get('stack'):
                        state['turn_end_trigger_fired'] = True
                from rules_port.lifecycle import clear_combat_damage, clear_expired_temporary_attributes
                clear_combat_damage(_resolve('_db'), session.session_id)
                clear_expired_temporary_attributes(_resolve('_db'), session.session_id, turn_pid, 'end_turn', clear_stat_buffs=True)
                _resolve('pvp_push_warzone_updates')(session, state)
                ev_view = _resolve('_pvp_fra_view')(state, turn_pid, end_opp)
                if ev_view.get('player_health') is not None:
                    state[f'hp_{turn_pid}'] = int(ev_view['player_health'])
                if ev_view.get('ai_health') is not None:
                    state[f'hp_{end_opp}'] = int(ev_view['ai_health'])
                _resolve('pvp_save_state')(session, state)
                if eg.events:
                    _resolve('_pvp_send_same_events')(session, eg, end_uid, end_opp_uid)
                log_req(f'    PvP TurnEndedEvent fired for {turn_pid}')
                if state.get('stack'):
                    _resolve('port_reset_priority_interval')(state, turn_pid)
                    _resolve('pvp_save_state')(session, state)
                    end_uid = _ge.UID.make(244, turn_pid)
                    end_opp_uid = _ge.UID.make(244, int(end_opp))
                    eg_priority = _ge.Game(int(session.session_id), end_uid, end_opp_uid)
                    eg_priority.push_green_light(end_uid, _ge.EPriorityContext.ResolveTopOfChain)
                    _resolve('_send_pvp_packet')(end_h, session, eg_priority, end_uid, 'turn-ended-chain')
                    _resolve('pvp_push_phase_options')(session, state, pid=turn_pid)
                    log_req(f'    PvP EndTurn chain held priority for {turn_pid}; turn remains at phase {old_phase}')
                    return True
        except Exception as e:
            import traceback
            log_req(f'    PvP TurnEndedEvent error: {e}')
            traceback.print_exc()
        turn_boundary = _resolve('port_advance_turn_state')(state, pids_)
        if turn_boundary['bonus_used']:
            log_req(f"    PvP: bonus turn for {turn_boundary['turn_pid']}")
        next_idx = 0
        new_phase = phase_list[0]
    elif old_phase != _ge.ETurnPhases.DeclareDefensePriorityWindow:
        new_phase = transition['new_phase']
    _resolve('port_enter_phase')(state, new_phase)
    _resolve('pvp_save_state')(session, state)
    log_req(f"    PvP: both passed phase {old_phase} → {new_phase} (idx {cur_idx}->{next_idx} of {len(phase_list)}, list={('C' if has_ready else 'B')}, turn={state.get('turn_pid')})")
    if old_phase == _ge.ETurnPhases.AssignFirstStrikeDamage:
        _pvp_resolve_combat(session, state, first_strike=True)
    elif old_phase == _ge.ETurnPhases.AssignDamage:
        _pvp_resolve_combat(session, state, first_strike=False)
    if _pvp_check_game_end(session, state):
        return True
    _resolve('_pvp_run_phase_start')(session, state, new_phase)
    _resolve('pvp_advance_past_non_stops')(session, state)


@_pvp_locked
def set_pvp_auto_pass(handler, session, passing_state=2):
    """Enable F10 auto-pass for one PvP client and consume its current pass.

    The client will send the subsequent passes itself whenever a GreenLight is
    handed back to it, including ResolveTopOfChain.  Record which player's
    configured stops should be ignored, then consume the current pass
    immediately so the server can advance through the current stop too.
    """
    pid = int(handler.client_reck_id)
    state = _resolve('pvp_load_state')(session)
    if not state:
        return False
    state['autopass_pid'] = pid
    state['autopass_state'] = int(passing_state or 2)
    _resolve('pvp_save_state')(session, state)
    current_priority = state.get('priority_pid')
    _resolve('route_pvp_pass')(handler, session)
    _pvp_drive_f10_priority(session, state)
    log_req(f'    PvP SetAutoPass: pid={pid} state={passing_state} priority_was={current_priority}')
    return True
