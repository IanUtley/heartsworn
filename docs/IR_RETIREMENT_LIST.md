# M4 retirement list: native code the interpreter makes redundant

This is the working list for milestone M4 in [`IR_ENGINE_GOAL.md`](IR_ENGINE_GOAL.md): the native code that
runs only for abilities the IR interpreter now covers, and what must happen before each piece can go. It was
measured on the `0.5.0` branch on 2026-11-09 with the census script described under "How this was measured".
Re-measure before acting on any count here.

## Summary

- **Coverage is not the whole story.** `ir_exec.coverage()` refused 0 of 8,996 `AbilityTemplate` records before the
  picker work. `ir_exec.engine_runs()` then sent **384** of them to the native path. The zone pickers moved them onto
  the interpreter, group by group (see the sections below). As of 2026-11-10, **1** remains on the native path:
  `9b36aabc-dffc-edf3-eaf9-cd514adf3d6a`, whose invoke points at the null GUID (a Records error, see
  `IR_ENGINE_GOAL.md`, Non-goals).
- **Every pick group is now wired** except that Records error: Choosing, hand, discard, underground, warzone, deck (any
  count), union-zone picks, and variable-count picks.
- `ir_exec.engine_runs` respects coverage. Before 2026-11-10 it did not, so a refused ability could be routed to the
  interpreter and raise `NotImplementedError` at run time. Coverage is now memoised per ability (`_refused_by_coverage`).
- The cost-comparing `IntAttrFilter` (`m_CompareToCost`) is now run by the interpreter (see the section below), so no
  ability is refused for it.

## Done: the Choosing-zone picker (2026-11-09)

