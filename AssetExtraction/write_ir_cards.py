"""Write ability card files (ir_cards/*.py) from the stored Records.

    python3 AssetExtraction/write_ir_cards.py <ability GUID> [<ability GUID> ...]

Each card file holds the ability's root as a constructor call of the generated mechanics classes, and
each effect template the root names as a named fragment (E0, E1, ...). The text is read back by
gamedata/ir_python.py, and the card is checked against the stored records by
tests/ir_equivalence.py (run it with IR_EQ_CARDS=1).
"""

import ast
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from gamedata import DEFAULT_RECORD_STORE as STORE  # noqa: E402
from gamedata import ir_python as P  # noqa: E402
from gamedata import mechanics_types as T  # noqa: E402


def _plain(value):
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _record(store_class, guid):
    for record in STORE.load(store_class):
        if record.guid.lower() == guid:
            return _plain(record.to_dict(include_metadata=True))
    raise KeyError(f"no {store_class} {guid}")


def _class(record):
    name = str(record.get("_t") or "").rsplit(".", 1)[-1]
    cls = getattr(T, name, None)
    if cls is None:
        raise ValueError(f"no generated class for {record.get('_t')}")
    return cls


CLAUSE_DEFAULTS = {
    "m_TargetTemplateIndex": 0, "m_EffectInstanceId": 0, "m_EffectDuration": "Instant",
    "m_OutputVariables": {}, "m_ContingentEffectInstanceId": -1, "m_IsOptional": 0,
    "m_ConditionId": {"m_Guid": "00000000-0000-0000-0000-000000000000"}, "m_EffectGroupId": 1,
    "m_VfxProjectileType": "None", "m_VfxEventType": "None",
    "m_VfxTargetSelectionType": "Targetable", "m_RecalculateTargets": "True", "m_Layer": 0,
    "m_SecondaryTargetIndex": -1,
}


# Words that carry no meaning in a name.
_NAME_STOPWORDS = {
    "a", "an", "the", "of", "to", "this", "that", "from", "on", "in", "at", "by", "for", "is", "are",
    "and", "or", "if", "your", "you", "each", "equal", "number", "under", "control", "controlled", "with",
}


def _text_name(text, fallback, used):
    """A name from card text: its words in capitals, without markup, cost tokens or filler words, at most
    five. Unique within the card (a suffix is added when a name is taken)."""
    plain = re.sub(r"<[^>]+>|\[[^\]]*\]|\([^)]*\)|#[A-Za-z0-9_]+#", " ", str(text or ""))
    words = [word.upper() for word in re.findall(r"[A-Za-z]+", plain)
             if word.lower() not in _NAME_STOPWORDS][:5]
    name = "_".join(words) or fallback
    unique, counter = name, 2
    while unique in used:
        unique = f"{name}_{counter}"
        counter += 1
    used.add(unique)
    return unique


def _fragment_name(record, index):
    """A readable name for an effect fragment: its record name in capitals, unique within the card."""
    words = re.sub(r"[^A-Za-z0-9]+", " ", str(record.get("m_Name") or f"effect {index}"))
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", words).split()
    name = ""
    for word in words:
        if name and len(name) + 1 + len(word) > 40:
            break
        name = f"{name}_{word.upper()}" if name else word.upper()
    name = name or f"EFFECT_{index}"
    return f"{name}_{index}" if index else name


def _clause_text(clause):
    """One effect-list entry as a Clause(...) call, with only the fields that differ from the default."""
    from gamedata.mechanics_fields import ir_name
    arguments = []
    for key, value in clause.items():
        if key == "m_EffectTemplateId":
            arguments.append(f'effect_template_id=Ref("{value["m_Guid"]}")')
        elif key in CLAUSE_DEFAULTS and value == CLAUSE_DEFAULTS[key]:
            continue
        elif isinstance(value, dict) and set(value) == {"m_Guid"}:
            arguments.append(f'{ir_name(key)}=Ref("{value["m_Guid"]}")')
        else:
            arguments.append(f"{ir_name(key)}={P._literal(value)}")
    return "Clause(" + ", ".join(arguments) + ")"


def _pretty(call_text, extra=()):
    """A constructor call with one keyword per line (the values themselves stay on one line)."""
    tree = ast.parse(call_text, mode="eval").body
    lines = [f"{keyword.arg}={ast.get_source_segment(call_text, keyword.value)}" for keyword in tree.keywords]
    lines += list(extra)
    return f"{tree.func.id}(\n" + "".join(f"    {line},\n" for line in lines) + ")"


def write(guid):
    root_record = _record("AbilityTemplate", guid)
    effect_lines = []
    clause_texts = []
    used = set()
    for index, clause in enumerate(root_record.get("m_AbilityEffectList") or []):
        effect_guid = str(clause["m_EffectTemplateId"]["m_Guid"]).lower()
        effect_record = _record("AbilityEffectTemplate", effect_guid)
        name = _text_name(effect_record.get("m_GameText"), _fragment_name(effect_record, index), used)
        effect_lines.append(f"# Records name: {effect_record.get('m_Name')}")
        effect_lines.append(f"{name} = {_pretty(P.dumps(_class(effect_record).from_record(effect_record)))}")
        clause_texts.append(_clause_text(clause))
    root_plain = dict(root_record)
    root_plain.pop("_v", None)
    root_plain.pop("m_AbilityEffectList", None)
    root_text = P.dumps(T.AbilityTemplate.from_record(root_plain))
    clauses_arg = "ability_effect_list=[" + ", ".join(clause_texts) + "]" if clause_texts else None
    body = _pretty(root_text, extra=[clauses_arg] if clauses_arg else [])
    root_name = _text_name(root_record.get("m_GameText"), "ABILITY", used)
    game_text = (root_record.get("m_GameText") or "").replace("\n", " ")
    header = [
        f"# Ability {guid[:8]}: {game_text}",
        "#",
        "# Constructor calls of the generated mechanics classes (gamedata/mechanics_types.py), read by",
        "# gamedata/ir_python.py. Effect fragments are named after their record; the root's clauses",
        "# reference them by GUID.",
        "",
    ]
    root_line = [f"# Records name: {root_record.get('m_Name')}", f"{root_name} = {body}"]
    return "\n".join(header + effect_lines + [""] + root_line + [""]), guid[:8]


def main(guids):
    out_dir = os.path.join(ROOT, "ir_cards")
    for guid in guids:
        text, guid8 = write(guid.lower())
        path = os.path.join(out_dir, f"troop_{guid8}.py")
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(text)
        print("wrote", path)


if __name__ == "__main__":
    main(sys.argv[1:])
