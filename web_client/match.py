"""One browser seat playing the server AI in a native PvP session.

A ``WebMatch`` builds the same disposable tournament PvP session the headless
runner (``pvp_autoplay``) uses: two virtual ``HCPHandler`` seats in
``player_handlers``, authored decks seeded into ``game_cards``, and the normal
ready/setup handshake. Every move then enters through
``HCPHandler.handle_service_request`` as a 3029 PlayerTransaction, so the
RulesPort validates and resolves it exactly as it would for the Unity client.

The browser seat never decides from server internals: what it may do comes
from the events the server publishes for it (``PlayerOptionList``,
``TurnPhaseUpdated``, ``GreenLight``), captured by ``PacketCapture``. The only
checkpoint facts read here are the ones the Unity client receives as prompts
of their own (pending target pickers and the setup choices), plus whose turn it
is for driving the AI seat.
"""

from __future__ import annotations

import itertools
import queue
import random
import sys
import threading
import time
import traceback

import game_engine
import game_session as gs
import pvp_autoplay as autoplay
import db as dbmod
from domain.constants import PLAY_CARD_ABILITY_TEMPLATE_ID, PLAYER_UID_TYPE
from services.tournament_game import (
    handle_ready_for_game_events, handle_ready_for_game_setup,
    player_handler_lock, player_handlers, pvp_default_state, pvp_load_state,
    pvp_save_state, pvp_shared_port,
)

from .events_json import PacketCapture

Phases = game_engine.ETurnPhases

PENDING_KEYS = ("pending_trigger", "pending_choice", "pending_deck_search",
                "pending_discard_ability", "pending_conversation")
DAMAGE_PHASES = (Phases.AssignFirstStrikeDamage, Phases.AssignDamage)
MAIN_PHASES = (Phases.FirstMainPhase, Phases.SecondMainPhase)
COMBAT_RESPONSE_PHASES = (
    Phases.DeclareCombatPriorityWindow, Phases.DeclareAttackPriorityWindow,
    Phases.DeclareDefensePriorityWindow, Phases.FirstStrikePriorityWindow,
    Phases.EndPhase)
# Runner slots above the simulator's parallel range keep a browser match's
# player ids, session id and game_cards row ids disjoint from any FRA run.
WEB_RUN_SLOT_BASE = 50000
AI_STEP_GUARD = 400

# The engine shares one SQLite connection; matches take turns on it.
ENGINE_LOCK = threading.RLock()
_slots = itertools.count(1)


def deck_choices():
    """Authored FRA decks with a champion whose cards the server can seed."""
    rows = dbmod.conn().execute(
        "SELECT deck_guid, deck_name, champion_guid, "
        "COALESCE(NULLIF(TRIM(ai_deck_personality), ''), 'Default'), "
        "COALESCE(is_elite, 0) FROM fra_encounters "
        "ORDER BY deck_name, deck_guid").fetchall()
    # Decks listing a card the snapshot has no template for cannot be built.
    import campaign_source
    campaign_source.ensure_campaign_path()
    import campaign_content
    known = {str(row[0]).lower() for row in dbmod.conn().execute("SELECT guid FROM card_templates")}
    known |= {str(row[0]).lower() for row in dbmod.conn().execute(
        "SELECT DISTINCT champion_guid FROM champion_abilities")}
    unseedable = {str(row["deck_guid"]).lower()
                  for row in campaign_content.table("encounter_deck_cards")
                  if str(row["card_guid"]).lower() not in known}
    out = []
    for deck_guid, name, champion_guid, personality, elite in rows:
        if not champion_guid or str(deck_guid).lower() in unseedable:
            continue
        out.append({"deck_guid": str(deck_guid), "name": str(name),
                    "champion_guid": str(champion_guid),
                    "personality": str(personality),
                    "elite": bool(elite)})
    return out


class ActionError(ValueError):
    """A browser action that does not fit the current prompt."""


def _opponent_spec(deck_guid):
    """The match spec of an authored FRA deck. The loader wants two decks, so the deck is listed twice and the first
    spec is kept."""
    return autoplay._fra_encounter_specs([deck_guid, deck_guid])[0]


