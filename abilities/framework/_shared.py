"""Shared utilities used by abilities framework modules."""

import game_engine
# Single owners live in rules_port; re-exported for the framework callers.
from rules_port.zone_effects import state_after_zone_exit  # noqa: F401
from rules_port.runtime_helpers import next_game_card_uid  # noqa: F401


# These flags describe the current battle/turn instance, not the card's
# identity.  A card leaving play (especially when it is returned to a deck)
# must not carry them into a later draw.  Keeping this mask here gives every
# zone-move implementation the same reset semantics.
ZONE_EXIT_STATE_FLAGS = (
    game_engine.ECardStates.Tapped |
    game_engine.ECardStates.Blocking |
    game_engine.ECardStates.Attacking |
    game_engine.ECardStates.Damaged |
    game_engine.ECardStates.Healed |
    game_engine.ECardStates.Dead |
    game_engine.ECardStates.HasAttacked |
    game_engine.ECardStates.HasBlocked |
    game_engine.ECardStates.EffectExpired |
    game_engine.ECardStates.ZoneChangeReplacement |
    game_engine.ECardStates.Activated |
    game_engine.ECardStates.CameOutThisTurn |
    game_engine.ECardStates.StartedATurnOnYourSide
)


def _log(msg):
    try:
        from db import log_req
        log_req(msg)
    except Exception:
        pass


def _card_state_of(db, session, card_uid):
    try:
        from pvp_db import db_card_state_value
        return db_card_state_value(
            session.session_id, int(card_uid), conn=db)
    except Exception:
        return 0


def card_collection_for_location(location):
    """Map the authoritative DB location to the client's card collection."""
    return {
        "deck": game_engine.ECardCollections.Deck,
        "hand": game_engine.ECardCollections.Hand,
        "discard": game_engine.ECardCollections.Discard,
        "void": game_engine.ECardCollections.Void,
        "warzone": game_engine.ECardCollections.Warzone,
        "CastSpells": game_engine.ECardCollections.CastSpells,
        "PlayedResources": game_engine.ECardCollections.PlayedResources,
        "underground": game_engine.ECardCollections.Underground,
        "choosing": game_engine.ECardCollections.Choosing,
    }.get(location, game_engine.ECardCollections.Warzone)


def owner_uid(owner_id, pl_t, ai_t, bstate=None):
    """Map a card's DB user_id to the UID of its controlling player.

    FRA/AI games: user_id 0 is the AI (ai_t), anything else is the human
    (pl_t).  PvP tournaments have TWO non-zero player ids (pids) — the pid
    maps directly to UID.make(244, pid), which is objective for both clients.
    """
    if bstate is not None and bstate.get("pvp"):
        return game_engine.UID.make(244, int(owner_id or 0))
    return pl_t if (owner_id or 0) != 0 else ai_t


def pvp_champion_uid(bstate, owner_id):
    """Return the authoritative champion SCID for a PvP player id."""
    if not (bstate or {}).get("pvp"):
        return None
    for pid, uid in ((bstate or {}).get("champ_map") or {}).items():
        try:
            if int(pid) == int(owner_id):
                return int(uid)
        except (TypeError, ValueError):
            continue
    return None


def pvp_opponent_pid(bstate, owner_id):
    """Return the other real player id in a PvP ability view."""
    if not (bstate or {}).get("pvp"):
        return None
    for pid in (bstate or {}).get("pids") or []:
        try:
            if int(pid) != int(owner_id):
                return int(pid)
        except (TypeError, ValueError):
            continue
    for pid in ((bstate or {}).get("champ_map") or {}).keys():
        try:
            if int(pid) != int(owner_id):
                return int(pid)
        except (TypeError, ValueError):
            continue
    return None


# ---------------------------------------------------------------------------
#  Card attributes (data-driven from ability text — no per-card GUIDs)
# ---------------------------------------------------------------------------

