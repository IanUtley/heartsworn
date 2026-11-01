"""Data-driven targeting — a Python port of the client's
Game.Shared.Mechanics.Cards.Filters + Abilities.TargetTemplates evaluation,
driven by the target_templates table (seeded from AbilityTargetTemplate.jsonl).

This is the first ported layer of the client rules engine: instead of
hardcoded/heuristic target checks, legality comes from the gamedata target
template (explicit/auto, collection flags, min/max counts, card filter) — e.g.
Solitary Exile's Deploy "void another target card" is explicit, 1 target,
Warzone, any card type except the ability source.
"""

import json
import re
from pathlib import Path


ZONE_MAP = {
    "Warzone": "warzone",
    "Hand": "hand",
    "Deck": "deck",
    "Crypt": "discard",
    "Discard": "discard",
    "Void": "void",
    "Champions": "champions",
    "CastSpells": "CastSpells",
    "Underground": "underground",
    "PlayedResources": "PlayedResources",
    "Choosing": "choosing",
}

ALL_TARGET_ZONES = tuple(ZONE_MAP.values())

_TEMPLATE_FACTIONS = None
_RECORD_GUIDS = {}
_EQUIPMENT_CARD_INFO = None
_EQUIPMENT_TYPES = None


def template_faction(template_guid):
    """Return the authoritative faction for a card template.

    ``card_templates`` predates faction being materialized as a DB column.
    CardFilter.InFaction is nevertheless part of the typed gamedata and is
    used by Conscript and several random-card effects.  Read the extracted
    CardTemplate records lazily and cache the small GUID -> faction map.
    """
    global _TEMPLATE_FACTIONS
    guid = str(template_guid or "").lower()
    if not guid:
        return ""
    if _TEMPLATE_FACTIONS is None:
        _TEMPLATE_FACTIONS = {}
        path = Path(__file__).resolve().parents[2] / "hex-private" / "Records" / \
            "CardTemplate.jsonl"
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        value = json.loads(line)
                        if isinstance(value, str):
                            value = json.loads(
                                re.sub(r",\s*([}\]])", r"\1", value))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if not isinstance(value, dict):
                        continue
                    card_id = value.get("m_Id") or {}
                    card_guid = str(card_id.get("m_Guid") or "").lower()
                    if card_guid:
                        _TEMPLATE_FACTIONS[card_guid] = str(
                            value.get("m_Faction") or "")
        except OSError:
            pass
    return _TEMPLATE_FACTIONS.get(guid, "")


def template_is_mercenary(template_guid):
    """Match IsMercenaryFilter against the extracted champion templates."""
    guid = str(template_guid or "").lower()
    if not guid:
        return False
    values = _RECORD_GUIDS.get("MercenaryTemplate")
    if values is None:
        values = set()
        path = Path(__file__).resolve().parents[2] / "hex-private" / "Records" / \
            "MercenaryTemplate.jsonl"
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        value = json.loads(line)
                        if isinstance(value, str):
                            value = json.loads(
                                re.sub(r",\s*([}\]])", r"\1", value))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if not isinstance(value, dict):
                        continue
                    ident = value.get("m_Id") or {}
                    if ident.get("m_Guid"):
                        values.add(str(ident["m_Guid"]).lower())
        except OSError:
            pass
        _RECORD_GUIDS["MercenaryTemplate"] = values
    return guid in values


