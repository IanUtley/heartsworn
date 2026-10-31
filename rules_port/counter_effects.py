"""RulesPort-owned counter persistence and client projection."""

from __future__ import annotations

import json
import game_engine


TUNNELING_COUNTER_GUID = "def75520-0b8b-447f-8705-b34e71043890"
# Universal keyword ability represented in Records as a TurnStarted trigger.
# Its lifecycle is handled by the native tunneling service, so generic trigger
# discovery must not resolve it a second time.
TUNNELING_ABILITY_GUID = "a4fc4440-4f02-4f40-b786-214ad0205dad"


def _counter_guid(db, name):
    from pvp_db import db_counter_template_id
    return (str(db_counter_template_id(name, conn=db) or
                (TUNNELING_COUNTER_GUID if name == "tunneling" else ""))
            .lower())


def _champion_owner(context, uid):
    """Return the controller id owning a champion SessionCardId, else None.

    Champion counters live in the persisted battle state keyed by champion
    identity, and every later projection (owner UID, template, health) is
    derived from this owner id.  The controller must therefore come from the
    PvP champion map or the PvE handler fields; converting a participant UID
    with ``int()`` is undefined for ``game_engine.UID`` and previously raised
    TypeError, dropping every champion counter into the card path.
    """
    from .runtime_helpers import champion_owner_id
    return champion_owner_id(context.handler, context.bstate, uid)


def _secret(guid):
    try:
        from gamedata import DEFAULT_RECORD_STORE
        record = DEFAULT_RECORD_STORE.get("CardCounterTemplate", guid)
        return bool(record and int(record.field("m_Secret", 0) or 0))
    except (AttributeError, TypeError, ValueError):
        return False


def _project(context, uid, owner, guid, old, new, champion=False):
    from .runtime_helpers import card_collection_for_location, owner_uid
    recipient = owner_uid(owner, context.player_uid, context.ai_uid,
                          context.bstate)
    scid = game_engine.SessionCardId(game_engine.UID(int(uid)))
    if champion:
        template = (getattr(context.handler, "_player_champ_guid", None)
                    if owner == context.bstate.get("pids", [owner])[0]
                    else getattr(context.handler, "_ai_champ_guid", None))
        # The champion HUD reads its effect icons (Stealth, Burning, Dazed,
        # Vulnerable) from the cached CardRepresentation counters, and the
        # client only stores those from a CardUpdated.  Champions sit in the
        # cache as collection None (the battle-start seed), and a Champions
        # collection here would be both suppressed by ``push_card_updated``
        # and read as a CardMoved, so keep the collection the client already
        # has while carrying the new counters.
        collection = game_engine.ECardCollections.None_
        card_type = game_engine.ECardTypes.Champion
        health = int(context.bstate.get(
            ((context.bstate.get("pvp_health_map") or {}).get(owner) or
             f"hp_{owner}"), 20))
        context.game.push_card_updated(
            scid, recipient, collection, card_type,
            template_id=template, defense=health, counters={guid: int(new)},
            secret_counter_guids={guid} if _secret(guid) else set(),
            secret_counter_owner_uid=recipient)
    else:
        from pvp_db import (db_card_source_info, db_card_owner_id,
                            db_card_mutation_field)
        row = db_card_source_info(context.session.session_id, int(uid), conn=context.db)
        if not row:
            return
        # ``db_card_source_info`` returns (template, type, location, owner);
        # the persisted counters live in ``permanent_buffs`` and must be read
        # through the typed mutation accessor.  Indexing row[4] raised
        # IndexError and aborted the turn-start (Tunneling) lifecycle.
        try:
            counts = json.loads(db_card_mutation_field(
                context.session.session_id, int(uid), "permanent_buffs",
                conn=context.db) or "{}").get("counters", {})
        except (TypeError, ValueError, json.JSONDecodeError):
            counts = {}
        counts = counts if isinstance(counts, dict) else {}
        encoded = {_counter_guid(context.db, str(name)): int(value or 0)
                   for name, value in counts.items()}
        tpl, card_type, _name, cost, attack, defense, gems = \
            context.handler._card_full_data(context.game, scid, row[0])
        owner = db_card_owner_id(context.session.session_id, int(uid), conn=context.db)
        # An underground card's Tunneling threshold is public progress: the
        # client draws the tunneling counter only when it has this int attr,
        # including on an opponent's face-down copy.
        tunneling_attrs = {}
        if str(row[2]).lower() == "underground":
            from .tunneling import tunneling_value, _saved_int_attrs
            threshold = tunneling_value(
                context.db, row[0],
                _saved_int_attrs(context.db, context.session.session_id, uid))
            if threshold > 0:
                tunneling_attrs = {"tunneling": threshold,
                                   "int_attrs": {"Tunneling": threshold}}
        context.game.push_card_updated(
            scid, owner_uid(owner if owner is not None else 0,
                            context.player_uid, context.ai_uid, context.bstate),
            card_collection_for_location(row[2]),
            game_engine.card_type_from_db(card_type),
            template_id=tpl, cost=cost, attack=attack, defense=defense,
            gems=gems, counters=encoded, nulling=str(row[2]).lower() == "deck",
            **tunneling_attrs,
            secret_counter_guids={key for key in encoded if _secret(key)},
            secret_counter_owner_uid=owner_uid(owner or 0,
                                               context.player_uid,
                                               context.ai_uid, context.bstate))
    # Tunneling progress is public (the opponent sees its underground counter),
    # so its class-54 refresh goes to both players; other counters stay private.
    public_counter = str(guid).lower() == TUNNELING_COUNTER_GUID
    context.game.push_card_counters_changed(
        scid, game_engine.ResourceId.from_str(guid), int(new), int(old),
        private_player_uid=None if public_counter else recipient)


