"""Card filters and ability, effect and trigger conditions for the IR interpreter, evaluated by the live path.

The live path evaluates a Records card filter in ``rules_port.filters`` and a condition in
``rules_port.conditions``. This module gives those same evaluators the interpreter's typed nodes and a
``ConditionContext`` for the ability being run, so a card passes a filter, and a condition holds, the same way on
both paths. No predicate is re-implemented here.

Coverage (``unsupported``) and run time (``*_holds``) read the same lists of accepted kinds, so an ability is refused
before it runs when the live evaluators would not run it.
"""

from __future__ import annotations

from typing import Any

from gamedata.mechanics_objects import MechanicsObject
from rules_port.condition_context import ConditionContext
from rules_port.conditions import evaluate_condition
from rules_port.filters import _FILTER_TYPES, records_filter_matches

# Condition kinds the live evaluator (``conditions._native_condition``) runs, for the ability, effect and trigger
# conditions the interpreter reaches. ``AbilityControllerHasThresholdAbilityCondition`` on an ability is checked by
# the interpreter itself (``ir_exec._ability_condition_holds``); this list covers it for an effect condition.
BRIDGED_CONDITIONS = frozenset({
    "AbilityControllerHasThresholdAbilityCondition", "AbilityControllerIsActiveAbilityCondition",
    "AbilityControllerIsStartingPlayerAbilityCondition", "AndAbilityCondition", "AndEffectCondition",
    "CardFilterAbilityCondition", "CardsDiscardedThisTurn", "ChampionActionsCastThisTurn",
    "ChampionCardsCastCondition", "OrAbilityCondition", "OrEffectCondition", "RequiresCardsControlled",
    "RequiresChampionCharges", "RequiresChampionHealth", "RequiresResourceThreshold",
    "RequiresSourcePassesFilterCondition", "RequiresTotalResources", "SourceCardHasCounters",
    "SourceCardHasKeywords", "TACTriggerCondition", "TurnPhaseCondition", "NotEffectCondition",
    "TriggerPlayerControlsCard",
    # Trigger tests the native evaluator reads from the event and the ability (conditions.evaluate_condition).
    "TriggerCardIsNthCardDrawnThisTurnByThisPlayer", "TriggerAbilityHasSpellPointCostModifier",
    "TriggerAbilityIsSpellPower", "TriggerAbilityHasUsesPerGameValue", "RequiresEncounterGameboard",
    "TriggerCardSameNameInZone",
})

# Filter classes the live predicate set has, but whose result depends on state the interpreter does not set: the
# stored targets, the chain stack, the movement history, or the other players' cards. They are refused.
DENIED_FILTERS = frozenset({
    "IsStoredCardFilter", "MatchesTargetFilter", "HasASharedShardWithTopOfChainFilter",
    "MovedBySource",
})

_COMBINATORS = frozenset({"AndCardFilter", "OrCardFilter", "NotCardFilter"})


def _kind(type_name: Any) -> str:
    """A Records class name without its namespace ("Game.Shared.Mechanics.X" -> "X")."""
    return str(type_name or "").rsplit(".", 1)[-1]


def filter_accepted(class_name: str) -> bool:
    """Whether the live predicate set evaluates this card filter class (and it is not one of the denied ones)."""
    if class_name in DENIED_FILTERS or class_name in _COMBINATORS:
        return class_name in _COMBINATORS
    return class_name.replace("Filter", "").lower() in _FILTER_TYPES


def _record(value: Any) -> Any:
    """The Records form of a typed node, as the live evaluators read it (without its version table)."""
    if isinstance(value, MechanicsObject):
        return {key: item for key, item in value.to_record().items() if key != "_v"}
    return value


def unsupported(node: Any, prefix: str) -> list[str]:
    """Every class in a stored Records condition the live evaluators cannot run, labelled ``prefix`` (for example
    "effect condition"). A card filter inside it is labelled "filter". Empty when all of it runs."""
    missing: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            kind = _kind(value.get("_t"))
            # A ResourceId is a value (a counter's identity), not a construct.
            if kind and kind != "ResourceId" and kind not in BRIDGED_CONDITIONS and not filter_accepted(kind):
                is_filter = kind.endswith("Filter") or kind in DENIED_FILTERS
                missing.append(f"filter {kind}" if is_filter else f"{prefix} {kind}")
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(_record(node))
    return sorted(set(missing))


