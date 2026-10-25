# HEX TCG — Encounter Reference

Launch encounters from the debug console:
```
camp.encounter <scene_name>     e.g. camp.encounter AZ0_Necrotic
camp.encounter <guid>           e.g. camp.encounter 7b9a5a0d-...
```

Or via chat:
```
!encounter <guid>
```

## AZ0 Training Encounters (Starter Panorama)

| Race | Opponent | Scene Name | Encounter GUID |
|------|----------|------------|---------------|
| Human | Gareth Kay | `AZ0_Human` | `5227b1b0-193a-45e8-a1a3-8215f20ac95b` |
| Elf | Nerissa | `AZ0_Elf` | `e5cd349e-a77d-4175-8cfc-3565395c2eb4` |
| Coyotle | Whispering Breeze | `AZ0_Coyotle` | `064d32cd-9171-4052-82a2-869eb0017b2a` |
| Orc | Moqui | `AZ0_Orc` | `84cdd061-93ec-4126-a2f6-530ac5217c96` |
| Dwarf | Gwendower | `AZ0_Dwarf` | `de641801-55a2-4df8-b298-d2e91c21718d` |
| Shin'Hare | Sora | `AZ0_Shinhare` | `510ed850-5a3e-437b-875e-7834cacd1865` |
| Vennen | Zilth | `AZ0_Vennen` | `f56ba80f-af31-4d75-a4bc-640f899ddd32` |
| Necrotic | Iddi | `AZ0_Necrotic` | `7b9a5a0d-727f-4df8-923f-1e5a5b24c522` |

## Castle Crayburn Dungeon Encounters

| Node | Name | Encounter GUID | Background |
|------|------|---------------|------------|
| WatchTower | Outer Watchtower | `1e65d03d-f3d8-41e9-b3a1-3600e1756378` | Crayburn Castle |
| Drawbridge | The Drawbridge | `cae9b735-ca90-400f-81bf-a0a763fa3dc3` | Crayburn Castle |
| CastleGate | Castle Gatehouse | `f3c0ac5b-ff09-488c-ad63-f11ff15acdcd` | Crayburn Castle |
| InnerBailey | Inner Bailey | `df073679-4fd2-4434-8aff-6c044d759f91` | Crayburn Castle |
| TowerGate | Tower Gatehouse | `2ba61b7b-6864-4582-a634-f9124fb2fdee` | Crayburn Castle |
| PenworthTower | Tower of Penworth | `5f222319-7b4e-4ba4-b0dc-f9678c000d8b` | Crayburn Castle |

## Encounter Rewards — notes (2026-08-02)

**Encounters do NOT specify rewards in gamedata.** `EncounterScene` records have
no reward field. Rewards live in a separate `RewardTemplate` section (only 5
records, all "Krakens Lair"/placeholder) and are delivered via scripted quest
logic, not encounter data.

**Reward delivery is server-push, not client-request.** The client's
`ClientCampaignManager.CacheRewards` is fed ONLY by server-pushed notifications:
`gameendnotify.Applied`, `InputResponse.Applied`, and `CampUpdateNotify.Applied`.
The server must determine the outcome and attach `EscrowAction`s (`GOLD`, `XP`,
`LEVEL`, `CARD`, `BOACARD`, ...) to `Applied.Completed` BEFORE pushing
`gameendnotify`. The client's loot window
(`UICampaignZoneVMBase.HandlePendingReward`) just renders them. So the server
must know the outcome (e.g. princess human vs frog form) at battle end, prior to
the narrative conversation.

**"Kiss of the Princess" / "Victoria's Secret" quest chain (AZ2):**
- "Princess Victoria" (`c0671e20`) is a ChampionTemplate and the AI champion in
  **Peterson Woods** (AZ2 Node 14A/14B):
  - `5deedd32` = Princess Victoria (vs. Ardent), AI deck `d69fabca`
  - `81d153e6` = Princess Victoria (vs. Underworld), AI deck `8ca8ac9d`
  - Both decks use DeckTemplate `2d4037f7` ("AZ2 - 14 - Peterson Woods"),
    which contains **Kiss of the Princess** (`c7bd54c6`) and the other 3
    variants (`7f0eb2dd`, `70c8dda9`, `73244714`) — +2/+2 vs +3/+3 buffs,
    Lifedrain, Prince synergy.
