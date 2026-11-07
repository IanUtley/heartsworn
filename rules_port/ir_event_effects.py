"""Interpreter leaves for the TAC, event-firing and bookkeeping effects (gap area N).

``FireEventEffectTemplate`` and ``TACAbilityEffectTemplate`` run through the native RulesPort leaves
(``rules_port.native_leaves.dispatch``), so the interpreter does what the resolver does: the same event is fired and the
same TAC operation runs. The resolver calls a card-scoped leaf once per target, and once with no target when there
is none. This module does the same, and restores the target aliases the leaves read once it is done.
"""

from __future__ import annotations

import rules_port.leaves_modifiers as _leaves_modifiers
import rules_port.leaves_variables as _leaves_variables
import json
from typing import Any, cast

import game_engine

from .runtime_helpers import raw_uid

# The effect templates this module runs through the native leaves. The bookkeeping ones remember a name, a keyword's
# powers or a registered trigger on the battle state, and the keyword one activates the target's triggered ability.
NATIVE_LEAF_EFFECTS = frozenset({
    "FireEventEffectTemplate", "TACAbilityEffectTemplate",
    "StoreNameAbilityEffectTemplate", "RememberKeywordPowersEffectTemplate",
    "RegisterTriggerAbilityEffectTemplate", "ActivateTriggeredAbilityEffectTemplate",
    # Copies the played spell and resolves the copy's authored abilities (rules_port.ir_event_effects.create_and_cast_spell).
    "CreateAndCastSpellAbilityEffectTemplate",
    # Presentation only: pushes the client animation for a talent or power (rules_port.ir_event_effects._animation_trigger).
    "AnimationTriggerEffectTemplate",
    # Records one typed entry in a named list of the ability (a shard pick's ChosenShards; rules_port.native_leaves).
    "StoreListAttrAbilityEffectTemplate",
    # Loads the authored deck template for the controller and shuffles it (rules_port.deck_effects.load_player_deck).
    "LoadPlayerDeckAbilityEffectTemplate",
    # Queues a copy of the activated ability (the one in battle state's card_activated_item), rules_port.ir_event_effects._copy_ability.
    "CopyAbilityEffectTemplate",
    # Offers the choice cards for an AI (chosen at once) or a player (the interpreter pauses for the player's answer,
    # see rules_port.ir_exec._pause_for_double_choice). rules_port.choice_effects.double_choice.
    "DoubleChoiceAbilityEffectTemplate",
    # Finishes a card's move into the warzone (the card's entry bookkeeping; rules_port.native_leaves dispatch).
    "FinishMovingCardToWarzoneEffectTemplate",
    # The card-play template's two effects: play the card (the native play machinery queues it on the chain) and finish
    # its resolution (rules_port.leaves_abilities.play_card, and the finish leaf).
    "BuiltInPlayCardAbilityEffectTemplate", "FinishResolvingCardAbilityEffectTemplate",
})

# The TAC operations the native leaf (EffectContext.tac) implements. A TAC operation outside this set is refused by
# coverage, so its ability is never half-run.
TAC_FUNCTIONS = frozenset({
    "AppendToList", "CycleCardArt", "DepleteResources", "Escalate", "ForgetAllCards",
    "GainTargetsThresholds", "GainTargetsProvidedThresholds", "MoveInDeck", "RemoveFromStoredTargetsAbility",
    "ReplaceCardInCollection", "RevokeBaseAbilities", "ShiftAbility", "Tame",
})

# The battle-state aliases a target is published under for the leaves (the resolver sets and clears the same keys).
_TARGET_KEYS = ("resolving_target_uid", "player_mod_target", "player_spell_target", "grant_target")


def tac_function_of(effect: Any) -> str:
    """The TAC operation a TACAbilityEffectTemplate names, read from its serialized TAC (or "" when it has none)."""
    from rules_port.tac import tac_function
    serialized = effect.get("m_SerializedTAC") if isinstance(effect, dict) else None
    if isinstance(serialized, dict):
        serialized = serialized.get("data")
    return tac_function(serialized or "")


