# Heartsworn (Hex TCG private server): design specification

Purpose: a specification from which the whole server can be rebuilt in a new project folder (`Hex2`). It describes
what the system must do, how its parts are divided, the wire formats the fixed 32-bit Mono client requires, the
rules engine and its phases, the ObjFmt object formatter, and the prompt (picker) contract. Where this document and
the code disagree, the code and the client disassembly are the reference, and this document must be corrected.

The one deliberate difference from the current server is in section 9: a choice (prompt) can be presented by either a
Unity picker or a web picker. Everything else is the same for both.

Sources this document draws on: `RULES.md` (gameplay contract), `HOWTO.md` (architecture), `docs/CLIENT_SERVER_PROTOCOL.md`,
`docs/UNITY.md`, `docs/IR_ENGINE_GOAL.md`, `docs/IR_INTERPRETER_GAPS.md`, `rules_port/README.md`, `abilities/ABILITIES.md`,
and the client disassembly in `hex-private/HexClient/`.

---

## 1. Scope and goals

Heartsworn is an authoritative game server for a digital trading card game (Hex: Shards of Fate). The fixed Unity client
(32-bit Mono) is a UI and local-state projection. The server owns the session, priority, card locations, card state,
resources, targets, combat, and results. The client sends intent; the server validates it, applies it, persists it, and
publishes events.

It must run:

- **Practice and PvE** against an AI opponent, including campaign zones (dungeons, areas, panoramas) and Frost Ring Arena.
- **PvP** between two human players.
- **Tournaments** (registration, matching, results, forfeit), which share the rules with PvP.
- **Supporting services**: profile, decks, store, packs and chests, mail, friends, chat, matchmaking, news, and a
  campaign editor.

Non-goals for the rebuild: changing the client (it cannot be changed; every fix is server-side), and reproducing
unimplemented Auction House behaviour beyond its API surface.

## 2. System layers

Build the server as these layers. Each layer may depend only on the layers below it.

| Layer | Responsibility | Current location |
|---|---|---|
| Transport | TCP framing, connection lifecycle, one worker per connection, ping handling | `hconnect_server.py`, `application/protocol_wire.py` |
| Protocol / wire codec | Packet headers, DataWrapper routing, ObjFmt encoding, binary event encoding | `encoder.py`, `objfmt_builder.py`, `game_engine.py`, `domain/` |
| Service dispatch | Request families (decks, store, mail, tournaments, ...) mapped to handlers | `services/__init__.py` (registry), `services/*.py` |
| Application | Typed commands and transactions; the boundary between wire and rules | `application/` |
| Rules engine | Phases, priority, chain, combat, costs, zones, ability interpreter | `rules_port/` |
| Persistence | One logical SQLite database; domain APIs over it | `db.py`, `static.py` (schema), `pvp_db.py`, `pve_db.py`, `profile_db.py`, `tournament_db.py`, `chat_db.py`, `replay_db.py` |
| Gamedata | Card, ability, target and condition metadata extracted from the client's Records | `gamedata/`, `AssetExtraction/`, `hex-private/Records/` |
| Modes | PvE, PvP, FRA, tournaments, campaign, AI | `gamemodes/`, `campaign/`, `ai.py`, `hexai/` |
| Prompts | Choices shown to a player (section 9) | `application/pending_choices.py`, `rules_port/resolution.py` |

Rules that govern the layers (from `HOWTO.md`):

- All schema DDL lives in one place (`static.ensure_schema`). No other module creates tables.
- The shared database connection and reusable SQL helpers live in one module. SQL is parameterized.
- Each request family is one module. New handlers belong in the registry, not in a second dispatch dictionary.
- Mode code selects setup, decks, opponent and rewards. It must not fork phase, zone, event or ability rules.
- A card's behaviour is data-driven from its metadata. Never branch on a card name.

## 3. Transport and packet framing

- The game protocol listens on TCP **9933**. The HTTP auth/proxy compatibility endpoint listens on **8081**.
- Each message is one packet:

```text
"HCPKT"                 5 bytes  identifier (ASCII)
rest_length             4 bytes  big-endian uint32: bytes after this field
header_length           4 bytes  big-endian uint32
headers                 header_length bytes: UTF-8 JSON object (route, target, instance, ...)
body_length             4 bytes  big-endian uint32
body                    body_length bytes (ObjFmt or binary payload; may be gzip-compressed)
```

