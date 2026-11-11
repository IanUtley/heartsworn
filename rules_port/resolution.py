"""RulesPort-owned ability resolution boundary."""

from __future__ import annotations

from .picks import INTERPRETED_CHOICE_ZONE
from .cost_cards import _cost_card_lists
from .interpreted_prompts import prompt_interpreted_reveal
from .target_values import (
    _answers_from_target_map, _list_target_values, _match_secondary_values, _random_target_sample,
    _referenced_effect_target_index, _resolved_effect_target_values)
from .runtime_helpers import as_int, raw_uid

import json
from typing import Any

from .actions import AbilityResolutionState


def _log_ability_start(handler, db, ability_guid, source_uid, owner_id,
                       target_map):
    """Write one readable trace line for every RulesPort ability resolution."""
    try:
        from pvp_db import db_ability_game_text, db_ability_raw_json
        import json
        raw = db_ability_raw_json(ability_guid, conn=db)
        record = json.loads(raw or "{}") if raw else {}
        name = record.get("m_Name") or db_ability_game_text(
            ability_guid, conn=db) or str(ability_guid)
    except Exception:
        name = str(ability_guid)
    targets = []
    for value in (target_map or {}).values():
        if isinstance(value, dict):
            value = value.get("value", value.get("uid64", value))
        try:
            targets.append(hex(int(value)))
        except (TypeError, ValueError):
            targets.append(str(value))
    getattr(handler, "_log_req", print)(
        f"    Ability resolve: {name} ({str(ability_guid)[:8]}) "
        f"source={hex(int(source_uid)) if source_uid is not None else None} "
        f"owner={owner_id} targets={targets}")






















def _choose_ai_explicit_target_map(handler, session, battle_state,
                                   player_uid, ai_uid, ability):
    """Ask the native AI evaluator for a fresh explicit target map.

    C# rebuilds an activation for every triggered ability.  The Python
    resolver can enter here with an empty map for a Runic/nested child, so a
    first-legal-target fallback would incorrectly reuse the old target.  A
    ``None`` result means the evaluator could not classify the ability; the
    caller may then retain its compatibility fallback.  An empty mapping is
    authoritative and means there is no legal selected target.
    """
    if int(getattr(ability, "responsible_player_id", 0) or 0) != 0:
        return None
    try:
        from ai_eval import build_evaluator
        evaluator = build_evaluator(
            handler, session, battle_state, ai_uid, player_uid,
            ai_owner_id=0)
        chooser = getattr(evaluator, "choose_ability_target_map", None)
        if not callable(chooser):
            return None
        selected = chooser(
            getattr(ability, "source_uid", None),
            getattr(ability, "ability_template_id", ""))
        if selected is None or not isinstance(selected, dict):
            return None
        normalised = {}
        for index, values in selected.items():
            try:
                index = int(index)
            except (TypeError, ValueError):
                continue
            if not isinstance(values, (tuple, list, set)):
                values = (values,)
            targets = []
            for value in values:
                try:
                    if value is not None:
                        targets.append(int(value))
                except (TypeError, ValueError):
                    continue
            if targets:
                normalised[index] = tuple(targets)
        return normalised
    except Exception:
        # Target selection must not make an otherwise resolvable ability fail
        # because a legacy/incomplete AI snapshot is unavailable.
        return None






