"""Native RulesPort card stat mutations and client projection."""

from __future__ import annotations

import json
import game_engine

# The durations whose grant must be undone later.  EndOfTurn lives in the card's ``temporary_buffs`` column, which the
# end-turn wipe clears; a while-in-play grant has no boundary to wait for, so it is applied as a permanent buff and
# recorded in the linked ledger (``rules_port.effect_lifetimes``), which expires it with its source.
LEDGER_DURATIONS = frozenset({"WhileCardInPlay", "BeginningOfOwnersTurn"})


def apply_stat_mod(context, target, attack=0, defense=0, *, this_turn=False,
                   duration=None):
    from pvp_db import (db_card_source_info, db_card_mutation_field,
                        db_set_card_mutation_field, db_card_state_value,
                        db_card_attribute_value)

    duration = str(duration or ("EndOfTurn" if this_turn else "Permanent"))
    this_turn = this_turn or duration == "EndOfTurn"
    uid = int(target)
    row = db_card_source_info(context.session.session_id, uid, conn=context.db)
    if not row:
        return None
    column = "temporary_buffs" if this_turn else "permanent_buffs"
    raw = db_card_mutation_field(
        context.session.session_id, uid, column, conn=context.db)
    try:
        buffs = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        buffs = {}
    if not isinstance(buffs, dict):
        buffs = {}
    buffs["atk"] = int(buffs.get("atk", 0) or 0) + int(attack or 0)
    buffs["def"] = int(buffs.get("def", 0) or 0) + int(defense or 0)
    db_set_card_mutation_field(
        context.session.session_id, uid, column,
        json.dumps(buffs, separators=(",", ":"), sort_keys=True),
        conn=context.db)
    context.db.commit()
    if duration in LEDGER_DURATIONS:
        from .effect_lifetimes import record_temporary_stat
        record_temporary_stat(
            context.bstate, uid, attack, defense, duration=duration,
            owner_id=int(context.bstate.get("resolving_owner_id", 0) or 0),
            source_uid=context.bstate.get("resolving_source_uid"))
    scid = game_engine.SessionCardId(game_engine.UID(uid))
    tpl, card_type, _name, cost, atk, def_, gems = \
        context.handler._card_full_data(context.game, scid, row[0])
    from .runtime_helpers import card_collection_for_location, owner_uid
    owner = owner_uid(row[3], context.player_uid, context.ai_uid, context.bstate)
    context.game.push_card_updated(
        scid, owner, card_collection_for_location(row[2]), card_type,
        template_id=tpl, cost=cost, attack=atk, defense=def_, gems=gems,
        attributes=db_card_attribute_value(
            context.session.session_id, uid, "card_attributes", conn=context.db),
        state=int(db_card_state_value(
            context.session.session_id, uid, conn=context.db) or 0),
        nulling=str(row[2]).lower() == "deck")
    return def_
