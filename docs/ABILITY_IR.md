# Ability IR: short names for ability variables and expressions

Status: draft. Names are proposals until the coverage check in the last section passes.

## Layers

```
sentences (campaign_editor/ability_dsl.py)  ->  IR (this spec)  ->  Records sections
```

- **Sentences** are what authors write, one clause per line.
- **IR** is the intermediate representation: an explicit graph of nodes with typed references. It is what the compiler validates.
- **Records** is the storage format the client and server read. The IR must compile to it without changing its shape.

The IR is a DAG. Each node has an id and a kind. Edges are typed references:
clause → target, clause → variable input, clause → condition, clause → call (another ability).
Call edges are checked for cycles. A dangling reference is a compile error, not a runtime failure.

## Vocabulary

Names follow the form `subject.attribute` or `function(arguments)`.

### Expressions, constants and references

| IR form | Meaning |
|---|---|
| `2`, `-1` | integer literal (`AbilityConstant`) |
| `$name` | reference to a root variable (`EffectInputVariable`) |
| `a + b`, `a * -1` | arithmetic over other expressions (`ExpressionAbilityVariable`) |

`ExpressionAbilityVariable` stores its expression as text (`m_ExpressionText`). The compiler parses that text into expression nodes. The stored text stays as a fallback for anything the parser cannot read.

### Cards

| IR form | Meaning | Fields |
|---|---|---|
| `cards(list)` | cards in a named list, such as `MovedCards` | `m_ListAttrName` |
| `cards.match(f).count` | count of cards matching filter `f` | `m_CardFilter` |
| `cards(list).match(f).sum(prop)` | sum of a property over matching cards | `m_ListAttrName`, `m_CardFilter`, `m_Property` |
| `cards.max(prop)` | highest value of a property among cards | `m_PlayerFilter`, `m_CollectionFlags`, `m_CardFilter`, `m_Property` |
| `cards.counter(name).sum` | total of one counter across cards | `m_CardCounterTemplateId`, `m_PlayerFilter`, `m_CardFilter`, `m_CollectionFlags`, `m_UseHighestValue` |

`cards(list)` names a list the ability reads, not a zone. `m_PullFromSourceCard` (on count and int variables) means the list is read from the source card instead.

Scope fields, shared by the aggregate forms:
- `player`: `you`, `opponent`, or both (`m_PlayerFilter`)
- `zones`: which collections are searched (`m_CollectionFlags`)

### Attributes and stored values

| IR form | Meaning | Fields |
|---|---|---|
| `int(name)` | stored integer attribute | `m_IntAttrName`, `m_PullFromSourceCard`, `m_HalfRoundedUp` |
| `ability.prop` | ability property (`AbilityPropertyVariable`) | `m_Property` (`EAbilityProperties`) |
| `event.damage` | damage from the triggering event (`TriggerEventDamageProperty`) | none |

`m_HalfRoundedUp` decides whether a halved value rounds up or down. It must be kept, not dropped.

### Source card

| IR form | Meaning | Fields |
|---|---|---|
| `card.prop` | a property of the ability's source card (`CardPropertyVariable`) | `m_Property` |
| `card.int` | an integer read from the source card (`CardIntegerVariable`) | to confirm |
| `self.escalation` | the source card's escalation count (expression identifier `ESC`) | none |
| `self.inspire` | the source card's inspire count (expression identifier `INS`): a per-card counter that rises by one each time an inspire triggers | none |

The source card is `source_uid` in the server. `card.prop` is never a choice of card.

### Player

| IR form | Meaning | Fields |
|---|---|---|
| `you.resources` | your resource count | `m_LookUpTemporaryResources` |
| `you.health` | your health | none |
| `you.charges` | your charge points | none |
| `you.threshold(Ruby)` | your threshold count for one colour | `m_Threshold` (`ECardShards`), `m_CountUniques`, `m_DontRecalculate` |
| `you.shards` | shard counts by zone (`SourcePlayerShardAbilityVariable`) | `m_Zone`, `m_DifferentThresholds` |
| `you.stat(name)` | a player-level integer stat (`SourcePlayerBriarLegionVariable` as `you.stat(BriarLegionsPlayedThisGame)`) | `m_IntAttrName` |

`you.stat(name)` replaces the card-specific Briar Legion class with a generic stat read. No card name appears in the IR.

### Trigger

| IR form | Meaning | Fields |
|---|---|---|
| `trigger.source.prop` | property of the trigger's source card | `m_Property` |
| `trigger.target.prop` | property of the trigger's target card | `m_Property` |
| `event.damage` | damage on the trigger event | none |

### Derived counts

| IR form | Meaning | Fields |
|---|---|---|
| `cards.count(kind)` | count of a card-derived quantity (`CardCountAbilityVariable`) | `m_PlayerFilter`, `m_CardFilter`, `m_CollectionFlags`, `m_OnlyIncludeDifferentRacesForFaction` |