- A reader must accumulate bytes until a full packet is present, then consume exactly that packet. A malformed or
  short packet waits for more bytes; it is not an error until the identifier is wrong.
- Keep **ping** packets off the worker queue: the reader answers them at once, so a long game action cannot make
  the client's heartbeat expire. Every other packet goes to a per-connection worker in arrival order, so game actions
  run one after another.
- Bodies may be gzip-compressed (level 1). Decompress before decoding.

## 4. Object formatter (ObjFmt)

ObjFmt is the Hex-specific typed-record encoding used for service responses (profile, store, mail, tournaments, load
balancer) and for some session data. The client reads it by type name and field name, so the writer must emit exactly
what the client's concrete type expects.

### 4.1 Layout

An ObjFmt document is a set of records, followed by a **type table** and a **size table**:

```text
<root header>     name ; index ; type-index ; propCount ; ...
<field>*          name ; index ; type-index ; flags ; value
...
<type table>      type names joined by ';', then newline
<size table>      byte size of the root and each field, joined by ';'
```

- Each field has: its name, its index (its position among the sizes), the index of its type in the **type table**,
  a reserved `0`, and the value. A field's size is recorded in the size table, so a reader can skip fields it does
  not know.
- The type table grows as types are first used. Writing a field records its type's index (`System.Int32`,
  `System.String`, a Hex enum type, and so on).
- The root header's property count is written after the fields are known; write a placeholder first and patch it.
- The size table begins with the root's size, then each field's size, separated by `;`.

### 4.2 Value encodings

| Client type | Encoding |
|---|---|
| `int`, `uint`, `ulong` (little-endian) | Hexadecimal digits of the little-endian bytes, followed by `;` |
| `bool` | Raw `0` or `1`, no hex |
| `string` | Decimal byte length, `;`, then the raw UTF-8 bytes (no trailing separator) |
| `DateTime` | `MM/dd/yyyy HH:mm:ss` |
| byte array | Length, `;`, raw bytes |
| list | Element count, then elements; element type and size indexes must match the concrete client type |
| enum | The exact client enum type name and its numeric value |
| UID / ResourceId / SessionCardId | Must carry a UID type the client recognizes (an unknown type makes the client throw) |

Rules that protect the client:

- The client is 32-bit. Default `int` is 32-bit. Do not send a `Dictionary<ulong, T>` unless the client is known to load it.
- `[DataMember(Order=N)]` fields are emitted in the client's effective order; a higher order is later.
- Use the client's loaded namespace and its concrete enum representation.
- An optional protocol identifier that is absent is the zero GUID, not an empty string.

### 4.3 Writing

Provide one builder with typed field methods (`field_int`, `field_str`, `field_enum`, `field_list`, ...) that tracks
sizes and types automatically, and a `finish()` that writes the type table and the size table. Encoders for each
service response are thin functions over the builder. Test each encoder against a captured client response
(the golden protocol checks).

## 5. Session events (game stream)

During a battle the server sends a `NetworkPacketSessionEventArgs` (wire class 255) carrying an ordered list of session
events. Each event has a class ID (`game_engine` event types, for example card moved, card updated, phase update,
green light, option list, chain events) and a binary payload.

Event order and visibility rules (from `docs/CLIENT_SERVER_PROTOCOL.md`):

- Events in one packet are applied in order. Some open client UI states asynchronously, so a later event can arrive
  before the UI is ready; the server must not rely on the client finishing an animation.
- A card's zone change is published as the destination `CardUpdated`. When the collection or controller changes, the
  client derives the UI move from it; do not also send a separate move for the same transition.
- A hidden zone is filtered per recipient. The opponent never receives card identity, hand contents, or private deck
  information unless a rule reveals it. Reconnect rebuilds the same filtered view from the database.
- Every event that changes a resource, threshold, pool or player value is emitted from the **post-mutation** state, so the
  HUD does not revert to defaults.
- Each packet announces each phase once.

## 6. Transactions, priority and the rules engine

### 6.1 The transaction loop

- The client sends **one transaction at a time** and waits for a reply. Every handled transaction must reply: a
  synchronization packet, or an empty acknowledgement. An unanswered transaction freezes the game silently.
- Fire-and-forget requests (mulligan keep or redraw, `SetTurnPhases`) get **no** reply. Sending an unexpected reply to
  one of these gives the client a "command handler not found" error.
