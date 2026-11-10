"""Target values for the native resolver and the interpreter: the cards a target template names, the matches of
a secondary target, the random sample of a candidate pool, and the answers of a chosen target map."""

from __future__ import annotations

from typing import Any

from .runtime_helpers import as_int
from .runtime_helpers import raw_uid


def _random_target_sample(candidates, count, battle_state):
    """Port of ``AbilityTargetTemplate.FilterRandomTargets``.

    A random auto-target resolves from the full legal pool but the effect
    applies to at most ``count`` cards chosen with the session RNG.  ``count``
    of 0 or less means "unlimited" (C# ``GetMaximumTargetCount`` returns
    ``int.MaxValue`` when unset).  The C# client does a partial Fisher-Yates:
    for ``i`` from ``n`` down to ``n - count + 1`` it picks ``rng.Next(i)``,
    swaps that slot with slot ``i-1`` and keeps the picked card.  The loop
    always runs (even when it consumes the whole pool), so the RNG call
    sequence matches the client for replay parity.
    """
    pool = list(candidates)
    total = len(pool)
    if total == 0:
        return ()
    count = int(count or 0)
    wanted = total if count <= 0 else min(total, count)
    rng = (battle_state or {}).get("_rules_rng")
    if rng is None or not hasattr(rng, "next"):
        import random
        return tuple(random.sample(pool, wanted))
    picked = []
    for i in range(total, total - wanted, -1):
        index = int(rng.next(i)) % i
        picked.append(pool[index])
        pool[index] = pool[i - 1]
    return tuple(picked)


def _uids(values):
    out = []
    for value in values or ():
        try:
            if value is not None:
                out.append(int(value))
        except (TypeError, ValueError):
            continue
    return tuple(out)


_LIST_ATTR_BY_KIND = {
    "SourceDrawnTargetTemplate": "DrawnCards",
    "SourceBuriedTargetTemplate": "BuriedCards",
    "AbilityCreatedTargetTemplate": "CreatedCards",
    "VoidedTargetTemplate": "VoidedCards",
}