class MatchSessionLost(RuntimeError):
    """The battle session behind a match is no longer in the database."""


class WebMatch:
    def __init__(self, player_deck_guid, opponent_deck_guid, *, seed=None,
                 player_name="You", ai_delay=0.35, threaded=True, player_spec=None):
        # ``threaded=False`` runs the match inline (the Pyodide demo has no
        # threads): setup happens here and each ``submit`` is processed at once.
        self.threaded = threaded
        self.slot = WEB_RUN_SLOT_BASE + next(_slots)
        self.seed = int(seed if seed is not None
                        else random.randrange(1, 1 << 30))
        self.session_id, pids = autoplay._autoplay_identity(
            self.slot, self.slot)
        self.human_pid, self.ai_pid = int(pids[0]), int(pids[1])
        self.player_name = player_name
        self.ai_delay = float(ai_delay)
        # A saved deck arrives as a prebuilt spec (profile.play_spec); an authored deck is loaded by GUID.
        if player_spec is None:
            self.specs = autoplay._fra_encounter_specs(
                [player_deck_guid, opponent_deck_guid])
        else:
            self.specs = [player_spec, _opponent_spec(opponent_deck_guid)]
        self.capture = PacketCapture(self.human_pid).start()
        self.log: list[dict] = []
        # Each browser action with the packet count when it was sent, so a capture shows what was done when.
        self.actions: list[dict] = []
        self.cond = threading.Condition()
        self.error = None
        self.ended = False
        # Set when the session behind the match cannot be found; the match then refuses further actions.
        self.stopped = False
        self.busy = True
        self._damage_done = None
        self._declared = set()
        self._ai_tried = set()
        self._actions: queue.Queue = queue.Queue()
        # Set once the seats are up (or setup failed), so a caller never reads the session before _setup has made it.
        self._set_up = threading.Event()
        if threaded:
            self._worker = threading.Thread(
                target=self._run, name=f"web-match-{self.slot}", daemon=True)
            self._worker.start()
            self._set_up.wait(timeout=120)
        else:
            self._start()

    # ------------------------------------------------------------------
    # Session plumbing
    # ------------------------------------------------------------------

    def _session(self):
        # Ended sessions are returned too: the terminal check reads them. Only a missing row is a failure.
        session = gs.find_session_by_player(game_engine.UID.make(
            PLAYER_UID_TYPE, self.human_pid).to_uint64())
        if session is None:
            raise MatchSessionLost(
                f"no session contains player {self.human_pid}")
        return session

    def _state(self):
        return pvp_load_state(self._session()) or {}

    def _handler(self, pid):
        return player_handlers[int(pid)]

    def _tx(self, pid, raw, typed_payload=None):
        with ENGINE_LOCK:
            autoplay._transaction(self._handler(pid), self._session(), raw,
                                  typed_payload=typed_payload)
        self._publish()

    def _setup(self):
        pids = [self.human_pid, self.ai_pid]
        rnd = random.Random(self.seed)
        autoplay._cleanup(self.session_id, pids)
        self.session = gs.GameSession(
            self.session_id,
            self.session_id * autoplay.AUTOPLAY_SERVER_ID_MULTIPLIER,
            f"tourney-{self.session_id}", pids[0])
        for pid in pids:
            self.session.add_player(
                autoplay.encoder.make_uid(PLAYER_UID_TYPE, pid), 0)
        row_base = self.slot * autoplay.AUTOPLAY_ROW_ID_RUN_STRIDE
        for seat, (pid, spec) in enumerate(zip(pids, self.specs)):
            deck = list(spec["cards"])
            rnd.shuffle(deck)
            seat_base = row_base + seat * autoplay.AUTOPLAY_ROW_ID_SEAT_STRIDE
            autoplay._seed_deck(
                self.session_id, pid, deck,
                uid_offset=seat * autoplay.AUTOPLAY_PLAYER_UID_STRIDE,
                row_id_base=seat_base)
            autoplay._seed_champion(
                self.session_id, pid, spec["champion_guid"],
                uid_offset=(seat * autoplay.AUTOPLAY_PLAYER_UID_STRIDE +
                            autoplay.AUTOPLAY_CHAMPION_UID_OFFSET),
                row_id_base=(seat_base +
                             autoplay.AUTOPLAY_ROW_ID_CHAMPION_OFFSET))
        dbmod.conn().commit()
        human = autoplay._make_handler(self.human_pid, self.session)
        human.user_profile["name"] = self.player_name
        ai = autoplay._make_handler(
            self.ai_pid, self.session,
            deck_personality=self.specs[1].get("ai_deck_personality"))
        with player_handler_lock:
            player_handlers[self.human_pid] = human
            player_handlers[self.ai_pid] = ai
        ready, events_ready = {}, {}
        handle_ready_for_game_setup(human, self.session, ready,
                                    player_handlers)
        handle_ready_for_game_setup(ai, self.session, ready, player_handlers)
        if not pvp_load_state(self._session()):
            state = pvp_default_state(self.human_pid, self.human_pid)
            state["goes_first_pid"] = self.human_pid
            pvp_save_state(self.session, state)
        handle_ready_for_game_events(human, self.session, events_ready,
                                     autoplay.log_req)
        handle_ready_for_game_events(ai, self.session, events_ready,
                                     autoplay.log_req)

    def close(self):
        self._actions.put(None)
        self.capture.stop()
        with ENGINE_LOCK:
            autoplay._cleanup(self.session_id, [self.human_pid, self.ai_pid])
            dbmod.conn().commit()

    # ------------------------------------------------------------------
    # Event log for the browser
    # ------------------------------------------------------------------

    def _publish(self, extra=None):
        packets = self.capture.drain()
        with self.cond:
            self.log.extend(
                {"seq": len(self.log) + 1, "events": events}
                for events in packets
                if events
            )
            if extra is not None:
                self.log.append({"seq": len(self.log) + 1, **extra})
            self.cond.notify_all()

    def export_capture(self) -> dict:
        """What is needed to replay this match: the seed, the seats, every packet the browser was sent, and every
        action with the packet count it was sent after."""
        with self.cond:
            packets = list(self.log)
            actions = list(self.actions)
        return {"version": 1, "seed": self.seed, "match": self.info(), "packets": packets, "actions": actions}

    def snapshot(self, after=0, wait=0.0):
        """Packets after ``after`` plus the current prompt."""
        deadline = time.monotonic() + max(0.0, wait)
        with self.cond:
            while (len(self.log) <= after and self.busy and
                   time.monotonic() < deadline):
                self.cond.wait(max(0.0, deadline - time.monotonic()))
            packets = self.log[after:]
            busy = self.busy
        return {
            "match": self.info(),
            "packets": packets,
            "busy": busy,
            "prompt": None if busy or self.stopped else self.prompt(),
            "error": self.error,
        }

    def info(self):
        return {
            "player_id": self.human_pid,
            "opponent_id": self.ai_pid,
            "player_uid": int(game_engine.UID.make(
                PLAYER_UID_TYPE, self.human_pid).uid64),
            "opponent_uid": int(game_engine.UID.make(
                PLAYER_UID_TYPE, self.ai_pid).uid64),
            "decks": [{"name": spec["name"], "deck_guid": spec["deck_guid"],
                       "champion_guid": spec["champion_guid"]}
                      for spec in self.specs],
            "ended": self.ended,
        }

    # ------------------------------------------------------------------
    # Who acts next
    # ------------------------------------------------------------------

    def _priority_pid(self, state):
        priority = state.get("priority_pid")
        port = pvp_shared_port(self._session())
        native = getattr(getattr(port, "action_stack", None),
                         "priority_player_id", None)
        if native is not None:
            try:
                value = int(getattr(native, "uid64", native))
                priority = value >> 8 if (value & 0xFF) == 244 else value
            except (TypeError, ValueError):
                pass
        try:
            return int(priority) if priority is not None else None
        except (TypeError, ValueError):
            return None

    def _chain(self):
        """Instance ids on the native chain (the checkpoint ``stack`` can
        retain resolved ignores-chain triggers)."""
        port = pvp_shared_port(self._session())
        return tuple(getattr(getattr(port, "chain", None), "_instance_ids",
                             ()) or ())

    def _decision(self, state):
        """Return ``(pid, kind, details)`` for the next required decision."""
        if self._terminal(state):
            return None, "ended", {}
        phase = state.get("phase")
        turn_pid = int(state.get("turn_pid") or self.human_pid)
        other_pid = self.ai_pid if turn_pid == self.human_pid else self.human_pid
        if phase == Phases.PickGoesFirst:
            return int(state.get("goes_first_pid", turn_pid)), "goes_first", {}
        if phase == Phases.Mulligan:
            ask = state.get("mulligan_pid")
            if ask is not None:
                return int(ask), "mulligan", {}
        pending_key = next((key for key in PENDING_KEYS if state.get(key)),
                           None)
        if pending_key is not None:
            pending = state[pending_key]
            chooser = int(pending.get("owner_id", turn_pid) or turn_pid)
            return chooser, "choose", {"key": pending_key,
                                       "pending": pending}
        if phase in DAMAGE_PHASES:
            key = (state.get("turn_number"), int(phase))
            if self._damage_done != key:
                return turn_pid, "assign_damage", {"key": key}
        # A declaration leaves the phase in place and opens its priority
        # window; after that the step is an ordinary pass.
        declared = (state.get("turn_number"), phase) in self._declared
        if phase == Phases.DeclareAttack and not declared:
            return turn_pid, "attack", {}
        if (phase == Phases.DeclareDefense and state.get("attackers") and
                not declared):
            return other_pid, "defend", {
                "attackers": [int(uid) for uid in state["attackers"]]}
        priority = self._priority_pid(state)
        if priority is None:
            return None, "stalled", {}
        return priority, "priority", {}

    def _terminal(self, state):
        session = self._session()
        if str(getattr(session, "state", "") or "").lower() == "ended":
            return True
        return autoplay._autoplay_terminal_result(
            session, state, [self.human_pid, self.ai_pid]) is not None

    def prompt(self):
        with ENGINE_LOCK:
            state = self._state()
            pid, kind, details = self._decision(state)
        if kind == "ended":
            result = autoplay._autoplay_terminal_result(
                self._session(), state, [self.human_pid, self.ai_pid]) or {}
            return {"kind": "ended",
                    "winner": result.get("winner_id"),
                    "you_won": result.get("winner_id") == self.human_pid}
        if pid != self.human_pid:
            return {"kind": "waiting", "on": kind}
        out = {"kind": kind, "phase": state.get("phase"),
               "turn_number": state.get("turn_number"),
               "your_turn": int(state.get("turn_pid") or 0) == self.human_pid}
        if kind == "choose":
            pending = details["pending"]
            candidates = [int(uid) for uid in self._candidates(
                details["key"], pending)]
            out.update({
                "pending": details["key"],
                "pick_kind": self._pick_kind(candidates),
                "source": pending.get("source_uid"),
                "candidates": candidates,
                "min_picks": self._pick_bounds(details["key"], pending)[0],
                "max_picks": self._pick_bounds(details["key"], pending)[1],
            })
            # A pick among revealed cards shows every revealed card, the ones that cannot be chosen too (the client's
            # Coverflow for a SourceRevealed target).
            revealed = [int(uid) for uid in (pending.get("revealed_cards") or ())]
            if details["key"] == "pending_deck_search" and revealed and set(candidates) <= set(revealed):
                out["revealed"] = revealed
        elif kind == "defend":
            out["attackers"] = details["attackers"]
        return out

    def _pick_kind(self, candidates):
        """``"threshold"`` when every candidate is a card that provides a threshold (a Standard resource, so the pick
        gains its threshold), otherwise ``"card"``. The candidates' own templates decide, not the ability's name."""
        from rules_port.resources import card_template_threshold_flags
        if not candidates:
            return "card"
        with ENGINE_LOCK:
            if all(card_template_threshold_flags(dbmod.conn(), self.session_id, uid)
                   for uid in candidates):
                return "threshold"
        return "card"

    def _candidates(self, key, pending):
        if key == "pending_choice":
            return list(pending.get("choice_uids") or ())
        if key == "pending_trigger":
            return self._trigger_candidates(pending)
        return list(pending.get("candidates") or ())

    def _trigger_candidates(self, pending):
        """Legal targets for a paused trigger.

        The Unity client enumerates these itself from the ability's target
        template (class 39 names only the ability), so use the RulesPort
        enumerator the server validates the reply with.
        """
        from gamedata import DEFAULT_RECORD_STORE, ability_graph
        from rules_port.targeting import legal_targets_for
        target = pending.get("target_template_id")
        if not target:
            graph = ability_graph(
                DEFAULT_RECORD_STORE,
                str(pending.get("ability_guid") or "").lower())
            inputs = [t for t in getattr(graph, "targets", ()) or ()
                      if t.requires_input and not t.is_auto
                      and t.target_kind != "PlayerTargetTemplate"]
            if not inputs:
                return []
            index = int(pending.get("target_index", 0) or 0)
            target = inputs[min(index, len(inputs) - 1)]
        state = self._state()
        champ_map = state.get("champ_map") or {}
        champions = [(int(champ_map[str(pid)]), pid, "Champ",
                      int(state.get(f"hp_{pid}", 20) or 0))
                     for pid in (self.human_pid, self.ai_pid)
                     if champ_map.get(str(pid))]
        owner = int(pending.get("owner_id") or state.get("turn_pid") or 0)
        try:
            found = legal_targets_for(
                dbmod.conn(), self.session_id, owner, target,
                int(pending.get("source_uid") or 0), both_players=True,
                champions=champions, battle_state=state)
        except Exception as exc:
            autoplay.log_req(f"    [web-client] trigger targets: {exc}")
            return []
        return [int(getattr(uid, "uid64", uid)) for uid in found or ()]

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def submit(self, action):
        with self.cond:
            if self.stopped:
                raise ActionError("the match has stopped")
            if self.busy:
                raise ActionError("the server is still resolving")
            self.busy = True
            self.actions.append({"after": len(self.log), "action": dict(action)})
        if self.threaded:
            self._actions.put(action)
        else:
            self._process(action)

    def _start(self):
        """Set the seats up and run the opening steps up to the first prompt."""
        try:
            with ENGINE_LOCK:
                self._setup()
        except Exception:
            self._fail()
            self._set_up.set()
            return
        self._set_up.set()
        try:
            self._publish()
            self._advance()
        except Exception:
            self._fail()

    def _process(self, action):
        """Apply one browser action and run the AI up to the next prompt."""
        try:
            self.error = None
            self._apply(action)
            self._advance()
        except ActionError as exc:
            self.error = str(exc)
        except Exception:
            self._fail()
        finally:
            with self.cond:
                self.busy = False
                self.cond.notify_all()

    def _run(self):
        self._start()
        while True:
            action = self._actions.get()
            if action is None:
                return
            self._process(action)

    def _fail(self):
        exc = sys.exc_info()[1]
        if isinstance(exc, MatchSessionLost):
            self.stopped = True
            self.error = f"match stopped: {exc}"
        else:
            self.error = traceback.format_exc(limit=6)
        autoplay.log_req(f"    [web-client] match {self.slot} error:\n"
                         f"{self.error}", "ERROR")
        with self.cond:
            self.busy = False
            self.cond.notify_all()

    def _advance(self):
        """Run automatic steps and the AI seat until the browser must act."""
        for _ in range(AI_STEP_GUARD):
            with ENGINE_LOCK:
                state = self._state()
                pid, kind, details = self._decision(state)
            if kind == "ended":
                self.ended = True
                break
            if kind == "stalled":
                raise RuntimeError(
                    f"no player holds priority (phase={state.get('phase')})")
            if kind == "assign_damage":
                # The Unity client sends this automatically on entering a
                # damage step, for either seat.
                self._damage_done = details["key"]
                self._tx(pid, b"AssignDamageOrderTransaction;",
                         typed_payload={"assignments": []})
                continue
            if pid == self.human_pid:
                break
            if self.ai_delay:
                time.sleep(self.ai_delay)
            self._ai_step(state, kind, details)
        else:
            raise RuntimeError("the AI seat did not hand control back")
        with self.cond:
            self.busy = False
            self.cond.notify_all()

    # ------------------------------------------------------------------
    # AI seat
    # ------------------------------------------------------------------

    def _ai_step(self, state, kind, details):
        ai, human = self.ai_pid, self.human_pid
        handler = self._handler(ai)
        session = self._session()
        if kind == "goes_first":
            return self._tx(ai, b"ChoosePlayTransaction;")
        if kind == "mulligan":
            return self._tx(ai, b"AcceptStartingHand;")
        if kind == "choose":
            pending = details["pending"]
            candidates = self._candidates(details["key"], pending)
            return self._choose(ai, details["key"], pending,
                                int(candidates[0]) if candidates else None)
        if kind == "attack":
            with ENGINE_LOCK:
                attackers, _reason = autoplay._ai_attack_decision(
                    handler, session, state, ai, human)
            return self._commit_attack(ai, attackers, state)
        if kind == "defend":
            with ENGINE_LOCK:
                blocks = autoplay._ai_block_decision(
                    handler, session, state, ai, human, details["attackers"])
            return self._commit_defense(ai, details["attackers"], blocks,
                                        state)
        # Priority: one action per step, then re-read the window. A window is
        # identified by its phase/turn/chain so a rejected action is not
        # retried forever.
        chain = self._chain()
        window = (state.get("turn_number"), state.get("phase"), chain)
        if (int(state.get("turn_pid") or 0) == ai and
                state.get("phase") in MAIN_PHASES and
                not chain and window not in self._ai_tried):
            if self._ai_main_action(state, handler, session):
                return
            self._ai_tried.add(window)
        elif (state.get("phase") in COMBAT_RESPONSE_PHASES and
              window not in self._ai_tried):
            self._ai_tried.add(window)
            with ENGINE_LOCK:
                played = autoplay._play_ai_combat_trick_response(
                    handler, session, state, ai, human)
            self._publish()
            if played:
                return
        self._tx(ai, b"PassPriorityTransaction;")

    def _ai_main_action(self, state, handler, session):
        ai, human = self.ai_pid, self.human_pid
        before = repr(self._state())
        with ENGINE_LOCK:
            resources = autoplay.db_hand_resources_with_template(
                self.session_id, ai)
        if resources and not state.get(f"res_played_{ai}", 0):
            self._tx(ai, autoplay._card_play_bytes(resources[0][1],
                                                   "Resource"))
            if repr(self._state()) != before:
                return True
        with ENGINE_LOCK:
            decision = autoplay._ai_main_decision(
                handler, session, self._state(), ai, human,
                pre_combat=state.get("phase") == Phases.FirstMainPhase)
        kind = decision["kind"]
        if kind == "card":
            chosen = decision["card"]
            target_map = decision.get("target_map") or {}
            targets = decision.get("target_uids") or (
                [decision["target_uid"]]
                if decision.get("target_uid") is not None else [])
            if not target_map and targets:
                target_map = {0: targets}
            self._tx(ai, autoplay._card_play_bytes(chosen.card_uid,
                                                   chosen.card_type),
                     typed_payload={"ability_data": [{
                         "target_map": {int(i): [int(u) for u in v]
                                        for i, v in target_map.items()},
                         "x_cost": int(decision.get("x_cost", 0) or 0)}]})
        elif kind == "champion_power":
            power = decision["power"]
            row = autoplay.db_game_champion(self.session_id, ai, conn=dbmod.conn())
            source = int(row[0]) if row else 0
            self._activate(ai, source, power["ability_guid"],
                           [power["target_uid"]]
                           if power.get("target_uid") is not None else [],
                           cost_targets=power.get("sacrifice_uids") or (),
                           champion=True)
        elif kind in ("warzone_ability", "resource_sink", "tunnel"):
            ability = decision["ability"]
            target = ability.get("target_uid")
            self._activate(ai, int(ability["source_uid"]),
                           ability["ability_guid"],
                           [target] if target is not None else [],
                           x_cost=int(ability.get("x_cost", 0) or 0))
        else:
            return False
        return repr(self._state()) != before

    # ------------------------------------------------------------------
    # Transactions shared by both seats
    # ------------------------------------------------------------------

    @staticmethod
    def _pick_bounds(key, pending):
        """How many cards the choice takes: the authored minimum and maximum of its target (a trigger's explicit target
        records them; "Two target opposing troops" is 2 and 2). Any other choice takes one card."""
        if key == "pending_trigger":
            entries = pending.get("target_entries") or []
            if entries:
                return int(entries[0].get("minimum", 1) or 0), max(1, int(entries[0].get("maximum", 1) or 1))
        return 1, 1

    def _choose(self, pid, key, pending, chosen_uid):
        # chosen_uid is one card, a list of cards (a multi-target pick), or None for no target.
        if isinstance(chosen_uid, (list, tuple)):
            chosen = [int(uid) for uid in chosen_uid]
        else:
            chosen = [int(chosen_uid)] if chosen_uid is not None else []
        if key == "pending_choice":
            index = int((pending.get("continuation") or {}).get(
                "target_index", 0) or 0)
        elif key == "pending_trigger":
            index = int(pending.get("target_index", 0) or 0)
        else:
            continuation = pending.get("continuation") or {}
            index = int(continuation.get(
                "target_index", pending.get("target_index", 0)) or 0)
        # With no legal target the reply carries an empty TargetMap and the
        # ability resolves without one, as when Unity's picker has no cards.
        raw = b"SetAbilityActivationDataTransaction;"
        targets = {}
        if chosen:
            for uid in chosen:
                raw += autoplay._mk_uid_bytes(uid)
            targets = {str(index): list(chosen)}
        self._tx(pid, raw, typed_payload={
            "AbilityInstanceId": int(pending.get("instance_id", 1) or 1),
            "AbilityActivationData": {"TargetMap": targets}})

    def _opponent_champion(self, pid, state):
        other = self.ai_pid if int(pid) == self.human_pid else self.human_pid
        return int((state.get("champ_map") or {}).get(str(other), 0))

    def _mark_declared(self, state, before, force=False):
        if force or repr(self._state()) != before:
            self._declared.add((state.get("turn_number"), state.get("phase")))

    def _commit_attack(self, pid, attackers, state):
        champion = self._opponent_champion(pid, state)
        attackers = [int(uid) for uid in attackers]
        before = repr(self._state())
        self._tx(pid, autoplay._attack_bytes(attackers, champion),
                 typed_payload={"m_Attacks": [{
                     "DefendingCardId": champion,
                     "AttackingCardIds": attackers}]})
        self._mark_declared(state, before)

    def _commit_defense(self, pid, attackers, blocks, state):
        champion = int((state.get("champ_map") or {}).get(str(pid), 0))
        blocks = {int(attacker): [int(uid) for uid in blockers]
                  for attacker, blockers in (blocks or {}).items()
                  if blockers}
        before = repr(self._state())
        self._tx(pid, autoplay._defense_bytes(attackers, blocks, champion),
                 typed_payload={"m_DefenseDeclarations": [
                     {"AttackerId": attacker, "DefendingCardIds": blockers}
                     for attacker, blockers in blocks.items()]})
        # "No blocks" is always a legal declaration, even when it leaves the
        # checkpoint unchanged.
        self._mark_declared(state, before, force=not blocks)

    def _activate(self, pid, source, ability_guid, targets, *,
                  target_map=None, cost_targets=(), cost_target_map=None,
                  x_cost=0, champion=False):
        target_map = target_map or ({0: [int(u) for u in targets]}
                                    if targets else {})
        cost_target_map = cost_target_map or {
            index: [int(uid)] for index, uid in enumerate(cost_targets)}
        first = next(iter(target_map.values()), [None])
        if champion:
            raw = autoplay._champion_power_bytes(source, {
                "ability_guid": ability_guid,
                "target_uid": first[0] if first else None,
                "sacrifice_uids": [u for v in cost_target_map.values()
                                   for u in v]})
        else:
            raw = autoplay._manual_ability_bytes(
                source, ability_guid, first[0] if first else None)
        self._tx(pid, raw, typed_payload={
            "source_card_id": int(source),
            "ability_template_id": str(ability_guid).lower(),
            "activation_data": {
                "target_map": {int(i): [int(u) for u in v]
                               for i, v in target_map.items()},
                "cost_target_map": {int(i): [int(u) for u in v]
                                    for i, v in cost_target_map.items()},
                "x_cost": int(x_cost or 0)},
            "ability_instance_id": 0})

    # ------------------------------------------------------------------
    # Browser actions
    # ------------------------------------------------------------------

    def _apply(self, action):
        kind = str(action.get("type") or "")
        with ENGINE_LOCK:
            state = self._state()
            pid, expected, details = self._decision(state)
        if pid != self.human_pid:
            raise ActionError("it is not your decision")
        before = repr(state)
        human = self.human_pid
        if kind in ("play_first", "draw_first"):
            if expected != "goes_first":
                raise ActionError("not choosing who goes first")
            self._tx(human, b"ChoosePlayTransaction;" if kind == "play_first"
                     else b"ChooseDrawTransaction;")
        elif kind in ("keep", "mulligan"):
            if expected != "mulligan":
                raise ActionError("not in the mulligan")
            self._tx(human, b"AcceptStartingHand;" if kind == "keep"
                     else b"MulliganTransaction;")
        elif kind == "choose":
            if expected != "choose":
                raise ActionError("nothing to choose")
            pending = details["pending"]
            legal = {int(u) for u in self._candidates(details["key"],
                                                       pending)}
            low, high = self._pick_bounds(details["key"], pending)
            if action.get("cards") is not None:
                cards = [int(uid) for uid in action.get("cards") or []]
                if len(set(cards)) != len(cards) or any(uid not in legal for uid in cards):
                    raise ActionError("that card is not a legal choice")
                if not low <= len(cards) <= high:
                    raise ActionError(f"choose {low} to {high} cards")
                chosen = cards or None
            else:
                chosen = action.get("card")
                chosen = int(chosen) if chosen is not None else None
                if (chosen is None and legal) or (
                        chosen is not None and chosen not in legal):
                    raise ActionError("that card is not a legal choice")
            self._choose(human, details["key"], pending, chosen)
        elif kind == "attack":
            if expected != "attack":
                raise ActionError("not declaring attackers")
            self._commit_attack(human, action.get("attackers") or [], state)
        elif kind == "defend":
            if expected != "defend":
                raise ActionError("not declaring blockers")
            self._commit_defense(human, details["attackers"],
                                 action.get("blocks") or {}, state)
        elif kind == "pass":
            if expected != "priority":
                raise ActionError("you cannot pass here")
            self._tx(human, b"PassPriorityTransaction;")
        elif kind == "play":
            if expected != "priority":
                raise ActionError("you do not have priority")
            card = int(action["card"])
            with ENGINE_LOCK:
                row = dbmod.conn().execute(
                    "SELECT card_type FROM game_cards WHERE session_id=? "
                    "AND card_uid=? AND user_id=?",
                    (self.session_id, card, human)).fetchone()
            if row is None:
                raise ActionError("that card is not yours")
            self._tx(human, autoplay._card_play_bytes(card, row[0]),
                     typed_payload={"ability_data": [{
                         "target_map": _index_map(action.get("targets")),
                         "x_cost": int(action.get("x", 0) or 0)}]})
        elif kind == "activate":
            if expected != "priority":
                raise ActionError("you do not have priority")
            source = int(action["card"])
            ability = str(action["ability"]).lower()
            if ability == PLAY_CARD_ABILITY_TEMPLATE_ID:
                raise ActionError("use play for the card's own cast")
            champion = source == int(
                (state.get("champ_map") or {}).get(str(human), 0))
            self._activate(human, source, ability, [],
                           target_map=_index_map(action.get("targets")),
                           cost_target_map=_index_map(action.get("costs")),
                           x_cost=int(action.get("x", 0) or 0),
                           champion=champion)
        else:
            raise ActionError(f"unknown action {kind!r}")
        if repr(self._state()) == before and kind != "pass":
            self._publish({"notice": "The server rejected that action."})


def _index_map(value):
    return {int(index): [int(uid) for uid in uids]
            for index, uids in (value or {}).items() if uids}
