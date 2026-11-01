"""Deterministic sparse feature encoding for Hex decisions.

Feature ids are 63-bit hashes of canonical ASCII keys.  The encoder never
touches the network, the clock, or the global RNG, so a captured state and the
same state at play time produce the same ids.  A model only ever stores the ids
it has seen; unknown ids are dropped at inference, exactly like MageZero's
dense vocabulary.

The feature set is intentionally game-shaped rather than card-shaped:

* pool features describe resources, thresholds, charge power, board pressure
  and hand shape so a deck-local model still sees the game situation;
* per-card features name the card template, its type, cost bucket and keyword
  flags, so two decks that share a card share those rows;
* opponent features are limited to public information (warzone, hand count,
  life, thresholds) - hidden cards are never encoded.
"""

from __future__ import annotations

import hashlib
import json

# 63 bits keeps ids inside a signed int64 and inside JSON's exact integer range.
_ID_MASK = (1 << 63) - 1
_NAMESPACE = b"hexai.v1|"
_HASH_CACHE: dict[str, int] = {}

# ECardAttributes bits that matter most for a board-state read.  Names match the
# client comment for the flag; unknown flags are simply not encoded.
_ATTRIBUTE_NAMES = {
    1: "spiritsdrain",
    2: "flight",
    4: "speed",
    8: "skyguard",
    16: "juggernaut",
    32: "steadfast",
    64: "immortal",
    128: "spellshield",
    256: "unique",
    512: "cantattack",
    1024: "cantblock",
    2048: "defensive",
    4096: "forceattack",
    8192: "cantready",
    16384: "firststrike",
    32768: "rage",
    65536: "mustblock",
    131072: "cantbeblocked",
    262144: "preventcombat",
    524288: "preventnoncombat",
    1048576: "dualstrike",
    16777216: "inspire",
    33554432: "escalation",
    268435456: "quickaction",
    1073741824: "mustbeblocked",
}

_COLOR_NAMES = {
    1: "colorless", 4: "blood", 8: "ruby", 16: "sapphire", 32: "wild",
    64: "diamond", 125: "any", 124: "anycolor",
}

_TAPPED = 1
_ATTACKING = 4


def feature_id(key: str) -> int:
    """Return the stable 63-bit id for one feature key."""
    cached = _HASH_CACHE.get(key)
    if cached is not None:
        return cached
    digest = hashlib.blake2b(
        _NAMESPACE + key.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "little") & _ID_MASK
    if len(_HASH_CACHE) < 200_000:
        _HASH_CACHE[key] = value
    return value


