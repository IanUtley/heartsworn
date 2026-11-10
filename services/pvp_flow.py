import json
import re
import struct
import game_engine as _ge
from db import log_req
from pvp_db import db_game_cards_at_location, db_card_basic
from services.pvp_core import _resolve
from services.pvp_state import _pvp_locked
from services.pvp_core import (
    _ECardCollections,
    _ECardTypes,
    _RECORD_STORE,
    _ability_has_effects_from,
    _pvp_advance_past_declare_defense,
    _pvp_apply_card_play_costs,
    _pvp_apply_visibility,
    _pvp_card_source_location,
    _pvp_chain_active,
    _pvp_check_game_end,
    _pvp_defender_blockable_count,
    _pvp_defer_trigger_event,
    _pvp_discard_prompt_data,
    _pvp_discard_prompt_pid,
    _pvp_drive_f10_priority,
    _pvp_emit_paid_cost_events,
    _pvp_gain_threshold_trigger_game,
    _pvp_offer_opponent_response,
    _pvp_push_discard_prompt,
    _pvp_push_turn_phase_with_elapsed,
    _pvp_push_waiting_on,
    _pvp_ready_step,
    _pvp_resolve_granted_resource_abilities,
    _pvp_resource_charge_points,
    _pvp_select_card_play_costs,
    _pvp_state_thresholds,
    _pvp_typed_card_targets,
    _pvp_typed_picker_uid,
    _take_pending_gain_charge_amount,
    _uid_int,
    _wire_pvp_state_based_handler,
    push_pvp_game_start,
    pvp_combat_has_swiftstrike,
    pvp_default_state,
    pvp_phase_is_stop,
    pvp_player_auto_passes,
    pvp_push_blocker_options,
    pvp_push_empty_blockers,
    pvp_skip_to_second_main,
    pvp_turn_has_attackers,
)


