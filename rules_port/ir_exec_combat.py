"""Combat, tap and turn effects for the IR interpreter (rules_port/ir_exec.py).

Each effect runs through the live primitive the native resolver uses, with the cards it acts on passed
explicitly: tap and untap (rules_port/effects.py), remove from combat (rules_port/context.py), the bonus turn
(rules_port/context.py) and the fight (rules_port/battle_effects.py). The interpreter's caster bridge supplies the
controller, so no effect logic is forked here.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from rules_port import ir_exec
from rules_port import ir_exec_targets

# The effect templates this module runs. ir_exec.coverage() accepts these through coverage_missing(), and
# ir_exec._run_clause hands them to run_clause().
COMBAT_EFFECTS = {
    "TapCardAbilityEffectTemplate", "UntapCardAbilityEffectTemplate", "RemoveCardFromCombatAbilityEffectTemplate",
    "GiveBonusTurnAbilityEffectTemplate", "Battle2CardsAbilityEffectTemplate", "BlockEffectTemplate",
    "SwapHealthAbilityEffectTemplate", "VerdictAbilityEffectTemplate",
}


def _second_clause(clauses: Any, clause: Any) -> Any:
    """The clause whose effect instance a fight's second card is read from (m_SecondaryTargetIndex is an effect
    instance id, not a target slot). An instance id of 0 is a real clause: only -1 means "none"."""
    wanted = ir_exec_targets.instance_ref(ir_exec._field(clause, "m_SecondaryTargetIndex"))
    if wanted < 0:
        return None
    for other in clauses:
        if ir_exec_targets.instance_ref(ir_exec._field(other, "m_EffectInstanceId")) == wanted:
            return other
    return None


def _second_targets(root: Any, clause: Any, scope: ir_exec.Scope) -> list[int]:
    """The cards the clause's referenced effect instance resolved: the native battle_effects leaves read them from
    the activation's target map, which the interpreter keeps as ``scope.resolved_by_instance``. A forward reference
    (an instance that has not run yet) resolves through its target, as the native target map already holds it."""
    second = _second_clause(root.m_AbilityEffectList, clause)
    if second is None:
        raise NotImplementedError("second card without a referenced clause")
    instance = ir_exec_targets.instance_ref(ir_exec._field(second, "m_EffectInstanceId"))
    if instance in scope.resolved_by_instance:
        return [int(uid) for uid in scope.resolved_by_instance[instance]]
    return ir_exec._targets(root, int(ir_exec._field(second, "m_TargetTemplateIndex") or 0), scope)


def coverage_missing(effect_type: str, clause: Any, clauses: Any) -> list[str]:
    """The reasons this combat effect clause cannot be run (empty when it can)."""
    if effect_type in ("Battle2CardsAbilityEffectTemplate", "BlockEffectTemplate") \
            and _second_clause(clauses, clause) is None:
        return ["fight or block without a second card"]
    return []


def run_clause(effect_type: str, root: Any, clause: Any, effect_raw: dict[str, Any], scope: ir_exec.Scope) -> None:
    """Run one combat, tap or turn effect clause against the cards its target picks."""
    context = scope.context
    targets = ir_exec._targets(root, int(ir_exec._field(clause, "m_TargetTemplateIndex") or 0), scope)
    previous_template = context.effect_template_override
    context.effect_template_override = effect_raw
    try:
        with ir_exec._bridge_caster(context.bstate, owner=scope.owner_uid):
            if effect_type == "TapCardAbilityEffectTemplate":
                from rules_port.effects import _tap
                _tap(context, targets=targets)
            elif effect_type == "UntapCardAbilityEffectTemplate":
                from rules_port.effects import _untap
                _untap(context, targets=targets)
            elif effect_type == "RemoveCardFromCombatAbilityEffectTemplate":
                for target in targets:
                    context.remove_from_combat(target=int(target))
            elif effect_type == "GiveBonusTurnAbilityEffectTemplate":
                # The native leaf queues the caster's next turn, whatever card the effect names.
                context.queue_bonus_turn()
            elif effect_type == "Battle2CardsAbilityEffectTemplate":
                _fight(root, clause, scope, targets)
            elif effect_type == "BlockEffectTemplate":
                _block(root, clause, scope, targets)
            elif effect_type == "SwapHealthAbilityEffectTemplate":
                # The native leaf swaps the controller's health with the champion it is targeting, if any.
                with _target_set(context.bstate, resolving_target_uid=targets[0] if targets else None):
                    context.swap_health()
            elif effect_type == "VerdictAbilityEffectTemplate":
                # The live verdict leaf resolves once for each card it is given, as the native path does.
                for target in targets:
                    with _target_set(context.bstate, resolving_target_uid=int(target)):
                        context.verdict()
            else:
                raise NotImplementedError(f"effect {effect_type}")
    finally:
        context.effect_template_override = previous_template


def _fight(root: Any, clause: Any, scope: ir_exec.Scope, defenders: list[int]) -> None:
    """The clause's target is the card (or cards) fought; the first card the referenced effect instance resolved is
    the one that fights back, as the native resolver reads them (battle_effects.battle_cards: the referenced
    instance's first card deals its damage once to each of this clause's targets)."""
    from rules_port.battle_effects import fight_cards
    attackers = _second_targets(root, clause, scope)
    if not attackers or not defenders:
        # The native fight needs two cards, so a side with none does nothing.
        return
    attacker = int(attackers[0])
    for defender in defenders:
        fight_cards(scope.context, attacker, int(defender))


def _block(root: Any, clause: Any, scope: ir_exec.Scope, attackers: list[int]) -> None:
    """The clause's target is the attacking troop (or troops), and the first card the referenced effect instance
    resolved is the blocker, as the native resolver reads them (combat_effects.block: one assignment per target)."""
    blockers = _second_targets(root, clause, scope)
    if not attackers or not blockers:
        return
    blocker = int(blockers[0])
    from rules_port.combat_effects import block
    for attacker in attackers:
        with _target_set(scope.context.bstate, player_spell_target=int(attacker),
                         resolving_target_uid=int(attacker), resolving_secondary_target_uid=blocker):
            block(scope.context)


@contextmanager
def _target_set(bstate: dict[str, Any], **keys: Any) -> Iterator[None]:
    """Set the target keys the live leaves read, for one call, and restore them after. A key set to None is left
    unset, so the leaf falls back as it does on the live path."""
    saved = {key: bstate.get(key) for key in keys}
    try:
        for key, value in keys.items():
            if value is not None:
                bstate[key] = value
            else:
                bstate.pop(key, None)
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                bstate.pop(key, None)
            else:
                bstate[key] = value


# AssignBlocker: the native block leaf (moved from rules_port.combat_effects).
def _active_combat(battle_state, attacker_uid):
    """Return the active attacker map and the map that stores its blockers."""
    state = battle_state or {}
    if state.get("pvp"):
        return state.get("attackers") or {}, "blockers"
    for key in ("ai_attackers", "player_attackers"):
        attackers = state.get(key) or {}
        if any(int(uid) == int(attacker_uid) for uid in attackers):
            # PvE's shared combat resolver uses ai_blockers for either side.
            return attackers, "ai_blockers"
    return {}, "ai_blockers"


def block(context):
    """Port of ``BlockEffectTemplate`` (``AssignBlocker``).

    The client receives the attacking troop as this effect's target and takes
    the blocker from ``m_SecondaryTargetIndex`` (normally the troop created by
    the preceding Conscript/PutIntoPlay effects).  No card text or card-name
    special case is needed: the metadata supplies both cards.
    """
    import game_engine
    from pvp_db import (db_card_owner_zone_state, db_card_sacrifice_info,
                        db_update_card_state)

    from .combat_rules import can_block
    from .runtime_helpers import champion_owner_id

    battle_state = context.bstate or {}
    attacker_uid = battle_state.get("player_spell_target")
    if attacker_uid is None:
        attacker_uid = context.resolved_target(source_fallback=False)
    blocker_uid = battle_state.get("resolving_secondary_target_uid")
    if attacker_uid is None or blocker_uid is None:
        return "block: missing attacker or blocker"
    attacker_uid = int(attacker_uid)
    blocker_uid = int(blocker_uid)

    attackers, blocker_key = _active_combat(battle_state, attacker_uid)
    defender_id = None
    for uid, defender in attackers.items():
        try:
            if int(uid) == attacker_uid:
                defender_id = champion_owner_id(
                    context.handler, battle_state, int(defender))
                break
        except (TypeError, ValueError):
            continue
    session_id = context.session.session_id
    blocker_state = db_card_owner_zone_state(
        session_id, blocker_uid, conn=context.db)
    blocker_type = db_card_sacrifice_info(
        session_id, blocker_uid, conn=context.db)
    blocker_row = ((blocker_state[0], blocker_state[1], blocker_type[2],
                    blocker_state[2])
                   if blocker_state and blocker_type else None)
    attacker_state = db_card_owner_zone_state(
        session_id, attacker_uid, conn=context.db)
    attacker_row = (attacker_state[1],) if attacker_state else None
    if not attackers or not attacker_row or attacker_row[0] != "warzone":
        return "block: attacker is not active"
    if not blocker_row or blocker_row[1] != "warzone":
        return "block: blocker is not in play"
    if "Troop" not in (blocker_row[2] or ""):
        return "block: blocker is not a troop"
    if defender_id is not None and int(blocker_row[0]) != int(defender_id):
        return "block: blocker controls the wrong side"
    existing = battle_state.get(blocker_key) or {}
    if any(blocker_uid in [int(value) for value in (values or [])]
           for values in existing.values()):
        return "block: blocker already assigned"
    if not can_block(context.db, session_id, battle_state,
                     attacker_uid, blocker_uid):
        return "block: illegal combat assignment"

    assigned = list(existing.get(str(attacker_uid), []) or [])
    if blocker_uid in [int(value) for value in assigned]:
        return "block: blocker already assigned"
    assigned.append(blocker_uid)
    existing[str(attacker_uid)] = [str(value) for value in assigned]
    battle_state[blocker_key] = existing
    db_update_card_state(
        session_id, blocker_uid,
        set_bits=game_engine.ECardStates.Blocking |
        game_engine.ECardStates.HasBlocked, conn=context.db)
    context.db.commit()

    defender_uid = None
    for uid, value in attackers.items():
        if int(uid) == attacker_uid:
            defender_uid = int(value)
            break
    if defender_uid is None:
        return "block: defender missing"
    if battle_state.get("pvp"):
        defender_owner = champion_owner_id(
            context.handler, battle_state, defender_uid)
        attacker_owner = next(
            (int(pid) for pid in (battle_state.get("champ_map") or {})
             if int(pid) != int(defender_owner or -1)), 0)
        combat_owner = game_engine.UID.make(244, attacker_owner)
    else:
        combat_owner = (context.ai_uid if (
            battle_state.get("ai_attackers") and
            str(attacker_uid) in battle_state["ai_attackers"])
            else context.player_uid)
    combat_id = game_engine.CombatId(combat_owner, attacker_uid & 0xFFFF)
    blocker_scids = [game_engine.SessionCardId(game_engine.UID(value))
                     for value in assigned]
    context.game.push_blockers_assigned(
        combat_id,
        game_engine.SessionCardId(game_engine.UID(attacker_uid)),
        game_engine.SessionCardId(game_engine.UID(defender_uid)),
        blocker_scids)

    # AssignBlocker queues these client trigger events; resolve them through
    # the shared native trigger dispatcher after the visible combat event.
    from .triggers import dispatch_native_trigger
    blocker_owner = int(blocker_row[0])
    dispatch_native_trigger(
        db=context.db, handler=context.handler, game=context.game,
        session=context.session, player_uid=context.player_uid,
        ai_uid=context.ai_uid, battle_state=battle_state,
        event_type="CardBlockedEvent", source_card_id=blocker_uid,
        source_player_id=blocker_owner, target_card_id=attacker_uid)
    attacker_owner = champion_owner_id(
        context.handler, battle_state, defender_uid)
    if attacker_owner is not None:
        dispatch_native_trigger(
            db=context.db, handler=context.handler, game=context.game,
            session=context.session, player_uid=context.player_uid,
            ai_uid=context.ai_uid, battle_state=battle_state,
            event_type="CardWasBlockedEvent", source_card_id=attacker_uid,
            source_player_id=attacker_owner)
    return f"blocked {hex(attacker_uid)} with {hex(blocker_uid)}"