def run_native_leaf(context: Any, effect_type: str, effect_raw: dict[str, Any], targets: list[int]) -> None:
    """Run one effect template through its native leaf, once per target (or once with no target)."""
    from rules_port.native_leaves import dispatch
    bstate = context.bstate
    previous = {key: bstate.get(key) for key in _TARGET_KEYS}
    try:
        candidates: list[int | None] = list(targets) if targets else [None]
        for target in candidates:
            if target is None:
                for key in _TARGET_KEYS:
                    bstate.pop(key, None)
                child = context.nested_effect_context(effect_raw, "", ())
            else:
                for key in _TARGET_KEYS:
                    bstate[key] = int(target)
                child = context.nested_effect_context(effect_raw, "", (int(target),))
            result = dispatch(effect_type, child)
            if result is None:
                raise RuntimeError(f"no native leaf for {effect_type}")
    finally:
        for key, value in previous.items():
            if value is None:
                bstate.pop(key, None)
            else:
                bstate[key] = value


def coverage_of(kind: str, effect: dict[str, Any]) -> list[str]:
    """The constructs of one of these effect templates (``kind``, its short class name) the interpreter does not run:
    empty when it runs all of them."""
    if kind == "TACAbilityEffectTemplate":
        function = tac_function_of(effect)
        if function not in TAC_FUNCTIONS:
            return [f"TAC {function or 'untyped'}"]
    return []


def _copy_ability(context):
    """Queue a copy of the activated ability through the port host seam."""
    original = (context.bstate or {}).get("card_activated_item")
    if not original or not original.get("ability_guid"):
        return "copy ability: no activated ability"
    import game_engine
    from . import chain

    instance_id = int((context.bstate or {}).get("_next_instance_id", 1))
    context.bstate["_next_instance_id"] = instance_id + 1
    copied = {
        "kind": "ability", "ability_guid": original["ability_guid"],
        "source_uid": original.get("source_uid"),
        "target_uid": original.get("target_uid"),
        "instance_id": instance_id,
        "activation_data": dict(original.get("activation_data") or {}),
    }
    chain.push(context.bstate, copied)
    source = original.get("source_uid")
    if source is not None:
        activation = copied["activation_data"]
        target_map = activation.get("target_map") or {}
        target_ids = []
        for selected in target_map.values() if isinstance(target_map, dict) else ():
            selected = selected if isinstance(selected, (tuple, list, set)) else (selected,)
            for value in selected:
                try:
                    target_ids.append(raw_uid(value))
                except (TypeError, ValueError):
                    continue
        context.game.push_ability_on_chain(
            game_engine.SessionCardId(game_engine.UID(int(source))),
            game_engine.ResourceId.from_str(str(original["ability_guid"])),
            ability_instance_id=instance_id,
            target_card_ids=target_ids)
    return f"copied ability {str(original['ability_guid'])[:8]}"