- `RequestPrioritySync` always gets an answer. While a choice is open, re-send the open choice, not just the green light.
- The server validates every transaction against authoritative state (phase, priority, ownership, zone, cost, target,
  transaction identity) before it mutates anything. A rejected transaction is acknowledged and changes nothing.

### 6.2 Phases

The phase values match the client's `ETurnPhases`. Keep these numbers, because they go on the wire.

| Value | Phase | Rule |
|---:|---|---|
| 0 | Unknown | Initial internal state. |
| 1 | NotPlaying | Session waiting state. |
| 2 | PreGame | Encounter setup and pre-game triggers; no player input. |
| 3 | PickGoesFirst | The coin-toss winner chooses Play or Draw; the server waits. |
| 4 | Mulligan | Each player keeps or redraws. |
| 5 | StartGame | Setup finishes and the first turn is prepared. |
| 6 | StartTurn | Active player and turn number set; per-turn flags reset. |
| 7 | Ready | Active cards are readied as allowed. |
| 8 | Prep | Start-of-turn triggers, resource refresh, healing. |
| 9 | Draw | The active player draws. |
| 10 | FirstMainPhase | Play cards and abilities; play one resource. |
| 11 | DeclareCombatPriorityWindow | Pre-combat priority for the attacker. |
| 12 | DeclareAttack | The active player declares attackers. |
| 13 | DeclareAttackPriorityWindow | Priority after attackers. |
| 14 | DeclareDefense | The defender declares blockers. |
| 15 | DeclareDefensePriorityWindow | Priority after blockers. |
| 16 | AssignFirstStrikeDamage | First-strike damage. |
| 17 | FirstStrikePriorityWindow | Priority after first-strike damage. |
| 18 | AssignDamage | Normal combat damage. |
| 19 | SecondMainPhase | Post-combat main phase. |
| 20 | EndPhase | End-of-turn triggers and effects. |
| 21 | Discard | The hand is reduced to its limit. |
| 22 | EndTurn | The turn ends; control passes. |
| 23 | Checksum | Internal consistency; not used in normal play. |
| 24 | EndGame | Terminal state and result publication. |

Normal turn:

```text
StartTurn -> Ready -> Prep -> Draw -> FirstMainPhase
  -> [DeclareCombatPriorityWindow .. AssignDamage, when combat is legal]
  -> SecondMainPhase -> EndPhase -> Discard -> EndTurn
```

The combat exit is a fixed point: the `CombatEndedEvent` fires when `AssignDamage` exits (its only permitted next phase
is `SecondMainPhase`), and when `FirstStrikePriorityWindow` exits with no normal-phase combat damage left.

Phase persistence: the phase list is stored in the session row, so a reconnect resumes the authoritative state. A phase
with no relevant stop may be pushed and auto-passed. `PickGoesFirst` and `Mulligan` always wait for their transaction.

Each phase is a state object with `on_entry`, `on_exit`, and a `next_phase` rule. A scheduler drives the stack of
pending actions until a stop (a player input, or the end of the phase). Keep phase transitions in one place.

### 6.3 Priority and the chain

- Priority is granted with `GreenLight`. A player may act only when the server has granted it to them.
- **Speed:** a quick card may be played when its controller has priority. A non-quick card (troops, resources) needs the
  active player in a main phase with an empty chain.
- **The chain** holds top-level items (spells, activated abilities, triggers) in order:
  1. Push one item and publish its chain event.
  2. Grant priority; when both sides pass, resolve the top item.
  3. Publish the resolution and removal events, then grant priority for the next item.
  4. When the chain is empty, apply state-based effects and continue the phase.
- An item that ignores the chain (`m_IgnoresChain`, or a trigger with no explicit target) still takes its position and
  resolves in order, but gets no priority window; the server publishes `AbilityPushedOnChain` with `ignores_chain=True`.
- Within one ability, effect groups run in the authored `m_AbilityEffectList` order. Those effects are not separate
  priority items.
- Every top-level chain item has one instance ID, used in the persisted stack item and in each chain event for it.

### 6.4 Setup, first turn and mulligan

- The server creates the player and opponent session identities, champion session-card IDs, decks, card instances and
  ordered zones before publishing battle state.