def template_equipment_match(template_guid, equipment_type="None"):
    """Match the client's IsEquippedCardFilter from CardTemplate TAC data."""
    global _EQUIPMENT_CARD_INFO, _EQUIPMENT_TYPES
    guid = str(template_guid or "").lower()
    if not guid:
        return False
    if _EQUIPMENT_CARD_INFO is None:
        from .tac import _tac_attr_hash, decode_tac_tree
        _EQUIPMENT_CARD_INFO = {}
        path = Path(__file__).resolve().parents[2] / "hex-private" / "Records" / \
            "CardTemplate.jsonl"
        required_hash = _tac_attr_hash("RequiredEquipment")
        guid_hash = _tac_attr_hash("Guid")
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        value = json.loads(line)
                        if isinstance(value, str):
                            value = json.loads(
                                re.sub(r",\s*([}\]])", r"\1", value))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if not isinstance(value, dict):
                        continue
                    ident = value.get("m_Id") or {}
                    card_guid = str(ident.get("m_Guid") or "").lower()
                    if not card_guid:
                        continue
                    if not value.get("m_EquipmentModifiedCard"):
                        continue
                    tac = value.get("m_SerializedTAC") or {}
                    data = tac.get("data", "") if isinstance(tac, dict) else ""
                    tree = decode_tac_tree(data)
                    required = set()
                    for entry in tree.get(required_hash, []) if isinstance(
                            tree.get(required_hash), list) else []:
                        if isinstance(entry, dict) and isinstance(
                                entry.get(guid_hash), str):
                            required.add(entry[guid_hash].lower())
                    _EQUIPMENT_CARD_INFO[card_guid] = required
        except OSError:
            pass
    required = _EQUIPMENT_CARD_INFO.get(guid)
    if required is None:
        return False
    if str(equipment_type or "None").lower() == "none":
        return True
    if _EQUIPMENT_TYPES is None:
        _EQUIPMENT_TYPES = {}
        path = Path(__file__).resolve().parents[2] / "hex-private" / "Records" / \
            "InventoryItemData.jsonl"
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if "InventoryEquipmentData" not in line:
                        continue
                    try:
                        value = json.loads(line)
                        if isinstance(value, str):
                            value = json.loads(
                                re.sub(r",\s*([}\]])", r"\1", value))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if not isinstance(value, dict):
                        continue
                    ident = value.get("m_Id") or {}
                    item_guid = str(ident.get("m_Guid") or "").lower()
                    if item_guid and value.get("m_EquipmentType"):
                        _EQUIPMENT_TYPES[item_guid] = str(
                            value["m_EquipmentType"]).lower()
        except OSError:
            pass
    wanted = str(equipment_type).lower()
    return any(_EQUIPMENT_TYPES.get(item) == wanted for item in required)


def shards_from_threshold(threshold_json):
    """Parse a template's threshold_json into ECardShards flags, e.g.
    {"list": [5]} -> [Diamond]."""
    try:
        d = json.loads(threshold_json or "{}")
    except Exception:
        return []
    idx_flags = {0: 0, 1: 4, 2: 8, 3: 16, 4: 32, 5: 64}
    if isinstance(d, list):
        items = d
    else:
        items = d.get("list") or d.get("values") or []
    return [idx_flags.get(int(i), 0) for i in items]


def _last(t):
    return str(t or "").split(".")[-1]


def _find_filter_type(node, filter_type):
    """Find the first metadata filter of *filter_type* in a filter tree."""
    if isinstance(node, dict):
        if _last(node.get("_t")) == filter_type:
            return node
        for child in node.values():
            found = _find_filter_type(child, filter_type)
            if found is not None:
                return found
    elif isinstance(node, list):
        for child in node:
            found = _find_filter_type(child, filter_type)
            if found is not None:
                return found
    return None


def _blocking_targets(battle_state, source_uid):
    """Return troops currently blocking ``source_uid``.

    ``ai_blockers`` is the attacker -> blocker assignment used by PvE, while
    PvP stores the same relationship as ``blockers``. The key is the attacking
    troop and the values are the defending troops, regardless of which side is
    controlled by the AI. The client calls this relationship
    ``BlockingFilter(IsAbilitySource)``.
    """
    if not battle_state or source_uid is None:
        return set()
    try:
        source_uid = int(source_uid)
    except (TypeError, ValueError):
        return set()
    attacking = set()
    for key in ("ai_attackers", "player_attackers", "attackers"):
        for attacker in (battle_state.get(key) or {}):
            try:
                attacking.add(int(attacker))
            except (TypeError, ValueError):
                pass
    if source_uid not in attacking:
        return set()
    for blocker_map in (battle_state.get("ai_blockers") or {},
                        battle_state.get("blockers") or {}):
        for attacker, blockers in blocker_map.items():
            try:
                if int(attacker) != source_uid:
                    continue
            except (TypeError, ValueError):
                continue
            result = set()
            for blocker in blockers or []:
                try:
                    result.add(int(blocker))
                except (TypeError, ValueError):
                    continue
            return result
    return set()


def _filter_zones(collections):
    return {ZONE_MAP.get(z, z.lower()) for z in
            str(collections or "").split("|") if z}