_ATTRIBUTE_KEYWORDS = {
    "flight": game_engine.ECardAttributes.Flight,
    "spiritdrain": game_engine.ECardAttributes.SpiritDrain,
    "lifedrain": game_engine.ECardAttributes.SpiritDrain,
    "steadfast": game_engine.ECardAttributes.Steadfast,
    "spellshield": game_engine.ECardAttributes.SpellShield,
    "immortal": game_engine.ECardAttributes.Immortal,
    "invincible": game_engine.ECardAttributes.PreventAllDamage,
    "rage": game_engine.ECardAttributes.Rage,
    "first strike": game_engine.ECardAttributes.FirstStrike,
    # Typed AttributeModifier metadata uses the enum spelling without a
    # space (FirstStrike), while localized game text uses "first strike".
    "firststrike": game_engine.ECardAttributes.FirstStrike,
    "swiftstrike": game_engine.ECardAttributes.FirstStrike,
    "quick action": game_engine.ECardAttributes.QuickAction,
    "canny block": game_engine.ECardAttributes.CantBlock,
    "speed": game_engine.ECardAttributes.Speed,
    "skyguard": game_engine.ECardAttributes.SkyGuard,
    "juggernaut": game_engine.ECardAttributes.Juggernaught,
    "defensive": game_engine.ECardAttributes.Defensive,
    "can't attack": game_engine.ECardAttributes.CantAttack,
    "can't block": game_engine.ECardAttributes.CantBlock,
    "must block": game_engine.ECardAttributes.MustBlock,
    "can't be blocked": game_engine.ECardAttributes.CantBeBlocked,
    "cantreadyautomatically": game_engine.ECardAttributes.CantReadyAutomatically,
    "dualstrike": game_engine.ECardAttributes.DualStrike,
}


def attribute_bits_from_text(text):
    """Map attribute keywords found in *text* to ECardAttributes bit flags."""
    low = (text or "").lower()
    bits = 0
    # "can't attack or block" contains both sub-strings; check the compound
    # first so the OR form doesn't get swallowed by a single keyword.
    if "can't attack or block" in low:
        bits |= game_engine.ECardAttributes.CantAttack | game_engine.ECardAttributes.CantBlock
    for keyword, flag in _ATTRIBUTE_KEYWORDS.items():
        if keyword in low:
            bits |= flag
    return bits


def attribute_bits_from_flags(flags):
    """Map typed AttributeModifier flags (for example ``FirstStrike``).

    This is deliberately separate from localized game-text parsing.  The
    extracted ``m_AttributeFlags`` value is authoritative whenever present.
    """
    if isinstance(flags, int):
        return int(flags)
    bits = 0
    for token in str(flags or "").replace("|", " ").split():
        normalized = token.strip().lower().replace("_", "")
        bits |= {
            "firststrike": game_engine.ECardAttributes.FirstStrike,
            "swiftstrike": game_engine.ECardAttributes.FirstStrike,
            "dualstrike": game_engine.ECardAttributes.DualStrike,
            "skyguard": game_engine.ECardAttributes.SkyGuard,
            "cantblock": game_engine.ECardAttributes.CantBlock,
            "cantattack": game_engine.ECardAttributes.CantAttack,
            "cantbeblocked": game_engine.ECardAttributes.CantBeBlocked,
            "cantreadyautomatically": game_engine.ECardAttributes.CantReadyAutomatically,
            "defensive": game_engine.ECardAttributes.Defensive,
            "flight": game_engine.ECardAttributes.Flight,
            "immortal": game_engine.ECardAttributes.Immortal,
            "spellshield": game_engine.ECardAttributes.SpellShield,
            "steadfast": game_engine.ECardAttributes.Steadfast,
            "spiritdrain": game_engine.ECardAttributes.SpiritDrain,
            "lifedrain": game_engine.ECardAttributes.SpiritDrain,
            "speed": game_engine.ECardAttributes.Speed,
            "quickaction": game_engine.ECardAttributes.QuickAction,
            # The client enum is historically spelled ``Juggernaught``
            # (two n's), while some localized/older records use the normal
            # ``Juggernaut`` spelling.  Accept both so typed AttributeModifier
            # metadata and rendered game text produce the same Crush bit.
            "juggernaut": game_engine.ECardAttributes.Juggernaught,
            "juggernaught": game_engine.ECardAttributes.Juggernaught,
        }.get(normalized, 0)
    return bits


_TEMPORARY_ATTRIBUTE_EXPIRATIONS = "__attribute_expirations"


# Compatibility exports.  Counter state is now implemented in the focused
# effects module, while older callers may continue importing from _shared.
from .effects.counters import (  # noqa: E402,F401
    _counters_payload, counter_guid_for_name, card_counters, card_counters_full,
    add_card_counter, push_card_counters, remove_card_counters,
    counter_name_from_text,
)
