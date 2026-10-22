# Unity client integration

How the server drives the Unity battle UI: choice popups, single targets,
multi-target pickers, priority, and packet order. Read this before changing
how the server sends battle events, and before building the web client's
equivalent (`web_client/`).

Sources: the decompiled client in `hex-private/HexClient/`, the client log at
`/mnt/d/SteamLibrary/steamapps/common/HEX SHARDS OF FATE/Hex_Data/output_log.txt`,
and the server log at `/tmp/hconnect_log.txt`. Where a rule was learned from a
bug rather than the source, the bug is named so it can be checked again.

## 1. The transaction loop

- The client sends **one transaction at a time** and waits for a reply
  (`SessionClient.cs:45`). A handled transaction must produce a reply: a
  `3055` sync packet, or an empty ack (`_push_transaction_ack`).
- A transaction that gets no reply stops every later transaction. The client
  does not report this; the game just looks frozen.
- Fire-and-forget requests (mulligan keep/redraw, `SetTurnPhases`) get no
  reply, and the server must not send one.
- `RequestPrioritySyncTransaction` is the client asking "what should I be
  doing?". Always answer it, even when there is nothing new to send.

## 2. Packets and event order

- A battle update is one `3055` packet holding an ordered list of events
  (`game.events`, built by `application/combat.py` `_send_battle_events`).
- The client applies a packet's events in order, but some of them open UI
  states asynchronously (through `AnimationManager`), so a later event can
  close a state an earlier event opened.
- **`TurnPhaseUpdated` with another player's priority collapses every non-root
  UI state** (`UIBattle.cs` `OnTurnPhaseUpdated`, around lines 4609-4633). A
  picker, target or choice that is open when this arrives is closed at once,
  and the client logs "Priority passed while multiple states are on the
  state-stack".
- So: **never send a choice in the same packet as a phase update that the
  client applies after it.** Send the phase update first, then the choice in
  its own packet (`_send_battle_events` now splits a picker into a second
  packet when phase events are present).
- Events that belong together: `PlayerOptionList` (the options and their
  targets), `TriggeredAbilityActivationDataRequired` (class 39, which opens the
  picker), and `GreenLight` to the player who must answer.

## 3. Choices and targets

Three UI shapes, all server-initiated:

| UI | Server events | Client answer |
|---|---|---|
| Trigger target picker ("At the start of your turn, target champion draws a card") | `PlayerOptionList` with one `OptionInstance` (`target_ids`, `target_instances`, min/max counts), class 39, `GreenLight` to the chooser | `SetAbilityActivationDataTransaction` with the chosen UID |
| Discard choice (class 23, `AbilityActivationDataRequired`) | `PlayerOptionList` + class 23 + `GreenLight` | `SetAbilityActivationDataTransaction` with the chosen hand card |
| Multi-target | one `OptionInstance` carrying all target templates (`target_index` per template) | one answer per target index |

Rules for each:

- **Candidates are UIDs.** Each `TargetInstance.targets` must list the legal
  candidate `SessionCardId`s. An empty list gives the client a picker with
  nothing to pick.
- **Pick from the candidates the server computed**, and check the answer
  against the same list. Reject anything else with a reply, never silently.
- **Bounds**: `min_target_counts` / `max_target_counts` must match the
  authored target. A discard that must take every match takes no choice at
  all (see section 5).
- **No legal target, no prompt.** Open a picker only when there is a legal
  candidate. An empty picker cannot be answered and strands the chain.
- **Pending state is server state.** The open choice is stored in the
  checkpoint as `pending_trigger` (or `pending_choice`,
  `pending_discard_ability`), including the candidate UIDs, so it can be sent
  again (`_resend_pending_trigger`, `application/pending_choices.py`).
- **Sync while a choice is open** (`RequestPrioritySync`): re-send the open
  choice. Do not just re-send the green light (`application/chain.py`
  `_push_rules_port_chain_priority_sync`, `application/transactions.py`).
- **Passing while a choice is open** is a client action that closes the
  picker. The server must treat the choice as still required and not resolve
  past it.

## 4. Holding the turn

A choice is a pause, not a step. While it is open:

- the native scheduler must not advance (`rules_port/session.py`
  `drive_until_input` checks `trigger_prompt_open`),
- the turn driver must not send another phase update
  (`application/turn_flow.py` `_advance_rules_port_to_priority`),
- and it resumes from the same point once the answer is accepted
  (`trigger_prompt_open` is cleared in `application/pending_choices.py` and
  `application/transactions.py`).

This is the continuation: without it, the AI's end-of-turn handoff ran past an
open picker and closed it.

## 5. Choices that are not choices

- A discard that takes **every** matching card (Dishonorable Death, "discard
  all cards with the same cost as the voided troop") has no choice. The server
  discards all matching hand cards itself (`application/combat.py`
  `_discard_same_cost`), reading the voided troop's cost from its own card.
  Each discard goes through the normal discard path, so `CardDiscardedEvent`
  fires for each card.
- Choose by the authored effect, not by the card name: check the ability's
  record (`DEFAULT_RECORD_STORE`) and its authored flags.

## 6. Start of game and start of turn

- The order is: setup packet, mulligan, `GameStarted` triggers (these grant
  abilities such as scene mods), then the first turn's `TurnStartedEvent`
  (start-of-turn triggers), then the first priority window.
- A trigger that depends on a granted ability fires only after the grant
  exists, so dispatch the first turn's start after the `GameStarted` chain has
  drained (`application/transactions.py`, before the StartGame packet).
- Start-of-turn triggers fire for whichever player starts, not only the human.

## 7. Scene mods and who controls them

- An encounter's mod cards are played by the AI before the start
  (`Session.RunScenarioSetup`, `Game/Shared/Session.cs` around line 5080). Their
  "opposing champion" effects reach the human's champion, and "each champion"
  effects reach both.
- The scene data (`campaign/campaign_data/encounter_scenes.json`) records
  `target: "AIPlayer"` only for cards the AI copies (25 entries). Untargeted
  mods are scene-wide and their effect comes from the card text.

## 8. Resources and charge

- A resource's charge comes from its authored `chargepoints` effects
  (`rules_port/resources.py` `resource_charge_amount`). Input variables are
  already resolved in the authored parameters. A resource with no such effect
  (Shards of Fate and similar) grants none.

## 9. Diagnosing a stuck client

1. Find the last server transaction and the last packet sent
   (`/tmp/hconnect_log.txt`: `PlayerTransaction`, `DEBUG-PACKET`).
2. Find the same moment in the client log, and read the lines without a
   timestamp too: `SUBMITTING TRANSACTION n` lines have none.
3. Check whether the client sent a transaction after the choice. None means
   the client never got a reply, or closed the choice (sections 2 and 3).
4. Check what state the client was in: `Pushing UI state` / `Popping UI state`
   lines show the picker opening and closing.

Debug lines added while diagnosing (`[DEBUG-PICKER]`, `[DEBUG-PACKET]`,
`[DEBUG-TX]`, `[DEBUG-ARMOR]`) are temporary. Remove them once a fix is
confirmed.

## 10. For the web client

The web client (`web_client/`) reads the same server events. It should not copy
the Unity client's timing:

- Treat a choice as a separate request from state updates: the server sends
  the request after the state it depends on, and the web UI keeps the request
  open until it is answered, rather than closing it on any phase update.
- Keep the answer contract: candidates from the server, the same bounds, and a
  reply to every transaction.
- The web client has no animation queue, so a packet's events are applied in
  order. That makes the Unity-specific collapse (section 2) a Unity detail, but
  the choice still has to stay open until answered.
