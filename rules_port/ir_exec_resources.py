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
# "This turn" costs on a played action (a resolved ability with no trigger) run through the same leaf, which writes
# them to the temporary buffs the end-of-turn wipe clears.
COST_DURATIONS = {"Instant", "Permanent", "UntilItLeavesYourHand", "EndOfTurn", "BeginningOfOwnersTurn"}
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
    # A half-health loss has no amount to read: the champion's health is halved when it runs (C# LoseHalfHealth).
    if ix._short((modifier.get("m_InputValue") or {}).get("_t")) not in ("EffectInputVariable", "EffectConstant") \
            and not (kind == "LoseLifeModifier" and modifier.get("m_LoseHalfHealth")) \
            and not (kind == "ChargePointsModifier" and modifier.get("m_RemoveAllCharges")):
        missing.append(f"{kind} without a typed amount")
    player = _player_filter(raw, index)
    if player is not None and player not in ix.SUPPORTED_PLAYER_TARGETS:
        missing.append(f"{kind} for player {player}")
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
    if kind == "LoseLifeModifier" and getattr(modifier, "m_LoseHalfHealth", False):
        # C# LoseHalfHealth: the champion loses half its current health, rounded down (context.lose_life halves it).
        for target in ix._targets(root, index, scope):
            context.lose_life(int(target), 0, {"lose_half_health": True})
        return
    if kind == "ChargePointsModifier" and modifier.m_RemoveAllCharges:
        # C# ChargePoints removal: the champion loses every charge it has (the leaf takes the count to zero).
        if context.bstate.get("pvp"):
            # PvP keeps the charge count per participant; the native path removes them, before anything changes here.
            raise NotImplementedError("remove all charges in PvP")
        for target in ix._targets(root, index, scope):
            owner = context.target_owner(int(target), default=None)
            side = "player" if owner else "ai"
            current = int(context.bstate.get(f"{side}_charges", 0) or 0)
            if current > 0:
                _apply_to_target(kind, modifier, int(target), -current, context)
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
    from rules_port.ir_modifiers import card_cost_modifier as _card_cost
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