class NativeEffectBackend:
    """Walk one typed ability without entering the legacy BOM resolver."""

    def __call__(self, *, handler, game, session, db, player_uid, ai_uid,
                 battle_state, ability, resume_from_order=None,
                 native_effect=None, effect_groups=None, event_tac=None):
        if native_effect is None:
            from .native_leaves import dispatch
            native_effect = dispatch
        from .native_leaves import SELF_TARGETED_EFFECTS
        from rules_port.context import EffectContext
        from rules_port.conditions import ConditionContext, evaluate_effect_condition
        # Opt-in resolver trace: per-leaf card/event deltas for audits.
        from .trace import begin_effect, end_effect

        old = {key: battle_state.get(key) for key in (
            "resolving_ability", "resolving_source_uid",
            "resolving_owner_id", "resolving_target_uid",
            "resolving_responsible_player_id", "resolving_effect_order",
            "resolving_effect_guid", "resolving_secondary_target_uid",
            "ability_target_map", "_rules_port_native_effect",
            "resolving_ability_instance_id", "_ability_damage_dealt",
            "ability_variables", "session_id", "player_mod_target",
            "player_spell_target", "grant_target", "_skip_transform",
            "rules_port_resolution_depth")}
        # An ability's list attrs (``VoidedCards`` and friends) belong to one
        # resolution: a nested child must see its parent's list, and a
        # finished resolution must not leave its list behind for the next
        # activation to sum a second time.  Mirrors the legacy resolver's
        # save/restore of ``ability_lists``.
        previous_lists = battle_state.get("ability_lists")
        if isinstance(previous_lists, dict):
            # Copy the flat list values too: restoring the dict alone would
            # keep the lists this resolution appended to.
            previous_lists = {
                key: (list(value) if isinstance(value, list) else value)
                for key, value in previous_lists.items()}
        # The legacy resolver guards pathological ability cycles with a depth
        # cap; mirror it so a malformed child chain cannot recurse forever.
        depth = int(battle_state.get("rules_port_resolution_depth", 0) or 0)
        if depth > 16:
            return "resolution depth exceeded"
        # Effect instance ids are only unique within one ability, so a
        # top-level activation starts a fresh m_WasApplied map.  A continuation
        # resumes the same instance and keeps its flags, while a nested child
        # runs against its own map that is restored to the parent's on exit.
        # This mirrors the legacy resolver's per-call local ``applied`` dict;
        # sharing one flat map let an earlier ability's flags satisfy a later
        # ability's contingencies.
        previous_applied = None
        if depth > 0:
            previous_applied = battle_state.get("applied_effects")
            battle_state["applied_effects"] = {}
        elif resume_from_order is None:
            battle_state["applied_effects"] = {}
        battle_state["rules_port_resolution_depth"] = depth + 1
        # Card-count/sum variables evaluate against the live game_cards rows,
        # and ``rules_port.fields.effect_field`` reads the session id from the
        # battle state to query them.  Without it a "for each ..." amount
        # resolved against session 0 and silently became zero (Woeful Webbing
        # summoned no Spider).  The legacy resolver sets the same key; mirror
        # it here, then restore the caller's value on exit.
        battle_state["session_id"] = int(session.session_id)
        for list_name, paid in _cost_card_lists(ability).items():
            # Union with anything the host's cost payment already recorded
            # for this activation; the activation's own cost map survives the
            # chain round trip even when the host's flat list did not (PvP
            # Bun'jitsu summoned a bare 3/3 Abomination).
            lists = battle_state.setdefault("ability_lists", {})
            if list_name == "ExhaustedCards":
                lists[list_name] = list(paid)
                continue
            current = lists.get(list_name)
            current = list(current) if isinstance(current, list) else []
            current.extend(uid for uid in paid if uid not in current)
            lists[list_name] = current
        battle_state["resolving_ability"] = ability.ability_template_id
        battle_state["resolving_source_uid"] = ability.source_uid
        battle_state["resolving_owner_id"] = int(
            ability.responsible_player_id or 0)
        battle_state["resolving_responsible_player_id"] = int(
            ability.responsible_player_id or 0)
        battle_state["ability_target_map"] = dict(
            getattr(ability.activation, "target_map", {}) or {})
        # EffectInputVariable.GetValue resolves through the active C# ability
        # instance: explicit activation values win, with authored variable
        # defaults as fallback. Target filters use this same scope while the
        # resolver computes automatic targets, so expose it for the duration
        # of this ability and restore a nested parent's scope on exit.
        from .fields import ability_variables
        active_variables = ability_variables(ability)
        active_variables.update(
            getattr(ability.activation, "variables", {}) or {})
        battle_state["ability_variables"] = active_variables
        runtime_instance_id = int(getattr(ability, "instance_id", 0) or 0)
        instance_key = str(runtime_instance_id)
        runtime_values = battle_state.setdefault("ability_runtime_state", {})
        instance_values = runtime_values.setdefault(instance_key, {})
        battle_state["resolving_ability_instance_id"] = runtime_instance_id
        battle_state["_ability_damage_dealt"] = int(
            instance_values.get("DamageDealt", 0) or 0)
        from .targeting import legal_targets
        battle_state["_rules_port_native_effect"] = True
        applied = battle_state.setdefault("applied_effects", {})
        champion_targets = ()
        try:
            champion_targets = tuple(
                getattr(handler, "_champion_targets", lambda: [])() or ())
        except Exception:
            champion_targets = ()
        ai_target_map = None
        ai_target_map_attempted = False

        def field(item, name, default=None):
            value = getattr(item, name, None)
            if value is not None:
                return value
            if isinstance(item, dict):
                aliases = {
                    "guid": "effect_guid",
                    "concrete_type": "effect_type",
                    "condition_guid": "condition_id",
                }
                return item.get(name, item.get(aliases.get(name, ""), default))
            return default

        def condition_passes(effect_value, target_value):
            """Evaluate one effect-instance condition the way C# does."""
            condition_id = str(field(effect_value, "condition_guid", ""))
            if not condition_id or condition_id == "0" * 36:
                return True
            secondary_value = field(
                effect_value, "secondary_target_index", -1)
            secondary_index = int(
                -1 if secondary_value is None else secondary_value)
            secondary_target_uids: tuple[int, ...] = ()
            if secondary_index >= 0:
                secondary_target_uids = tuple(
                    resolved_by_instance.get(secondary_index, ()) or ())
                if not secondary_target_uids:
                    def has_instance(candidate):
                        candidate_id = field(
                            candidate, "effect_instance_id", -1)
                        return int(-1 if candidate_id is None else
                                   candidate_id) == secondary_index

                    referenced = next(
                        (candidate for candidate in effects
                         if has_instance(candidate)), None)
                    target_index_value = (field(
                        referenced, "target_index", -1)
                        if referenced is not None else -1)
                    referenced_index = int(
                        -1 if target_index_value is None
                        else target_index_value)
                    raw_targets = (ability.activation.target_map.get(
                        referenced_index, ()) if referenced_index >= 0 else ())
                    if raw_targets is None:
                        raw_targets = ()
                    if not isinstance(raw_targets, (tuple, list, set)):
                        raw_targets = (raw_targets,)
                    secondary_target_uids = tuple(
                        int(value) for value in raw_targets
                        if value is not None)
            condition_context = ConditionContext(
                db, session, battle_state,
                event_type="AbilityEffectEvent",
                ability_source_uid=ability.source_uid,
                ability_source_owner_id=ability.responsible_player_id,
                trigger_uid=target_value,
                extra_target=target_value,
                pl_t=player_uid, ai_t=ai_uid,
                champions=champion_targets,
                event_int_attribute=None,
                event_tac=event_tac,
                effect_secondary_target_uids=secondary_target_uids)
            return bool(evaluate_effect_condition(
                db, condition_id, condition_context))

        def dispatch_nested_effect(parent_context, template, loop_count):
            """Run a typed child as C# Apply(effectInstance) does."""
            from gamedata.semantics import EffectSpec, _effect_param
            from rules_port.fields import _as_dict
            raw = _as_dict(template)
            short_type = str(raw.get("_t", "")).rsplit(".", 1)[-1]
            if not short_type:
                short_type = str(getattr(template, "short_type", "") or "")
            if not short_type:
                return "repeat: invalid nested effect"
            nested_spec = EffectSpec(
                guid=(str(getattr(template, "guid", "") or "") or
                      str(parent_context.effect_guid)),
                concrete_type=short_type,
                operation=short_type.removesuffix("AbilityEffectTemplate")
                or short_type,
                name=str(raw.get("m_Name", "") or ""),
                target_index=-1,
                effect_instance_id=int(battle_state.get(
                    "resolving_effect_order", 0) or 0),
                effect_group_id=0,
                duration=parent_context.effect_duration,
                condition_guid="", optional=False,
                recalculate_targets="UseDefault",
                secondary_target_index=-1,
                output_variables={}, template=template)
            param = _effect_param(nested_spec)
            bases: set[Any] = set()
            for entry in raw.get("_v", ()) or ():
                if isinstance(entry, dict):
                    bases.update(str(name).rsplit(".", 1)[-1]
                                 for name in entry)
            # CardAbilityEffectTemplate.Apply iterates each member of the
            # current target instance. A child which overrides the whole
            # instance (such as another repeat) runs once with that list.
            card_scoped = ("CardAbilityEffectTemplate" in bases and
                           short_type not in SELF_TARGETED_EFFECTS)
            targets = tuple(parent_context.effect_targets or (None,))
            apply_targets = targets if card_scoped else (None,)
            state_keys = ("resolving_target_uid", "player_mod_target",
                          "player_spell_target", "grant_target")
            previous = {key: battle_state.get(key) for key in state_keys}
            results = []
            try:
                for _iteration in range(max(0, int(loop_count or 0))):
                    for target_value in apply_targets:
                        if target_value is None:
                            for key in state_keys:
                                battle_state.pop(key, None)
                        else:
                            target_value = int(target_value)
                            battle_state["resolving_target_uid"] = target_value
                            battle_state["player_mod_target"] = target_value
                            battle_state["player_spell_target"] = target_value
                            battle_state["grant_target"] = target_value
                        child_context = parent_context.nested_effect_context(
                            template, param, targets)
                        result = native_effect(
                            short_type, child_context, {"param": param})
                        if result is None:
                            raise RuntimeError(
                                "RulesPort nested effect has no handler: "
                                f"{short_type}")
                        results.append(str(result))
                        if battle_state.get("resolution_paused"):
                            break
                    if battle_state.get("resolution_paused"):
                        break
            finally:
                for key, value in previous.items():
                    if value is None:
                        battle_state.pop(key, None)
                    else:
                        battle_state[key] = value
            return (f"repeat {short_type} x{max(0, int(loop_count or 0))}: "
                    + "; ".join(results))

        try:
            effects = ability.ordered_effects
            start = int(resume_from_order or 0)
            allowed_groups = (None if effect_groups is None else
                              {int(group) for group in effect_groups})
            # Effect-instance positions let a contingency reject a forward
            # reference the way the client does, and let a continuation mark
            # every already-executed effect as applied (m_WasApplied) so its
            # dependents still run after the pause.
            instance_positions = {
                int(field(item, "effect_instance_id", index)): index
                for index, item in enumerate(effects)}
            # The first card each effect instance resolved, for the
            # ``resolving_secondary_target_uid`` alias a leaf such as a
            # damage-shield reads.
            resolved_by_instance: dict[Any, Any] = {}
            for position, effect in enumerate(effects):
                instance_id = int(field(effect, "effect_instance_id", position))
                if position < start:
                    applied[instance_id] = True
                    continue
                battle_state["resolving_effect_order"] = position
                effect_guid = str(field(effect, "guid", "")).lower()
                effect_type = str(field(effect, "concrete_type", ""))
                effect_group = int(field(effect, "effect_group_id", 0))
                if allowed_groups is not None and effect_group not in allowed_groups:
                    continue
                target_index = int(field(effect, "target_index", -1))
                contingent = int(field(
                    effect, "contingent_effect_instance_id", -1))
                if contingent >= 0 and (
                        instance_positions.get(contingent, len(effects))
                        >= position or
                        not applied.get(contingent, False)):
                    applied[instance_id] = False
                    continue
                battle_state["resolving_effect_guid"] = effect_guid
                target_values = ability.activation.target_map.get(
                    target_index, ()) if target_index >= 0 else ()
                target_spec = None
                native_waiting = False
                if not target_values and target_index >= 0:
                    target_spec = (ability.metadata.targets[target_index]
                                   if target_index < len(ability.metadata.targets)
                                   else None)
                    if target_spec is not None:
                        kind = str(target_spec.target_kind or "")
                        if kind == "AbilitySourceCardTargetTemplate":
                            from .targeting import ability_source_target_uid
                            target_values = (ability_source_target_uid(
                                db, session.session_id,
                                ability.source_uid, target_spec.guid,
                                ability.responsible_player_id),)
                        elif kind.endswith("PlayerTargetTemplate"):
                            # A PlayerTargetTemplate identifies a champion in
                            # the client session, not the controller's raw
                            # player id.  In PvE the AI controller is ``0``;
                            # preserving that value made a "You get ..."
                            # GrantAbility look for card UID 0 instead of the
                            # AI champion's synthetic SessionCardId.  Resolve
                            # the slot actually being processed: deriving the
                            # champion from the ability's first template made
                            # Booby Trap's target-index-1 "You" damage resolve
                            # against its target-index-0 "Self" instead and
                            # report "damage: no target".
                            from .targeting import implicit_champion_target
                            player_filter = str(
                                getattr(target_spec, "player_filter", "")
                                or "").lower()
                            champion = implicit_champion_target(
                                db, session, handler, battle_state,
                                opposing=player_filter in {
                                    "opponent", "opposing",
                                    "singleopponent", "multipleopponents"},
                                template_id=getattr(target_spec, "guid", None))
                            target_values = ((champion,) if champion is not None
                                             else ())
                        elif (int(ability.responsible_player_id or 0) == 0 and
                              kind.endswith("AbilityTargetTemplate")
                              and not target_spec.is_auto):
                            if not ai_target_map_attempted:
                                ai_target_map = _choose_ai_explicit_target_map(
                                    handler, session, battle_state, player_uid,
                                    ai_uid, ability)
                                ai_target_map_attempted = True
                            if ai_target_map is not None:
                                target_values = tuple(
                                    ai_target_map.get(target_index, ()))
                                if effect_type == (
                                        "SacrificeCardAbilityEffectTemplate"):
                                    source_uid = ability.source_uid
                                    target_values = tuple(
                                        uid for uid in target_values
                                        if (source_uid is None or
                                            int(uid) != int(source_uid)))
                                if target_values:
                                    # Keep every authored slot chosen by the
                                    # evaluator available to later effects in
                                    # this activation, including shared target
                                    # instances that were initially empty.
                                    ability.activation.target_map.update(
                                        ai_target_map)
                                    battle_state["ability_target_map"].update(
                                        ai_target_map)
                            else:
                                # If the evaluator cannot build a complete
                                # snapshot, preserve the old native fallback.
                                # Authored random targets still use the session
                                # RNG and explicit non-random targets take the
                                # first legal candidate.
                                both_players = str(
                                    target_spec.player_filter or "").lower() not in {
                                        "self", "you", "controller"}
                                candidates = tuple(legal_targets(
                                    db, session.session_id,
                                    ability.responsible_player_id,
                                    target_spec.guid, ability.source_uid,
                                    both_players=both_players,
                                    champions=(getattr(
                                        handler, "_champion_targets", lambda: [])()
                                               or []),
                                    battle_state=battle_state))
                                if effect_type == "SacrificeCardAbilityEffectTemplate":
                                    # "another troop you control" never sacrifices
                                    # the source as its own payment.
                                    source_uid = ability.source_uid
                                    candidates = tuple(
                                        uid for uid in candidates
                                        if (source_uid is None or
                                            int(uid) != int(source_uid)))
                                if target_spec.is_random:
                                    candidates = _random_target_sample(
                                        candidates,
                                        int(target_spec.resolved_maximum(
                                            ability.activation.variables) or 0),
                                        battle_state)
                                    target_values = candidates
                                else:
                                    maximum = int(target_spec.resolved_maximum(
                                        ability.activation.variables) or 0)
                                    target_values = (candidates[:maximum]
                                                     if maximum > 0 else candidates[:1])
                            if ai_target_map is not None and not target_values:
                                # A complete evaluator result is authoritative:
                                # no legal selected target disables this effect
                                # instead of letting a leaf fall back to the
                                # resolving source card.
                                applied[instance_id] = condition_passes(
                                    effect, None)
                                for key in ("resolving_target_uid",
                                            "player_mod_target",
                                            "player_spell_target",
                                            "grant_target"):
                                    battle_state.pop(key, None)
                                continue
                        elif kind == "AbilityTriggerCardTargetTemplate":
                            from .targeting import _target_field
                            selector = str(_target_field(
                                target_spec.guid, "m_TriggerSelector",
                                "TriggerSource") or "TriggerSource").rsplit(
                                    ".", 1)[-1]
                            selector_key = ("resolving_trigger_target_uid"
                                   if selector == "TriggerTarget" else
                                   "resolving_trigger_source_uid"
                                   if selector == "TriggerSource" else None)
                            selected = battle_state.get(selector_key) if selector_key else None
                            if selected is None:
                                # Some event classes carry only one of the
                                # trigger cards; the legacy resolver's
                                # transient target is the next fallback, then
                                # the ability source (the card that raised the
                                # event for #TRIGGER_SOURCE#).
                                selected = (
                                    battle_state.get("player_spell_target")
                                    or battle_state.get("player_mod_target")
                                    or battle_state.get("resolving_target_uid"))
                            if selected is None:
                                selected = ability.source_uid
                            target_values = ((int(selected),)
                                             if selected is not None else ())
                        elif kind in ("SourceDrawnTargetTemplate",
                                      "SourceBuriedTargetTemplate",
                                      "SourceStoredTargetTemplate",
                                      "AbilityCreatedTargetTemplate",
                                      "VoidedTargetTemplate"):
                            target_values = _list_target_values(
                                battle_state, ability, kind,
                                ability.source_uid, target_spec, db,
                                session.session_id)
                            # The authored count applies to a list target: a random one draws its cards once
                            # (C# FilterRandomTargets), and a fixed one is capped to its maximum.
                            maximum = int(target_spec.resolved_maximum(
                                battle_state.get("ability_variables") or
                                getattr(ability.activation, "variables", {}) or {}) or 0)
                            if target_spec.is_random:
                                target_values = tuple(_random_target_sample(
                                    target_values, maximum, battle_state))
                            elif maximum > 0:
                                target_values = tuple(target_values[:maximum])
                            if not target_values:
                                # C# disables an effect whose authored list
                                # target enumerates nothing; it never falls
                                # back to the source card.  The instance still
                                # counts as applied when its condition holds,
                                # matching the client's m_WasApplied
                                # bookkeeping for contingent effects.
                                applied[instance_id] = condition_passes(
                                    effect, None)
                                for key in ("resolving_target_uid",
                                            "player_mod_target",
                                            "player_spell_target",
                                            "grant_target"):
                                    battle_state.pop(key, None)
                                continue
                        elif kind == "SecondaryTargetTemplate":
                            # C# SecondaryTargetTemplate: the input cards are
                            # the resolved outputs of the contingent effect's
                            # target (m_SecondaryTargetIndex), filtered by the
                            # template card filter.
                            sec = int(field(effect, "secondary_target_index", -1))
                            raw = (_resolved_effect_target_values(
                                ability, sec, battle_state,
                                resolved_by_instance) if sec >= 0 else ())
                            from .targeting import filter_resolved_targets
                            target_values = filter_resolved_targets(
                                db, session.session_id,
                                ability.responsible_player_id,
                                target_spec.guid, ability.source_uid, raw,
                                battle_state)
                            if (target_spec.requires_input and
                                    int(ability.responsible_player_id or 0) != 0):
                                # "Put one of them": the player picks among the cards the
                                # look produced, after that effect has run. A choice already
                                # made for this target (the resumed continuation) is used as is.
                                from .targeting import is_chosen_secondary_target
                                prior = ability.activation.target_map.get(target_index)
                                prior_values = (prior if isinstance(prior, (list, tuple))
                                                else (prior,))
                                allowed = {int(uid) for uid in target_values}
                                chosen = tuple(int(uid) for uid in prior_values
                                               if uid is not None and int(uid) in allowed)
                                needs_choice = bool(target_values) and (
                                    len(target_values) > 1 or target_spec.optional or
                                    int(target_spec.minimum or 0) == 0)
                                if chosen:
                                    target_values = chosen
                                elif needs_choice and is_chosen_secondary_target(target_spec):
                                    prompt = getattr(handler, "_prompt_revealed_choice", None)
                                    if callable(prompt):
                                        continuation = {
                                            "ability_instance_id": int(ability.instance_id),
                                            "ability_guid": ability.ability_template_id,
                                            "source_uid": int(ability.source_uid or 0),
                                            "owner_id": int(ability.responsible_player_id or 0),
                                            "target_map": {
                                                str(key): value for key, value in
                                                ability.activation.target_map.items()},
                                            "variables": dict(ability.activation.variables or {}),
                                            "resume_effect_order": int(position),
                                            "target_index": int(target_index),
                                        }
                                        prompt(
                                            game, session, player_uid, ai_uid,
                                            battle_state, ability.ability_template_id,
                                            int(ability.source_uid or 0),
                                            int(ability.responsible_player_id),
                                            list(target_values),
                                            list(battle_state.get("revealed_cards") or []),
                                            optional=bool(target_spec.optional or
                                                          int(target_spec.minimum or 0) == 0),
                                            continuation=continuation)
                                        battle_state["resolution_paused"] = True
                                        native_waiting = True
                                        target_values = ()
                        elif kind == "MatchSecondaryTargetTemplate":
                            target_values = _match_secondary_values(
                                db, session.session_id, ability, effect,
                                target_spec, battle_state,
                                champions=(getattr(
                                    handler, "_champion_targets",
                                    lambda: [])() or []),
                                resolved_by_instance=resolved_by_instance)
                            # This specialized enumerator bypasses the base
                            # auto-target branch below, so apply its authored
                            # random and maximum-count semantics here too.
                            maximum = int(target_spec.resolved_maximum(
                                battle_state.get("ability_variables") or
                                getattr(ability.activation, "variables", {}) or
                                {}) or 0)
                            if target_spec.is_random:
                                target_values = _random_target_sample(
                                    target_values, maximum, battle_state)
                            elif maximum > 0:
                                target_values = tuple(target_values[:maximum])
                        elif target_spec.target_kind == "SourceRevealedTargetTemplate":
                            from .targeting import (revealed_target_uids,
                                                    _target_ignore_acted_on)
                            acted_on = ()
                            if _target_ignore_acted_on(target_spec.guid):
                                sec_value = field(
                                    effect, "secondary_target_index", -1)
                                sec_index = (-1 if sec_value is None
                                             else int(sec_value))
                                # "the remaining cards" ignores what the
                                # referenced mapping already acted on: the
                                # card the player just chose, not that
                                # template's whole legal candidate pool.
                                acted_on = _resolved_effect_target_values(
                                    ability, sec_index, battle_state,
                                    resolved_by_instance)
                                referenced_target_index = (
                                    _referenced_effect_target_index(
                                        ability, sec_index))
                                if (referenced_target_index is not None and
                                        referenced_target_index < len(
                                            ability.metadata.targets)):
                                    sec_spec = ability.metadata.targets[
                                        referenced_target_index]
                                    acted_on = acted_on or tuple(revealed_target_uids(
                                        db, session.session_id,
                                        ability.responsible_player_id,
                                        ability.source_uid, sec_spec.guid,
                                        battle_state.get("revealed_cards") or [],
                                        battle_state=battle_state))
                            candidates = revealed_target_uids(
                                db, session.session_id,
                                ability.responsible_player_id, ability.source_uid,
                                target_spec.guid,
                                battle_state.get("revealed_cards") or [],
                                battle_state=battle_state, acted_on_uids=acted_on)
                            if not candidates:
                                # Nothing revealed matches (no artifact among
                                # the cards, or every card was already taken).
                                # C# enumerates an empty target list and the
                                # effect does nothing; it must not fall back
                                # to the source card (Gearsmith moved itself
                                # into the deck).
                                applied[instance_id] = condition_passes(
                                    effect, None)
                                for key in ("resolving_target_uid",
                                            "player_mod_target",
                                            "player_spell_target",
                                            "grant_target"):
                                    battle_state.pop(key, None)
                                continue
                            # A SourceRevealed target is input-bearing only when
                            # the authored template asks the player to choose.
                            # Oakhenge's child ability targets "a revealed troop"
                            # (a real picker) and then "the remaining cards"
                            # (m_IsAutoTarget): prompting for the second one asked
                            # the player to pick twice and re-moved the chosen
                            # card, and the extra pause left the spell on the
                            # chain without priority.
                            # A single non-optional candidate is not a choice:
                            # the client's single-target path (AutoTargetCoverflow)
                            # needs card views the combined reveal never created,
                            # so resolve it directly instead of sending a picker
                            # with one target.
                            single_forced = (len(candidates) == 1 and not (
                                target_spec.optional or
                                int(target_spec.minimum or 0) == 0))
                            if (candidates and target_spec.requires_input
                                    and not single_forced
                                    and int(ability.responsible_player_id or 0) != 0):
                                prompt = getattr(handler, "_prompt_revealed_choice", None)
                                if callable(prompt):
                                    continuation = {
                                        "ability_instance_id": int(ability.instance_id),
                                        "ability_guid": ability.ability_template_id,
                                        "source_uid": int(ability.source_uid or 0),
                                        "owner_id": int(ability.responsible_player_id or 0),
                                        "target_map": {
                                            str(key): value for key, value in
                                            ability.activation.target_map.items()},
                                        "variables": dict(ability.activation.variables or {}),
                                        "resume_effect_order": int(position),
                                        "target_index": int(target_index),
                                    }
                                    if battle_state.get("_choice_parent"):
                                        continuation["parent"] = dict(
                                            battle_state["_choice_parent"])
                                    prompt(
                                        game, session, player_uid, ai_uid,
                                        battle_state, ability.ability_template_id,
                                        int(ability.source_uid or 0),
                                        int(ability.responsible_player_id),
                                        candidates,
                                        list(battle_state.get("revealed_cards") or []),
                                        # "Up to one" (minimum 0) may be
                                        # declined like an optional target.
                                        optional=bool(
                                            target_spec.optional or
                                            int(target_spec.minimum or 0) == 0),
                                        continuation=continuation)
                                    battle_state["resolution_paused"] = True
                                    native_waiting = True
                            elif not candidates:
                                # C# disables an effect whose authored
                                # SourceRevealed target enumerates nothing.  The
                                # resolver previously rewrote the empty list to
                                # ``(None,)`` and the MoveCardToZone leaf's
                                # source fallback then moved the resolving card
                                # itself: Oakhenge's "put a revealed troop into
                                # your hand" put the spell into hand when the
                                # top five held no troop, and the spell was
                                # never discarded from CastSpells.
                                applied[instance_id] = condition_passes(
                                    effect, None)
                                for key in ("resolving_target_uid",
                                            "player_mod_target",
                                            "player_spell_target",
                                            "grant_target"):
                                    battle_state.pop(key, None)
                                continue
                            else:
                                # C# SourceRevealedTargetTemplate leaves
                                # m_MaximumTargetCount unset (int.MaxValue),
                                # so a 0/absent maximum means "all of them",
                                # not a single card.
                                _max = int(target_spec.maximum or 0)
                                target_values = tuple(
                                    candidates[:_max] if _max > 0 else candidates)
                                # A card chosen from a revealed set ("put a
                                # revealed Human into your hand") stays public
                                # when it moves to hand (move_card_to_zone).
                                chosen = battle_state.setdefault(
                                    "source_revealed_chosen", [])
                                chosen.extend(
                                    int(uid) for uid in target_values
                                    if int(uid) not in chosen)
                        elif target_spec.is_auto:
                            from .targeting import target_uses_both_players
                            both_players = target_uses_both_players(
                                db, target_spec.guid)
                            # A "top N of your deck" look takes the first N deck cards; legal_targets does not
                            # apply that limit and would offer every card in the listed zones.
                            from .targeting import authored_target_candidates
                            authored = authored_target_candidates(
                                db, session.session_id,
                                int(ability.responsible_player_id or 0),
                                [spec.guid for spec in ability.metadata.targets],
                                target_index, source_uid=ability.source_uid,
                                battle_state=battle_state)
                            if authored is not None:
                                candidates = tuple(authored)
                            else:
                                candidates = tuple(legal_targets(
                                    db, session.session_id,
                                    int(ability.responsible_player_id or 0),
                                    target_spec.guid, ability.source_uid,
                                    both_players=both_players,
                                    champions=(getattr(handler, "_champion_targets",
                                                       lambda: [])() or []),
                                    battle_state=battle_state))
                            if not candidates:
                                # C# GetAutoTargets yields an empty set when no
                                # card matches the filter.  Falling through to
                                # ``target_values = (None,)`` made target-aware
                                # effects fall back to ``resolving_source_uid``;
                                # Corinth's Shifted Paradigm (auto-targets "your
                                # hand"/"your crypt") then moved the champion
                                # into the deck.  An effect with no auto-target
                                # card simply does nothing.
                                applied[instance_id] = False
                                continue
                            if target_spec.is_random:
                                # A random auto-target (e.g. Infernal Professor's
                                # "a random non-resource card from your deck")
                                # must resolve to a bounded random sample, not
                                # every legal card.  Iterating the whole pool
                                # moved the entire deck into hand.
                                candidates = _random_target_sample(
                                    candidates,
                                    int(target_spec.resolved_maximum(
                                        ability.activation.variables) or 0),
                                    battle_state)
                            else:
                                _max = int(target_spec.resolved_maximum(
                                    ability.activation.variables) or 0)
                                if _max > 0 and len(candidates) > _max:
                                    # C# GetAutoTargets truncates a non-random
                                    # auto-target to GetMaximumTargetCount.
                                    candidates = candidates[:_max]
                            target_values = candidates
                        if target_values:
                            # Effects that share a target-template index share
                            # one client AbilityTargetInstance.  RecalculateTargets
                            # refreshes that instance before its first effect;
                            # it does not choose a different random card for
                            # every subsequent effect using the same mapping.
                            # Keep the resolved automatic target on this
                            # activation so a move followed by modifiers (for
                            # example Infernal Professor) remains one atomic
                            # card operation.
                            resolved_targets = tuple(target_values)
                            ability.activation.target_map[target_index] = \
                                resolved_targets
                            battle_state["ability_target_map"][target_index] = \
                                resolved_targets
                            target_values = resolved_targets
                if native_waiting:
                    break
                hand_discard_waiting_for_input = False
                if (effect_type == "DiscardCardAbilityEffectTemplate" and
                        target_spec is not None and
                        target_spec.requires_input and not target_values):
                    # A Records-authored hand target for DiscardCard is a
                    # class-23 prompt boundary. Let the leaf receive None so
                    # EffectContext.discard can save its continuation and
                    # publish the normal hand picker. Other empty input
                    # targets remain unapplied as below.
                    from .targeting import filter_restricts_to_zone
                    hand_filter = target_spec.card_filter
                    to_dict = getattr(hand_filter, "to_dict", None)
                    if callable(to_dict):
                        hand_filter = to_dict()
                    hand_discard_waiting_for_input = filter_restricts_to_zone(
                        hand_filter, "Hand")
                if (target_spec is not None and
                        target_spec.requires_input and not target_values and
                        not hand_discard_waiting_for_input):
                    # An input-bearing target with no selected legal cards
                    # resolves as an empty target instance when its minimum
                    # is zero. C# leaves that effect unapplied; it does not
                    # pass a null target to the leaf (which can fall back to
                    # the source card).
                    applied[instance_id] = False
                    for key in ("resolving_target_uid", "player_mod_target",
                                "player_spell_target", "grant_target"):
                        battle_state.pop(key, None)
                    continue
                if not target_values:
                    target_values = (None,)
                if not isinstance(target_values, (tuple, list)):
                    target_values = (target_values,)
                effect_targets = tuple(target_values)
                if (effect_type == "RevealCardsAbilityEffectTemplate" and
                        len(target_values) > 1):
                    # C# RevealCards receives the full AbilityTargetInstance
                    # once. Keep that set in effect_targets for the leaf, and
                    # invoke the self-targeted effect only once to avoid
                    # replaying the same reveal for each card.
                    target_values = tuple(target_values[:1])
                resolved_by_instance[instance_id] = tuple(
                    int(value) for value in effect_targets
                    if value is not None)
                effect_param = field(effect, "param", "")
                # ``m_SecondaryTargetIndex`` names an earlier effect instance,
                # and the client answers it with that instance's resolved
                # card (e.g. the damage dealer a shield is restricted to).
                # Expose the same alias the legacy resolver published; a leaf
                # with no such reference must not inherit a previous effect's.
                secondary_index = int(field(effect, "secondary_target_index", -1))
                secondary_values = (resolved_by_instance.get(secondary_index, ())
                                    if secondary_index >= 0 else ())
                if secondary_values:
                    battle_state["resolving_secondary_target_uid"] = int(
                        secondary_values[0])
                else:
                    battle_state.pop("resolving_secondary_target_uid", None)
                # C# runs a non-card AbilityEffectTemplate once per effect
                # instance with that instance's whole target list, while the
                # card-scoped leaves below expect one call per target.  A
                # self-targeted effect resolves its own set from Records, so
                # looping its resolved targets re-runs the whole operation:
                # Oakhenge's reveal fired once per revealed card.
                if effect_type in SELF_TARGETED_EFFECTS:
                    target_values = (next(
                        (value for value in target_values
                         if value is not None), None),)
                # A picker paused an earlier target of this effect: the
                # resumed effect skips the targets it already resolved.
                resume_target = battle_state.pop("rules_port_resume_target", None)
                first_target = 0
                if (isinstance(resume_target, dict) and
                        int(resume_target.get("position", -1)) == position):
                    first_target = int(resume_target.get("index", 0) or 0)
                for target_offset, target in enumerate(target_values):
                    if target_offset < first_target:
                        continue
                    battle_state["rules_port_current_target"] = {
                        "ability_guid": str(getattr(
                            ability, "ability_template_id", "") or "").lower(),
                        "position": position, "index": target_offset,
                        "count": len(target_values)}
                    if target is None:
                        battle_state.pop("resolving_target_uid", None)
                        for key in ("player_mod_target", "player_spell_target",
                                    "grant_target"):
                            battle_state.pop(key, None)
                    else:
                        target = int(target)
                        battle_state["resolving_target_uid"] = target
                        battle_state["player_mod_target"] = target
                        battle_state["player_spell_target"] = target
                        battle_state["grant_target"] = target
                    effect_context = EffectContext.from_rules_port(
                        game, session, db, handler, player_uid, ai_uid,
                        battle_state, effect_guid, effect_param,
                        ability=ability, effect_targets=effect_targets,
                        nested_effect_dispatch=dispatch_nested_effect)
                    if not condition_passes(effect, target):
                        applied[instance_id] = False
                        # Incantation-style BOMs put the five-counter gate on
                        # the remove-counters effect while the following
                        # transform leaf has no condition of its own; carry
                        # the failed gate forward instead of transforming the
                        # first target anyway.
                        if effect_type == "CardModifierAbilityEffectTemplate":
                            try:
                                modifier = json.loads(effect_param or "{}")
                            except (TypeError, ValueError,
                                    json.JSONDecodeError):
                                modifier = {}
                            if (str(modifier.get("property") or "") == "counter"
                                    and int(modifier.get("amount") or 0) <= 0):
                                battle_state["_skip_transform"] = True
                        continue
                    trace_effect = {
                        "effect_guid": effect_guid,
                        "effect_type": effect_type,
                        "effect_order": position,
                    }
                    trace = begin_effect(
                        db, session, game, battle_state, trace_effect, target)
                    try:
                        result = native_effect(
                            effect_type, effect_context, effect)
                    except Exception as exc:
                        end_effect(
                            db, session, game, battle_state, trace, error=exc)
                        raise
                    if result is None:
                        raise RuntimeError(
                            "RulesPort native effect has no handler: "
                            f"{effect_type} ({effect_guid})")
                    end_effect(
                        db, session, game, battle_state, trace, result=result)
                    applied[instance_id] = True
                    battle_state.setdefault("rules_port_effect_results", []).append(
                        {"instance_id": instance_id, "result": str(result)})
                    if (battle_state.get("resolution_paused") and
                            target_offset + 1 < len(target_values)):
                        # Each target is its own choice (one champion's deck pick, then the next's): stop here and
                        # resume at the next target when the pick is answered.
                        _record_resume_target(battle_state, {
                            "ability_guid": str(getattr(
                                ability, "ability_template_id", "") or "").lower(),
                            "position": position, "index": target_offset + 1})
                        break
                battle_state.pop("rules_port_current_target", None)
                if battle_state.get("resolution_paused"):
                    battle_state["rules_port_resume_effect_order"] = position
                    break
        finally:
            if battle_state.get("trace_ability_resolution"):
                # ``applied_effects`` is scoped to one resolver call, but the
                # contract sweep compares traces after nested and repeated
                # abilities have returned. Preserve the aggregate outcome by
                # ability while that opt-in trace is enabled so effect IDs
                # from a different ability cannot make a valid trace look
                # unapplied.
                ability_guid = str(
                    getattr(ability, "ability_template_id", "") or ""
                ).lower()
                if ability_guid:
                    by_ability = battle_state.setdefault(
                        "_ability_effect_status_by_guid", {})
                    status = by_ability.setdefault(ability_guid, {})
                    for effect_instance_id, was_applied in applied.items():
                        key = str(effect_instance_id)
                        status[key] = bool(status.get(key, False) or
                                           was_applied)
            runtime_values = battle_state.get("ability_runtime_state") or {}
            instance_values = runtime_values.get(instance_key)
            if isinstance(instance_values, dict):
                if battle_state.get("resolution_paused"):
                    instance_values["DamageDealt"] = int(
                        battle_state.get("_ability_damage_dealt", 0) or 0)
                else:
                    runtime_values.pop(instance_key, None)
                    if not runtime_values:
                        battle_state.pop("ability_runtime_state", None)
            cache = battle_state.get("ability_variable_cache") or {}
            if battle_state.get("resolution_paused"):
                pass
            else:
                cache.pop(instance_key, None)
                if not cache:
                    battle_state.pop("ability_variable_cache", None)
            for key, value in old.items():
                if value is None:
                    battle_state.pop(key, None)
                else:
                    battle_state[key] = value
            if previous_lists is None:
                battle_state.pop("ability_lists", None)
            else:
                battle_state["ability_lists"] = previous_lists
            if depth > 0:
                # A nested child ran against its own m_WasApplied map; give
                # the parent's back so the remainder of the parent walk sees
                # the flags it set before the child was activated.
                if previous_applied is None:
                    battle_state.pop("applied_effects", None)
                else:
                    battle_state["applied_effects"] = previous_applied