def _list_target_values(battle_state, ability, kind, source_uid, target_spec,
                        db, session_id):
    """Resolve the per-ability stored/drawn/buried/voided/created card lists.

    C# SourceStored/Drawn/Buried/Created/VoidedTargetTemplate enumerate the
    authored ``ListAttrs`` recorded on the ability instance (or the card).
    The port previously left these kinds empty, so "the card you stored /
    voided / created" resolved to no target.
    """
    state = battle_state or {}
    raw_guid = str(getattr(ability, "ability_template_id", "") or "")
    guid = raw_guid.lower()
    from gamedata import DEFAULT_RECORD_STORE
    record = DEFAULT_RECORD_STORE.get(
        "AbilityTargetTemplate", str(target_spec.guid).lower())

    def field(name, default=None):
        return record.field(name, default) if record is not None else default

    if kind == "SourceStoredTargetTemplate":
        values: list[Any] = []
        if bool(field("m_StoredInAbility", True)):
            for key in ("stored_targets", "stored_targets_this_turn"):
                store = state.get(key) or {}
                values.extend(store.get(guid, store.get(raw_guid, ())) or ())
            values.extend((state.get("ability_lists") or {}).get(
                "StoredTargets", ()) or ())
            values.extend(((state.get("list_attrs") or {}).get(guid) or {}).get(
                "StoredTargets", ()) or ())
        if bool(field("m_StoredInCard", True)):
            for key in ("stored_targets_by_card",
                        "stored_targets_by_card_this_turn"):
                store = state.get(key) or {}
                values.extend(store.get(str(int(source_uid or 0)),
                                        store.get(int(source_uid or 0), ())) or ())
            from .statistics import tac_list
            for scope in ("PermanentData", "ThisTurnsData"):
                values.extend(tac_list(
                    state, "cards", int(source_uid or 0), scope,
                    "StoredTargets"))
        return _uids(values)

    list_name = (str(field("m_ListAttrName", "BuriedCards") or
                     "BuriedCards")
                 if kind == "SourceBuriedTargetTemplate" else
                 _LIST_ATTR_BY_KIND.get(kind, ""))
    lists = state.get("list_attrs") or {}
    entries = (lists.get(guid) or {}).get(list_name) or []
    out = list(_uids((state.get("ability_lists") or {}).get(list_name, ())))
    for entry in entries:
        uid = (entry.get("source_uid", entry.get("Id", entry.get("id")))
               if isinstance(entry, dict) else entry)
        try:
            if uid is not None:
                out.append(int(uid))
        except (TypeError, ValueError):
            continue

    if (kind == "VoidedTargetTemplate" and field("m_VoidedBy") is None
            and source_uid is not None):
        # "put each card voided by it into play": the source card's own void
        # ledger, mirroring the legacy resolver's ``voided_by`` fallback.
        out.extend(_uids((state.get("voided_by") or {}).get(
            str(int(source_uid)))))

    if kind == "VoidedTargetTemplate" and field("m_VoidedBy") is not None:
        from .targeting import _source_card
        from .filters import records_filter_matches
        source = _source_card(db, session_id, source_uid,
                              ability.responsible_player_id)
        voided_by_filter = field("m_VoidedBy")
        card_filter = field("m_CardFilter")
        out = []
        voided_by_kind = str(
            getattr(voided_by_filter, "type_name", "")
            or (voided_by_filter.get("_t", "")
                if isinstance(voided_by_filter, dict) else "")
        ).rsplit(".", 1)[-1]
        for voider_uid, targets in (state.get("voided_by") or {}).items():
            try:
                voider = _source_card(
                    db, session_id, int(voider_uid),
                    ability.responsible_player_id)
                # ``IsAbilitySource`` is an identity predicate: the voider must
                # be the ability's own source card.  Evaluating it through the
                # generic filter context picked up a stale event source and
                # matched every voider.
                if voided_by_kind == "IsAbilitySource":
                    matched = int(voider_uid) == int(source_uid or 0)
                else:
                    matched = bool(voider and records_filter_matches(
                        voider, voided_by_filter, source=source,
                        context=state))
                if matched:
                    for uid in _uids(targets):
                        target = _source_card(
                            db, session_id, uid,
                            ability.responsible_player_id)
                        if (not target or str(target.get("location") or "")
                                .lower() != "void"):
                            continue
                        if card_filter and not records_filter_matches(
                                target, card_filter, source=source,
                                context=state,
                                player=int(ability.responsible_player_id or 0)):
                            continue
                        out.append(uid)
            except (TypeError, ValueError):
                continue

    # These special list templates still inherit CardFilter and collection
    # semantics.  The VoidedBy form instead filters the card which caused the
    # void and the client does not apply CollectionFlags in that branch.
    if kind != "VoidedTargetTemplate" or field("m_VoidedBy") is None:
        from .targeting import _source_card, template_zones
        from .filters import records_filter_matches
        zones = {zone.lower() for zone in template_zones({
            "collection_flags": str(field("m_CollectionFlags", "None") or "")})}
        source = _source_card(db, session_id, source_uid,
                              ability.responsible_player_id)
        card_filter = field("m_CardFilter")
        filtered = []
        for uid in dict.fromkeys(out):
            card = _source_card(db, session_id, uid,
                                ability.responsible_player_id)
            if not card:
                continue
            if zones and str(card.get("location") or "").lower() not in zones:
                continue
            if card_filter and not records_filter_matches(
                    card, card_filter, source=source, context=state,
                    player=int(ability.responsible_player_id or 0)):
                continue
            filtered.append(int(uid))
        out = filtered
    return tuple(dict.fromkeys(out))


def _shares_subtype(left, right):
    left = {part.strip().lower() for part in str(left or "").split()
            if part.strip() and part.strip().lower() != "of"}
    right = {part.strip().lower() for part in str(right or "").split()
             if part.strip() and part.strip().lower() != "of"}
    return bool(left & right)


