# Goal: the IR interpreter as Hex's main card engine

## Goal

Make the Records-driven IR interpreter (`rules_port/ir_exec.py` and its modules) the engine that runs every card
ability in Hex, in Practice/PvE and in PvP, with the native resolver kept only for what the interpreter cannot
yet run, until that remainder is empty.

Three measurable parts:

1. **Coverage.** Raise the share of Records abilities the interpreter runs.
2. **Gaps.** Close the refusal reasons in the gap report until none is left, except where the Records themselves are wrong.
3. **Main engine.** Turn the interpreter on by default, in both modes, and retire the native card-ability resolver for
   the abilities it covers.

## Where we are

Measured with `tools/ir_coverage/ir_gap_report.py` over the 8,992 `AbilityTemplate` records in the Records (four placeholder-art abilities are excluded, as the report does):

| Measure | Value |
| --- | --- |
| Covered by the interpreter | 8,965 (99.7%) |
| Refused by `coverage()` (reachable) | 12 (0.1%) |
| Orphans (refused, unreachable from any game record) | 15 |
| Coverage check raises (bad Records data) | 0 |

The interpreter is on by default in `restart.sh` and the Docker image (`HEX_IR_ENGINE=1`); set `HEX_IR_ENGINE=0` to
turn it off at start-up. The code default is still off, so tests and imports that do not go through those launchers
run the native resolver. `!irengine on|off` changes it in game. The equivalence harness (`tests/ir_equivalence.py`) confirms that covered abilities leave the same
battle state as the live engine, but it only covers turn and trigger-event abilities, and it cannot yet build a
scenario for most setup abilities.

The remaining refusals are listed, with the code that raises each one, in `docs/IR_INTERPRETER_GAPS.md`.

## Done when

- **Coverage:** at least 99% of Records abilities are covered. The rest are explicitly listed as Records data
  errors, with the Records fix needed, and not as interpreter gaps.
- **Gaps:** `docs/IR_INTERPRETER_GAPS.md` lists no refusal that the interpreter could run. Every entry left is a
  Records error, a rules question for the user, or a dependency on another system (see Non-goals).
- **Validation:** the equivalence harness has an acceptance case for every event family the interpreter runs,
  including setup events (`GameStartedEvent`, `PreGameEvent`), and each passes against the live engine in Practice/PvE
  and in PvP. There are no unexplained failures.
- **Default:** the interpreter is on by default in Practice/PvE and PvP. The native resolver still exists for
  any ability the interpreter refuses, and the switch can turn the interpreter off for diagnosis.
- **Retirement:** for each covered family, the native code path that duplicates it is removed, or a dated
  decision keeps it, with the reason written down.

## Milestones

Each milestone has an exit check that can be run without judgement.

### M1: Make the evidence trustworthy

- Extend the equivalence harness to every trigger event in `TRIGGER_EVENTS`, including setup events.
  Exit: a case per event family runs and passes, or is listed as harness-blocked with the reason.
- Build setup scenarios the harness can run. Setup abilities share their card with other setup abilities, so the
  harness needs a fixture that fires them together, and compares the whole set on both sides.
  Exit: `GameStartedEvent` and `PreGameEvent` cases compare the live and interpreted states, not just "did run".
- Run the same cases in PvP. The harness today runs Practice/PvE only.
  Exit: the same family cases pass in the PvP adapter.

### M2: Close the largest gaps

In order of ability count (current counts from the gap report):

1. Multi-card targets and chosen targets of more than one card (about 400 across min and max counts, plus 146 chosen).
   Needs the picker to take a set, validated against the native picker.
2. Continuous modifiers with `WhileCardInPlay` (about 320 attribute and int-attribute, about 40 rule modifiers).
   Needs the static path to project every attribute and int-attribute kind, shown by test, before the interpreter
   accepts them. Otherwise keyword grants can be dropped silently.
3. Trigger events: `TriggerTarget` selectors (135), `CardReadiedEvent` (76), `CardExitedZoneEvent` (37),
   `GainThresholdEvent` (37), `GainChargeEvent` (31), `CardBlockedEvent` (28), and the long tail.
4. Effect kinds: `RepeatingAbilityEffectTemplate` and `ConversationAbilityEffectTemplate` (23 each), transform
   effects (about 90 across forms), `CreateTokenMatchingTargetAbilityEffectTemplate` (11), and the rest in the gap report.
5. Variables: `CardIntegerVariable` (19), trigger-scope variables (about 20), and the rest. Bind computed variables
   through the native evaluator, as the intattr and list variables already are.
6. Grants: powers from sockets, payments, remembered or random sources (65).

Exit for each: its refusal reason is gone from the gap report, with a passing test, and its harness cases pass.

### M3: Make it the default

- Turn `ENGINE_ON` on by default in Practice/PvE, with a documented off switch for diagnosis.
  Exit: a full Practice game and a full PvE campaign run to the end with no interpreter-caused errors in the log.
- Do the same for PvP. Exit: a full PvP match runs to the end with the same check.
- Confirm against the client log before each step (see AGENTS.md). Client errors such as
  `Command handler not found` or a missing 3055 acknowledgement are server defects to fix, not reasons to roll back.

### M4: Retire the duplicated native code

- For each covered family, list the native functions that now run only for abilities the interpreter covers.
- Remove them, or keep them with a written reason (for example, the fallback for a refusal that stays).
- Exit: the list is empty, or each kept item has a dated reason.

## Non-goals

- **Records data errors.** The Records are the authority. Where a record is wrong, the fix is in the extraction,
  not in the interpreter. One known case: `9b36aabc-dffc-edf3-eaf9-cd514adf3d6a`, whose clause points at a null template.
- **Client changes.** The client cannot be changed, so every fix is server side.
- **Rules questions.** Where C# and the live native path disagree (for example, a random target with no card filter),
  the goal is to ask which is right before changing either. The interpreter follows C# by default.
- **Unrelated server features.** Campaign and economy work are out of scope, except where a campaign uses an ability.

## Risks

- **Silent behaviour change.** The interpreter can run an ability that the native path resolved differently, and the
  game still looks fine. The equivalence harness is the guard, so M1 comes first.
- **Setup ordering.** Setup abilities depend on the opening sequence (coin toss, mulligan, first priority). A
  change there breaks every game, not one card. Keep setup discovery native until M1 covers it.
- **The static path.** Continuous effects are projected by `rules_port/static_rules.py`. The interpreter must not
  take over a continuous effect that the static path does not fully project.
- **PvP and PvE divergence.** A card fix has to be checked in both modes. Per AGENTS.md, a choice made for one mode
  must be asked about for the other.

## How progress is tracked

- The gap report (`tools/ir_coverage/ir_gap_report.py`) is the source of the counts. Re-run it after each change, and
  update the numbers in `docs/IR_INTERPRETER_GAPS.md`.
- Each milestone's exit check is run and its result written into the change's commit message.
- A refusal removed without a passing test does not count as closed.