_EVENT_INSTANCE_STATS = frozenset({"DamageDealt"})


class PortAbilityResolver:
    """Resolve one port-owned ability and persist its UI continuation."""

    def __init__(self, handler, game, game_session, db, player_uid, ai_uid,
                 battle_state: dict, *, native_effect=None,
                 effect_groups=None, event_tac=None) -> None:
        self.handler = handler
        self.game = game
        self.game_session = game_session
        self.db = db
        self.player_uid = player_uid
        self.ai_uid = ai_uid
        self.battle_state = battle_state
        self.effect_groups = effect_groups
        self.event_tac = {}
        # Named instance IntAttrs the triggering event hands to the ability
        # instance (CardDealtDamageEvent's ``DamageDealt``).  The hashed TAC
        # map above keeps only integer keys, so carry these separately.
        self.event_instance_stats = {}
        for key, value in (event_tac or {}).items():
            if key in _EVENT_INSTANCE_STATS:
                try:
                    self.event_instance_stats[key] = int(value or 0)
                except (TypeError, ValueError):
                    pass
                continue
            try:
                self.event_tac[int(key)] = value
            except (TypeError, ValueError):
                continue
        self.effect_backend = NativeEffectBackend()
        if native_effect is None:
            from .native_leaves import dispatch
            native_effect = dispatch
        self.native_effect = native_effect

    def __call__(self, ability) -> AbilityResolutionState:
        # A reconnect or hot reload may replace the mutable checkpoint after
        # this resolver was constructed. Resolve against the session's current
        # RulesPort state, never the dictionary captured at attach time.
        from .persistence import load_state
        requested_resume = self.battle_state.get(
            "rules_port_resume_effect_order")
        requested_target = self.battle_state.get("rules_port_resume_target")
        current_state = load_state(self.game_session)
        if isinstance(current_state, dict) and current_state:
            self.battle_state = current_state
        if requested_target is not None:
            # The parent's paused target travels with the resume request, as the effect order does.
            self.battle_state["rules_port_resume_target"] = requested_target
        # The continuation caller may have supplied a newer in-memory offset
        # than the last checkpoint. Preserve it across the reload. Conversely
        # an explicitly fresh nested child must not inherit its parent's
        # offset and skip its own effect 0.
        if requested_resume is None:
            self.battle_state.pop("rules_port_resume_effect_order", None)
        else:
            self.battle_state["rules_port_resume_effect_order"] = int(
                requested_resume)
        rng = getattr(self.game_session, "random_number_generator", None)
        if rng is not None:
            self.battle_state["_rules_rng"] = rng
        previous_strict = self.battle_state.get("_rules_port_strict_effects")
        self.battle_state["_rules_port_strict_effects"] = True
        resume_order = self.battle_state.get("rules_port_resume_effect_order")
        # A continuation may legitimately have no target map.  Choice-zone
        # effects save only the effect offset because the selected card is
        # supplied through the persisted choice state.  Never restart the
        # parent ability merely because its target map is empty.
        if resume_order is not None:
            self.battle_state.pop("resolution_paused", None)
        elif self.event_instance_stats:
            # C# IntAttrAbilityVariable reads the AbilityInstance TAC; a
            # triggered instance starts with its event's values there, so
            # Inferno Goliath's "a burning counter for each damage dealt"
            # reads the event's damage rather than its own (zero) damage.
            from .statistics import seed_ability_stats
            seed_ability_stats(self.battle_state, self.event_instance_stats,
                               instance_id=getattr(ability, "instance_id", 0))
        try:
            self.effect_backend(
                handler=self.handler, game=self.game, session=self.game_session,
                db=self.db, player_uid=self.player_uid, ai_uid=self.ai_uid,
                battle_state=self.battle_state, ability=ability,
                resume_from_order=resume_order, native_effect=self.native_effect,
                effect_groups=self.effect_groups,
                event_tac=self.event_tac)
        finally:
            if previous_strict is None:
                self.battle_state.pop("_rules_port_strict_effects", None)
            else:
                self.battle_state["_rules_port_strict_effects"] = previous_strict
        port_session = getattr(self.game_session, "_rules_port_session", None)
        if self.battle_state.get("pending_discard_ability") and port_session is not None:
            pending = self.battle_state.get("pending_discard_continuation")
            child_guid = str((pending or {}).get("ability_guid") or "").lower()
            current_guid = str(ability.ability_template_id or "").lower()
            if (self.battle_state.get("resolution_paused") and
                    isinstance(pending, dict) and child_guid and
                    child_guid != current_guid):
                paused_order = self.battle_state.get(
                    "rules_port_resume_effect_order")
                if paused_order is None:
                    paused_order = self.battle_state.get(
                        "resolving_effect_order", 0)
                continuation = ability.continuation(
                    resume_effect_order=int(paused_order or 0) + 1)
                activation = continuation.pop("activation", {})
                continuation["target_map"] = dict(
                    activation.get("target_map") or {})
                continuation["variables"] = dict(
                    activation.get("variables") or
                    continuation.get("variables") or {})
                # ActivateAbility effects may nest. Keep each suspended
                # parent in order so the picker answer resumes the child,
                # then its parent, then any enclosing ability.
                cursor = pending
                ancestors = {(child_guid, int(pending.get(
                    "ability_instance_id", pending.get("instance_id", 1)) or 1))}
                while isinstance(cursor.get("parent"), dict):
                    cursor = cursor["parent"]
                    ancestors.add((str(cursor.get("ability_guid") or "").lower(),
                                   int(cursor.get("ability_instance_id", 1) or 1)))
                identity = (current_guid, int(ability.instance_id))
                if identity not in ancestors:
                    cursor["parent"] = continuation
            port_session.pending_activation = {
                "ability_instance_id": int(ability.instance_id),
                "responsible_player_id": int(ability.responsible_player_id),
                "continuation": ability.continuation(), "prompts": (),
            }
            port_session.persist()
        elif resume_order is not None and not self.battle_state.get(
                "resolution_paused"):
            for key in ("rules_port_resume_effect_order", "rules_port_resume_target",
                        "pending_discard_ability", "pending_discard_target_template",
                        "pending_discard_scid"):
                self.battle_state.pop(key, None)
        if self.battle_state.get("resolution_paused"):
            return AbilityResolutionState.WAITING_FOR_INPUT
        return AbilityResolutionState.COMPLETED


