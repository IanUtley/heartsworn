# abilities/framework/

Helpers shared by the RulesPort runtime, the AI and the protocol host. None of
these modules resolve abilities; `rules_port` does (see `../README.md`).

| Module | Owns |
|---|---|
| `_shared.py` | Logging, card-state masks, zone/collection mapping, champion-owner helpers |
| `deathcry.py` | Deathcry filtering; resolution is delegated to `rules_port.resolution` |
| `resources.py` | Resource-ability entry points over RulesPort |
| `stat_mod.py` | Persist ATK/DEF modifiers and push `CardUpdated` |
| `statics.py` | Continuous (WhileCardInPlay / Permanent) static evaluation |
| `tac.py` | TAC v2 decoder (`tac_function`, `decode_tac`, ...) |
| `triggers.py` | Historical trigger entry points; every event goes to `rules_port.triggers` |
| `effects/` | Counters, deck search, choice constants, visibility projection, zone-move projection |
| `keywords/combat.py` | Rage adapter over `statics.apply_rage` |

Several of these duplicate logic that also exists in `rules_port`
(`rules_port/conditions.py` now covers condition evaluation; `rules_port/targeting.py` now holds target and filter evaluation). Prefer
the `rules_port` version in new code; the copies here are candidates for the
next consolidation pass.