- `PreGame -> PickGoesFirst -> Mulligan` is the client-compatible setup. Tutorials choose Play automatically; other
  sessions wait for `ChoosePlayTransaction` or `ChooseDrawTransaction`.
- The opening hand is seven cards. A redraw reduces the hand by one (7, 6, ... 0); zero forces a keep. A player is ready
  only when both have kept.
- Maximum hand size: ten in PvE, Arena and campaign; seven in PvP. A champion with `UnlimitedHandSize` removes the limit.

### 6.5 Resources, thresholds and costs

- One resource may be played per turn in one's own main phase; a resource play opens no response window.
- Current resources refresh to total resources at Prep. Costs are paid from the authoritative current pool; thresholds are
  checked against the player's threshold flags.
- Threshold flags: Blood 4, Ruby 8, Sapphire 16, Wild 32, Diamond 64. Stored arrays use colour indexes and must be
  converted before encoding.
- A free play requires `OwnerCanPlayForFree` on the card or `CanPlayCardsForFree` on the controller's champion; while it
  holds, the effective cost is zero. A `Mobilize` card may tap ready troops to reduce its cost by two each.
- A dual-colour land asks its controller which colour it represents when played.

### 6.6 Cards and zones

- Normalized locations: `deck`, `hand`, `warzone`, `PlayedResources`, `void`, `discard` (and `CastSpells`, `Choosing`,
  `Underground` where the rules use them). The database row for a card is authoritative: its location, owner, ordered
  position and mutable state.
- A card's playability and its legal target sets come from its metadata (section 7), not from its name.
- Permanents enter the warzone; spells resolve through the cast path. At `FinishResolvingCard`, a card still on
  `CastSpells` enters the warzone if it is a troop, artifact or constant; otherwise it goes to discard.
- A champion's ability and a card's state are persisted; reconnect rebuilds the filtered view.

### 6.7 Combat

- A troop may attack when it is in the warzone, ready, legal for the active player, and has begun a turn on that side
  (unless it has Speed). An attacker is normally exhausted on attack.
- Any legal untapped defender may block; a troop blocks at most one attacker per combat, and several blockers may block
  one attacker. The attacker orders damage among its blockers.
- First-strike damage resolves in phases 16 and 17; normal damage in 18. Crush and Trample carry excess damage through a
  blocker. Lifelink and SpiritDrain grant life for damage dealt.
- Damage order of checks: immunity; outgoing multipliers; received-damage modifiers and `PreventMy*` attributes; shields;
  armour (`Armor` minus `ArmorUsed`, reset at each Ready step); then the champion's noncombat multiplier and reductions.
- Combat damage resolves once per damage phase. Resuming that phase after a trigger opens a response window must continue
  the phase without applying the damage again.
- Lethal troops move to the graveyard and fire their Deathcry through the ability path. State-based deaths occur after
  resolution, when the chain is empty.
- A champion at zero health loses. Withdrawal is a server loss and publishes the normal game-end events.

## 7. Gamedata and the ability interpreter

### 7.1 Gamedata

The client's Records (card templates, ability templates, effects, conditions, target templates, deck and encounter
data) are extracted once into `hex-private/Records/`. The seed pipeline is:

```text
client Records -> AssetExtraction (extractors) -> seed materialization -> SQLite metadata tables
               -> generic ability, targeting, playability and event code
```

The metadata tables are a projection of the Records. An existing database is never re-seeded wholesale; a fingerprint of
the snapshot upserts only the seed-owned columns. Before changing engine code for a card whose metadata looks right,
check the projection (the seed comparison reports missing, extra and changed rows).

### 7.2 The interpreter

Every ability is a typed graph (`AbilityTemplate`) with: triggers, conditions, costs, target templates, and an ordered list
of effect clauses. Each effect clause names an effect template, a target template index and a condition. The interpreter:

1. Checks the trigger and the ability condition against the event that raised it.
2. Binds the ability's variables (constants, expressions, counts) from the source card and the battle.
3. Pays costs and settles options before the effects run.
4. For each clause in order: checks its condition; resolves its targets (automatic from the target template, or chosen
   by the player); runs the effect through a leaf.
5. Records durations: a clause's effect either applies to the card permanently, until end of turn, until a boundary
   (the owner's next turn, the opponent's next turn, a zone change, damage), or while a source stays in play. Lifetimes
   are kept in a ledger that the turn boundaries and event sweeps expire.

