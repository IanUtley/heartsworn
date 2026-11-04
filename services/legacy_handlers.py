"""Dispatch table for the request families moved out of hconnect_server."""

from services.legacy_loadbalancer import LEGACY_HANDLERS as _legacy_loadbalancer
from services.legacy_tournaments import LEGACY_HANDLERS as _legacy_tournaments
from services.legacy_session import LEGACY_HANDLERS as _legacy_session

LEGACY_HANDLERS = {}
LEGACY_HANDLERS.update(_legacy_loadbalancer)
LEGACY_HANDLERS.update(_legacy_tournaments)
LEGACY_HANDLERS.update(_legacy_session)