- "Victoria's Secret" quest (`q_uw_victorias_secret`, QuestTemplate `0fc14070`):
  defeat Princess Victoria → return her locket to Ada the Apparitionist
  (Naagaan, Node 27). No reward field in the quest record.

**"Daphne in Distress" (AZ1) — the princess-saving chain:**
- Encounter "The Sea Witch Unfettered" (`b9bc1f22`, AZ1 Node 06) has mod
  **"Daphne in Distress"** — "Save the transmogrified princess from the evil
  Sea Witch" — granting the player **Princess Savior #1** (`d715ce7b`,
  "Summon Princess Daphne") and **Princess Savior #2** (`5134fdf5`,
  "transform each Princess Daphne into Enchanted Frogs").
- **Princess Daphne** (`686ab5d7`, troop, can't attack/block) has a hidden
  one-shot ability: at end of turn while you control it, trigger conversation
  `88e88d7f` = "AZ1 - Node 6 - Hag - Princess Revert" (hag admits the
  transformation spell; you take Daphne).
- The reward for the full chain is likely **Princess Victoria / Kiss of the
  Princess cards**, granted only when the encounter is completed with the
  princess in human form (not as a frog / Enchanted Frog). The human-vs-frog
  outcome split is NOT encoded in encounter/quest data — it's scripted quest
  logic. **TODO: implement server-side outcome tracking + EscrowAction rewards
  in `gameendnotify`.**

  Each AZ1 Tamed encounter remains retryable after a win without a capture;
  capturing the objective troop awards that captured card once and advances
  the Tamed quest.

## Brutecrown Bluff — Castle Ruins (NodeJ) special encounter

NodeJ of Brutecrown Bluff is one battle per route, not one battle per stage. The
route is picked earlier in the zone; each route's AI champion chain runs
*inside* that one battle. Each step is a champion's **would-lose replacement**:
instead of losing, the champion sets its health, starts a conversation
mid-battle, and transforms into the next form.

**Route selection (flags).** The conversation that ends each route's first
visit sets the route flag:

| Route | Set by (node, conversation) | Flag |
|-------|------------------------------|------|
| Vargar | NodeE, *Vargar's Shed - First conversation* (and Repeating) | `brutecrown_chose_vargar` |
| Burgg | NodeF, *Burgg's Shanty - First conversation* (and Repeating) | `brutecrown_chose_burgg` |
| Zagg | NodeG, *Zagg's Tent - First conversation* (and Repeating) | `brutecrown_chose_zagg` |

The three flags are mutually exclusive: one route per run.

**NodeJ entries (offered from the node's tables).**

| Order | Entry | Conversation / battle | Gate |
|-------|-------|-----------------------|------|
| 1 | Castle Ruins entry | `d0a2b255` *Brutecrown Bluff - Castle Ruins* (King Kazraga) | faction Ardent |
| 2 | Intro (Vargar) | `bfb0ff63`, then battle **J-1 (Helping Vargar)** `0aa33344` | `flag(brutecrown_chose_vargar)` |
| 2 | Intro (Burgg) | `e8907e92`, then battle **J-2 (Helping Burgg)** `c755cec9` | `flag(brutecrown_chose_burgg)` |
| 2 | Intro (Zagg) | `79ae6d73`, then battle **J-3 (Helping Zagg)** `aa88bcc3` | `flag(brutecrown_chose_zagg)` |
| 3 | Success (per route) | `4eacf2ae` Burgg / `988048ed` Vargar / `efedb76c` Zagg; plus the *Success 2* variants for Ardent and Underworld | `outcome:success` and the route flag |
| 3 | Fail | `63f5099c` *Castle Ruins - Fail* | `outcome:fail` |

The Intro for a route is the pre-battle conversation of its own J battle. The
J scenes carry the route flag as a scene tag, so each route's J battle is
reachable only on its own route.

**Route chains (the would-lose replacements).** The "Trigger encounter
conversation" in each row is a `ConversationAbilityEffect` that plays mid-battle.
Health values are the health the champion has after the replacement.

*Vargar (J-1):*

| Stage | Champion (GUID) | On would-lose | Next form |
|-------|-----------------|---------------|-----------|
| 1 | Rabid Gnoll `02125548` | health 15; conversation `4437cce2` (Transition 1 - Vargar) | Ravenous Gnolls `0f356809` |
| 2 | Ravenous Gnolls `0f356809` | health 30; conversation `3fcd7e9e` (Transition 2 - Vargar) | Raving Gnoll Cackle `b3ac7c53` |
| 3 | Raving Gnoll Cackle `b3ac7c53` | health 60; conversation `4a58c545` (Transition 3 - Vargar); summons Burgg and Zagg | King Kazraga |

*Burgg (J-2):*

| Stage | Champion (GUID) | On would-lose | Next form |
|-------|-----------------|---------------|-----------|
| 1 | Salivating Gnoll `cac6e84c` | health 15; conversation `4a1d9692` (Transition 1 - Burgg) | Sadistic Gnolls `85b6c1ff` |
| 2 | Sadistic Gnolls `85b6c1ff` | health 30; conversation `bfda8a3e` (Transition 2 - Burgg) | Savage Gnoll Cackle `363927b0` |
| 3 | Savage Gnoll Cackle `363927b0` | health 60; conversation `df62d557` (Transition 3 - Burgg); summons Zagg and Vargar | King Kazraga |

*Zagg (J-3):*

| Stage | Champion (GUID) | On would-lose | Next form |
|-------|-----------------|---------------|-----------|
| 1 | Craven Gnoll `45c3442f` | health 15; conversation `13531a47` (Transition 1 - Zagg) | Carnivorous Gnolls `a5466533` |
| 2 | Carnivorous Gnolls `a5466533` | health 30; conversation `c5eac8f8` (Transition 2 - Zagg) | Crazed Gnoll Cackle `dd4ba061` |
| 3 | Crazed Gnoll Cackle `dd4ba061` | health 60; conversation `dd29f795` (Transition 3 - Zagg); summons Burgg and Vargar | King Kazraga |

King Kazraga is the final form on all three routes. The records give no
would-lose replacement for him, so his loss is the battle's normal defeat
(the Fail conversation).

**Transition conversations are not pre-battle talk.** The transition
conversations (`4437cce2`, `3fcd7e9e`, `4a58c545`, and the Burgg and Zagg
equivalents) are started by the champions' abilities, not by the node. They
are listed in the zone's `triggered_conversations` and are excluded from node
offers, so they are never offered as separate pre-battle entries.

**Where the data lives.** Node conversation tags are in
`campaign/campaign_data/dungeons.json` (`campaign_node_conversations`). The
champion abilities and conversation GUIDs are in the Records (`hex-private/Records`)
and in `hconnect.db` (`champion_abilities`, `ability_effects`).

**Status.** The offers and the route chains are in place server-side. The
mid-battle conversations and transforms have not been played through on a live
server; check a full route before relying on it.

## Encounter mechanics implemented on the server

| Mechanic | How it is authored | Where it runs |
|----------|--------------------|---------------|
| `check_tamed()` (a victory action) | Scene actions in `encounter_scenes` / campaign data | `campaign/campaign_runtime/offers.py` (`is_check_tamed`, `tamed_flag`); the battle reports `tamed_troop`, which sets `tamed:<scene8>` |
| `let_pass()` (ending action) | Conversation endings / scene outcomes | `campaign/campaign_rules.py` (`let_pass` lets the player leave an impassable node for one visit) |
| `set_flag(X)` / `clear_flag(X)` (scene and ending actions) | Scene actions and conversation endings | `campaign/campaign_rules.py` (`clear_flag(prefix*)` clears every matching flag) |
| Tagged pre-battle conversation | Node conversation tags (`dungeons.json`) | `campaign/campaign_runtime/offers.py` (`build_offers`): the conversation is offered with its battle while its tags hold |
| Mid-battle conversation (`ConversationAbilityEffect`) | Champion ability effect (Records) | `rules_port/context.py` (`conversation`) pauses the battle; `application/pending_choices.py` (`_queue_conversation_prompt`) queues the conversation and resumes after the client's answer |
| Would-lose replacement (`ChampionWouldLoseEvent`) | Champion ability with that trigger (Records) | `rules_port/triggers.py`, `rules_port/death_effects.py`, `abilities/framework/triggers.py` |
| Triggered conversations (not pre-battle) | Derived from `ability_effects` (`ConversationAbilityEffectTemplate`) | `campaign/campaign_runtime/catalog_data.py` (`triggered_conversations`); excluded from node offers in `offers.py` |
| Placed battle scenes | `PLACED_SCENE_NODES` (per template) | `campaign/campaign_runtime/catalog_data.py` (`placed_scene_nodes`) |