## Properties

`ECardProperties` (client, `Game/Shared/Mechanics/ECardProperties.cs`) has these values. IR names map onto them.

| IR name | ECardProperties | Notes |
|---|---|---|
| `attack` | `CurrentAttackValue` | server reads effective attack (`effective_stats`) |
| `defense` | `CurrentDefenseValue` | server reads effective defense (`effective_stats`) |
| `health` | `CurrentHealthValue` | **to verify**: server currently maps this to effective defense |
| `cost` | `ResourceCost` | `ResourceCostTrue` returns the same value in the client |
| `xcost` | `ResourceXCost` | |
| `lifecost` | `LifeCost` | |
| `chargecost` | `ChargePointCost` | |
| `sacrificecost` | `SacrificeCost` | |
| `discardcost` | `DiscardCost` | |
| `exhaustcost` | `ExhaustCost` | |
| `voidcost` | `VoidCost` | |
| `countercost` | `CounterCost` | |
| `castingcost` | `CastingCost` | |
| `spellpointcost` | `SpellPointCost` | |
| `putintodeckcost` | `PutIntoDeckCost` | |
| `revealcost` | `RevealCost` | |
| `putintohandcost` | `PutIntoHandCost` | |
| `lastdamage` | `LastDamageDealt` | |
| `memory` | `InternalMemoryValue` | |

`ResourceCost` and `ResourceCostTrue` return the same value in the client, so the IR writes only `cost` and the compiler maps `ResourceCostTrue` to it.

## Class mapping

Usage counts are abilities that use the class, from `hconnect.db` (`card_abilities_meta.raw_json`).

| C# class | Abilities | IR form | Extra fields |
|---|---|---|---|
| `AbilityConstant` | 4771 | literal | none |
| `AbilityVariable` (base) | 223 | variable with no computation | `m_RequiresPlayerInput`, `m_RequiresExplicitSet` |
| `ExpressionAbilityVariable` | 385 | expression | `m_ExpressionText`, `m_DontRecalculate`, `m_Calculated` |
| `CardCountAbilityVariable` | 314 | `cards.count(kind)` | `m_PlayerFilter`, `m_CardFilter`, `m_CollectionFlags`, `m_OnlyIncludeDifferentRacesForFaction`, `m_DontRecalculate` |
| `CardPropertyVariable` | 95 | `card.prop` | `m_Property` |
| `CountListAttrAbilityVariable` | 133 | `cards(list).match(f).count` | `m_ListAttrName`, `m_PullFromSourceCard`, `m_CardFilter` |
| `IntAttrAbilityVariable` | 80 | `int(name)` | `m_IntAttrName`, `m_PullFromSourceCard`, `m_HalfRoundedUp` |
| `SumVariableInListAttrCardsAbilityVariable` | 68 | `cards(list).match(f).sum(prop)` | `m_ListAttrName`, `m_CardFilter`, `m_Property` |
| `AbilityPropertyVariable` | 51 | `ability.prop` | `m_Property` (`EAbilityProperties`) |
| `CounterVariable` | 37 | `cards.counter(name).sum` | `m_CardCounterTemplateId`, `m_PlayerFilter`, `m_CardFilter`, `m_CollectionFlags`, `m_UseHighestValue` |
| `SourcePlayerThresholdAbilityVariable` | 28 | `you.threshold(c)` | `m_Threshold`, `m_CountUniques`, `m_DontRecalculate` |
| `CardIntegerVariable` | 19 | `card.int` | to confirm (reads an integer from the source card) |
| `HighestCardAbilityVariable` | 15 | `cards.max(prop)` | `m_PlayerFilter`, `m_CollectionFlags`, `m_CardFilter`, `m_Property` |
| `TriggerEventDamageProperty` | 11 | `event.damage` | none |
| `SourcePlayerHealthVariable` | 5 | `you.health` | none |
| `TriggerSourcePropertyVariable` | 6 | `trigger.source.prop` | `m_Property` |
| `CardSumAbilityVariable` | 5 | `cards.match(f).sum(prop)` | `m_PlayerFilter`, `m_CardFilter`, `m_CollectionFlags`, `m_Property`, `m_Attribute` |
| `TriggerTargetPropertyVariable` | 3 | `trigger.target.prop` | `m_Property` |
| `SourcePlayerShardAbilityVariable` | 2 | `you.shards` | `m_Zone`, `m_DifferentThresholds` |
| `SourcePlayerBriarLegionVariable` | 1 | `you.stat(BriarLegionsPlayedThisGame)` | replaces a card-specific class |
| `SourcePlayerResourceAbilityVariable` | 1 | `you.resources` | `m_LookUpTemporaryResources` |
| `SourcePlayerChargeVariable` | 1 | `you.charges` | none |

