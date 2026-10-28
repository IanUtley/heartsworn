"""Gap area K of the interpreter (rules_port/ir_exec.py): transform, token copy and tunnel effects.

Each effect runs the live leaf the native resolver runs, once per target the interpreter picked. The leaves read
their target from the battle state (``resolving_target_uid``), which the resolver sets for each target, so each
target is bridged there for one call and restored after it. The effect's own template is read through
``effect_template_override``, as the move effects do. Effects this module does not run are refused by coverage.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterator

ZERO_GUID = "00000000-0000-0000-0000-000000000000"

# The effect templates this module runs (their names as ir_exec reads them).
EFFECTS = {
    "TunnelAbilityEffectTemplate",
    "TransformCardAbilityEffectTemplate",
    "TransformCardAtRandomAbilityEffectTemplate",
    "CreateTokenCopyAbilityEffectTemplate",
    "TransformSelfAbilityEffectTemplate",
    "ConscriptAbilityEffectTemplate",
}

# The effects whose target is the player (their champion stands for them). Conscript and a transform ("You transform
# into X") take a player target as their own meaning: the champion is the card transformed, as C# applies the effect to
# the player's champion card. For every other effect here a player target has no card to act on, so it is refused.
PLAYER_TARGET_EFFECTS = {"ConscriptAbilityEffectTemplate", "TransformCardAbilityEffectTemplate"}

# The typed amount field an effect reads, when it is not m_InputValue.
AMOUNT_FIELDS = {"ConscriptAbilityEffectTemplate": "m_Amount"}


def tac_data(serialized: Any) -> str:
    """The base64 TAC of a serialized TAC (a dict with a ``data`` field, or the string itself)."""
    if isinstance(serialized, dict):
        return str(serialized.get("data") or "")
    return str(serialized or "")


def stored_copy(kind: str, effect: dict[str, Any]) -> bool:
    """Whether a transform is the authored copy of a stored target (its TAC's UseStoredTarget flag)."""
    from rules_port.tac import tac_int
    return kind == "TransformCardAbilityEffectTemplate" and bool(tac_int(tac_data(effect.get("m_SerializedTAC")),
                                                                         "UseStoredTarget", 0))


# The TAC flags a stored-copy transform may carry: the copy's source is stored, and CopyGems copies the source's gem.
# The TAC flags each form of transform may carry. A stored copy (UseStoredTarget) copies the stored card's gem with
# CopyGems. An ordinary transform may be irreversible: it records its original template on the card.
STORED_COPY_FLAGS = ("UseStoredTarget", "CopyGems")
TRANSFORM_FLAGS = ("Irreversible",)


def _only_flags(data: str, names: tuple[str, ...]) -> bool:
    """Whether a serialized TAC carries nothing but the named flags."""
    from rules_port.tac import _tac_attr_hash, decode_tac_tree
    allowed = {_tac_attr_hash(name) for name in names}
    return set(decode_tac_tree(data)) <= allowed


def refusals(kind: str, effect: dict[str, Any], target_kind: str) -> list[str]:
    """The reasons an effect of this area cannot run in the interpreter (empty when it can). A serialized TAC is
    refused unless it carries only the flags this form runs: a stored copy (UseStoredTarget, CopyGems at most), or an
    ordinary transform that is irreversible. An effect whose target is a player (not a card) is refused unless it is
    one that takes the champion as its card (PLAYER_TARGET_EFFECTS)."""
    missing: list[str] = []
    data = tac_data(effect.get("m_SerializedTAC"))
    copy_form = stored_copy(kind, effect)
    if kind != "TransformCardAbilityEffectTemplate":
        allowed: tuple[str, ...] = ()
    else:
        allowed = STORED_COPY_FLAGS if copy_form else TRANSFORM_FLAGS
    if data and not (allowed and _only_flags(data, allowed)):
        missing.append(f"{kind} with a serialized TAC")
    if target_kind == "PlayerTargetTemplate" and kind not in PLAYER_TARGET_EFFECTS:
        missing.append(f"{kind} of a player")
    if kind == "TransformCardAbilityEffectTemplate" and not effect.get("m_Portal") and not copy_form:
        template = str((effect.get("m_CardTemplateId") or {}).get("m_Guid") or ZERO_GUID)
        if template.lower() == ZERO_GUID:
            missing.append("transform to no template")
    return missing


def run(kind: str, effect: dict[str, Any], targets: list[int], context: Any, *, count: int | None = None) -> None:
    """Run one effect on each target, in order, through the live leaf for its kind."""
    runner = _RUNNERS[kind]
    previous = context.effect_template_override
    context.effect_template_override = effect
    try:
        for target in targets:
            with _resolving_target(context.bstate, int(target)):
                runner(context, int(target), count)
    finally:
        context.effect_template_override = previous


@contextmanager
def _resolving_target(bstate: dict[str, Any], target: int) -> Iterator[None]:
    """Name ``target`` as the resolving target for one leaf call, then restore what was there."""
    saved = bstate.get("resolving_target_uid")
    bstate["resolving_target_uid"] = target
    try:
        yield
    finally:
        if saved is None:
            bstate.pop("resolving_target_uid", None)
        else:
            bstate["resolving_target_uid"] = saved


def _tunnel(context: Any, target: int, count: int | None) -> None:
    # C# TunnelAbilityEffectTemplate: the target goes underground through the live zone move.
    context.tunnel()


def _transform(context: Any, target: int, count: int | None) -> None:
    # C# TransformCardAbilityEffectTemplate: the target becomes the authored card (or a portal of its rarity). A stored
    # copy (UseStoredTarget) makes the stored card a copy of the target, through the leaf the native resolver runs.
    import game_engine
    from rules_port.tac import tac_int
    from rules_port.transform_effects import (irreversible_original, transform_instance,
                                              transform_into_stored_copy)
    serialized = tac_data(context.template_value("m_SerializedTAC", None))
    if tac_int(serialized, "UseStoredTarget", 0):
        transform_into_stored_copy(context, serialized)
        return
    if context.template_value("m_Portal", False):
        new_template = context._portal_template_for(target)
        if not new_template:
            return
    else:
        new_template = str(context.template_value("m_CardTemplateId", "")).lower()
    transform_instance(context, target, new_template, keep_zone=True,
                       event_gems=game_engine.EGemTypesNew.Unknown,
                       original_template_guid=irreversible_original(
                           serialized, context.template_value("m_CardTemplateId", "")))


def _transform_at_random(context: Any, target: int, count: int | None) -> None:
    # C# TransformCardAtRandomAbilityEffectTemplate: the target becomes a random card the authored filter passes.
    from rules_port.transform_effects import transform_card_at_random
    transform_card_at_random(context)


def _transform_self(context: Any, target: int, count: int | None) -> None:
    # C# TransformSelfAbilityEffectTemplate: the source becomes a copy of the target (a replica when authored), or a
    # garden plant at random. The leaf reads the source and the target from the battle state (both bridged).
    context.transform_self()


def _conscript(context: Any, target: int, count: int | None) -> None:
    # C# ConscriptAbilityEffectTemplate: cards from the deck for the player (the target is the player's champion).
    # The leaf reads its amount from the bridged ability variables, and the owner from the target.
    context.conscript()


def _copy_token(context: Any, target: int, count: int | None) -> None:
    # C# CreateTokenCopyAbilityEffectTemplate: copies of the target, as many as the effect's count.
    from rules_port.token_effects import create_token_copy
    create_token_copy(context, count=count)


_RUNNERS: dict[str, Callable[[Any, int, int | None], None]] = {
    "TunnelAbilityEffectTemplate": _tunnel,
    "TransformCardAbilityEffectTemplate": _transform,
    "TransformCardAtRandomAbilityEffectTemplate": _transform_at_random,
    "CreateTokenCopyAbilityEffectTemplate": _copy_token,
    "TransformSelfAbilityEffectTemplate": _transform_self,
    "ConscriptAbilityEffectTemplate": _conscript,
}