A **leaf** is the one place a state change happens (move a card, deal damage, change a counter, add a stat, store a
list). Leaves are shared by the interpreter and by any native path, so there is one implementation of each rule.

**Static projection:** a continuous effect with a while-in-play duration is not run once. It is projected on every read of
a card's stats, cost, attributes or targetability, from the source's current state (`rules_port/static_rules.py`).

**Pauses:** a player's choice pauses the interpreter. The pause carries the interpreter's continuation (the clause to
resume, its variables, its chosen targets). The answer resumes the continuation exactly once. A nested invoke keeps its
invoking frames on the continuation, so resuming the child carries on from the invoking clause.

**Coverage:** an ability is covered when every construct in it has a runner. The coverage check is run over every Records
ability, and the report of refused abilities is the list of constructs still to build.

### 7.3 What runs where

- The interpreter runs every ability (triggered, activated, spell, static, setup), except the card-play template, which
  is owned by the native card-play machinery (`BuiltInPlayCard`, which queues the card on the chain, and
  `FinishResolvingCard`).
- The client's built-in templates (Dazed, Vulnerable, Stealth, Conscript, Finish-into-play) are Records entries the
  interpreter runs through their leaves, with the two Stealth keywords and Dazed/Vulnerable projected as statics.
- One ability whose invoked choice is picked from the choice zone is still resolved natively, because the interpreter
  has no picker for the choice zone. This is the only remaining case.

## 8. Modes, AI and campaigns

- **PvE/Practice/Arena/FRA/tournament** share the session, zone, event and ability rules with PvP. Mode code chooses setup,
  decks, opponent identity and rewards.
- **AI** is server-side. It takes legal actions and publishes the same state and events as a player, and it respects
  waits that need a human transaction. Attack selection scores blocker assignments for the attacking group; a no-block
  race is chosen only when its block-aware value is positive.
- **Campaign** zones are authored as rules files (`campaign/campaign_rules/*.json`) and driven by a runtime
  (`campaign/campaign_runtime/`). Zones have counters (dungeon-scoped), flags, quests, and node progress. A dungeon's
  until-end-of-dungeon effects are carried in the campaign progress and cleared when the champion leaves for an area.
- **Tournaments** handle registration, matching, results, forfeit and participant-scoped history as separate domain
  operations.

## 9. Prompts: Unity pickers and web pickers

This is the one place the two front ends differ. The engine asks for a choice in one way; the front end presents it.

### 9.1 The prompt contract (shared)

A prompt is a request made by the engine and answered by the player:

```text
Prompt {
  id                   // the pending-choice id, stored in the checkpoint
  player               // who chooses (never the opponent)
  kind                 // target | choose-card | reveal-pick | deck-search | matching-target | choice-zone | double-choice | trigger | discard | conversation
  source               // the card that asks
  candidates[]         // legal card UIDs, computed by the server
  minimum, maximum     // how many the player must pick
  optional             // whether the player may pick none
  continuation         // opaque server state to resume (persisted)
}
Answer {
  prompt_id
  picks[]              // card UIDs; each must be in candidates; count within [minimum, maximum]
}
```

Rules that every picker follows:

- Candidates come from the server, and an answer is checked against the same list. An answer outside it is rejected with
  a reply, never silently.
- A prompt opens only when there is at least one legal candidate. An empty picker strands the chain.
- The open prompt is stored in the checkpoint with its candidates, so it can be sent again after a reconnect or a sync.
- Passing while a prompt is open is a client action that closes the picker; the server treats the choice as still required
  and does not resolve past it.
- A prompt is answered once. The answer resumes the same continuation, then the engine emits the next priority event.
- Hidden candidates stay hidden: the opponent's cards are shown to the chooser only, and only where the rule reveals them.

### 9.2 Unity picker (current)

- The server sends a `PlayerOptionList` with one `OptionInstance` (target UIDs, `target_instances`, min and max counts), a
  class-39 `TriggeredAbilityActivationDataRequired` event, and a `GreenLight` to the chooser.
- The client answers with `SetAbilityActivationDataTransaction` carrying the chosen UIDs. The server answers with a sync
  packet or an empty acknowledgement.
- A discard choice uses class 23 (`AbilityActivationDataRequired`).
- A deck search uses a temporary `Choosing` projection of the candidates, returned to the deck afterwards.
- A revealed-card pick uses the revealed-card picker.

