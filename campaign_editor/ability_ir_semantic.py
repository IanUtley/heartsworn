"""Ability IR, semantic form: an ability as the sentences it reads as.

This is the form the editor works on. It is the structure the sentence DSL parses to
(``ability_dsl.parse``), so it reads as the ability does:

    {"trigger": "when this enters play",
     "settings": [...], "requires": "",
     "clauses": [{"shape": "damage", "target": "target opposing troop", "amount": "2",
                  "duration": "", "condition": "", "count": "", "contingent": False,
                  "layer": 0, "recalculate": "", "new_target": False}]}

``render`` turns it into sentence text, and ``parse`` turns text back into it, so the
form and the sentences are two views of one thing. ``compile_text`` turns the text into
Records. Abilities the DSL cannot express have no semantic form; ``read`` says so, and
they stay in the mirror layers (``ability_ir_root``).

Editing rules: a value must come from the DSL's own vocabulary (the trigger catalogue,
the clause shapes, the durations), so ``check`` reports anything else before a save.
"""

from __future__ import annotations

from typing import Any

import ability_dsl as dsl

DERIVED_FIELDS = ("event_type",)
CLAUSE_FIELDS = ("shape", "target", "amount", "count", "duration", "condition", "contingent",
                 "layer", "recalculate", "new_target")


def read(guid: str) -> dict[str, Any]:
    """The semantic form of a stored ability, or the reasons it has none."""
    found = dsl.decompile(guid)
    if not found.get("supported") or not found.get("exact"):
        return {"guid": found["guid"], "semantic": False, "text": found.get("text", ""),
                "reasons": found.get("reasons", [])}
    ir = normalise(found["parsed"])
    return {"guid": found["guid"], "semantic": True, "text": text(ir), "ir": ir, "reasons": []}


def normalise(parsed: dict[str, Any]) -> dict[str, Any]:
    """The semantic form with every clause field present, in a fixed order."""
    clauses = []
    for clause in parsed.get("clauses") or []:
        # Every field the parser gives (a clause also carries its shape's own fields, such
        # as "filter" or "card"), with the usual defaults filled in where one is missing.
        defaults = {key: (False if key in ("contingent", "new_target") else "") for key in CLAUSE_FIELDS}
        # Fields the sentences do not carry: GUIDs are resolved from names when they compile,
        # and an event type follows from the shape, so neither is part of the form.
        fields = {key: value for key, value in clause.items()
                  if not key.endswith("_guid") and key not in DERIVED_FIELDS and value is not None}
        clauses.append({**defaults, **fields})
    return {"named_targets": {name: dict(props) for name, props in (parsed.get("named_targets") or {}).items()},
            "trigger": parsed.get("trigger") or "",
            "settings": [str(item).lower() for item in parsed.get("settings") or []],
            "requires": parsed.get("requires") or "", "clauses": clauses}


def text(ir: dict[str, Any]) -> str:
    """Sentence text for a semantic form."""
    return dsl.render(ir)


def check(ir: dict[str, Any]) -> list[str]:
    """What the DSL would refuse in this form: unknown triggers, shapes or durations, and
    text that does not read back to the same form."""
    problems: list[str] = []
    trigger = ir.get("trigger") or ""
    if trigger and dsl._trigger_named(trigger) == "":
        problems.append(f"no trigger {trigger!r} in Records")
    for index, clause in enumerate(ir.get("clauses") or []):
        if clause.get("shape") not in dsl.SHAPE_BY_ID:
            problems.append(f"clause {index + 1}: no shape {clause.get('shape')!r}")
            continue
        if not clause.get("target"):
            problems.append(f"clause {index + 1}: no target")
    if not problems:
        again = normalise(dsl.parse(text(ir)))
        if again != normalise(ir):
            problems.append("the sentences do not read back to the same form")
    return problems


def save(ir: dict[str, Any], guid: str = "") -> dict[str, Any]:
    """Save a semantic form through the DSL's own save path (checks, lock, atomic write)."""
    problems = check(ir)
    if problems:
        raise dsl.Unsupported(problems)
    return dsl.save_text(text(ir), guid)


def from_text(text_in: str) -> dict[str, Any]:
    """The semantic form for sentence text, with what the DSL would refuse in it."""
    try:
        ir = normalise(dsl.parse(text_in))
    except ValueError as error:
        return {"error": str(error)}
    return {"ir": ir, "problems": check(ir)}


def vocab() -> dict[str, Any]:
    """The choices the form offers: triggers, clause shapes and durations."""
    cat = dsl.catalogue()
    return {
        "triggers": sorted(cat["triggers"].by_phrase),
        "shapes": [{"id": shape["id"], "phrase": shape.get("phrase", shape["id"])}
                   for shape in dsl.SHAPES],
        "durations": [{"value": value, "label": label} for value, label in dsl.DURATIONS.items()],
    }
