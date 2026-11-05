"""Native RulesPort turn-boundary state transitions and the battle checkpoint.

This is the single home of the persisted battle-state (``bstate``) helpers:
phase lists and cursor, phase stops, the chain facade, checkpoint load/save
and the turn-boundary card updates.  (It absorbed the former
``battle_engine`` module.)
"""

from __future__ import annotations

import json

import game_engine
# Chain facade and checkpoint persistence, re-exported so callers only need
# this module for the whole battle checkpoint.
from .chain import (  # noqa: F401
    push as stack_push, pop as stack_pop, top as stack_top,
    empty as stack_empty, clear as stack_clear, set_pass as stack_set_pass,
    both_passed as stack_both_passed, reset_passes as stack_reset_passes)
from . import persistence
from .persistence import persistence_state, save_state  # noqa: F401 (re-export)


PLAYER = "player"
AI = "ai"
BASE_TURN_PHASES = [
    game_engine.ETurnPhases.StartTurn,
    game_engine.ETurnPhases.Ready,
    game_engine.ETurnPhases.Prep,
    game_engine.ETurnPhases.Draw,
    game_engine.ETurnPhases.FirstMainPhase,
    game_engine.ETurnPhases.SecondMainPhase,
    game_engine.ETurnPhases.EndPhase,
    game_engine.ETurnPhases.Discard,
    game_engine.ETurnPhases.EndTurn,
]
TURN_PHASES = BASE_TURN_PHASES
COMBAT_STEPS = [
    game_engine.ETurnPhases.DeclareCombatPriorityWindow,
    game_engine.ETurnPhases.DeclareAttack,
    game_engine.ETurnPhases.DeclareAttackPriorityWindow,
    game_engine.ETurnPhases.DeclareDefense,
    game_engine.ETurnPhases.DeclareDefensePriorityWindow,
    game_engine.ETurnPhases.AssignFirstStrikeDamage,
    game_engine.ETurnPhases.FirstStrikePriorityWindow,
    game_engine.ETurnPhases.AssignDamage,
]

# Shared client stop policy used by both the ordinary RulesPort session and
# the tournament projection.  The tournament state has its own ``phase`` /
# player-id schema, so it consumes these constants without using this module's
# load/save helpers.
COMBAT_TURN_PHASES = [
    game_engine.ETurnPhases.StartTurn,
    game_engine.ETurnPhases.Ready,
    game_engine.ETurnPhases.Prep,
    game_engine.ETurnPhases.Draw,
    game_engine.ETurnPhases.FirstMainPhase,
] + COMBAT_STEPS + [
    game_engine.ETurnPhases.SecondMainPhase,
    game_engine.ETurnPhases.EndPhase,
    game_engine.ETurnPhases.Discard,
    game_engine.ETurnPhases.EndTurn,
]

SELF_ALWAYS_STOPS = {
    game_engine.ETurnPhases.PickGoesFirst,
    game_engine.ETurnPhases.Mulligan,
    game_engine.ETurnPhases.StartGame,
    game_engine.ETurnPhases.DeclareAttack,
    game_engine.ETurnPhases.AssignDamage,
    game_engine.ETurnPhases.AssignFirstStrikeDamage,
}
OPP_ALWAYS_STOPS = {game_engine.ETurnPhases.DeclareDefense}
SELF_DEFAULT_STOPS = {
    game_engine.ETurnPhases.FirstMainPhase,
    game_engine.ETurnPhases.SecondMainPhase,
    game_engine.ETurnPhases.DeclareAttackPriorityWindow,
    game_engine.ETurnPhases.DeclareDefensePriorityWindow,
}
OPP_DEFAULT_STOPS = {
    game_engine.ETurnPhases.SecondMainPhase,
    game_engine.ETurnPhases.DeclareAttackPriorityWindow,
    game_engine.ETurnPhases.DeclareDefensePriorityWindow,
}

