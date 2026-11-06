"""Inline an ActivateAbility child into its parent root.

An ``ActivateAbilityEffectTemplate`` clause runs another ability (the child) as a new
instance. The client gives that instance only its own template variables, so a value
flows from parent to child only where the sentences say so. Inlining makes the child's
clauses part of the parent: the parent's variables are then the child's, and a
"child input = parent input" link is a plain rename.

``inline_children`` does not write Records. It returns a new root dict with each
eligible ActivateAbility clause replaced by the child's clauses, and a report of the
clauses it left alone and why.

Renumbering: the child's targets are appended after the parent's (its target index k
becomes parent_count + k), and its effect instance ids are shifted past the parent's,
so no two clauses share an id.
"""

from __future__ import annotations

import copy
from typing import Any, Callable

from ability_client_names import INT_ATTR_NAMES, STATIC_IDENTIFIERS

ACTIVATE = "ActivateAbilityEffectTemplate"
NULL_GUID = "00000000-0000-0000-0000-000000000000"
CONSTANT = "AbilityConstant"
INSTANCE_FIELDS = ("m_EffectInstanceId", "m_ContingentEffectInstanceId")
VARIABLE_TEST = "AbilityVariableCondition"
EXPRESSION = "Game.Shared.Mechanics.Abilities.ExpressionAbilityVariable"


def _guid(value: Any) -> str:
    return str((value or {}).get("m_Guid") or "").lower() if isinstance(value, dict) else ""


def _effect_type(effect: dict[str, Any]) -> str:
    return str(effect.get("_t") or "").rsplit(".", 1)[-1]


def _variable_names(root: dict[str, Any], *, constants: bool) -> set[str]:
    return {str(v.get("m_Name")) for v in root.get("m_Variables") or []
            if constants or not str(v.get("_t") or "").endswith(CONSTANT)}


def _writes(value: Any, out: set[str]) -> set[str]:
    """The variable names a stored effect writes: set-variable effects name theirs in
    ``m_VariableName``; a copy effect writes its destination's input name."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "m_VariableName" and isinstance(item, str):
                out.add(item)
            if key == "m_Destination" and isinstance(item, dict) and isinstance(item.get("m_InputVariableName"), str):
                out.add(item["m_InputVariableName"])
            _writes(item, out)
    elif isinstance(value, list):
        for item in value:
            _writes(item, out)
    return out


def _condition_blockers(condition: str, child: dict[str, Any], effect_of: Callable[[str], dict[str, Any]],
                        condition_of: Callable[[str], dict[str, Any] | None]) -> list[str]:
    """Why a condition on the invoking clause cannot be copied onto the child's clauses. The
    client checks it once, when the child starts. Copied, each clause checks it when it runs,
    so the copy is only the same when the condition is a variable test and no child clause
    changes the variable it tests."""
    inner = (condition_of(condition) or {}).get("m_Condition") or {}
    if not str(inner.get("_t") or "").endswith(VARIABLE_TEST):
        return ["invoking condition is not a variable test"]
    if any(_guid(e.get("m_ConditionId")) not in ("", NULL_GUID) for e in child.get("m_AbilityEffectList") or []):
        return ["child clauses have their own conditions"]
    child_writes: set[str] = set()
    for entry in child.get("m_AbilityEffectList") or []:
        child_writes |= _writes(effect_of(_guid(entry.get("m_EffectTemplateId"))), set())
    if child_writes & {inner.get("m_Lhs"), inner.get("m_Rhs")}:
        return ["child changes the variable its condition tests"]
    return []


def blockers(entry: dict[str, Any], effect: dict[str, Any], parent: dict[str, Any], child: dict[str, Any],
             effect_of: Callable[[str], dict[str, Any]],
             target_of: Callable[[str], dict[str, Any] | None],
             condition_of: Callable[[str], dict[str, Any] | None]) -> list[str]:
    """Reasons this ActivateAbility clause cannot be inlined (empty when it can)."""
    reasons = []
    if effect.get("m_RandomlyLuckyOrUnlucky"):
        reasons.append("random lucky or unlucky child")
    condition = _guid(entry.get("m_ConditionId"))
    if condition not in ("", NULL_GUID):
        reasons += _condition_blockers(condition, child, effect_of, condition_of)
    if (child.get("m_TriggerEventType") or {}).get("m_InternalType") or child.get("m_TriggerCondition"):
        reasons.append("child has its own trigger")
    if child.get("m_AbilityCondition"):
        reasons.append("child has an ability condition")
    if any(_effect_type(effect_of(_guid(entry.get("m_EffectTemplateId")))) == ACTIVATE
           for entry in child.get("m_AbilityEffectList") or []):
        reasons.append("child invokes another ability")
    # A chosen target is picked when the child is reached, after the parent's earlier effects.
    # Inlined, it would be picked when the parent starts, which changes the order of play.
    if any(not (target_of(_guid(t)) or {}).get("m_IsAutoTarget") for t in child.get("m_AbilityTargetTemplateIds") or []):
        reasons.append("child has a chosen target")
    if _variable_names(parent, constants=False) & _variable_names(child, constants=False):
        reasons.append("parent and child share a variable name")
    if bool(parent.get("m_IgnoresChain")) != bool(child.get("m_IgnoresChain")):
        reasons.append("chain flags differ")
    return reasons


def _renumber(entry: dict[str, Any], target_base: int, instance_base: int,
              condition: Any = None) -> dict[str, Any]:
    entry = dict(entry)
    index = int(entry.get("m_TargetTemplateIndex") or 0)
    entry["m_TargetTemplateIndex"] = index + target_base
    if condition is not None:
        # The invoking clause's condition, now checked on each inlined clause (see _condition_blockers).
        entry["m_ConditionId"] = dict(condition)
    # A child always regenerates its auto targets when it starts (its map is empty). Inlined,
    # the target has a map, so each clause is marked to regenerate before it runs, as the child did.
    entry["m_RecalculateTargets"] = "True"
    for field in INSTANCE_FIELDS:
        if isinstance(entry.get(field), int) and entry[field] >= 0:
            entry[field] += instance_base
    return entry


def _number_instances(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clause i gets instance id i, and every contingency points at the instance it names.
    Instance ids were already unique across parent and child, so the mapping is one-to-one."""
    position = {int(e.get("m_EffectInstanceId") or 0): i for i, e in enumerate(entries)}
    out = []
    for i, entry in enumerate(entries):
        entry = dict(entry)
        contingent = int(entry.get("m_ContingentEffectInstanceId", -1))
        entry["m_ContingentEffectInstanceId"] = position.get(contingent, -1) if contingent >= 0 else -1
        entry["m_EffectInstanceId"] = i
        out.append(entry)
    return out