def _current_match_card(db, session_id, uid, owner_id, battle_state):
    """Project the properties used by C# Card.SharesSubtype/Cost."""
    from .targeting import _source_card
    card = _source_card(db, session_id, int(uid), owner_id)
    if not card:
        return None
    try:
        from .static_rules import effective_card_properties, effective_cost
        thresholds, subtype = effective_card_properties(
            db, session_id, battle_state or {}, int(uid))
        card["thresholds"] = thresholds
        card["shards"] = thresholds
        card["subtype"] = subtype
        card["cost"] = effective_cost(
            db, session_id, battle_state or {}, int(uid))
    except (AttributeError, TypeError, ValueError, RuntimeError):
        # Basic card rows are still usable in compact test/older schemas; the
        # native live path supplies the current effective values above.
        pass
    return card


def _match_secondary_values(db, session_id, ability, effect, target_spec,
                            battle_state, champions=None,
                            resolved_by_instance=None):
    """Port of ``MatchSecondaryTargetTemplate.EnumerateLegalTargets``.

    Emits the legal cards that are related to the contingent effect's resolved
    target under the authored SameCost/SameOwner/SharesRace/DoesntShareRace/
    SameName/CantBePreviousTarget flags.  The port previously resolved this
    template as "every card in the collection".
    """
    from gamedata import DEFAULT_RECORD_STORE
    rec = DEFAULT_RECORD_STORE.get("AbilityTargetTemplate", target_spec.guid)
    if rec is None:
        return ()

    def flag(name):
        try:
            return bool(rec.field(name))
        except (AttributeError, TypeError, ValueError):
            return False

    same_cost = flag("m_SameCost")
    same_owner = flag("m_SameOwner")
    shares_race = flag("m_SharesRace")
    doesnt_share_race = flag("m_DoesntShareRace")
    same_name = flag("m_SameName")
    cant_be_prev = flag("m_CantBePreviousTarget")
    sec_value = (effect.get("secondary_target_index", -1)
                 if isinstance(effect, dict)
                 else getattr(effect, "secondary_target_index", -1))
    sec = -1 if sec_value is None else int(sec_value)
    if sec < 0:
        return ()

    def fix_champion_owner(card, uid):
        """Give synthetic champion targets their persisted controller ID.

        Champions have no ``game_cards`` row.  The generic source-card
        projection therefore falls back to the resolving player's ID, which
        can make a MatchSecondary ``m_SameOwner`` target match the wrong
        player's cards (for example, a Vampire's "their hand" reveal).
        """
        if not card:
            return card
        owner = None
        for raw_owner, raw_uid in (battle_state.get("champ_map") or {}).items():
            try:
                if int(raw_uid) == int(uid):
                    owner = int(raw_owner)
                    break
            except (TypeError, ValueError):
                continue
        if owner is None:
            for row in champions or ():
                try:
                    if int(row[0]) == int(uid):
                        owner = int(row[1])
                        break
                except (IndexError, TypeError, ValueError):
                    continue
        if owner is not None:
            card["user_id"] = owner
            card["owner_id"] = owner
            card["controller_id"] = owner
        return card

    raw = _resolved_effect_target_values(
        ability, sec, battle_state, resolved_by_instance)
    from .targeting import legal_targets
    secondary = []
    for value in raw:
        if value is None:
            continue
        view = _current_match_card(
            db, session_id, int(value), ability.responsible_player_id,
            battle_state)
        if view:
            fix_champion_owner(view, value)
            secondary.append((int(value), view))
    if not secondary:
        return ()
    candidates = legal_targets(
        db, session_id, ability.responsible_player_id, target_spec.guid,
        ability.source_uid, both_players=True, champions=champions,
        battle_state=battle_state)
    out = []
    for uid in candidates:
        view = _current_match_card(
            db, session_id, int(uid), ability.responsible_player_id,
            battle_state)
        if not view:
            continue
        fix_champion_owner(view, uid)
        for other_uid, other in secondary:
            if cant_be_prev and int(uid) == other_uid:
                continue
            if same_cost and int(view.get("cost") or 0) != int(
                    other.get("cost") or 0):
                continue
            if same_owner and int(view.get("user_id") or 0) != int(
                    other.get("user_id") or 0):
                continue
            if same_name and str(view.get("name") or "").lower() != str(
                    other.get("name") or "").lower():
                continue
            if shares_race and not _shares_subtype(
                    view.get("subtype"), other.get("subtype")):
                continue
            if doesnt_share_race and _shares_subtype(
                    view.get("subtype"), other.get("subtype")):
                continue
            out.append(int(uid))
            break
    return tuple(out)


