"""Historical trigger entry points that forward to ``rules_port.triggers``.

Projection and mode adapters still call ``resolve_triggers`` /
``resolve_enters_play_triggers`` by these names; every event is dispatched
by the native RulesPort trigger backend.
"""

import json

import game_engine
from gamedata import DEFAULT_RECORD_STORE, ability_graph

from ._shared import (
    _log,
    owner_uid,
)


_RECORD_STORE = DEFAULT_RECORD_STORE


def resolve_triggers(db, handler, game, session, pl_t, ai_t, bstate,
                     event_type, source_uid, source_owner_uid=None,
                     extra_target=None, zones=None,
                     event_source_collection=None,
                     event_destination_collection=None,
                     event_previous_state=None, event_int_attribute=None,
                     event_tac=None):
    """Fire every card ability whose trigger_event_type == event_type.

    ``source_uid`` is the card that entered / attacked / blocked / died.
    For ``CardCastEvent``, it is the casting champion and ``extra_target`` is
    the card being cast. ``source_owner_uid`` is the event player's DB id
    (0 = AI).
    Returns a log string.
    """
    bstate = bstate or {}
    from rules_port.triggers import NativeTriggerBackend, TriggerEvent

    # RulesPort owns the live trigger lifecycle. Projection and mode adapters
    # still call this historical function name, so every event goes through the
    # typed dispatcher, including recursive native events.
    return NativeTriggerBackend()(
        db=db, handler=handler, game=game, session=session,
        player_uid=pl_t, ai_uid=ai_t, battle_state=bstate,
        event=TriggerEvent(
            str(event_type).rsplit(".", 1)[-1], source_uid,
            source_owner_uid, extra_target,
            data={"zones": zones,
                  "event_source_collection": event_source_collection,
                  "event_destination_collection": event_destination_collection,
                  "event_previous_state": event_previous_state,
                  "event_int_attribute": event_int_attribute,
                  "event_tac": dict(event_tac or {})}))