def _binding_blockers(bindings: tuple[tuple[str, str], ...], parent: dict[str, Any], child: dict[str, Any],
                      effect_of: Callable[[str], dict[str, Any]]) -> list[str]:
    """Why a binding (child input, parent input) cannot be compiled. The child's input becomes a
    read-through alias of the parent's, so the child must only read it. The client accepts a write
    to an expression variable, but with DontRecalculate 0 the next read recalculates and drops it."""
    reasons = []
    child_inputs = {str(v.get("m_Name")) for v in child.get("m_Variables") or []}
    parent_inputs = {str(v.get("m_Name")) for v in parent.get("m_Variables") or []}
    writes: set[str] = set()
    for entry in child.get("m_AbilityEffectList") or []:
        writes |= _writes(effect_of(_guid(entry.get("m_EffectTemplateId"))), set())
    for child_name, parent_name in bindings:
        if child_name not in child_inputs:
            reasons.append(f"binding {child_name} = ${parent_name}: the child has no input {child_name}")
        if parent_name not in parent_inputs:
            reasons.append(f"binding {child_name} = ${parent_name}: the parent has no input {parent_name}")
        if child_name in writes:
            reasons.append(f"child changes {child_name}, which a binding can only read")
        # The client reads these names as attributes or static identifiers, not as the variable.
        reasons.extend(f"binding name {name} is read by the client as an attribute or static identifier" for name in (child_name, parent_name) if name in INT_ATTR_NAMES or name.upper() in STATIC_IDENTIFIERS)
    return reasons


def _alias(name: str, parent_name: str) -> dict[str, Any]:
    """The parent-side variable that reads ``parent_name`` on every use (``C1I1 = $I1``)."""
    return {"_t": EXPRESSION, "m_Name": name, "m_DefaultValue": 0, "m_RequiresExplicitSet": 0,
            "m_RequiresPlayerInput": 0, "m_ExpressionText": parent_name, "m_DontRecalculate": 0,
            "m_Calculated": 0}


def inline_children(root: dict[str, Any], effect_of: Callable[[str], dict[str, Any]],
                    root_of: Callable[[str], dict[str, Any] | None],
                    target_of: Callable[[str], dict[str, Any] | None],
                    condition_of: Callable[[str], dict[str, Any] | None],
                    bindings: dict[int, tuple[tuple[str, str], ...]] | None = None) -> tuple[dict[str, Any], list[str]]:
    """The root with every eligible ActivateAbility clause inlined, and the reasons for the rest.

    ``bindings`` maps a clause position to its (child input, parent input) pairs. Those clauses
    are inlined only when the binding can be compiled (see ``_binding_blockers``)."""
    bindings = bindings or {}
    parent = copy.deepcopy(root)
    targets = list(parent.get("m_AbilityTargetTemplateIds") or [])
    variables = list(parent.get("m_Variables") or [])
    instance_base = 1 + max([int(e.get("m_EffectInstanceId") or 0) for e in parent.get("m_AbilityEffectList") or []] or [0])
    entries: list[dict[str, Any]] = []
    report: list[str] = []
    for position, entry in enumerate(parent.get("m_AbilityEffectList") or []):
        effect = effect_of(_guid(entry.get("m_EffectTemplateId")))
        child = root_of(_guid(effect.get("m_AbilityToInvoke"))) if _effect_type(effect) == ACTIVATE else None
        if child is None:
            entries.append(entry)
            continue
        reasons = blockers(entry, effect, root, child, effect_of, target_of, condition_of)
        clause_bindings = bindings.get(position, ())
        reasons += _binding_blockers(clause_bindings, root, child, effect_of)
        if reasons:
            report.extend(reasons)
            entries.append(entry)
            continue
        target_base = len(targets)
        targets.extend(child.get("m_AbilityTargetTemplateIds") or [])
        bound = dict(clause_bindings)
        for child_var in child.get("m_Variables") or []:
            name = str(child_var.get("m_Name"))
            if name in bound:
                variables.append(_alias(name, bound[name]))
            elif not any(v.get("m_Name") == child_var.get("m_Name") for v in variables):
                variables.append(copy.deepcopy(child_var))
        condition = entry.get("m_ConditionId") if _guid(entry.get("m_ConditionId")) not in ("", NULL_GUID) else None
        entries.extend((_renumber(child_entry, target_base, instance_base, condition)) for child_entry in (child.get("m_AbilityEffectList") or []))
        instance_base += 1 + max([int(e.get("m_EffectInstanceId") or 0) for e in child.get("m_AbilityEffectList") or []] or [0])
    parent["m_AbilityTargetTemplateIds"] = targets
    parent["m_Variables"] = variables
    parent["m_AbilityEffectList"] = _number_instances(entries)
    return parent, report