- `ir_exec._authored_zone` reads the authored `InZone` collection of a target's card filter. The collection *flags*
  are a visibility union (they name `Choosing` for Scheme's deck pick), so they must not decide the picker.
- `ir_exec.PICKER_WIRED_ZONES = {"choosing"}`: the native-path gate admits these. The deck gate is unchanged.
- `ir_exec.NeedsChoice.authored_zone` carries that zone to the caller.
- `rules_port/picks.py` `pick_kind`: revealed, then choice-zone, then deck search (matching-target for a match). This
  is the seam a web presenter registers against.
- `rules_port/resolution.py` `_prompt_interpreted_choice_zone` presents the picker through the Unity handler's
  `_prompt_choice_cards`, with a pending of kind `interpreted_choice_zone` that keeps the interpreter continuation.
- Answers: `application/pending_choices.py` (PvE) and `services/pvp_flow.py` (PvP) resume the interpreter with the
  chosen card. Nothing is played; the card is the target the authored pick names.
- `ir_exec._effective_zone`: a pick with no authored `InZone` takes the one zone its collection flags name (warzone
  troop picks). Flags naming more than one zone give "" and stay native. Checked: for 29 warzone picks, the interpreter's
  candidates equal `targeting.legal_targets` over the same battle (the native picker), and 21 of them are non-empty.
- `picks.ZONE_PICK_ZONES` (hand, discard, underground, warzone) and `ir_exec._picker_wired`: an authored `Hand` pick runs in the interpreter when its maximum card
  count is a constant (`picks.has_constant_maximum`). Variable counts stay native.
- `picks.hand_answer`: a single pick answers one UID (or None when an optional pick is declined); a multi-card pick
  answers the list of chosen UIDs (empty when declined). A required pick with too few cards keeps the prompt open.
- `rules_port/resolution.py` `_prompt_interpreted_zone_pick` (kind `zone_pick`) presents the pick through the trigger-target prompt
  (`_prompt_trigger_targets`, with an `interpreter=` continuation stored on the pending trigger). A declined optional
  pick resumes with no target.
- Answers: PvE `_resolve_pending_trigger_target` and PvP `_pvp_resolve_trigger_target` (via
  `_pvp_resume_interpreted_pick`) resume the interpreter instead of pushing the native chain item. PvE's Choosing and
  hand answers share `_resume_interpreted_pick`.
- Test: `tests/tests_ir_choice_zone.py` (pick kind; a discard pick of the opponent's troops reaches the zone picker; the Choosing child pauses with its candidates and the presenter
  receives the continuation; the single hand child pauses, is single-card, and reaches the trigger-target prompt).
- **Not verified:** the Unity presentation and the PvE/PvP answer paths have no test, and no live client run. The
  focused IR suites (`tests/tests_ir_*.py`) were not run.
- So the "0 refusals" figure in `IR_INTERPRETER_GAPS.md` is true for `coverage()` only. The choice-zone picker
  is the real open gap, and it keeps the whole native walker alive.
- **Nothing in the native effect path can be deleted yet.** The interpreter does not reimplement effects. It calls
  the live leaves in `rules_port/leaves_*.py` (imported by `ir_exec*.py`), so those leaves are shared, not
  duplicated. What is duplicated is the native *walker* that decides which effect runs when.

## Blocker: the choice-zone picker (must come first)

Until this is done, every native walker function below stays live.

| Item | Where | Status |
| --- | --- | --- |
| Deck picks gated by flags, variable-count hand picks, union-flag picks (64 abilities, one a Records error) | `rules_port/ir_exec.py` `_invoked_non_revealed_choice`, `_picker_wired`, `engine_runs` | Gated to native. Each needs a presenter in `rules_port/picks.py` and an answer shape. Validated against the native picker (`IR_ENGINE_GOAL.md` M2.1). The Choosing, hand, discard, underground and warzone picks with a constant count are done (see above). |
| Records error, null invoke (1 ability) | `9b36aabc-dffc-edf3-eaf9-cd514adf3d6a` | Records data. Excluded from counts; fix in extraction. |

**Exit check:** `engine_runs` returns True for every record in the census, and `NATIVE_OWNED_TEMPLATES` either
has entries with a written reason or is deleted.

## Shared helpers to move (not retire)

The interpreter imports these from `rules_port/resolution.py`, so `resolution.py` cannot be deleted while they
live there. Move them into a neutral module (for example `rules_port/target_values.py`) and re-point the callers.
This is a pure move: no behaviour change, and the `tests_resolution.py` and `tests_rules_port_kernel.py`
imports need updating in the same change.

| Function (`resolution.py` line) | Lines | Used by |
| --- | ---: | --- |
| `_random_target_sample` (39) | 28 | `ir_exec_targets.py`, native walker, `tests_rules_port_kernel.py` |
| `_list_target_values` (88) | 136 | `ir_exec_targets.py`, native walker |
| `_match_secondary_values` (256) | 114 | `ir_exec_targets.py`, `tests_resolution.py` |
| `_resolved_effect_target_values` (431) | 25 | `ir_exec_targets.py`, native walker |
| `_resolved_target_values` (372), `_referenced_effect_target_index` (403) | 29, 26 | native walker |
| `_uids`, `_shares_subtype`, `_current_match_card` (77, 226, 234) | 9, 6, 20 | target helpers above |
| `_cost_card_lists`, `_exhausted_cost_cards` (517, 527) | 8, 38 | cost payment; `tests_chain_abilities.py` |
| `_answers_from_target_map` (2161) | 9 | `chain_items.py`, `ir_event_effects` callers |
| `trigger_event_fields` (2239) | 12 | `application/pending_choices.py`, `transactions.py`, `pvp_flow.py` |
| `prompt_interpreted_reveal` (2184) | 53 | interpreter and native callers in `application/` |

## Native walker: retirement candidates

These run the native effect walk. Each can go once its callers are gone (the callers are listed in the next
section). Do not delete any of them before the blocker is done.

| Item (`resolution.py`) | Lines | What it does | Retire when |
| --- | ---: | --- | --- |
| `NativeEffectBackend` (567) | 963 | Native effect walk for one ability, one leaf per effect. | Blocker done, and no caller of `PortAbilityResolver` remains. Largest single removal. |
| `PortAbilityResolver` (1535) | 141 | Ability lifecycle around the walk (cost, targets, effects, stops). | Same. |
| `build_port_ability` (1678) | 25 | Builds the native ability object. | Same. |
| `resolve_port_ability` (1705) | 37 | Entry point to the native walk. | Same; it is the fallback for every interpreter caller. |
| `resume_ability_continuation_parents` (1762) | 164 | Resumes a paused native continuation. | Interpreter has its own pause/resume (`NeedsChoice`, `NeedsConversation`) and no native pause is left. |
| `resolve_port_played_spell` (1928) | 169 | Native play of a spell. | Interpreter runs spells (`chain_items.py` line ~653) and the Play Card path is checked (see below). |
| `resolve_port_played_permanent` (2121) | 38 | Native play of a permanent; calls the spell path. | Same as above. |
| `restore_played_card_ability_lists` (2099) | 20 | Restores card ability lists after play. | Same. |
| `_choose_ai_explicit_target_map` (458) | 48 | AI target choice on the native path. | AI must use the interpreter's targets first; check `ai.py` callers. |
| `resolve_port_trigger` (2289) | 150 | Native trigger resolution from the chain. | Interpreter already runs the trigger first (`run_interpreted_trigger`). Retire after the native fallback branch is removed. |

Subtotal: about 1,700 lines of `resolution.py` (the 963-line walker plus its entry points and the trigger path).

## Callers that carry the native fallback branch

Each of these tries the interpreter and then falls back to native. The fallback line is the one to delete once
the blocker is done. Each is an `if engine_runs(...)` or `interpreted is not None` check.

| Caller | Branch | Notes |
| --- | --- | --- |
| `rules_port/triggers.py` ~702–721 | interpreter, then `resolve_port_ability` | Inline trigger path. |
| `rules_port/resolution.py` ~2388–2400 | interpreter, then `resolve_port_ability` | Chain trigger path (`resolve_port_trigger`). |
| `rules_port/chain_items.py` ~650–668 | interpreter, then `resolve_port_ability` | Spell and activated abilities. |
| `rules_port/ir_event_effects.py` 317, 332, 436, 454 | native calls from inside an interpreter effect | Re-entrant: the interpreter calls native for granted or copied abilities. Move to the interpreter first. |
| `rules_port/leaves_abilities.py` 277, 309, 370, 479, 485, 649, 653 | native calls from inside the shared leaves | The leaves call back into the native walker. Break this cycle before removing either side. |
| `rules_port/choice_effects.py` 204, 267 | native calls for choice continuations | Depends on the choice-zone picker. |
| `rules_port/resources.py` 171, 194, 208 | native calls for resource abilities | Check each against the interpreter's coverage. |
| `rules_port/pregame.py` 529 | native call during setup | Setup stays native until `IR_ENGINE_GOAL.md` M1 covers setup events. |
| `ai.py` 2035, 4192 | native call from AI evaluation | Replace with the interpreter's target answers. |
| `application/*.py`, `services/pvp_*.py`, `services/ready_to_start.py` | many `resolve_port_*` and `resume_*` entry points | Entry points, not fallbacks. Re-point them after the walker is gone. |
| `hconnect_server.py` 1051 | `PortAbilityResolver` directly | Check whether it is a fallback or a live path before changing it. |

## Stale comments and docs found during the census

These do not change behaviour but will mislead the next change. Fix them with the list above.

- `rules_port/ir_exec.py` ~1144: `NATIVE_OWNED_TEMPLATES` is an empty `frozenset`. Its comment says it holds the
  built-in templates (Dazed, Vulnerable, Stealth, card-play, conscript). The `ENABLED` set (~1138) lists Dazed,
  Vulnerable and Stealth as interpreter-run, which contradicts the comment. Decide which is true, then fix both.
- `rules_port/resolution.py` `run_interpreted_trigger` (~2253) docstring says an ability the interpreter refuses
  "returns None, and the caller resolves it on the native path". The code only catches `NeedsChoice` and
  `NeedsConversation`. A `NotImplementedError` from `run_ability` is not caught, so the documented path does not exist.
- `docs/IR_ENGINE_GOAL.md` says Play Card is a native chain template. The census found no `engine_runs` exclusion
  for it, so this is probably stale. Confirm with the Play Card ability GUID before relying on it.
- `docs/IR_INTERPRETER_GAPS.md` "Outstanding" table reads 0, which hides the 383 choice-zone abilities above.
- `docs/RULES_PORT_PARITY.md` rows around lines 1243–1244 still cite `abilities/framework/triggers.py`, which is
  deleted.
- `abilities/` contains only stale `__pycache__` files and no tracked source. It can be removed.

## Milestone exit checks (M4)

1. The census reports 0 abilities with `engine_runs` False, apart from documented Records errors.
2. Every shared helper in the table above lives outside `resolution.py`, and `resolution.py` imports nothing
   that a native walker removal would break.
3. Each fallback branch in the callers table is removed, or kept with a dated reason here.
4. The native walker items are removed, or kept with a dated reason here, and the focused tests in
   `tests/tests_ir_*.py`, `tests/tests_resolution.py` and `tests/tests_chain_abilities.py` pass.
5. The equivalence harness (`tests/ir_equivalence.py`) passes for each family whose native code was removed.

## How this was measured

A census script (not committed) loaded every `AbilityTemplate` from the Records through
`gamedata.DEFAULT_RECORD_STORE`, then ran `ir_exec.coverage(guid)` and `ir_exec.engine_runs(guid, 0, 0)` on each.
The 383/384 split came from `ir_exec._invoked_non_revealed_choice` over the native-path set. Function sizes were
taken from the `ast` line ranges of `rules_port/resolution.py` (2,438 lines). The caller list came from
`grep` over `--include=*.py` and excludes `tests/` except where noted.


## Done: deck, union and variable-count picks, and cost-comparing filters (2026-11-10)

- **Deck picks.** An authored `Deck` pick with a constant count takes the deck-search prompt (the same prompt the
  flags-deck picks already used). The gate had excluded these by collection flags alone. Candidates were checked
  against `targeting.legal_targets` in two synthetic battles: 28 of 30 matched. The two that differed both compare
  their attribute to the card's cost, so they are now refused by coverage.
- **Union-zone picks.** A collection naming several zones, or several flags with no `InZone`, is the pick from any of
  them (`_effective_zone` gives `any`). The zone prompt covers it. Two distinct cases checked: both match, non-empty.
- **Variable-count picks.** The interpreter's count comes from the ability's variables, so the zone prompt now takes
  the interpreter's bounds (`_prompt_trigger_targets(bounds=...)`), not the template's. Three hand and three warzone
  variable picks checked against the native candidates: all match, non-empty.
- **Cost-comparing filters.** `IntAttrFilter` with `m_CompareToCost` compares to the card's cost. The interpreter's
  card carries no cost, and the native filter uses `resource_cost` plus the casting adjustment (zero for some card
  types). Rather than copy that into the interpreter, coverage refuses these filters (`_compares_to_cost`). One
  Records target template has the flag; four abilities reach it. This changes four abilities from interpreter to
  native, which is the safe direction.
- Tests: `tests/tests_ir_choice_zone.py` has seven tests. The new one checks the cost refusal. All seven pass when run
  by calling the functions directly, since pytest isn't installed. The wider `tests/tests_ir_*.py` suites have not
  been run; they are to be run last.
- **Not verified:** the Unity client and both PvE and PvP answer paths for deck, union and variable picks. The
  comparisons above are single synthetic battles, not a full equivalence run.


## Done: card cost in the interpreter (2026-11-10)

- The interpreter's `IntAttrFilter` with `m_CompareToCost` compares to the card's **current** cost (`ir_exec._effective_cost`),
  not the authored value. Before this it compared to `m_Value` (0), which ignored the cost.
- A card with a `game_cards` row has the payment path's cost: `rules_port.static_rules.effective_cost` (printed cost,
  `card_cost_mod`, and static cost modifiers). The cost changes during a battle, so it is read when a filter needs it.