def _lose_threshold(context, effect):
    # The authored shard list lives on the effect template (m_Thresholds), not
    # in the flattened ``param`` (which is empty for this type), so the
    # previous read silently lost nothing.
    names = context.template_value("m_Thresholds", None)
    if names is None:
        try:
            names = json.loads((effect or {}).get("param") or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            names = []
    if not isinstance(names, (list, tuple)):
        names = []
    return context.lose_thresholds(names)


def _resource_modifier(context, effect):
    """Resolve the typed resource subset of CardModifier natively.

    CardModifier is a large Records union.  Returning ``None`` for every
    other property is intentional: the staged backend remains responsible
    for card stats, damage, and health until those typed operations migrate.
    """
    try:
        param = json.loads((effect or {}).get("param") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(param, dict):
        return None
    property_name = str(param.get("property") or "").lower()
    if property_name not in {"currentresource", "totalresource",
                             "chargepoints", "threshold"}:
        return None
    amount = int(param.get("amount") or 0)
    # A set (CurrentResourceModifier/TotalResourceModifier with Set) makes the pool the value, not a change of it.
    is_set = bool(param.get("set")) and property_name in {"currentresource", "totalresource"}
    if amount == 0 and not is_set:
        amount = context.modifier_value(param, {}, property_name)
    target = context.resolved_target()
    owner = context.target_owner(target, default=None)
    if owner is None:
        owner = int(context.bstate.get("resolving_owner_id", 0) or 0)
    if property_name == "chargepoints" and target is not None:
        # Session.AddChargePoints treats a non-champion Card target as a
        # counter change on that card. Only champion targets modify the
        # player's charge pool and emit GainChargeEvent. Routing every target
        # through the player pool made cards such as Eternal Drifter trigger
        # their own "when you gain a charge" ability indefinitely.
        from .runtime_helpers import champion_owner_id
        if champion_owner_id(context.handler, context.bstate, target) is None:
            old, new = context.counter(
                int(target), "charge",
                counter_guid=param.get("counter_template_guid"),
                amount=amount, operation="add")
            return f"card charge counter {old}->{new}"
    # Practice uses ``0`` for the AI owner, while PvP uses raw participant
    # IDs for both players.  Truthiness therefore cannot identify the side in
    # a PvP effect view; map the resolved owner against the view's ordered
    # participant IDs instead.
    if context.bstate.get("pvp"):
        pids = [int(pid) for pid in (context.bstate.get("pids") or ())]
        side = "player" if pids and int(owner) == pids[0] else "ai"
    else:
        side = "player" if owner else "ai"
    color = 0
    if property_name == "threshold":
        shard = str(param.get("shard") or "").rsplit(".", 1)[-1].lower()
        import game_engine
        random_lowest = param.get("randomlowestthreshold", False)
        random_color = param.get("random", False)
        random_lowest = (random_lowest is True or
                         str(random_lowest).strip().lower() in ("1", "true"))
        random_color = (random_color is True or
                        str(random_color).strip().lower() in ("1", "true"))
        if random_lowest or random_color:
            # ThresholdModifier.Apply uses the session RNG. RandomLowestThreshold
            # enumerates the player's five initialized thresholds in Player's
            # constructor order; Random uses the five shard bits in enum order.
            color_names = (("blood", "sapphire", "wild", "diamond", "ruby")
                           if random_lowest else
                           ("blood", "ruby", "sapphire", "wild", "diamond"))
            colors = [int(game_engine.SHARD_TO_FLAG[name])
                      for name in color_names]
            if random_lowest:
                thresholds = context.bstate.get(f"{side}_threshold") or {}
                current = {
                    flag: int(thresholds.get(
                        flag, thresholds.get(str(flag), 0)) or 0)
                    for flag in colors
                }
                minimum = min(current.values())
                colors = [flag for flag in colors
                          if current[flag] == minimum]
            if len(colors) > 1:
                rng = context.bstate.get("_rules_rng")
                if rng is not None and hasattr(rng, "next"):
                    color = colors[int(rng.next(0, len(colors))) % len(colors)]
                else:
                    import random
                    color = random.choice(colors)
            elif colors:
                color = colors[0]
        else:
            color = int(game_engine.SHARD_TO_FLAG.get(shard, 0))
        if not color:
            return None
    if is_set:
        pool_key = f"{side}_resources" if property_name == "currentresource" else f"{side}_total_resources"
        amount = int(amount) - int(context.bstate.get(pool_key, 0) or 0)
    from .resources import project_resource_change
    change = project_resource_change(
        context.game, context.session, context.bstate,
        context.player_uid, context.ai_uid, side, property_name, amount,
        color=color)
    if property_name == "chargepoints":
        # Session.AddChargePoints queues one singular GainChargeEvent for
        # every actual point gained, including ChargePointBonus. The counter
        # projection above remains one aggregate UI event.
        context.emit_gain_charge_triggers(
            int(change.new_value) - int(change.old_value), owner_id=owner)
    # The C# resource modifiers keep per-effect-instance accounting alongside
    # the player pool mutation. These values back shipped IntAttr operands such
    # as ResourcesDepleted and ChargePointsDrained.
    target_info = None
    if target is not None:
        from pvp_db import db_card_source_info
        target_info = db_card_source_info(
            context.session.session_id, int(target), conn=context.db)
    is_resource_card = bool(
        target_info and "Resource" in str(target_info[1] or ""))
    if not is_resource_card:
        from .statistics import add_ability_stat
        delta = int(change.new_value) - int(change.old_value)
        if property_name == "currentresource":
            if delta > 0:
                add_ability_stat(context.bstate, "ResourcesReplenished", delta)
            elif delta < 0:
                add_ability_stat(context.bstate, "ResourcesDepleted", -delta)
        elif property_name == "totalresource":
            if delta > 0:
                add_ability_stat(context.bstate, "ResourcesGained", delta)
            elif delta < 0:
                add_ability_stat(context.bstate, "ResourcesLost", -delta)
        elif property_name == "chargepoints" and delta < 0:
            add_ability_stat(
                context.bstate, "ChargePointsDrained",
                min(-delta, int(change.old_value)))
    # TurnStarted triggers resolve before the following Prep refill. Preserve
    # positive temporary current-resource gains so Prep restores the normal
    # total and then reapplies the authored bonus (for example Lithe
    # Lyricist's +1 current resource).  A current gain that came with a total
    # gain in the same StartTurn (Chained Goliath's [L1][R1]) is not a bonus:
    # the refill to total already grants it, so the two are netted here.
    if property_name in ("currentresource", "totalresource") and amount > 0:
        import game_engine
        phase = context.bstate.get("phase")
        if phase == game_engine.ETurnPhases.StartTurn:
            owner_id = int(owner or 0)
            if context.bstate.get("pvp"):
                bonus_key = f"start_turn_resource_bonus_{owner_id}"
            else:
                bonus_key = ("start_turn_resource_bonus_player"
                             if owner_id else
                             "start_turn_resource_bonus_ai")
            from .resources import record_start_turn_resource_gain
            record_start_turn_resource_gain(
                context.bstate, bonus_key, property_name, int(amount),
                int(context.bstate.get("turn_number", 1) or 1))
    label = {"currentresource": "resources",
             "totalresource": "total",
             "chargepoints": "charges",
             "threshold": f"threshold {color}"}[property_name]
    return f"{side} {label} {change.old_value}->{change.new_value}"


def _repeating(context, effect):
    """Apply C#'s nested ``RepeatingEffect`` on the shared target instance."""
    try:
        loops = int(context.value("m_LoopCount", 0) or 0)
    except (TypeError, ValueError):
        loops = 0
    if not loops:
        # RepeatingAbilityEffectTemplate.Initialize folds the deprecated
        # ability-variable form into m_LoopCount; older records only carry it.
        try:
            loops = int(context.value("m_LoopCount_DEPRECATED", 1) or 1)
        except (TypeError, ValueError):
            loops = 1
    child = context.template_value("m_RepeatingEffect", None)
    if child is None:
        return "repeat: no nested effect"
    return context.apply_repeating_effect(child, max(0, loops))