def build_port_ability(ability_guid, source_uid, owner_id, *, instance_id=1,
                       target_map=None, variables=None, cost_target_map=None):
    """Build one typed ability instance from the authoritative Records graph."""
    from gamedata import DEFAULT_RECORD_STORE, ability_graph
    from gamedata.play_plan import AbilityInstance as MetadataAbility
    from .abilities import AbilityInstance

    graph = ability_graph(DEFAULT_RECORD_STORE, str(ability_guid).lower())
    if graph is None:
        raise RuntimeError(f"ability {ability_guid} is missing from Records")
    metadata = MetadataAbility.from_graph(
        graph, source_uid=None if source_uid is None else int(source_uid),
        owner_id=int(owner_id or 0), responsible_player_id=owner_id)
    ability = AbilityInstance(
        instance_id=int(instance_id), metadata=metadata,
        activating_player_id=owner_id, responsible_player_id=owner_id)
    activation = {"target_map": target_map or {},
                  "variables": variables or {}}
    if cost_target_map:
        # Additional-cost selections (exhaust/sacrifice/...) paid when the
        # ability was activated; effects such as Construction Plans count
        # them ("for each troop exhausted this way").
        activation["cost_target_map"] = cost_target_map
    ability.bind_activation(activation)
    return ability


def resolve_port_ability(handler, game, session, db, player_uid, ai_uid,
                         battle_state, ability_guid, source_uid, owner_id,
                         *, target_map=None, variables=None,
                         resume_from_order=None, instance_id=1,
                         native_effect=None,
                         effect_groups=None, event_tac=None,
                         cost_target_map=None):
    """Resolve a persisted continuation through the port-owned lifecycle."""
    log_targets = target_map
    if not log_targets:
        fallback = ((battle_state or {}).get("resolving_target_uid")
                    or (battle_state or {}).get("player_spell_target")
                    or (battle_state or {}).get("player_mod_target"))
        if fallback is not None:
            log_targets = {0: fallback}
    _log_ability_start(handler, db, ability_guid, source_uid, owner_id,
                       log_targets)
    ability = build_port_ability(
        ability_guid, source_uid, owner_id, instance_id=instance_id,
        target_map=target_map, variables=variables,
        cost_target_map=cost_target_map)
    # The resume offset is persisted in the existing session snapshot because
    # the continuation may have crossed a reconnect boundary.
    if resume_from_order is not None:
        battle_state["rules_port_resume_effect_order"] = int(resume_from_order)
    else:
        # A nested child is a fresh ability. Do not let it inherit the parent
        # continuation offset from the shared battle-state dictionary.
        battle_state.pop("rules_port_resume_effect_order", None)
    if event_tac is None and (battle_state or {}).get(
            "_spell_played_from_hand"):
        from .tac import _tac_attr_hash
        event_tac = {_tac_attr_hash("PlayedFromHand"): 1}
    return PortAbilityResolver(
        handler, game, session, db, player_uid, ai_uid, battle_state,
        native_effect=native_effect,
        effect_groups=effect_groups, event_tac=event_tac)(ability)


