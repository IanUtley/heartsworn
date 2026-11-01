"""What an ability does, in words, from its Records-shaped records: its trigger, each effect with
its duration, its target's card filter, its modifier and the value it reads, the ability a grant
effect gives, and the ability's variables with where each is used.

The field editor shows the raw fields; this shows the implementation the fields describe. It is
read-only and does not change any record.
"""

from __future__ import annotations

from typing import Any

import card_abilities as abilities

NS_SUFFIXES = ("EffectTemplate", "AbilityTemplate", "Modifier", "Filter", "Event")
MOVE_SUFFIX = "ToZone"


def _short(value: Any) -> str:
    return str(value or "").rsplit(".", 1)[-1]


def _name(record: dict[str, Any] | None, fallback: str) -> str:
    return str((record or {}).get("m_Name") or fallback)


class _Context:
    """The variables an ability declares (name -> default) and the ones its effects read."""

    def __init__(self, variables: list[dict[str, Any]]) -> None:
        self.defaults = {str(v.get("m_Name")): v.get("m_DefaultValue")
                         for v in variables or [] if isinstance(v, dict) and v.get("m_Name")}
        self.read: set[str] = set()

    def value(self, value: Any) -> str:
        if isinstance(value, dict) and _short(value.get("_t")) == "EffectInputVariable":
            name = str(value.get("m_InputVariableName") or "")
            self.read.add(name)
            default = self.defaults.get(name)
            return f"{name} ({default})" if default is not None else name
        if value is None or value == "":
            return "-"
        return str(value)


def describe_filter(spec: Any) -> str:
    """A card filter in words: ``not a resource; any card type; controlled by you; in your deck``."""
    if not isinstance(spec, dict):
        return "any card"
    kind = _short(spec.get("_t"))
    if kind == "AndCardFilter":
        return "; ".join(describe_filter(f) for f in spec.get("m_TargetFilters") or []) or "any card"
    if kind == "OrCardFilter":
        return " or ".join(describe_filter(f) for f in spec.get("m_TargetFilters") or []) or "any card"
    if kind == "NotCardFilter":
        return "not (" + describe_filter(spec.get("m_TargetFilter")) + ")"
    if kind == "IsResource":
        return "a resource"
    if kind == "IsType":
        return "type " + " / ".join(part for part in str(spec.get("m_CardType") or "").split("|") if part)
    if kind == "IsControlledBy":
        return "controlled by you"
    if kind == "IsNotControlledBy":
        return "not controlled by you"
    if kind == "InZone":
        return "in " + " / ".join(str(spec.get("m_Collection") or "").split("|"))
    if kind == "IsSubType":
        return f"subtype {spec.get('m_SubType')}"
    if kind == "IsCardName":
        return f"named {spec.get('m_CardName')}"
    return kind.removeprefix("Is").lower() or kind


def describe_target(target: dict[str, Any] | None) -> str:
    if not target:
        return "no target"
    if not target.get("m_CardFilter"):
        # A player target (you, or your opponent): it names players, not cards.
        return f"players: {target.get('m_PlayerFilter') or 'unspecified'}"
    parts = [describe_filter(target.get("m_CardFilter"))]
    zones = str(target.get("m_CollectionFlags") or "")
    if zones and len(zones.split("|")) < 9:
        parts.append("in " + " / ".join(zones.split("|")))
    parts.append("auto target (the engine chooses)" if target.get("m_IsAutoTarget") else "chosen when played")
    return "; ".join(parts)


def describe_modifier(modifier: dict[str, Any], ctx: _Context) -> str:
    kind = _short(modifier.get("_t"))
    if kind == "CardCostModifier":
        mode = "sets cost to" if modifier.get("m_ReplaceExistingValue") else "changes cost by"
        return f"{mode} {ctx.value(modifier.get('m_InputValue'))}"
    if kind == "IntAttrModifier":
        amount = modifier.get("m_ValueField") if modifier.get("m_ValueField") is not None else modifier.get("m_Value")
        return f"{str(modifier.get('m_Operation') or '').lower()} {ctx.value(amount)} to {modifier.get('m_Attribute')}"
    detail = modifier.get("m_InputValue") if modifier.get("m_InputValue") is not None else modifier.get("m_Value")
    return f"{kind.removesuffix('Modifier')}{' ' + ctx.value(detail) if detail is not None else ''}"


