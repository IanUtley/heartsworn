"""Area I of the interpreter: resource pools, charges, thresholds, life and spell points, and card costs.

Each construct runs the live leaf it shares with the native resolver, with its target passed explicitly. A player
target stands for the player through the player's champion UID (as ``ir_exec._targets`` gives it), so the leaves see
the same target the native resolver does. Anything this module does not run is reported by ``coverage_*`` and the
ability is refused before it runs."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator

from gamedata.mechanics_objects import MechanicsObject
from rules_port import ir_exec as ix

# The CardModifier kinds that change a player's pools, thresholds, life or spell points, mapped to the property name
# the live resource leaf takes.
RESOURCE_PROPERTIES = {
    "CurrentResourceModifier": "currentresource",
    "TotalResourceModifier": "totalresource",
    "ChargePointsModifier": "chargepoints",
    "ThresholdModifier": "threshold",
}
PLAYER_MODIFIERS = set(RESOURCE_PROPERTIES) | {"LoseLifeModifier", "SpellPointsModifier"}
CARD_COST_MODIFIER = "CardCostModifier"
CARD_THRESHOLD_MODIFIER = "CardThresholdModifier"
MODIFIERS = PLAYER_MODIFIERS | {CARD_COST_MODIFIER, CARD_THRESHOLD_MODIFIER}
# The effects that change a player's pools or thresholds without a modifier.
EFFECTS = {"ReplenishResourcesAbilityEffectTemplate", "LoseThresholdAbilityEffectTemplate"}
# The clause durations a resource change runs with. The live leaves do not read the duration, so only the
# instantaneous and permanent durations are the same on both paths. Lasting durations belong to area H.
RESOURCE_DURATIONS = {"Instant", "Permanent"}
# A card cost change runs through the live cost leaf, which handles these two durations.
COST_DURATIONS = {"Instant", "Permanent"}
# A linked clause (triggered, player-activated, or an invoked child's) also takes the while-in-play and this-turn
# lifetimes: the cost delta is applied at resolution and recorded in the linked ledger, which reverses it when its
# source is gone or at the end of the turn.
LINKED_COST_DURATIONS = COST_DURATIONS | {"WhileCardInPlay", "EndOfTurn"}


def _target_template(raw: dict[str, Any], index: int) -> tuple[str, dict[str, Any]]:
    """The kind and stored record of a clause's target template, from the ability's raw record."""
    guid = ix._guid(raw["m_AbilityTargetTemplateIds"][index])
    return ix._stored("AbilityTargetTemplate", guid)


def _player_filter(raw: dict[str, Any], index: int) -> str | None:
    """The player filter of a clause's player target, or None when the clause does not target a player."""
    kind, template = _target_template(raw, index)
    if ix._short(kind) != "PlayerTargetTemplate":
        return None
    return str(template.get("m_PlayerFilter") or "")


def coverage_modifier(kind: str, effect: dict[str, Any], clause: dict[str, Any], raw: dict[str, Any],
                      *, linked: bool = False) -> list[str]:
    """The constructs of one CardModifier clause this module cannot run (empty when it runs it).

    ``linked`` says the clause belongs to a triggered, player-activated or invoked ability, whose cost change applies
    at resolution and is recorded in the linked ledger rather than projected by the static scan.
    """
    modifier = effect.get("m_Modifier") or {}
    duration = str(clause.get("m_EffectDuration") or "")
    index = int(clause.get("m_TargetTemplateIndex") or 0)
    missing: list[str] = []
    if kind == CARD_COST_MODIFIER:
        if duration not in (LINKED_COST_DURATIONS if linked else COST_DURATIONS):
            missing.append(f"cost duration {duration}")
        return missing
    if duration not in RESOURCE_DURATIONS:
        missing.append(f"{kind} duration {duration}")
    if kind == CARD_THRESHOLD_MODIFIER:
        # Its thresholds are set on each card it names, with no amount to read.
        return missing
    if ix._short((modifier.get("m_InputValue") or {}).get("_t")) not in ("EffectInputVariable", "EffectConstant"):
        missing.append(f"{kind} without a typed amount")
    player = _player_filter(raw, index)
    if player is not None and player not in ix.SUPPORTED_PLAYER_TARGETS:
        missing.append(f"{kind} for player {player}")
    if kind == "ChargePointsModifier" and modifier.get("m_RemoveAllCharges"):
        missing.append("remove all charges")
    if kind == "LoseLifeModifier" and modifier.get("m_LoseHalfHealth"):
        missing.append("lose half health")
    return missing


def coverage_effect(kind: str, effect: dict[str, Any], clause: dict[str, Any], raw: dict[str, Any]) -> list[str]:
    """The constructs of one resource effect this module cannot run (empty when it runs it)."""
    duration = str(clause.get("m_EffectDuration") or "")
    index = int(clause.get("m_TargetTemplateIndex") or 0)
    missing: list[str] = []
    if duration not in RESOURCE_DURATIONS:
        missing.append(f"{ix._short(kind)} duration {duration}")
    player = _player_filter(raw, index)
    if player not in ix.SUPPORTED_PLAYER_TARGETS:
        missing.append(f"{ix._short(kind)} for {player or 'a card'}")
    return missing