_OWNERSHIP_FILTERS = {"IsControlledBy", "IsNotControlledBy", "DifferentOwners"}


def _filter_type_names(node, acc):
    if isinstance(node, dict):
        name = str(node.get("_t", "")).rsplit(".", 1)[-1]
        if name:
            acc.add(name)
        for child in node.get("m_TargetFilters") or ():
            _filter_type_names(child, acc)
        _filter_type_names(node.get("m_TargetFilter"), acc)
    return acc


def _ownership_context_is_incomplete(card, filter_json, source):
    """Return True when only the legacy side fallback can resolve ownership."""
    if not (_filter_type_names(filter_json, set()) & _OWNERSHIP_FILTERS):
        return False
    if isinstance(card, dict) and card.get("src_owner_id") is not None:
        return False
    if source is None:
        return True
    return not any(source.get(key) is not None for key in
                   ("user_id", "owner_id", "controller_id", "player_id"))


def _evaluate_card_filter_via_port(card, filter_json, source_uid,
                                   stored_names, source_card, card_pool,
                                   ability_state, db):
    from rules_port.filters import records_filter_matches
    source = source_card
    if source is None and source_uid is not None:
        source = {"card_uid": source_uid}
    if _ownership_context_is_incomplete(card, filter_json, source):
        raise ValueError("legacy ownership context is incomplete")
    context = ability_state if isinstance(ability_state, dict) else {}
    extra = {}
    if stored_names is not None:
        extra["stored_names"] = stored_names
    if card_pool is not None:
        extra["all_cards"] = tuple(card_pool)
    if db is not None:
        extra["db"] = db
    if extra:
        context = {**context, **extra}
    # Ownership filters compare numeric owner ids: the player is the source's
    # owner, falling back to the card's own owner id.
    player = None
    if isinstance(source, dict) and source.get("user_id") is not None:
        player = source["user_id"]
    elif isinstance(card, dict):
        player = card.get("src_owner_id", card.get("src_owner_side"))
    return records_filter_matches(card, filter_json, source=source,
                                  context=context, player=player)


def evaluate_card_filter(card, filter_json, source_uid, stored_names=None,
                         source_card=None, card_pool=None, champion_pool=None,
                         ability_state=None, db=None):
    """Evaluate a gamedata CardFilter tree against one card.

    ``card`` is a dict with at least card_uid, card_type, location, user_id,
    attack, defense.  The native RulesPort evaluator owns the semantics; an
    ownership filter whose context is incomplete raises instead of guessing.
    """
    if not isinstance(filter_json, dict):
        return True
    return _evaluate_card_filter_via_port(
        card, filter_json, source_uid, stored_names, source_card,
        card_pool, ability_state, db)


def _side_of(user_id):
    return "ai" if not user_id else "player"


def target_template(db, template_id):
    """Return the target_templates row as a dict, or None."""
    from pvp_db import db_target_template_row
    row = db_target_template_row(template_id, conn=db)
    if not row:
        return None
    return {
        "template_id": row[0], "game_text": row[1],
        "is_auto_target": row[2], "is_random_target": row[3],
        "optional": row[4], "explicit": row[5],
        "player_filter": row[6], "collection_flags": row[7],
        "min_target_count": row[8], "max_target_count": row[9],
        "filter_json": row[10] or "{}",
        "target_kind": row[11] or "",
    }


def target_uses_both_players(db, template_id):
    """Return whether a target template may draw candidates from both sides.

    ``SinglePlayer`` describes cardinality, not ownership.  Ownership is
    expressed by the template's player filter and/or card filter.  The PvP
    option builders must therefore not turn every single-player target into a
    self-only target; doing so hides valid opposing champions and troops.
    """
    tpl = target_template(db, template_id)
    if not tpl:
        return False
    return (tpl.get("player_filter") or "").lower() not in {
        "self", "you", "controller",
    }