def grant_ability(context):
    """Apply a typed ability grant to a session card or champion."""
    import game_engine
    from gamedata import DEFAULT_RECORD_STORE, ability_graph
    from pvp_db import (db_ability_metadata_exists, db_card_grant_info,
                        db_set_card_abilities)
    from .granted_abilities import is_zero_guid
    try:
        granted = str(context.param or "").lower()
    except Exception:
        granted = ""
    if is_zero_guid(granted):
        # No typed template: the abilities come from what the resolving ability remembered, or from one of the
        # effect's power-source flags below (sockets, payments, remembered powers, a random pick).
        remembered = (context.bstate.get("remembered_powers") or {})
        values = remembered.get(context.bstate.get("resolving_ability"), [])
        granted_values = [str(value).lower() for value in values]
    else:
        granted_values = [granted]
    if not granted_values:
        typed = str(context.template_value(
            "m_GrantedAbilityTemplateId", "") or "").lower()
        if not is_zero_guid(typed):
            granted_values = [typed]
    target = context.bstate.get("grant_target")
    if target is None:
        target = context.resolved_target()
    if target is None:
        return "grant: no target"
    template = context.template_value("m_AbilityIsUnique", True)
    unique = bool(template)

    # Port of GrantAbilityEffectTemplate.Apply's typed power sources.  The
    # random powers are picked once per ability instance (the client caches
    # the choice in the ability instance's RandomPowerTemplate string).
    source_uid = context.bstate.get("resolving_source_uid")
    random_inspire = bool(context.template_value("m_RandomInspirePower", False))
    random_charge = bool(context.template_value(
        "m_RandomChampionChargePower", False))
    all_socket_source = bool(context.template_value(
        "m_AllSocketedPowersOfSource", False))
    all_socket_target = bool(context.template_value(
        "m_AllSocketedPowersOfTarget", False))
    all_socket_master = bool(context.template_value(
        "m_AllSocketedPowersOfMyMaster", False))
    all_pay_source = bool(context.template_value(
        "m_AllPaymentPowersOfSource", False))
    all_pay_target = bool(context.template_value(
        "m_AllPaymentPowersOfTarget", False))
    all_remembered = bool(context.template_value("m_AllRememberedPowers", False))
    if (random_inspire or random_charge) and not granted_values:
        # Keyed by the resolving activation's instance (as C# stores it on the
        # ability instance): it survives a pause/continuation of this
        # activation and is dropped when the activation finishes.
        instance_values = context.bstate.setdefault(
            "ability_runtime_state", {}).setdefault(
                str(int(context.bstate.get("resolving_ability_instance_id")
                        or 0)), {})
        picked = instance_values.get("RandomPowerTemplate")
        if not picked:
            from pvp_db import (db_champion_charge_power_guids,
                                db_inspire_ability_guids)
            pool = (db_inspire_ability_guids(conn=context.db) if random_inspire
                    else db_champion_charge_power_guids(conn=context.db))
            if pool:
                rng = context.bstate.get("_rules_rng")
                if rng is not None and hasattr(rng, "next"):
                    picked = pool[int(rng.next(len(pool))) % len(pool)]
                else:
                    import random as _random
                    picked = pool[_random.randrange(len(pool))]
                instance_values["RandomPowerTemplate"] = picked
        if picked:
            granted_values = [str(picked).lower()]
    if (all_socket_source or all_socket_target or all_socket_master or
            all_pay_source or all_pay_target or all_remembered):
        from pvp_db import (db_card_gem_ability_guids,
                            db_card_manual_ability_guids)
        values = set(granted_values)
        if source_uid is not None and (all_socket_source or all_socket_master):
            # ParentLink.GemAbilities is the master's socketed powers.  The
            # master link is not materialized server-side, so the source's own
            # socketed gems are the available authored set.
            values.update(db_card_gem_ability_guids(
                context.session.session_id, int(source_uid), conn=context.db))
        if all_socket_target:
            values.update(db_card_gem_ability_guids(
                context.session.session_id, int(target), conn=context.db))
        if source_uid is not None and all_pay_source:
            values.update(db_card_manual_ability_guids(
                context.session.session_id, int(source_uid), conn=context.db))
        if all_pay_target:
            values.update(db_card_manual_ability_guids(
                context.session.session_id, int(target), conn=context.db))
        if all_remembered:
            remembered = context.bstate.get("remembered_powers") or {}
            for guid in remembered.get(
                    context.bstate.get("resolving_ability"), []):
                if guid:
                    values.add(str(guid).lower())
        granted_values = list(dict.fromkeys(
            str(value).lower() for value in values if value))
    # C# grants "powers of the target" to the ability's source card (Mega-Bot
    # 9000, Gemborn Prowler); every other source grants to the target.
    destination = target
    if (all_socket_target or all_pay_target) and source_uid is not None:
        destination = source_uid
    duration = context.effect_duration
    grant_owner = int(context.bstate.get("resolving_owner_id", 0) or 0)

    def record_duration_bound_grants(added_values, target_owner):
        if not added_values:
            return
        from .effect_lifetimes import record_grant
        expiration_owner = grant_owner
        if duration == "BeginningOfOpponentsTurn":
            if context.bstate.get("pvp"):
                expiration_owner = next((int(pid) for pid in
                    context.bstate.get("pids", ())
                    if int(pid) != grant_owner), 0)
            else:
                profile = getattr(context.handler, "user_profile", None) or {}
                player_id = int(profile.get("id", 0) or 0)
                expiration_owner = 0 if grant_owner else player_id
        elif duration == "AfterCardsReadyOnPlayersTurn":
            expiration_owner = int(target_owner or grant_owner)
        for granted_guid in added_values:
            record_grant(
                context.bstate, source_uid=source_uid,
                target_uid=int(destination), ability_guid=granted_guid,
                duration=duration, owner_id=grant_owner,
                target_owner_id=target_owner,
                expiration_owner_id=expiration_owner)

    # Champions are represented in the client session by SessionCardId, but
    # deliberately have no ``game_cards`` row.  Encounter setup cards can
    # grant an ability to a champion (for example Cockatwice grants its
    # opposing champion the GameStarted Taming Sphere summon), so retain
    # those abilities on the handler's per-battle champion list.
    profile = getattr(context.handler, "user_profile", None) or {}
    player_owner = int(profile.get("id", 0) or 0)
    for attr, owner in (("_player_champ_scid", player_owner),
                        ("_ai_champ_scid", 0)):
        champion = getattr(context.handler, attr, None)
        try:
            champion_uid = int(getattr(cast(Any, champion), "uid").uid64)
        except (AttributeError, TypeError, ValueError):
            continue
        if champion_uid != int(target):
            continue
        dynamic = getattr(context.handler,
                          "_champion_granted_ability_guids", None)
        if dynamic is None:
            dynamic = context.handler._champion_granted_ability_guids = {}
        abilities = dynamic.setdefault(champion_uid, [])
        added = []
        for guid in granted_values:
            if (not db_ability_metadata_exists(guid, conn=context.db) and
                    ability_graph(DEFAULT_RECORD_STORE, guid) is None):
                continue
            if unique and guid in abilities:
                continue
            abilities.append(guid)
            added.append(guid)
        record_duration_bound_grants(
            added, context.target_owner(int(destination), default=grant_owner))
        # A hidden encounter setup card can grant a GameStarted ability while
        # the event is already traversing the opposing side.  That champion's
        # normal discovery pass has then finished, so run the newly granted
        # authored trigger now instead of losing its one setup opportunity.
        # A permanent CardModifier with a card-filter target is a continuous
        # champion aura.  Resolve its current board effect at the same point;
        # later CardEnteredZoneEvent dispatches keep that aura current.
        if (added and context.bstate.get("event_type") ==
                "GameStartedEvent"):
            from rules_port.resolution import resolve_port_ability
            for guid in added:
                graph = ability_graph(DEFAULT_RECORD_STORE, guid)
                continuous_card_modifier = bool(graph and not graph.trigger_event_type and
                    any(effect.concrete_type == "CardModifierAbilityEffectTemplate" and
                        str(effect.duration).lower() == "permanent"
                        for effect in graph.effects) and
                    any(target.card_filter for target in graph.targets))
                if (graph is not None and
                    (str(graph.trigger_event_type or "").rsplit(
                        ".", 1)[-1] == "GameStartedEvent" or
                     continuous_card_modifier)):
                    from .effect_lifetimes import mark_gamestart_grant
                    if not mark_gamestart_grant(context.bstate, champion_uid, guid):
                        continue
                    resolve_port_ability(
                        context.handler, context.game, context.session,
                        context.db, context.player_uid, context.ai_uid,
                        context.bstate, guid, champion_uid, owner,
                        target_map={})
        return f"granted {len(added)} champion ability(s) to {hex(champion_uid)}"

    row = db_card_grant_info(
        context.session.session_id, int(destination), conn=context.db)
    if not row:
        return "grant: target card not found"
    try:
        abilities = json.loads(row[0] or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        abilities = []
    added = []
    for guid in granted_values:
        if (not db_ability_metadata_exists(guid, conn=context.db) and
                ability_graph(DEFAULT_RECORD_STORE, guid) is None):
            continue
        if unique and guid in abilities:
            continue
        abilities.append(guid)
        added.append(guid)
    record_duration_bound_grants(
        added, context.target_owner(int(destination), default=grant_owner))
    db_set_card_abilities(
        context.session.session_id, int(destination), json.dumps(abilities),
        conn=context.db)
    context.db.commit()
    if random_inspire:
        # C# sets the Inspire IntAttr on the granted card in the target loop.
        from pvp_db import db_card_mutation_field, db_set_card_mutation_field
        try:
            buffs = json.loads(db_card_mutation_field(
                context.session.session_id, int(target), "permanent_buffs",
                conn=context.db) or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            buffs = {}
        if not isinstance(buffs, dict):
            buffs = {}
        attrs = buffs.get("int_attrs")
        if not isinstance(attrs, dict):
            attrs = {}
        previous_inspire = int(attrs.get("Inspire", 0) or 0)
        attrs["Inspire"] = 1
        buffs["int_attrs"] = attrs
        db_set_card_mutation_field(
            context.session.session_id, int(target), "permanent_buffs",
            json.dumps(buffs), conn=context.db)
        context.db.commit()
        _leaves_modifiers.emit_int_attribute_gained(context,
            int(target), "Inspire", previous_inspire, 1)
    scid = game_engine.SessionCardId(game_engine.UID(int(destination)))
    from .runtime_helpers import card_collection_for_location, owner_uid
    owner = owner_uid(row[2], context.player_uid, context.ai_uid,
                      context.bstate)
    tpl, card_type, _name, cost, attack, defense, gems = \
        context.handler._card_full_data(context.game, scid, row[1], None)
    context.game.push_card_updated(
        scid, owner, card_collection_for_location(row[3]),
        game_engine.card_type_from_db(card_type)
        if isinstance(card_type, str) else card_type,
        attack=attack, defense=defense, cost=cost,
        state=int(row[4] or 0), template_id=tpl, gems=gems,
        nulling=(row[3] == "deck"))
    return f"granted {len(added)} ability(s) to {hex(int(destination))}"


def create_and_cast_spell(context):
    """Copy the selected spell and resolve its authored abilities natively.

    ``m_AmountField`` is the typed copy count (C# ``CopyCardAndPutOnChain``
    runs once per amount); the typed single-copy form defaults to one.
    ``m_SendPlayAction`` is a client-side presentation flag and does not alter
    the server's chain behavior.
    """
    from pvp_db import (db_card_zone_details, db_copy_template_payload,
                        db_next_game_card_row_id, db_insert_generated_card,
                        db_discard_card)
    from .runtime_helpers import next_game_card_uid
    target = (context.bstate.get("card_cast_copy_target") or
              context.resolved_target())
    if target is None:
        return "copy spell: no target"
    row = db_card_zone_details(
        context.session.session_id, int(target), conn=context.db)
    if not row:
        return "copy spell: target template missing"
    template_guid = row[0]
    payload = db_copy_template_payload(template_guid, conn=context.db)
    if not payload:
        return "copy spell: template not found"
    try:
        amount = int(context.value("m_AmountField", 1) or 1)
    except (TypeError, ValueError):
        amount = 1
    amount = max(1, min(amount, 20))
    owner = int(context.bstate.get("resolving_owner_id", 0) or 0)
    try:
        ability_guids = [str(value).lower() for value in json.loads(
            payload[1] or "[]") if value]
    except (TypeError, ValueError, json.JSONDecodeError):
        ability_guids = []
    from rules_port.resolution import resolve_port_played_spell
    copied = 0
    for _ in range(amount):
        card_uid = next_game_card_uid(context.db, context.session.session_id)
        db_insert_generated_card(
            context.session.session_id, owner, card_uid, template_guid,
            "CastSpells", payload[0], payload[1], payload[2],
            db_next_game_card_row_id(context.session.session_id, conn=context.db),
            conn=context.db)
        context.db.commit()
        scid = game_engine.SessionCardId(game_engine.UID(card_uid))
        player = context.player_uid if owner else context.ai_uid
        _tpl, card_type, _name, cost, attack, defense, gems = \
            context.handler._card_full_data(context.game, scid, template_guid)
        context.game.push_card_updated(
            scid, player, game_engine.ECardCollections.CastSpells, card_type,
            template_id=template_guid, cost=cost, attack=attack,
            defense=defense, gems=gems)
        resolve_port_played_spell(
            context.game, context.session, context.db, context.handler,
            context.player_uid, context.ai_uid, context.bstate, ability_guids)
        db_discard_card(
            context.session.session_id, card_uid, connection=context.db)
        copied += 1
        # A copy that opened a picker/continuation must finish before the
        # next copy is created, exactly like one chain item at a time.
        if context.bstate.get("resolution_paused"):
            break
    return f"copied+cast {template_guid[:8]} x{copied}"


def _animation_trigger(context):
    trigger = context.template_value("m_AnimationTrigger", "Invalid")
    values = {"Invalid": 0, "CannonTalent": 1, "MageTalent": 2,
              "WarriorTalent": 3, "ClericTalent": 4, "RangerTalent": 5,
              "Kraken": 8}
    value = values.get(str(trigger).rsplit(".", 1)[-1], 0)
    if value:
        context.game.push_animation_trigger(value)
    return f"animation trigger {trigger}"


def _store_list_attr(context):
    list_name = str(context.template_value("m_ListAttrName", "") or "")
    attr_name = str(context.template_value("m_IntAttrName", "") or "")
    value = int(context.template_value("m_IntAttrValue", 0) or 0)
    return _leaves_variables.store_list_attr(context,
        list_name, attr_name, value,
        set_list=bool(context.template_value("m_Set", False)),
        until_end_of_turn=bool(
            context.template_value("m_OnlyUntilEndOfTurn", False)))
