"""Generate ``gamedata/mechanics.py``: the client's serialized mechanics classes.

Two places in the client declare the serialized classes:

* ``Persistence/DefineBits.cs``: one ``DefineX(dch)`` per class. ``AddClass`` names the class,
  ``StartVersion(N)`` opens version N, and ``AddField`` / ``RemoveField`` are the deltas of that
  version. This is the persistence schema, so it is the primary source: a class's version is its
  last ``StartVersion``, and its fields are the deltas applied in order.
* ``[TDFClassInfo(_currentVersion = N)]`` attributes on the class declarations. Used for the
  classes DefineBits does not declare.

A field's default is its initializer (``= 0``, ``= false``, ``= ResourceId.Invalid``) when it has
one, else the zero value of its type. The IR writer leaves out fields that hold their default.
Inherited fields are not copied: a class lists its ``base``, and the consumer walks the chain.

Run from the repo root:

    python3 AssetExtraction/extract_mechanics.py
"""

import glob
import os
import re

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
CLIENT = os.path.join(ROOT, "hex-private", "HexClient")
DEFINE_BITS = os.path.join(CLIENT, "Assembly-CSharp-firstpass", "Persistence", "DefineBits.cs")
SOURCE_GLOBS = (
    os.path.join(CLIENT, "Assembly-CSharp-firstpass", "**", "*.cs"),
    os.path.join(CLIENT, "Assembly-CSharp", "**", "*.cs"),
)
OUTPUT = os.path.join(ROOT, "gamedata", "mechanics.py")

METHOD = re.compile(r"public\s+static\s+void\s+Define\w+\(DeltaClassHelper\s+dch\)\s*\{(.*?)\n\t\t\}", re.S)
ADD_CLASS = re.compile(r'dch\.AddClass\(\s*\w+\s*,\s*"(\w+)"')
NAMESPACE = re.compile(r'_namespace\s*=\s*"([\w.]+)"')
START = re.compile(r"StartVersion\((\d+)\)")
ADD_FIELD = re.compile(r'AddField\(\s*"([^"]*)"\s*,\s*"(\w+)"\s*(?:,\s*(?:"([^"]*)"|string\.Empty)\s*)?[,)]')
REMOVE_FIELD = re.compile(r'RemoveField\(\s*"(\w+)"')
RENAME_FIELD = re.compile(r'RenameField\(\s*"(\w+)"\s*,\s*"(\w+)"')
ADD_BASE = re.compile(r'AddBaseClass\(\s*dch\s*,\s*"(\w+)"')
ATTRIBUTE_VERSION = re.compile(r"\[TDFClassInfo\(\s*_currentVersion\s*=\s*(\d+)")
CLASS_DECL = re.compile(
    r"(?:public|internal)?\s*(?:abstract\s+|sealed\s+|static\s+|partial\s+)*class\s+(\w+)\s*"
    r"(?::\s*([\w.<>,\s]+?))?\s*\{")
FIELD_DECL = re.compile(
    r"\[TDFIncludeField[^\]]*\]\s*(?:\[[^\]]*\]\s*)*"
    r"(?:(?:public|internal|private|protected|static|readonly)\s+)*"
    r"([\w.<>\[\],?]+)\s+(\w+)\s*(?:=\s*([^;]+?))?\s*;")
ZERO = {"int": "0", "uint": "0", "long": "0", "ulong": "0", "short": "0", "byte": "0",
        "float": "0.0", "double": "0.0", "bool": "False", "string": '""'}


def _read(path):
    with open(path, encoding="utf-8", errors="replace") as stream:
        return stream.read()


def _initializer(text):
    """A C# initializer as a Python expression, or None when it has no plain value."""
    value = text.strip().lstrip("=").strip()
    if value in ("", "string.Empty"):
        return '""' if value else None
    if value.startswith("new List<") or value.startswith("new HashSet<"):
        return "[]"
    if value.startswith("new Dictionary<"):
        return "{}"
    if value == "null" or value.startswith("ResourceId.Invalid") or value.startswith("new "):
        return "None"
    if value in ("true", "false"):
        return value.capitalize()
    if re.fullmatch(r"-?\d+", value):
        return value
    if re.fullmatch(r"-?\d+\.\d+", value):
        return value
    if re.fullmatch(r"-?\d+(\.\d+)?[fF]", value):
        return value[:-1]
    if re.fullmatch(r"\w+\.\w+", value):
        # An enum member is stored by its name (``ETriggerSelectors.TriggerSource`` is ``TriggerSource``).
        return repr(value.split(".")[-1])
    return None