def default_state(turn_player=PLAYER):
    """Create the shared checkpoint used when a native game starts."""
    return {
        "turn_player": turn_player,
        "turn_number": 1,
        "phase_idx": 0,
        "player_passed": False,
        "ai_passed": False,
        "player_resources": 0,
        "player_total_resources": 0,
        "player_threshold": {},
        "ai_resources": 0,
        "ai_total_resources": 0,
        "ai_threshold": {},
        "player_resource_played_this_turn": False,
        "ai_resource_played_this_turn": False,
        "player_resource_plays_this_turn": 0,
        "ai_resource_plays_this_turn": 0,
        "player_charges": 0,
        "ai_charges": 0,
        "player_spell_points": 0,
        "ai_spell_points": 0,
        "player_sp_uses": {},
        "player_self_stops": None,
        "player_opp_stops": None,
        "player_health": 20,
        "ai_health": 20,
        "player_attackers": {},
        "player_has_ready_troop": False,
        "turn_phases": list(BASE_TURN_PHASES),
        "resolve_counter": 0,
    }


def build_turn_phases(state):
    """Build the persisted phase list from native combat facts."""
    phases = (BASE_TURN_PHASES if not state.get("player_has_ready_troop")
              else BASE_TURN_PHASES[:5] + COMBAT_STEPS + BASE_TURN_PHASES[5:])
    entries = (state.get("extra_combats_this_turn") or {}).get(PLAYER, [])
    if not entries:
        return phases
    try:
        second_main = phases.index(game_engine.ETurnPhases.SecondMainPhase)
    except ValueError:
        return phases
    return (phases[:second_main + 1] +
            sum((COMBAT_STEPS + [game_engine.ETurnPhases.SecondMainPhase]
                 for _entry in entries), []) + phases[second_main + 1:])


def next_turn_player(state):
    current = state.get("turn_player")
    if state.get("bonus_turn") == current:
        state.pop("bonus_turn", None)
        return current
    return AI if current == PLAYER else PLAYER


def complete_turn(state):
    """Apply the native end-turn checkpoint transition and return its owner.

    End-of-turn effects and card/event projection stay with the mode adapter;
    this function owns only the shared scheduler state consumed by both PvE
    and PvP hosts.
    """
    if not isinstance(state, dict):
        return None
    from .damage_effects import expire_champion_shields
    expire_champion_shields(state)
    next_player = next_turn_player(state)
    state["turn_player"] = next_player
    state["turn_number"] = int(state.get("turn_number", 1) or 1) + 1
    state["phase_idx"] = 0
    state["turn_phases"] = list(BASE_TURN_PHASES)
    state["player_passed"] = False
    state["ai_passed"] = False
    state.pop("ai_turn_phase_idx", None)
    state.pop("ai_attackers", None)
    state.pop("ai_blockers", None)
    state.pop("player_attackers", None)
    state.pop("blocked_attackers", None)
    state.pop("player_damage_order", None)
    state[f"{next_player}_resource_played_this_turn"] = False
    state[f"{next_player}_resource_plays_this_turn"] = 0
    return next_player


def turn_phases(state):
    phases = state.get("turn_phases")
    if not phases:
        phases = build_turn_phases(state)
        state["turn_phases"] = phases
    return phases


def skip_to_phase(state, phase):
    phases = turn_phases(state)
    try:
        target = phases.index(phase, int(state.get("phase_idx", 0) or 0))
    except (TypeError, ValueError):
        return False
    state["phase_idx"] = target
    return True


def _configured_stops(configured, defaults):
    """A stop list the player saved replaces the defaults, even when empty
    (the client's "no optional stops"); ``None`` means never configured."""
    return defaults if configured is None else set(configured)


def is_self_stop(state, phase):
    """Whether the human stops at ``phase`` on their own turn."""
    return (phase in SELF_ALWAYS_STOPS or phase in _configured_stops(
        state.get("player_self_stops"), SELF_DEFAULT_STOPS))


