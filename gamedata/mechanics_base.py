"""The base of the generated mechanics classes (``gamedata/mechanics_types.py``).

A generated class declares its client type name, its version, its base class, its own serialized
fields with their client defaults, and its ``deprecated`` fields (removed or renamed, which a stored
record may still carry). ``Typed`` gives every class the same behaviour:

* a field is a keyword argument named in snake_case (``master_deals_damage``); its attribute holds the
  Records name (``m_MasterDealsDamage``), so the interpreter reads the same names the Records use;
* an argument left out takes the client default;
* ``from_record`` reads a stored object, keeping its key order, and fills in the current fields it
  leaves out with their defaults;
* ``to_record`` writes the Records object back, with a bool as 0 or 1, as the Records store it.

Nested objects are typed objects too; a list of them is a list.
"""

from __future__ import annotations

from typing import Any, ClassVar

from gamedata.mechanics_fields import ir_name


class Typed:
    """A serialized mechanics object. Subclasses are generated; see the module docstring."""

    type_name: ClassVar[str] = ""
    version: ClassVar[int | None] = None
    fields: ClassVar[dict[str, tuple[str, Any]]] = {}       # own fields: record name -> (type, default)
    deprecated: ClassVar[dict[str, tuple[str, Any]]] = {}   # removed or renamed fields

    def __init__(self, **arguments: Any) -> None:
        known = _argument_names(type(self))
        unknown = sorted(set(arguments) - set(known))
        if unknown:
            raise TypeError(f"{type(self).__name__} has no field {', '.join(unknown)}")
        for argument, record_name in known.items():
            value = arguments[argument] if argument in arguments else _default_of(type(self), record_name)
            setattr(self, record_name, _copy_default(value))

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "Typed":
        """The typed object for a stored record. Its keys keep the stored order; a current field the
        record leaves out takes its client default."""
        obj = cls.__new__(cls)
        order = [key for key in record if key not in ("_t", "_v")]
        for name in _record_names(cls):
            setattr(obj, name, _copy_default(_default_of(cls, name)))
        for key in order:
            setattr(obj, key, _typed_value(record[key]))
        obj._record_order = order
        obj._stored_type = record.get("_t", cls.type_name)
        return obj

    def to_record(self) -> dict[str, Any]:
        out: dict[str, Any] = {"_t": getattr(self, "_stored_type", self.type_name)}
        names = getattr(self, "_record_order", None) or _record_names(type(self))
        for record_name in names:
            out[record_name] = _record_value(getattr(self, record_name))
        return out

    def __repr__(self) -> str:
        shown = ", ".join(f"{ir_name(name)}={getattr(self, name)!r}" for name in _record_names(type(self))[:4])
        return f"{type(self).__name__}({shown})"


def _chain(cls: type) -> list[type]:
    """The class and its typed base classes, nearest first."""
    out = []
    current: type | None = cls
    while current is not None and issubclass(current, Typed) and current is not Typed:
        out.append(current)
        current = current.__bases__[0]
    return out


def _record_names(cls: type) -> list[str]:
    """Every current field of a class, inherited ones first (the order the client serializes them in)."""
    names: list[str] = []
    for klass in reversed(_chain(cls)):
        for name in klass.fields:
            if name not in names:
                names.append(name)
    return names


def _argument_names(cls: type) -> dict[str, str]:
    """Keyword argument (snake_case) -> record field name."""
    return {ir_name(name): name for name in _record_names(cls)}


def _default_of(cls: type, record_name: str) -> Any:
    for klass in _chain(cls):
        if record_name in klass.fields:
            return klass.fields[record_name][1]
    raise KeyError(record_name)


def _copy_default(value: Any) -> Any:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return dict(value)
    return value


def _typed_value(value: Any) -> Any:
    """A stored value as typed objects: a tagged object becomes its generated class."""
    if isinstance(value, list):
        return [_typed_value(item) for item in value]
    if isinstance(value, dict):
        type_name = value.get("_t")
        if isinstance(type_name, str):
            from gamedata import mechanics_types
            cls = getattr(mechanics_types, type_name.rsplit(".", 1)[-1], None)
            if isinstance(cls, type) and issubclass(cls, Typed):
                return cls.from_record(value)
        return {key: _typed_value(item) for key, item in value.items()}
    return value


def _record_value(value: Any) -> Any:
    if isinstance(value, Typed):
        return value.to_record()
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, list):
        return [_record_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _record_value(item) for key, item in value.items()}
    return value
