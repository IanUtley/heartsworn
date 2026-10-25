"""Ability IR, child links: an ActivateAbility clause as a typed link to its child ability.

An ``ActivateAbilityEffectTemplate`` clause runs another ability (the child) as a new
instance. In Records the clause carries only the child's GUID. The IR gives that edge a
type: the parent clause it sits in, the child root, the parent target it is on, and the
bindings the sentences declare ("child input = parent input", written ``C1I1 = I1``).

A link keeps the stored clause, so it rebuilds the exact Records entry. Bindings are not
in Records, so a link that declares one is only valid where the client's own rules give the
same result. ``check`` reports those cases; ``campaign_editor/ability_inline.py`` is the
compile step that realises a checked binding by inlining the child.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import ability_inline as inl

Lookup = Callable[[str], "dict[str, Any] | None"]


@dataclass(frozen=True)
class Binding:
    """The child's input takes the parent's value: ``child`` is the child's variable name,
    ``parent`` is the parent's (``C1I1 = I1``)."""

    child: str
    parent: str


_BINDING = re.compile(r"^\s*(\w+)\s*=\s*\$(\w+)\s*$")


def binding_text(binding: Binding) -> str:
    """The sentence form: ``C1I1 = $I1``. The ``$`` marks the parent's input, as in ABILITY_IR.md."""
    return f"{binding.child} = ${binding.parent}"


def parse_binding(text: str) -> Binding:
    """The binding a sentence form states. ``ValueError`` when it is not of that form."""
    found = _BINDING.match(text or "")
    if not found:
        raise ValueError(f"not a binding: {text!r} (write it as child = $parent)")
    return Binding(child=found.group(1), parent=found.group(2))


@dataclass
class ChildLink:
    """One ActivateAbility clause: where it sits, what it runs, and what it passes down."""

    clause: int                 # position in the parent's m_AbilityEffectList
    child: str                  # the child root's GUID (m_AbilityToInvoke)
    target: int                 # the parent target index the clause is on
    lucky: bool                 # m_RandomlyLuckyOrUnlucky on the effect template
    bindings: tuple[Binding, ...] = ()
    entry: dict[str, Any] = field(default_factory=dict)   # the stored clause, for the round trip


def links_of(root: dict[str, Any], effect_of: Lookup) -> list[ChildLink]:
    """The child links of a root, in clause order."""
    links = []
    for position, entry in enumerate(root.get("m_AbilityEffectList") or []):
        effect = effect_of(inl._guid(entry.get("m_EffectTemplateId"))) or {}
        if inl._effect_type(effect) != inl.ACTIVATE:
            continue
        links.append(ChildLink(clause=position, child=inl._guid(effect.get("m_AbilityToInvoke")),
                               target=int(entry.get("m_TargetTemplateIndex") or 0),
                               lucky=bool(effect.get("m_RandomlyLuckyOrUnlucky")),
                               entry=copy.deepcopy(entry)))
    return links


def to_entry(link: ChildLink) -> dict[str, Any]:
    """The Records clause for a link. Bindings are not written: Records have no place for them."""
    return copy.deepcopy(link.entry)


def check(link: ChildLink, parent: dict[str, Any], effect_of: Lookup, root_of: Lookup,
          target_of: Callable[[str], dict[str, Any] | None],
          condition_of: Callable[[str], dict[str, Any] | None]) -> list[str]:
    """What is wrong with a link's bindings (empty when it is valid). A link with no bindings
    is always valid: it is the client's own ActivateAbility."""
    if not link.bindings:
        return []
    child = root_of(link.child)
    if child is None:
        return [f"clause {link.clause + 1}: no child ability {link.child}"]
    # A binding is realised by inlining, so the child must be one the inliner can take, and the
    # binding's names must exist on both sides (the inliner's binding rules say which is missing).
    effect = effect_of(_effect_guid(link.entry)) or {}
    problems = inl.blockers(link.entry, effect, parent, child, effect_of, target_of, condition_of)
    problems += inl._binding_blockers(_pairs(link), parent, child, effect_of)
    return problems


def _pairs(link: ChildLink) -> tuple[tuple[str, str], ...]:
    return tuple((binding.child, binding.parent) for binding in link.bindings)


def compile_root(root: dict[str, Any], links: list[ChildLink], effect_of: Lookup, root_of: Lookup,
                 target_of: Callable[[str], dict[str, Any] | None],
                 condition_of: Callable[[str], dict[str, Any] | None]) -> tuple[dict[str, Any] | None, list[str]]:
    """The root with each link's bindings compiled by inlining its child, or ``None`` with the
    problems when a bound link cannot be compiled. A link without bindings is left as the
    client's ActivateAbility unless the inliner takes it. A bound link is never left in place
    without its binding, since that would change what the ability does."""
    problems: list[str] = []
    bindings: dict[int, tuple[tuple[str, str], ...]] = {}
    for link in links:
        if not link.bindings:
            continue
        found = check(link, root, effect_of, root_of, target_of, condition_of)
        if found:
            problems += found
        else:
            bindings[link.clause] = _pairs(link)
    if problems:
        return None, problems
    compiled, _ = inl.inline_children(root, effect_of, root_of, target_of, condition_of, bindings)
    return compiled, []


def _effect_guid(entry: dict[str, Any]) -> str:
    return inl._guid(entry.get("m_EffectTemplateId"))
