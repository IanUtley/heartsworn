"""The interpreter's counter-spell effect: C# CounterSpellAbilityEffectTemplate.

Each target is countered through the live counter leaf (rules_port.context counter_spell). The leaf reads its target
from the battle state, so the target is set for the one call and restored after it. The leaf itself refuses a card
that is no longer on the chain, or one that cannot be interrupted.
"""

from __future__ import annotations

import rules_port.leaves_abilities as _leaves_abilities
from typing import Any

import game_engine


def counter_targets(context: Any, targets: list[int], effect_raw: dict[str, Any]) -> None:
    """Counter each target card that is still on the chain, in order, through the live counter leaf."""
    bstate = context.bstate
    previous_template = context.effect_template_override
    previous_target = bstate.get("resolving_target_uid")
    context.effect_template_override = effect_raw
    try:
        for target in targets:
            bstate["resolving_target_uid"] = int(target)
            _leaves_abilities.counter_spell(context)
    finally:
        context.effect_template_override = previous_template
        if previous_target is None:
            bstate.pop("resolving_target_uid", None)
        else:
            bstate["resolving_target_uid"] = previous_target


def counter_spell(context):
    """Port of ``CounterSpellAbilityEffectTemplate`` / ``Session.CounterCard``.

    The effect only counters a card still held on the chain (CastSpells) and
    must respect the authored ``CantBeInterrupted`` int-attribute.  The Python
    native branch previously imported a function that did not exist and
    raised ImportError for every counter spell (43 Records rows).
    """
    target = context.resolved_target()
    if target is None:
        return "counter spell: no target"
    target = int(target)
    from pvp_db import db_card_location
    location = str(db_card_location(
        context.session.session_id, target, conn=context.db) or "").lower()
    if location != "castspells":
        return "counter spell: target not on chain"
    from rules_port.combat_rules import card_int_attr
    if card_int_attr(context.db, context.session.session_id, target,
                     "CantBeInterrupted",
                     battle_state=context.bstate) > 0:
        return "counter spell: cannot be interrupted"
    # Session.CounterCard removes the countered card's own ability from the
    # chain, then discards the card.  The native chain is keyed by ability
    # instance id, so find the item whose source is the target card instead of
    # passing the card UID to ``remove_ability`` (which silently did nothing).
    port = getattr(context.session, "_rules_port_session", None)
    if port is not None:
        chain = getattr(port, "chain", None)
        try:
            for ability in list(chain or ()):
                if int(getattr(ability, "source_uid", -1) or -1) != target:
                    continue
                instance_id = int(getattr(ability, "instance_id", 0) or 0)
                if chain is None:
                    break
                chain.remove_ability(instance_id)
                port.forget_projected_chain(instance_id)
        except (AttributeError, TypeError, ValueError):
            pass
    from pvp_db import db_card_chain_info, db_card_owner_id, db_discard_card
    from .runtime_helpers import owner_uid
    owner_id = int(db_card_owner_id(
        context.session.session_id, target, conn=context.db) or 0)
    scid = game_engine.SessionCardId(game_engine.UID(target))
    tpl_row = db_card_chain_info(
        context.session.session_id, target, conn=context.db)
    db_discard_card(context.session.session_id, target,
                    connection=context.db,
                    extra_set="card_state=0, card_damage=0, temporary_buffs=?, "
                              "temporary_attributes=0",
                    extra_params=["{}"])
    context.db.commit()
    owner = owner_uid(owner_id, context.player_uid, context.ai_uid,
                      context.bstate)
    if tpl_row:
        _tpl, card_type, _name, cost, attack, defense, gems = \
            context.handler._card_full_data(
                context.game, scid, tpl_row[0])
        context.game.push_card_updated(
            scid, owner, game_engine.ECardCollections.Discard,
            game_engine.card_type_from_db(card_type),
            template_id=tpl_row[0], cost=cost, attack=attack,
            defense=defense, gems=gems)
    else:
        # Even if the template row is unavailable, update the destination so
        # the C# client cache derives the zone move instead of relying on an
        # explicit class-50 packet with a stale representation.
        context.game.push_card_updated(
            scid, owner, game_engine.ECardCollections.Discard,
            game_engine.ECardTypes.Unknown, nulling=True)
    _leaves_abilities._emit_trigger(context,
        "CardCounteredEvent", target,
        context.bstate.get("resolving_owner_id", 0),
        event_source_collection="CastSpells",
        event_destination_collection="discard")
    return f"countered {hex(target)}"