def _default(type_name, initializer, enums):
    """The client default of one field, as a Python expression."""
    if initializer is not None:
        return initializer
    if type_name in ZERO:
        return ZERO[type_name]
    if type_name in enums:
        return repr(enums[type_name])
    if type_name.startswith("List<"):
        return "[]"
    return "None"


def _enums():
    """The first member of each enum, which the client's zero value is."""
    table = {}
    for path in glob.glob(os.path.join(CLIENT, "**", "*.cs"), recursive=True):
        for name, body in re.findall(r"public\s+enum\s+(\w+)[^{]*\{([^}]*)\}", _read(path)):
            first = re.match(r"\s*(\w+)", body)
            if first:
                table.setdefault(name, first.group(1))
    return table


def from_define_bits(enums):
    """Classes declared in Persistence/DefineBits.cs, by name."""
    classes = {}
    for body in METHOD.findall(_read(DEFINE_BITS)):
        cls = ADD_CLASS.search(body)
        if not cls:
            continue
        name = cls.group(1)
        namespace = NAMESPACE.search(body)
        base = ADD_BASE.search(body)
        fields = {}
        deprecated = {}
        version = 0
        # Each StartVersion opens a version; its deltas run until the next one.
        for chunk in re.split(r"(?=StartVersion\(\d+\))", body):
            start = START.match(chunk)
            if not start:
                continue
            version = max(version, int(start.group(1)))
            for field_type, field_name, initializer in ADD_FIELD.findall(chunk):
                fields[field_name] = (field_type, initializer)
            # A removed field leaves the current fields; the Records keep it under a deprecated
            # name. A renamed field's old name is deprecated too: older records still store it.
            for field_name in REMOVE_FIELD.findall(chunk):
                if field_name in fields:
                    deprecated[field_name + "_DEPRECATED"] = fields.pop(field_name)
            for old_name, new_name in RENAME_FIELD.findall(chunk):
                if old_name in fields:
                    deprecated[old_name] = fields.pop(old_name)
                    fields[new_name] = deprecated[old_name]
        classes[name] = {
            "version": version,
            "base": base.group(1) if base else None,
            "namespace": namespace.group(1) if namespace else "",
            "source": "Persistence/DefineBits.cs",
            "fields": {field_name: {"type": field_type,
                                    "default": _default(field_type, _initializer(initializer or ""), enums)}
                       for field_name, (field_type, initializer) in fields.items()},
            "deprecated": {field_name: {"type": field_type,
                                        "default": _default(field_type, _initializer(initializer or ""), enums)}
                           for field_name, (field_type, initializer) in deprecated.items()},
        }
    return classes


def from_declarations(enums):
    """Every C# class that declares serialized fields, with its base and attribute version.

    A base class is often unversioned (``AbilityField`` carries the fields of every ability
    field), so a class is kept when it has ``[TDFIncludeField]`` members, versioned or not.
    """
    classes = {}
    for pattern in SOURCE_GLOBS:
        for path in glob.glob(pattern, recursive=True):
            text = _read(path)
            declarations = list(CLASS_DECL.finditer(text))
            for index, decl in enumerate(declarations):
                name = decl.group(1)
                if name in classes:
                    continue
                end = declarations[index + 1].start() if index + 1 < len(declarations) else len(text)
                body = text[decl.end():end]
                previous = declarations[index - 1].end() if index else 0
                version = ATTRIBUTE_VERSION.findall(text[previous:decl.start()])
                fields = {field_name: {"type": field_type,
                                       "default": _default(field_type, _initializer(initializer or ""), enums)}
                          for field_type, field_name, initializer in FIELD_DECL.findall(body)}
                base = decl.group(2)
                # Keep a class with a version, fields, or a base: an abstract base with no
                # members of its own still sits in a chain that other classes inherit fields from.
                if not version and not fields and not base:
                    continue
                classes[name] = {
                    "version": int(version[-1]) if version else None,
                    "base": (base or "").split(",")[0].strip().split("<")[0].split(".")[-1] or None,
                    "namespace": "",
                    "source": os.path.relpath(path, ROOT),
                    "fields": fields,
                    "deprecated": {},
                }
    return classes


def record_versions():
    """Each class's version as the Records store it. Every class has one version there."""
    import json
    found = {}
    for path in glob.glob(os.path.join(ROOT, "hex-private", "Records", "*.jsonl")):
        for line in open(path, encoding="utf-8", errors="replace"):
            if "_v" not in line:
                continue
            for entry in re.findall(r'\\?"(\w+)\\?"\s*:\s*(\d+)', line.split('_v', 1)[1][:600]):
                found.setdefault(entry[0], int(entry[1]))
    return found