def project_counter_cost_change(context, source_uid, counter_guid,
                               old_value, new_value):
    """Project one counter delta already applied as an activation cost."""
    source_uid = int(source_uid)
    owner = _champion_owner(context, source_uid)
    is_champion = owner is not None
    if not is_champion:
        from pvp_db import db_card_owner_id
        owner = db_card_owner_id(
            context.session.session_id, source_uid, conn=context.db)
    _project(context, source_uid, owner if owner is not None else 0,
             str(counter_guid or "").lower(), int(old_value),
             int(new_value), champion=is_champion)


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
    context._emit_trigger(
        "CardCounteredEvent", target,
        context.bstate.get("resolving_owner_id", 0),
        event_source_collection="CastSpells",
        event_destination_collection="discard")
    return f"countered {hex(target)}"


def _emit_counter_added(context, target, name, old, new, owner):
    """``CounterAddedToCardEvent`` for a gain, typed by the counter's name.

    Cards with a "when this gains a seed counter" trigger (a Seed Pod at three)
    only fire on this event, so every native counter gain must emit it.
    """
    if new <= old:
        return
    from rules_port.tac import _tac_attr_hash
    context._emit_trigger(
        "CounterAddedToCardEvent", target, owner,
        event_tac={_tac_attr_hash("GainedCounterType"): name})


_REMOVING_OPERATIONS = ("remove", "subtract")
_CLEARING_OPERATIONS = ("clear", "removeall")


def next_counter_value(old, operation, amount):
    """The value a counter takes after one operation, as C# CounterModifier.Apply computes it: a count
    never goes below zero, and clearing takes it to zero."""
    amount = int(amount or 0)
    if operation == "set":
        return amount
    if operation in _CLEARING_OPERATIONS:
        return 0
    if operation in _REMOVING_OPERATIONS:
        return max(0, old - amount)
    return max(0, old + amount)


def _dazed_change_blocked(context, target, name, operation):
    """Whether the target's CantAddDazedCounters or CantRemoveDazedCounters int attribute blocks this change.

    C# CounterModifier.Apply returns before a Dazed add or remove when the card context has the attribute.
    Clearing and setting are not blocked there, so they are not here either."""
    if name != "dazed":
        return False
    if operation in _REMOVING_OPERATIONS:
        attribute = "CantRemoveDazedCounters"
    elif operation in _CLEARING_OPERATIONS or operation == "set":
        return False
    else:
        attribute = "CantAddDazedCounters"
    from .damage_effects import _int_attr
    return _int_attr(context, target, attribute) > 0


def change_counter(context, target, name, counter_guid, amount, operation):
    """Apply one typed counter operation and emit its client projection.

    The change follows C# CounterModifier.Apply: an operation that leaves the count where it is is a
    no-op (nothing is written, projected or emitted), a Dazed add or remove the card blocks is a no-op,
    and the count never goes below zero. Returns ``(old, new)``."""
    from pvp_db import db_card_mutation_field, db_set_card_mutation_field
    target = int(target)
    from .statistics import add_ability_stat
    name = str(name or "counter").lower()
    guid = str(counter_guid or _counter_guid(context.db, name)).lower()
    champion_owner = _champion_owner(context, target)
    if champion_owner is not None:
        values = context.bstate.get("champion_counters", {}).get(str(target), {})
        old = int(values.get(guid, 0) or 0)
    else:
        raw = db_card_mutation_field(context.session.session_id, target,
                                     "permanent_buffs", conn=context.db)
        try:
            data = json.loads(raw or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            data = {}
        counters = data.setdefault("counters", {})
        old = int(counters.get(name, 0) or 0)
    new = next_counter_value(old, operation, amount)
    if new == old or _dazed_change_blocked(context, target, name, operation):
        return old, old
    if champion_owner is not None:
        values = context.bstate.setdefault("champion_counters", {}).setdefault(str(target), {})
        if new:
            values[guid] = new
        else:
            values.pop(guid, None)
        _project(context, target, champion_owner, guid, old, new, champion=True)
        if new < old:
            add_ability_stat(context.bstate, "CountersRemoved", old - new)
        _emit_counter_added(context, target, name, old, new, champion_owner)
        return old, new
    if new:
        counters[name] = new
    else:
        counters.pop(name, None)
    data.setdefault("counter_guids", {})[name] = guid
    db_set_card_mutation_field(context.session.session_id, target,
                               "permanent_buffs", json.dumps(data), conn=context.db)
    context.db.commit()
    from pvp_db import db_card_owner_id
    owner = db_card_owner_id(context.session.session_id, target, conn=context.db)
    _project(context, target, owner if owner is not None else 0, guid, old, new)
    if new < old:
        add_ability_stat(context.bstate, "CountersRemoved", old - new)
    _emit_counter_added(context, target, name, old, new, owner)
    return old, new