- A card with no row yet, such as one a template makes before it is placed, costs its template's cost
  (`card_templates.cost`). The card carries its template (`Card.template`) for this.
- Coverage no longer refuses this filter (`_compares_to_cost` was removed). Only one Records target template has the
  flag; the four abilities that reach it are interpreter-run again.
- **Differs from native.** The native filter reads the card dict's `cost` key, which is built from the template's
  printed cost, so it does not see a `card_cost_mod`. The interpreter follows the game_cards cost, as the user asked.
  If a native deck pick ever disagrees after a cost change, the native side is the one to check.
- Tests: `tests/tests_ir_choice_zone.py` has seven tests. The cost test checks the current cost, the template fallback for a
  card with no row, and that a `-2` `card_cost_mod` in game_cards changes which card passes. All seven pass when run by
  calling the functions directly, since pytest isn't installed.
- **Not verified:** the Unity client, and whether any generated card reaches this filter before it has a row. The
  wider `tests/tests_ir_*.py` suites have not been run; they are to be run last.


## Done: the shared helpers moved out of `resolution.py` (2026-11-10)

The helpers the interpreter and the application layer use now live in neutral modules. `resolution.py` keeps only the
native walker and its entry points (1,910 lines, down from 2,491).

- `rules_port/target_values.py`: `_random_target_sample`, `_uids`, `_LIST_ATTR_BY_KIND`, `_list_target_values`,
  `_shares_subtype`, `_current_match_card`, `_match_secondary_values`, `_resolved_target_values`,
  `_referenced_effect_target_index`, `_resolved_effect_target_values`, `_answers_from_target_map`.