def is_opp_stop(state, phase):
    """Whether the human stops at ``phase`` on the opponent's turn."""
    return (phase in OPP_ALWAYS_STOPS or phase in _configured_stops(
        state.get("player_opp_stops"), OPP_DEFAULT_STOPS))


def practice_priority_players(state, phase, *, active_is_player):
    """Return the native priority-window kind for a Practice/PvE phase.

    Practice has one wire client and one server-driven participant, but it
    still uses the same stop semantics as a two-player session.  When both
    players stop in the active player's phase the native queue is ``ALL``:
    the active player passes, then the opponent passes internally, and only
    then may RulesPort advance the phase.
    """
    from .kernel import TurnPhasePlayers

    if active_is_player:
        self_stop = is_self_stop(state, phase)
        opponent_stop = is_opp_stop(state, phase)
        if self_stop and opponent_stop:
            return TurnPhasePlayers.ALL
        if self_stop:
            return TurnPhasePlayers.ACTIVE
        if opponent_stop:
            return TurnPhasePlayers.ALL
        return TurnPhasePlayers.NONE

    # DeclareDefense is the one phase whose C# action deliberately queues the
    # defending player rather than APNAP/all-player priority. All ordinary
    # opponent stops use ALL so the AI can pass through its native queue.
    if phase == game_engine.ETurnPhases.DeclareDefense:
        return TurnPhasePlayers.DEFENDING
    # The server AI still needs a native priority turn to play resources and
    # cards.  A missing opponent stop only means the human should not be
    # shown that AI window; it does not mean the AI phase is NONE.  With an
    # opponent stop, ALL lets the AI act first and then exposes the human
    # response window.
    return (TurnPhasePlayers.ALL if is_opp_stop(state, phase)
            else TurnPhasePlayers.ACTIVE)


def practice_phase_priority(state, phase, *, active_player_id,
                            player_id):
    """Resolve Practice's native window policy across persisted UID forms.

    Practice checkpoints historically persist participant IDs as uint64s,
    while the live Game projection uses ``UID`` objects.  Keep that identity
    normalization beside the stop matrix so the HConnect adapter cannot
    accidentally classify the human's own phase as an opponent phase.
    """
    def uid_value(value):
        try:
            return int(getattr(value, "uid64", value))
        except (TypeError, ValueError):
            return None

    active_value = uid_value(active_player_id)
    player_value = uid_value(player_id)
    active_is_player = (
        active_value is not None and player_value is not None and
        active_value == player_value)
    return practice_priority_players(
        state, phase, active_is_player=active_is_player)


def ai_held_phase_context(state):
    """Return ``(phase, index, phases)`` for a paused AI opponent stop.

    ``ai_turn_phase_idx`` is the resume cursor written just before the AI
    hands priority to the human.  Prefer the persisted phase list, but fall
    back to the combat/base lists when a stale list cannot contain the cursor.
    """
    if not isinstance(state, dict) or state.get("ai_turn_phase_idx") is None:
        return None
    try:
        resume_idx = int(state["ai_turn_phase_idx"])
    except (TypeError, ValueError):
        return None
    candidates = []
    stored = state.get("turn_phases")
    if isinstance(stored, (list, tuple)):
        candidates.append(stored)
    candidates.extend((COMBAT_TURN_PHASES, BASE_TURN_PHASES))
    for phases in candidates:
        if 0 < resume_idx <= len(phases):
            return phases[resume_idx - 1], resume_idx - 1, phases
    return None


def load_state(session):
    """Load the session's battle checkpoint, or a fresh default state."""
    try:
        return persistence.load_state(session, default=default_state)
    except (TypeError, ValueError):
        return default_state()


