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
