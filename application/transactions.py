"""Player transaction handlers (mulligan, choose, pass priority, attack, defend, ability activation and more)."""

import re
import struct


class TransactionsMixin:
    """Methods of HCPHandler, mixed into it (see hconnect_server)."""

    def _handle_priority_sync_transaction(self, session, transaction):
        """Reconcile a client's lost priority state without changing game state."""
        import hconnect_server as _hc
        if not session:
            return False

        port = getattr(session, "_rules_port_session", None)
        if port is not None:
            chain_sync = self._push_rules_port_chain_priority_sync(
                session, port)
            if chain_sync is not None:
                if not chain_sync:
                    self._push_transaction_ack(session)
                return True

        # PvP tournament path.
        if (session.session_name or "").startswith("tourney-"):
            from services.tournament_game import pvp_load_state
            pvp_state = pvp_load_state(session)
            if pvp_state:
                my_pid = int(self.client_reck_id)
                my_uid = _hc.game_engine.UID.make(244, my_pid)
                pvp_pids = [int(k) for k in (pvp_state.get("pids") or [])
                            if int(k) != my_pid]
                opp_pid = pvp_pids[0] if pvp_pids else my_pid
                opp_uid = _hc.game_engine.UID.make(244, opp_pid)
                game_ps = _hc.game_engine.Game(int(session.session_id), my_uid, opp_uid)
                phase = pvp_state.get("phase", 3)
                turn_uid = _hc.game_engine.UID.make(244, pvp_state["turn_pid"])
                if phase == _hc.game_engine.ETurnPhases.Mulligan:
                    if pvp_state.get("mulligan_pid") == my_pid:
                        game_ps.push_green_light(
                            my_uid, _hc.game_engine.EPriorityContext.Normal)
                        pkt_ps = game_ps.make_network_packet(my_uid)
                        dw_ps = _hc.encode_datawrapper(
                            0, 3055,
                            _hc.compress_gzip(_hc.encode_sync_event(pkt_ps)), 1,
                            _hc.client_session_guid(self))
                        self.scnt += 1
                        self.send({
                            "issuer": f"0.0.0.0.ServiceGameSession.246."
                                      f"{session.session_id}.{self.scnt}",
                            "target": "ServiceGameSession",
                            "instance": str(session.server_id),
                            "reqid": 0, "c": 0, "conh": 0, "sid": self.sid,
                        }, dw_ps)
                        _hc.log_req(f"    RequestPrioritySync (PvP): Mulligan greenlight to actor {my_pid}")
                    else:
                        self._push_transaction_ack(session)
                        _hc.log_req(f"    RequestPrioritySync (PvP): Mulligan — {my_pid} not the actor, acked")
                elif phase == _hc.game_engine.ETurnPhases.PickGoesFirst:
                    winner_pid = pvp_state.get(
                        "goes_first_pid", pvp_state["turn_pid"])
                    if my_pid == winner_pid:
                        winner_uid = _hc.game_engine.UID.make(244, winner_pid)
                        game_ps.push_turn_phase(
                            _hc.game_engine.ETurnPhases.PickGoesFirst,
                            winner_uid, winner_uid)
                        game_ps.push_green_light(
                            my_uid, _hc.game_engine.EPriorityContext.Normal)
                        pkt_ps = game_ps.make_network_packet(my_uid)
                        dw_ps = _hc.encode_datawrapper(
                            0, 3055,
                            _hc.compress_gzip(_hc.encode_sync_event(pkt_ps)), 1,
                            _hc.client_session_guid(self))
                        self.scnt += 1
                        self.send({
                            "issuer": f"0.0.0.0.ServiceGameSession.246."
                                      f"{session.session_id}.{self.scnt}",
                            "target": "ServiceGameSession",
                            "instance": str(session.server_id),
                            "reqid": 0, "c": 0, "conh": 0, "sid": self.sid,
                        }, dw_ps)
                        _hc.log_req(f"    RequestPrioritySync (PvP): PickGoesFirst greenlight to winner {my_pid}")
                    else:
                        self._push_transaction_ack(session)
                        _hc.log_req(f"    RequestPrioritySync (PvP): PickGoesFirst — {my_pid} is not the winner, acked")
                else:
                    game_ps.push_turn_phase(phase, turn_uid, my_uid)
                    game_ps.push_green_light(
                        my_uid, _hc.game_engine.EPriorityContext.Normal)
                    pkt_ps = game_ps.make_network_packet(my_uid)
                    dw_ps = _hc.encode_datawrapper(
                        0, 3055,
                        _hc.compress_gzip(_hc.encode_sync_event(pkt_ps)), 1,
                        _hc.client_session_guid(self))
                    self.scnt += 1
                    self.send({
                        "issuer": f"0.0.0.0.ServiceGameSession.246."
                                  f"{session.session_id}.{self.scnt}",
                        "target": "ServiceGameSession",
                        "instance": str(session.server_id),
                        "reqid": 0, "c": 0, "conh": 0, "sid": self.sid,
                    }, dw_ps)
                    _hc.log_req(f"    RequestPrioritySync (PvP): re-sent GreenLight for phase {phase}")
            return True

        _be = self._checkpoint_engine(session)
        pl_t = _hc.game_engine.UID.make(244, int(self.client_reck_id))
        ai_t = _hc.game_engine.UID.make(_hc.game_engine.SERVICE_AI_UID_TYPE, 1000)
        try:
            bstate = _be.load_state(session)
            self._current_bstate = bstate
            phase = _be.current_phase(bstate)
        except Exception:
            bstate, phase = None, None
        if bstate and bstate.get("pending_trigger") and not bstate.get("pvp"):
            self._resend_pending_trigger(session, bstate, pl_t, ai_t)
            _hc.log_req("    RequestPrioritySync: picker still pending, re-sent")
            return True
        if bstate and phase is not None and bstate.get("turn_player") == _be.PLAYER:
            game = self._fresh_game(session, pl_t, ai_t, bstate)
            game.push_green_light(pl_t, self._priority_context_for(phase, bstate))
            game.push_player_updated(
                pl_t, champ_id=getattr(self, "_player_champ_scid", None))
            game.push_player_updated(
                ai_t, champ_id=getattr(self, "_ai_champ_scid", None))
            self._send_battle_events(session, game, pl_t)
            if phase == _hc.game_engine.ETurnPhases.DeclareAttack:
                self._push_attack_options(session, pl_t, ai_t)
            elif phase in (_hc.game_engine.ETurnPhases.FirstMainPhase,
                           _hc.game_engine.ETurnPhases.SecondMainPhase):
                self._push_main_phase_options(session, pl_t, ai_t)
            else:
                self._push_phase_options_empty(session, pl_t, ai_t)
            _hc.log_req(f"    RequestPrioritySync: re-sent GreenLight for phase {phase}")
        elif (bstate and phase is not None
              and bstate.get("turn_player") == _be.AI
              and bstate.get("ai_turn_phase_idx") is not None):
            # During the AI's turn there is no human-side battle state in the
            # sense used by the old player-turn branch above: the AI drives the
            # phase loop server-side and only records ``ai_turn_phase_idx``
            # while waiting for the human at an opponent stop.  If the client
            # loses its local priority bit (for example after combat damage),
            # LocalPlayer requests a priority resync.  A bare 3055 ack leaves
            # the client with no Pass window and strands the AI turn.
            # Re-announce the held phase with the human as priority, then send
            # the same context/options contract as the normal AI-stop path.
            held = _be.ai_held_phase_context(bstate)
            if held is not None:
                held_phase, held_idx, held_phases = held
                # The resume cursor is written at the stop boundary and is
                # safer than a stale phase_idx left by a competing packet.
                bstate["turn_phases"] = list(held_phases)
                bstate["phase_idx"] = held_idx
                _be.save_state(session, bstate)
                phase = held_phase
            game = self._fresh_game(session, pl_t, ai_t, bstate)
            game.push_turn_phase(phase, ai_t, pl_t)
            context = (_hc.game_engine.EPriorityContext.Normal
                       if phase == _hc.game_engine.ETurnPhases.DeclareDefense
                       else _hc.game_engine.EPriorityContext.ResolveTopOfChain)
            game.push_green_light(pl_t, context)
            self._send_battle_events(session, game, pl_t)
            if phase == _hc.game_engine.ETurnPhases.DeclareDefense:
                self._push_blocker_options(session, pl_t, ai_t)
            else:
                self._push_phase_options_empty(session, pl_t, ai_t)
            _hc.log_req(f"    RequestPrioritySync: re-sent AI-stop GreenLight "
                    f"for phase {phase}")
        else:
            self._push_transaction_ack(session)
            _hc.log_req("    RequestPrioritySync: no active battle, acked")
        return True

    def _handle_choose_pick_transaction(self, session, transaction):
        """Resolve Play/Draw selection and enter the Mulligan phase."""
        import hconnect_server as _hc
        inner_bytes = transaction.inner_bytes
        is_set_ability_data = transaction.is_set_ability_data
        is_ability_activate = transaction.is_ability_activate
        # PvP: chose Play or Draw — draw hands, then push Mulligan.
        if ((session.session_name or "").startswith("tourney-")
                and not is_set_ability_data
                and not is_ability_activate):
            from services.tournament_game import (pvp_load_state, pvp_save_state,
                                                  db_game_session_pids, _push_to_both_players)
            state = pvp_load_state(session)
            my_pid = int(self.client_reck_id)
            chose_draw = b"ChooseDrawTransaction" in inner_bytes
            pids = db_game_session_pids(session.session_id)
            if state and pids:
                winner_pid = state["goes_first_pid"]
                loser_pid = next(p for p in pids if p != winner_pid)
                # ONLY the coin-flip winner decides who plays/draws
                # first.  The loser's client never receives the
                # PickGoesFirst GreenLight (only the winner is the
                # active player), so it never shows the Play/Draw
                # dialog and never submits a choose transaction.
                # Requiring a pick from BOTH players therefore stalls
                # forever after the winner chooses ("chose to go first,
                # then nothing happened").  Ignore any non-winner pick
                # and resolve on the winner's first choose.
                if my_pid != winner_pid:
                    _hc.log_req(f"    PvP choose: {my_pid} (non-winner) "
                            f"pick ignored — waiting on winner "
                            f"{winner_pid}")
                    self._push_transaction_ack(session)
                    return True
                if state.get("pick_resolved"):
                    # Already resolved — this is a stale/re-sent choose
                    # from the winner; just ack to avoid double-drawing.
                    _hc.log_req(f"    PvP choose: already resolved "
                            f"(draws_first {state['draws_first_pid']}) "
                            f"— stale pick from {my_pid}")
                    self._push_transaction_ack(session)
                    return True
                # The winner picks Play -> winner plays first and the
                # loser draws first; Draw -> winner draws first and the
                # loser plays first.  The player who draws first goes
                # SECOND (the client reorders its player list so the
                # draws-first player is last / the other is "starting"),
                # so the actual first turn belongs to play_first_pid.
                from services.tournament_game import pvp_session_lock
                with pvp_session_lock(session):
                    draw_first_pid = winner_pid if chose_draw else loser_pid
                    play_first_pid = loser_pid if chose_draw else winner_pid
                    state["draws_first_pid"] = draw_first_pid
                    state["play_first_pid"] = play_first_pid
                    # The first turn goes to the play-first player, NOT the
                    # coin-flip winner — if the winner picked Draw, the
                    # loser opens the game.
                    state["turn_pid"] = play_first_pid
                    state["pids"] = [play_first_pid, draw_first_pid]
                    state["pick_resolved"] = True
                    # Advance the persisted PvP phase from PickGoesFirst(3)
                    # to Mulligan(4) BEFORE any events go out. Otherwise a
                    # client priority sync during the handoff can re-push
                    # PickGoesFirst and restore the wrong setup UI.
                    state["phase"] = 4
                    # Mulligan is an ACTIVE-player phase in C#. The player
                    # who plays first gets the first decision; the coin
                    # winner may have chosen Draw and therefore be second.
                    state["mulligan_pid"] = play_first_pid
                    state["mulligan_count"] = {}
                    pvp_save_state(session, state)

                    # Corinth starts with four shards; ordinary PvP starts
                    # with the constructed-game hand size.
                    opening_hand_size = int(
                        (getattr(session, "encounter_data", {}) or {}).get(
                            "starting_hand_size", 7) or 7)
                    for apid in pids:
                        _hc.db_game_draw_cards(session.session_id, apid,
                                           opening_hand_size)
                    _hc.log_req(f"    PvP choose: drew hands for both "
                            f"({opening_hand_size})")

                # Push hand + Mulligan to each player.
                hands = {
                    int(apid): _hc.db_game_get_hand(session.session_id, apid)
                    for apid in pids
                }
                hand_uids = [int(card_uid)
                             for hand in hands.values()
                             for card_uid, _template_guid in hand]
                hand_projections = _hc.db_card_instance_dynamic_projections(
                    session.session_id, hand_uids, conn=_hc._db)
                for apid in pids:
                    h = _hc.player_handlers.get(apid)
                    if not h: continue
                    aopp = pids[0] if pids[1] == apid else pids[1]
                    pl = _hc.game_engine.UID.make(244, apid)
                    op = _hc.game_engine.UID.make(244, aopp)
                    g = _hc.game_engine.Game(int(session.session_id), pl, op)
                    for idx, (cu, tg) in enumerate(hands[int(apid)]):
                        scid = _hc.game_engine.SessionCardId(_hc.game_engine.UID(int(cu)))
                        # Populate the CardDef (cost/atk/def/thresholds/
                        # abilities/gems) so the client renders the hand
                        # and the Mulligan keep/redraw dialog correctly.
                        _tpl_g, ct, _nm, _c, _a, _d, gem_type = \
                            self._card_full_data(
                                g, scid, tg,
                                instance_projection=hand_projections.get(int(cu)))
                        g.push_card_updated(scid, pl, _hc.game_engine.ECardCollections.Hand,
                                            ct, template_id=tg, gems=gem_type)
                    for idx, (cu, tg) in enumerate(hands[int(aopp)]):
                        scid = _hc.game_engine.SessionCardId(_hc.game_engine.UID(int(cu)))
                        _tpl_g, ct, _nm, _c, _a, _d, gem_type = \
                            self._card_full_data(
                                g, scid, tg,
                                instance_projection=hand_projections.get(int(cu)))
                        g.push_card_updated(scid, op, _hc.game_engine.ECardCollections.Hand, ct,
                                            template_id=tg, nulling=True, gems=gem_type)
                    # Tell each client who draws first and who plays
                    # first.  Order matters: the Draw event first, then
                    # the Play event — the client derives the mulligan
                    # dialog line ("Your opponent chose to Play/Draw")
                    # from the LAST event that names the OPPONENT; each
                    # player's OWN pick resets it to Unknown.  With
                    # Draw-then-Play the picker's own client shows no
                    # opponent message and the opponent's client shows
                    # the correct "chose to Play/Draw".
                    g.push_player_wishes_to_draw_first(_hc.game_engine.UID.make(244, draw_first_pid))
                    g.push_player_wishes_to_play_first(_hc.game_engine.UID.make(244, play_first_pid))
                    # MulliganState uses the same active-player contract as
                    # C#: only play_first_pid starts with the dialog and
                    # priority. Both clients receive the same global phase
                    # owner; the waiting player does not get a temporary
                    # local priority that would open a second dialog.
                    mulligan_uid = _hc.game_engine.UID.make(
                        244, play_first_pid)
                    g.push_disable_interface(apid != play_first_pid)
                    g.push_green_light(
                        mulligan_uid, _hc.game_engine.EPriorityContext.Normal)
                    g.push_turn_phase(_hc.game_engine.ETurnPhases.Mulligan,
                                      mulligan_uid, mulligan_uid)
                    pkt = g.make_network_packet(pl)
                    dw = _hc.encode_datawrapper(0, 3055, _hc.compress_gzip(_hc.encode_sync_event(pkt)), 1,
                                            "00000000-0000-0000-0000-000000000000")
                    h.scnt += 1
                    h.send({"issuer": f"0.0.0.0.ServiceGameSession.246.{session.session_id}.{h.scnt}",
                            "target": "ServiceGameSession", "instance": str(session.server_id),
                            "reqid": 0, "c": 0, "conh": 0, "sid": h.sid}, dw)
                    _hc.log_req(f"    PvP choose: hand to pid {apid}")
            else:
                _push_to_both_players(session, self, lambda g, pt:
                    [g.push_turn_phase(_hc.game_engine.ETurnPhases.Mulligan, pt, pt),
                     g.push_green_light(pt, _hc.game_engine.EPriorityContext.Normal)], _hc.log_req)
            return True
        else:
            # The coin-flip winner chose Play or Draw. FRA can resolve this
            # branch for the AI as well as accepting the player's transaction;
            # the latter is only legal when the player won the toss.
            ai_choice = bool(getattr(transaction, "ai_choice", False))
            coin_winner_is_player = getattr(
                self, "_pve_coin_winner_is_player", True)
            if not coin_winner_is_player and not ai_choice:
                _hc.log_req("    PvE choose: ignored player pick; AI won coin toss")
                self._push_transaction_ack(session)
                return True
            chose_draw = b"ChooseDrawTransaction" in inner_bytes
            if (coin_winner_is_player and
                    getattr(self, "_pve_forced_draw_first", False)):
                if not chose_draw:
                    _hc.log_req("    PvE choose: Weight rejected Play; forcing Draw")
                chose_draw = True
                self._pve_forced_draw_first = False
            # A draw-first player draws on turn 1. The play/draw choice is
            # made by the coin-toss winner, so invert the meaning when the AI
            # won: AI Draw means the human plays first, AI Play means the
            # human draws first.
            self._pending_player_draws_first = (
                coin_winner_is_player == chose_draw)
            self._pending_play_first_is_player = (
                coin_winner_is_player != chose_draw)
            encounter_data = dict(session.encounter_data or {})
            encounter_data["_pve_ai_mulligan_done"] = False
            encounter_data["_pve_player_mulliganed"] = False
            encounter_data["_pve_play_first_is_player"] = bool(
                self._pending_play_first_is_player)
            encounter_data["_pve_player_draws_first"] = bool(
                self._pending_player_draws_first)
            if (session.session_name or "").startswith("camp_"):
                encounter_data["_campaign_mulligan_redraws"] = 0
            session.encounter_data = encounter_data
            session._persist()
            pl_t = _hc.game_engine.UID.make(244, int(self.client_reck_id))
            ai_t = _hc.game_engine.UID.make(_hc.game_engine.SERVICE_AI_UID_TYPE, 1000)
            game = _hc.game_engine.Game(session.session_id, pl_t, ai_t)
            # Draw the opening hands NOW — like PvP, PreGame and
            # PickGoesFirst show empty hands; each player draws after the
            # Play/Draw pick. Campaign uses the class/talent opening-hand
            # value for the player; the AI keeps the normal seven cards.
            # Campaign/PvE uses the campaign mulligan rule: the first redraw
            # replaces the opening hand at the same size.  Keep this counter
            # in the persisted session metadata so it survives the separate
            # transaction that carries the redraw request.  PvP retains its
            # existing one-fewer-card mulligan rule.
            if not _hc.db_game_get_hand(session.session_id,
                                    self.user_profile["id"]):
                # A champion with "can't draw cards" opens with no hand.
                if not self._champion_cannot_draw(session, player=True):
                    _hc.db_game_draw_cards(session.session_id,
                                       self.user_profile["id"],
                                       self._starting_hand_size(session))
                if not self._champion_cannot_draw(session, player=False):
                    _hc.db_game_draw_cards(session.session_id, 0, 7)
            player_hand = _hc.db_game_get_hand(session.session_id,
                                           self.user_profile["id"])
            ai_hand = _hc.db_game_get_hand(session.session_id, 0)
            opening_hand_projections = _hc.db_card_instance_dynamic_projections(
                session.session_id,
                [int(card_uid) for hand in (player_hand, ai_hand)
                 for card_uid, _template_guid in hand],
                conn=_hc._db)
            for idx, (cu, tg) in enumerate(player_hand):
                scid = _hc.game_engine.SessionCardId(
                    _hc.game_engine.UID(int(cu)))
                _tpl_g, ct, _nm, _c, _a, _d, gem_type = \
                    self._card_full_data(
                        game, scid, tg,
                        instance_projection=opening_hand_projections.get(int(cu)))
                game.push_card_updated(
                    scid, pl_t, _hc.game_engine.ECardCollections.Hand,
                    ct, template_id=tg, gems=gem_type)
            for idx, (cu, tg) in enumerate(ai_hand):
                scid = _hc.game_engine.SessionCardId(
                    _hc.game_engine.UID(int(cu)))
                _tpl_g, ct, _nm, _c, _a, _d, gem_type = \
                    self._card_full_data(
                        game, scid, tg,
                        instance_projection=opening_hand_projections.get(int(cu)))
                game.push_card_updated(
                    scid, ai_t, _hc.game_engine.ECardCollections.Hand,
                    ct, template_id=tg, nulling=True, gems=gem_type)
            if ai_choice:
                if chose_draw:
                    game.push_player_wishes_to_draw_first(ai_t)
                else:
                    game.push_player_wishes_to_play_first(ai_t)
            play_first_is_player = bool(
                self._pending_play_first_is_player)
            ai_mulligan_done = _hc._push_pve_mulligan_start(
                game, pl_t, ai_t, play_first_is_player,
                lambda: self._resolve_ai_mulligan(session, game, ai_t))
            if ai_mulligan_done:
                encounter_data = dict(session.encounter_data or {})
                encounter_data["_pve_ai_mulligan_done"] = True
                session.encounter_data = encounter_data
                session._persist()
            self._sync_rules_port_setup_checkpoint(
                session, _hc.game_engine.ETurnPhases.Mulligan, pl_t)
            if game.events:
                pkt = game.make_network_packet(pl_t)
                dw = _hc.encode_datawrapper(0, 3055, _hc.compress_gzip(_hc.encode_sync_event(pkt)), 1,
                                        "00000000-0000-0000-0000-000000000000")
                self._game_scnt = max(self._game_scnt, self.scnt) + 1
                self.scnt = self._game_scnt
                gs_inst = str(session.server_id)
                self.send({
                    "issuer": f"0.0.0.0.ServiceGameSession.{_hc.SERVICE_GAME_SESSION_UID}.{gs_inst}.{self.scnt}",
                    "target": "ServiceGameSession", "instance": gs_inst,
                    "reqid": 0, "c": 0, "conh": 0, "sid": self.sid,
                }, dw)
                _hc.log_req(f"    Pushed Mulligan phase ({len(dw)}b)")
            return True

    def _handle_mulligan_keep_transaction(self, session, transaction):
        """Resolve an accepted opening hand for PvP or Practice."""
        import hconnect_server as _hc
        handled = False
        if (session.session_name or "").startswith("tourney-"):
            from services.tournament_game import (
                pvp_load_state, pvp_save_state,
                pvp_mulligan_next, _pvp_push_mulligan_prompt,
                db_game_session_pids)
            # Track which player kept
            state = pvp_load_state(session)
            my_pid = int(self.client_reck_id)
            if state:
                from services.tournament_game import pvp_session_lock
                with pvp_session_lock(session):
                    # A keep can race with a retry or phase handoff. Reload
                    # under the session lock so a stale snapshot cannot roll
                    # the durable phase back after the first turn has begun.
                    state = pvp_load_state(session) or state
                    kept = state.get("kept") or []
                    mulligan_counts = state.get("mulligan_count") or {}
                    mulliganed = bool(int(mulligan_counts.get(
                        str(my_pid), mulligan_counts.get(my_pid, 0)) or 0))
                    # The client can resend AcceptStartingHand while
                    # the first response is still animating.  Treat a
                    # duplicate as an idempotent acknowledgement: an
                    # extra AcceptedStartingHand/priority packet can
                    # pop the visible Mulligan dialog on the opponent.
                    if my_pid in kept:
                        _hc.log_req(f"    PvP: duplicate keep from "
                                f"{my_pid}; ignored")
                    else:
                        kept.append(my_pid)
                        state["kept"] = kept
                        pvp_save_state(session, state)
                        # Tell BOTH clients the keeper's hand is
                        # accepted. The opponent also needs the event
                        # to recompute the keeper's deck count.
                        pl_t = _hc.game_engine.UID.make(244, my_pid)
                        pids = db_game_session_pids(session.session_id)
                        other_pid = pids[0] if pids[1] == my_pid else pids[1]
                        other_uid = _hc.game_engine.UID.make(244, other_pid)
                        for _keep_pid in pids:
                            _keep_h = _hc.player_handlers.get(_keep_pid)
                            if not _keep_h:
                                continue
                            g = _hc.game_engine.Game(int(session.session_id), pl_t, other_uid)
                            # The keeper has finished their decision.  Disable
                            # both clients while the other player's mulligan
                            # decision is pending; the next prompt enables
                            # only its asker.
                            g.push_disable_interface(True)
                            g.push_accepted_starting_hand(
                                pl_t, mulliganed=mulliganed)
                            pkt = g.make_network_packet(pl_t)
                            dw = _hc.encode_datawrapper(0, 3055, _hc.compress_gzip(_hc.encode_sync_event(pkt)), 1,
                                                     "00000000-0000-0000-0000-000000000000")
                            _keep_h.scnt += 1
                            _keep_h.send({
                                "issuer": f"0.0.0.0.ServiceGameSession.246.{session.session_id}.{_keep_h.scnt}",
                                "target": "ServiceGameSession",
                                "instance": str(session.server_id),
                                "reqid": 0, "c": 0, "conh": 0, "sid": _keep_h.sid,
                            }, dw)
                        _hc.log_req(f"    PvP: player {my_pid} kept hand (pushed to both)")
                        from services.tournament_game import pvp_mulligan_next
                        pvp_mulligan_next(session, state, my_pid)
            return True
        else:
            pl_t = _hc.game_engine.UID.make(244, int(self.client_reck_id))
            ai_t = _hc.game_engine.UID.make(_hc.game_engine.SERVICE_AI_UID_TYPE, 1000)
            # The selected Fortune is consumed when the human finishes the
            # opening-hand decision.  ReadyToStartGame has its own local
            # campaign-id variable, so derive it again here rather than
            # relying on that unrelated handler scope.
            try:
                camp_id = int((session.session_name or "camp_0").split("_")[-1]
                              or 0) if (session.session_name or "").startswith(
                                  "camp_") else 0
            except (TypeError, ValueError):
                camp_id = 0
        if not handled:
            game = _hc.game_engine.Game(session.session_id, pl_t, ai_t)
            game.player_resources = 0
            game.player_total_resources = 0
            game.max_hand_size = self._max_hand_size(session)
            setup_encounter = dict(session.encounter_data or {})
            ai_mulligan_done = bool(setup_encounter.get(
                "_pve_ai_mulligan_done", False))
            player_mulliganed = bool(setup_encounter.get(
                "_pve_player_mulliganed", False))
            play_first_is_player = bool(setup_encounter.get(
                "_pve_play_first_is_player",
                getattr(self, "_pending_play_first_is_player", True)))
            player_draws_first = setup_encounter.get(
                "_pve_player_draws_first",
                getattr(self, "_pending_player_draws_first", None))
            # Get cards in hand
            rows = _hc.db_hand_cards_raw(session.session_id, self.user_profile["id"])
            hand_cards = []  # list of (scid, cost)
            for r in rows:
                scid = _hc.game_engine.SessionCardId(_hc.game_engine.UID(r[0]))
                instance_id = r[1]
                # Resolve template data via the stored template_guid — one
                # path for both instance-based (FRA) and GUID (campaign/AI)
                # cards.
                t = self._template_by_guid(r[2])
                if t:
                    tpl_guid, ct_name, _name, cost, atk, def_ = t
                    ct = _hc.game_engine.card_type_from_db(ct_name)
                else:
                    ct = _hc.game_engine.ECardTypes.Troop
                    cost, atk, def_ = 0, 0, 0
                hand_cards.append((scid, cost))
                # Full card data from the template via _card_full_data so
                # the re-pushed hand card keeps its thresholds, abilities,
                # ATTRIBUTES (e.g. Flight) and any stat mods — a bare
                # CardDef("Card", ...) without attributes wipes the Flight
                # keyword the client renders from the attributes bitmask.
                _tpl_g, ct, _nm, _c, _a, _d, gem_type = self._card_full_data(
                    game, scid, r[2], instance_id)
                game.push_card_updated(scid, pl_t, _hc.game_engine.ECardCollections.Hand, ct,
                    template_id=r[2], gems=gem_type)

            # The human kept: push the starting-hand acceptance + the AI's
            # mulligan decision, then enter the StartGame phase. The server
            # pushes ONE phase at a time; StartTurn/Ready/Prep/Draw/
            # FirstMainPhase are advanced one-by-one as the client (auto-)
            # passes, driven by the 3029 PassPriority handler.
            if (session.session_name or "").startswith("camp_"):
                _hc.campaign.apply_starting_hand_talents(
                    self, _hc._db, session, game, pl_t,
                    getattr(self, "_campaign_starting_hand_effects", []))
            game.push_accepted_starting_hand(
                pl_t, mulliganed=player_mulliganed)
            # A human keep passes priority to the AI if it has not already
            # completed its active-first mulligan during setup.
            if not ai_mulligan_done:
                game.push_green_light(
                    ai_t, _hc.game_engine.EPriorityContext.Normal)
                game.push_turn_phase(
                    _hc.game_engine.ETurnPhases.Mulligan, ai_t, ai_t)
                self._resolve_ai_mulligan(session, game, ai_t)
            setup_encounter = dict(session.encounter_data or {})
            for transient_key in (
                    "_pve_ai_mulligan_done",
                    "_pve_player_mulliganed",
                    "_pve_play_first_is_player",
                    "_pve_player_draws_first"):
                setup_encounter.pop(transient_key, None)
            session.encounter_data = setup_encounter
            session._persist()
            first_turn_uid = pl_t if play_first_is_player else ai_t
            game.push_turn_phase(_hc.game_engine.ETurnPhases.StartGame,
                                 first_turn_uid, first_turn_uid)
            # Initialise the DB-backed battle state with the player who won the
            # subsequent Play/Draw choice. (The choice can belong to the AI.)
            # The client shows NO interaction during StartGame (no BattleState
            # is pushed for it), so a pass can never arrive — the server
            # advances StartGame -> StartTurn itself below.
            from rules_port import lifecycle as _be
            bstate = _be.default_state(
                turn_player=_be.PLAYER if play_first_is_player else _be.AI)
            setup_encounter = session.encounter_data or {}
            fra_start_state = (setup_encounter.get(
                "_fra_starting_battle_state", {})
                if isinstance(setup_encounter, dict) else {})
            # PreGame resources/thresholds (a champion's setup gains) apply
            # unless the saved start state already carries them.
            fra_start_state = {**(getattr(self, "_pregame_resource_seed", None) or {}),
                               **fra_start_state}
            bstate["player_resources"] = int(
                fra_start_state.get("player_resources", 0) or 0)
            bstate["player_total_resources"] = int(
                fra_start_state.get("player_total_resources", 0) or 0)
            bstate["ai_resources"] = int(
                fra_start_state.get("ai_resources", 0) or 0)
            bstate["ai_total_resources"] = int(
                fra_start_state.get("ai_total_resources", 0) or 0)
            for side in ("player", "ai"):
                thresholds = fra_start_state.get(f"{side}_threshold", {})
                bstate[f"{side}_threshold"] = {
                    int(color): int(value or 0)
                    for color, value in (thresholds or {}).items()
                }
            bstate["ai_resource_played_this_turn"] = False
            bstate["ai_resource_plays_this_turn"] = 0
            bstate["player_resource_plays_this_turn"] = 0
            bstate["player_charges"] = int(fra_start_state.get(
                "player_charges", getattr(self, "_player_starting_charges", 0)) or 0)
            bstate["ai_charges"] = int(fra_start_state.get(
                "ai_charges", getattr(self, "_ai_starting_charges", 0)) or 0)
            # Champion starting health (persisted for combat + reconnect).
            bstate["player_health"] = int(fra_start_state.get(
                "player_health", getattr(self, "_player_starting_health", 20)) or 20)
            bstate["ai_health"] = int(fra_start_state.get(
                "ai_health", getattr(self, "_ai_starting_health", 10)) or 10)
            # Attach before opening GameStarted triggers.  Otherwise the first
            # gameplay event would be resolved by the compatibility scanner,
            # leaving the session with a mixed native/legacy lifecycle from
            # the very first chain item.
            self._maybe_attach_rules_port(session, game, bstate)
            # A re-entrant attach refreshes this fresh seed with the shared
            # checkpoint created during setup, before PreGame modifiers ran.
            # That snapshot has no charge count, so re-apply the PreGame seed
            # (e.g. Fury's +2 charges) here or the starting charges are lost.
            bstate["player_charges"] = int(fra_start_state.get(
                "player_charges", getattr(self, "_player_starting_charges", 0)) or 0)
            bstate["ai_charges"] = int(fra_start_state.get(
                "ai_charges", getattr(self, "_ai_starting_charges", 0)) or 0)
            # Reapplying the challenge snapshot after RulesPort attachment
            # mirrors the charge restoration above and keeps the saved
            # round-zero resource/health mods from being overwritten by the
            # setup checkpoint created before they were applied.
            for key in ("player_resources", "player_total_resources",
                        "ai_resources", "ai_total_resources",
                        "player_health", "ai_health"):
                if key in fra_start_state:
                    bstate[key] = int(fra_start_state.get(key, 0) or 0)
            for side in ("player", "ai"):
                key = f"{side}_threshold"
                if key in fra_start_state:
                    bstate[key] = {
                        int(color): int(value or 0)
                        for color, value in
                        (fra_start_state.get(key) or {}).items()
                    }
            native_port = getattr(session, "_rules_port_session", None)
            first_turn_started_natively = False
            if native_port is not None:
                # The setup packet contains the client's non-interactive
                # StartGame phase, but the cached native scheduler is still
                # sitting on its Mulligan priority action. Leave that action
                # before invoking either the human or AI turn driver.
                if native_port.begin_first_turn(first_turn_uid):
                    # Entering StartTurn already dispatched TurnStartedEvent.
                    first_turn_started_natively = True
                    _hc.log_req(
                        "    RulesPort began first turn: "
                        f"active={native_port.active_player_id!r}")
            # Fortune readings are opponent-owned encounter effects. Resolve
            # the selected card through the normal metadata/BOM path so its
            # target templates choose the player's champion for
            # EachOpposingChampion, while EachChampion effects still affect
            # both champions. Sapphire's opening-hand modifier was applied
            # before the hand was dealt above.
            fortune_ability = getattr(
                self, "_campaign_fortune_ability_guid", None)
            fortune_guid = getattr(self, "_campaign_fortune_guid", None)
            if fortune_ability:
                self._current_bstate = bstate
                try:
                    from rules_port.resolution import resolve_port_ability
                    fortune_source = int(self._ai_champ_scid.uid.uid64)
                    fortune_log = resolve_port_ability(
                        self, game, session, _hc._db, pl_t, ai_t, bstate,
                        fortune_ability, fortune_source, 0, target_map={})
                    _hc.log_req(f"    Fortune {fortune_guid}: {fortune_log}")
                except Exception as exc:
                    _hc.log_req(f"    Fortune {fortune_guid} error: {exc}")
                finally:
                    for key in ("player_mod_target", "player_spell_target",
                                "resolving_target_uid", "resolving_effect_guid"):
                        bstate.pop(key, None)
                    _hc.campaign.consume_fortune(_hc._db, camp_id, fortune_guid)
                    self._campaign_fortune_guid = None
                    self._campaign_fortune_ability_guid = None
            game.player_threshold = dict(bstate.get("player_threshold", {}))
            game.player_charges = bstate.get("player_charges", 0)
            game.player_resources = bstate.get("player_resources", 0)
            game.player_total_resources = bstate.get("player_total_resources", 0)
            game.ai_threshold = dict(bstate.get("ai_threshold", {}))
            game.ai_charges = bstate.get("ai_charges", 0)
            game.ai_resources = bstate.get("ai_resources", 0)
            game.ai_total_resources = bstate.get("ai_total_resources", 0)
            game.player_health = bstate.get("player_health", 20)
            game.ai_health = bstate.get("ai_health", 10)
            # Apply any phase stops the client configured before the battle
            # state existed (SetTurnPhasesTransaction during setup), or the
            # player's saved preferences from previous battles.
            if getattr(self, "_pending_player_stops", None) is not None:
                pending_stops = self._pending_player_stops
                if (isinstance(pending_stops, (tuple, list)) and
                        len(pending_stops) >= 2):
                    bstate["player_self_stops"], bstate["player_opp_stops"] = pending_stops[:2]
                self._pending_player_stops = None
            else:
                saved_s, saved_o = self._load_player_stops(self.user_profile["id"])
                if saved_s is not None:
                    bstate["player_self_stops"] = saved_s
                if saved_o is not None:
                    bstate["player_opp_stops"] = saved_o
            # A draw-first player draws a card on their first turn.
            if player_draws_first is not None:
                bstate["player_draws_first_turn"] = bool(player_draws_first)
            self._pending_player_draws_first = None
            self._pending_play_first_is_player = None
            bstate["turn_player"] = (
                _be.PLAYER if play_first_is_player else _be.AI)
            _be.save_state(session, bstate)
            # "At the start of the game" triggers for both players'
            # opening-hand / warzone cards (e.g. Princess Victoria).
            game_started_event_start = len(game.events)
            # One dispatch: trigger discovery already scans both sides, so
            # dispatching once per owner ran every start-of-game ability twice
            # (Zuba's Expendable Lives granted its Deathcry twice, and Early
            # Sprouts' resources were applied twice).
            self._dispatch_native_trigger(
                game, session, pl_t, ai_t, bstate,
                "GameStartedEvent", None, self.user_profile["id"],
                zones=("hand", "warzone"))
            # GameStarted triggers are mandatory setup work, not a priority
            # window.  Drain their normal chain in LIFO order before the
            # StartGame packet is sent; otherwise _advance_to_priority leaves
            # the client waiting for a manual pass during the opening flow.
            # A real choice (target, revealed card, or discard) still pauses
            # here and is sent to the client normally.
            self._autopass_game_started_chain(
                session, pl_t, ai_t, bstate, game)
            # The first turn starts like every turn, once the GameStarted chain has
            # granted its abilities (e.g. a scene mod's "at the start of your turn"):
            # TurnStartedEvent fires start-of-turn triggers, whichever player goes first.
            first_champ = (self._player_champ_scid if play_first_is_player
                           else getattr(self, "_ai_champ_scid", None))
            first_owner = self.user_profile["id"] if play_first_is_player else 0
            # Dispatching it again would resolve the start-of-turn abilities twice
            # (Cloaked Figure's Catalysis mills the next three cards a second time).
            if first_champ is not None and not first_turn_started_natively:
                self._dispatch_native_trigger(
                    game, session, pl_t, ai_t, bstate, "TurnStartedEvent",
                    int(first_champ.uid.uid64), first_owner)
                _hc.log_req(f"    First turn: TurnStartedEvent dispatched for owner {first_owner}")
            if (_be.stack_empty(bstate) and
                    not bstate.get("pending_choice") and
                    not bstate.get("pending_deck_search") and
                    not bstate.get("pending_trigger") and
                    not bstate.get("pending_discard_ability") and
                    not bstate.get("pending_conversation")):
                self._suppress_completed_game_started_chain_events(
                    game, game_started_event_start)
            # GameStarted abilities may change the DB-backed battle state.  In
            # particular, encounter-scene battleboard cards such as Savage
            # Lord's Resource Rich grant both current and permanent resources.
            # Persist after resolving them so reconnects and the next phase
            # load the same 1/1 state that was sent in the setup packet.
            _be.save_state(session, bstate)
            # Send the StartGame packet (one phase per packet).
            if game.events:
                pkt = game.make_network_packet(pl_t)
                dw = _hc.encode_datawrapper(0, 3055, _hc.compress_gzip(_hc.encode_sync_event(pkt)), 1, "00000000-0000-0000-0000-000000000000")
                self._game_scnt = max(self._game_scnt, self.scnt) + 1
                self.scnt = self._game_scnt
                gs_inst = str(session.server_id)
                self.send({
                    "issuer": f"0.0.0.0.ServiceGameSession.{_hc.SERVICE_GAME_SESSION_UID}.{session.session_id}.{self.scnt}",
                    "target": "ServiceGameSession", "instance": gs_inst, "reqid": 0, "c": 0, "conh": 0, "sid": self.sid,
                }, dw)
                self._event_q.append((self.scnt, dw, {}))
                _hc.log_req(f"    Pushed StartGame phase after keep ({len(dw)}b)")
            # Auto-pass non-stop phases (StartTurn -> Ready -> Prep -> Draw)
            # until the next stop phase (FirstMainPhase).  PvP sessions
            # advance via route_pvp_pass instead — calling the AI-turn
            # path here overwrites the PvP state with a human-vs-AI
            # battle state (turn_player etc.) and desyncs GreenLight.
            if not (session.session_name or "").startswith("tourney-"):
                if bstate.get("turn_player") == _be.AI:
                    self._run_ai_turn(session, pl_t, ai_t, bstate)
                else:
                    self._advance_to_priority(session, pl_t, ai_t, bstate)
        return True

    def _handle_discard_transaction(self, session, transaction, *, card_uid=None):
        """Process a discard selection and acknowledge it."""
        import hconnect_server as _hc
        inner_bytes = transaction.inner_bytes
        is_set_ability_data = transaction.is_set_ability_data
        is_ability_activate = transaction.is_ability_activate
        if ((session.session_name or "").startswith("tourney-")
                and not is_set_ability_data
                and not is_ability_activate):
            # Tournament PvP has its own persisted two-player phase
            # state.  Never let a discard transaction fall through to
            # the human-vs-AI checkpoint path: that would overwrite
            # turn_order_json and can jump the clients back to First
            # Main or even emit a false deck-out victory.
            from services.tournament_game import pvp_handle_discard
            handled = pvp_handle_discard(self, session, inner_bytes)
            if handled:
                self._push_transaction_ack(session)
                return True
        pl_t = _hc.game_engine.UID.make(244, int(self.client_reck_id))
        ai_t = _hc.game_engine.UID.make(_hc.game_engine.SERVICE_AI_UID_TYPE, 1000)
        from rules_port import lifecycle as _be
        gd = _hc.game_engine.Game(session.session_id, pl_t, ai_t)
        card_uids = []
        if card_uid is not None:
            try:
                card_uids = [int(getattr(card_uid, "uid64", card_uid))]
            except (TypeError, ValueError):
                card_uids = []
        elif isinstance(inner_bytes, bytes):
            for m in re.finditer(rb'm_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});', inner_bytes):
                try:
                    card_uid = struct.unpack('<Q', bytes.fromhex(m.group(1).decode()))[0]
                    # Only Card-type UIDs (the champion/player UIDs are
                    # type 0/3 and must NOT be discarded).
                    if (card_uid & 0xFF) != 1:
                        continue
                except Exception:
                    continue
                card_uids.append(int(card_uid))
        for card_uid in card_uids:
            try:
                # Typed RulesPort discard IDs and raw legacy IDs share the
                # same owner/hand validation in the domain helper.
                if (int(card_uid) & 0xFF) != 1:
                    continue
                gd_owner, _owner_uid = self._discard_card_to_owner(
                    session, pl_t, ai_t, int(card_uid))
                if gd_owner:
                    gd.events.extend(gd_owner.events)
                _hc.log_req(f"    Discarded card {card_uid}")
            except Exception:
                continue
        _hc._db.commit()
        if gd.events:
            self._send_battle_events(session, gd, pl_t)
        # Discard down to the max hand size (7): each DiscardTransaction
        # moves ONE card, so stay in the Discard phase until the hand fits.
        # Only then advance to EndTurn.
        bstate = _be.load_state(session)
        self._current_bstate = bstate
        hand_count = _hc.db_hand_card_count(session.session_id, self.user_profile["id"])
        if bstate.get("pending_discard_native"):
            # Stargazer-style discard is a temporary native discard window,
            # not an end-of-turn hand-limit discard. Restore the phase and
            # clear the continuation after exactly one DiscardTransaction.
            return_phase_idx = bstate.pop("pending_discard_return_phase_idx", bstate.get("phase_idx", 0))
            bstate.pop("pending_discard_native", None)
            bstate.pop("pending_discard_ability", None)
            bstate.pop("pending_discard_ability_instance_id", None)
            bstate.pop("pending_discard_target_template", None)
            bstate.pop("pending_discard_scid", None)
            bstate["phase_idx"] = return_phase_idx
            _be.save_state(session, bstate)
            self._advance_to_priority(session, pl_t, ai_t, bstate)
            return True
        if (hand_count > self._max_hand_size(session) and
                not self._hand_size_unlimited(session, bstate)):
            g2 = self._fresh_game(session, pl_t, ai_t, bstate)
            g2.push_green_light(pl_t, _hc.game_engine.EPriorityContext.Normal)
            g2.push_player_updated(pl_t, champ_id=getattr(self, "_player_champ_scid", None))
            g2.push_player_updated(ai_t, champ_id=getattr(self, "_ai_champ_scid", None))
            self._send_battle_events(session, g2, pl_t)
            _hc.log_req(f"    Still over hand limit ({hand_count} cards) — re-granted priority to discard more")
        else:
            _be.advance_phase(bstate)
            _be.save_state(session, bstate)
            self._advance_to_priority(session, pl_t, ai_t, bstate)
            # Discarding down to size ends the turn. Only the keep and pass
            # paths run the AI's turn, so hand the turn to the AI driver here,
            # or its StartTurn waits for a pass that is never sent.
            bstate = _be.load_state(session)
            if bstate.get("turn_player") == _be.AI and not (
                    session.session_name or "").startswith("tourney-"):
                self._run_ai_turn(session, pl_t, ai_t, bstate)
        return True

    def _handle_mulligan_redraw_transaction(self, session, transaction):
        """Redraw the opening hand for PvP or Practice."""
        import hconnect_server as _hc
        handled = False
        # PvP tournament: use reckoning id for game_cards.user_id.
        if (session.session_name or "").startswith("tourney-"):
            from services.tournament_game import (
                db_game_session_pids, pvp_load_state, pvp_save_state,
                pvp_session_lock)
            import random as _rp
            my_pid = int(self.client_reck_id)
            pl_t = _hc.game_engine.UID.make(244, my_pid)
            pids = db_game_session_pids(session.session_id)
            opp_pid = pids[0] if pids[1] == my_pid else pids[1]
            opp_t = _hc.game_engine.UID.make(244, opp_pid)
            from pvp_db import (db_rules_port_hand_rows,
                                db_rules_port_redraw_hand)
            hand_rows = db_rules_port_hand_rows(session.session_id, my_pid)
            redraw_count = len(hand_rows)
            draw_count = max(0, redraw_count - 1)
            _hc.log_req(f"    PvP mulligan redraw: {redraw_count} -> {draw_count} cards")
            # Save old card_uids so we can push them back to deck.
            old_scids = [_hc.game_engine.UID(row[1]) for row in hand_rows]
            _old_rows, new_rows = db_rules_port_redraw_hand(
                session.session_id, my_pid, draw_count)
            with pvp_session_lock(session):
                state = pvp_load_state(session)
                if state is not None:
                    mulligan_counts = dict(state.get("mulligan_count") or {})
                    key = str(my_pid)
                    mulligan_counts[key] = int(
                        mulligan_counts.get(key,
                                            mulligan_counts.get(my_pid, 0)) or 0) + 1
                    state["mulligan_count"] = mulligan_counts
                    pvp_save_state(session, state)
            # Push the updated hand to both clients. A priority handoff is
            # sent only after the player keeps or is auto-accepted at zero.
            for pid in pids:
                h = _hc.player_handlers.get(pid)
                if not h: continue
                is_me = (pid == my_pid)
                p_uid = _hc.game_engine.UID.make(244, pid)
                o_uid = _hc.game_engine.UID.make(244, opp_pid if is_me else my_pid)
                g = _hc.game_engine.Game(int(session.session_id), p_uid, o_uid)
                from rules_port.host_mutations import project_mulligan_cards
                project_mulligan_cards(
                    self, g, pl_t, _old_rows, new_rows,
                    new_player_uid=p_uid, reveal_new=is_me)
                # A C# MulliganTransaction redraw does not pass priority.
                # Keep the redrawer enabled and leave the opponent waiting;
                # only AcceptStartingHand hands priority to the other player.
                g.push_disable_interface(pid != my_pid)
                # The client uses this event to clear the local
                # BattleStateMulligan animation and refresh the hand
                # count.  CardMoved/CardUpdated alone can leave the
                # redraw state showing the old hand.
                g.push_player_mulliganed_hand(pl_t, draw_count)
                pkt = g.make_network_packet(p_uid)
                dw = _hc.encode_datawrapper(0, 3055, _hc.compress_gzip(_hc.encode_sync_event(pkt)), 1,
                                        "00000000-0000-0000-0000-000000000000")
                h.scnt += 1
                h.send({"issuer": f"0.0.0.0.ServiceGameSession.246.{session.session_id}.{h.scnt}",
                        "target": "ServiceGameSession", "instance": str(session.server_id),
                        "reqid": 0, "c": 0, "conh": 0, "sid": h.sid}, dw)
                _hc.log_req(f"    PvP mulligan: pushed redrawn hand to pid {pid}")
            if draw_count == 0:
                # C# auto-accepts a zero-card hand and passes priority.
                return self._handle_mulligan_keep_transaction(
                    session, transaction)
            # Redrawing retains the active player's priority and Mulligan UI.
            handled = True
        if not handled:
            pl_t = _hc.game_engine.UID.make(244, int(self.client_reck_id))
            ai_t = _hc.game_engine.UID.make(_hc.game_engine.SERVICE_AI_UID_TYPE, 1000)
            import random as _random
            import json as _redraw_json
            game2 = _hc.game_engine.Game(session.session_id, pl_t, ai_t)

            # Fetch deck's active_gems ONCE (not per card)
            deck_gems = {}
            arena_lookup = _hc.db_get_arena_state(self.user_profile["id"])
            deck_lookup_id = self._resolve_fra_deck_id(
                arena_lookup["deck_id"]) or 0
            from pvp_db import db_deck_active_gems
            active_gems = db_deck_active_gems(deck_lookup_id)
            if active_gems:
                try:
                    import json as _gem_json
                    deck_gems = _gem_json.loads(active_gems)
                except Exception:
                    pass

            # Count cards in hand
            from pvp_db import db_rules_port_hand_rows
            hand_rows = db_rules_port_hand_rows(
                session.session_id, self.user_profile["id"])
            redraw_count = len(hand_rows)
            is_campaign = (session.session_name or "").startswith("camp_")
            if is_campaign:
                encounter_data = dict(session.encounter_data or {})
                try:
                    redraws = max(0, int(encounter_data.get(
                        "_campaign_mulligan_redraws", 0) or 0))
                except (TypeError, ValueError):
                    redraws = 0
                # The first campaign redraw is penalty-free.  Preserve the
                # normal shrinking-hand behavior if a client attempts a
                # second redraw before accepting its hand.
                draw_count = (redraw_count if redraws == 0 else
                              max(0, redraw_count - 1))
                encounter_data["_campaign_mulligan_redraws"] = redraws + 1
                session.encounter_data = encounter_data
                session._persist()
            else:
                draw_count = max(0, redraw_count - 1)

            # Persist the redraw atomically; this RulesPort domain operation
            # returns the old/new rows for the client event projection below.
            from pvp_db import db_rules_port_redraw_hand
            old_rows, new_cards = db_rules_port_redraw_hand(
                session.session_id, self.user_profile["id"], draw_count)
            encounter_data = dict(session.encounter_data or {})
            encounter_data["_pve_player_mulliganed"] = True
            session.encounter_data = encounter_data
            session._persist()
            from rules_port.host_mutations import project_mulligan_cards
            project_mulligan_cards(
                self, game2, pl_t, old_rows, new_cards,
                move_new=False, draw_new=True, update_old_first=False)

            game2.push_player_mulliganed_hand(pl_t, len(new_cards))

            if game2.events:
                pkt = game2.make_network_packet(pl_t)
                dw = _hc.encode_datawrapper(0, 3055, _hc.compress_gzip(_hc.encode_sync_event(pkt)), 1, "00000000-0000-0000-0000-000000000000")
                self._game_scnt = max(self._game_scnt, self.scnt) + 1
                self.scnt = self._game_scnt
                gs_inst = str(session.server_id)
                self.send({
                    "issuer": f"0.0.0.0.ServiceGameSession.{_hc.SERVICE_GAME_SESSION_UID}.{session.session_id}.{self.scnt}",
                    "target": "ServiceGameSession", "instance": gs_inst, "reqid": 0, "c": 0, "conh": 0, "sid": self.sid,
                }, dw)
                self._event_q.append((self.scnt, dw, {}))
                _hc.log_req(f"    Mulligan redraw: {redraw_count} back, {draw_count} drawn")
            if draw_count == 0:
                # AuthoritativeSessionBase.Mulligan accepts the hand and
                # passes priority immediately when the reduced size is zero.
                return self._handle_mulligan_keep_transaction(
                    session, transaction)
            handled = True
            # MulliganTransaction retains priority in C#. The AI is asked only
            # after the human accepts, if the AI has not already gone first.
        return True


    def _handle_debug_cheat_transaction(self, session, transaction):
        """Execute a client DebugCheatTransaction and acknowledge it."""
        import hconnect_server as _hc
        from debug_cheats import handle_debug_cheat
        _hc.log_req("    Debug cheat — dispatching server-side action")
        handled = handle_debug_cheat(self, session, transaction.inner_bytes)
        if handled:
            self._push_transaction_ack(session)
        return True

    def _handle_encounter_mod_dialog_transaction(self, session, transaction):
        """Route the class-55 conversation acknowledgement to its owner."""
        if (session.session_name or "").startswith("tourney-"):
            # Tournament dialogs resolve through the RulesPort encounter-mod
            # resolver; with no attached host there is no legacy path.
            import hconnect_server as _hc
            _hc.log_req("    PvP RulesPort boundary: no attached host, "
                        "refusing legacy fallback")
            self._push_transaction_ack(session)
            return True
        return self._resolve_pending_conversation(session, transaction)

    def _handle_set_ability_data_transaction(self, session, transaction):
        """Resolve target data supplied for a pending ability prompt."""
        import hconnect_server as _hc
        inner_bytes = transaction.inner_bytes
        pl_t = _hc.game_engine.UID.make(244, int(self.client_reck_id))
        ai_t = _hc.game_engine.UID.make(_hc.game_engine.SERVICE_AI_UID_TYPE, 1000)
        _be = self._checkpoint_engine(session)
        bstate = _be.load_state(session)
        self._current_bstate = bstate
        if bstate.get("pending_trigger"):
            # The player answered a class-39 triggered-ability target
            # prompt (e.g. Solitary Exile's Deploy "Void another target
            # card").  Extract the chosen target (last Card-type UID in
            # the TargetMap), push the trigger onto the chain with that
            # target, and let the normal pass flow resolve it.
            chosen_uid = None
            if isinstance(inner_bytes, bytes):
                for m_du in re.finditer(
                        rb'm_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});',
                        inner_bytes):
                    try:
                        uid64 = struct.unpack('<Q', bytes.fromhex(m_du.group(1).decode()))[0]
                        if (uid64 & 0xFF) == 1:
                            chosen_uid = int(uid64)
                    except Exception:
                        continue
            pend = bstate.pop("pending_trigger")
            _port = getattr(session, "_rules_port_session", None)
            if _port is not None:
                _port.trigger_prompt_open = False
            ag = pend["ability_guid"]
            src = int(pend["source_uid"])
            inst_id = int(pend["instance_id"])
            from rules_port.resolution import trigger_event_fields
            _be.stack_push(bstate, {
                "kind": "trigger", "ability_guid": ag,
                "source_uid": src, "target_uid": chosen_uid,
                "instance_id": inst_id,
                **trigger_event_fields(pend),
            })
            _be.save_state(session, bstate)
            g = self._fresh_game(session, pl_t, ai_t, bstate)
            g.push_ability_on_chain(
                _hc.game_engine.SessionCardId(_hc.game_engine.UID(src)),
                _hc.game_engine.ResourceId.from_str(ag),
                ability_instance_id=inst_id)
            g.push_green_light(pl_t, _hc.game_engine.EPriorityContext.ResolveTopOfChain)
            self._send_battle_events(session, g, pl_t)
            self._push_transaction_ack(session)
            _hc.log_req(f"    Trigger target chosen: {ag[:8]} "
                    f"target={hex(chosen_uid) if chosen_uid else 'none'}")
            handled = True
        if bstate.get("pending_deck_search"):
            # The player answered a "search your deck" class-39 prompt
            # (e.g. Darkspire Priestess's Deathcry) — move the chosen
            # matching deck card into the controller's hand.
            handled = self._resolve_pending_deck_search(
                session, pl_t, ai_t, inner_bytes)
        if bstate.get("pending_discard_ability"):
            # The chosen card is the LAST Card-type UID in the transaction
            # (the TargetMap embeds it after the source card id).
            discard_card_uid = None
            if isinstance(inner_bytes, bytes):
                for m_du in re.finditer(rb'm_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});', inner_bytes):
                    try:
                        uid64 = struct.unpack('<Q', bytes.fromhex(m_du.group(1).decode()))[0]
                        if (uid64 & 0xFF) == 1:
                            discard_card_uid = int(uid64)
                    except Exception:
                        continue
            if not discard_card_uid:
                discard_card_uid = bstate.get("pending_discard_scid")
            row = None
            if discard_card_uid:
                from pvp_db import db_rules_port_hand_card
                row = db_rules_port_hand_card(
                    session.session_id, discard_card_uid,
                    self.user_profile["id"])
            if row and discard_card_uid is not None:
                # Discard to the card's OWNER (a Mind Grasp steal returns
                # to the AI's graveyard; a player card to the player's).
                gd_owner, owner_uid = self._discard_card_to_owner(
                    session, pl_t, ai_t, int(discard_card_uid))
                gd = gd_owner if gd_owner else _hc.game_engine.Game(session.session_id, pl_t, ai_t)
                tpl_d = "00000000-0000-0000-0000-000000000000"
                # Generic post-ability rule: re-grant priority to the
                # activating player after the discard resolves (the class-23
                # prompt consumed the priority window).
                gd.push_green_light(pl_t, _hc.game_engine.EPriorityContext.Normal)
                self._send_battle_events(session, gd, pl_t)
                self._push_main_phase_options(session, pl_t, ai_t)
                _hc.log_req(f"    Discard (class-23 follow-up): {discard_card_uid} owner={owner_uid} ({tpl_d})")
            else:
                _hc.log_req(f"    Discard (class-23 follow-up): no hand card uid={discard_card_uid}")
            bstate.pop("pending_discard_ability", None)
            bstate.pop("pending_discard_ability_instance_id", None)
            bstate.pop("pending_discard_target_template", None)
            bstate.pop("pending_discard_scid", None)
            # The discard is the terminal continuation of Stargazer's
            # draw-then-discard BOM.  Clear the resume cursor and port
            # activation checkpoint so the next activation starts at effect
            # zero and cannot inherit the previous player's discard state.
            bstate.pop("rules_port_resume_effect_order", None)
            bstate.pop("resolution_paused", None)
            try:
                port = getattr(session, "_rules_port_session", None)
                if port is not None:
                    port.pending_activation = None
                    port.persist()
            except Exception:
                pass
            _be.save_state(session, bstate)
        return True


    def _handle_commit_defense_transaction(self, session, transaction,
                                            *, declarations=None,
                                            authoritative_port=None):
        """Declare the player's blockers for the current combat."""
        import hconnect_server as _hc
        inner_bytes = transaction.inner_bytes
        from rules_port.persistence import load_state as load_checkpoint
        from rules_port.persistence import save_state as save_checkpoint
        pl_t = _hc.game_engine.UID.make(244, int(self.client_reck_id))
        ai_t = _hc.game_engine.UID.make(_hc.game_engine.SERVICE_AI_UID_TYPE, 1000)
        bstate = load_checkpoint(session)
        self._current_bstate = bstate
        attackers = {int(k): int(v) for k, v in (bstate.get("ai_attackers") or {}).items()}
        from pvp_db import db_warzone_card_uids
        player_troops = {int(row[0]) for row in db_warzone_card_uids(session.session_id, self.user_profile["id"])}
        all_uids = []
        if declarations is not None:
            for attacker, blockers in declarations:
                try:
                    all_uids.append(int(attacker))
                    all_uids.extend(int(blocker) for blocker in blockers)
                except (TypeError, ValueError):
                    continue
        elif isinstance(inner_bytes, bytes):
            for m in re.finditer(rb'm_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});', inner_bytes):
                try:
                    u = struct.unpack('<Q', bytes.fromhex(m.group(1).decode()))[0]
                    if (u & 0xFF) == 1:
                        all_uids.append(int(u))
                except Exception:
                    continue
        blockers_map = {}
        if declarations is not None:
            for attacker, blockers in declarations:
                try:
                    blockers_map[int(attacker)] = [int(blocker)
                                                   for blocker in blockers]
                except (TypeError, ValueError):
                    continue
        cur_attacker = None
        for u in all_uids:
            if declarations is not None:
                continue
            if u in attackers:
                cur_attacker = u
                blockers_map.setdefault(cur_attacker, [])
            elif cur_attacker is not None and u in player_troops:
                blockers_map[cur_attacker].append(u)
        # RulesPort has already validated and staged blocker assignment
        # against its shared Combat objects.  Project that accepted map
        # directly; a second legacy can_block pass could discard a valid
        # blocker when its SQLite-derived state lagged the port snapshot.
        port_blockers = {}
        port_attackers = {}
        current_ai_attackers = {
            int(value) for value in (bstate.get("ai_attackers") or {})
        }
        for combat in authoritative_port.combat_manager.combats:
            attacker = combat.attacker
            if attacker is None:
                continue
            attacker_id = int(getattr(
                getattr(attacker, "session_card_id", attacker),
                "uid64", getattr(attacker, "session_card_id", attacker)))
            if current_ai_attackers and attacker_id not in current_ai_attackers:
                continue
            port_attackers[attacker_id] = combat
            port_blockers[attacker_id] = []
            for blocker in combat.blockers:
                value = getattr(blocker, "session_card_id", blocker)
                value = getattr(value, "uid64", value)
                port_blockers[attacker_id].append(int(value))
        if port_attackers:
            attackers = {
                uid: int((bstate.get("ai_attackers") or {}).get(
                    str(uid), (bstate.get("ai_attackers") or {}).get(uid, 0)))
                for uid in port_attackers
            }
            blockers_map = port_blockers
        bstate["ai_blockers"] = {str(k): [str(b) for b in v]
                                 for k, v in blockers_map.items()}
        block_uids = {b for v in blockers_map.values() for b in v}
        if block_uids:
            _hc.db_bulk_blocker_state(session.session_id, list(block_uids))
        game = self._fresh_game(session, pl_t, ai_t, bstate)
        player_champ_scid = getattr(self, "_player_champ_scid", None) or _hc.game_engine.SessionCardId(pl_t)
        combats = []
        for u in attackers:
            scid = _hc.game_engine.SessionCardId(_hc.game_engine.UID(u))
            combat_id = self._wire_combat_id_for_attacker(
                session, ai_t, u, u & 0xFFFF)
            blockers = [_hc.game_engine.SessionCardId(_hc.game_engine.UID(int(b)))
                        for b in blockers_map.get(u, [])]
            game.push_blockers_assigned(combat_id, scid, player_champ_scid, blockers)
            cs = _hc.game_engine.CombatSessionEventArgs()
            cs.player_id = ai_t
            cs.id = combat_id
            cs.attacker = scid
            cs.blockers = blockers
            combats.append(cs)
        # A repeated commit of the same blocks must not announce them again:
        # each attacker/blocker pair's block triggers go on the chain once.
        turn = int(bstate.get("turn_number", 0) or 0)
        announced = set(bstate.get("blocker_events_announced") or [])
        fresh = []
        for u in attackers:
            new_blockers = []
            for blocker in blockers_map.get(u, []):
                key = f"{turn}:{int(u)}:{int(blocker)}"
                if key not in announced:
                    announced.add(key)
                    new_blockers.append(blocker)
            fresh.append((u, new_blockers))
        bstate["blocker_events_announced"] = sorted(announced)
        self._dispatch_blocker_events(game, session, bstate, fresh)
        if combats:
            game.push_combat_listing(pl_t, combats)
        self._send_battle_events(session, game, pl_t)
        block_summary = ", ".join(
            hex(int(k)) + "->" + (hex(int(v[0])) if v else "none")
            for k, v in blockers_map.items())
        _hc.log_req(f"    CommitDefense: {len(attackers)} attacker(s), "
                f"blocks={block_summary}")
        if authoritative_port is not None:
            # The native defense transaction already staged the blockers and
            # consumed the defender's DeclareDefense priority before this
            # compatibility projection ran.  Drive that completed native
            # action into DeclareDefensePriorityWindow; re-entering the AI
            # turn loop here re-published DeclareDefense and left the client
            # submitting a second (often empty) defense transaction.
            _hc.log_req(f"    PROBE commit before save: port={authoritative_port.current_turn_phase} "
                        f"phase_idx={bstate.get('phase_idx')} stack={len(bstate.get('stack') or [])} "
                        f"turn_phases={len(bstate.get('turn_phases') or [])}")
            save_checkpoint(session, bstate)
            # The commit consumes the defender's DeclareDefense window.  A
            # blocked trigger queued by this commit must not hold the port in
            # that window: pass the defender's priority so the port moves on to
            # DeclareDefensePriorityWindow, where the chain resolves.
            if (authoritative_port.current_turn_phase ==
                    _hc.game_engine.ETurnPhases.DeclareDefense and
                    authoritative_port.action_stack.priority_player_id is not None):
                authoritative_port.pass_player_priority(
                    authoritative_port.action_stack.priority_player_id)
                _hc.log_req("    PROBE commit passed defender window: "
                            f"port={authoritative_port.current_turn_phase}")
            self._advance_to_priority(session, pl_t, ai_t, bstate)
            # Tell the client the phase the port reached: the events built
            # before the advance still name DeclareDefense.
            phase_game = self._fresh_game(session, pl_t, ai_t, bstate)
            priority_uid = authoritative_port.action_stack.priority_player_id
            phase_game.push_turn_phase(
                authoritative_port.current_turn_phase,
                authoritative_port.active_player_id,
                priority_uid if priority_uid is not None else pl_t)
            self._send_battle_events(session, phase_game, pl_t)
            _hc.log_req(f"    PROBE commit after advance: port={authoritative_port.current_turn_phase} "
                        f"phase_idx={bstate.get('phase_idx')} stack={len(bstate.get('stack') or [])}")
            return True
        start_idx = bstate.get("ai_turn_phase_idx", 0)
        bstate.pop("ai_turn_phase_idx", None)
        save_checkpoint(session, bstate)
        self._run_ai_turn(session, pl_t, ai_t, bstate, start_idx=start_idx)
        return True

    def _handle_commit_attack_transaction(self, session, transaction,
                                          *, declarations=None,
                                          authoritative_port=None):
        """Declare the player's attackers for the current combat."""
        import hconnect_server as _hc
        inner_bytes = transaction.inner_bytes
        from rules_port.persistence import load_state as load_checkpoint
        from rules_port.persistence import save_state as save_checkpoint
        from rules_port.lifecycle import advance_phase as advance_checkpoint
        pl_t = _hc.game_engine.UID.make(244, int(self.client_reck_id))
        ai_t = _hc.game_engine.UID.make(_hc.game_engine.SERVICE_AI_UID_TYPE, 1000)
        bstate = load_checkpoint(session)
        self._current_bstate = bstate
        # Extract the attacking card UIDs: every Card-type SessionCardId
        # in the transaction that is in the player's warzone. The
        # defender (AI champion) is not in the player's warzone.
        attacker_uids = []
        if declarations is not None:
            for _defender, attackers in declarations:
                try:
                    attacker_uids.extend(int(attacker) for attacker in attackers)
                except (TypeError, ValueError):
                    continue
        elif isinstance(inner_bytes, bytes):
            for m_du in re.finditer(rb'm_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});', inner_bytes):
                try:
                    uid64 = struct.unpack('<Q', bytes.fromhex(m_du.group(1).decode()))[0]
                    if (uid64 & 0xFF) == 1:  # Card type
                        attacker_uids.append(int(uid64))
                except Exception:
                    continue
        # RulesPort has already validated and staged these declarations.  In
        # that live path the host is only projecting the accepted decision;
        # re-running the old legality loop here used to silently discard
        # attackers when its legacy state view lagged the port view.
        attackers = []
        staged = {
            int(getattr(combat.attacker, "session_card_id",
                        getattr(combat.attacker, "uid", combat.attacker)))
            for combat in authoritative_port.combat_manager.combats
            if combat.attacker is not None
        }
        attackers = [uid for uid in attacker_uids if uid in staged]
        # Persist declared attackers in battle state (DB) so combat
        # resolution and a reconnect can reconstruct them.
        ai_champ_uid = getattr(self, "_ai_champ_scid", None)
        ai_champ_uid64 = ai_champ_uid.uid.to_uint64() if ai_champ_uid else 0
        # Merge with any server-auto-declared ForceAttack ("Must
        # attack") attackers so a commit (or pass) never un-declares
        # them — e.g. Savage Raider is already attacking.
        existing = {int(k)
                    for k in (bstate.get("player_attackers") or {})}
        merged = existing | set(attackers)
        bstate["player_attackers"] = {
            str(u): str(ai_champ_uid64) for u in merged}
        save_checkpoint(session, bstate)
        _hc.log_req(f"    CommitTroopsToAttack: {len(attackers)} new attacker(s) -> "
                f"{[hex(u) for u in attackers]} (merged, total {len(merged)})")
        # Push the combat events: one AttackDeclared (class 27) per
        # attacker + a CombatListing (62) with a CombatSession (63) per
        # attacker (no blockers yet — the AI will pass DeclareDefense).
        # Also mark each attacker Attacking | HasAttacked | Tapped (the
        # client renders the tap + attack line from these states) and
        # persist in card_state for reconnect.
        game = _hc.game_engine.Game(session.session_id, pl_t, ai_t)
        ai_champ_scid = ai_champ_uid if ai_champ_uid else _hc.game_engine.SessionCardId(ai_t)
        combats = []
        # Only the newly committed attackers get events here — the
        # auto-declared ForceAttack troops already got their
        # AttackDeclared / CombatListing when the phase opened.
        new_attackers = [u for u in attackers if u not in existing]
        for i, u in enumerate(new_attackers):
            cid = _hc.game_engine.SessionCardId(_hc.game_engine.UID(u))
            combat_id = self._wire_combat_id_for_attacker(
                session, pl_t, u, u & 0xFFFF)
            game.push_attack_declared(combat_id, pl_t, ai_champ_scid, cid)
            trow = _hc.db_card_template_attrs_joined(session.session_id, int(u))
            tpl_guid = trow[0] if trow else None
            # Effective attributes = template static + instance-granted
            # (a SHIFTED Steadfast lives in card_attributes, not the
            # template — checking only ct.attributes would tap it).
            attrs = (trow[1] if trow and trow[1] else 0) | (trow[2] if trow and trow[2] else 0)
            # Mark the troop as attacking (and tapped UNLESS Steadfast —
            # Steadfast troops don't tap when attacking). Persist + push.
            state = (_hc.game_engine.ECardStates.Attacking |
                     _hc.game_engine.ECardStates.HasAttacked)
            if not (attrs & _hc.game_engine.ECardAttributes.Steadfast):
                state |= _hc.game_engine.ECardStates.Tapped
            from rules_port.host_mutations import apply_attacking_card_state
            apply_attacking_card_state(session, int(u), state)
            from pvp_db import db_card_state_value
            pushed_state = db_card_state_value(session.session_id, int(u))
            if not pushed_state:
                pushed_state = state
            from rules_port.host_mutations import project_attacking_card
            # Continuous attack-time keywords (for example Emberleaf
            # Duelist's "while this is attacking" Swiftstrike) are derived
            # from the authoritative state. Include them in the attack
            # CardUpdated so the client cache sees the same attributes used
            # by combat resolution.
            from rules_port.static_rules import effective_attributes
            attack_attributes = effective_attributes(
                _hc._db, session.session_id, bstate, int(u))
            project_attacking_card(self, game, cid, pl_t, tpl_guid,
                                   pushed_state, attack_attributes)
            # Attacking normally exhausts the troop.  Emit the same typed tap
            # event used by activated and AI attacks so data-defined effects
            # such as Spider Nest's granted "when this exhausts" ability fire
            # for player-declared attackers too.  Steadfast attackers do not
            # become tapped and therefore must not emit this event.
            if state & _hc.game_engine.ECardStates.Tapped:
                self._dispatch_native_trigger(
                    game, session, pl_t, ai_t, bstate, "CardTappedEvent",
                    int(u), self.user_profile["id"])
            cs = _hc.game_engine.CombatSessionEventArgs()
            cs.player_id = pl_t
            cs.id = combat_id
            cs.attacker = cid
            cs.blockers = []
            combats.append(cs)
            # Fire "when this attacks" triggers (e.g. Chimera Guard
            # Outrider: +[ATK] equal to this troop's [DEF] this turn).
            for event_type in ("CardAttackedEvent",
                               "CardAttackedOrBlockedEvent"):
                self._dispatch_native_trigger(
                    game, session, pl_t, ai_t, bstate, event_type, u,
                    self.user_profile["id"])
            from rules_port.context import EffectContext
            from rules_port.combat_effects import apply_rage
            apply_rage(EffectContext.from_rules_port(
                game, session, _hc._db, self, pl_t, ai_t, bstate,
                "", ability=None), u)
        if attackers:
            # The client raises one champion-scoped CardsAttackedEvent after
            # the complete attack declaration, carrying the total attacker
            # count for metadata conditions such as "three or more".
            player_champ = (getattr(self, "_player_champ_scid", None) or
                            _hc.game_engine.SessionCardId(pl_t))
            from rules_port.tac import _tac_attr_hash
            self._dispatch_native_trigger(
                game, session, pl_t, ai_t, bstate, "CardsAttackedEvent",
                int(player_champ.uid.uid64), self.user_profile["id"],
                event_tac={_tac_attr_hash("NumAttackers"): len(attackers)})
        _hc._db.commit()
        game.push_combat_listing(pl_t, combats)
        self._send_battle_events(session, game, pl_t)
        # Advance to DeclareAttackPriorityWindow (the next stop).
        advance_checkpoint(bstate)
        save_checkpoint(session, bstate)
        self._advance_to_priority(session, pl_t, ai_t, bstate)
        return True

    def _handle_assign_damage_transaction(self, session, transaction,
                                          *, assignments=None,
                                          native_combat=False):
        """Resolve the client's combat damage assignment transaction."""
        import hconnect_server as _hc
        inner_bytes = transaction.inner_bytes
        from rules_port.lifecycle import (
            advance_phase as advance_checkpoint,
            current_phase as current_checkpoint_phase,
            load_state as load_checkpoint, save_state as save_checkpoint)
        pl_t = _hc.game_engine.UID.make(244, int(self.client_reck_id))
        ai_t = _hc.game_engine.UID.make(_hc.game_engine.SERVICE_AI_UID_TYPE, 1000)
        bstate = load_checkpoint(session)
        self._current_bstate = bstate
        cur_phase = current_checkpoint_phase(bstate)
        native_phase_advanced = False
        from rules_port.session import _same_player_uid
        port = getattr(session, "_rules_port_session", None)
        if (cur_phase == _hc.game_engine.ETurnPhases.AssignDamage and port is not None and
                _same_player_uid(port.active_player_id, ai_t)):
            # The AI attacks this turn, so the damage is already resolved and the damage order is not ours to give.
            # Re-resolving on a stale order from the client would deal the damage twice.
            _hc.log_req("    AssignDamageOrder ignored: the AI is the attacking player")
            self._push_transaction_ack(session)
            return True
        if cur_phase == _hc.game_engine.ETurnPhases.AssignDamage:
            n_att = len(bstate.get("player_attackers") or {})
            # The AssignDamageOrderTransaction carries the player's
            # chosen blocker order (weakest-to-toughest) per combat:
            # m_AssignedDamageOrder -> DamageAssignment(CombatId,
            # ordered CardIds). Extract every card UID in stream order,
            # then order each attacker's blockers by their first
            # occurrence so resolve_combat assigns damage in that order.
            if assignments is not None:
                seq = []
                for _combat_id, card_ids in assignments:
                    try:
                        seq.extend(int(card_id) for card_id in card_ids)
                    except (TypeError, ValueError):
                        continue
            elif isinstance(inner_bytes, bytes):
                seq = []
                for m_du in re.finditer(rb'm_UID64;[^;]*;[^;]*;[^;]*;([0-9A-Fa-f]{16});', inner_bytes):
                    try:
                        v = struct.unpack('<Q', bytes.fromhex(m_du.group(1).decode()))[0]
                        if (v & 0xFF) == 1:
                            seq.append(int(v))
                    except Exception:
                        continue
                blockers = {int(k): set(int(b) for b in (v or []))
                            for k, v in (bstate.get("ai_blockers") or {}).items()}
                order_map = {}
                for att, bset in blockers.items():
                    ordered = [u for u in seq if u in bset]
                    ordered += [b for b in bset if b not in ordered]
                    if ordered:
                        order_map[att] = ordered
                if order_map:
                    bstate["player_damage_order"] = {str(k): [str(b) for b in v]
                                                     for k, v in order_map.items()}
                    save_checkpoint(session, bstate)
            _hc.log_req(f"    AssignDamageOrder (AssignDamage step): resolving {n_att} attackers")
            if native_combat:
                bstate = self._resolve_combat_damage(
                    session, pl_t, ai_t, bstate)
                # Combat damage is resolved by the host projection, but the
                # native scheduler still owns the phase cursor.  Its priority
                # action is intentionally waiting for this transaction, so a
                # normal ``tick()`` cannot advance it.  Clear that consumed
                # window and perform the native transition before asking the
                # RulesPort driver to project the next input window;
                # advancing only the compatibility checkpoint leaves the
                # port at AssignDamage and makes Mono submit empty orders in
                # a loop.
                port = getattr(session, "_rules_port_session", None)
                if port is not None:
                    # Drop only the consumed phase window: triggers raised by
                    # the damage keep their chain actions (see
                    # discard_phase_priority_windows).
                    port.discard_phase_priority_windows()
                    port.advance_turn_phase()
                    phases = list(bstate.get("turn_phases") or ())
                    try:
                        bstate["phase_idx"] = phases.index(
                            port.current_turn_phase)
                    except ValueError:
                        pass
                    native_phase_advanced = True
            else:
                bstate = self._resolve_combat_damage(session, pl_t, ai_t, bstate)
            save_checkpoint(session, bstate)
            # If the AI's champion is dead, end the game (player wins).
            if bstate.get("ai_health", 10) <= 0:
                _hc.log_req(f"    AI defeated! ai_health={bstate['ai_health']}")
                self._finish_battle_gameend(
                    session, True, winners=[pl_t], losers=[ai_t])
                handled = True
                self._push_transaction_ack(session)
                return True
        elif cur_phase == _hc.game_engine.ETurnPhases.AssignFirstStrikeDamage:
            # Swiftstrike step: FirstStrike/DualStrike combatants deal
            # damage now; casualties are removed before the normal step.
            _hc.log_req("    AssignDamageOrder (Swiftstrike step): "
                    "resolving first-strike damage")
            if native_combat:
                bstate = self._resolve_combat_damage(
                    session, pl_t, ai_t, bstate, first_strike=True)
                port = getattr(session, "_rules_port_session", None)
                if port is not None:
                    # Drop only the consumed phase window: triggers raised by
                    # the damage keep their chain actions (see
                    # discard_phase_priority_windows).
                    port.discard_phase_priority_windows()
                    port.advance_turn_phase()
                    phases = list(bstate.get("turn_phases") or ())
                    try:
                        bstate["phase_idx"] = phases.index(
                            port.current_turn_phase)
                    except ValueError:
                        pass
                    native_phase_advanced = True
            else:
                bstate = self._resolve_combat_damage(
                    session, pl_t, ai_t, bstate, first_strike=True)
            save_checkpoint(session, bstate)
        else:
            _hc.log_req(f"    AssignDamageOrder ({cur_phase}): no damage yet, advancing")
        if bstate.get("pending_deck_search"):
            # A combat-death deck-search prompt is awaiting the player's
            # answer — do NOT advance to the next phase yet (the phase
            # packet would tear the client's target picker down).  The
            # SetAbilityActivationData answer advances the turn.
            save_checkpoint(session, bstate)
            self._push_transaction_ack(session)
            _hc.log_req("    AssignDamage: paused for deck-search answer")
            return True
        # Advance to the next phase (SecondMain after AssignDamage).  Native
        # RulesPort already performed that transition above; applying the
        # compatibility cursor a second time would skip the next phase.
        if not native_phase_advanced:
            advance_checkpoint(bstate)
        save_checkpoint(session, bstate)
        self._advance_to_priority(session, pl_t, ai_t, bstate)
        return True

    def _handle_set_auto_pass_transaction(self, session, transaction):
        """Record and apply the client's one-turn auto-pass choice."""
        import hconnect_server as _hc
        inner_bytes = transaction.inner_bytes
        from rules_port import lifecycle as _be
        import re
        handled = False
        pl_t = _hc.game_engine.UID.make(244, int(self.client_reck_id))
        ai_t = _hc.game_engine.UID.make(_hc.game_engine.SERVICE_AI_UID_TYPE, 1000)
        # Tournament PvP has its own persisted two-player priority
        # state.  Do not feed its SetAutoPass through the PvE checkpoint:
        # that is the PvE/AI state machine and can advance a stale
        # turn_order, causing the other client to miss its resolve
        # priority.  Record F10 on the PvP state and let the normal
        # PvP pass route hand GreenLight between the clients.
        # m_PassingState: 1=Attack, 2=EndOfTurn, 3=EndPhase.
        st = 2
        if isinstance(inner_bytes, bytes):
            m = re.search(rb'm_PassingState;\d+;\d+;\d+;([0-9A-Fa-f]+);', inner_bytes)
            if m:
                try:
                    st = int(m.group(1), 16)
                except Exception:
                    pass
        if (session.session_name or "").startswith("tourney-"):
            try:
                from services.tournament_game import set_pvp_auto_pass
                if set_pvp_auto_pass(self, session, st):
                    self._push_transaction_ack(session)
                    handled = True
            except Exception as exc:
                _hc.log_req(f"    PvP SetAutoPass exception: {exc}")
        if handled:
            pass
        else:
            bstate = _be.load_state(session)
            self._current_bstate = bstate
            bstate["player_autopass"] = st
            _be.save_state(session, bstate)
            self._push_transaction_ack(session)
            _hc.log_req(f"    SetAutoPass: state={st} (player auto-passes to end of turn)")
            self._advance_to_priority(session, pl_t, ai_t, bstate)
        return True

    def _handle_cancel_auto_pass_transaction(self, session, transaction):
        """Clear the client's one-turn auto-pass choice."""
        import hconnect_server as _hc
        from rules_port import lifecycle as _be
        handled = False
        pl_t = _hc.game_engine.UID.make(244, int(self.client_reck_id))
        ai_t = _hc.game_engine.UID.make(_hc.game_engine.SERVICE_AI_UID_TYPE, 1000)
        if (session.session_name or "").startswith("tourney-"):
            try:
                from services.tournament_game import pvp_load_state, pvp_save_state
                pstate = pvp_load_state(session)
                if pstate is not None:
                    if int(pstate.get("autopass_pid", 0)) == int(self.client_reck_id):
                        pstate.pop("autopass_pid", None)
                        pstate.pop("autopass_state", None)
                        pvp_save_state(session, pstate)
                    self._push_transaction_ack(session)
                    _hc.log_req("    PvP CancelAutoPass: auto-pass cleared")
                    handled = True
            except Exception as exc:
                _hc.log_req(f"    PvP CancelAutoPass exception: {exc}")
        if not handled:
            bstate = _be.load_state(session)
            self._current_bstate = bstate
            bstate.pop("player_autopass", None)
            _be.save_state(session, bstate)
            self._push_transaction_ack(session)
            _hc.log_req("    CancelAutoPass: auto-pass cleared")
        return True

    def _handle_set_turn_phases_transaction(self, session, transaction):
        """Persist client phase stops and acknowledge the transaction."""
        import hconnect_server as _hc
        inner_bytes = transaction.inner_bytes
        self_stops = self._extract_enum_list(inner_bytes, "m_SelfTurnPhases", "m_OpponentTurnPhases")
        opp_stops = self._extract_enum_list(inner_bytes, "m_OpponentTurnPhases", None)
        # Persist for future battles.
        if self_stops is not None or opp_stops is not None:
            self._save_player_stops(self.user_profile["id"], self_stops, opp_stops)
        try:
            raw_to = session.turn_order
            if isinstance(raw_to, dict) and "turn_player" in raw_to:
                from rules_port import lifecycle as _bse
                bstate = _bse.load_state(session)
                if self_stops is not None:
                    bstate["player_self_stops"] = self_stops
                if opp_stops is not None:
                    bstate["player_opp_stops"] = opp_stops
                _bse.save_state(session, bstate)
                # If the AI turn is paused at an opponent-stop phase that
                # the player just removed from their stops, resume the AI
                # turn automatically instead of waiting for a pass.
                resume = bstate.get("ai_turn_phase_idx")
                if resume is not None:
                    paused_phase = _bse.TURN_PHASES[resume - 1]
                    if not _bse.is_opp_stop(bstate, paused_phase):
                        start_idx = resume
                        bstate.pop("ai_turn_phase_idx", None)
                        _bse.save_state(session, bstate)
                        _hc.log_req(f"    Stops updated — phase {paused_phase} no longer a stop; resuming AI turn")
                        pl_t = _hc.game_engine.UID.make(244, int(self.client_reck_id))
                        ai_t = _hc.game_engine.UID.make(_hc.game_engine.SERVICE_AI_UID_TYPE, 1000)
                        self._run_ai_turn(session, pl_t, ai_t, bstate, start_idx=start_idx)
            elif (session.session_name or "").startswith("tourney-"):
                # PvP: persist each player's stops into the PvP state,
                # keyed by their reckoning pid, so the auto-advance can
                # respect "stop on Ready (self or opponent)" etc.
                from services.tournament_game import (pvp_load_state,
                                                      pvp_save_state,
                                                      pvp_session_lock)
                with pvp_session_lock(session):
                    _pstate = pvp_load_state(session)
                    if _pstate:
                        my_pid = int(self.client_reck_id)
                        if self_stops is not None:
                            _pstate[f"stops_self_{my_pid}"] = self_stops
                        if opp_stops is not None:
                            _pstate[f"stops_opp_{my_pid}"] = opp_stops
                        pvp_save_state(session, _pstate)
                self._pending_player_stops = (self_stops, opp_stops)
            else:
                self._pending_player_stops = (self_stops, opp_stops)
        except (ValueError, TypeError):
            self._pending_player_stops = (self_stops, opp_stops)
        _hc.log_req(f"    Captured phase stops: self={self_stops} opp={opp_stops}")
        # No 3055 events are pushed for SetTurnPhases — send an empty
        # sync packet so the client's one-at-a-time transaction pipeline
        # (m_HasPreviousTransactionBeenRespondedByServer) is unblocked;
        # otherwise the next transaction (incl. Withdraw) is dropped.
        self._push_transaction_ack(session)
        return True

    def _handle_pass_priority_transaction(self, session, transaction):
        """Resolve a priority pass through the gameplay service boundary."""
        import hconnect_server as _hc
        pass_turn_phase = transaction.pass_turn_phase
        handled = False
        from rules_port import lifecycle as _be
        pl_t = _hc.game_engine.UID.make(244, int(self.client_reck_id))
        ai_t = _hc.game_engine.UID.make(_hc.game_engine.SERVICE_AI_UID_TYPE, 1000)
        # A pass arriving during setup (PreGame/PickGoesFirst/Mulligan,
        # before the player has kept their opening hand) has no turn-cycle
        # battle state yet — session.turn_order is still the default [].
        # Treat it as a no-op pass: acknowledge the transaction but do
        # NOT advance the turn, or it would corrupt the setup flow. The
        # client normally sends no transaction during PreGame (the
        # priority window auto-completes), but this is defensive.
        try:
            raw_to = session.turn_order
            setup_in_progress = not (isinstance(raw_to, dict) and "turn_player" in raw_to)
        except (ValueError, TypeError):
            setup_in_progress = True
        if setup_in_progress:
            from services.tournament_game import route_pvp_pass
            try:
                _rpv = route_pvp_pass(self, session)
            except Exception as _e:
                import traceback
                _hc.log_req(f"    PvP pass exception: {_e}")
                traceback.print_exc()
                _rpv = True
            if not _rpv:
                _hc.log_req("    Pass during setup phase — no-op (battle not started)")
            self._push_transaction_ack(session)
            handled = True
        else:
            bstate = _be.load_state(session)
            self._current_bstate = bstate
            _hc.log_req(f"    Pass priority: turn={bstate.get('turn_player')} priority=player "
                    f"phase={_be.current_phase(bstate)} (client={pass_turn_phase})")

            # The client also auto-passes non-stop phases, which race with
            # the server's own auto-advance. Ignore a stale pass whose
            # phase no longer matches the server's current phase.
            # A pending AI card can survive a phase-list rebuild (for example
            # when the AI gains an attack phase after its main phase). The
            # client still submits the phase it was shown when it received
            # priority; do not strand that chain item as a stale pass.
            pending_ai_chain = (
                not _be.stack_empty(bstate)
                and bstate.get("turn_player") == _be.AI
                and bstate.get("ai_turn_phase_idx") is not None)
            # A paused AI stop stores the resume cursor one phase past the
            # stop. If a late packet left phase_idx at an earlier AI phase,
            # reconcile it only when that cursor identifies the exact phase
            # shown by the client. This prevents a legitimate post-combat pass
            # from being discarded while still rejecting arbitrary stale
            # passes.
            if (pass_turn_phase is not None
                    and pass_turn_phase != _be.current_phase(bstate)
                    and bstate.get("turn_player") == _be.AI
                    and bstate.get("ai_turn_phase_idx") is not None):
                held = _be.ai_held_phase_context(bstate)
                if held is not None and held[0] == pass_turn_phase:
                    _held_phase, held_idx, held_phases = held
                    bstate["turn_phases"] = list(held_phases)
                    bstate["phase_idx"] = held_idx
                    _be.save_state(session, bstate)
                    _hc.log_req(f"    Reconciled stale AI phase_idx to held stop "
                            f"phase {pass_turn_phase}")
            if (pass_turn_phase is not None
                    and pass_turn_phase != _be.current_phase(bstate)
                    and not pending_ai_chain):
                _hc.log_req(f"    Ignored stale pass (client phase {pass_turn_phase} != {_be.current_phase(bstate)})")
                self._push_transaction_ack(session)
                handled = True
            elif (_be.stack_empty(bstate) and
                  bstate.get("turn_player") == _be.AI):
                # The human passed during the AI's turn (an opponent-stop
                # phase). Resume the AI turn from the next phase.
                start_idx = bstate.get("ai_turn_phase_idx", 0)
                bstate.pop("ai_turn_phase_idx", None)
                _be.save_state(session, bstate)
                _hc.log_req(f"    Human passed during AI turn — resuming AI at idx {start_idx}")
                self._run_ai_turn(session, pl_t, ai_t, bstate, start_idx=start_idx)
                handled = True
            elif _be.current_phase(bstate) in (_hc.game_engine.ETurnPhases.EndTurn,
                                               _hc.game_engine.ETurnPhases.Discard):
                # Hand the human's turn to the AI. The "end of your turn" triggers are not dispatched
                # here: the turn flow fires them once, when it enters the EndTurn phase, and a pass at
                # that stop only hands the turn over.
                g_end2 = self._fresh_game(session, pl_t, ai_t, bstate)
                self._clear_combat_damage(session, bstate)
                self._clear_expired_temporary_attributes(
                    session, bstate, self.user_profile["id"], "end_turn",
                    clear_stat_buffs=True)
                from pvp_db import db_rules_port_warzone_projection
                for cu, tpl, card_user_id, card_state in db_rules_port_warzone_projection(
                        session.session_id):
                    scid = _hc.game_engine.SessionCardId(_hc.game_engine.UID(cu))
                    _tpl, ct, _n, _c, _a, _d, _g = self._card_full_data(
                        g_end2, scid, tpl)
                    g_end2.push_card_updated(
                        scid, pl_t if card_user_id else ai_t,
                        _hc.game_engine.ECardCollections.Warzone, ct,
                        template_id=tpl,
                        state=int(card_state or 0))
                self._send_battle_events(session, g_end2, pl_t)
                if bstate.get("_rules_port_attached"):
                    from rules_port.lifecycle import complete_turn
                    next_player = complete_turn(bstate)
                else:
                    next_player = _be.next_turn_player(bstate)
                    bstate["turn_player"] = next_player
                    bstate["player_passed"] = False
                    bstate["ai_passed"] = False
                    bstate["phase_idx"] = 0
                    bstate["turn_phases"] = _be.BASE_TURN_PHASES
                    bstate[f"{next_player}_resource_played_this_turn"] = False
                    bstate[f"{next_player}_resource_plays_this_turn"] = 0
                _be.save_state(session, bstate)
                if next_player == _be.PLAYER:
                    self._advance_to_priority(session, pl_t, ai_t, bstate)
                    _hc.log_req("    Bonus turn kept player in control")
                else:
                    self._run_ai_turn(session, pl_t, ai_t, bstate)
                    _hc.log_req("    Turn passed to AI; AI turn played out")
            else:
                # A non-empty chain: passing resolves the top of the chain
                # instead of advancing the phase (troops/spells/abilities/
                # triggers). After resolving, if the chain is still non-empty
                # re-grant priority (the player can respond to the next
                # item); when it empties, push ChainEmpty and advance.
                if not _be.stack_empty(bstate):
                    _be.stack_set_pass(bstate, _be.PLAYER, True)
                    if not _be.stack_both_passed(bstate):
                        # The AI hasn't passed yet — it auto-passes, so
                        # count it now and resolve.
                        _be.stack_set_pass(bstate, _be.AI, True)
                    if _be.stack_both_passed(bstate):
                        item = _be.stack_pop(bstate)
                        _be.stack_reset_passes(bstate)
                        if item is None:
                            _hc.log_req("    RulesPort rejected empty chain item after both passes")
                            self._push_transaction_ack(session)
                            return True
                        # A FRESH game seeded from battle state so any
                        # PlayerUpdated pushed during resolution (e.g.
                        # after a heal) reports the real health/resources.
                        gs = self._fresh_game(session, pl_t, ai_t, bstate)
                        self._resolve_stack_item(session, pl_t, ai_t, bstate, item, gs)
                        if (_be.stack_empty(bstate) and
                                not bstate.get("pending_choice") and
                                not bstate.get("pending_trigger") and
                                not bstate.get("pending_deck_search") and
                                not bstate.get("pending_conversation")):
                            gs.push_chain_empty()
                        cur_phase = _be.current_phase(bstate)
                        pending_input = (bstate.get("pending_choice") or
                                         bstate.get("pending_trigger") or
                                         bstate.get("pending_deck_search") or
                                         bstate.get("pending_conversation"))
                        paused_ai_idx = (
                            bstate.get("ai_turn_phase_idx")
                            if (_be.stack_empty(bstate) and not pending_input and
                                bstate.get("turn_player") == _be.AI)
                            else None)
                        if paused_ai_idx is not None:
                            # The chain interrupted an AI phase. Once the
                            # human resolves the item, continue the AI from
                            # its saved cursor; granting another human
                            # greenlight here strands the turn between phases.
                            bstate.pop("ai_turn_phase_idx", None)
                            _be.save_state(session, bstate)
                            self._send_battle_events(session, gs, pl_t)
                            self._run_ai_turn(
                                session, pl_t, ai_t, bstate,
                                start_idx=int(paused_ai_idx))
                        else:
                            # Priority to the active player for the next item
                            # (or the next phase). ResolveTopOfChain keeps the
                            # pass button labelled "Resolve <Card>".
                            if (_be.stack_empty(bstate) and not pending_input and
                                    bstate.get("turn_player") == _be.PLAYER):
                                if cur_phase in (
                                        _hc.game_engine.ETurnPhases.FirstMainPhase,
                                        _hc.game_engine.ETurnPhases.SecondMainPhase):
                                    # A troop may have entered during this chain
                                    # (notably a Speed troop). Recompute before
                                    # rebuilding the green light so the next
                                    # prompt immediately offers ProceedToCombat.
                                    if cur_phase == _hc.game_engine.ETurnPhases.FirstMainPhase:
                                        bstate["player_has_ready_troop"] = self._player_can_attack_troops(session)
                                    bstate["turn_phases"] = _be.build_turn_phases(bstate)
                                    _be.save_state(session, bstate)
                                # The chain fully emptied on the player's turn:
                                # re-announce the current phase with the player as
                                # priority player so the client pushes a FRESH state.
                                # Without this the prior state's cached button tail
                                # (the resolved card's name, set only under
                                # ResolveTopOfChain) lingers — the pass button shows
                                # "Continue to Second Main Phase <CardName>".
                                if cur_phase is not None:
                                    gs.push_turn_phase(cur_phase, pl_t, pl_t)
                                    self._push_phase_options(
                                        session, pl_t, ai_t, cur_phase)
                            if not pending_input:
                                gs.push_green_light(pl_t, self._priority_context_for(
                                    cur_phase, bstate))
                            self._send_battle_events(session, gs, pl_t)
                        _hc.log_req(f"    Resolved chain item {item.get('kind')} "
                                f"({item.get('ability_guid', '')[:8] or hex(item.get('source_uid') or 0)})")
                    else:
                        self._push_transaction_ack(session)
                    handled = True
                else:
                    # The player passed a stop phase: advance one phase, then
                    # auto-pass non-stop phases until the next stop phase.
                    bstate["player_passed"] = True
                    bstate["ai_passed"] = True
                    # Passing the FirstMainPhase decides whether combat
                    # happens. Recompute AFTER any troop the player played
                    # this main phase has resolved to the warzone — a Speed
                    # (haste) troop can attack the turn it enters, so the
                    # decision is made here, not just at Prep.
                    if (_be.current_phase(bstate) ==
                            _hc.game_engine.ETurnPhases.FirstMainPhase and
                            bstate.get("turn_player") == _be.PLAYER):
                        bstate["player_has_ready_troop"] = self._player_can_attack_troops(session)
                        bstate["turn_phases"] = _be.build_turn_phases(bstate)
                        _be.save_state(session, bstate)
                        _hc.log_req(f"    FirstMain pass: combat={bstate['player_has_ready_troop']} "
                                f"phases={len(bstate['turn_phases'])}")
                    # If the pass ends the AssignDamage step, resolve the
                    # combat damage (unblocked attackers hit the champion)
                    # before moving on. AssignDamageOrderTransaction is the
                    # normal path; a plain pass here is the fallback.
                    if (_be.current_phase(bstate) in (
                            _hc.game_engine.ETurnPhases.AssignDamage,
                            _hc.game_engine.ETurnPhases.AssignFirstStrikeDamage)):
                        fs = (_be.current_phase(bstate) ==
                              _hc.game_engine.ETurnPhases.AssignFirstStrikeDamage)
                        bstate = self._resolve_combat_damage(
                            session, pl_t, ai_t, bstate, first_strike=fs)
                        _be.save_state(session, bstate)
                        if bstate.get("ai_health", 10) <= 0:
                            _hc.log_req(f"    AI defeated via pass at AssignDamage! ai_health={bstate['ai_health']}")
                            self._finish_battle_gameend(
                                session, True, winners=[pl_t], losers=[ai_t])
                            self._push_transaction_ack(session)
                            return True
                        if bstate.get("pending_deck_search"):
                            # Pause for the deck-search answer; the
                            # follow-up advances the phase.
                            self._push_transaction_ack(session)
                            _hc.log_req("    Pass at AssignDamage: paused for deck-search answer")
                            return True
                    _be.advance_phase(bstate)
                    _be.save_state(session, bstate)
                    self._advance_to_priority(session, pl_t, ai_t, bstate)
        return True