def current_phase(state):
    """The phase at the checkpoint's cursor.

    Uses the persisted phase list; a projection that only records ``phase``
    returns it; otherwise the turn's list is built (and cached) first.
    """
    if isinstance(state, dict) and not state.get("turn_phases") and \
            "phase" in state:
        return state["phase"]
    phases = turn_phases(state)
    try:
        index = int(state.get("phase_idx", 0) or 0) % len(phases)
    except (TypeError, ValueError):
        index = 0
    return phases[index]


_EXPIRATIONS = "__attribute_expirations"


def advance_phase(state):
    """Advance the checkpoint's phase cursor; past EndTurn, start the next turn.

    The next turn belongs to the opponent unless a GiveBonusTurn marker keeps
    the current player (``next_turn_player``); its phase list is rebuilt from
    the combat facts (the caller sets ``player_has_ready_troop`` first).
    """
    if not isinstance(state, dict):
        return None
    state["player_passed"] = False
    state["ai_passed"] = False
    phases = turn_phases(state)
    try:
        index = int(state.get("phase_idx", 0) or 0) + 1
    except (TypeError, ValueError):
        index = 1
    if index >= len(phases):
        state["turn_player"] = next_turn_player(state)
        state["turn_number"] = int(state.get("turn_number", 1) or 1) + 1
        state["phase_idx"] = 0
        state.pop("extra_combats_this_turn", None)
        state["turn_phases"] = build_turn_phases(state)
    else:
        state["phase_idx"] = index
    return current_phase(state)


def should_draw_for_turn(state, side):
    """Return whether the active side draws in its current Draw phase."""
    if int((state or {}).get("turn_number", 1) or 1) > 1:
        return True
    first_player_draws = bool((state or {}).get("player_draws_first_turn"))
    return first_player_draws if str(side).lower() == "player" else not first_player_draws


def prime_cards_for_turn(db, session_id, active_owner_id):
    """Apply the port-owned ResetActiveCards/PrimeCard transition."""
    from pvp_db import db_warzone_display_rows, db_set_card_state_exact
    prime_mask = (
        game_engine.ECardStates.Blocking |
        game_engine.ECardStates.Attacking |
        game_engine.ECardStates.Damaged |
        game_engine.ECardStates.Healed |
        game_engine.ECardStates.HasAttacked |
        game_engine.ECardStates.HasBlocked |
        game_engine.ECardStates.ZoneChangeReplacement |
        game_engine.ECardStates.Activated |
        game_engine.ECardStates.VoidsIfDestroyed |
        game_engine.ECardStates.CameOutThisTurn |
        game_engine.ECardStates.StartedATurnOnYourSide)
    changed = []
    for card_uid, template_guid, owner_id, old_state, card_type in \
            db_warzone_display_rows(session_id, conn=db):
        old_state = int(old_state or 0)
        new_state = old_state & ~game_engine.ECardStates.CameOutThisTurn
        if int(owner_id or 0) == int(active_owner_id or 0):
            new_state = ((new_state & ~prime_mask) |
                         game_engine.ECardStates.StartedATurnOnYourSide)
        if new_state == old_state:
            continue
        db_set_card_state_exact(session_id, int(card_uid), new_state, conn=db)
        changed.append((int(card_uid), template_guid, int(owner_id or 0),
                        new_state, card_type))
    db.commit()
    return changed


def reset_armor(db, session_id, battle_state=None):
    """Card.ResetArmor for every card in the Ready-state cache.

    C# ReadyState.OnEntry runs the reset against the whole ResourceCache, so
    both players' cards and synthetic champion IntAttrs clear ArmorUsed at
    every Ready step, not only the active player's.
    """
    for column in ("permanent_buffs", "temporary_buffs"):
        db.execute(
            "UPDATE game_cards SET " + column + " = json_remove(" + column +
            ", '$.int_attrs.ArmorUsed') WHERE session_id = ? AND " + column +
            " IS NOT NULL AND json_valid(" + column + ") AND "
            "json_extract(" + column + ", '$.int_attrs.ArmorUsed') "
            "IS NOT NULL", (session_id,))
    if isinstance(battle_state, dict):
        for attrs in (battle_state.get("champion_int_attrs") or {}).values():
            if isinstance(attrs, dict):
                attrs.pop("ArmorUsed", None)
    db.commit()


