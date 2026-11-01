# Ability condition glossary

Generated from the client source (`hex-private/HexClient`) and the Records (`AbilityTemplate`, `AbilityEffectTemplate`, `AbilityTargetTemplate`, `AbilityEffectConditionTemplate`). Covers ability conditions (`Abilities.Conditions`, And/Or) and trigger conditions (`Triggers.Conditions`). Meanings come from the designer-facing `ToolName` attributes in the client, so they describe intent, not the code path.

- Condition classes: 46 seen; 5 with a client name; none with a description.
- Condition fields: 131 (class, field) pairs; 68 with a description; 23 with a name only; 40 unknown.

**Matching:** a stored field `m_Foo` matches the client member `Foo` (case and underscores ignored), by name. A description can come from another class that uses the same member name, so check the source file.

## Classes

| Class | Uses | Client name | Client description | Source |
|---|---|---|---|---|
| `AbilityControllerHasThresholdAbilityCondition` | 1014 | Ability Controller has Threshold |  | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/AbilityControllerHasThresholdAbilityCondition.cs` |
| `AbilityControllerIsActiveAbilityCondition` | 6 | Ability Controller Is Active Player |  | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/AbilityControllerIsActiveAbilityCondition.cs` |
| `AbilityControllerIsStartingPlayerAbilityCondition` | 4 | Ability Controller Is Starting Player |  | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/AbilityControllerIsStartingPlayerAbilityCondition.cs` |
| `AbilityVariableCondition` | 96 |  |  |  |
| `AndAbilityCondition` | 219 |  |  |  |
| `AndEffectCondition` | 53 |  |  |  |
| `AndTriggerCondition` | 2831 |  |  |  |
| `CardFilterAbilityCondition` | 166 | Ability Source matches Card Filter |  | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/CardFilterAbilityCondition.cs` |
| `CardsDiscardedThisTurn` | 2 |  |  |  |
| `ChampionActionsCastThisTurn` | 6 |  |  |  |
| `ChampionCardsCastCondition` | 2 |  |  |  |
| `NotAbilityCondition` | 1 |  |  |  |
| `NotContingentAbilityCondition` | 16 |  |  |  |
| `NotEffectCondition` | 10 |  |  |  |
| `NotTriggerCondition` | 454 |  |  |  |
| `OrAbilityCondition` | 10 |  |  |  |
| `OrEffectCondition` | 13 |  |  |  |
| `OrTriggerCondition` | 11 |  |  |  |
| `RequiresCardsControlled` | 467 |  |  |  |
| `RequiresChampionCharges` | 8 |  |  |  |
| `RequiresChampionHealth` | 39 |  |  |  |
| `RequiresDateTime` | 24 |  |  |  |
| `RequiresEncounterGameboard` | 2 |  |  |  |
| `RequiresResourceThreshold` | 24 |  |  |  |
| `RequiresSourcePassesFilterCondition` | 133 |  |  |  |
| `RequiresTargetPassesFilterCondition` | 48 |  |  |  |
| `RequiresTotalResources` | 5 |  |  |  |
| `SourceCardHasCounters` | 50 |  |  |  |
| `SourceCardHasKeywords` | 1 |  |  |  |
| `TACTriggerCondition` | 40 | TAC Trigger Condition |  | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TACTriggerCondition.cs` |
| `TriggerAbilityHasSpellPointCostModifier` | 1 |  |  |  |
| `TriggerAbilityHasUsesPerGameValue` | 1 |  |  |  |
| `TriggerAbilityIsChargePower` | 14 |  |  |  |
| `TriggerAbilityIsSpellPower` | 1 |  |  |  |
| `TriggerCardEnteredZone` | 1629 |  |  |  |
| `TriggerCardIsAbilitySource` | 1984 |  |  |  |
| `TriggerCardIsNthCardDrawnThisTurnByThisPlayer` | 2 |  |  |  |
| `TriggerCardIsStoredTargetOfAbilitySource` | 2 |  |  |  |
| `TriggerCardMatchesFilter` | 1614 |  |  |  |
| `TriggerCardSameNameInZone` | 1 |  |  |  |
| `TriggerEventIntAttribute` | 4 |  |  |  |
| `TriggerEventIsCombatDamage` | 96 |  |  |  |
| `TriggerPlayerControlsAbilitySource` | 1549 |  |  |  |
| `TriggerPlayerControlsCard` | 4 |  |  |  |
| `TriggerPlayerControlsTarget` | 212 |  |  |  |
| `TurnPhaseCondition` | 12 |  |  |  |

## Fields with a client description

| Class | Field | Client description | Source |
|---|---|---|---|
| `AbilityControllerHasThresholdAbilityCondition` | `m_ColorFlags` | Desired resource threshold(s). | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/RequiresResourceThreshold.cs` |
| `AbilityControllerHasThresholdAbilityCondition` | `m_RequiredQuantity` | Spell point cost modifier must must match this quantity. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TriggerAbilityHasSpellPointCostModifier.cs` |
| `AbilityVariableCondition` | `m_Lhs` | The name of the left-hand side ABILITY VARIABLE for the comparison, for example: Lhs >= Rhs. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/AbilityVariableCondition.cs` |
| `AbilityVariableCondition` | `m_Rhs` | The name of the right-hand side ABILITY VARIABLE for the comparison, for example: Lhs >= Rhs. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/AbilityVariableCondition.cs` |
| `AndAbilityCondition` | `m_Conditions` | TAC conditions that must be valid. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TACTriggerCondition.cs` |
| `AndEffectCondition` | `m_Conditions` | TAC conditions that must be valid. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TACTriggerCondition.cs` |
| `AndTriggerCondition` | `m_Conditions` | TAC conditions that must be valid. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TACTriggerCondition.cs` |
| `CardFilterAbilityCondition` | `m_CardFilter` | Cards cannot be targeted by cards that match this filter. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/TargetingImmunityModifier.cs` |
| `CardsDiscardedThisTurn` | `m_PlayerFilter` | Player filter to match. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/CardSumAbilityVariable.cs` |
| `CardsDiscardedThisTurn` | `m_RequiredQuantity` | Spell point cost modifier must must match this quantity. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TriggerAbilityHasSpellPointCostModifier.cs` |
| `ChampionActionsCastThisTurn` | `m_PlayerFilter` | Player filter to match. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/CardSumAbilityVariable.cs` |
| `ChampionActionsCastThisTurn` | `m_RequiredQuantity` | Spell point cost modifier must must match this quantity. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TriggerAbilityHasSpellPointCostModifier.cs` |
| `ChampionCardsCastCondition` | `m_NonResource` | Only count non-resource cards cast | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/ChampionCardsCastCondition.cs` |
| `ChampionCardsCastCondition` | `m_PlayerFilter` | Player filter to match. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/CardSumAbilityVariable.cs` |
| `ChampionCardsCastCondition` | `m_RequiredQuantity` | Spell point cost modifier must must match this quantity. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TriggerAbilityHasSpellPointCostModifier.cs` |
| `ChampionCardsCastCondition` | `m_Resource` | Only count resource cards cast | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/ChampionCardsCastCondition.cs` |
| `ChampionCardsCastCondition` | `m_ThisTurn` | Only this turn? or all game? | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/ChampionCardsCastCondition.cs` |
| `NotAbilityCondition` | `m_Condition` | Condition to negate. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/NotAbilityCondition.cs` |
| `NotContingentAbilityCondition` | `m_EffectIndex` | Effect that must have NOT run. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/NotContingentAbilityCondition.cs` |
| `NotEffectCondition` | `m_Condition` | Condition to negate. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/NotAbilityCondition.cs` |
| `NotTriggerCondition` | `m_Condition` | Condition to negate. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/NotAbilityCondition.cs` |
| `OrAbilityCondition` | `m_Conditions` | TAC conditions that must be valid. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TACTriggerCondition.cs` |
| `OrEffectCondition` | `m_Conditions` | TAC conditions that must be valid. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TACTriggerCondition.cs` |
| `OrTriggerCondition` | `m_Conditions` | TAC conditions that must be valid. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TACTriggerCondition.cs` |
| `RequiresCardsControlled` | `m_CardCollection` | Card Collection to place summoned token. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/SummonXTokenTroopsAbilityEffectTemplate.cs` |
| `RequiresCardsControlled` | `m_CardFilter` | Cards cannot be targeted by cards that match this filter. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/TargetingImmunityModifier.cs` |
| `RequiresCardsControlled` | `m_OnlyIncludeDifferentCosts` | If true, only count different costs. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/RequiresCardsControlled.cs` |
| `RequiresCardsControlled` | `m_OnlyIncludeDifferentNames` | If true, only count different names. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/RequiresCardsControlled.cs` |
| `RequiresCardsControlled` | `m_OnlyIncludeDifferentRacesForFaction` | If not Unknown, only count different races for the specified faction. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/CardCountAbilityVariable.cs` |
| `RequiresCardsControlled` | `m_PlayerFilter` | Player filter to match. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/CardSumAbilityVariable.cs` |
| `RequiresCardsControlled` | `m_QuantityCardFilter` | If not null, RequiredQuantity is overwritten with the count from this card filter. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/RequiresCardsControlled.cs` |
| `RequiresCardsControlled` | `m_RequiredQuantity` | Spell point cost modifier must must match this quantity. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TriggerAbilityHasSpellPointCostModifier.cs` |
| `RequiresCardsControlled` | `m_SameOwner` | Is the owner of the new card the same as the target card? | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/CreateTokenCopyAbilityEffectTemplate.cs` |
| `RequiresChampionCharges` | `m_PlayerFilter` | Player filter to match. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/CardSumAbilityVariable.cs` |
| `RequiresChampionCharges` | `m_RequiredQuantity` | Spell point cost modifier must must match this quantity. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TriggerAbilityHasSpellPointCostModifier.cs` |
| `RequiresChampionHealth` | `m_PlayerFilter` | Player filter to match. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/CardSumAbilityVariable.cs` |
| `RequiresChampionHealth` | `m_RequiredQuantity` | Spell point cost modifier must must match this quantity. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TriggerAbilityHasSpellPointCostModifier.cs` |
| `RequiresDateTime` | `m_Day` | 1-31, Compared to DateTime.Now.Day | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/RequiresDateTime.cs` |
| `RequiresDateTime` | `m_DayOfWeek` | 0-6, Sunday-Saturday, Compared to DateTime.Now.DayOfWeek | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/RequiresDateTime.cs` |
| `RequiresDateTime` | `m_DayOfYear` | 1-366, Compared to DateTime.Now.DayOfYear | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/RequiresDateTime.cs` |
| `RequiresDateTime` | `m_Hour` | 0-23, Compared to DateTime.Now.Hour | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/RequiresDateTime.cs` |
| `RequiresDateTime` | `m_Minute` | 0-59, Compared to DateTime.Now.Minute | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/RequiresDateTime.cs` |
| `RequiresDateTime` | `m_Month` | 1-12, Compared to DateTime.Now.Month | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/RequiresDateTime.cs` |
| `RequiresDateTime` | `m_Second` | 0-59, Compared to DateTime.Now.Second | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/RequiresDateTime.cs` |
| `RequiresDateTime` | `m_Year` | 1-9999, Compared to DateTime.Now.Year | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/RequiresDateTime.cs` |
| `RequiresEncounterGameboard` | `m_Gameboard` | Gameboard for the encounter | `Assembly-CSharp-firstpass/Reckoning/Game/EncounterScene.cs` |
| `RequiresResourceThreshold` | `m_ColorFlags` | Desired resource threshold(s). | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/RequiresResourceThreshold.cs` |
| `RequiresResourceThreshold` | `m_RequiredQuantity` | Spell point cost modifier must must match this quantity. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TriggerAbilityHasSpellPointCostModifier.cs` |
| `RequiresSourcePassesFilterCondition` | `m_Filter` | Bury until matching this card filter | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/BuryCardAbilityEffectTemplate.cs` |
| `RequiresTargetPassesFilterCondition` | `m_Filter` | Bury until matching this card filter | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/BuryCardAbilityEffectTemplate.cs` |
| `RequiresTotalResources` | `m_PlayerFilter` | Player filter to match. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/CardSumAbilityVariable.cs` |
| `RequiresTotalResources` | `m_RequiredQuantity` | Spell point cost modifier must must match this quantity. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TriggerAbilityHasSpellPointCostModifier.cs` |
| `SourceCardHasCounters` | `m_CardCounterTemplateId` | Type of counter to remove from the target card. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/CounterClearModifier.cs` |
| `SourceCardHasKeywords` | `m_RequiredQuantity` | Spell point cost modifier must must match this quantity. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TriggerAbilityHasSpellPointCostModifier.cs` |
| `TACTriggerCondition` | `m_Conditions` | TAC conditions that must be valid. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TACTriggerCondition.cs` |
| `TriggerAbilityHasSpellPointCostModifier` | `m_RequiredQuantity` | Spell point cost modifier must must match this quantity. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TriggerAbilityHasSpellPointCostModifier.cs` |
| `TriggerAbilityHasUsesPerGameValue` | `m_UsesPerGameValue` | Trigger ability's UsesPerGame is compared to this value. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TriggerAbilityHasUsesPerGameValue.cs` |
| `TriggerCardEnteredZone` | `m_DestinationCollection` | Where the source card is. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TriggerCardEnteredZone.cs` |
| `TriggerCardEnteredZone` | `m_Opposing` | Only work when moving to opposing zones | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TriggerCardEnteredZone.cs` |
| `TriggerCardEnteredZone` | `m_SourceCollection` | Where the source card came from. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TriggerCardEnteredZone.cs` |
| `TriggerCardEnteredZone` | `m_Your` | Only work when moving to friendly zones | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TriggerCardEnteredZone.cs` |
| `TriggerCardIsNthCardDrawnThisTurnByThisPlayer` | `m_Nth` | Which card should I be? | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TriggerCardIsNthCardDrawnThisTurnByThisPlayer.cs` |
| `TriggerCardMatchesFilter` | `m_CardFilter` | Cards cannot be targeted by cards that match this filter. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/TargetingImmunityModifier.cs` |
| `TriggerCardSameNameInZone` | `m_Amount` | The number of cards to add | `Assembly-CSharp-firstpass/Reckoning/Game/EncounterModAddCard.cs` |
| `TriggerCardSameNameInZone` | `m_Zone` | Which zone should I be in? | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TriggerCardSameNameInZone.cs` |
| `TriggerEventIntAttribute` | `m_Attribute` | Attributes to add or remove from the target. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/IntAttrModifier.cs` |
| `TriggerPlayerControlsCard` | `m_CardFilter` | Cards cannot be targeted by cards that match this filter. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/TargetingImmunityModifier.cs` |
| `TurnPhaseCondition` | `m_TurnPhase` | The phase it should be when this action is to be performed | `Assembly-CSharp-firstpass/Game/Shared/Tutorial/NonStandardTurnPhaseAction.cs` |

## Fields with a client name only

| Class | Field | Client name | Source |
|---|---|---|---|
| `AbilityControllerHasThresholdAbilityCondition` | `m_ComparisonOp` | Comparison Operator | `Assembly-CSharp-firstpass/Game/Shared/Challenge/Restriction/TestQuestState.cs` |
| `AbilityVariableCondition` | `m_ComparisonOp` | Comparison Operator | `Assembly-CSharp-firstpass/Game/Shared/Challenge/Restriction/TestQuestState.cs` |
| `CardsDiscardedThisTurn` | `m_ComparisonOp` | Comparison Operator | `Assembly-CSharp-firstpass/Game/Shared/Challenge/Restriction/TestQuestState.cs` |
| `ChampionActionsCastThisTurn` | `m_ComparisonOp` | Comparison Operator | `Assembly-CSharp-firstpass/Game/Shared/Challenge/Restriction/TestQuestState.cs` |
| `ChampionCardsCastCondition` | `m_ComparisonOp` | Comparison Operator | `Assembly-CSharp-firstpass/Game/Shared/Challenge/Restriction/TestQuestState.cs` |
| `RequiresCardsControlled` | `m_ComparisonOp` | Comparison Operator | `Assembly-CSharp-firstpass/Game/Shared/Challenge/Restriction/TestQuestState.cs` |
| `RequiresCardsControlled` | `m_QuantityIsHighestOpposingChampionsMatch` | Quantity Is Highest Opposing Champion's Match | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/RequiresCardsControlled.cs` |
| `RequiresCardsControlled` | `m_QuantityIsLowestOpposingChampionsMatch` | Quantity Is Lowest Opposing Champion's Match | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/RequiresCardsControlled.cs` |
| `RequiresChampionCharges` | `m_ComparisonOp` | Comparison Operator | `Assembly-CSharp-firstpass/Game/Shared/Challenge/Restriction/TestQuestState.cs` |
| `RequiresChampionCharges` | `m_QuantityIsHighestOpposingChampionsCharges` | Quantity Is Highest Opposing Champion's Charges | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/RequiresChampionCharges.cs` |
| `RequiresChampionHealth` | `m_ComparisonOp` | Comparison Operator | `Assembly-CSharp-firstpass/Game/Shared/Challenge/Restriction/TestQuestState.cs` |
| `RequiresChampionHealth` | `m_QuantityIsHighestOpposingChampionsHealth` | Quantity Is Highest Opposing Champion's Health | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/RequiresChampionHealth.cs` |
| `RequiresChampionHealth` | `m_QuantityIsTwiceStartingHealth` | Quantity Is Twice Starting Health | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/RequiresChampionHealth.cs` |
| `RequiresDateTime` | `m_ComparisonOp` | Comparison Operator | `Assembly-CSharp-firstpass/Game/Shared/Challenge/Restriction/TestQuestState.cs` |
| `RequiresResourceThreshold` | `m_ComparisonOp` | Comparison Operator | `Assembly-CSharp-firstpass/Game/Shared/Challenge/Restriction/TestQuestState.cs` |
| `RequiresResourceThreshold` | `m_RequiresDifferentThresholds` | Requires Different Thresholds | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/RequiresResourceThreshold.cs` |
| `RequiresTotalResources` | `m_ComparisonOp` | Comparison Operator | `Assembly-CSharp-firstpass/Game/Shared/Challenge/Restriction/TestQuestState.cs` |
| `RequiresTotalResources` | `m_QuantityIsHighestOpposingChampionsMatch` | Quantity Is Highest Opposing Champion's Match | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/RequiresCardsControlled.cs` |
| `RequiresTotalResources` | `m_UseTemporaryResources` | Use Temporary Resources | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Conditions/RequiresTotalResources.cs` |
| `SourceCardHasCounters` | `m_ComparisonOp` | Comparison Operator | `Assembly-CSharp-firstpass/Game/Shared/Challenge/Restriction/TestQuestState.cs` |
| `SourceCardHasKeywords` | `m_ComparisonOp` | Comparison Operator | `Assembly-CSharp-firstpass/Game/Shared/Challenge/Restriction/TestQuestState.cs` |
| `TriggerAbilityHasSpellPointCostModifier` | `m_ComparisonOp` | Comparison Operator | `Assembly-CSharp-firstpass/Game/Shared/Challenge/Restriction/TestQuestState.cs` |
| `TriggerPlayerControlsCard` | `m_ComparisonOp` | Comparison Operator | `Assembly-CSharp-firstpass/Game/Shared/Challenge/Restriction/TestQuestState.cs` |

## Unknown fields

No `ToolName` annotation found. Meaning to be confirmed.

| Class | Field |
|---|---|
| `AndTriggerCondition` | `m_SerializedTAC` |
| `AndTriggerCondition` | `m_TriggerTest` |
| `NotTriggerCondition` | `m_SerializedTAC` |
| `NotTriggerCondition` | `m_TriggerTest` |
| `OrTriggerCondition` | `m_SerializedTAC` |
| `OrTriggerCondition` | `m_TriggerTest` |
| `SourceCardHasCounters` | `m_RequiredCounters` |
| `TACTriggerCondition` | `m_SerializedTAC` |
| `TACTriggerCondition` | `m_TriggerTest` |
| `TriggerAbilityHasSpellPointCostModifier` | `m_SerializedTAC` |
| `TriggerAbilityHasSpellPointCostModifier` | `m_TriggerTest` |
| `TriggerAbilityHasUsesPerGameValue` | `m_SerializedTAC` |
| `TriggerAbilityHasUsesPerGameValue` | `m_TriggerTest` |
| `TriggerAbilityIsChargePower` | `m_SerializedTAC` |
| `TriggerAbilityIsChargePower` | `m_TriggerTest` |
| `TriggerAbilityIsSpellPower` | `m_SerializedTAC` |
| `TriggerAbilityIsSpellPower` | `m_TriggerTest` |
| `TriggerCardEnteredZone` | `m_SerializedTAC` |
| `TriggerCardEnteredZone` | `m_TriggerTest` |
| `TriggerCardIsAbilitySource` | `m_SerializedTAC` |
| `TriggerCardIsAbilitySource` | `m_TriggerTest` |
| `TriggerCardIsNthCardDrawnThisTurnByThisPlayer` | `m_SerializedTAC` |
| `TriggerCardIsNthCardDrawnThisTurnByThisPlayer` | `m_TriggerTest` |
| `TriggerCardIsStoredTargetOfAbilitySource` | `m_SerializedTAC` |
| `TriggerCardIsStoredTargetOfAbilitySource` | `m_TriggerTest` |
| `TriggerCardMatchesFilter` | `m_SerializedTAC` |
| `TriggerCardMatchesFilter` | `m_TriggerTest` |
| `TriggerCardSameNameInZone` | `m_SerializedTAC` |
| `TriggerCardSameNameInZone` | `m_TriggerTest` |
| `TriggerEventIntAttribute` | `m_SerializedTAC` |
| `TriggerEventIntAttribute` | `m_TriggerTest` |
| `TriggerEventIsCombatDamage` | `m_SerializedTAC` |
| `TriggerEventIsCombatDamage` | `m_TriggerTest` |
| `TriggerPlayerControlsAbilitySource` | `m_SerializedTAC` |
| `TriggerPlayerControlsAbilitySource` | `m_TriggerTest` |
| `TriggerPlayerControlsCard` | `m_MinimumAmount` |
| `TriggerPlayerControlsCard` | `m_SerializedTAC` |
| `TriggerPlayerControlsCard` | `m_TriggerTest` |
| `TriggerPlayerControlsTarget` | `m_SerializedTAC` |
| `TriggerPlayerControlsTarget` | `m_TriggerTest` |
