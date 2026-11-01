"""The client's own field types and enums, read from the disassembled C# (hex-private/HexClient).

The ability editor shows and checks a Records object by the C# class its ``_t`` names:

* ``fields(type_name)``: ``{m_ field: C# type}`` for the class and its base classes,
  with the default each field is initialised to (``= EAbilityDurations.Instant``).
* ``enum(name)``: the members of a C# enum, and whether it is ``[Flags]`` (Records then
  writes several members joined by ``|``).
* ``describe(cs_type)``: how the editor shows a field (bool, int, enum, flags, guid,
  text, or json for nested objects and lists).
* ``is_a(type_name, base)``: whether a class derives from (or implements) ``base``.
* ``enum_members(name)``: an enum's members with their values and the display name
  their ``[ToolName]`` attribute gives (``[ToolName("Crush")] Juggernaught = 16``).
* ``attribute_names()``: the client's attribute names (``IntAttrs``, ``StringAttrs``,
  ``TACAttrs``, ``ListAttrs``), the keys of a TAC (``new IntAttrs("Lethal", false)``).

The sources are scanned once per process, under a lock. Nothing here is written.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

from paths import REPO  # noqa: E402

CLIENT = REPO / "hex-private" / "HexClient"
# The game rules live in firstpass; the UI assembly is scanned for enums only.
FIRSTPASS = CLIENT / "Assembly-CSharp-firstpass"
ASSEMBLIES = (FIRSTPASS, CLIENT / "Assembly-CSharp")

INT_TYPES = {"int", "uint", "short", "ushort", "long", "ulong", "byte", "sbyte"}
NUMBER_TYPES = {"float", "double"}
INT32 = (-(1 << 31), (1 << 31) - 1)

_FIELD = re.compile(
    r"^\s*(?:public|private|protected|internal)\s+(?!static\b|const\b)(?:readonly\s+)?"
    r"([\w.]+(?:<[\w.<>, ]+>)?(?:\[\])?)\s+(m_\w+)\s*(?:=\s*([^;]+))?;", re.M)
_CLASS = re.compile(r"\b(?:class|interface|struct)\s+(\w+)(?:<[^>]*>)?\s*(?::\s*([^{\n]+))?")
_NAMESPACE = re.compile(r"^namespace\s+([\w.]+)", re.M)
_ENUM = re.compile(r"(\[Flags\][^{}]*?)?\benum\s+(\w+)\s*(?::\s*\w+)?\s*\{([^}]*)\}", re.S)

# The AttributeName subclasses: each file declares its names as static fields.
ATTRIBUTE_KINDS = ("IntAttrs", "StringAttrs", "TACAttrs", "ListAttrs")
# [ToolName], [ToolName("Crush")] or [ToolName("Done", "description", true)]: the first
# string is the display name.
_TOOL_NAME = re.compile(r'^\[ToolName(?:\("([^"]*)"[^\]]*\))?\]$')
_MEMBER = re.compile(r"^(\w+)\s*(?:=\s*(-?(?:0x[0-9a-fA-F]+|\d+)))?\s*,?$")

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {}


def _strip(text: str) -> str:
    """C# body text without comments and attributes."""
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r"\[[^\]\n]*\]", "", text)


def _scan() -> dict[str, Any]:
    """Index the client sources: classes by full and short name, and enums."""
    with _LOCK:
        if _STATE:
            return _STATE
        classes: dict[str, dict[str, Any]] = {}
        short: dict[str, list[str]] = {}
        enums: dict[str, dict[str, Any]] = {}
        bodies: dict[str, str] = {}   # enum -> its body as written, attributes and all
        attributes: dict[str, list[str]] = {}   # AttributeName subclass -> declared names
        for assembly in ASSEMBLIES:
            if not assembly.is_dir():
                continue
            for path in assembly.rglob("*.cs"):
                text = path.read_text(encoding="utf-8-sig", errors="replace")
                for found in _ENUM.finditer(text):
                    name = found.group(2)
                    members = [re.split(r"\s*=", part.strip())[0]
                               for part in _strip(found.group(3)).split(",")]
                    enums.setdefault(name, {"values": [m for m in members if m],
                                            "flags": bool(found.group(1))})
                    bodies.setdefault(name, found.group(3))
                if assembly is FIRSTPASS:
                    _index_class(path, text, classes, short)
                    if path.stem in ATTRIBUTE_KINDS:
                        attributes[path.stem] = list(dict.fromkeys(
                            re.findall(rf'new {path.stem}\("([^"]+)"', text)))
        _STATE.update(classes=classes, short=short, enums=enums, enum_bodies=bodies,
                      attributes=attributes)
        return _STATE


def _index_class(path: Path, text: str, classes: dict, short: dict) -> None:
    namespace = _NAMESPACE.search(text)
    body = _strip(text)
    found = _CLASS.search(body)
    if not found or found.group(1) != path.stem:
        return
    bases = [b.strip().split("<")[0].rsplit(".", 1)[-1]
             for b in (found.group(2) or "").split(",") if b.strip()]
    full = f"{namespace.group(1)}.{path.stem}" if namespace else path.stem
    classes[full] = {"name": path.stem, "namespace": namespace.group(1) if namespace else "",
                     "bases": bases, "fields": _serialized_fields(text),
                     "abstract": bool(re.search(rf"\babstract\s+class\s+{path.stem}\b", body))}
    short.setdefault(path.stem, []).append(full)