_READY_BLOCKING_INTATTRS = frozenset((
    "cantready", "cantreadynormally", "cantreadynormallyhidden"))


def card_ready_blocked(db, session_id, card_uid):
    """Card.CanReadyAtStartOfTurn's IntAttr gates beyond the enum bit."""
    from pvp_db import db_card_mutation_field
    for column in ("permanent_buffs", "temporary_buffs"):
        try:
            data = json.loads(db_card_mutation_field(
                session_id, int(card_uid), column, conn=db) or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        attrs = data.get("int_attrs") if isinstance(data, dict) else None
        if not isinstance(attrs, dict):
            continue
        for name, value in attrs.items():
            if (str(name).lower() in _READY_BLOCKING_INTATTRS
                    and int(value or 0) > 0):
                return True
    return False


_READY_CLEAR_MASK = (game_engine.ECardStates.Tapped |
                     game_engine.ECardStates.Attacking |
                     game_engine.ECardStates.HasAttacked |
                     game_engine.ECardStates.Blocking |
                     game_engine.ECardStates.HasBlocked)


def ready_card(db, session_id, card_uid, previous, attrs):
    """C# Session.ReadyCard for one warzone card: clear its ready-step bits.

    A card that CantReadyAutomatically, or that ``card_ready_blocked`` holds,
    keeps Tapped. The row is written here and the new state bits returned; the
    caller raises CardReadiedEvent when Tapped was cleared, so every untap
    raises the same event whichever path readies the card.
    """
    from pvp_db import db_reset_warzone_troop
    mask = _READY_CLEAR_MASK
    blocked = bool(
        attrs and int(attrs) &
        game_engine.ECardAttributes.CantReadyAutomatically)
    if blocked or card_ready_blocked(db, session_id, card_uid):
        mask &= ~game_engine.ECardStates.Tapped
    db_reset_warzone_troop(session_id, int(card_uid), mask, conn=db)
    return int(previous) & ~mask


def ready_cards_for_turn(db, session_id, owner_id, battle_state=None):
    """Apply the port-owned Prep readiness transition for one controller."""
    from pvp_db import (db_warzone_cards_with_state,
                        db_warzone_card_state_attributes)
    changed = []
    for card_uid, template_guid, card_owner, card_state in \
            db_warzone_cards_with_state(session_id, conn=db):
        if int(card_owner or 0) != int(owner_id or 0):
            continue
        previous = int(card_state or 0)
        attrs_row = db_warzone_card_state_attributes(
            session_id, int(card_uid), conn=db)
        attrs = attrs_row[1] if attrs_row else 0
        new_state = ready_card(db, session_id, card_uid, previous, attrs)
        if new_state != previous:
            changed.append((int(card_uid), template_guid, int(card_owner or 0),
                            previous, new_state))
    db.commit()
    return changed


_READIED_FOR = "_readied_for"


def readied_this_turn(battle_state, side):
    """True once ``side`` has had this turn's ready step."""
    if not isinstance(battle_state, dict):
        return False
    marker = battle_state.get(_READIED_FOR)
    if not isinstance(marker, dict):
        return False
    turn = int(battle_state.get("turn_number", 1) or 1)
    try:
        return int(marker.get(str(side), -1)) == turn
    except (TypeError, ValueError):
        return False


def ready_step_for_turn(db, session_id, owner_id, battle_state, side,
                        handler=None):
    """C# StartTurn expiry + ReadyState readiness for the active controller.

    StartTurnState removes BeginningOfOwnersTurn effects and enqueues
    TurnStartedEvent; ReadyState.OnEntry then readies the active player's
    cards (except CantReady/CantReadyAutomatically) and resets armor before
    the Ready priority window drains TurnStarted.  Callers run this before
    dispatching TurnStartedEvent, so a start-of-turn trigger that exhausts its
    own card leaves it exhausted.  Runs once per side per turn: returns None
    when ``side`` was already readied this turn (Prep then skips it), else the
    ``ready_cards_for_turn`` change list.
    """
    if not isinstance(battle_state, dict) or readied_this_turn(
            battle_state, side):
        return None
    clear_expired_temporary_attributes(
        db, session_id, owner_id, "start_turn", clear_stat_buffs=True,
        battle_state=battle_state, handler=handler)
    reset_armor(db, session_id, battle_state)
    changes = ready_cards_for_turn(db, session_id, owner_id,
                                   battle_state=battle_state)
    marker = battle_state.get(_READIED_FOR)
    marker = dict(marker) if isinstance(marker, dict) else {}
    marker[str(side)] = int(battle_state.get("turn_number", 1) or 1)
    battle_state[_READIED_FOR] = marker
    return changes


_CANT_HEAL_AT_END_OF_TURN = "CantHealAtEndOfTurn".lower()
_OPPOSING_TROOPS_DONT_HEAL = "OpposingTroopsDontHealAtEndOfTurn".lower()


def _end_of_turn_heal_flags(template, raw_permanent, raw_temporary):
    """Lowercase heal-gating IntAttr names a card carries, printed or buffed.

    C# reads these through ``Card.GetCardContext().GetBool``, so an instance
    buff counts the same as the printed TAC.
    """
    flags = set()
    if template is not None:
        if template.cant_heal_at_end_of_turn:
            flags.add(_CANT_HEAL_AT_END_OF_TURN)
        if template.opposing_troops_dont_heal_at_end_of_turn:
            flags.add(_OPPOSING_TROOPS_DONT_HEAL)
    for raw in (raw_permanent, raw_temporary):
        try:
            buffs = json.loads(raw or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        attrs = buffs.get("int_attrs") if isinstance(buffs, dict) else None
        if isinstance(attrs, dict):
            flags.update(str(name).lower() for name, value in attrs.items()
                         if int(value or 0) > 0)
    return flags


def clear_combat_damage(db, session_id):
    """C# Session.ResetCardDamage for the end-of-turn heal.

    A troop is not healed when it carries ``CantHealAtEndOfTurn`` (it keeps
    its marked damage), or when another player's warzone card carries
    ``OpposingTroopsDontHealAtEndOfTurn``. The card's own controller still heals
    unless their opponent has such a card too, in which case both are blocked.
    """
    from pvp_db import (db_clear_warzone_damage_except,
                        db_warzone_card_heal_rows)
    from gamedata import DEFAULT_RECORD_STORE
    rows = db_warzone_card_heal_rows(session_id, conn=db)
    flags = {}
    for row in rows:
        template = DEFAULT_RECORD_STORE.get(
            "CardTemplate", str(row[2] or "").lower())
        flags[int(row[0])] = _end_of_turn_heal_flags(
            template, row[4], row[5])
    opposing_owners = {int(row[1]) for row in rows
                       if _OPPOSING_TROOPS_DONT_HEAL in flags[int(row[0])]}
    exempt = []
    for row in rows:
        if "troop" not in str(row[3] or "").lower():
            continue
        uid, owner = int(row[0]), int(row[1])
        if (_CANT_HEAL_AT_END_OF_TURN in flags[uid]
                or opposing_owners - {owner}):
            exempt.append(uid)
    db_clear_warzone_damage_except(session_id, exempt, conn=db)
    db.commit()


def clear_expired_temporary_attributes(db, session_id, owner_id, boundary,
                                       clear_stat_buffs=False,
                                       battle_state=None, handler=None):
    """Expire temporary attributes and stat grants at an authored boundary."""
    from pvp_db import (db_temporary_attribute_rows,
                        db_set_temporary_card_state)
    stat_changes = []
    if isinstance(battle_state, dict):
        from .effect_lifetimes import (expire_grants,
                                       expire_temporary_intattrs,
                                       expire_temporary_stats,
                                       expire_while_in_play)
        expire_temporary_intattrs(
            db, session_id, battle_state, boundary=boundary,
            boundary_owner=owner_id, handler=handler)
        from .effect_lifetimes import expire_owner_turn_rules
        rule_changes = expire_owner_turn_rules(
            db, session_id, battle_state, boundary, owner_id)
        expire_grants(
            db, session_id, battle_state, boundary=boundary,
            boundary_owner=owner_id, handler=handler)
        stat_changes = list(expire_temporary_stats(
            db, session_id, battle_state, boundary=boundary,
            boundary_owner=owner_id, handler=handler))
        # A while-in-play grant ends with its source, not at a turn step, so the boundary pass reconciles it too (the
        # event sweep only sees events; a source that left play during a step that emits none would linger).
        stat_changes = list(dict.fromkeys(
            stat_changes + expire_while_in_play(
                db, session_id, battle_state, handler=handler) + rule_changes))
    changed = list(stat_changes)
    for card_uid, target_owner, attrs, raw_buffs in db_temporary_attribute_rows(
            session_id, conn=db):
        try:
            buffs = json.loads(raw_buffs or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            buffs = {}
        if not isinstance(buffs, dict):
            buffs = {}
        metadata = buffs.get(_EXPIRATIONS) or {}
        expired_bits = 0
        remaining = {}
        for bit_text, rule in metadata.items():
            try:
                bit = int(bit_text)
            except (TypeError, ValueError):
                continue
            # "This turn" (EndOfTurn) ends at the end of whichever turn it was
            # cast in, even when the caster's opponent's turn is the one ending
            # (C# AbilityManager.RemoveEndOfTurnAbilities), so the end_turn
            # boundary does not need the caster to be the ending player.
            if (isinstance(rule, dict) and
                    rule.get("boundary") == boundary and
                    (boundary == "end_turn" or
                     int(rule.get("owner", -1)) == int(owner_id))):
                expired_bits |= bit
            else:
                remaining[bit_text] = rule
        if not metadata and int(target_owner or 0) == int(owner_id):
            expired_bits = int(attrs or 0)
        new_attrs = int(attrs or 0) & ~expired_bits
        if remaining:
            buffs[_EXPIRATIONS] = remaining
        else:
            buffs.pop(_EXPIRATIONS, None)
        if clear_stat_buffs:
            # Keep payloads whose own metadata says they remain active beyond
            # turn cleanup. Temporary cost changes use the source card's
            # UntilItLeavesYourHand lifetime; IntAttrs with another boundary
            # are listed in the battle checkpoint until that boundary fires.
            retained = {}
            for key in (_EXPIRATIONS, "temporary_cost_modifiers"):
                if key in buffs:
                    retained[key] = buffs[key]
            if isinstance(battle_state, dict):
                pending = {
                    str(item.get("attribute") or "")
                    for item in battle_state.get(
                        "temporary_card_int_attrs", ()) or ()
                    if isinstance(item, dict) and
                    int(item.get("uid", -1)) == int(card_uid)
                }
                int_attrs_payload = buffs.get("int_attrs")
                if isinstance(int_attrs_payload, dict):
                    active_attrs = {name: value for name, value in int_attrs_payload.items()
                                    if name in pending}
                    if active_attrs:
                        retained["int_attrs"] = active_attrs
            buffs = retained
        new_buffs = json.dumps(buffs, separators=(",", ":"), sort_keys=True)
        if (new_attrs == int(attrs or 0) and
                new_buffs == (raw_buffs or "{}")):
            continue
        db_set_temporary_card_state(
            session_id, int(card_uid), new_attrs, new_buffs, conn=db)
        changed.append(int(card_uid))
    if changed:
        db.commit()
    return changed
