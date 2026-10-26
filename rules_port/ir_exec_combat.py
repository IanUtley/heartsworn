"""Combat, tap and turn effects for the IR interpreter (rules_port/ir_exec.py).

Each effect runs through the live primitive the native resolver uses, with the cards it acts on passed
explicitly: tap and untap (rules_port/effects.py), remove from combat (rules_port/context.py), the bonus turn
(rules_port/context.py) and the fight (rules_port/battle_effects.py). The interpreter's caster bridge supplies the
controller, so no effect logic is forked here.
"""

from __future__ import annotations

from typing import Any

from rules_port import ir_exec

# The effect templates this module runs. ir_exec.coverage() accepts these through coverage_missing(), and
# ir_exec._run_clause hands them to run_clause().
COMBAT_EFFECTS = {
    "TapCardAbilityEffectTemplate", "UntapCardAbilityEffectTemplate", "RemoveCardFromCombatAbilityEffectTemplate",
    "GiveBonusTurnAbilityEffectTemplate", "Battle2CardsAbilityEffectTemplate",
}


def _second_clause(clauses: Any, clause: Any) -> Any:
    """The clause whose effect instance a fight's second card is read from (m_SecondaryTargetIndex is an effect
    instance id, not a target slot)."""
    wanted = int(ir_exec._field(clause, "m_SecondaryTargetIndex") or -1)
    if wanted < 0:
        return None
    for other in clauses:
        if int(ir_exec._field(other, "m_EffectInstanceId") or -1) == wanted:
            return other
    return None


def coverage_missing(effect_type: str, clause: Any, clauses: Any) -> list[str]:
    """The reasons this combat effect clause cannot be run (empty when it can)."""
    if effect_type == "Battle2CardsAbilityEffectTemplate" and _second_clause(clauses, clause) is None:
        return ["fight without a second card"]
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
            else:
                raise NotImplementedError(f"effect {effect_type}")
    finally:
        context.effect_template_override = previous_template


def _fight(root: Any, clause: Any, scope: ir_exec.Scope, defenders: list[int]) -> None:
    """The clause's target is the card that is fought; the referenced effect's target is the card that fights it,
    as the native resolver reads them (battle_effects.battle_cards)."""
    from rules_port.battle_effects import fight_cards
    second = _second_clause(root.m_AbilityEffectList, clause)
    if second is None:
        raise NotImplementedError("fight without a second card")
    second_index = int(ir_exec._field(second, "m_TargetTemplateIndex") or 0)
    attackers = ir_exec._targets(root, second_index, scope)
    if not attackers or not defenders:
        # The native fight needs two cards, so a side with none does nothing.
        return
    if len(attackers) != 1 or len(defenders) != 1:
        raise NotImplementedError("fight with more than one card on a side")
    fight_cards(scope.context, int(attackers[0]), int(defenders[0]))