def build():
    enums = _enums()
    classes = from_define_bits(enums)
    declared = from_declarations(enums)
    for name, info in declared.items():
        if name not in classes:
            classes[name] = info
        elif not classes[name]["base"]:
            classes[name]["base"] = info["base"]
    versions = record_versions()
    for name, info in classes.items():
        if info["version"] is None and name in versions:
            info["version"] = versions[name]
    # Keep the classes the Records use or inherit from: the versioned ones and their bases.
    keep = set()
    for name, info in classes.items():
        if info["version"] is not None:
            current = name
            while current and current in classes and current not in keep:
                keep.add(current)
                current = classes[current]["base"]
    return {name: classes[name] for name in keep}


def render(classes):
    lines = [
        '"""The client\'s serialized mechanics classes (generated by AssetExtraction/extract_mechanics.py).',
        "",
        "Do not edit by hand. ``CLASSES[name]`` holds the class's ``version``, its ``base`` class name,",
        "its ``namespace`` and ``source``, its current serialized ``fields`` and its ``deprecated`` ones",
        "(removed or renamed; the Records may still store them). Each field is ``(C# type, client default)``.",
        "Inherited fields come from the base class.",
        '"""',
        "",
        "CLASSES = {",
    ]
    for name in sorted(classes):
        info = classes[name]
        lines.append(f"    {name!r}: {{")
        lines.append(f"        'version': {info['version']!r},")
        lines.append(f"        'base': {info['base']!r},")
        lines.append(f"        'namespace': {info['namespace']!r},")
        lines.append(f"        'source': {info['source']!r},")
        lines.append("        'fields': {")
        for field_name in sorted(info["fields"]):
            field = info["fields"][field_name]
            lines.append(f"            {field_name!r}: ({field['type']!r}, {field['default']}),")
        lines.append("        },")
        lines.append("        'deprecated': {")
        for field_name in sorted(info["deprecated"]):
            field = info["deprecated"][field_name]
            lines.append(f"            {field_name!r}: ({field['type']!r}, {field['default']}),")
        lines.append("        },")
        lines.append("    },")
    lines.append("}")
    return "\n".join(lines) + "\n"


TYPES_OUTPUT = os.path.join(ROOT, "gamedata", "mechanics_types.py")
DEFAULT_NAMESPACE = "Game.Shared.Mechanics"


def _depth(name, classes):
    depth = 0
    current = classes[name]["base"]
    while current in classes and depth < 64:
        depth += 1
        current = classes[current]["base"]
    return depth


def render_types(classes):
    """The Python classes: one per serialized type, each subclassing its base."""
    lines = [
        '"""The client\'s serialized mechanics types as Python classes (generated by',
        "AssetExtraction/extract_mechanics.py; do not edit by hand).",
        "",
        "Each class names its client type (``type_name``), its version, and its own fields as",
        "``fields``: record name -> (C# type, client default). Inherited fields come from the base.",
        '"""',
        "",
        "from gamedata.mechanics_base import Typed",
        "",
    ]
    for name in sorted(classes, key=lambda item: (_depth(item, classes), item)):
        info = classes[name]
        base = info["base"] if info["base"] in classes else "Typed"
        namespace = info.get("namespace") or DEFAULT_NAMESPACE
        lines += [
            "",
            f"class {name}({base}):",
            f"    type_name = {namespace + '.' + name!r}",
            f"    version = {info['version']!r}",
            "    fields = {",
        ]
        for field_name in sorted(info["fields"]):
            field = info["fields"][field_name]
            lines.append(f"        {field_name!r}: ({field['type']!r}, {field['default']}),")
        lines.append("    }")
        lines.append("    deprecated = {")
        for field_name in sorted(info["deprecated"]):
            field = info["deprecated"][field_name]
            lines.append(f"        {field_name!r}: ({field['type']!r}, {field['default']}),")
        lines.append("    }")
    return "\n".join(lines) + "\n"


def main():
    classes = build()
    with open(OUTPUT, "w", encoding="utf-8") as stream:
        stream.write(render(classes))
    with open(TYPES_OUTPUT, "w", encoding="utf-8") as stream:
        stream.write(render_types(classes))
    fields = sum(len(c["fields"]) for c in classes.values())
    print(f"wrote {OUTPUT} and {TYPES_OUTPUT}: {len(classes)} classes, {fields} own fields")


if __name__ == "__main__":
    main()
