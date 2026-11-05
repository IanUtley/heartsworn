"""Base class for the EffectContext mixins.

The mixins read and write host attributes that are declared on EffectContext
itself. Under type checking HostBase accepts any attribute as Any, so the
mixin bodies type-check without repeating the host's fields. At runtime it
is an empty class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:

    class HostBase:
        def __getattr__(self, name: str) -> Any: ...

        def __setattr__(self, name: str, value: Any) -> None: ...

else:

    class HostBase:
        pass
