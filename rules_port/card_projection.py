"""RulesPort-owned CardUpdated projection helpers."""

from __future__ import annotations

import json

import game_engine

from .runtime_helpers import owner_uid


def push_card_state(game, session, db, handler, player_uid, ai_uid, card_uid,
                    new_state, battle_state=None):
    """Project a card's authoritative current zone and state.

    This is a wire projection, not a rules decision. Keeping it here prevents
    native effects such as revoke/untap/remove-from-combat from importing the
    legacy BOM merely to emit ``CardUpdated``.
    """
    from pvp_db import db_card_ability_payload, db_card_zone_details
    details = db_card_zone_details(session.session_id, int(card_uid), conn=db)
    if not details:
        return False
    template_guid, owner_id, location = details[0], details[2], details[3]
    scid = game_engine.SessionCardId(game_engine.UID(int(card_uid)))
    _tpl, card_type, _name, cost, attack, defense, gems = \
        handler._card_full_data(game, scid, template_guid)
    cdef = game.card_defs.get(scid)
    if cdef is not None:
        try:
            abilities = json.loads(db_card_ability_payload(
                session.session_id, int(card_uid), conn=db) or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            abilities = []
        if isinstance(abilities, list):
            cdef.abilities = [game_engine.ResourceId.from_str(str(guid))
                              for guid in abilities if guid]
    # Transaction ingress may carry the numeric service UID while the Game
    # projection normally carries a typed UID wrapper.  Normalize at this
    # wire boundary so ownership and PvP namespace checks do not depend on
    # which path constructed the projection Game.
    player_uid = game_engine.UID(
        int(getattr(player_uid, "uid64", player_uid)))
    ai_uid = game_engine.UID(
        int(getattr(ai_uid, "uid64", ai_uid)))
    if ((battle_state and battle_state.get("pvp")) or
            ((int(player_uid.uid64) & 0xff) == 244 and
             (int(ai_uid.uid64) & 0xff) == 244)):
        recipient = game_engine.UID.make(244, int(owner_id))
    else:
        recipient = owner_uid(owner_id, player_uid, ai_uid, battle_state)
    collections = {
        "hand": game_engine.ECardCollections.Hand,
        "deck": game_engine.ECardCollections.Deck,
        "warzone": game_engine.ECardCollections.Warzone,
        "discard": game_engine.ECardCollections.Discard,
        "void": game_engine.ECardCollections.Void,
        "playedresources": game_engine.ECardCollections.PlayedResources,
        # Hidden encounter-mod collection: never project it as Warzone.
        "mod": game_engine.ECardCollections.Mod,
    }
    collection = collections.get(str(location or "").lower(),
                                 game_engine.ECardCollections.Warzone)
    # The client's "affected by" list: each ability another card granted this one, as (source card template,
    # ability template). A champion source has no card row, so only card-sourced grants can be named.
    from .effect_lifetimes import grants_on_card
    # The client lists each pair once: a repeated grant of the same ability from the same card adds no line.
    affecting = []
    listed = set()
    for source_uid, ability_guid in grants_on_card(battle_state, card_uid):
        source = db_card_zone_details(session.session_id, source_uid, conn=db)
        pair = (str(source[0]).lower(), ability_guid) if source else None
        if pair and pair not in listed:
            listed.add(pair)
            affecting.extend([game_engine.ResourceId.from_str(pair[0]),
                              game_engine.ResourceId.from_str(pair[1])])
    game.push_card_updated(
        scid, recipient, collection, card_type, template_id=template_guid,
        attack=attack, defense=defense, cost=cost, state=int(new_state or 0),
        gems=gems, nulling=str(location or "").lower() == "deck",
        affecting_abilities=affecting)
    return True
