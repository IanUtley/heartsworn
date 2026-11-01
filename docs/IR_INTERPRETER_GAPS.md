# IR interpreter: remaining gaps

The Records-driven IR interpreter (`rules_port/ir_exec.py` and its `ir_exec_*`, `ir_modifiers.py`,
`ir_triggers.py`, `ir_variables.py`, `granted_abilities.py` modules) runs an ability only when `coverage()`
accepts every construct in it. An ability with any refused construct stays on the native resolver path. The
interpreter never half-runs an ability.

## Current state

Measured with `tools/ir_coverage/ir_gap_report.py` over the 8,992 `AbilityTemplate` records (four abilities of placeholder-art cards are excluded: OpponentChooseAndPlay, ChooseRevealAndPlay, Footsteps from the Hall (A) and (B); their implementation is done, and the migration counts them as done), at the latest run:

- Covered by the interpreter: 8,978
- Refused: 14 (12 orphans, 0 Records errors, 2 covered in context, 0 reachable; the orphans are listed below) (the reasons are under "Outstanding after the latest run" below)
- Coverage errors (the coverage check raising on bad Records data): 0 at this run

Regenerate the numbers and the full reason list from the current code and Records:

```bash
python3 tools/ir_coverage/ir_gap_report.py --top 80 --json /tmp/ir_gaps.json
```

The counts below are one ability's refusal reasons each. An ability with several reasons is counted under each.

Closed so far: chosen `SinglePlayer` targets, random targets with a card filter, `PlayedResources` moves,
turn-boundary durations on attribute and int-attribute modifiers, and the `IntAttrAbilityVariable`,
`CountListAttrAbilityVariable` and `SumVariableInListAttrCardsAbilityVariable` variables, and the
`GameStartedEvent` and `PreGameEvent` setup triggers (see Triggers for what is not yet validated), the
`CardIntegerVariable` variable, valid `SetCardIntegerVariable` operations, the `TriggerTarget` selector, and the
`CardReadiedEvent` trigger (68 harness cases pass; 51 are harness-blocked), and the `CardBlockedEvent` trigger
(21 harness cases pass; 21 are harness-blocked), the `CardSacrificedEvent` trigger (8 harness cases pass;
4 are harness-blocked), and the `WhileCardInPlay` continuous clauses: 370 the static scan
can project, plus the 11 linked ones (triggered, player-activated or invoked) whose grant is now applied and recorded
in the linked ledger (see that section), and the `CardExitedZoneEvent`, `GainChargeEvent` and `ChampionHealedEvent`
triggers (88 abilities; 112 harness cases pass, and the event's own player is now the trigger's (see Triggers)), the
fight/block "second card" family (80 abilities, 72 of which now run; see the section below), and the granted powers
from sockets, payments, remembered powers and random picks (62 of the 66 grants now run; see the section below).

## Outstanding after the latest run

The 0 reachable refused abilities, by refusal reason (orphans excluded). An ability with several reasons is counted under each, so the
column sums to 0. Regenerate with `python3 tools/ir_coverage/ir_gap_report.py --top 200`.

| Reason | Abilities |
| --- | ---: |

Notes on the largest entries (the counts in these notes are from an earlier run, and the table above is the current one):

- **Chosen follow-up pickers and multi-card targets (about 26 with the invoked forms).** These need the native pick
  prompts mirrored in the interpreter, with a parity check per ability. The deck-search picker is already wired for a
  deck-only pick; a pick from a wider set has no interpreter picker yet.
- **Static and "until" durations (about 15).** `WhileCardInPlay` on an authored static is projected by the native
  static path rather than run, so it is refused here by design where the interpreter would have to apply it. The
  `WhileCardOnTopOfDeck`, `UntilEndOfDungeon` and `BeginningOfOpponentsTurn` durations need their own lifetimes.
- **Data-driven gaps.** `cost duration EndOfTurn` on played actions needs the linked-cost ledger checked against C#.

## Orphan abilities

A refused ability that no game record can reach: no card, champion, talent, deck or encounter starts it, directly or through the records they refer to. Nothing in the Records can start these, so they are not counted in the reasons above. The count is 15. Some may still be reached from client code rather than the Records: the client's built-in card-play template (`Play Card Ability`, see HOWTO.md) is one. Check each against the client before treating it as dead. Regenerate the list with `ir_gap_report.py --json`.