def pvp_phase_after_blockers(session, state):
    """Select the first phase after the blocker response window.

    This must be evaluated at the end of DeclareDefensePriorityWindow, after
    both players had their Quick Action opportunity.  In particular, do not
    cache the Swiftstrike result when attackers are declared or blockers are
    assigned: a temporary keyword grant may arrive during that window.
    """
    return _resolve('port_phase_after_blockers')(bool(state.get('attackers') or {}), _resolve("pvp_combat_has_swiftstrike")(session, state))


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
    _resolve("_pvp_push_waiting_on")(session, ask_pid)


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
    typed_uid = _resolve("_pvp_typed_picker_uid")(typed_payload)
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
        _resolve("_pvp_check_game_end")(self.session, self.state)
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
    _resolve("_pvp_check_game_end")(session, state)


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
                    _resolve("push_pvp_game_start")(h, session)
                except Exception as e:
                    import traceback
                    log_req(f'    push_pvp_game_start FAILED: {e}\n{traceback.format_exc()}')
            del pvp_events_ready[session.session_id]
            try:
                state = _resolve('pvp_load_state')(session) or {}
                _resolve("_pvp_push_waiting_on")(session, int(state.get('goes_first_pid') or 0) or None)
            except Exception as e:
                log_req(f'    PvP pick-goes-first wait push failed: {e}')
            return True
    except Exception as e:
        import traceback
        log_req(f'    handle_events_ready FAILED: {e}\n{traceback.format_exc()}')
    return False


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
    _resolve("_pvp_push_turn_phase_with_elapsed")(g, phase, turn_uid, priority_uid, priority_elapsed_seconds)
    g.push_reconnect_done()
    _resolve('_send_pvp_packet')(handler, session, g, pl_uid, 'reconnect-snapshot')
    if priority_pid == pid:
        if phase in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
            _resolve('pvp_push_main_phase_options')(session, state)
        elif phase == _ge.ETurnPhases.DeclareAttack:
            _resolve('pvp_push_attack_options')(session, state)
        elif phase == _ge.ETurnPhases.DeclareDefense:
            _resolve("pvp_push_blocker_options")(session, state)
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
    _resolve("_pvp_apply_visibility")(game, state)
    priority_uid = _ge.UID.make(244, survivor_pid)
    turn_uid = _ge.UID.make(244, int(state.get('turn_pid') or survivor_pid))
    game.push_green_light(priority_uid, _ge.EPriorityContext.Normal)
    _resolve("_pvp_push_turn_phase_with_elapsed")(game, phase, turn_uid, priority_uid, _resolve('priority_elapsed_ticks')(state, survivor_pid) // 10000000)
    _resolve('_send_pvp_packet')(survivor_handler, session, game, pl_uid, 'disconnect-priority')
    if phase in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
        _resolve('pvp_push_main_phase_options')(session, state)
    elif phase == _ge.ETurnPhases.DeclareAttack:
        _resolve('pvp_push_attack_options')(session, state)
    elif phase == _ge.ETurnPhases.DeclareDefense:
        _resolve("pvp_push_blocker_options")(session, state)
    elif phase not in (3, 4, 5, 6, 7, 8, 9):
        _resolve('pvp_push_phase_options')(session, state, pid=survivor_pid)
    return True


def _pvp_resume_interpreted_pick(handler, session, state, pend, answer, my_pid):
    """Resume the interpreter with the answer to an authored hand pick (rules_port/resolution.py) on the PvP view, then
    save and push the same priority the choice answer does. ``answer`` is a UID, a list of UIDs for a multi-card pick, or
    None for a declined optional single pick. The native chain push is not used."""
    from rules_port import ir_exec
    from rules_port.context import EffectContext
    from rules_port.interpreted_prompts import prompt_interpreted_reveal
    pids = _resolve('db_game_session_pids')(session.session_id)
    owner_id = int(pend.get('owner_id', my_pid))
    opp_pid = pids[0] if pids[1] == owner_id else pids[1]
    pl_t = _ge.UID.make(244, owner_id)
    ai_t = _ge.UID.make(244, opp_pid)
    state.pop('pending_trigger', None)
    view = _resolve('_pvp_fra_view')(state, owner_id, opp_pid)
    view.pop('pending_trigger', None)
    view.pop('resolution_paused', None)
    view['resolving_owner_id'] = owner_id
    g = _ge.Game(int(session.session_id), pl_t, ai_t)
    _resolve('_pvp_populate_game_state')(g, state, owner_id, opp_pid)
    context = EffectContext.from_rules_port(g, session, _resolve('_db'), handler, pl_t, ai_t, view, 'trigger', ability=None)
    try:
        if isinstance(answer, list):
            answer = [int(uid) for uid in answer]
        else:
            answer = None if answer is None else int(answer)
        ir_exec.resume_ability(pend['interpreter'], answer, context=context)
    except ir_exec.NeedsChoice as pause:
        prompt_interpreted_reveal(handler, g, session, _resolve('_db'), pl_t, ai_t, view, pause)
    except ir_exec.NeedsConversation:
        pass  # the leaf saved the pause; the client's answer resumes the interpreter
    else:
        completed = int(state.pop('paused_chain_instance_id', 0) or 0)
        if completed:
            state['completed_chain_instance_id'] = completed
    _resolve('_pvp_sync_view_to_state')(state, view, owner_id, opp_pid)
    _resolve('pvp_save_state')(session, state)
    _resolve('_pvp_send_same_events')(session, g, pl_t, ai_t)
    if any(state.get(key) for key in ('pending_choice', 'pending_trigger', 'pending_deck_search',
                                      'pending_conversation', 'resolution_paused')):
        return True
    _resolve('_pvp_push_waiting_on')(session, None)
    g2 = _ge.Game(int(session.session_id), pl_t, ai_t)
    g2.push_chain_empty()
    state['priority_pid'] = int(state.get('turn_pid') or owner_id)
    _resolve('pvp_save_state')(session, state)
    turn_handler = _resolve('player_handlers').get(int(state['priority_pid']))
    if turn_handler is not None:
        turn_uid = _ge.UID.make(244, int(state['priority_pid']))
        other_uid = _ge.UID.make(244, opp_pid if int(state['priority_pid']) == owner_id else owner_id)
        g2 = _ge.Game(int(session.session_id), turn_uid, other_uid)
        g2.push_green_light(turn_uid, _ge.EPriorityContext.Normal)
        _resolve('_send_pvp_packet')(turn_handler, session, g2, turn_uid, 'greenlight-after-trigger-pick')
    if state.get('phase') in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
        _resolve('pvp_push_main_phase_options')(session, state)
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
    if isinstance(pend.get('interpreter'), dict):
        # An authored hand pick the interpreter paused for (rules_port/resolution.py). Every card the client named is an
        # answer (a multi-card pick names several); the interpreter takes the answer, not the native chain push.
        from rules_port import picks
        raw_uids = []
        if isinstance(pending_target_map, dict):
            selected = pending_target_map.get(target_index, pending_target_map.get(str(target_index)))
            values = selected if isinstance(selected, (list, tuple, set)) else (selected,) if selected is not None else ()
            raw_uids.extend(int(getattr(value, 'uid64', value)) for value in values
                            if isinstance(getattr(value, 'uid64', value), int))
        if isinstance(inner_bytes, bytes):
            for m_du in _re.finditer(b'm_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});', inner_bytes):
                try:
                    uid64 = _st.unpack('<Q', bytes.fromhex(m_du.group(1).decode()))[0]
                except Exception:
                    continue
                if uid64 & 255 == 1:
                    raw_uids.append(int(uid64))
        if chosen_uid is not None:
            raw_uids.append(int(chosen_uid))
        entry = (pend.get('target_entries') or [{}])[0]
        answer, complete = picks.hand_answer(
            raw_uids, entry.get('uids') or [], minimum=int(entry.get('minimum', 1)),
            maximum=int(entry.get('maximum', 1)), optional=bool(pend.get('optional')))
        if not complete:
            log_req('    PvP interpreted hand pick: too few cards chosen; the prompt stays open')
            return True
        return _pvp_resume_interpreted_pick(handler, session, state, pend, answer, my_pid)
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
        from rules_port.resolution import resolve_port_trigger
        from rules_port.interpreted_prompts import trigger_event_fields
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
    if _resolve("_pvp_check_game_end")(session, state):
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
        _resolve("_pvp_push_turn_phase_with_elapsed")(resume, int(state.get('phase', 0)), turn_uid, turn_uid, _resolve('priority_elapsed_ticks')(state, turn_pid) // 10000000)
        _resolve('_send_pvp_packet')(turn_h, session, resume, turn_uid, 'trigger-target-chain-empty')
    phase = int(state.get('phase', 0))
    if phase in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
        _resolve('pvp_push_main_phase_options')(session, state)
    elif phase == _ge.ETurnPhases.DeclareAttack:
        _resolve('pvp_push_attack_options')(session, state)
    elif phase == _ge.ETurnPhases.DeclareDefense:
        _resolve("pvp_push_blocker_options")(session, state)
    log_req(f"    PvP trigger resolved: {ag[:8]} -> {(hex(chosen_uid) if chosen_uid else 'none')}; priority -> {turn_pid}")
    return True


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
    if _resolve("_pvp_chain_active")(session, state):
        _resolve('pvp_push_phase_options')(session, state, pid=pid)
        return
    phase = int(state.get('phase', 0) or 0)
    if phase in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
        _resolve('pvp_push_main_phase_options')(session, state)
    elif phase == _ge.ETurnPhases.DeclareAttack:
        _resolve('pvp_push_attack_options')(session, state)
    elif phase == _ge.ETurnPhases.DeclareDefense:
        _resolve("pvp_push_blocker_options")(session, state)
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
        _resolve("_pvp_push_turn_phase_with_elapsed")(resume, int(state.get('phase', 0)), turn_uid, turn_uid, _resolve('priority_elapsed_ticks')(state, turn_pid) // 10000000)
        _resolve('_send_pvp_packet')(turn_h, session, resume, turn_uid, 'optional-trigger-declined')
    phase = int(state.get('phase', 0))
    if not state.get('stack'):
        if phase in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
            _resolve('pvp_push_main_phase_options')(session, state)
        elif phase == _ge.ETurnPhases.DeclareAttack:
            _resolve('pvp_push_attack_options')(session, state)
        elif phase == _ge.ETurnPhases.DeclareDefense:
            _resolve("pvp_push_blocker_options")(session, state)
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
                _resolve("_pvp_ready_step")(session, state, turn_uid, warm, turn_uid_p, handler=turn_h)
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
        if _resolve("_pvp_check_game_end")(session, state):
            return
    prep_wz = []
    prep_cooldown_uids = []
    prep_champion_cooldowns = {}
    if phase == 8:
        from rules_port.resources import begin_turn_resources_for_player
        resource_refill = begin_turn_resources_for_player(state, turn_uid)
        from rules_port.lifecycle import clear_expired_temporary_attributes
        ready_changes = _resolve("_pvp_ready_step")(session, state, turn_uid, handler=_resolve('player_handlers').get(turn_uid))
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
        _resolve("_pvp_defer_trigger_event")(state, event_type='TurnPhaseEvent', source_card_id=None, source_player_id=turn_uid, phase=int(phase), data={'phase': int(phase)})
        dr = _resolve('_pvp_run_draw')(session, state)
        if dr is not None:
            for _pid in pids:
                pvp_draw_cache[_pid] = dr
            if _resolve("_pvp_check_game_end")(session, state):
                return
    if phase != _ge.ETurnPhases.StartTurn:
        phase_h = _resolve('player_handlers').get(turn_uid)
        if phase_h:
            phase_game = _ge.Game(int(session.session_id), _ge.UID.make(244, turn_uid), _ge.UID.make(244, defender_pid))
            _resolve("_pvp_apply_visibility")(phase_game, state)
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
    chain_from_phase_start = _resolve("_pvp_chain_active")(session, state)
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
        _resolve("_pvp_apply_visibility")(g, state)
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
        _resolve("_pvp_push_turn_phase_with_elapsed")(g, phase, turn_uid_p, prio_uid, priority_elapsed_seconds)
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
    _resolve("_pvp_check_game_end")(session, state)


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
            blockable = _resolve("_pvp_defender_blockable_count")(session, state)
            if blockable <= 0:
                log_req(f'    PvP post-attack: defender {opp_pid} has {blockable} blocker(s) — auto-passing DeclareDefense')
                try:
                    _resolve("pvp_push_empty_blockers")(session, state)
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
        _resolve("pvp_skip_to_second_main")(session, state)
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
    my_wz = {r[0] for r in _resolve('db_card_uids_in_zone')(session.session_id, my_pid, 'warzone', conn=_resolve('_db'))}
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
        _resolve("_pvp_advance_past_declare_defense")(session, state)
    return True


def _pvp_offer_trigger_response(session, state, caster_pid):
    """Offer the opponent priority for a newly-created trigger chain."""
    _resolve("_pvp_offer_opponent_response")(session, state, caster_pid)
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
    source_location = _resolve("_pvp_card_source_location")(session, state, my_pid, int(played_card_uid))
    if source_location is None:
        log_req(f'    PvP REJECTED spell {card_name}: card is not in a playable zone')
        return True
    prior_card_zone = _resolve('db_card_owner_zone_state')(session.session_id, int(played_card_uid), conn=_resolve('_db'))
    targets = _resolve("_pvp_typed_card_targets")(typed_payload, played_card_uid)
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
    cost_selection = _resolve("_pvp_select_card_play_costs")(handler, session, state, play_plan, int(played_card_uid), my_pid, targets, champ_targets)
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
    _resolve("_pvp_apply_card_play_costs")(handler, g, session, state, my_uid, opp_uid, cost_selections, int(played_card_uid), scrounge=any((getattr(ability, 'is_scrounge', False) for ability in play_plan.cast_abilities)))
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
        if opp_h and (not _resolve("pvp_player_auto_passes")(state, opp_pid)):
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
    source_location = _resolve("_pvp_card_source_location")(session, state, my_pid, int(played_card_uid))
    if source_location is None:
        log_req(f'    PvP REJECTED permanent {card_name}: card is not in a playable zone')
        return True
    prior_card_zone = _resolve('db_card_owner_zone_state')(session.session_id, int(played_card_uid), conn=_resolve('_db'))
    try:
        play_plan = PlayPlan.from_card(_RECORD_STORE, tpl_guid, source_uid=int(played_card_uid), owner_id=my_pid)
    except KeyError as exc:
        log_req(f'    PvP REJECTED permanent {card_name}: {exc}')
        return True
    targets = _resolve("_pvp_typed_card_targets")(typed_payload, played_card_uid)
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
    cost_selection = _resolve("_pvp_select_card_play_costs")(handler, session, state, play_plan, int(played_card_uid), my_pid, targets, champ_targets)
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
    _resolve("_pvp_apply_card_play_costs")(handler, g, session, state, my_uid, opp_uid, cost_selections, int(played_card_uid), scrounge=any((getattr(ability, 'is_scrounge', False) for ability in play_plan.cast_abilities)))
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
    if opp_h and (not _resolve("pvp_player_auto_passes")(state, opp_pid)):
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
    source_location = _resolve("_pvp_card_source_location")(session, state, my_pid, int(played_card_uid))
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
    charge_grant = _resolve("_pvp_resource_charge_points")(session, played_card_uid)
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
    thresh = _resolve("_pvp_state_thresholds")(state, my_pid)
    threshold_trigger_games = []
    for flag, _amount in threshold_grants:
        trigger = _resolve("_pvp_gain_threshold_trigger_game")(handler, session, state, my_pid, flag)
        if trigger is not None:
            threshold_trigger_games.append(trigger)
    printed_skip = None
    if not is_shards_of_fate:
        printed_skip = {str(resource_choice_ability).lower()} if resource_choice_ability else set()
    resource_ability_events = _resolve("_pvp_resolve_granted_resource_abilities")(handler, session, state, int(played_card_uid), my_pid, printed_skip=printed_skip)
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
        pending_charge_amount = _resolve("_take_pending_gain_charge_amount")(state, my_pid)
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
        pending_charge_amount = _resolve("_take_pending_gain_charge_amount")(state, my_pid)
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
                _resolve("_pvp_emit_paid_cost_events")(cost_game, state, handler=handler, session=session, db=_resolve('_db'), player_uid=pl_t, ai_uid=ai_t)
                if cost_game.events:
                    _resolve('_pvp_send_same_events')(session, cost_game, pl_t, ai_t)
                ability_event_start = len(g.events)
                ability_player_health_before = int(view.get('player_health', 20))
                ability_ai_health_before = int(view.get('ai_health', 20))
                activation_data = item.get('activation_data') or {}
                target_map = dict(activation_data.get('target_map') or {}) if isinstance(activation_data, dict) else {}
                if not target_map and item.get('target_uid') is not None:
                    from gamedata import DEFAULT_RECORD_STORE
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
        discard_prompt = _resolve("_pvp_discard_prompt_data")(ag)
        if discard_prompt:
            item_activation = item.get('activation_data') or {}
            discard_pid = _resolve("_pvp_discard_prompt_pid")(session, state, ag, owner_id, opp_pid, src_uid, target_uid=item.get('target_uid'), target_map=item_activation.get('target_map') if isinstance(item_activation, dict) else None)
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
    _resolve("_pvp_emit_paid_cost_events")(g, state, handler=handler, session=session, db=_resolve('_db'), player_uid=pl_t, ai_uid=ai_t)
    _resolve('_pvp_send_same_events')(session, g, pl_t, ai_t)
    if chain_empty and pending_discard:
        discard_other = int(opp_pid) if discard_pid == int(owner_id) else int(owner_id)
        _resolve("_pvp_push_discard_prompt")(session, state, discard_pid, discard_other, int(src_uid))
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
    if _resolve("_pvp_check_game_end")(session, state):
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
            _resolve("_pvp_push_turn_phase_with_elapsed")(gg, int(state.get('phase', 0)), turn_pt, turn_pt, _resolve('priority_elapsed_ticks')(state, turn_pid) // 10000000)
            _resolve('_send_pvp_packet')(turn_h, session, gg, turn_pt, 'chain-empty')
        _cur_phase = int(state.get('phase', 0))
        if _cur_phase in (_ge.ETurnPhases.FirstMainPhase, _ge.ETurnPhases.SecondMainPhase):
            _resolve('pvp_push_main_phase_options')(session, state)
        elif _cur_phase == _ge.ETurnPhases.DeclareAttack:
            _resolve('pvp_push_attack_options')(session, state)
        elif _cur_phase == _ge.ETurnPhases.DeclareDefense:
            _resolve("pvp_push_blocker_options")(session, state)
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
    interpreted_pick = pending.get('kind') == 'interpreted_choice_zone' and isinstance(pending.get('interpreter'), dict)
    if interpreted_pick:
        # An authored Choosing pick the interpreter paused for (rules_port/resolution.py): the chosen card is its target.
        # Nothing is played; resume the interpreter on the PvP view, which is saved below.
        from rules_port import ir_exec
        from rules_port.interpreted_prompts import prompt_interpreted_reveal
        try:
            ir_exec.resume_ability(pending['interpreter'], int(chosen_uid), context=choice_context)
        except ir_exec.NeedsChoice as pause:
            prompt_interpreted_reveal(handler, g, session, _resolve('_db'), pl_t, ai_t, view, pause)
        except ir_exec.NeedsConversation:
            pass  # the leaf saved the pause; the client's answer resumes the interpreter
    elif choice_zone_target:
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
    pending_charge_amount = _resolve("_take_pending_gain_charge_amount")(state, owner_id) if not view.get('pending_choice') else 0
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
    _resolve("_pvp_push_waiting_on")(session, None)
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
    typed_uid = _resolve("_pvp_typed_picker_uid")(typed_payload, continuation.get('target_index', 0))
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
    pending_charge_amount = _resolve("_take_pending_gain_charge_amount")(state, owner_id)
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
    typed_uid = _resolve("_pvp_typed_picker_uid")(typed_payload, continuation.get('target_index', 0))
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
    typed_uid = _resolve("_pvp_typed_picker_uid")(typed_payload)
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
        threshold_trigger_game = _resolve("_pvp_gain_threshold_trigger_game")(handler, session, state, owner_id, flag)
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
    pending_charge_amount = _resolve("_take_pending_gain_charge_amount")(state, owner_id)
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
        _resolve("_wire_pvp_state_based_handler")(cached, handler, session, game)
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
            _resolve("_pvp_apply_card_play_costs")(current_handler(), game, session, live, player_uid, opponent_uid, cost_selections, int(ability.source_uid))
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
            _resolve("_pvp_apply_card_play_costs")(current_handler(), cost_game, session, state, payer_uid, other_uid, cost_selections, int(getattr(metadata, 'source_uid', 0) or 0))
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
                has_attackers = _resolve("pvp_turn_has_attackers")(session, int(live.get('turn_pid', 0) or 0))
                port.has_legal_attackers = bool(has_attackers)
                port.active_player_skips_attack = not bool(has_attackers)
            raw_player = _resolve("_uid_int")(player_id)
            pass_pid = raw_player >> 8 if raw_player & 255 == 244 else raw_player
            responding_to_chain = getattr(native_priority_action, 'ability_responding_to', None) is not None
            pass_key = 'stack_passed' if responding_to_chain else 'passes'
            passed = {int(value) for value in live.get(pass_key) or ()}
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
                    _resolve("_pvp_push_turn_phase_with_elapsed")(handoff, current_phase, _ge.UID.make(244, int(live.get('turn_pid') or next_priority)), _ge.UID.make(244, next_priority), _resolve('priority_elapsed_ticks')(live, next_priority) // 10000000)
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
        if phase == _ge.ETurnPhases.DeclareDefensePriorityWindow and live.get('attackers') and (not live.get('blockers')) and (_resolve("_pvp_defender_blockable_count")(session, live) <= 0):
            _resolve("pvp_push_empty_blockers")(session, live)
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
            port.active_player_skips_attack = not _resolve("pvp_turn_has_attackers")(session, turn_pid)
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
                _resolve("_pvp_push_waiting_on")(session, None)
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
    _resolve("_wire_pvp_state_based_handler")(port, handler, session, game)
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
            card_uid = _resolve("_uid_int")(payload.get('card_id'))
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
            return bool(_resolve("_pvp_resolve_deck_search")(current, session, raw, my_pid, typed_payload=payload))
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
                _resolve("_pvp_resolve_combat")(session, live, first_strike=phase == _ge.ETurnPhases.AssignFirstStrikeDamage)
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
                        _resolve("_pvp_advance_from_damage_step")(session, live, phase)
            _resolve('pvp_save_state')(session, live)
            return True
        except (TypeError, ValueError, struct.error) as exc:
            log_req(f'    PvP AssignDamageOrder projection error: {exc}')
            return False
    if kind == 'play_resource':
        try:
            card_uid = _resolve("_uid_int")(payload.get('card_id'))
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
    if _resolve("pvp_phase_is_stop")(state, int(state.get('phase', 6)), turn_pid, opp_pid):
        log_req(f"    PvP auto-advance: phase {state.get('phase')} is a player stop — not auto-passing")
        return False
    has_ready = _resolve("pvp_turn_has_attackers")(session, turn_pid)
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
        if _resolve("pvp_phase_is_stop")(state, new_phase, turn_pid, opp_pid):
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
        _resolve("_pvp_push_waiting_on")(session, None)
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
        _resolve("_pvp_drive_f10_priority")(session, live_native)
        return True
    my_pid = int(handler.client_reck_id) if hasattr(handler, 'client_reck_id') else 0
    pids = _resolve('db_game_session_pids')(session.session_id)
    if len(pids) < 2:
        return False
    state = _resolve('pvp_load_state')(session)
    if state is None:
        state = _resolve("pvp_default_state")(my_pid, my_pid)
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
                _resolve("_pvp_push_turn_phase_with_elapsed")(g, int(state['phase']), _ge.UID.make(244, int(state.get('turn_pid') or waiting_pid)), waiting_uid, _resolve('priority_elapsed_ticks')(state, waiting_pid) // 10000000)
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
                    _resolve("_pvp_push_turn_phase_with_elapsed")(passed_game, int(state['phase']), _ge.UID.make(244, int(state.get('turn_pid') or waiting_pid)), waiting_uid, _resolve('priority_elapsed_ticks')(state, waiting_pid) // 10000000)
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
        has_ready = _resolve("pvp_turn_has_attackers")(session, turn_pid)
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
        _resolve("_pvp_resolve_combat")(session, state, first_strike=True)
    elif old_phase == _ge.ETurnPhases.AssignDamage:
        _resolve("_pvp_resolve_combat")(session, state, first_strike=False)
    if _resolve("_pvp_check_game_end")(session, state):
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
    _resolve("_pvp_drive_f10_priority")(session, state)
    log_req(f'    PvP SetAutoPass: pid={pid} state={passing_state} priority_was={current_priority}')
    return True