def _record_resume_target(battle_state, record):
    """Keep a paused effect's next target on the parent continuation of the pending pick.

    The pick's continuation is what is persisted and handed back when the
    player answers, so the target index has to live there.  The parent is the
    ability whose ActivateAbility effect raised the pick.
    """
    for key in ("pending_deck_search", "pending_choice", "pending_trigger"):
        pending = battle_state.get(key)
        continuation = pending.get("continuation") if isinstance(pending, dict) else None
        parent = continuation.get("parent") if isinstance(continuation, dict) else None
        if (isinstance(parent, dict) and
                str(parent.get("ability_guid") or "").lower() == record["ability_guid"]):
            parent["rules_port_resume_target"] = dict(record)
            return True
    return False


def resume_ability_continuation_parents(
        handler, game, session, db, player_uid, ai_uid, battle_state,
        continuation):
    """Resume nested parents after an invoked child ability finishes."""
    state = battle_state or {}
    continuation = dict(continuation or {})
    parent = ((continuation or {}).get("parent")
              if isinstance(continuation, dict) else None)

    def suspend_for_trigger(current):
        pending_trigger = state.get("pending_trigger")
        if (not isinstance(pending_trigger, dict) or
                isinstance(pending_trigger.get("continuation"), dict)):
            return
        try:
            chain_instance_id = int(
                state.get("paused_chain_instance_id", 0) or 0)
        except (TypeError, ValueError):
            chain_instance_id = 0
        if chain_instance_id <= 0:
            return
        suspended = dict(current)
        paused_order = state.get("rules_port_resume_effect_order")
        if paused_order is None:
            paused_order = state.get("resolving_effect_order")
        if paused_order is not None:
            # The trigger was raised by the effect at this order; that effect
            # has already applied before the resolver exposes its pause point.
            suspended["resume_effect_order"] = int(paused_order) + 1
        state["rules_port_suspended_chain_continuation"] = {
            "chain_instance_id": chain_instance_id,
            "continuation": {"parent": suspended},
        }

    if (state.get("resolution_paused") and any(state.get(key) for key in (
            "pending_choice", "pending_trigger", "pending_deck_search",
            "pending_conversation", "pending_discard_ability"))):
        suspend_for_trigger(continuation)
        return AbilityResolutionState.WAITING_FOR_INPUT
    if not isinstance(parent, dict):
        # A class-23 picker can outlive the process that created it. Older
        # checkpoints stored the child continuation but not the suspended
        # ActivateAbility parent, leaving the parent chain item to replay on
        # every pass. Rebuild that one edge from the persisted chain
        # descriptor and authored ActivateAbility metadata.
        try:
            from gamedata import DEFAULT_RECORD_STORE, ability_graph
            child_guid = str(continuation.get("ability_guid") or "").lower()
            chain_id = int(state.get("paused_chain_instance_id", 0) or 0)
            item = next((entry for entry in reversed(state.get("stack") or [])
                         if int(entry.get("instance_id", -1)) == chain_id), None)
            parent_guid = str((item or {}).get("ability_guid") or "").lower()
            parent_graph = (ability_graph(DEFAULT_RECORD_STORE, parent_guid)
                            if parent_guid else None)
            if (chain_id > 0 and child_guid and item is not None and
                    parent_graph is not None and
                    parent_guid != child_guid):
                for order, effect in enumerate(parent_graph.effects):
                    if effect.concrete_type not in (
                            "ActivateAbilityEffectTemplate",
                            "ActivatePowerAbilityEffectTemplate"):
                        continue
                    invoked = getattr(effect.template, "m_AbilityToInvoke", None)
                    invoked_guid = (invoked.get("m_Guid")
                                    if isinstance(invoked, dict) else
                                    getattr(invoked, "m_Guid", None))
                    if str(invoked_guid or "").lower() != child_guid:
                        continue
                    activation = (item.get("activation_data") or {})
                    parent = {
                        "ability_instance_id": chain_id,
                        "ability_guid": parent_guid,
                        "source_uid": int(continuation.get(
                            "source_uid", item.get("source_uid", 0)) or 0),
                        "owner_id": int(continuation.get(
                            "owner_id", item.get("owner_id", 0)) or 0),
                        "target_map": dict(
                            activation.get("target_map") or {}),
                        "variables": dict(
                            activation.get("variables") or {}),
                        "resume_effect_order": int(order) + 1,
                    }
                    continuation["parent"] = parent
                    break
        except (TypeError, ValueError, AttributeError):
            parent = None
    result = AbilityResolutionState.COMPLETED
    while isinstance(parent, dict) and parent.get("ability_guid"):
        parent_guid = str(parent.get("ability_guid") or "").lower()
        activation = parent.get("activation") or {}
        target_map = parent.get("target_map")
        variables = parent.get("variables")
        if isinstance(activation, dict):
            if target_map is None:
                target_map = activation.get("target_map")
            if variables is None:
                variables = activation.get("variables")
        resume_order = int(parent.get("resume_effect_order", 0) or 0)
        paused_target = parent.get("rules_port_resume_target")
        if isinstance(paused_target, dict) and str(
                paused_target.get("ability_guid") or "").lower() == parent_guid:
            # The picker paused a target of this effect: re-enter that effect and
            # let its target loop skip the targets already resolved.
            resume_order = int(paused_target["position"])
            state["rules_port_resume_target"] = {
                "position": resume_order, "index": int(paused_target["index"])}
        result = resolve_port_ability(
            handler, game, session, db, player_uid, ai_uid, state,
            parent_guid, parent.get("source_uid"),
            int(parent.get("owner_id", 0) or 0),
            target_map=target_map or {}, variables=variables or {},
            resume_from_order=resume_order,
            instance_id=int(parent.get(
                "ability_instance_id", parent.get("instance_id", 1)) or 1),
            event_tac=parent.get("event_tac"))
        if state.get("resolution_paused"):
            # A nested trigger can pause an already-resumed card ability after
            # the picker child has completed. Keep the current parent and its
            # remaining ancestors on the paused native chain item; otherwise
            # the next chain pass starts the printed BOM from effect zero.
            # Ordinary pickers own their own continuation and are resumed by
            # their transaction handler, so this marker is only needed for a
            # pending triggered-ability prompt, which has no child
            # continuation of its own.
            suspend_for_trigger(parent)
            return result
        parent = parent.get("parent")

    if not state.get("resolution_paused"):
        completed = int(state.pop("paused_chain_instance_id", 0) or 0)
        if completed:
            state["completed_chain_instance_id"] = completed
        # The class-23 prompt was projected as a host event rather than as a
        # normal RulesPort activation response. Release the activation slot
        # once the child and every suspended parent have resumed; otherwise
        # the next player transaction can still look like a reply to the old
        # prompt after its chain item has been completed.
        port_session = getattr(session, "_rules_port_session", None)
        pending_activation = getattr(port_session, "pending_activation", None)
        if port_session is not None and isinstance(pending_activation, dict):
            # The slot names whichever ability level stored the pause last:
            # for an invoked picker (Giant Corpse Fly's Deploy invoking
            # DiscardACard) that is the suspended ActivateAbility PARENT, not
            # the child continuation.  Match any level of the resumed chain,
            # or the stale slot keeps ``_push_main_phase_options`` treating
            # the player as mid-prompt for the rest of the game.
            resumed_ids = set()
            cursor = continuation
            while isinstance(cursor, dict):
                try:
                    resumed_ids.add(as_int(cursor.get(
                        "ability_instance_id", cursor.get("instance_id"))))
                except (TypeError, ValueError):
                    pass
                cursor = cursor.get("parent")
            try:
                pending_id = int(pending_activation.get(
                    "ability_instance_id", -1))
            except (TypeError, ValueError):
                pending_id = None
            if pending_id is not None and pending_id in resumed_ids:
                port_session.pending_activation = None
                port_session.persist()
    return result


