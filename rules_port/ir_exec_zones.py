"""Interpreter runners for draw, discard, destroy, void and bury effects (gap area J).

Each effect calls the live leaf on EffectContext with its target passed explicitly, the same way the move and
reveal runners do. The leaves are the live ones, so the client events match the native path. The helpers that
resolve targets and owners live in rules_port.ir_exec; they are imported inside the functions to avoid a cycle.
"""

from __future__ import annotations

from typing import Any

# The effect templates this module runs, by class name.
ZONE_EFFECTS = {
    "DrawNCardsAbilityEffectTemplate",
    "PutTopOfDeckIntoHandAbilityEffectTemplate",
    "DiscardCardAbilityEffectTemplate",
    "DestroyCardAbilityEffectTemplate",
    "DestroyCardByDefenseAbilityEffectTemplate",
    "VoidCardAbilityEffectTemplate",
    "BuryCardAbilityEffectTemplate",
}
# The effects whose number is an input value the interpreter binds (m_InputValue, or m_Amount for a bury).
AMOUNT_FIELDS = {
    "DrawNCardsAbilityEffectTemplate": "m_InputValue",
    "PutTopOfDeckIntoHandAbilityEffectTemplate": "m_InputValue",
    "BuryCardAbilityEffectTemplate": "m_Amount",
}
TYPED_VALUES = ("EffectInputVariable", "EffectConstant")
# Discard, destroy and void act on a card, never on a player.
CARD_ONLY_EFFECTS = {
    "DiscardCardAbilityEffectTemplate",
    "DestroyCardAbilityEffectTemplate",
    "VoidCardAbilityEffectTemplate",
}


def _target_kind(raw: dict[str, Any], clause: dict[str, Any]) -> str:
    """The class name of the target template a clause names (from its stored AbilityTargetTemplate)."""
    from rules_port.ir_exec import _guid, _short, _stored
    ids = raw.get("m_AbilityTargetTemplateIds") or []
    index = int(clause.get("m_TargetTemplateIndex") or 0)
    template_type, _template = _stored("AbilityTargetTemplate", _guid(ids[index]))
    return _short(template_type)


def zone_effect_gaps(kind: str, raw: dict[str, Any], clause: dict[str, Any], effect: dict[str, Any]) -> list[str]:
    """The reasons a draw, discard, destroy, void or bury clause cannot run in the interpreter (empty when it can)."""
    from rules_port.ir_exec import _short
    gaps: list[str] = []
    short = kind[:-len("AbilityEffectTemplate")]
    if kind in AMOUNT_FIELDS:
        amount = effect.get(AMOUNT_FIELDS[kind])
        amount_kind = _short(amount.get("_t")) if isinstance(amount, dict) else ""
        # A bury may leave its count out when it counts the top half of the deck or a filter instead.
        optional = kind == "BuryCardAbilityEffectTemplate" and not amount
        # A draw's count may be the player's chosen amount, an ability variable the activation sets (C# DrawAmount).
        typed = set(TYPED_VALUES) | ({"EffectAbilityVariable"} if kind == "DrawNCardsAbilityEffectTemplate" else set())
        if not optional and amount_kind not in typed:
            gaps.append(f"{short} amount {amount_kind or 'untyped'}")
    if kind in CARD_ONLY_EFFECTS and _target_kind(raw, clause) == "PlayerTargetTemplate":
        gaps.append(f"{short} for a player target")
    return gaps


def run_zone_effect(kind: str, root: Any, clause: Any, effect_raw: dict[str, Any], scope: Any) -> None:
    """Run one draw, discard, destroy, void or bury clause through the live leaf, once per target."""
    from rules_port.ir_exec import _bridge_caster, _field, _targets
    context = scope.context
    index = int(_field(clause, "m_TargetTemplateIndex") or 0)
    previous_template = context.effect_template_override
    context.effect_template_override = effect_raw
    try:
        with _bridge_caster(context.bstate, owner=scope.owner_uid, source=scope.source_uid,
                            variables=scope.variables):
            if kind == "DestroyCardByDefenseAbilityEffectTemplate":
                # The live leaf rolls for every warzone troop, so it runs once per target, as the resolver calls it.
                for _target in _targets(root, index, scope):
                    context.destroy_by_defense()
            elif kind == "DrawNCardsAbilityEffectTemplate":
                # The live leaf draws once per target, for that target's controller (a player target's champion
                # stands for the player). A target with no controller draws nothing.
                count = context.value("m_InputValue", default=0)
                for target in _targets(root, index, scope):
                    owner = context.target_owner(int(target), default=None)
                    if owner is not None:
                        context.draw(count, owner=owner)
            else:
                for target in _targets(root, index, scope):
                    _LEAF[kind](context, int(target))
    finally:
        context.effect_template_override = previous_template


# The live leaf each per-target effect calls, with its target passed explicitly.
_LEAF = {
    "DiscardCardAbilityEffectTemplate": lambda context, target: context.discard(target=target),
    "DestroyCardAbilityEffectTemplate": lambda context, target: context.destroy(target=target),
    "VoidCardAbilityEffectTemplate": lambda context, target: context.void_card(target=target),
    "BuryCardAbilityEffectTemplate": lambda context, target: context.bury(target=target),
    "PutTopOfDeckIntoHandAbilityEffectTemplate": lambda context, target: context.put_top_into_hand(target=target),
}