def feature_ids(keys) -> list[int]:
    """Hash a key iterable, preserving order and dropping duplicates."""
    seen = set()
    out = []
    for key in keys:
        value = feature_id(str(key))
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def bucket(value, edges) -> int:
    """Index of the first edge greater than ``value`` (clamped to len(edges))."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = 0
    for index, edge in enumerate(edges):
        if value < edge:
            return index
    return len(edges)


_LIFE_EDGES = (1, 3, 5, 8, 11, 14, 17, 21, 26, 31, 41)
_COUNT_EDGES = (1, 2, 3, 4, 5, 6, 7, 8, 10, 13, 17, 22)
_BIG_EDGES = (1, 2, 3, 5, 8, 12, 18, 26, 40, 60, 100)
_COST_EDGES = (1, 2, 3, 4, 5, 6, 7, 8, 10, 13)


def _card_type(card) -> str:
    raw = str(getattr(card, "card_type", "") or "")
    low = raw.lower()
    if "quickaction" in low:
        return "quickaction"
    if "basicaction" in low:
        return "action"
    for name in ("troop", "resource", "artifact", "constant"):
        if name in low:
            return name
    return "other"


def _keywords(card) -> list[str]:
    attributes = int(getattr(card, "attributes", 0) or 0)
    if attributes == 0:
        return []
    return [name for bit, name in _ATTRIBUTE_NAMES.items()
            if attributes & bit]


def _threshold_keys(prefix: str, thresholds) -> list[str]:
    keys = []
    if not isinstance(thresholds, dict):
        return keys
    for color, count in thresholds.items():
        try:
            color = int(color)
            count = int(count or 0)
        except (TypeError, ValueError):
            continue
        if count <= 0:
            continue
        name = _COLOR_NAMES.get(color, f"c{color}")
        keys.append(f"{prefix}:{name}:{min(count, 6)}")
    return keys


def _champion_key(session, pid) -> str | None:
    """Champion template for a seat, or None when the row is not available."""
    try:
        from pvp_db import db_game_champion
        row = db_game_champion(session.session_id, int(pid))
    except Exception:
        return None
    if not row:
        return None
    return str(row[1] or "").lower() or None


def encode_state(evaluator, state, seat_pid, opponent_pid, *,
                 session=None) -> list[int]:
    """Encode one decision state from ``seat_pid``'s point of view.

    ``evaluator`` is the ``ai_eval.CardEvaluator`` already built for this
    decision (``ai_*`` is the seat, ``player_*`` is the opponent).  ``state`` is
    the PvP checkpoint ``pvp_autoplay`` passes around.
    """
    state = state or {}
    session = session or getattr(evaluator, "session", None)
    keys: list[str] = []

    # -- global / public game shape ---------------------------------------
    turn = int(state.get("turn_number", 0) or 0)
    keys.append(f"turn:{bucket(turn, (1, 2, 3, 4, 5, 6, 8, 10, 13, 16, 21))}")
    phase = state.get("phase")
    if phase is not None:
        try:
            phase = int(phase)
        except (TypeError, ValueError):
            pass
        keys.append(f"phase:{phase}")
    keys.append("active" if int(state.get("turn_pid", -1) or -1) == int(
        seat_pid) else "inactive")
    keys.append(f"esc:{bucket(state.get(f'esc_{seat_pid}', 0), _COST_EDGES)}")

    own_life = int(getattr(evaluator, "ai_health", 20) or 0)
    opp_life = int(getattr(evaluator, "player_health", 20) or 0)
    keys.append(f"life:{bucket(own_life, _LIFE_EDGES)}")
    keys.append(f"olife:{bucket(opp_life, _LIFE_EDGES)}")
    keys.append(f"lifediff:{bucket(own_life - opp_life,
                                   (-16, -10, -6, -3, -1, 1, 3, 6, 10, 16))}")

    hand = list(getattr(evaluator, "hand", ()) or ())
    own_warzone = list(getattr(evaluator, "ai_warzone", ()) or ())
    opp_warzone = list(getattr(evaluator, "player_warzone", ()) or ())
    keys.append(f"hand:{bucket(len(hand), _COUNT_EDGES)}")
    keys.append(f"ohand:{bucket(getattr(evaluator, 'player_hand_count', 0),
                                _COUNT_EDGES)}")
    keys.append(f"wz:{bucket(len(own_warzone), _COUNT_EDGES)}")
    keys.append(f"owz:{bucket(len(opp_warzone), _COUNT_EDGES)}")

    resources = int(getattr(evaluator, "resources", 0) or 0)
    total_resources = int(getattr(evaluator, "total_resources", 0) or 0)
    keys.append(f"res:{bucket(resources, _BIG_EDGES)}")
    keys.append(f"res_total:{bucket(total_resources, _BIG_EDGES)}")
    keys.append(f"ores_total:{bucket(state.get(f'res_total_{opponent_pid}', 0),
                                      _BIG_EDGES)}")
    keys.append(f"chg:{bucket(state.get(f'chg_{seat_pid}', 0), _COUNT_EDGES)}")
    keys.append(f"sp:{bucket(state.get(f'sp_{seat_pid}', 0), _COUNT_EDGES)}")

    # Thresholds are public (shards on the board); opponent values are present
    # in the PvP checkpoint as ``thresh_<pid>``.
    keys.extend(_threshold_keys(
        "th", state.get(f"thresh_{seat_pid}") or
        getattr(evaluator, "threshold", {}) or {}))
    keys.extend(_threshold_keys(
        "oth", state.get(f"thresh_{opponent_pid}") or {}))

    ready_own = sum(1 for card in own_warzone
                    if not (int(getattr(card, "card_state", 0) or 0) & _TAPPED))
    ready_opp = sum(1 for card in opp_warzone
                    if not (int(getattr(card, "card_state", 0) or 0) & _TAPPED))
    keys.append(f"ready:{bucket(ready_own, _COUNT_EDGES)}")
    keys.append(f"oready:{bucket(ready_opp, _COUNT_EDGES)}")

    own_troops = [card for card in own_warzone
                  if getattr(card, "is_troop", lambda: False)()]
    opp_troops = [card for card in opp_warzone
                  if getattr(card, "is_troop", lambda: False)()]
    keys.append(f"atroops:{bucket(len(own_troops), _COUNT_EDGES)}")
    keys.append(f"otroops:{bucket(len(opp_troops), _COUNT_EDGES)}")
    keys.append(f"aatk:{bucket(sum(int(card.effective_attack(in_play=True) or 0)
                                   for card in own_troops), _BIG_EDGES)}")
    keys.append(f"adef:{bucket(sum(int(card.effective_defense(in_play=True) or 0)
                                   for card in own_troops), _BIG_EDGES)}")
    keys.append(f"oatk:{bucket(sum(int(card.effective_attack(in_play=True) or 0)
                                   for card in opp_troops), _BIG_EDGES)}")
    keys.append(f"odef:{bucket(sum(int(card.effective_defense(in_play=True) or 0)
                                   for card in opp_troops), _BIG_EDGES)}")

    # -- own hand, card by card -------------------------------------------
    hand_types = {"troop": 0, "action": 0, "quickaction": 0, "constant": 0,
                  "artifact": 0, "resource": 0, "other": 0}
    for card in hand:
        tpl = str(getattr(card, "template_guid", "") or "").lower()
        if not tpl:
            continue
        ctype = _card_type(card)
        hand_types[ctype] = hand_types.get(ctype, 0) + 1
        keys.append(f"h:{tpl}")
        keys.append(f"htype:{ctype}")
        keys.append(f"hcost:{bucket(getattr(card, 'cost', 0), _COST_EDGES)}")
        for keyword in _keywords(card):
            keys.append(f"hkw:{keyword}")
        if getattr(card, "has_variable_cost", False):
            keys.append("hvar")
        if getattr(card, "is_quick_action", lambda: False)():
            keys.append("hquick")
    for ctype, count in hand_types.items():
        if count:
            keys.append(f"hcount:{ctype}:{bucket(count, _COUNT_EDGES)}")

    # -- public warzones ---------------------------------------------------
    for prefix, warzone in (("w", own_warzone), ("o", opp_warzone)):
        for card in warzone:
            tpl = str(getattr(card, "template_guid", "") or "").lower()
            if not tpl:
                continue
            attack = (int(card.effective_attack(in_play=True) or 0)
                      if callable(getattr(card, "effective_attack", None))
                      else 0)
            defense = (int(card.effective_defense(in_play=True) or 0)
                       if callable(getattr(card, "effective_defense", None))
                       else 0)
            keys.append(f"{prefix}:{tpl}")
            keys.append(f"{prefix}type:{_card_type(card)}")
            keys.append(f"{prefix}atk:{bucket(attack, _COST_EDGES)}")
            keys.append(f"{prefix}def:{bucket(defense, _COST_EDGES)}")
            state_bits = int(getattr(card, "card_state", 0) or 0)
            if not state_bits & _TAPPED:
                keys.append(f"{prefix}ready:{tpl}")
            if state_bits & _ATTACKING:
                keys.append(f"{prefix}attacking:{tpl}")
            for keyword in _keywords(card):
                keys.append(f"{prefix}kw:{keyword}")
            damage = int(getattr(card, "card_damage", 0) or 0)
            if damage:
                keys.append(f"{prefix}dmg:{bucket(damage, _COUNT_EDGES)}")

    # -- champion identity (charge power lives here) -----------------------
    own_champ = _champion_key(session, seat_pid) if session is not None else None
    opp_champ = _champion_key(session, opponent_pid) if session is not None else None
    if own_champ:
        keys.append(f"champ:{own_champ}")
    if opp_champ:
        keys.append(f"ochamp:{opp_champ}")

    return feature_ids(keys)


def encode_action(kind: str, card=None, *, targets=(), target_templates=(),
                  extra_keys=()) -> list[int]:
    """Encode an action as a bag of keys shared with the state embedding table.

    ``kind`` is one of ``card``/``resource``/``pass``/``attack``/``block``/
    ``power``/``ability``/``choice``.  ``target_templates`` are template GUIDs
    for chosen targets (usually one or two), not raw uids, so the action row is
    reusable across games.
    """
    keys = [f"act:{kind}"]
    if card is not None:
        tpl = str(getattr(card, "template_guid", "") or "").lower()
        if tpl:
            keys.append(f"act_card:{tpl}")
        keys.append(f"act_type:{_card_type(card)}")
        keys.append(f"act_cost:{bucket(getattr(card, 'cost', 0), _COST_EDGES)}")
        for keyword in _keywords(card):
            keys.append(f"act_kw:{keyword}")
        if getattr(card, "has_variable_cost", False):
            keys.append("act_var")
    for index, tpl in enumerate(target_templates or ()):
        if index >= 2:
            break
        tpl = str(tpl or "").lower()
        if tpl:
            keys.append(f"act_target:{tpl}")
    for key in extra_keys or ():
        keys.append(f"act:{key}")
    return feature_ids(keys)


def describe(state) -> str:
    """Short JSON summary used by capture metadata (never for model input)."""
    try:
        return json.dumps({
            "turn": int(state.get("turn_number", 0) or 0),
            "phase": int(state.get("phase", 0) or 0),
            "turn_pid": state.get("turn_pid"),
        }, separators=(",", ":"))
    except (TypeError, ValueError):
        return "{}"
