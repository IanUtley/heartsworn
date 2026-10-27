# IR interpreter: remaining gaps

The Records-driven IR interpreter (`rules_port/ir_exec.py` and its `ir_exec_*`, `ir_modifiers.py`,
`ir_triggers.py`, `ir_variables.py`, `granted_abilities.py` modules) runs an ability only when `coverage()`
accepts every construct in it. An ability with any refused construct stays on the native resolver path. The
interpreter never half-runs an ability.

## Current state

Measured with `AssetExtraction/ir_gap_report.py` over all 8,996 `AbilityTemplate` records:

- Covered by the interpreter: 6,914
- Refused: 2,081
- Coverage check raises on bad Records data: 1 (see Data below)

Regenerate the numbers and the full reason list from the current code and Records:

```bash
python3 AssetExtraction/ir_gap_report.py --top 80 --json /tmp/ir_gaps.json
```

The counts below are one ability's refusal reasons each. An ability with several reasons is counted under each.

Closed so far: chosen `SinglePlayer` targets, random targets with a card filter, `PlayedResources` moves,
turn-boundary durations on attribute and int-attribute modifiers, and the `IntAttrAbilityVariable` and
`CountListAttrAbilityVariable` variables.

## Triggers

The interpreter runs only the trigger events in `TRIGGER_EVENTS` (`rules_port/ir_triggers.py`), plus the turn and
entered-zone events in `ir_exec.py`. Other trigger types are refused as `trigger <Event>`.

- `GameStartedEvent` (221) and `PreGameEvent` (68): pregame setup. Discovery and skip rules live in
  `rules_port/pregame.py`, `rules_port/triggers.py` and `rules_port/trigger_discovery.py`. The timing is tied to
  the opening priority window.
- `trigger selector TriggerTarget` (135): the trigger's target is chosen by the trigger, not the event.
- `CardReadiedEvent` (76), `CardExitedZoneEvent` (37), `GainThresholdEvent` (37), `GainChargeEvent` (31),
  `CardBlockedEvent` (28), `ChampionHealedEvent` (25), and about 40 more event types with fewer than 20 abilities each.
- Trigger conditions: `TACTriggerCondition` (38), `TriggerAbilityIsChargePower` (14), `trigger condition IntAttrFilter` (12).

## Targets

- `chosen target of more than one card` (146, plus 39 invoked) and multi-card targets with a count other than one
  (about 400 across the min and max counts). The interpreter picks one card per target index.
- `fight or block without a second card` (78): `rules_port/ir_exec_combat.py`.
- `chosen follow-up target` (14): `follow_up_gaps` in `rules_port/ir_exec_targets.py`.
- `reveal or revert with store targets` (43): the two TODOs at the `StoreTargets` pairing check in
  `rules_port/ir_exec.py`. The engine's reveal pick and the native revert do not match yet.
- `filter TACFilter` (8), `filter PlayersWhoControlMatchingFilter` (5), `filter EffectInputVariable` (5).
- `random target without a card filter` (2): the live pool drops every card for these, while C# takes all of them.
  The interpreter follows C#, so these stay native until the live behaviour is confirmed.

## Modifiers, costs and grants

- `AttributeModifier` (169) and `IntAttrModifier` (154) with `WhileCardInPlay`: continuous. The runtime already
  skips static-owned clauses, but accepting these in coverage needs the static path to project every attribute and
  int-attribute kind. It projects the champion intattr flags, the champion intattr sums and card scans. Accepting
  the clauses without that check could silently drop keyword grants.
- `DamageMultiplierModifier` (14), `BlockImmunityModifier` (13), `BlockImmunityExceptionModifier` (8),
  `DamageImmunityModifier` (3), `TargetingImmunityModifier` (2), all with `WhileCardInPlay`: continuous rule
  modifiers that the static path owns.
- `stat duration BeginningOfOwnersTurn` (23), `stat duration UntilEndOfDungeon` (2): stat modifiers accept only
  `Permanent` and `EndOfTurn` (`STAT_DURATIONS` in `ir_exec.py`).
