"""Test hook: let a stub context override a native leaf the way a method override used to.

The native leaves are module functions that take the context as ``ctx``.  Several tests stub a
method on a context object (``context._emit_trigger = ...``); the leaf now runs from its module, so
the stub would be bypassed.  ``install_leaf_routes()`` wraps each leaf so that a callable attribute
of the same name on the context, when present, is called instead.  Real EffectContext objects have
no such attribute, so they run the original leaf unchanged.
"""

from __future__ import annotations

import functools
import importlib
import inspect

_LEAF_MODULES = (
    "rules_port.leaves_abilities",
    "rules_port.leaves_modifiers",
    "rules_port.leaves_players",
    "rules_port.leaves_transforms",
    "rules_port.leaves_variables",
    "rules_port.leaves_zones",
)
_installed = False


def _route(module, name, original):
    @functools.wraps(original)
    def routed(ctx, *args, **kwargs):
        override = getattr(ctx, name, None)
        if callable(override):
            return override(*args, **kwargs)
        return original(ctx, *args, **kwargs)
    routed.__signature__ = inspect.signature(original)
    setattr(module, name, routed)


def install_leaf_routes() -> None:
    """Wrap every leaf once per process."""
    global _installed
    if _installed:
        return
    for module_name in _LEAF_MODULES:
        module = importlib.import_module(module_name)
        for name, value in list(vars(module).items()):
            if inspect.isfunction(value) and value.__module__ == module.__name__ \
                    and not name.startswith("__"):
                _route(module, name, value)
    _installed = True