def _champions(context: Any) -> list[tuple]:
    """The champions as ``(card uid, owner, name, health)``, from the handler the live path holds. A context with no
    handler (the static paths) takes them from the battle state's champion map, with the health the handler reads."""
    champions = getattr(context.handler, "_champion_targets", lambda: [])() or []
    if champions:
        return list(champions)
    state = context.bstate or {}
    rows = []
    for owner, uid in (state.get("champ_map") or {}).items():
        is_ai = int(owner) == 0
        health = state.get("ai_health" if is_ai else "player_health", 20)
        rows.append((int(uid), int(owner), "AI" if is_ai else "Player", int(health)))
    return rows


def _secondary_uids(scope: Any) -> tuple[int, ...]:
    """The cards the clause's secondary target names (C# EffectSecondaryTarget). A condition with SameOwner reads
    their owner, so an effect such as "destroy that champion if its deck has 250 Eggs" needs them."""
    from rules_port import ir_exec_targets
    from rules_port.ir_exec import _field
    clause = getattr(scope, "clause", None)
    if clause is None:
        return ()
    instance = ir_exec_targets.instance_ref(_field(clause, "m_SecondaryTargetIndex"))
    if instance is None or int(instance) < 0:
        return ()
    return tuple(int(uid) for uid in (scope.resolved_by_instance.get(instance) or ()))


def condition_context(scope: Any, *, target: int | None = None, event_type: str | None = None) -> ConditionContext:
    """A condition context for the ability in ``scope``. The source and its controller are the ability's own; the
    trigger is the event's card, or ``target`` when an effect condition tests a target. ``event_type`` is the event a
    trigger condition is judged for (a threshold's colour is read from a GainThresholdEvent). Built fresh for each use,
    because a condition reads the battle as it stands when it is tested."""
    context = scope.context
    champions = _champions(context)
    event = scope.event or {}
    condition = ConditionContext(
        context.db, context.session, context.bstate, event_type=event_type or "AbilityEffectEvent",
        ability_source_uid=int(scope.source_uid), ability_source_owner_id=int(scope.owner_uid),
        trigger_uid=target if target is not None else event.get("card"), extra_target=target,
        pl_t=getattr(context, "player_uid", None), ai_t=getattr(context, "ai_uid", None), champions=champions,
        trigger_owner_id=event.get("player"),
        event_source_collection=event.get("from"), event_destination_collection=event.get("to"),
        event_tac=event.get("event_tac"), effect_secondary_target_uids=_secondary_uids(scope))
    # The ability's bound variables (a number the player chose, say) are what its filters read, as the native path reads
    # them from the ability's activation.
    condition.ability_variables = dict(scope.variables or {})
    return condition


def filter_holds(card_filter: Any, uid: int, scope: Any) -> bool:
    """Whether the card ``uid`` passes a card filter, as the live card filters judge it. The source is the ability's."""
    context = condition_context(scope)
    card = context.card(uid)
    if card is None:
        return False
    return records_filter_matches(card, _record(card_filter), source=context.card(scope.source_uid), context=context)


def trigger_card_holds(card_filter: Any, scope: Any) -> bool:
    """A card filter that is itself the trigger condition: it judges the event's card, else the ability's source (as
    the live trigger check does for an IntAttrFilter)."""
    context = condition_context(scope)
    card = context.card(scope.event.get("card")) if scope.event.get("card") is not None else None
    if card is None:
        card = context.card(scope.source_uid)
    if card is None:
        return False
    return records_filter_matches(card, _record(card_filter), context=context)


def condition_holds(condition: Any, scope: Any, *, target: int | None = None) -> bool:
    """Whether a Records condition holds, as the live condition check finds it for this ability. A TAC trigger condition
    reads the event the trigger raised (a threshold's colour, say), so it is judged for that event's type."""
    record = _record(condition)
    event_type = None
    if str((record or {}).get("_t") or "").rsplit(".", 1)[-1] == "TACTriggerCondition":
        event_type = (scope.event or {}).get("event_type")
    return bool(evaluate_condition(record, condition_context(scope, target=target, event_type=event_type)))


def target_gate(stored: Any, scope: Any):
    """The test a RequiresTargetPassesFilterCondition applies to each target of its clause, as a ``uid -> bool``."""
    return lambda uid: condition_holds(stored, scope, target=int(uid))


__all__ = ["BRIDGED_CONDITIONS", "DENIED_FILTERS", "condition_context", "condition_holds", "filter_accepted",
           "filter_holds", "target_gate", "trigger_card_holds", "unsupported"]