@contextmanager
def _bridge_target(bstate: dict[str, Any], champion: int) -> Iterator[None]:
    """Name a player's champion as the target of the resource leaves, for the duration of one leaf call. The leaves
    read the target from these aliases before the source card, so each one is set and restored."""
    keys = ("player_spell_target", "player_mod_target", "resolving_target_uid")
    saved = {key: bstate.get(key) for key in keys}
    try:
        for key in keys:
            bstate[key] = int(champion)
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                bstate.pop(key, None)
            else:
                bstate[key] = value


def _player_champion(root: MechanicsObject, index: int, scope: ix.Scope) -> int:
    """The champion that stands for the player a clause targets (the one target a player template gives)."""
    targets = ix._targets(root, index, scope)
    if len(targets) != 1:
        raise NotImplementedError("a resource change needs exactly one player")
    return int(targets[0])


def run_modifier(kind: str, modifier: MechanicsObject, root: MechanicsObject, clause: Any, scope: ix.Scope) -> None:
    """Run one CardModifier clause of a resource kind through the live leaf for it, once per target. A player target is
    the player's champion; a card target is the card, whose controller is the one the leaf charges."""
    context = scope.context
    index = int(ix._field(clause, "m_TargetTemplateIndex") or 0)
    if kind == CARD_COST_MODIFIER:
        _run_card_cost(modifier, root, index, str(ix._field(clause, "m_EffectDuration") or ""), scope)
        return
    if kind == CARD_THRESHOLD_MODIFIER:
        for target in ix._targets(root, index, scope):
            context.card_threshold(int(target), {
                "shard": str(modifier.m_Shard),
                "setthresholds": bool(modifier.m_SetThresholds),
                "copysourcecard": bool(modifier.m_CopySourceCard),
            })
        return
    amount = ix._input_value(modifier, scope)
    for target in ix._targets(root, index, scope):
        _apply_to_target(kind, modifier, int(target), amount, context)


def _apply_to_target(kind: str, modifier: MechanicsObject, target: int, amount: int, context: Any) -> None:
    """One resource or life change on one target, through the live leaf for its kind."""
    if kind in RESOURCE_PROPERTIES:
        from rules_port.effects import _resource_modifier
        param: dict[str, Any] = {"property": RESOURCE_PROPERTIES[kind], "amount": amount,
                                 "set": bool(getattr(modifier, "m_Set", False))}
        if kind == "ThresholdModifier":
            param.update({
                "shard": str(modifier.m_ThresholdColor),
                "random": bool(modifier.m_Random),
                "randomlowestthreshold": bool(modifier.m_RandomLowestThreshold),
            })
        with _bridge_target(context.bstate, target):
            _resource_modifier(context, {"param": json.dumps(param)})
    elif kind == "LoseLifeModifier":
        context.lose_life(target, amount, {"lose_half_health": False})
    elif kind == "SpellPointsModifier":
        context.spell_points(target, amount)
    else:
        raise NotImplementedError(f"modifier {kind}")


def _run_card_cost(modifier: MechanicsObject, root: MechanicsObject, index: int, duration: str,
                   scope: ix.Scope) -> None:
    """C# CardCostModifier: each target's cost changes through the live cost leaf, for the clause's duration."""
    from rules_port.effects import _card_cost
    amount = ix._input_value(modifier, scope)
    replace = bool(modifier.m_ReplaceExistingValue)
    if not amount and not replace:
        return
    for target in ix._targets(root, index, scope):
        _card_cost(scope.context, int(target), {
            "amount": int(amount),
            "replaceexistingvalue": replace,
            "duration": duration,
        })


def run_effect(kind: str, effect_raw: dict[str, Any], root: MechanicsObject, clause: Any, scope: ix.Scope) -> None:
    """Run one resource effect (replenish the pool, lose thresholds) through its live leaf."""
    context = scope.context
    index = int(ix._field(clause, "m_TargetTemplateIndex") or 0)
    champion = _player_champion(root, index, scope)
    if kind == "ReplenishResourcesAbilityEffectTemplate":
        # The pool refilled is the caster's (the owner the interpreter bridges for the run).
        context.replenish_resources()
    elif kind == "LoseThresholdAbilityEffectTemplate":
        with _bridge_target(context.bstate, champion):
            context.lose_thresholds(list(effect_raw.get("m_Thresholds") or []))
    else:
        raise NotImplementedError(f"effect {kind}")


def coverage_invocation_cost(raw: dict[str, Any]) -> list[str]:
    """An ability that both invokes another ability and changes a card's cost is refused. The invoked ability draws
    its random picks in a different order from the native resolver, so the cost change can land on a different card
    than the native path (Mawstorm). Every other cost change runs as the native resolver runs it."""
    kinds = []
    for clause in raw.get("m_AbilityEffectList") or []:
        effect_type, effect = ix._stored("AbilityEffectTemplate", ix._guid(clause.get("m_EffectTemplateId")))
        kinds.append((ix._short(effect_type), ix._short((effect.get("m_Modifier") or {}).get("_t"))))
    invokes = any(kind == "ActivateAbilityEffectTemplate" for kind, _ in kinds)
    changes_cost = any(modifier == CARD_COST_MODIFIER for _, modifier in kinds)
    return ["cost change in an ability that invokes another"] if invokes and changes_cost else []
