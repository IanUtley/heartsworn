"""TAC decoding for the RulesPort runtime.

The decoder lives in :mod:`gamedata.tac` (one implementation, following the client's reader). This
module keeps the names the runtime imports.
"""

from gamedata.tac import (  # noqa: F401
    attr_hash as _tac_attr_hash,
    decode_tac,
    decode_tac_tree,
    tac_function,
    tac_guid,
    tac_int,
    tac_string,
)
