# IR interpreter: remaining gaps

This lists what the Records-driven IR interpreter (`rules_port/ir_exec.py` and its `ir_exec_*`,
`ir_modifiers.py`, `ir_triggers.py`, `ir_variables.py`, `granted_abilities.py` modules) does not yet run.
An ability with any construct listed here is refused by `coverage()` before anything runs, so it stays on the
native resolver path. It is never half-run.

The list was built by reading the code. The IR interpreter tests were not run against this list, so each
entry says where the refusal is raised and has not been reproduced by a test.

## Stored, triggered and follow-up targets (gap area B)

- A reveal whose picked card a `StoreTargets` effect stores resolves to a different card on the interpreter
  than on the native path (Create four, Put into your hand). The pair is refused.
  See the TODO at `rules_port/ir_exec.py` (the `StoreTargets` pairing check).
- A revert after `StoreTargets` emits a revert the native path skips (Valorous). The pair is refused.
  Same TODO.
- Follow-up target kinds outside `FOLLOW_UP_KINDS` are refused by `follow_up_gaps` in
  `rules_port/ir_exec_targets.py`.

## Triggers and trigger conditions (gap area C)

- Only the trigger events listed in `TRIGGER_EVENTS` (`rules_port/ir_triggers.py`) run. Any other trigger type
  stays refused.
- Trigger conditions outside those `condition_holds` handles raise `NotImplementedError`
  (`rules_port/ir_exec.py`, trigger-condition evaluation).

## Modifiers and durations (gap area G)

- `ir_modifiers.DURATIONS` is `{"Permanent", "EndOfTurn"}`. Any other clause duration on a keyword, attribute or
  protection modifier is refused. Lasting durations are the remaining work of area H.
- `DamageShieldModifier` runs only with the `Instant` duration (`SHIELD_DURATIONS`).
- `SubTypeModifier` runs only with `Instant` or `Permanent` (`SUBTYPE_DURATIONS`).
- Stat modifiers run only with `Permanent` or `EndOfTurn` (`STAT_DURATIONS` in `ir_exec.py`). An effect whose
  text derives its operand from the troop's own DEF, or from a voided troop's stat (`DERIVED_STAT_TEXT`), is refused.
- Modifier kinds outside `SUPPORTED_MODIFIERS` are refused, as are counter operations outside
  `COUNTER_OPERATIONS` and any `CounterModifier` with `m_RemoveAllCounters`.

## Combat (`rules_port/ir_exec_combat.py`)

- A fight or block with a missing second card, or with more than one card on a side, is refused.
- Combat effects not in the handled set raise `NotImplementedError`.

## Granted abilities (`rules_port/granted_abilities.py`)

- A grant runs only with a duration in `GRANT_DURATIONS` (`Instant`, `Permanent`, `EndOfTurn`,
  `BeginningOfOwnersTurn`, `WhileCardInPlay`).
- A grant without a typed ability is refused.
- A grant of powers from sockets, payments, remembered or random sources (`UNSUPPORTED_POWER_FLAGS`) is refused.

## Targets and filters (`rules_port/ir_exec.py`)

- Player targets run only for `SUPPORTED_PLAYER_TARGETS` (`Self` and `SingleOpponent`). Other player targets
  raise `NotImplementedError`.
- Card filters refused by the interpreter include: a top-of-deck filter counted from the bottom or the half;
  an opponent that is not a single player; the name or subtype of a champion the battle does not name;
  an uncontrolled card that fails the filter. Each is raised as `NotImplementedError` in `ir_exec.py`.
- A card filter or ability condition that the shared evaluators in `ir_exec_conditions.py` do not cover
  raises `NotImplementedError`.

## Variables and thresholds (`rules_port/ir_variables.py`)

- Expression variables are gaps when the formula text falls outside the supported grammar (`expression_gaps`).
- Card-property, ability-property, highest-card and threshold variables each have a `*_gaps` check that refuses
  the variables they do not compute.
- An ability with a threshold requirement is refused (`ir_exec.py`, the ability threshold requirement check).

## Remaining work outside the interpreter

The following gaps are not specific to the interpreter. They are tracked in `docs/RULES_PORT_PARITY.md`
under "Remaining work" and in `docs/PRIVATE_SERVER_FEATURES.md`:

- Champion-targeted continuous `CardModifier` leaves that are not IntAttrs are not folded into a synthetic
  champion context.
- Full replacement pause/resume and `UntilDamaged` duration removal.
- Consistent state-based death timing across effect groups and simultaneous damage.
- Client acceptance scenarios for trigger and zone events.

## How to close an entry

1. Find the refusal named above (search the message text, for example `duration`, or `NotImplementedError`).
2. Run the live leaf the native resolver uses for that construct, with its target passed explicitly, as the
   existing `ir_exec_*` runners do.
3. Update `coverage()` so the construct is no longer refused, and remove the entry here.
4. Run the IR interpreter test modules for the area. A refusal that is removed without a passing test is not
   closed.