- `cost duration WhileCardInPlay` (44) and `cost duration EndOfTurn` (19).
- `stat derived from another card` (14), `stat replaces the existing value` (2), `stat property CurrentAttackValue`
  (2) and `CurrentDefenseValue` (2).
- `modifier SetHeroHealthModifier` (15), `LoseLifeModifier without a typed amount` (2).
- `grant of powers from sockets, payments, remembered or random sources` (65): `rules_port/granted_abilities.py`.
- `grant duration BeginningOfOpponentsTurn` (2), `IntAttrModifier duration WhileCardOnTopOfDeck` (4).

## Variables

- `SumVariableInListAttrCardsAbilityVariable` (68, plus 6 invoked). The native evaluator computes it
  (`_sum_variable` and `_list_sum_variable` in `rules_port/static_rules.py`), so binding it the way
  `IntAttrAbilityVariable` is bound (`_native_variable_value` in `ir_exec.py`) is the next step.
- `variable CardIntegerVariable` (19), `card count of different races` (18), `CardSumAbilityVariable` (5),
  `SourcePlayerHealthVariable` (5), `SourcePlayerShardAbilityVariable` (2).
- `variable TriggerEventDamageProperty` (11), `TriggerSourcePropertyVariable` (6), `TriggerTargetPropertyVariable` (3):
  trigger-scope fields the interpreter does not carry yet.
- `variable target count` (16, with 12 for the min count and 10 invoked): target counts that vary with a variable.
- `random variable with a second value or a dynamic bound` (4, plus 3 invoked).

## Effects

- `effect RepeatingAbilityEffectTemplate` (23) and `effect ConversationAbilityEffectTemplate` (23).
- `TransformCardAbilityEffectTemplate of a player` (41, plus 9 and 9 invoked), `with a serialized TAC` (16),
  `transform to no template` (12): `rules_port/ir_exec_transform.py`.
- `resource set (not add)` (13) and `effect SetCardIntegerVariableEffectTemplate` (13): operations the interpreter
  does not run.
- `effect CreateTokenMatchingTargetAbilityEffectTemplate` (11), `effect CreateAndCastSpellAbilityEffectTemplate` (9),
  `DestroyCard for a player target` (5), `effect StoreListAttrAbilityEffectTemplate` (5),
  `effect LoadPlayerDeckAbilityEffectTemplate` (4), `move to none` (4).
- `cost or optional (m_VariableActivationCost)` (12) and `cost change in an ability that invokes another` (9).
- `effect condition NotEffectCondition` (7), `effect condition AbilityVariableCondition` (2),
  `condition SerializableTAC` (41), `condition TurnPhaseCondition` (12),
  `condition TriggerCardIsNthCardDrawnThisTurnByThisPlayer` (2): condition kinds the shared evaluators do not cover.

## Data

- One Records ability (`9b36aabc-dffc-edf3-eaf9-cd514adf3d6a`) has a clause that points at a null template GUID.
  Its coverage check raises `KeyError`, so the report counts it as a coverage error, not a refusal. It stays on the
  native path. Fixing it needs the Records data corrected.

## Remaining work outside the interpreter

These gaps are not specific to the interpreter. They are tracked in `docs/RULES_PORT_PARITY.md` under "Remaining
work" and in `docs/PRIVATE_SERVER_FEATURES.md`: continuous `CardModifier` leaves that are not IntAttrs, full
replacement pause and resume, `UntilDamaged` removal, state-based death timing, and client acceptance for trigger
and zone events.

## How to close an entry

1. Find the refusal in the report (`AssetExtraction/ir_gap_report.py`) and the code that raises it.
2. Call the live leaf or evaluator the native path uses, with its inputs passed explicitly, as the existing
   `ir_exec_*` runners do. Prefer calling the native function over re-implementing it, so both paths agree by
   construction.
3. Update `coverage()` so the construct is accepted, then regenerate the report.
4. Run the IR interpreter test modules (`tests/tests_ir_*.py`, `tests/tests_ability_ir.py`, `tests/tests_conditions.py`,
   `tests/tests_targeting.py`) and add one focused test for the area. A refusal removed without a passing test is not
   closed.