def legal_targets(db, session_id, controller_uid, template_id, source_uid,
                  both_players=False, champions=None, battle_state=None):
    """Return the card_uid list the client should be offered for a target
    template: every card in the template's collections that passes its filter.

    ``controller_uid`` is the DB user_id of the ability's controller (0 = AI).
    ``both_players`` includes the opponent's cards in the candidate pool (the
    target template's card filter decides legality — e.g. Solitary Exile's
    Deploy may void any warzone card that isn't itself).
    ``champions`` is an optional list of (card_uid, user_id, name, health)
    tuples for the two champions — the client can target champions (IsHero
    filters), so they join the candidate pool when the template allows it.
    ``battle_state`` optionally supplies transient per-turn filter data, such
    as the cards that dealt damage to an opposing champion this turn.
    """
    tpl = target_template(db, template_id)
    if not tpl:
        return []
    # ``AbilitySourceCardTargetTemplate`` means literal ``this``.  Its
    # collection flags are authored for the card's normal zone (often
    # Warzone), but self-triggered abilities can resolve after the source has
    # moved to Underground or Discard.  The source identity is authoritative
    # at this boundary; do not lose the target merely because its current
    # collection differs from the template's visibility mask.
    if (str(tpl.get("target_kind") or "") ==
            "AbilitySourceCardTargetTemplate" and source_uid is not None):
        return [int(source_uid)]
    try:
        fjson = json.loads(tpl["filter_json"] or "{}")
    except Exception:
        fjson = {}

    # Filters such as HasSourceResourceCost and shared-source filters are
    # evaluated against the live source card by the original client.  Build
    # that context once and pass it through every candidate evaluation below;
    # previously those filters silently saw ``None`` and defaulted open.
    source_card = None
    if source_uid is not None:
        from pvp_db import db_target_source_row
        source_row = db_target_source_row(session_id, int(source_uid), conn=db)
        if source_row:
            source_card = {
                "card_uid": int(source_row[0]),
                "card_type": source_row[1] or "",
                "location": source_row[2] or "",
                "user_id": source_row[3],
                "state": int(source_row[4] or 0),
                "attack": int(source_row[5] or 0),
                "defense": int(source_row[6] or 0),
                "template_guid": source_row[7] or "",
                "name": source_row[8] or "",
                "cost": int(source_row[9] or 0),
                "subtype": source_row[10] or "",
                "rarity": "",
                "shards": shards_from_threshold(source_row[11]),
                "attributes": int(source_row[12] or 0),
                "faction": template_faction(source_row[7]),
                "rarity": source_row[13] or "",
                "socket_count": int(source_row[14] or 0),
                "gems": int(source_row[15] or 0),
                "original_template_guid": source_row[16] or "",
                "card_abilities": json.loads(source_row[17] or "[]")
                if isinstance(source_row[17], str) else (source_row[17] or []),
                "counters": {}, "counter_guids": {}, "int_attrs": {},
            }
            try:
                source_buffs = json.loads(source_row[18] or "{}")
                source_card["parent_uid"] = int(
                    source_buffs.get("parent_uid", 0) or 0)
                if isinstance(source_buffs.get("subtype"), str):
                    source_card["subtype"] = source_buffs["subtype"]
                if isinstance(source_buffs.get("thresholds"), list):
                    source_card["shards"] = [int(value) for value in
                                              source_buffs["thresholds"]]
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
    if source_card is None:
        # Champion abilities use a SessionCardId that is not necessarily
        # materialized in game_cards.  Keep ownership available to typed
        # filters even when the source's printed card row is absent.
        source_card = {"card_uid": int(source_uid or 0),
                       "user_id": controller_uid,
                       "src_owner_id": controller_uid,
                       "src_owner_side": _side_of(controller_uid),
                       "card_type": "Champion", "subtype": "",
                       "shards": [], "card_abilities": []}
    top_n = _find_filter_type(fjson, "TopNOfDeck")
    blocking_filter = _find_filter_type(fjson, "BlockingFilter")
    blocking_targets = _blocking_targets(battle_state, source_uid) \
        if blocking_filter is not None else None

    # collection_flags is a visibility mask in the client data, not always
    # the actual target zone. TopNOfDeck is explicitly evaluated against a
    # controller's ordered deck and its nested filter decides which cards in
    # that deck qualify (Brightmoon Brave is the common example).
    if top_n is not None:
        zones = ["deck"]
    else:
        zones = [ZONE_MAP.get(z, z.lower())
                 for z in (tpl["collection_flags"] or "").split("|") if z]
    # Client semantics: None/empty collection flags means any collection,
    # rather than an empty legal-target set.  Keep the hidden/placeholder
    # collections out of this broad fallback.
    if not zones:
        zones = list(dict.fromkeys(ALL_TARGET_ZONES))
    player_filter = (tpl.get("player_filter") or "").lower()
    opposing = player_filter in {
        "opponent", "opposing", "singleopponent", "multipleopponents",
    }
    self_only = player_filter in {"self", "you", "controller"}
    wants_champions = any(z in ("champions", "warzone") for z in zones) or \
        "IsHero" in (tpl["filter_json"] or "")
    from pvp_db import (db_target_candidate_rows, db_ability_effect_rows,
                        db_gem_template_name)
    candidate_rows = db_target_candidate_rows(
        session_id, zones, controller_uid=controller_uid,
        both_players=both_players, top_n=top_n is not None, conn=db)
    out = []
    candidate_cards = []
    top_n_by_owner = {}
    for cu, ctype, loc, uid, template_guid, state, atk, def_, name, cost, subtype, thresh, card_abs, raw_buffs, rarity, socket_count, gems, original_template_guid \
            in (tuple(row)[:18] for row in candidate_rows):
        int_attrs = {}
        counters = {}
        counter_guids = {}
        dynamic_shards = None
        try:
            saved = json.loads(raw_buffs or "{}")
            persisted = saved.get("int_attrs", {}) if isinstance(saved, dict) else {}
            if isinstance(persisted, dict):
                int_attrs.update({str(k): int(v or 0) for k, v in persisted.items()})
            if isinstance(saved, dict):
                if isinstance(saved.get("counters"), dict):
                    counters = dict(saved["counters"])
                if isinstance(saved.get("counter_guids"), dict):
                    counter_guids = dict(saved["counter_guids"])
                if isinstance(saved.get("subtype"), str):
                    subtype = saved["subtype"]
                if isinstance(saved.get("thresholds"), list):
                    dynamic_shards = [int(value) for value in
                                      saved["thresholds"]]
            parent_uid = int(saved.get("parent_uid", 0) or 0) \
                if isinstance(saved, dict) else 0
        except (TypeError, ValueError, json.JSONDecodeError):
            parent_uid = 0
        try:
            for ability_guid in json.loads(card_abs or "[]"):
                for _eg, _et, _ep in db_ability_effect_rows(
                        ability_guid, conn=db):
                    if _et != "CardModifierAbilityEffectTemplate":
                        continue
                    _pd = json.loads(_ep or "{}")
                    if str(_pd.get("property", "")).lower() == "intattr":
                        _attr = str(_pd.get("attribute") or "")
                        if not _attr:
                            low_text = str(_pd.get("text") or "").lower()
                            if "untamed" in low_text:
                                _attr = "Untamed"
                            elif "tamed" in low_text:
                                _attr = "Tamed"
                        if _attr:
                            int_attrs[_attr] = 1
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        if int_attrs.get("Tamed", 0) > 0:
            int_attrs.pop("Untamed", None)
        if blocking_targets is not None and int(cu) not in blocking_targets:
            continue
        card = {"card_uid": int(cu), "card_type": ctype, "location": loc,
                "user_id": uid, "state": int(state or 0),
                "attack": atk, "defense": def_, "name": name or "",
                "cost": cost or 0, "src_owner_side": _side_of(controller_uid),
                "src_owner_id": controller_uid,
                "subtype": subtype or "",
                "int_attrs": int_attrs,
                "faction": template_faction(template_guid),
                "rarity": rarity or "",
                "socket_count": int(socket_count or 0),
                "gems": int(gems or 0),
                "gem_count": 1 if int(gems or 0) else 0,
                "original_template_guid": original_template_guid or "",
                "parent_uid": parent_uid,
                "card_abilities": json.loads(card_abs or "[]")
                if isinstance(card_abs, str) else (card_abs or []),
                "counters": counters,
                "counter_guids": counter_guids,
                "shards": (dynamic_shards if dynamic_shards is not None else
                            shards_from_threshold(thresh))}
        if int(gems or 0):
            try:
                gem_name = db_gem_template_name(int(gems), conn=db)
                card["gem_is_minor"] = bool(
                    gem_name and "minor" in str(gem_name).lower())
            except Exception:
                card["gem_is_minor"] = False
        if battle_state and battle_state.get("turn_player"):
            active = battle_state.get("turn_player")
            card["active_player_id"] = (
                controller_uid if active == _side_of(controller_uid) else
                (0 if _side_of(controller_uid) == "player" else
                 controller_uid))
        if battle_state:
            marker_turn = int(battle_state.get("damaged_opponent_turn", 0) or 0)
            current_turn = int(battle_state.get("turn_number", 0) or 0)
            if marker_turn == current_turn:
                card["damaged_opponent_this_turn"] = list(
                    battle_state.get("damaged_opponent_this_turn") or [])
        if self_only and int(uid or 0) != int(controller_uid or 0):
            continue
        if opposing and int(uid or 0) == int(controller_uid or 0):
            continue
        if top_n is not None:
            # TopNOfDeck wraps the actual card filter in m_Filter. Keep the
            # complete ordered deck here: Amount counts matching cards, while
            # TopHalfOfDeck limits the inspected portion of the deck before
            # applying the nested filter.
            top_n_by_owner.setdefault(int(uid or 0), []).append(card)
        else:
            candidate_cards.append(card)

    # Comparison filters inspect a collection that may be broader than the
    # target template's own collection flags.  The current candidate pool is
    # still the correct fallback for small fixtures and ordinary targets; the
    # resolver supplies the complete live collection when it is available.
    def _blocked_by_targeting_immunity(card):
        if card.get("card_type") == "Champion":
            return False
        try:
            from .statics import rule_modifiers
            rules = rule_modifiers(
                db, session_id, battle_state or {}, int(card["card_uid"]))
        except (AttributeError, TypeError, ValueError):
            rules = []
        for rule in rules:
            if rule.get("property") != "targetingimmunity":
                continue
            filter_json = rule.get("filter") or rule.get("cardfilter")
            if filter_json and source_card and evaluate_card_filter(
                    source_card, filter_json, source_uid,
                    source_card=card, card_pool=candidate_cards,
                    champion_pool=champions or [],
                    ability_state=battle_state, db=db):
                return True
        return False

    for card in candidate_cards:
        if (_blocked_by_targeting_immunity(card) or
                not evaluate_card_filter(card, fjson, source_uid,
                                source_card=source_card,
                                card_pool=candidate_cards,
                                champion_pool=champions or [],
                                ability_state=battle_state, db=db)):
            continue
        out.append(int(card["card_uid"]))

    if top_n is not None:
        nested = top_n.get("m_Filter") or {}
        selected = []
        for owner_cards in top_n_by_owner.values():
            if top_n.get("m_CountFromBottom"):
                owner_cards = list(reversed(owner_cards))
            amount = int(top_n.get("m_Amount", 1) or 1)
            owner = owner_cards[0].get("user_id") if owner_cards else controller_uid
            if top_n.get("m_AddSapphire"):
                thresholds = (battle_state or {}).get(
                    "player_threshold" if _side_of(owner) == "player"
                    else "ai_threshold", {})
                amount += int(thresholds.get(16, thresholds.get("16", 0)) or 0)
            if top_n.get("m_AddX"):
                amount += int((battle_state or {}).get(
                    "x_cost_paid", (source_card or {}).get(
                        "card_x_cost_paid", 0)) or 0)
            if top_n.get("m_AddRemovedCounters"):
                amount += int((battle_state or {}).get(
                    "counter_cost_paid", 0) or 0) * int(
                        top_n.get("m_AddRemovedCountersMultiplier", 1) or 1)
            if top_n.get("m_AddSourceCardsAttack"):
                amount += int((source_card or {}).get("attack", 0) or 0)
            if top_n.get("m_AddSourceCardsDefense"):
                amount += int((source_card or {}).get("defense", 0) or 0)
            if top_n.get("m_AddSourceCardsCost"):
                amount += int((source_card or {}).get("cost", 0) or 0)
            if top_n.get("m_AddDamageDealt"):
                amount += int((battle_state or {}).get("damage_dealt", 0) or 0)
            if top_n.get("m_AddDamageThatWouldBeDealt"):
                amount += int((battle_state or {}).get(
                    "damage_that_would_be_dealt", 0) or 0)
            variable = top_n.get("m_AddCardIntegerVariable") or ""
            if variable:
                amount += int((source_card or {}).get("int_attrs", {}).get(
                    variable, 0) or 0)
            amount = max(0, amount)
            if top_n.get("m_TopHalfOfDeck"):
                inspect = owner_cards[:(len(owner_cards) + 1) // 2]
                selected.extend(
                    int(card["card_uid"])
                    for card in inspect
                    if amount > 0 and
                    evaluate_card_filter(card, nested, source_uid,
                                         source_card=source_card,
                                         card_pool=owner_cards,
                                         ability_state=battle_state, db=db))
            else:
                owner_count = 0
                for card in owner_cards:
                    if not evaluate_card_filter(card, nested, source_uid,
                                                source_card=source_card,
                                                card_pool=owner_cards,
                                                ability_state=battle_state,
                                                db=db):
                        continue
                    selected.append(int(card["card_uid"]))
                    owner_count += 1
                    if owner_count >= amount:
                        break
        return selected
    if wants_champions and champions:
        for c_uid, c_owner, c_name, c_hp in champions:
            if not both_players and c_owner != controller_uid:
                continue
            if blocking_targets is not None and int(c_uid) not in blocking_targets:
                continue
            champ_card = {"card_uid": int(c_uid), "card_type": "Champion",
                          "location": "warzone", "user_id": c_owner,
                          "state": 0, "attack": 0, "defense": c_hp,
                          "name": c_name or "Champion", "cost": 0,
                          "subtype": "", "shards": [],
                          "faction": "",
                          "src_owner_side": _side_of(controller_uid),
                          "src_owner_id": controller_uid,
                          "gem_count": 0, "card_abilities": []}
            if battle_state:
                marker_turn = int(
                    battle_state.get("damaged_opponent_turn", 0) or 0)
                current_turn = int(battle_state.get("turn_number", 0) or 0)
                if marker_turn == current_turn:
                    champ_card["damaged_opponent_this_turn"] = list(
                        battle_state.get("damaged_opponent_this_turn") or [])
            if self_only and int(c_owner or 0) != int(controller_uid or 0):
                continue
            if opposing and int(c_owner or 0) == int(controller_uid or 0):
                continue
            if evaluate_card_filter(champ_card, fjson, source_uid,
                                    source_card=source_card,
                                    card_pool=candidate_cards,
                                    champion_pool=champions or [],
                                    ability_state=battle_state, db=db):
                out.append(int(c_uid))
    return out


def legal_targets_for(db, session_id, controller_uid, target, source_uid,
                      *, both_players=None, champions=None,
                      battle_state=None):
    """Evaluate one ``AbilityBuilder`` target through the shared predicate.

    Callers in PvE and PvP used to repeat the same conversion from a typed
    target object to ``legal_targets`` arguments.  This helper centralizes
    that boundary while keeping the actual filter evaluator above as the
    single source of target legality.
    """
    template_id = getattr(target, "guid", target)
    if both_players is None:
        both_players = target_uses_both_players(db, template_id)
    return legal_targets(
        db, session_id, controller_uid, template_id, source_uid,
        both_players=bool(both_players), champions=champions,
        battle_state=battle_state)


def validate_target_selection(db, session_id, controller_uid, template_id,
                              source_uid, selected, both_players=False,
                              champions=None, battle_state=None):
    """Validate activation target IDs using the same metadata as auto-targets.

    The client rejects incomplete/illegal TargetInstances before applying an
    effect.  Server transaction data is untrusted, so explicit target maps
    must pass this check too.  The caller decides whether an empty result
    should open a prompt or make the effect a no-op.
    """
    tpl = target_template(db, template_id)
    values = selected if isinstance(selected, (list, tuple)) else [selected]
    values = [int(v) for v in values if v is not None]
    if tpl is None:
        return values
    if len(values) > int(tpl.get("max_target_count") or 1):
        return []
    if not values and tpl.get("optional"):
        return []
    legal = set(legal_targets(
        db, session_id, controller_uid, template_id, source_uid,
        both_players=both_players, champions=champions or [],
        battle_state=battle_state))
    if not values or any(v not in legal for v in values):
        return []
    minimum = int(tpl.get("min_target_count") or 0)
    if len(values) < minimum:
        return []
    return values