def resolve_port_played_spell(game, session, db, handler, player_uid, ai_uid,
                               battle_state, ability_guids, *,
                               activations=None, played_from_hand=False,
                               voided_cost_uids=()):
    """Resolve all authored cast abilities through the port lifecycle.

    Card play is a multi-ability activation, not a special legacy resolver.
    Each graph is still sourced from Records, while scheduling, continuations,
    and effect dispatch remain RulesPort-owned.
    """
    from gamedata import ActivationData, DEFAULT_RECORD_STORE, ability_graph
    from .tac import _tac_attr_hash, tac_int

    bstate = battle_state or {}
    spell_event_tac = ({_tac_attr_hash("PlayedFromHand"): 1}
                       if played_from_hand else {})
    previous_played_from_hand = bstate.get("_spell_played_from_hand")
    bstate["_spell_played_from_hand"] = bool(played_from_hand)
    from .persistence import save_state
    save_state(session, bstate)
    target_uid = bstate.get("player_spell_target")
    source_uid = bstate.get("resolving_source_uid")
    owner_id = bstate.get("resolving_owner_id")
    if source_uid is not None:
        # A played card's real owner is authoritative: a stale
        # resolving_owner_id (for example 0 left by an earlier AI trigger)
        # must not resolve the spell as the opponent's.
        from pvp_db import db_card_owner_id
        source_owner = db_card_owner_id(
            session.session_id, int(source_uid), conn=db)
        if source_owner is not None:
            owner_id = source_owner
    if owner_id is None:
        owner_id = handler.user_profile["id"] if handler.user_profile else 0

    activation_map = {
        str(key).lower(): ActivationData.from_dict(value)
        for key, value in (activations or {}).items()
    }
    previous = bstate.get("_esc_counted_this_resolution")
    bstate["_esc_counted_this_resolution"] = False
    previous_lists = bstate.get("ability_lists")
    if isinstance(previous_lists, dict):
        previous_lists = {
            key: (list(value) if isinstance(value, list) else value)
            for key, value in previous_lists.items()}
    voided_cost_uids = list(dict.fromkeys(
        int(uid) for uid in (voided_cost_uids or ())
        if uid is not None and int(uid) > 0))
    # Cost results belong to this cast's ability instance. Start from a clean
    # VoidedCards list so stale values from an older play cannot opt Scrounge
    # in or change a "for each troop voided this way" amount.
    scoped_lists = (dict(previous_lists)
                    if isinstance(previous_lists, dict) else {})
    for key, value in tuple(scoped_lists.items()):
        if isinstance(value, list):
            scoped_lists[key] = list(value)
    scoped_lists.pop("VoidedCards", None)
    if voided_cost_uids:
        scoped_lists["VoidedCards"] = list(voided_cost_uids)
    if scoped_lists:
        bstate["ability_lists"] = scoped_lists
    else:
        bstate.pop("ability_lists", None)
    logs = []
    try:
        guid_values = list(ability_guids or [])
        for ability_index, guid_value in enumerate(guid_values):
            instance_id = ability_index + 1
            guid = str(guid_value).lower()
            graph = ability_graph(DEFAULT_RECORD_STORE, guid)
            if graph is None:
                raise RuntimeError(f"played ability {guid} is missing from Records")
            # Session.ActivateAutomaticAbilities calls Card.GetAutomaticAbilities,
            # whose IsAutomatic predicate excludes manual, triggered, and
            # indexed abilities. Triggered abilities are dispatched from their
            # authored session events; replaying one here when PlayCardEffect
            # casts its source can recursively play the same card forever.
            if (graph.manual or graph.trigger_event_type or
                    int(graph.ability_index) >= 0 or not graph.effects):
                continue
            if (tac_int(graph.serialized_tac, "Scrounge", 0) and
                    not voided_cost_uids):
                continue
            activation = activation_map.get(guid)
            target_map = (dict(activation.target_map)
                          if activation is not None else {})
            if not target_map and target_uid is not None:
                for target_index, target in enumerate(graph.targets):
                    if target.requires_input:
                        target_map[target_index] = (int(target_uid),)
                        break
            state = resolve_port_ability(
                handler, game, session, db, player_uid, ai_uid, bstate,
                guid, source_uid, owner_id, target_map=target_map,
                variables=(activation.variables if activation is not None
                           else None), instance_id=instance_id,
                event_tac=spell_event_tac)
            logs.append(state.name if hasattr(state, "name") else state)
            if bstate.get("resolution_paused"):
                continuation = None
                for key in ("pending_deck_search", "pending_choice",
                            "pending_trigger", "pending_conversation",
                            "pending_discard_continuation"):
                    pending = bstate.get(key)
                    if not isinstance(pending, dict):
                        continue
                    candidate = pending.get("continuation")
                    if isinstance(candidate, dict):
                        continuation = candidate
                        break
                    if key == "pending_discard_continuation":
                        continuation = pending
                        break
                remaining = guid_values[ability_index + 1:]
                if continuation is not None and remaining:
                    # A picker can suspend one printed ability while later
                    # abilities on the played card remain pending. Preserve
                    # the original card-play event context through that
                    # continuation so authored conditions such as
                    # PlayedFromHand still evaluate when the sibling resumes.
                    continuation["event_tac"] = dict(spell_event_tac)
                    tail = continuation
                    visited = set()
                    while (isinstance(tail.get("parent"), dict) and
                           id(tail) not in visited):
                        visited.add(id(tail))
                        tail = tail["parent"]
                    for next_index, next_guid_value in enumerate(
                            remaining, ability_index + 2):
                        next_guid = str(next_guid_value).lower()
                        next_activation = activation_map.get(next_guid)
                        parent = {
                            "ability_guid": next_guid,
                            "source_uid": int(source_uid or 0),
                            "owner_id": int(owner_id or 0),
                            "ability_instance_id": next_index,
                            "instance_id": next_index,
                            "target_map": (dict(next_activation.target_map)
                                           if next_activation is not None else {}),
                            "variables": (dict(next_activation.variables or {})
                                          if next_activation is not None else {}),
                            "resume_effect_order": 0,
                            "event_tac": dict(spell_event_tac),
                        }
                        tail["parent"] = parent
                        tail = parent
                break
    finally:
        if bstate.get("resolution_paused"):
            bstate["_played_card_ability_lists_scope"] = {
                "source_uid": int(source_uid or 0),
                "previous_lists": previous_lists,
            }
        else:
            if previous_lists is None:
                bstate.pop("ability_lists", None)
            else:
                bstate["ability_lists"] = previous_lists
        if previous is None:
            bstate.pop("_esc_counted_this_resolution", None)
        else:
            bstate["_esc_counted_this_resolution"] = previous
        if not bstate.get("resolution_paused"):
            if previous_played_from_hand is None:
                bstate.pop("_spell_played_from_hand", None)
            else:
                bstate["_spell_played_from_hand"] = previous_played_from_hand
    return "; ".join(str(value) for value in logs if value)