def describe_effect(effect: dict[str, Any], ctx: _Context) -> str:
    kind = _short(effect.get("_t"))
    if kind == "CardModifierAbilityEffectTemplate":
        return describe_modifier(effect.get("m_Modifier") or {}, ctx)
    if kind == "GrantAbilityEffectTemplate":
        granted = str((effect.get("m_GrantedAbilityTemplateId") or {}).get("m_Guid") or "")
        return f"gives the ability {granted}" if granted else "gives an ability"
    if kind == "MoveCardToZoneEffectTemplate":
        return f"moves the card to {effect.get('m_DestinationCollection') or 'another zone'}"
    if kind == "DrawNCardsAbilityEffectTemplate":
        return f"draws {ctx.value(effect.get('m_InputValue'))} cards"
    label = kind
    for suffix in NS_SUFFIXES:
        label = label.removesuffix(suffix)
    return label or kind


def _granted(effect: dict[str, Any]) -> dict[str, Any] | None:
    guid = str((effect.get("m_GrantedAbilityTemplateId") or {}).get("m_Guid") or "").lower()
    if not guid:
        return None
    record, source = abilities.lookup("AbilityTemplate", guid)
    if not record:
        return {"guid": guid, "text": "(not found)", "effects": [], "source": "missing"}
    local = _Context(record.get("m_Variables") or [])
    effects = []
    for entry in record.get("m_AbilityEffectList") or []:
        found, _ = abilities.lookup("AbilityEffectTemplate",
                                    str((entry.get("m_EffectTemplateId") or {}).get("m_Guid") or "").lower())
        effects.append(describe_effect(found or {}, local))
    return {"guid": guid, "name": _name(record, guid), "text": str(record.get("m_GameText") or ""),
            "effects": effects, "source": source}


def summary(root: dict[str, Any]) -> dict[str, Any]:
    """The implementation of one root ability (``root`` is its record)."""
    ctx = _Context(root.get("m_Variables") or [])
    trigger = root.get("m_TriggerEventType") or {}
    condition = root.get("m_TriggerCondition") or {}
    trigger_text = _short(trigger.get("m_InternalType")) if isinstance(trigger, dict) else ""
    if isinstance(condition, dict) and condition:
        extra = [f"{k.removeprefix('m_')}: {v}" for k, v in condition.items()
                 if k not in ("_t", "m_SerializedTAC", "m_TriggerTest") and v not in (None, "")]
        trigger_text = f"{trigger_text or 'no trigger'} ({_short(condition.get('_t'))}{'; ' + ', '.join(extra) if extra else ''})"
    effects = []
    targets = root.get("m_AbilityTargetTemplateIds") or []
    for index, entry in enumerate(root.get("m_AbilityEffectList") or []):
        effect_guid = str((entry.get("m_EffectTemplateId") or {}).get("m_Guid") or "").lower()
        effect, source = abilities.lookup("AbilityEffectTemplate", effect_guid)
        position = entry.get("m_TargetTemplateIndex", 0)
        target_guid = str((targets[position] if isinstance(position, int) and 0 <= position < len(targets) else {}).get("m_Guid") or "").lower()
        target, _ = abilities.lookup("AbilityTargetTemplate", target_guid) if target_guid else (None, "")
        ctx.read.clear()
        text = describe_effect(effect or {}, ctx)
        effects.append({"index": index, "duration": entry.get("m_EffectDuration") or "Instant",
                        "contingent": entry.get("m_ContingentEffectInstanceId", -1),
                        "effect_guid": effect_guid, "effect_source": source or "missing",
                        "text": text, "reads": sorted(ctx.read),
                        "target_guid": target_guid, "target": describe_target(target),
                        "granted": _granted(effect or {})})
    variables = []
    for variable in root.get("m_Variables") or []:
        if not isinstance(variable, dict):
            continue
        name = str(variable.get("m_Name") or "")
        variables.append({"name": name, "default": variable.get("m_DefaultValue"),
                          "kind": _short(variable.get("_t")),
                          "used_by": [e["index"] for e in effects if name in e["reads"]]})
    return {"trigger": trigger_text or "no trigger", "effects": effects, "variables": variables}


