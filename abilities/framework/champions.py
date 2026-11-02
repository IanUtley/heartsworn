"""Champion ability framework.

Most champion charge / spell powers are data-driven: their effects live in
the ``ability_effects`` table (BOM) and are executed by the leaf executors
in ``bom.py``.  Custom handlers go in ``abilities/cards/``.

This module provides champion-specific utilities that aren't leaf effects:
- Validating ability activation (charges, SP, threshold)
- Deducting costs and pushing update events
- Utility resolvers for common champion ability patterns.
"""

import json
import random

import game_engine
import battle_engine as _be

from ._shared import _log


# ── Activation helpers ──────────────────────────────────────────────────────



# ── Common champion ability patterns ────────────────────────────────────────

def heal_self(game, pl_t, amount, bstate, key="player_health"):
    """Add *amount* health to the player, capped at max (default 25)."""
    max_hp = 25
    current = bstate.get(key, max_hp)
    new_val = min(current + amount, max_hp)
    bstate[key] = new_val
    ev = game_engine.ChampionHealthChangedSessionEventArgs()
    ev.player_id = pl_t
    ev.operation = 1
    ev.delta = amount
    ev.new_value = new_val
    game._push(ev)
    return f"heal {amount} HP -> {new_val}"


def damage_self(game, pl_t, amount, bstate, key="player_health"):
    """Subtract *amount* health from the player (no minimum)."""
    current = bstate.get(key, 20)
    new_val = max(0, current - amount)
    bstate[key] = new_val
    ev = game_engine.ChampionHealthChangedSessionEventArgs()
    ev.player_id = pl_t
    ev.operation = 2
    ev.delta = amount
    ev.new_value = new_val
    game._push(ev)
    return f"self-damage {amount} HP -> {new_val}"


def draw_cards(game, session, handler, pl_t, ai_t, bstate, count=1):
    """Draw *count* cards for the player."""
    for _ in range(count):
        handler._player_draw_card(game, session, pl_t)
    return f"draw {count} card(s)"