def _resolved_target_values(ability, index, battle_state=None):
    """Return the cards already resolved for one activation mapping index.

    ``ActivateAbility``/``MoveCardToZone`` effects can reference a previous
    effect's target ("the remaining cards", ``m_SecondaryTargetIndex``).  The
    client resolves those from the *target instance* of that mapping, so the
    resolver must read the activation map instead of re-enumerating that
    template's whole legal pool.
    """
    if index is None:
        return ()
    try:
        key = int(index)
    except (TypeError, ValueError):
        return ()
    if key < 0:
        return ()
    maps = (
        getattr(getattr(ability, "activation", None), "target_map", {}) or {},
        (battle_state or {}).get("ability_target_map") or {},
    )
    for mapping in maps:
        value = mapping.get(key, mapping.get(str(key)))
        if value is None:
            continue
        if isinstance(value, (tuple, list, set)):
            return tuple(int(item) for item in value if item is not None)
        return (int(value),)
    return ()


def _referenced_effect_target_index(ability, instance_id):
    """Return the activation target slot for an effect instance reference."""
    try:
        wanted = int(instance_id)
    except (TypeError, ValueError):
        return None
    if wanted < 0:
        return None
    for candidate in getattr(ability, "ordered_effects", ()) or ():
        if isinstance(candidate, dict):
            candidate_id = candidate.get(
                "effect_instance_id", candidate.get("instance_id", -1))
            target_index = candidate.get("target_index", -1)
        else:
            candidate_id = getattr(
                candidate, "effect_instance_id",
                getattr(candidate, "instance_id", -1))
            target_index = getattr(candidate, "target_index", -1)
        try:
            if as_int(candidate_id) != wanted:
                continue
            target_index = int(target_index)
        except (TypeError, ValueError):
            continue
        return target_index if target_index >= 0 else None
    return None


def _resolved_effect_target_values(ability, instance_id, battle_state=None,
                                  resolved_by_instance=None):
    """Resolve ``m_SecondaryTargetIndex`` through an effect instance.

    The authored field is an effect-instance id, not an activation target-map
    slot. Most abilities happen to use the same numbers for both, which hid
    this distinction until an effect such as Herofall stored its first target
    in instance 1 while using target slot 0.
    """
    try:
        wanted = int(instance_id)
    except (TypeError, ValueError):
        return ()
    if wanted < 0:
        return ()
    if resolved_by_instance is not None:
        value = resolved_by_instance.get(wanted)
        if value is None:
            value = resolved_by_instance.get(str(wanted))
        if value is not None:
            return _uids(value)
    target_index = _referenced_effect_target_index(ability, wanted)
    if target_index is None:
        return ()
    return _resolved_target_values(ability, target_index, battle_state)


def _answers_from_target_map(target_map: dict[Any, Any]) -> dict[int, int | list[int] | None]:
    """A chain item's target map as the interpreter's answers: each target index to the card chosen, the list of cards
    when several were chosen, or None."""
    answers: dict[int, int | list[int] | None] = {}
    for index, value in (target_map or {}).items():
        values = value if isinstance(value, (list, tuple, set)) else (value,)
        chosen = [int(item) for item in values if item is not None and str(item).lstrip("-").isdigit()]
        answers[int(index)] = chosen if len(chosen) > 1 else (chosen[0] if chosen else None)
    return answers

