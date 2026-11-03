# abilities/framework/

Helpers shared by the RulesPort runtime, the AI and the protocol host. None of
these modules resolve abilities; `rules_port` does (see `../README.md`).

| Module | Owns |
|---|---|
| `_shared.py` | Logging, card-state masks, zone/collection mapping, champion-owner helpers |

Several of these duplicate logic that also exists in `rules_port`
(`rules_port/conditions.py` now covers condition evaluation; `rules_port/targeting.py` now holds target and filter evaluation). Prefer
the `rules_port` version in new code; the copies here are candidates for the
next consolidation pass.