| Ability GUID | Records name |
| --- | --- |
| `0eb4917d-6545-965d-d564-aa6917402301` | AtTheStartOfYourTurnIfYouHaveFiveOrMoreChargesYouLoseAllCharges |
| `16ffaa33-6b56-4e77-a9b9-c5fd8db4420a` | WhileYouAreBdazedbIgnoreChainNonmresourceCardsInYourHandHaveCostP1 |
| `1730bd95-7c2a-4a19-9d6d-80c9c22bb720` | WhileYouAreBstealthbOpposingTroopsCanTAttack |
| `26f9efa9-584e-0dfc-ccca-e499245137e8` | TwoRandomArdentOrUnderworldTroopsInYourDeckGetM1AtkM1Def |
| `340add49-e730-a933-e64b-8bf4b4130be2` | Basic3ArrowrYourCardsAndEffectsDealDoubleNonmcombatDamageUntilTheStartOfYourNextTurn |
| `5a8783b0-e420-4f41-b2a1-96f70b0cd851` | Play Card Ability |
| `714f16cc-9caa-1436-56f3-22acc288bfbb` | TwoRandomTroopsInYourDeckGetM1Atk |
| `79c61378-3764-430e-ae8b-efebe258701d` | YourBvulnerablebChampionOrYourBvulnerablebTroopsTakeDoubleDamage |
| `7e8bae76-202f-4225-84ef-dde8339ccff2` | WhileYouAreBstealthbYouHaveBspellshieldb |
| `8553550b-a8cb-4780-be5c-b698cd540df2` | Conscript Modifier Ability |
| `aa1c4b06-b125-9e8a-0edc-1ed28b7d6c1c` | ARandomTroopInYourHandGetsM1Atk |
| `ae783759-b8ea-427c-826b-883f9adef746` | IgnoreChainFinishMovingSelfToWarzone |

## Closed in the latest stretch

Checked against the C# implementation, with the live engine agreeing on each case the harness can run:

- Trigger events now run in the interpreter: `CardAttackedOrBlockedEvent`, `CardTappedEvent`, `CardDestroyedEvent`,
  `CardScroungedEvent`, `CardsAttackedEvent` (with the attacker count in the event TAC), `CounterAddedToCardEvent`,
  `CardTransformedEvent`, `IlluminatedEvent`, `VerdictEvent`, `HiddenCardEnteredZoneEvent`.
- Conversations: an encounter conversation pauses the ability at the native leaf, and the client's answer resumes the
  interpreter at the next effect (`ir_exec.resume_conversation`).
- Card filters: `TACFilter` (quick, basic and threshold checks); `PlayersWhoControlMatchingFilter`, after the target
  scan's cards carry effective attack and defense and the champion branch reads the board.
- Trigger conditions the native evaluator already reads: six of them, and the serialized TAC as data in coverage.
- Variables: `TriggerTargetPropertyVariable`, the source-player charge, resource, shard and briar-legion variables,
  `DrawAmount` (the draw count a player chooses), and `RandomizeVariable` with a second value and a variable maximum.
- Stats: `ReplaceExistingValue` and a named `CurrentAttackValue` or `CurrentDefenseValue` property now apply the C#
  rule on both paths (the native stat leaf previously ignored both).
- `CreateAndCastSpell`, the talent animation effect and "lose half your health" run through their native leaves.
- Activation gates: an `IntAttrFilter` gate on an ability with no trigger (C# tests the trigger condition on
  activation) runs and matches the native gate.
- Chosen numbers in a card filter: the client already sends the player-chosen value with the activation, so the
  interpreter reads it from the ability's variables.