def _serialized_fields(text: str) -> dict[str, dict[str, str]]:
    """The m_ fields the TDF serializer writes, as ``{name: {type, default}}``.

    A class marked ``RequireIncludeField`` writes only ``[TDFIncludeField]`` fields;
    any other class writes every field not marked ``[TDFIgnore]``/``[NonSerialized]``.
    The attributes sit on the lines above a field, after the previous declaration.
    """
    code = re.sub(r"//[^\n]*", "", text)
    include_only = "RequireIncludeField" in code
    out, previous = {}, 0
    for found in _FIELD.finditer(code):
        attributes = code[previous:found.start()]
        previous = found.end()
        if include_only and "TDFIncludeField" not in attributes:
            continue
        if not include_only and ("TDFIgnore" in attributes or "NonSerialized" in attributes):
            continue
        out[found.group(2)] = {"type": found.group(1), "default": (found.group(3) or "").strip()}
    return out


def _class(type_name: str, near: str = "") -> dict[str, Any] | None:
    """The class a ``_t`` (full) or a C# base name (short) refers to."""
    state = _scan()
    if type_name in state["classes"]:
        return state["classes"][type_name]
    candidates = state["short"].get(type_name.rsplit(".", 1)[-1], [])
    for full in candidates:   # prefer the namespace of the class that names it
        if near and full.rsplit(".", 1)[0] == near:
            return state["classes"][full]
    return state["classes"][candidates[0]] if candidates else None


def known_type(type_name: str) -> bool:
    return _class(type_name) is not None


def lineage(type_name: str) -> list[dict[str, Any]]:
    """The class and its bases, most derived first."""
    chain, seen = [], set()
    current = _class(type_name)
    while current and current["name"] not in seen:
        seen.add(current["name"])
        chain.append(current)
        parent = next((b for b in current["bases"] if _class(b, current["namespace"])), None)
        current = _class(parent, current["namespace"]) if parent else None
    return chain


def is_a(type_name: str, base: str) -> bool:
    """Whether ``type_name`` is ``base`` or derives from or implements it (short names)."""
    wanted = base.rsplit(".", 1)[-1]
    for entry in lineage(type_name):
        if entry["name"] == wanted or wanted in entry["bases"]:
            return True
    return False


def fields(type_name: str) -> dict[str, dict[str, str]]:
    """``{m_ field: {type, default}}`` over the class and its bases."""
    merged: dict[str, dict[str, str]] = {}
    for entry in reversed(lineage(type_name)):
        merged.update(entry["fields"])
    return merged


def enum(name: str) -> dict[str, Any] | None:
    return _scan()["enums"].get(name.rsplit(".", 1)[-1])


def enum_members(name: str) -> list[dict[str, Any]]:
    """``[{name, value, label}]`` for an enum's members in source order. ``label`` is the
    member's ``[ToolName("...")]``, its own name for a bare ``[ToolName]``, and "" when it
    has none. A member without ``= value`` is one more than the member before it."""
    body = _scan()["enum_bodies"].get(name.rsplit(".", 1)[-1])
    if body is None:
        return []
    out, label, value = [], None, -1
    for line in body.splitlines():
        line = re.sub(r"//.*", "", line).strip()
        tool = _TOOL_NAME.match(line)
        if tool:
            label = tool.group(1) if tool.group(1) is not None else ""
            continue
        member = _MEMBER.match(line)
        if not member:
            continue
        value = int(member.group(2), 0) if member.group(2) else value + 1
        out.append({"name": member.group(1), "value": value,
                    "label": (label or member.group(1)) if label is not None else ""})
        label = None
    return out


def attribute_names() -> dict[str, list[str]]:
    """``{kind: [name]}`` for the client's attribute names (``ATTRIBUTE_KINDS``)."""
    return {kind: list(names) for kind, names in _scan()["attributes"].items()}


def concrete_types(base: str) -> list[str]:
    """Full names of the non-abstract client classes that derive from ``base``."""
    return sorted(full for full, entry in _scan()["classes"].items()
                  if not entry["abstract"] and is_a(full, base))


def describe(cs_type: str) -> dict[str, Any]:
    """How the editor shows a C# field type: ``{kind, ...}``."""
    if cs_type == "bool":
        return {"kind": "bool"}
    if cs_type in INT_TYPES:
        return {"kind": "int"}
    if cs_type in NUMBER_TYPES:
        return {"kind": "number"}
    if cs_type == "string":
        return {"kind": "text"}
    if cs_type in ("Type", "System.Type"):
        return {"kind": "type_name"}   # a C# type, serialized by its full name
    if cs_type == "ResourceId":
        return {"kind": "guid"}
    if cs_type == "List<ResourceId>":
        return {"kind": "guid_list"}
    # A class of the same short name wins: several enums and classes share names.
    found = None if _class(cs_type) else enum(cs_type)
    if found:
        return {"kind": "flags" if found["flags"] else "enum", "enum": cs_type,
                "values": found["values"]}
    item = re.fullmatch(r"List<(\w+)>", cs_type)
    return {"kind": "json", "item": item.group(1) if item else ""}


def default_value(spec: dict[str, str]) -> Any:
    """The value a field starts with in a new record, from its C# initialiser."""
    kind = describe(spec["type"])
    default = spec.get("default") or ""
    if kind["kind"] in ("enum", "flags"):
        member = default.rsplit(".", 1)[-1] if default else ""
        return member if member in kind["values"] else (kind["values"] or [""])[0]
    if kind["kind"] == "bool" and default in ("true", "false"):
        return int(default == "true")   # ``bool m_DontRecalculate = true``
    if kind["kind"] in ("bool", "int"):
        return int(default) if re.fullmatch(r"-?\d+", default) else 0
    if kind["kind"] == "number":
        return 0
    if kind["kind"] == "text":
        return ""
    if kind["kind"] == "guid":
        return {"m_Guid": "00000000-0000-0000-0000-000000000000"}
    if kind["kind"] == "guid_list" or spec["type"].startswith("List<"):
        return []
    if spec["type"].startswith("Dictionary<"):
        return {}
    return None