Every variable also carries `m_Name` and `m_DefaultValue`. `m_RequiresPlayerInput` means the player chooses the value (as with X costs). The IR represents it as a `choose` node, not as a flag on every variable.

## Open items

1. **`CurrentHealthValue`:** the server maps `health` to effective defense (`rules_port/static_rules.py`, `_card_property_value`). The client returns a separate `CurrentHealthValue`. Verify before the IR uses either.
2. **`CardIntegerVariable`:** the source field it reads is not yet identified.
3. **`CardCountAbilityVariable` and `CardSumAbilityVariable`:** the fields are read from the client but the server's evaluation has not been traced.
4. **Expression grammar:** the parser needs the operators that appear in `m_ExpressionText` across the data. That has not been counted yet.
5. **Coverage:** each mapping above should be checked against the Records. Every class in the table should map to exactly one IR form, and every ability using the class should compile.

## Card filters

Filters are the `Game.Shared.Mechanics.Cards.Filters.*` classes: combinators (`all_of`, `any_of`, `not`) over leaf tests such as `troop` or `in_zone`. Each class is one IR kind in `campaign_editor/ability_ir_filters.py` (65 kinds).

- **Field names** are the stored names with `m_` dropped and CamelCase turned into snake_case (`m_TargetFilters` → `target_filters`). Two acronyms (`AI`, `TAC`) are restored on the way back. Every stored key is checked to invert exactly.
- **Nested values** keep their shape: a filter becomes a `Filter` node, an ability variable goes through the variable layer, and any other object (serialized TACs, counter types) is kept as `Raw`.
- **Coverage:** all 5,228 top-level filters in the Records round-trip exactly. Filters nested inside other filters are checked as part of their parent.

## Effect templates

An effect template is one `...EffectTemplate` class (73 in the Records), and its `m_Modifier` is one of 25 `Game.Shared.Mechanics.Modifiers` classes. They live in `campaign_editor/ability_ir_effects.py`.

- **Kinds:** a node's `kind` is its class name with the effect suffix dropped and snake_cased (`SummonTokenTroopAbilityEffectTemplate` → `summon_token_troop`). The Records class is kept in `record_type`, so the round trip is exact.
- **Fields** use the filter layer's names (`m_` dropped, snake_case, with `DEPRECATED` and `TAC` kept as acronyms).
- **Nested values** dispatch by what they are: filters to the filter layer, variables and targets to their layers, modifiers and nested effects to nodes, and anything else to `Raw`. A stored double underscore (as in `m_Amount_DEPRECATED`) is written as `amount__deprecated`, so it inverts.
- **Serialized TACs** are `Raw` for now.
- **Coverage:** all 5,850 effect templates round-trip (one undecodable line is skipped, as the editor does).
- **Variables added for effects:** `effect_constant` (`EffectConstant`, a literal) and `effect_variable` (`EffectAbilityVariable`, a reference to an ability variable by name).

Not covered yet: the clauses that apply an effect (`m_AbilityEffectList` in the ability root), and the effect conditions (`AbilityEffectConditionTemplate`).

## Per-copy values (`CardStats`)

`card_instance_bits.CardStats` is `Dictionary<ECardStatType, int>`, one entry per owned card. The client never displays it, and the card-back panel that would show it (`GoCardDoubleBack`) has empty build methods. The server sends it as an empty dictionary (`encoder.py`) and handles neither `IncrementCardStats` nor `GetCardInstanceInfo`, so the client's increments are dropped today.

| Key | Name in client | Meaning in the IR (to confirm) |
|---|---|---|
| 1 | `MatchesPlayed` | matches the copy has been in |
| 2 | `MatchesWon` | matches won with the copy |
| 3 | `MatchesLost` | matches lost with the copy |
| 4 | `Experience` | experience for the card-back XP bar |
| 5 | `MaxDamageDealt` | highest damage the copy has dealt |
| 6 | `MaxDamageRecieved` | highest damage the copy has taken |
| 7 and up | none | custom slots, IR name `custom.N` |

- **Keys 1 to 6** keep their client names, so the card back would show real values if it were ever built. Changing a meaning is a design choice.
- **Keys 7 and up** have no name in the client. The client's enum reader decodes a number straight into the enum's value, so an unnamed key should load without error. This is from reading the decoder, not from running the client, so it stays unverified until a client log shows a copy with a custom key.
- **Client increments** are dropped by the server today. If the server starts recording these values, it must ignore increments for keys it owns.

## Non-goals

- Card-specific variable classes are not mapped one-to-one. They become generic stat reads, so no card name appears in the IR (per AGENTS.md).
- The IR does not replace Records. It only validates what the sentences mean before they are stored.