def restore_played_card_ability_lists(battle_state, source_uid=None):
    """Restore ability lists after a played card's paused BOM completes."""
    state = battle_state or {}
    scope = state.get("_played_card_ability_lists_scope")
    if not isinstance(scope, dict):
        return
    try:
        scoped_source = int(scope.get("source_uid") or 0)
        requested_source = (None if source_uid is None
                            else int(source_uid or 0))
    except (TypeError, ValueError):
        return
    if requested_source is not None and scoped_source != requested_source:
        return
    previous_lists = scope.get("previous_lists")
    if previous_lists is None:
        state.pop("ability_lists", None)
    else:
        state["ability_lists"] = previous_lists
    state.pop("_played_card_ability_lists_scope", None)


def resolve_port_played_permanent(game, session, db, handler, player_uid,
                                  ai_uid, battle_state, item, owner_id, *,
                                  bom_completed=False):
    """Resolve a permanent's authored cast abilities before it enters play.

    A permanent may gain a triggered ability while being cast (Scrounge is a
    common example). Resolve its cast graphs while it is still in CastSpells,
    then let the caller publish the CastSpells -> Warzone transition so the
    entry trigger scan sees the newly granted abilities. A suspended cast is
    resumed by the chain continuation and calls back with ``bom_completed``.
    """
    state = battle_state or {}
    source_uid = int(item.get("source_uid") or 0)
    if bom_completed:
        restore_played_card_ability_lists(state, source_uid)
        for key in ("player_spell_target", "resolving_source_uid",
                    "resolving_owner_id", "x_cost"):
            state.pop(key, None)
        return ""

    ability_guids = item.get("ability_guids") or ()
    if not source_uid or not ability_guids:
        return ""

    state["player_spell_target"] = item.get("target_uid")
    state["resolving_source_uid"] = source_uid
    state["resolving_owner_id"] = int(owner_id or 0)
    state["x_cost"] = int(item.get("x_cost") or 0)
    result = resolve_port_played_spell(
        game, session, db, handler, player_uid, ai_uid, state,
        ability_guids, activations=item.get("activations"),
        played_from_hand=bool(item.get("played_from_hand")),
        voided_cost_uids=item.get("voided_cost_uids", ()))
    if not state.get("resolution_paused"):
        for key in ("player_spell_target", "resolving_source_uid",
                    "resolving_owner_id", "x_cost"):
            state.pop(key, None)
    return result