### 9.3 Web picker (to build)

The web client (`web_client/`) receives the same prompt object as JSON over its own channel and answers with the same
`Answer` object. It must:

- keep the prompt open until it is answered (do not close it on a state update);
- show only the server's candidates, and send back only those;
- enforce the bounds before sending;
- reconnect with the prompt re-sent from the checkpoint.

The web client has no animation queue, so the events of one packet apply in order; the choice still stays open until answered.

### 9.4 Implementing the choice in Hex2

Define one `PromptPort` interface in the engine with two methods: `present(prompt)` and `await(prompt_id) -> Answer`.
Implement `UnityPicker` (the `PlayerOptionList` path above) and `WebPicker` (the JSON path) behind it. The engine never
knows which front end is in use. Choose the picker per player connection.

## 10. Persistence and reconnect

- One logical database with domain APIs over it: profile, decks and collection, PvE campaigns and FRA, PvP sessions and
  cards, tournaments, chat, replays. Schema changes live in one schema module with an idempotent one-off migration.
- Durable session state: the phase list, the stack and chain, the card rows (`game_cards`), the checkpoint of pending
  choices (with candidates and continuations), and the campaign progress.
- A reconnect rebuilds the filtered per-player view from these rows. It never trusts client state.
- Sessions are cleaned up after a retention window, except tournament sessions and sessions awaiting replay indexing.

## 11. Verification

- **Focused tests** for each rule and each construct, with valid and invalid inputs. Keep the number of tests to the key
  areas: one test per mechanism, not per card.
- **Equivalence**: where an ability is projected by the interpreter, compare its values with the native rule (the Dazed,
  Vulnerable and Stealth tests are the pattern).
- **Golden protocol checks**: captured client-facing responses that encoders must reproduce.
- **Client check**: the client log (`output_log.txt`) must show no `Command handler not found`, UID, or state-machine
  errors for client-tested changes. Search it first when something is stuck.
- **Coverage report**: the list of Records abilities the interpreter refuses, by construct. The target is zero refused
  reachable abilities.
- **Acceptance criteria** for a rule: stated in the rules document; owner and state mutation clear; valid and invalid
  inputs covered; event types, recipients and order tested; reconnect tested when state is durable; PvE and PvP both
  considered.

## 12. Known gaps and open decisions

These are open in the current server. Hex2 must decide them explicitly rather than inherit them silently.

- **Live verification:** the chain-interrupt flow, the double-choice flow, the Dazed/Vulnerable/Stealth defaults, and the
  card-play path through the interpreter are checked at interpreter level only. They need a PvE game against the client.
- **Chain-target prompt:** a counter-spell interrupt chooses a spell on the chain. The prompt for choosing a chain item
  must follow the client's chain flow; it is not yet a separate picker.
- **Choice-zone picker:** an invoked choice picked from the choice zone has no interpreter picker, so it stays native.
- **Stealth removal:** the start-of-turn removal of a stealth counter is an engine step (`stealth.advance`), not a Records
  ability.
- **Legacy services:** `services/legacy_*.py` holds fallback handlers for 44 request types, 19 of them shadowed by the
  registry. A registry handler that raises falls through to the legacy copy. Retiring it requires migrating the 25
  legacy-only types first; `legacy_loadbalancer.py` also holds live battle setup.
- **Legacy `abilities/` package:** still the shared leaf and context library that `rules_port` imports. It is not dead
  code; it should be reorganized into the rules engine in Hex2 rather than carried over as a separate package.
- **Records error:** none open.

## 13. Suggested build order for Hex2

1. Transport and packet codec (section 3), with a round-trip test against captured packets.
2. ObjFmt builder and the first encoders (profile, decks), checked against captured responses (section 4).
3. The database schema and seed pipeline (section 7.1), from the same Records snapshot.
4. The phase machine, priority and chain (section 6), with no abilities: a turn that passes, draws and ends.
5. Cards and zones, costs and resources (section 6.5 and 6.6).
6. The ability interpreter with the leaves (section 7), starting with the simplest effect and growing to coverage.
7. Combat (section 6.7).
8. Prompts through the `PromptPort`, Unity first, then the web picker (section 9).
9. Setup, mulligan and PvE against the AI (section 6.4 and 8).
10. PvP, tournaments, campaigns and the remaining services.

Each step ends with its focused tests and a live check in the client.