def _input_refs(value: Any, field: str = "") -> list[tuple[str, str]]:
    """Every EffectInputVariable inside ``value``: ``(field it sits in, variable name)``."""
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        if _short(value.get("_t")) == "EffectInputVariable" and value.get("m_InputVariableName"):
            found.append((field, str(value["m_InputVariableName"])))
        for key, item in value.items():
            found.extend(_input_refs(item, str(key).removeprefix("m_")))
    elif isinstance(value, list):
        for item in value:
            found.extend(_input_refs(item, field))
    return found


def graph(root: dict[str, Any]) -> dict[str, Any]:
    """The ability as a directed graph of its records: nodes (ability, effect, target, modifier,
    variable, granted ability) and edges (step, target, does, grants, declares, and reads with the
    field that holds the variable). A record used more than once is drawn once, with an arrow from
    each user. Read-only.
    """
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []
    seen: set[str] = set()

    def node(key: str, kind: str, label: str, **extra: Any) -> str:
        nodes.setdefault(key, {"id": key, "kind": kind, "label": label, **extra})
        return key

    def link(source: str, target: str, label: str) -> None:
        edge = {"from": source, "to": target, "label": label}
        if edge not in edges:
            edges.append(edge)

    def variables_of(record: dict[str, Any], scope: str) -> None:
        for variable in record.get("m_Variables") or []:
            if isinstance(variable, dict) and variable.get("m_Name"):
                name = str(variable["m_Name"])
                node(f"{scope}var:{name}", "variable", f"{name} = {variable.get('m_DefaultValue')}",
                     declared=True)

    def read(source: str, scope: str, value: Any) -> None:
        for field, name in _input_refs(value):
            key = f"{scope}var:{name}"
            if key not in nodes:
                node(key, "variable", f"{name} (not declared here)", declared=False)
            link(source, key, field or "reads")

    def effects_of(record: dict[str, Any], owner: str, scope: str) -> None:
        ctx = _Context(record.get("m_Variables") or [])
        targets = record.get("m_AbilityTargetTemplateIds") or []
        for index, entry in enumerate(record.get("m_AbilityEffectList") or []):
            effect_guid = str((entry.get("m_EffectTemplateId") or {}).get("m_Guid") or "").lower()
            effect, source = abilities.lookup("AbilityEffectTemplate", effect_guid)
            ctx.read.clear()
            text = describe_effect(effect or {}, ctx)
            key = node(scope + "effect:" + effect_guid + f":{index}", "effect", f"{index + 1}. {text}",
                       guid=effect_guid, duration=entry.get("m_EffectDuration") or "Instant",
                       source=source or "missing")
            link(owner, key, f"step {index + 1}")
            read(key, scope, effect or {})
            position = entry.get("m_TargetTemplateIndex", 0)
            if isinstance(position, int) and 0 <= position < len(targets):
                tguid = str((targets[position] or {}).get("m_Guid") or "").lower()
                target, _ = abilities.lookup("AbilityTargetTemplate", tguid)
                tkey = node("target:" + tguid, "target", describe_target(target), guid=tguid)
                link(key, tkey, "target")
                read(tkey, scope, target or {})
            if isinstance(effect, dict) and isinstance(effect.get("m_Modifier"), dict):
                mkey = node(scope + "mod:" + effect_guid + f":{index}", "modifier",
                            describe_modifier(effect["m_Modifier"], ctx))
                link(key, mkey, "does")
                read(mkey, scope, effect["m_Modifier"])
            granted = _granted_guid(effect or {})
            if granted:
                record_g, _ = abilities.lookup("AbilityTemplate", granted)
                gkey = node("ability:" + granted, "granted", str((record_g or {}).get("m_GameText") or granted),
                            guid=granted)
                link(key, gkey, "grants")
                if record_g and granted not in seen:
                    seen.add(granted)
                    gscope = "ability:" + granted + "/"
                    variables_of(record_g, gscope)
                    effects_of(record_g, gkey, gscope)

    root_guid = str((root.get("m_AbilityTemplateId") or {}).get("m_Guid") or "root")
    root_key = node("ability:" + root_guid, "ability", _name(root, "ability"), guid=root_guid)
    seen.add(root_guid)
    scope = root_key + "/"
    variables_of(root, scope)
    effects_of(root, root_key, scope)
    return {"root": root_key, "nodes": list(nodes.values()), "edges": edges}


def _granted_guid(effect: dict[str, Any]) -> str:
    return str((effect.get("m_GrantedAbilityTemplateId") or {}).get("m_Guid") or "").lower()