- `rules_port/cost_cards.py`: `_COST_LIST_NAMES`, `_cost_card_lists`, `_exhausted_cost_cards`.
- `rules_port/interpreted_prompts.py`: `prompt_interpreted_reveal`, `_ai_revealed_pick`,
  `_prompt_interpreted_choice_zone`, `_prompt_interpreted_zone_pick`, `trigger_event_fields`.
- Callers re-pointed: `ir_exec_targets.py`, `chain_items.py`, `application/pending_choices.py`,
  `application/transactions.py`, `services/pvp_flow.py`, and the tests (including the `mock.patch` path).
  Nothing imports a moved name from `resolution` any more (checked by an AST scan of the source directories).
- `resolution.py` imports the moved names it still calls, so the native walker keeps working until it is retired.
- Not moved: `_log_ability_start`, `_choose_ai_explicit_target_map` and the native walker classes. They are used only
  inside `resolution.py`, and the walker retirement removes them.

Verification: the three modules compile and have no undefined names. Four tests ran and passed (the choice-zone file, the
match-secondary test, the random-sample test, and the exhaust-cost test). The wider `tests/tests_ir_*.py` suites have
not been run; they are to be run last.


## IR suites run (2026-11-10)

Run: every `tests/tests_ir_*.py`, `tests/tests_conditions.py`, `tests/tests_targeting.py`, plus the two script-style
files `tests/tests_ability_ir.py` (9 tests, OK) and `tests/tests_ir_surface_write.py` (10 tests, OK). Pytest is not
installed here, so the function-style files ran through a small runner that binds each module's `make_db()` the way
`tests/conftest.py` does.

- **114 pass, 1 fails.**
- Updated one stale expectation: `tests/tests_ir_exec.py::test_a_choice_from_the_choice_zone_runs_in_the_interpreter`
  (was `..._keeps_its_ability_on_the_native_path`). It encoded the contract from before the picker work, and the pick
  is now a wired Choosing pick.
- **Pre-existing failure, not from this work:** `tests/tests_ir_exec_targets.py::test_store_targets_stores_each_target_for_this_ability_and_restores_the_state`.
  Its `_StoreContext` stub lacks `template_value`, which `leaves_variables.store_target` reads (`m_SetTargets`). The read
  was added in commit `3b5ba3d`. Adding `def template_value(self, name, default=None): return default` to the stub
  is the likely fix; I have not made it.
