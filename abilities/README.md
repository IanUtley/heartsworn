# abilities/

Ability helpers that the RulesPort runtime and the protocol host still call.
Ability **resolution** is not here: `rules_port/resolution.py` owns the
ability lifecycle and `rules_port/effects.py` dispatches each Records effect
type to a named `EffectContext` operation (`rules_port/context.py`). There is
no second resolver, leaf registry, or custom-handler registry.

## How abilities work

1. Battle transactions enter through `rules_port.wire` as typed activation
   data. The port validates phase, priority, source collection, cost,
   thresholds and metadata-defined targets before anything resolves.
2. An accepted activation becomes one `AbilityInstance` on the RulesPort
   action stack. `rules_port.resolution` walks its effect groups in authored
   order, resolves targets, evaluates effect conditions and contingencies, and
   calls `rules_port.effects.dispatch` for each concrete effect.
3. If a UI decision is needed, the continuation (instance, source, targets,
   variables, resume order) is persisted and the matching typed response
   resumes the same instance.
4. The host projection commits the SQLite mutation, emits the Unity events,
   and rebuilds options/priority.

The projection never chooses cards or infers rules from `card_text`.

Resource modifiers keep the client distinction between `currentresource`
(a temporary/current pool grant) and `totalresource` (a maximum-pool
increase). Hideous Conversion's authored `[L1][R0]` therefore grants one
temporary current resource.

## Adding behaviour for a new effect type

Add a named operation to `EffectContext` (or a focused `rules_port/*_effects.py`
module it calls) and register the effect type in the dispatch table in
`rules_port/effects.py`, then list it in `rules_port/coverage.py`
(`NATIVE_EFFECTS`). Read typed `m_*` fields through `EffectContext.value` /
`template_value`; never parse `game_text`.