def run_interpreted_trigger(handler, game, session, db, player_uid, ai_uid, battle_state, guid,
                            source_uid, owner_uid, event_name, trigger_source_uid, event_data, answers=None,
                            target_player_uid=None, source_player_uid=None, instance_id=None,
                            target_card_uid=None):
    """Resolve a trigger's ability with the interpreter (rules_port/ir_exec.py) on the live battle state.

    Its effects go through the live primitives, so the outcome is the native resolver's. Returns the
    marker ``"interpreter"`` in place of the native resolver's state.

    ``instance_id`` is the chain item's activation id, which the native resolver publishes as
    ``resolving_ability_instance_id``; a shared leaf keys per-activation state on it (a granted random power is
    picked once per activation), so the interpreter publishes the same one.

    An ability the interpreter refuses at run time before it has sent any client event (a name or subtype it cannot
    read for a champion, say) returns None, and the caller resolves it on the native path. A refusal after an event
    was sent is re-raised: the effects have started, so they cannot be replayed."""
    from . import ir_exec
    from .context import EffectContext
    try:
        # The event hands the ability instance its named IntAttrs (CardDealtDamageEvent's DamageDealt), as the native
        # resolver seeds them, so an instance read ("a counter for each damage dealt") sees the event's values.
        event_tac = (dict(event_data or {}).get("event_tac") or {})
        instance_stats = {str(k): int(v or 0) for k, v in event_tac.items() if str(k) in _EVENT_INSTANCE_STATS}
        if instance_stats and instance_id is not None:
            from .statistics import seed_ability_stats
            seed_ability_stats(battle_state, instance_stats, instance_id=int(instance_id))
        # An item with no trigger event (a spell or an activated ability) runs with no event, not an empty event name,
        # which the interpreter would read as a trigger and judge by its zone.
        ir_exec.run_ability(
            guid, event_name or None,
            context=EffectContext.from_rules_port(
                game, session, db, handler, player_uid, ai_uid, battle_state, ""),
            source_uid=int(source_uid), owner_uid=int(owner_uid),
            event=ir_exec.live_event(event_name, int(owner_uid), event_data, trigger_source_uid,
                                     target_player_uid, source_player_uid, target_card_uid=target_card_uid),
            answers=answers,
            instance_id=None if instance_id is None else int(instance_id))
    except ir_exec.NeedsChoice as pause:
        return prompt_interpreted_reveal(handler, game, session, db, player_uid, ai_uid, battle_state, pause)
    except ir_exec.NeedsConversation:
        # The leaf queued the conversation and saved the pause; the client's answer resumes the interpreter.
        return "interpreter"
    return "interpreter"


def resolve_port_trigger(handler, game, session, db, player_uid, ai_uid,
                         battle_state, item):
    """Resolve one chain trigger without re-entering the legacy BOM walker."""
    from gamedata import DEFAULT_RECORD_STORE, ability_graph

    guid = str(item.get("ability_guid") or "").lower()
    if not guid:
        return ""
    graph = ability_graph(DEFAULT_RECORD_STORE, guid)
    if graph is None:
        raise RuntimeError(f"ability {guid} is missing from current Records")
    source_uid = item.get("source_uid")
    owner_id = item.get("source_owner_uid")
    if owner_id is None and source_uid is not None:
        from pvp_db import db_card_owner_id
        owner_id = db_card_owner_id(
            session.session_id, int(source_uid), conn=db)
    if owner_id is None:
        owner_id = 0
        pchamp = getattr(handler, "_player_champ_scid", None)
        achamp = getattr(handler, "_ai_champ_scid", None)
        if pchamp is not None and source_uid is not None and int(
                pchamp.uid.uid64) == int(source_uid):
            owner_id = handler.user_profile["id"] if handler.user_profile else 0
        elif achamp is not None and source_uid is not None and int(
                achamp.uid.uid64) == int(source_uid):
            owner_id = 0
    activated = item.get("activated_ability_guid")
    if activated:
        battle_state["card_activated_item"] = {
            "kind": "ability", "ability_guid": activated,
            "source_uid": item.get("activated_source_uid"),
            "target_uid": item.get("activated_target_uid"),
            "instance_id": item.get("activated_ability_instance_id"),
            "activation_data": item.get("activated_activation_data") or {},
        }
    # ``target_uid`` is the card chosen for the authored input-bearing target
    # (a player picker, or the AI's legal pool), while ``trigger_target_uid``
    # is the card that raised the event.  They differ whenever a triggered
    # ability chooses "another" card: preferring the event card resolved the
    # effect against the trigger's own source (Armitron's Deploy buffed
    # Armitron instead of the selected Robot).  Keep the event card only for
    # the templates that name it, such as AbilityTriggerCardTargetTemplate.
    selected_target = item.get("target_uid")
    # Keep the authored event target distinct from the card selected for this
    # trigger's effect target. C# TriggerTargetPropertyVariable reads the
    # triggering event, even when the ability also selected another card.
    trigger_target = item.get("trigger_target_uid")
    activation_data = item.get("activation_data") or {}
    target_map = (dict(activation_data.get("target_map") or {})
                  if isinstance(activation_data, dict) else {})
    if not target_map and selected_target is not None:
        from .targeting import is_chosen_secondary_target
        for index, spec in enumerate(graph.targets):
            # A "one of them" target is chosen at resolution; the item's own target is not its answer.
            if spec.requires_input and not is_chosen_secondary_target(spec):
                target_map[index] = int(selected_target)
                break
    answers = _answers_from_target_map(target_map)
    old_source = battle_state.get("resolving_source_uid")
    old_owner = battle_state.get("resolving_owner_id")
    old_target = battle_state.get("resolving_target_uid")
    old_trigger_source = battle_state.get("resolving_trigger_source_uid")
    old_trigger_target = battle_state.get("resolving_trigger_target_uid")
    old_event_type = battle_state.get("resolving_trigger_event_type")
    old_event_data = battle_state.get("resolving_trigger_event_data")
    battle_state["resolving_source_uid"] = source_uid
    battle_state["resolving_owner_id"] = owner_id
    # The legacy trigger boundary exposed the trigger's selected card as the
    # transient target so an #TRIGGER_SOURCE#/#TRIGGER_TARGET# template whose
    # event class has no TargetCardId could still resolve the card that raised
    # it.  Publish the same alias for the remainder of this resolution.
    if selected_target is None:
        battle_state.pop("resolving_target_uid", None)
    else:
        battle_state["resolving_target_uid"] = int(selected_target)
    trigger_source = item.get("trigger_source_uid")
    if trigger_source is None:
        battle_state.pop("resolving_trigger_source_uid", None)
    else:
        battle_state["resolving_trigger_source_uid"] = int(trigger_source)
    # C# keeps the triggering event on the ability instance, so a target
    # template of kind AbilityTriggerCardTargetTemplate ("TriggerSource")
    # still resolves the event's card when the trigger waits on the chain for
    # priority instead of resolving inline.
    if trigger_target is None:
        battle_state.pop("resolving_trigger_target_uid", None)
    else:
        battle_state["resolving_trigger_target_uid"] = int(trigger_target)
    battle_state["resolving_trigger_event_type"] = str(
        item.get("trigger_event_type") or "")
    event_data = item.get("trigger_event_data") or {}
    battle_state["resolving_trigger_event_data"] = (
        dict(event_data) if isinstance(event_data, dict) else {})
    try:
        from . import ir_exec
        event_name = str(item.get("trigger_event_type") or "").rsplit(".", 1)[-1]
        interpreted = None
        if ir_exec.engine_runs(guid, source_uid, owner_id, answers=answers):
            interpreted = run_interpreted_trigger(
                handler, game, session, db, player_uid, ai_uid, battle_state,
                guid, source_uid, owner_id, event_name,
                item.get("trigger_source_uid"),
                event_data if isinstance(event_data, dict) else {}, answers=answers,
                target_player_uid=item.get("trigger_target_player_uid"),
                source_player_uid=item.get("trigger_source_player_uid"),
                instance_id=item.get("instance_id"),
                target_card_uid=item.get("trigger_target_uid"))
        if interpreted is not None:
            state = interpreted
        else:
            state = resolve_port_ability(
                handler, game, session, db, player_uid, ai_uid, battle_state,
                guid, source_uid, owner_id, target_map=target_map,
                event_tac=(event_data.get("event_tac")
                           if isinstance(event_data, dict) else None),
                instance_id=int(item.get("instance_id", 1)))
        if not battle_state.get("resolution_paused"):
            # A queued trigger resolves here (mulligan/setup drains, the port's
            # chain resolver, and the PvP projections).  Consume an authored
            # ONE-SHOT once its effect has applied, exactly like the inline
            # trigger path, so the card loses the used ability on both sides.
            from .triggers import consume_one_shot_trigger
            consume_one_shot_trigger(
                handler, session, game, db, player_uid, ai_uid, battle_state,
                guid, source_uid)
        return state.name if hasattr(state, "name") else str(state)
    finally:
        battle_state["resolving_source_uid"] = old_source
        battle_state["resolving_owner_id"] = old_owner
        if old_target is None:
            battle_state.pop("resolving_target_uid", None)
        else:
            battle_state["resolving_target_uid"] = old_target
        if old_trigger_source is None:
            battle_state.pop("resolving_trigger_source_uid", None)
        else:
            battle_state["resolving_trigger_source_uid"] = old_trigger_source
        if old_trigger_target is None:
            battle_state.pop("resolving_trigger_target_uid", None)
        else:
            battle_state["resolving_trigger_target_uid"] = old_trigger_target
        if old_event_type is None:
            battle_state.pop("resolving_trigger_event_type", None)
        else:
            battle_state["resolving_trigger_event_type"] = old_event_type
        if old_event_data is None:
            battle_state.pop("resolving_trigger_event_data", None)
        else:
            battle_state["resolving_trigger_event_data"] = old_event_data