Harness: a case may set `quick` (the source card is a quick card), `counters` (seeded source counters), `card_type`
(the source's type), `event_source` (the event is sourced on the player's or opponent's champion) and `event_tac`
(the event's TAC). `any_template` lets a case run an ability no card template fires alone.

## Historical sections

The sections from "Ruling queue" to "Data" record the refusal counts of earlier runs (952, then 873, then the counts
recorded under each area). They are kept for the reasoning behind each ruling. The counts in them are superseded by
"Outstanding after the latest run" above.

## Ruling: `WhileCardInPlay` (implemented)

A `WhileCardInPlay` clause is either *projected* or *recorded*, and which one it is follows from who owns the clause:

- An **authored static** (`m_TriggerEventType` empty, not `m_Manual`, not an invoked child) is projected. The
  interpreter runs nothing, and the native static path derives the effect from the source card's *current* state on
  every read (`rules_port/static_rules.py`). That is one mechanism, not two, and the reason it is a projection is the
  transform case: `transform_instance` → `db_transform_card_instance` overwrites `game_cards.card_abilities` with the
  new template's list, so a transformed source's aura disappears on the next read with no cleanup step, while an
  *applied* delta would need an inverse that nothing fires.
- A **linked clause** -- triggered, player-activated, or an invoked child's -- applies to its targets when it resolves
  and is recorded in the linked ledger (`rules_port/effect_lifetimes.py`), which drops it when the source leaves play
  or loses the ability. It cannot be projected: it is not an aura on the source's ability list, and its targets are
  chosen at resolution.

The C# rules engine agrees, and shows the boundary is the source *ability*, not the source card:

- `AbilityInstance` (`AbilityInstance.cs:44`) builds one `AbilityEffectInstance` per mapping in
  `m_AbilityTemplate.m_AbilityEffectList`, so each effect instance is owned by the ability instance that created it.
- `AbilityManager.RemoveAbilities` (`AbilityManager.cs:804`) disables on `SourceCard == sourceCard && m_Duration ==
  duration`. Only `WhileTargetInPlay` is keyed on the target, so a transform of the *target* does not end another
  card's `WhileCardInPlay` effect.
- `Session.DeactivateCard` (leaving play), `Session.TransformCard`, `Session.RevertCard` and
  `SetChampionTalents` each call `RemoveAbilities(card, WhileCardInPlay)` **before** the template swap, then
  `CreateAutomaticAbilitiesForTransform` rebuilds fresh instances from the new template. So a transform, revert or
  talent change ends the effect.
- `AbilityEffectInstance.Apply` (`AbilityEffectInstance.cs:590`) then gates the re-applied effect on the source being
  in `Champions|Warzone|Simulacrum`.

Implementation, and what each part is for:

- `continuous_statics.covered_as_static` no longer accepts on the duration alone. It accepts only an ability that is an
  authored static (no trigger, not `m_Manual`, not an invoked child) whose clause the scan actually resolves:
  `static_rules.projects_continuous_modifier` classifies the leaf (property the scan spells out, operand either
  authored or a variable kind the native evaluator binds) and `static_rules.projects_clause` then asks the scan's own
  snapshot (static metadata plus the materialized effect row for that clause). Gating on the duration alone let leaves
  the scan ignores pass as covered and vanish silently — 20 abilities were doing exactly that.
- `ir_exec._run_clause` still skips an authored static's clause at runtime, and applies a *linked* clause with the same
  duration. `ir_exec` decides which one a clause is at coverage time (the `linked` flag in `coverage`) and at run time
  (no trigger, not `m_Manual`, not `scope.invoked`); both ask the same three questions, so coverage never accepts a
  clause the runner then skips, nor refuses one it would run.
- `effect_lifetimes` carries the linked model uniformly: every entry (`record_grant`, `record_temporary_intattr`,
  `record_temporary_stat`) stores the creating ability (`source_ability`, from `_resolving_ability`, which prefers
  `resolving_root_ability`), and `_expired`, `_intattr_expired` and `_stat_expired` all expire a source-scoped entry
  when the source leaves play **or** `_source_ability_gone` finds the ability missing from the source's current ability
  list (card abilities plus socketed gems; a champion has no card row, so its list is never rebuilt here and its effects
  stand). `_stat_expired` had no `WhileCardInPlay` branch at all before, so a stat recorded with that duration never
  expired.
- The ledger keys on the **root** ability, not the current frame. An invoked child is not on its source card's ability
  list, so recording the child would make `_source_ability_gone` true at once; C# removes the effect when the card's
  whole list is rebuilt, which is the root's relationship to the card. `Scope.root_ability` and `Scope.invoked` carry
  that through invocation, pause and resume, and `_bridge_caster` publishes it as `resolving_root_ability`.
- The recording side exists for each kind: `stat_effects.apply_stat_mod(..., duration=)` writes the delta into
  `permanent_buffs` and records it with `record_temporary_stat`; `effects._int_attribute` accepts the duration and
  records it; `attribute_effects.apply_attribute_grant` puts the bits in `temporary_attributes` with a
  `{"boundary": "while_in_play", "source", "ability"}` rule in the card's `__attribute_expirations`; and
  `effects._card_cost` reverses the cost delta through the ledger. A while-in-play cost change is applied as a permanent
  modifier and undone on expiry, the only symmetric inverse available.
- The sweep is `effect_lifetimes.expire_while_in_play`, called before every dispatched event (`triggers` then pushes a
  `CardUpdated` for each card it changed) and at every turn boundary
  (`lifecycle.clear_expired_temporary_attributes`). A while-in-play effect has no boundary to wait for, and those are the
  two places the board can change.

What is left of the cluster (24 abilities, all 24 with this as their only refusal): every one is absent from the
projection snapshot -- 24 have no `card_abilities_meta` row and 1 no `ability_effects` row -- so the scan cannot
project them no matter what their Records say. None of them is referenced by a card's `m_CardAbilities` or
`m_AutomaticAbilities`, so they are unreachable templates rather than live auras. No linked ability remains.


## Ruling queue

The refusals grouped by the node type that causes them, ordered by how many abilities the node alone
blocks. (Counts from the 952-refusal run; the reveal-and-store row has been removed since.) One ruling on a node type closes every ability
listed against it; a ruling is a statement of what the node means, when it fires or applies, and how it changes
the battle.

| Node type | sole | any |
| --- | ---: | ---: |
| `TransformCardAbilityEffectTemplate` of a player | 24 | 41 |
| `stat duration BeginningOfOwnersTurn` | 21 | 23 |
| `effect RepeatingAbilityEffectTemplate` | 20 | 23 |
| `card count of different races` | 18 | 18 |
| `cost duration EndOfTurn` | 17 | 19 |
| `trigger CardTransformsEvent` | 17 | 18 |
| `trigger ConscriptEvent` | 15 | 15 |
| `trigger FateweavedEvent` | 14 | 16 |
| `cost or optional (m_VariableActivationCost)` | 12 | 12 |
| `chosen follow-up target` (`ir_exec_targets.follow_up_gaps`) | 11 | 14 |
| `trigger condition IntAttrFilter` | 10 | 12 |
| `trigger CardAttackedOrBlockedEvent` | 10 | 10 |
| `stat derived from another card` | 9 | 14 |
| `IntAttrModifier duration WhileCardInPlay` absent from the projection snapshot | 9 | 11 |
| `effect CreateTokenMatchingTargetAbilityEffectTemplate` | 9 | 11 |
| `effect CreateAndCastSpellAbilityEffectTemplate` | 9 | 9 |
| `cost change in an ability that invokes another` | 9 | 9 |
| `TransformCardAbilityEffectTemplate` of a player (invoked) | 9 | 9 |
| `TransformCardAbilityEffectTemplate` of a player (invoked twice) | 9 | 9 |
| `trigger PowerShiftedEvent` | 8 | 8 |

The remaining 148 node types each block fewer than eight abilities on their own (211 between them; 72 of them
never block one alone), and they are listed in full by the report below.

## Triggers

The interpreter runs only the trigger events in `TRIGGER_EVENTS` (`rules_port/ir_triggers.py`), plus the turn and
entered-zone events in `ir_exec.py`. Other trigger types are refused as `trigger <Event>`.

- Setup triggers now run in the interpreter: `GameStartedEvent` and `PreGameEvent` are in `TRIGGER_EVENTS`.
  Discovery, the zone and collection checks, and the setup ordering stay on the native path (`rules_port/triggers.py`,
  `rules_port/pregame.py`, `services/tournament_game.py`). Only the effects of a setup ability are interpreted.
  Not yet validated against the live result: the equivalence harness (`tests/ir_equivalence.py`) cannot build a
  scenario for most setup abilities, because they share their card with other setup abilities and so never fire
  alone. Two covered setup cases did not reach the interpreter in the harness, so their discovery is unverified.
- `trigger selector TriggerTarget` (135): the trigger's target is chosen by the trigger, not the event.
- `GainThresholdEvent` (37): harness-blocked. The equivalence harness builds scenarios for it, but the native
  trigger never fires there, because the harness does not pass the event data a threshold gain carries (the colour
  and count). The event stays refused until the harness fires it, and then it passes against the live result.
- `CardExitedZoneEvent` (33), `GainChargeEvent` (30) and `ChampionHealedEvent` (25) are covered: the events carry
  nothing beyond a source card and its player (plus, for the exit, the two collections), so the interpreter needs
  no event data of its own. See the section below for what each one means. 6 of the 94 abilities that use them stay
  refused for unrelated constructs (a repeating effect, a three-card target, a conversation, a token that copies
  its target, a `BeginningOfOwnersTurn` duration).
- About 40 more event types with fewer than 20 abilities each.
- Trigger conditions: `TACTriggerCondition` (38), `trigger condition IntAttrFilter` (12). (`TriggerAbilityIsChargePower`
  now runs through the shared evaluator, which reads the activated ability from the battle state.)

## Trigger event data, and the events' own player (implemented)

Three findings decided this cluster, and each one changed behaviour beyond it.

**The event's player is the event's `SourcePlayerId`.** C# sets `TriggerEvent.SourcePlayerId` from whoever the
event is about -- the healed champion's controller for `ChampionHealedEvent`, the champion's controller for
`GainChargeEvent`, the controller the card had *before* the move for `CardExitedZoneEvent` -- and
`TriggerPlayerControlsAbilitySource` reads it. The interpreter was handed the resolving ability's owner instead, so
`TriggerPlayerControlsAbilitySource` was true whenever its own trigger fired at all: the 109 already-covered
abilities whose condition is `NotTriggerCondition(TriggerPlayerControlsAbilitySource)` ("when an *opposing* ...")
could never be true, and the new cluster's per-player conditions would have fired for the opponent's charge or
heal. `ir_exec.live_event` now takes the dispatch's `source_player_id`; `run_interpreted_trigger` and both chain
items in `rules_port/triggers.py` carry it. An ability with no source player (a synthetic event) still falls back
to the ability's owner.

**`CardExitedZoneEvent` is an `IZoneChange`.** `Session.FinishMovingCard` enqueues it only when the collection
actually changes, and before `CardEnteredZoneEvent`, with `SourceCollection`, `DestinationCollection` and the
previous controller. The port's death path and its effect-driven discard sent neither collection, so every "when
this leaves play" condition only worked through `TriggerCardEnteredZone`'s fallback to the card's current zone --
and the interpreter, which had no fallback, judged them false. Both sites now pass `warzone -> discard` (death) and
`hand/deck/... -> discard` (discard leaf). The condition compares the collections as flag masks
(`(wanted & actual) != None`), which is what makes a destination of "Deck|Hand|Champions|Warzone|Discard|Void|
CastSpells|Underground|Choosing" -- "anywhere", 29 of the 37 exit abilities -- accept the crypt the card really
entered.

**`ChampionHealedEvent` comes from `HealChampion` only.** `Session.SetChampionHealth` publishes
`ChampionHealthChanged` and nothing else; `HealChampion` enqueues the healed event after `CantGainHealth` and the
life-gain modifiers. `set_hero_health` (`SetHeroHealthModifier`) was emitting it whenever the health rose, so a
"set" counted as a "gain". It no longer does. (`gain_health` still does not apply `LifeGainModifier` or
`LifeGainMultiplier`; that divergence predates this cluster.)

The three event-reading trigger conditions (`TriggerCardIsAbilitySource`, `TriggerPlayerControlsAbilitySource`,
`TriggerCardEnteredZone`) are now evaluated by the native shared evaluator (`rules_port.conditions`) from the
interpreter too (`SHARED_TRIGGER_CONDITIONS` in `ir_exec.py`), so the two paths cannot disagree about which card
moved, between which zones, or whose event it was.

Evidence: `tests/ir_equivalence.py` runs the three events' 112 cases (both owners for a champion-sourced event, both
controllers for an exit, plus the abilities' own target variants) with no difference between the live path and the
interpreter, including the authored "When an *opposing* champion gains health" ability that could not fire before.
`IR_EQ_EVENTS=ChampionHealedEvent,GainChargeEvent,CardExitedZoneEvent` reruns just this cluster.
`tests/tests_rules_port_kernel.py` pins the event player, the exit's collections and the death path's
exit-before-entry ordering.

One thing the harness found while listing these cases is worth recording separately: run over *every* ability of an
event rather than the first 40 cases, it reports 8 covered `CardEnteredZoneEvent` abilities and 3 covered
`TurnStartedEvent` ones whose interpreted result differs from the live one -- a stat grant that did not apply, an
extra or missing `CardUpdated` (the 3 turn cases are all this: the live path publishes an update for a no-op), a
counted amount of 0 instead of 1, and one charge removed on the live side and added on the interpreted side. None
of them reads trigger-event data, so they are not this cluster, and they are listed here as the next parity work
rather than refused: the divergence is in the effect, and which of the two paths is wrong is not yet known.

The same full-set run (`IR_EQ_LIMIT=100000 IR_EQ_WORKERS=8`) reports **1110 passed, 14 failed, 3393 uncovered**
(plus 22 choice comparisons passing and 1 failing). The 14 are effect divergences of the kinds above: an extra or
missing `CardUpdated` (a no-op on one side, and the extra `atk`/`def` of 0 buffs on the interpreter's side), a charge
whose `operation` differs, one side gaining 2 health where the other applies nothing, a reveal that is not
re-published, and one crypt move the interpreter does not make. Re-running them with the pre-change readers
(`instance_ref` reading 0 as none, and the contingency check ignoring a 0 reference) leaves all 14 failing, so none
of them comes from the fight/block work. They depend on the cases that ran before them in a process (one of them
passes when run alone), because the port keeps state across cases.

## Fights and blocks: the second card (implemented)

`Battle2CardsAbilityEffectTemplate` ("this battles that") and `BlockEffectTemplate` ("this blocks that") each
name their other card through `m_SecondaryTargetIndex` on the *battle* clause. That field is an
`m_EffectInstanceId`, not a target slot, and Records author instance ids from **0** upward: all 80 refused
abilities resolved their second card from instance 0 (78 spell it on the battle clause itself, 2 through an invoked
child) -- the clause that resolves the troop which fights (`StoreTargets`, `CardModifier`, `MoveCardToZone`, or the
source itself). `ir_exec_combat._second_clause` read the field by
truthiness (`int(field or -1)`), so 0 became "no reference" and the ability was refused as "fight or block
without a second card" with its partner clause sitting in the same list. `ir_exec_targets.instance_ref` is now
the one reader (0 is a real instance; only `None`/`""` mean none), and the same bug is fixed where it was
silently wrong rather than refusing:

- `ir_exec_targets.referenced_instances` did not record a clause's resolved cards when its instance id was 0, so a
  later `SecondaryTargetTemplate` ("the remaining cards") or effect condition naming instance 0 found nothing there.
- `ir_exec_targets.follow_up_targets` turned a `SecondaryTargetTemplate` / `MatchSecondaryTargetTemplate`
  reference of 0 into "no reference", so the follow-up target resolved from no cards at all.
- `ir_exec._contingency_holds` treated `m_ContingentEffectInstanceId == 0` as "no contingency", so 56 covered
  abilities ran a contingent clause unconditionally (native skips it unless instance 0 applied).

What the two effects do, from the native leaves rather than from card text:

- **Fight** (`rules_port/battle_effects.battle_cards`): the referenced instance's *first* resolved card is the
  one that fights; each card the battle clause itself resolved is faced in turn (the native resolver runs a card
  effect once per resolved target). Damage is the `fight_cards` leaf both paths share, so the reciprocal hit and
  the `CardBattledEvent`s follow the template's `m_FightBack`. The interpreter now loops the clause's targets and
  uses the first attacker instead of refusing "more than one card on a side", which is what "this battles each
  opposing troop" needs.
- **Block** (`rules_port/combat_effects.block`): the clause's target is the attacker and the referenced
  instance's first card is the blocker, published through the same `player_spell_target` /
  `resolving_secondary_target_uid` aliases the live leaf reads, once per attacking troop.

Evidence: 170 `AbilityTemplate` records carry a battle or block clause; 80 of them were refused on this reason, and
72 of those now run (the other 8 have an unrelated refusal). The equivalence check answers the ability's chosen
targets and compares the interpreter with `resolve_port_ability` given the same target map: 122 comparisons over 61
abilities (the same board with the first and the last candidate of every choice) show no difference. Of the rest, 97
are one-shot spells no card template in the harness carries, so no board can hold them; 4 are triggered by events the
check's synthetic event does not satisfy (a champion's heal, an opposing cast, a deathcry -- the trigger gate refuses
them before the fight, so they were not exercised); 8 stay refused. The harness's own `CASES`/`choice_cases` cannot
express these either, because a triggered ability with a chosen target stays on the native path unless the chain item
already carries the player's answer (`ir_exec.engine_runs`). `tests/tests_rules_port_kernel.py` pins the instance-0
read and the first-card-plus-per-target behaviour.

## Granted powers from sockets, payments, remembered powers and random picks (implemented)

A `GrantAbilityEffectTemplate` can name no typed template at all and take its abilities from the battle instead:
the source's socketed gems (`m_AllSocketedPowersOfSource`), the target's (`m_AllSocketedPowersOfTarget`), the
master's (`m_AllSocketedPowersOfMyMaster`), the source's or the target's manual powers
(`m_AllPaymentPowersOfSource` / `m_AllPaymentPowersOfTarget`), what the resolving ability remembered
(`m_AllRememberedPowers`), or a random pick from the Inspire pool (`m_RandomInspirePower`) or the champion
charge-power pool (`m_RandomChampionChargePower`). The interpreter refused all eight flags as sources it did not
model, which kept 66 abilities ("this gets all socketed powers of this" and friends) on the native path. It now
runs them through the leaf the native resolver runs (`rules_port.effects.grant_ability`), which reads every one of
those sources from the battle this ability is resolving in, for the card it is applying to -- one implementation,
so the two paths cannot disagree about which powers are available.
`rules_port.granted_abilities.POWER_SOURCE_FLAGS` replaces the old `UNSUPPORTED_POWER_FLAGS`. A grant is still
refused when it names neither a typed template nor a source (`grant without a typed ability`): the leaf then falls
back to whatever the resolving ability happened to remember under its own guid, a source the effect alone does not
name.

Two defects had to be fixed before the leaf could be trusted from either path:

- **The empty guid is dashed.** Records render a null `m_GrantedAbilityTemplateId` as
  `00000000-0000-0000-0000-000000000000`, and the leaf's `== "0" * 36` test never matched it. `context.param`
  therefore read as a real typed template, `granted_values` was truthy, and the random branch was skipped
  entirely: the three `m_RandomInspirePower` / `m_RandomChampionChargePower` abilities granted *nothing* on the
  native path either (verified on the native resolver: 0 abilities granted, no cached pick). `is_zero_guid` in
  `granted_abilities` is now the one test, used by the leaf and by the interpreter's coverage, and the native path
  grants the picked power again. Nine more `"0" * 36` comparisons in `resources.py`, `visibility.py`,
  `pregame.py`, `choice_effects.py`, `context.py`, `static_rules.py` and `resolution.py` are latent bugs of the
  same kind; they are not part of this cluster and are not investigated.

- **The random pick belongs to the activation.** C# caches the pick on the ability instance
  (`ability_runtime_state[<instance>]["RandomPowerTemplate"]`): it survives a pause and continuation of that
  activation and is dropped when the activation finishes. The interpreter published no instance id, so every
  activation shared the `"0"` entry and a second activation reused the first one's power instead of drawing its
  own. `Scope.instance_id` carries the chain item's instance id; `_bridge_caster` publishes it as
  `resolving_ability_instance_id` and restores the caller's value afterwards; continuation frames and invoked
  children inherit it; `run_interpreted_trigger`, `resolve_port_trigger` and the inline trigger path in
  `rules_port/triggers.py` pass it; and `ir_exec._finish_activation` drops the entry when a run completes (a paused
  run keeps it until it resumes), which is what the native resolver does at `resolution.py:1402`.

Evidence: a parity driver over the 66 abilities that grant powers directly or through an invoked child runs each
one both ways on the same board, with `instance_id=1` and the same answers, and compares the cards (abilities and
`gems`), `remembered_powers`, `ability_runtime_state` and the client events: **94 comparisons pass, 12 never fire,
4 have no usable card template in the test database, 1 differs inside its invoked child and 2 differ by one
leftover empty `remembered_powers` key**. The 12 that never fire are trigger conditions the driver's synthetic event
does not satisfy (8 `CardDealtDamageEvent` that need champion-damage data, 2 `AsEntersPlayEvent` for "another
troop", 1 for "another Warlock", 1 for a Candlekin subtype) -- the trigger gate declines, so nothing is claimed.
The 1 that differs is `35939a98`, a choice-zone/transform divergence in its invoked child; `engine_runs` excludes
it at run time. Coverage moved 7,982 -> 8,044 covered and 1,014 -> 952 refused: 62 of the 66 grants are covered, 4
stay refused for other constructs (2 `trigger ConscriptEvent`, 2 a three-card target), and 22 of the covered ones
are skipped by `engine_runs` because they need a player-picked target. Three kernel tests pin the coverage, the
grant reaching the clause's own targets, and the per-activation pick.

## Targets

- `chosen target of more than one card` (146, plus 39 invoked) and multi-card targets with a count other than one
  (about 400 across the min and max counts). The interpreter picks one card per target index.
- `chosen follow-up target` (14): `follow_up_gaps` in `rules_port/ir_exec_targets.py`.
- `filter TACFilter` (8), `filter PlayersWhoControlMatchingFilter` (5), `filter EffectInputVariable` (5).
- `random target without a card filter` (2): the live pool drops every card for these, while C# takes all of them.
  The interpreter follows C#, so these stay native until the live behaviour is confirmed.

## Modifiers, costs and grants

- `WhileCardInPlay`: see the ruling above. The `AttributeModifier`, `IntAttrModifier`, `CardCostModifier`,
  `DamageMultiplierModifier`, `BlockImmunityModifier`, `BlockImmunityExceptionModifier`, `DamageImmunityModifier` and
  `TargetingImmunityModifier` clauses the scan projects are covered, and a triggered, player-activated or invoked
  clause is applied and recorded in the linked ledger; 24 abilities remain, all absent from the projection snapshot.
- `stat duration BeginningOfOwnersTurn` (23), `stat duration UntilEndOfDungeon` (2): stat modifiers accept only
  `Permanent` and `EndOfTurn` (`STAT_DURATIONS` in `ir_exec.py`).
- `cost duration EndOfTurn` (19).
- `stat derived from another card` (14), `stat replaces the existing value` (2), `stat property CurrentAttackValue`
  (2) and `CurrentDefenseValue` (2).
- `modifier SetHeroHealthModifier` (15), `LoseLifeModifier without a typed amount` (2).
- `grant duration BeginningOfOpponentsTurn` (2), `IntAttrModifier duration WhileCardOnTopOfDeck` (4). The power
  sources of a grant (sockets, payments, remembered powers, a random pick) now run -- see the granted-powers
  section above.

## Variables

- `variable CardIntegerVariable` (19), `card count of different races` (18), `CardSumAbilityVariable` (5),
  `SourcePlayerHealthVariable` (5), `SourcePlayerShardAbilityVariable` (2).
- `variable TriggerEventDamageProperty` (11), `TriggerSourcePropertyVariable` (6), `TriggerTargetPropertyVariable` (3):
  trigger-scope fields the interpreter does not carry yet.
- `variable target count` (16, with 12 for the min count and 10 invoked): target counts that vary with a variable.
- `random variable with a second value or a dynamic bound` (4, plus 3 invoked).

## Effects

- `effect RepeatingAbilityEffectTemplate` (23): running the repeat as a native leaf was tried and reverted. The two
  "Repeat ... choose one at random" abilities then differed from the live path in the equivalence harness, so the
  repeat stays refused until the nested random choice is shown to match.
- `effect ConversationAbilityEffectTemplate` (23): a conversation pauses the ability for a client transaction. The
  pause and resume live in the battle handler, not in the interpreter, so this needs a continuation the interpreter
  can persist and resume.
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

- The latest run reports 0 coverage errors. An earlier note recorded one Records ability with a null template GUID;
  it is not reported now, but its cause was not re-checked.

## Remaining work outside the interpreter

These gaps are not specific to the interpreter. They are tracked in `docs/RULES_PORT_PARITY.md` under "Remaining
work" and in `docs/PRIVATE_SERVER_FEATURES.md`: continuous `CardModifier` leaves that are not IntAttrs, full
replacement pause and resume, `UntilDamaged` removal, state-based death timing, and client acceptance for trigger
and zone events.

## How to close an entry

1. Find the refusal in the report (`tools/ir_coverage/ir_gap_report.py`) and the code that raises it.
2. Call the live leaf or evaluator the native path uses, with its inputs passed explicitly, as the existing
   `ir_exec_*` runners do. Prefer calling the native function over re-implementing it, so both paths agree by
   construction.
3. Update `coverage()` so the construct is accepted, then regenerate the report.
4. Run the IR interpreter test modules (`tests/tests_ir_*.py`, `tests/tests_ability_ir.py`, `tests/tests_conditions.py`,
   `tests/tests_targeting.py`) and add one focused test for the area. A refusal removed without a passing test is not
   closed.
