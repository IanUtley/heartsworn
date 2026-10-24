# Effect and modifier glossary

Generated from the client source (`hex-private/HexClient`) and the Records (`AbilityEffectTemplate`). Each row is a stored field name in the effect templates and modifiers. Meanings come from the designer-facing `ToolName` attributes in the client, so they describe intent, not the code path.

- Fields: 109 distinct. 93 have a client `ToolName`, of which 65 have a description and 28 have only a name. 16 have neither.
- Classes: 82 seen; 59 with a client class description.

**Matching:** a stored field `m_Foo` matches the client member `Foo` (case and underscores ignored). The match is by name, so a description can come from another class that uses the same member name. Check the source file before relying on one.

## Fields with a client description

| Field | Used on | Client description | Source |
|---|---|---|---|
| `m_AbilityIsUnique` | GrantAbilityEffectTemplate | A Unique ability can only be in the Current Abilities list once | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/GrantAbilityEffectTemplate.cs` |
| `m_AbilityOpponentTakesControl` | MoveCardToZoneEffectTemplate | if checked, the ability source card's opponent takes control of the card. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/MoveCardToZoneEffectTemplate.cs` |
| `m_AbilityOwnerTakesControl` | MoveCardToZoneEffectTemplate, PutTopOfDeckIntoHandAbilityEffectTemplate | if checked, the ability source card's owner takes control of the card. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/MoveCardToZoneEffectTemplate.cs` |
| `m_AbilityToInvoke` | ActivateAbilityEffectTemplate | Resource Id of the new ability that should activate. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/ActivateAbilityEffectTemplate.cs` |
| `m_AllPowers` | RememberKeywordPowersEffectTemplate | Remember all powers of the target | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/RememberKeywordPowersEffectTemplate.cs` |
| `m_Amount` | BuryCardAbilityEffectTemplate, ConscriptAbilityEffectTemplate, SummonTokenTroopAbilityEffectTemplate | The number of cards to add | `Assembly-CSharp-firstpass/Reckoning/Game/EncounterModAddCard.cs` |
| `m_AnimationTrigger` | AnimationTriggerEffectTemplate | Trigger to fire | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/AnimationTriggerEffectTemplate.cs` |
| `m_ArenaChampionTakesControl` | MoveCardToZoneEffectTemplate | if checked, the Arena Master takes control of the card. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/MoveCardToZoneEffectTemplate.cs` |
| `m_Attribute` | IntAttrModifier | Attributes to add or remove from the target. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/IntAttrModifier.cs` |
| `m_AttributeFlags` | AttributeModifier | Attributes to add or remove from the target. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/AttributeModifier.cs` |
| `m_Bloodwash` | PutTopOfDeckIntoHandAbilityEffectTemplate | if checked, the card's thresholds all turn to blood. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/PutTopOfDeckIntoHandAbilityEffectTemplate.cs` |
| `m_CardCollection` | CreateTokenCopyAbilityEffectTemplate, CreateTokenMatchingTargetAbilityEffectTemplate, SummonTokenTroopAbilityEffectTemplate | Card Collection to place summoned token. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/SummonXTokenTroopsAbilityEffectTemplate.cs` |
| `m_CardCounterTemplateId` | CounterModifier | Type of counter to remove from the target card. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/CounterClearModifier.cs` |
| `m_CardFilter` | AttackImmunityModifier, BlockImmunityExceptionModifier, BlockImmunityModifier, BlockRestrictionModifier, ConscriptAbilityEffectTemplate, CreateTokenMatchingTargetAbilityEffectTemplate, DamageImmunityModifier, SummonTokenTroopAbilityEffectTemplate, TargetingImmunityModifier | Cards cannot be targeted by cards that match this filter. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/TargetingImmunityModifier.cs` |
| `m_CardLocation` | CreateTokenCopyAbilityEffectTemplate, CreateTokenMatchingTargetAbilityEffectTemplate, SummonTokenTroopAbilityEffectTemplate | Location in collection to place summoned token. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/SummonXTokenTroopsAbilityEffectTemplate.cs` |
| `m_CardTemplateId` | SummonTokenTroopAbilityEffectTemplate, TransformCardAbilityEffectTemplate | The template id of the card to be played | `Assembly-CSharp-firstpass/Game/Shared/Tutorial/TurnPhaseActionZoomCard.cs` |
| `m_Choices` | DoubleChoiceAbilityEffectTemplate | Choose cards to chooose from | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/DoubleChoiceAbilityEffectTemplate.cs` |
| `m_CombinedCost` | SummonTokenTroopAbilityEffectTemplate | How much should the cost total | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/SummonTokenTroopAbilityEffectTemplate.cs` |
| `m_ControlGivenToTargetIndex` | MoveCardToZoneEffectTemplate | -1 is current owner | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/MoveCardToZoneEffectTemplate.cs` |
| `m_ConversationId` | ConversationAbilityEffectTemplate | Conversation to launch if we should launch one. | `Assembly-CSharp-firstpass/Reckoning/Game/LocationItemData.cs` |
| `m_CopyGems` | CreateTokenCopyAbilityEffectTemplate, SummonTokenTroopAbilityEffectTemplate | Copy gems from the source card? | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/SummonTokenTroopAbilityEffectTemplate.cs` |
| `m_CopySourceCard` | CardThresholdModifier | If checked, this modifier copies the threholds of the source card. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/CardThresholdModifier.cs` |
| `m_DeckTemplateId` | LoadPlayerDeckAbilityEffectTemplate | Resource Id of the deck template to load. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/LoadPlayerDeckAbilityEffectTemplate.cs` |
| `m_DestinationCollection` | MoveCardToZoneEffectTemplate | Where the source card is. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Triggers/Conditions/TriggerCardEnteredZone.cs` |
| `m_Double` | IntAttrModifier | Doubles the current attribute. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/IntAttrModifier.cs` |
| `m_FightBack` | Battle2CardsAbilityEffectTemplate | The second thing does damage | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Battle2CardsAbilityEffectTemplate.cs` |
| `m_Filter` | BuryCardAbilityEffectTemplate, TransformCardAtRandomAbilityEffectTemplate | Bury until matching this card filter | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/BuryCardAbilityEffectTemplate.cs` |
| `m_GrantedAbilityTemplateId` | GrantAbilityEffectTemplate | The ability to grant to a target. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/GrantAbilityEffectTemplate.cs` |
| `m_InputValue` | AttackModifier, CardCostModifier, CardThresholdModifier, ChargePointsModifier, CounterModifier, CreateTokenCopyAbilityEffectTemplate, CreateTokenMatchingTargetAbilityEffectTemplate, CurrentResourceModifier, DamageModifier, DamageMultiplierModifier, DamageShieldModifier, DefenseModifier, DrawNCardsAbilityEffectTemplate, HealHeroModifier, LoseLifeModifier, PutTopOfDeckIntoHandAbilityEffectTemplate, SetCardIntegerVariableEffectTemplate, SetHeroHealthModifier, SpellPointsModifier, ThresholdModifier, TotalResourceModifier | EffectConstant: a static value, like 3.\nEffectInputVariable: a value looked up through the effect input map.\nEffectAbilityVariable: a value looked up directly from an ability variable. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/NumericModifier.cs` |
| `m_IntAttrName` | StoreListAttrAbilityEffectTemplate | Which IntAttr should be looked up. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/IntAttrAbilityVariable.cs` |
| `m_IntAttrValue` | StoreListAttrAbilityEffectTemplate | Value of the ListAttr to add | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/StoreListAttrAbilityEffectTemplate.cs` |
| `m_IsCombatDamage` | DamageImmunityModifier | Determines which type of damage immunity this applies to. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/DamageImmunityModifier.cs` |
| `m_IsReplica` | CreateTokenCopyAbilityEffectTemplate, TransformSelfAbilityEffectTemplate | Is Replica | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/TransformSelfAbilityEffectTemplate.cs` |
| `m_JustActivate` | PlayCardAbilityEffectTemplate | Don't send a trigger event, only works on resources | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/PlayCardAbilityEffectTemplate.cs` |
| `m_Keyword` | ActivateTriggeredAbilityEffectTemplate, RememberKeywordPowersEffectTemplate | The keyword powers to grant | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/RememberKeywordPowersEffectTemplate.cs` |
| `m_ListAttrName` | StoreListAttrAbilityEffectTemplate | Which ListAttr should be looked up. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/CountListAttrAbilityVariable.cs` |
| `m_LoopCount` | RepeatingAbilityEffectTemplate | Number of times to repeat the effect. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/RepeatingAbilityEffectTemplate.cs` |
| `m_LoseHalfHealth` | LoseLifeModifier | If true, loses half the current health value. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/LoseLifeModifier.cs` |
| `m_MaxValue` | RandomizeVariableEffectTemplate | Largest number that can be generated | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/RandomizeVariableEffectTemplate.cs` |
| `m_MaxValueField` | RandomizeVariableEffectTemplate | Value field for Largest number that can be generated | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/RandomizeVariableEffectTemplate.cs` |
| `m_MinValue` | RandomizeVariableEffectTemplate | Smallest number that can be generated | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/RandomizeVariableEffectTemplate.cs` |
| `m_Modifier` | CardModifierAbilityEffectTemplate | Modifier or list of modifiers to apply to the target card. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/CardModifierAbilityEffectTemplate.cs` |
| `m_Name` | ActivateAbilityEffectTemplate, ActivateTriggeredAbilityEffectTemplate, AnimationTriggerEffectTemplate, Battle2CardsAbilityEffectTemplate, BlockEffectTemplate, BuiltInPlayCardAbilityEffectTemplate, BuryCardAbilityEffectTemplate, CardModifierAbilityEffectTemplate, ConscriptAbilityEffectTemplate, ConversationAbilityEffectTemplate, CopyAbilityEffectTemplate, CounterSpellAbilityEffectTemplate, CreateAndCastSpellAbilityEffectTemplate, CreateTokenCopyAbilityEffectTemplate, CreateTokenMatchingTargetAbilityEffectTemplate, DestroyCardAbilityEffectTemplate, DestroyCardByDefenseAbilityEffectTemplate, DiscardCardAbilityEffectTemplate, DiscardOrSacrificeCardAbilityEffectTemplate, DoubleChoiceAbilityEffectTemplate, DrawNCardsAbilityEffectTemplate, FinishMovingCardToWarzoneEffectTemplate, FinishResolvingCardAbilityEffectTemplate, FireEventEffectTemplate, GiveBonusTurnAbilityEffectTemplate, GrantAbilityEffectTemplate, LoadPlayerDeckAbilityEffectTemplate, LoseThresholdAbilityEffectTemplate, MoveCardToZoneEffectTemplate, PlayCardAbilityEffectTemplate, PutTopOfDeckIntoHandAbilityEffectTemplate, RandomizeVariableEffectTemplate, RegisterTriggerAbilityEffectTemplate, RememberKeywordPowersEffectTemplate, RemoveCardFromCombatAbilityEffectTemplate, RepeatingAbilityEffectTemplate, ReplenishResourcesAbilityEffectTemplate, RevealCardsAbilityEffectTemplate, RevertPermanentModificationsAbilityEffectTemplate, RevokeAbilityEffectTemplate, SacrificeCardAbilityEffectTemplate, SetCardIntegerVariableEffectTemplate, StoreListAttrAbilityEffectTemplate, StoreNameAbilityEffectTemplate, StoreTargetsAbilityEffectTemplate, SummonTokenTroopAbilityEffectTemplate, SwapHealthAbilityEffectTemplate, TACAbilityEffectTemplate, TapCardAbilityEffectTemplate, TransformCardAbilityEffectTemplate, TransformCardAtRandomAbilityEffectTemplate, TransformCardIntoReplicaAbilityEffectTemplate, TransformSelfAbilityEffectTemplate, TunnelAbilityEffectTemplate, UntapCardAbilityEffectTemplate, VerdictAbilityEffectTemplate, VoidCardAbilityEffectTemplate | Name of hotspot | `Assembly-CSharp-firstpass/Reckoning/Game/LocationItemData.cs` |
| `m_NumOptions` | DoubleChoiceAbilityEffectTemplate | Number of options in the first choice | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/DoubleChoiceAbilityEffectTemplate.cs` |
| `m_OnlyUntilEndOfTurn` | StoreListAttrAbilityEffectTemplate, StoreNameAbilityEffectTemplate, StoreTargetsAbilityEffectTemplate | Do we forget these targets at the end of the turn? | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/StoreTargetsAbilityEffectTemplate.cs` |
| `m_Operation` | AttributeModifier, CounterModifier, IntAttrModifier, SetCardIntegerVariableEffectTemplate, SubTypeModifier | Determines whether to add or remove the tag. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/TagModifier.cs` |
| `m_PreviousControllerTakesControl` | MoveCardToZoneEffectTemplate | if checked, the previous controller of the card takes control of the card. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/MoveCardToZoneEffectTemplate.cs` |
| `m_Property` | AttackModifier, DefenseModifier | Which ability property should this variable hold. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/AbilityPropertyVariable.cs` |
| `m_RandomLocation` | MoveCardToZoneEffectTemplate | Insert into a random index up to this number | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/MoveCardToZoneEffectTemplate.cs` |
| `m_RepeatingEffect` | RepeatingAbilityEffectTemplate | The effect to repeat. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/RepeatingAbilityEffectTemplate.cs` |
| `m_ReplaceExistingValue` | AttackModifier, CardCostModifier, DamageMultiplierModifier, DefenseModifier | If checked, this modifier replaces the existing attack value on a card. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/AttackModifier.cs` |
| `m_RevokedAbilityTemplateId` | RevokeAbilityEffectTemplate | The ability to remove from the target. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/RevokeAbilityEffectTemplate.cs` |
| `m_SameOwner` | CreateTokenCopyAbilityEffectTemplate | Is the owner of the new card the same as the target card? | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/CreateTokenCopyAbilityEffectTemplate.cs` |
| `m_SecondChoice` | DoubleChoiceAbilityEffectTemplate | Marks the effect being the follow up, should have a previous double choice effect | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/DoubleChoiceAbilityEffectTemplate.cs` |
| `m_SecondValue` | RandomizeVariableEffectTemplate | Should a second value be randomized also? | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/RandomizeVariableEffectTemplate.cs` |
| `m_Set` | CurrentResourceModifier, StoreListAttrAbilityEffectTemplate, TotalResourceModifier | If not Set, then it is additive. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/CurrentResourceModifier.cs` |
| `m_SetTargets` | StoreTargetsAbilityEffectTemplate | Replace current targets or add to them? | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/StoreTargetsAbilityEffectTemplate.cs` |
| `m_SetThresholds` | CardThresholdModifier | If checked, this modifier sets thresholds to a single of the specified shards. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/CardThresholdModifier.cs` |
| `m_Shard` | CardThresholdModifier | If checked, this modifier sets the threholds to this shard. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/CardThresholdModifier.cs` |
| `m_Subtype` | SubTypeModifier | Subtype to add or remove from the target. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/SubTypeModifier.cs` |
| `m_TargetIndex` | StoreNameAbilityEffectTemplate, StoreTargetsAbilityEffectTemplate | Targets to store on the source card | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/ClearStoredAbilityEffectTemplate.cs` |
| `m_ThresholdColor` | ThresholdModifier | Color threshold to add or remove. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/ThresholdModifier.cs` |
| `m_Thresholds` | LoseThresholdAbilityEffectTemplate | Thresholds to lose. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/LoseThresholdAbilityEffectTemplate.cs` |
| `m_TopHalfOfDeck` | BuryCardAbilityEffectTemplate, MoveCardToZoneEffectTemplate | Bury the top half of the deck | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/BuryCardAbilityEffectTemplate.cs` |
| `m_TriggerAbilityTemplateId` | RegisterTriggerAbilityEffectTemplate | The ability to register. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/RegisterTriggerAbilityEffectTemplate.cs` |

## Fields with a client name only

The client annotates these with a name but no description.

| Field | Used on | Client name | Source |
|---|---|---|---|
| `m_AllCardsOfTargetInZone` | MoveCardToZoneEffectTemplate | AllCardsOfTargetInZone | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/MoveCardToZoneEffectTemplate.cs` |
| `m_AllPaymentPowersOfSource` | GrantAbilityEffectTemplate | All Payment Powers Of Source | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/GrantAbilityEffectTemplate.cs` |
| `m_AllPaymentPowersOfTarget` | GrantAbilityEffectTemplate | All Payment Powers Of Target | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/GrantAbilityEffectTemplate.cs` |
| `m_AllSocketedPowersOfMyMaster` | GrantAbilityEffectTemplate | All Socketed Powers Of My Master | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/GrantAbilityEffectTemplate.cs` |
| `m_AllSocketedPowersOfSource` | GrantAbilityEffectTemplate | All Socketed Powers Of Source | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/GrantAbilityEffectTemplate.cs` |
| `m_AllSocketedPowersOfTarget` | GrantAbilityEffectTemplate | All Socketed Powers Of Target | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/GrantAbilityEffectTemplate.cs` |
| `m_AmountField` | CopyAbilityEffectTemplate, CreateAndCastSpellAbilityEffectTemplate, SummonTokenTroopAbilityEffectTemplate | AmountField | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/CopyAbilityEffectTemplate.cs` |
| `m_CantBeSameCard` | TransformCardAtRandomAbilityEffectTemplate | CantBeSameCard | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/TransformCardAtRandomAbilityEffectTemplate.cs` |
| `m_CombatDamageOnly` | DamageMultiplierModifier | Combat Damage Only | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/DamageMultiplierModifier.cs` |
| `m_EntersPlayAttacking` | MoveCardToZoneEffectTemplate, SummonTokenTroopAbilityEffectTemplate | EntersPlayAttacking | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/SummonTokenTroopAbilityEffectTemplate.cs` |
| `m_EntersPlayExhausted` | MoveCardToZoneEffectTemplate, SummonTokenTroopAbilityEffectTemplate | EntersPlayExhausted | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/SummonTokenTroopAbilityEffectTemplate.cs` |
| `m_Faction` | ConscriptAbilityEffectTemplate | Faction | `Assembly-CSharp-firstpass/Game/Shared/Challenge/Restriction/ChampionIsFaction.cs` |
| `m_LastsIndefinitely` | DamageShieldModifier | Lasts Indefinitely | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/DamageShieldModifier.cs` |
| `m_NonCombatDamageOnly` | DamageMultiplierModifier | Non-Combat Damage Only | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/DamageMultiplierModifier.cs` |
| `m_OneShot` | DamageShieldModifier | One Shot | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/DamageShieldModifier.cs` |
| `m_OnlyCombatDamage` | DamageShieldModifier | Only Combat Damage | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/DamageShieldModifier.cs` |
| `m_OnlyPreventFromDamageDealer` | DamageShieldModifier | Damage Shield only happens against additional target | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/DamageShieldModifier.cs` |
| `m_PlantGarden` | TransformSelfAbilityEffectTemplate | PlantGarden | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/TransformSelfAbilityEffectTemplate.cs` |
| `m_RandomChampionChargePower` | GrantAbilityEffectTemplate | Random Champion Charge Power | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/GrantAbilityEffectTemplate.cs` |
| `m_RandomInspirePower` | GrantAbilityEffectTemplate | Random Inspire Power | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/GrantAbilityEffectTemplate.cs` |
| `m_RemoveAllCharges` | ChargePointsModifier | Remove All Charges | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/ChargePointsModifier.cs` |
| `m_RemoveAllCounters` | CounterModifier | Remove All Counters | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/CounterModifier.cs` |
| `m_RemoveHalfRoundedUp` | CounterModifier | Remove Half Rounded Up | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/CounterModifier.cs` |
| `m_SendPlayAction` | CreateAndCastSpellAbilityEffectTemplate | SendPlayAction | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/CreateAndCastSpellAbilityEffectTemplate.cs` |
| `m_Terminus` | SummonTokenTroopAbilityEffectTemplate | Terminus | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/SummonTokenTroopAbilityEffectTemplate.cs` |
| `m_Value` | IntAttrModifier | Value | `Assembly-CSharp-firstpass/Game/Shared/Challenge/Restriction/TestCampaignState.cs` |
| `m_ValueField` | IntAttrModifier | ValueField | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/IntAttrModifier.cs` |
| `m_VariableName` | RandomizeVariableEffectTemplate, SetCardIntegerVariableEffectTemplate | Variable Name | `Assembly-CSharp-firstpass/Game/Shared/Challenge/Restriction/TestCampaignState.cs` |

## Unknown fields

No `ToolName` annotation found. Meaning to be confirmed. Some of these are structural (`m_TemplateId`, `m_SerializedTAC`, `m_GameText`, `m_EditorVariableMap`) and are covered elsewhere in the IR.

| Field | Used on |
|---|---|
| `m_AllRememberedPowers` | GrantAbilityEffectTemplate |
| `m_Amount_DEPRECATED` | AttackModifier, CardCostModifier, CardThresholdModifier, ChargePointsModifier, CounterModifier, CurrentResourceModifier, DamageModifier, DamageMultiplierModifier, DamageShieldModifier, DefenseModifier, HealHeroModifier, LoseLifeModifier, SetHeroHealthModifier, SpellPointsModifier, ThresholdModifier, TotalResourceModifier |
| `m_DamageDealerAdditionalTarget` | DamageShieldModifier |
| `m_DestinationLocation` | MoveCardToZoneEffectTemplate |
| `m_EditorVariableMap` | ActivateAbilityEffectTemplate, ActivateTriggeredAbilityEffectTemplate, AnimationTriggerEffectTemplate, Battle2CardsAbilityEffectTemplate, BlockEffectTemplate, BuiltInPlayCardAbilityEffectTemplate, BuryCardAbilityEffectTemplate, CardModifierAbilityEffectTemplate, ConscriptAbilityEffectTemplate, ConversationAbilityEffectTemplate, CopyAbilityEffectTemplate, CounterSpellAbilityEffectTemplate, CreateAndCastSpellAbilityEffectTemplate, CreateTokenCopyAbilityEffectTemplate, CreateTokenMatchingTargetAbilityEffectTemplate, DestroyCardAbilityEffectTemplate, DestroyCardByDefenseAbilityEffectTemplate, DiscardCardAbilityEffectTemplate, DiscardOrSacrificeCardAbilityEffectTemplate, DoubleChoiceAbilityEffectTemplate, DrawNCardsAbilityEffectTemplate, FinishMovingCardToWarzoneEffectTemplate, FinishResolvingCardAbilityEffectTemplate, FireEventEffectTemplate, GiveBonusTurnAbilityEffectTemplate, GrantAbilityEffectTemplate, LoadPlayerDeckAbilityEffectTemplate, LoseThresholdAbilityEffectTemplate, MoveCardToZoneEffectTemplate, PlayCardAbilityEffectTemplate, PutTopOfDeckIntoHandAbilityEffectTemplate, RandomizeVariableEffectTemplate, RegisterTriggerAbilityEffectTemplate, RememberKeywordPowersEffectTemplate, RemoveCardFromCombatAbilityEffectTemplate, RepeatingAbilityEffectTemplate, ReplenishResourcesAbilityEffectTemplate, RevealCardsAbilityEffectTemplate, RevertPermanentModificationsAbilityEffectTemplate, RevokeAbilityEffectTemplate, SacrificeCardAbilityEffectTemplate, SetCardIntegerVariableEffectTemplate, StoreListAttrAbilityEffectTemplate, StoreNameAbilityEffectTemplate, StoreTargetsAbilityEffectTemplate, SummonTokenTroopAbilityEffectTemplate, SwapHealthAbilityEffectTemplate, TACAbilityEffectTemplate, TapCardAbilityEffectTemplate, TransformCardAbilityEffectTemplate, TransformCardAtRandomAbilityEffectTemplate, TransformCardIntoReplicaAbilityEffectTemplate, TransformSelfAbilityEffectTemplate, TunnelAbilityEffectTemplate, UntapCardAbilityEffectTemplate, VerdictAbilityEffectTemplate, VoidCardAbilityEffectTemplate |
| `m_GameText` | ActivateAbilityEffectTemplate, ActivateTriggeredAbilityEffectTemplate, AnimationTriggerEffectTemplate, Battle2CardsAbilityEffectTemplate, BlockEffectTemplate, BuiltInPlayCardAbilityEffectTemplate, BuryCardAbilityEffectTemplate, CardModifierAbilityEffectTemplate, ConscriptAbilityEffectTemplate, ConversationAbilityEffectTemplate, CopyAbilityEffectTemplate, CounterSpellAbilityEffectTemplate, CreateAndCastSpellAbilityEffectTemplate, CreateTokenCopyAbilityEffectTemplate, CreateTokenMatchingTargetAbilityEffectTemplate, DestroyCardAbilityEffectTemplate, DestroyCardByDefenseAbilityEffectTemplate, DiscardCardAbilityEffectTemplate, DiscardOrSacrificeCardAbilityEffectTemplate, DoubleChoiceAbilityEffectTemplate, DrawNCardsAbilityEffectTemplate, FinishMovingCardToWarzoneEffectTemplate, FinishResolvingCardAbilityEffectTemplate, FireEventEffectTemplate, GiveBonusTurnAbilityEffectTemplate, GrantAbilityEffectTemplate, LoadPlayerDeckAbilityEffectTemplate, LoseThresholdAbilityEffectTemplate, MoveCardToZoneEffectTemplate, PlayCardAbilityEffectTemplate, PutTopOfDeckIntoHandAbilityEffectTemplate, RandomizeVariableEffectTemplate, RegisterTriggerAbilityEffectTemplate, RememberKeywordPowersEffectTemplate, RemoveCardFromCombatAbilityEffectTemplate, RepeatingAbilityEffectTemplate, ReplenishResourcesAbilityEffectTemplate, RevealCardsAbilityEffectTemplate, RevertPermanentModificationsAbilityEffectTemplate, RevokeAbilityEffectTemplate, SacrificeCardAbilityEffectTemplate, SetCardIntegerVariableEffectTemplate, StoreListAttrAbilityEffectTemplate, StoreNameAbilityEffectTemplate, StoreTargetsAbilityEffectTemplate, SummonTokenTroopAbilityEffectTemplate, SwapHealthAbilityEffectTemplate, TACAbilityEffectTemplate, TapCardAbilityEffectTemplate, TransformCardAbilityEffectTemplate, TransformCardAtRandomAbilityEffectTemplate, TransformCardIntoReplicaAbilityEffectTemplate, TransformSelfAbilityEffectTemplate, TunnelAbilityEffectTemplate, UntapCardAbilityEffectTemplate, VerdictAbilityEffectTemplate, VoidCardAbilityEffectTemplate |
| `m_LoopCount_DEPRECATED` | RepeatingAbilityEffectTemplate |
| `m_MasterDealsDamage` | DamageModifier |
| `m_PlayerRevealTargets` | RevealCardsAbilityEffectTemplate |
| `m_Portal` | TransformCardAbilityEffectTemplate |
| `m_Random` | ThresholdModifier |
| `m_RandomLowestThreshold` | ThresholdModifier |
| `m_RandomlyLuckyOrUnlucky` | ActivateAbilityEffectTemplate |
| `m_SerializedTAC` | ActivateAbilityEffectTemplate, ActivateTriggeredAbilityEffectTemplate, AnimationTriggerEffectTemplate, Battle2CardsAbilityEffectTemplate, BlockEffectTemplate, BuiltInPlayCardAbilityEffectTemplate, BuryCardAbilityEffectTemplate, CardModifierAbilityEffectTemplate, ConscriptAbilityEffectTemplate, ConversationAbilityEffectTemplate, CopyAbilityEffectTemplate, CounterSpellAbilityEffectTemplate, CreateAndCastSpellAbilityEffectTemplate, CreateTokenCopyAbilityEffectTemplate, CreateTokenMatchingTargetAbilityEffectTemplate, DestroyCardAbilityEffectTemplate, DestroyCardByDefenseAbilityEffectTemplate, DiscardCardAbilityEffectTemplate, DiscardOrSacrificeCardAbilityEffectTemplate, DoubleChoiceAbilityEffectTemplate, DrawNCardsAbilityEffectTemplate, FinishMovingCardToWarzoneEffectTemplate, FinishResolvingCardAbilityEffectTemplate, FireEventEffectTemplate, GiveBonusTurnAbilityEffectTemplate, GrantAbilityEffectTemplate, LoadPlayerDeckAbilityEffectTemplate, LoseThresholdAbilityEffectTemplate, MoveCardToZoneEffectTemplate, PlayCardAbilityEffectTemplate, PutTopOfDeckIntoHandAbilityEffectTemplate, RandomizeVariableEffectTemplate, RegisterTriggerAbilityEffectTemplate, RememberKeywordPowersEffectTemplate, RemoveCardFromCombatAbilityEffectTemplate, RepeatingAbilityEffectTemplate, ReplenishResourcesAbilityEffectTemplate, RevealCardsAbilityEffectTemplate, RevertPermanentModificationsAbilityEffectTemplate, RevokeAbilityEffectTemplate, SacrificeCardAbilityEffectTemplate, SetCardIntegerVariableEffectTemplate, StoreListAttrAbilityEffectTemplate, StoreNameAbilityEffectTemplate, StoreTargetsAbilityEffectTemplate, SummonTokenTroopAbilityEffectTemplate, SwapHealthAbilityEffectTemplate, TACAbilityEffectTemplate, TapCardAbilityEffectTemplate, TransformCardAbilityEffectTemplate, TransformCardAtRandomAbilityEffectTemplate, TransformCardIntoReplicaAbilityEffectTemplate, TransformSelfAbilityEffectTemplate, TunnelAbilityEffectTemplate, UntapCardAbilityEffectTemplate, VerdictAbilityEffectTemplate, VoidCardAbilityEffectTemplate |
| `m_TemplateId` | ActivateAbilityEffectTemplate, ActivateTriggeredAbilityEffectTemplate, AnimationTriggerEffectTemplate, Battle2CardsAbilityEffectTemplate, BlockEffectTemplate, BuiltInPlayCardAbilityEffectTemplate, BuryCardAbilityEffectTemplate, CardModifierAbilityEffectTemplate, ConscriptAbilityEffectTemplate, ConversationAbilityEffectTemplate, CopyAbilityEffectTemplate, CounterSpellAbilityEffectTemplate, CreateAndCastSpellAbilityEffectTemplate, CreateTokenCopyAbilityEffectTemplate, CreateTokenMatchingTargetAbilityEffectTemplate, DestroyCardAbilityEffectTemplate, DestroyCardByDefenseAbilityEffectTemplate, DiscardCardAbilityEffectTemplate, DiscardOrSacrificeCardAbilityEffectTemplate, DoubleChoiceAbilityEffectTemplate, DrawNCardsAbilityEffectTemplate, FinishMovingCardToWarzoneEffectTemplate, FinishResolvingCardAbilityEffectTemplate, FireEventEffectTemplate, GiveBonusTurnAbilityEffectTemplate, GrantAbilityEffectTemplate, LoadPlayerDeckAbilityEffectTemplate, LoseThresholdAbilityEffectTemplate, MoveCardToZoneEffectTemplate, PlayCardAbilityEffectTemplate, PutTopOfDeckIntoHandAbilityEffectTemplate, RandomizeVariableEffectTemplate, RegisterTriggerAbilityEffectTemplate, RememberKeywordPowersEffectTemplate, RemoveCardFromCombatAbilityEffectTemplate, RepeatingAbilityEffectTemplate, ReplenishResourcesAbilityEffectTemplate, RevealCardsAbilityEffectTemplate, RevertPermanentModificationsAbilityEffectTemplate, RevokeAbilityEffectTemplate, SacrificeCardAbilityEffectTemplate, SetCardIntegerVariableEffectTemplate, StoreListAttrAbilityEffectTemplate, StoreNameAbilityEffectTemplate, StoreTargetsAbilityEffectTemplate, SummonTokenTroopAbilityEffectTemplate, SwapHealthAbilityEffectTemplate, TACAbilityEffectTemplate, TapCardAbilityEffectTemplate, TransformCardAbilityEffectTemplate, TransformCardAtRandomAbilityEffectTemplate, TransformCardIntoReplicaAbilityEffectTemplate, TransformSelfAbilityEffectTemplate, TunnelAbilityEffectTemplate, UntapCardAbilityEffectTemplate, VerdictAbilityEffectTemplate, VoidCardAbilityEffectTemplate |
| `m_TriggerType` | FireEventEffectTemplate |

## Classes

| Class | Client description | Source |
|---|---|---|
| `ActivateAbilityEffectTemplate` | Activate an additional ability | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/ActivateAbilityEffectTemplate.cs` |
| `ActivateTriggeredAbilityEffectTemplate` | Activate a triggered ability | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/ActivateTriggeredAbilityEffectTemplate.cs` |
| `AnimationTriggerEffectTemplate` | Fire an animation | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/AnimationTriggerEffectTemplate.cs` |
| `AttackImmunityModifier` | *no description* | |
| `AttackModifier` | *no description* | |
| `AttributeModifier` | *no description* | |
| `Battle2CardsAbilityEffectTemplate` | Battle 2 cards | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/Battle2CardsAbilityEffectTemplate.cs` |
| `BlockEffectTemplate` | Move card to a new zone | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/BlockEffectTemplate.cs` |
| `BlockImmunityExceptionModifier` | *no description* | |
| `BlockImmunityModifier` | *no description* | |
| `BlockRestrictionModifier` | *no description* | |
| `BuiltInPlayCardAbilityEffectTemplate` | Play a card (NOT FOR TOOL) | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/BuiltInPlayCardAbilityEffectTemplate.cs` |
| `BuryCardAbilityEffectTemplate` | Bury Target | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/BuryCardAbilityEffectTemplate.cs` |
| `CardCostModifier` | Modified the CMC of a card for the duration of the effect. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/CardCostModifier.cs` |
| `CardModifierAbilityEffectTemplate` | Modify a card's properties | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/CardModifierAbilityEffectTemplate.cs` |
| `CardThresholdModifier` | Modified the thresholds of a card for the duration of the effect. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Modifiers/CardThresholdModifier.cs` |
| `ChargePointsModifier` | *no description* | |
| `ConscriptAbilityEffectTemplate` | Conscript a card | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/ConscriptAbilityEffectTemplate.cs` |
| `ConversationAbilityEffectTemplate` | Pop up an encounter conversation | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/ConversationAbilityEffectTemplate.cs` |
| `CopyAbilityEffectTemplate` | Create and cast a copy of passed in target | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/CopyAbilityEffectTemplate.cs` |
| `CounterModifier` | *no description* | |
| `CounterSpellAbilityEffectTemplate` | Counter a card on the chain | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/CounterSpellAbilityEffectTemplate.cs` |
| `CreateAndCastSpellAbilityEffectTemplate` | Create and cast a copy of passed in target | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/CreateAndCastSpellAbilityEffectTemplate.cs` |
| `CreateTokenCopyAbilityEffectTemplate` | Create a copy of a card | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/CreateTokenCopyAbilityEffectTemplate.cs` |
| `CreateTokenMatchingTargetAbilityEffectTemplate` | Create a card matching various properties of a target card | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/CreateTokenMatchingTargetAbilityEffectTemplate.cs` |
| `CurrentResourceModifier` | *no description* | |
| `DamageImmunityModifier` | *no description* | |
| `DamageModifier` | *no description* | |
| `DamageMultiplierModifier` | *no description* | |
| `DamageShieldModifier` | *no description* | |
| `DefenseModifier` | *no description* | |
| `DestroyCardAbilityEffectTemplate` | Destroy a card | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/DestroyCardAbilityEffectTemplate.cs` |
| `DestroyCardByDefenseAbilityEffectTemplate` | Destroy a card based on its Defense | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/DestroyCardByDefenseAbilityEffectTemplate.cs` |
| `DiscardCardAbilityEffectTemplate` | Discard a card | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/DiscardCardAbilityEffectTemplate.cs` |
| `DiscardOrSacrificeCardAbilityEffectTemplate` | Discard or Sacrifice a card | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/DiscardOrSacrificeCardAbilityEffectTemplate.cs` |
| `DoubleChoiceAbilityEffectTemplate` | Double choice creation effet | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/DoubleChoiceAbilityEffectTemplate.cs` |
| `DrawNCardsAbilityEffectTemplate` | Draw n cards | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/DrawNCardsAbilityEffectTemplate.cs` |
| `FinishMovingCardToWarzoneEffectTemplate` | Finish moving card to warzone | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/FinishMovingCardToWarzoneEffectTemplate.cs` |
| `FinishResolvingCardAbilityEffectTemplate` | Play a card (NOT FOR TOOL) | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/FinishResolvingCardAbilityEffectTemplate.cs` |
| `FireEventEffectTemplate` | Finish moving card to warzone | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/FireEventEffectTemplate.cs` |
| `GiveBonusTurnAbilityEffectTemplate` | Give the player an extra turn | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/GiveBonusTurnAbilityEffectTemplate.cs` |
| `GrantAbilityEffectTemplate` | Give a new ability to a card | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/GrantAbilityEffectTemplate.cs` |
| `HealHeroModifier` | *no description* | |
| `IntAttrModifier` | *no description* | |
| `LoadPlayerDeckAbilityEffectTemplate` | Load Player Deck | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/LoadPlayerDeckAbilityEffectTemplate.cs` |
| `LoseLifeModifier` | *no description* | |
| `LoseThresholdAbilityEffectTemplate` | Cause a champion to lose thresholds | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/LoseThresholdAbilityEffectTemplate.cs` |
| `MoveCardToZoneEffectTemplate` | Move card to a new zone | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/MoveCardToZoneEffectTemplate.cs` |
| `PlayCardAbilityEffectTemplate` | Play a card for free | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/PlayCardAbilityEffectTemplate.cs` |
| `PutTopOfDeckIntoHandAbilityEffectTemplate` | Put top of deck into hand | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/PutTopOfDeckIntoHandAbilityEffectTemplate.cs` |
| `RandomizeVariableEffectTemplate` | Randomize Variable | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/RandomizeVariableEffectTemplate.cs` |
| `RegisterTriggerAbilityEffectTemplate` | Register a trigger to happen | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/RegisterTriggerAbilityEffectTemplate.cs` |
| `RememberKeywordPowersEffectTemplate` | Remembers the keyword powers of targeted card | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/RememberKeywordPowersEffectTemplate.cs` |
| `RemoveCardFromCombatAbilityEffectTemplate` | Remove a card from combat | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/RemoveCardFromCombatAbilityEffectTemplate.cs` |
| `RepeatingAbilityEffectTemplate` | Repeats the attached effect a number of times equal to the value of the Loop Count ability variable. | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/RepeatingAbilityEffectTemplate.cs` |
| `ReplenishResourcesAbilityEffectTemplate` | Replenish Resources | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/ReplenishResourcesAbilityEffectTemplate.cs` |
| `RevealCardsAbilityEffectTemplate` | Reveal hidden cards to one or more players | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/RevealCardsAbilityEffectTemplate.cs` |
| `RevertPermanentModificationsAbilityEffectTemplate` | Revert permanent modifications on a card | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/RevertPermanentModificationsAbilityEffectTemplate.cs` |
| `RevokeAbilityEffectTemplate` | Remove an ability from card | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/RevokeAbilityEffectTemplate.cs` |
| `SacrificeCardAbilityEffectTemplate` | Sacrifice a card | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/SacrificeCardAbilityEffectTemplate.cs` |
| `SetCardIntegerVariableEffectTemplate` | Set Card Integer Variable | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/SetCardIntegerVariableEffectTemplate.cs` |
| `SetHeroHealthModifier` | *no description* | |
| `SpellPointsModifier` | *no description* | |
| `StoreListAttrAbilityEffectTemplate` | Store a ListAttr | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/StoreListAttrAbilityEffectTemplate.cs` |
| `StoreNameAbilityEffectTemplate` | Store Name of Target | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/StoreNameAbilityEffectTemplate.cs` |
| `StoreTargetsAbilityEffectTemplate` | Store Targets | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/StoreTargetsAbilityEffectTemplate.cs` |
| `SubTypeModifier` | *no description* | |
| `SummonTokenTroopAbilityEffectTemplate` | Summon a card | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/SummonTokenTroopAbilityEffectTemplate.cs` |
| `SwapHealthAbilityEffectTemplate` | Swap champion's health | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/SwapHealthAbilityEffectTemplate.cs` |
| `TACAbilityEffectTemplate` | The only Ability Effect Template we should ever need | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/TACAbilityEffectTemplate.cs` |
| `TapCardAbilityEffectTemplate` | Exhaust a card | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/TapCardAbilityEffectTemplate.cs` |
| `TargetingImmunityModifier` | *no description* | |
| `ThresholdModifier` | *no description* | |
| `TotalResourceModifier` | *no description* | |
| `TransformCardAbilityEffectTemplate` | Transform a card into a new template | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/TransformCardAbilityEffectTemplate.cs` |
| `TransformCardAtRandomAbilityEffectTemplate` | Transform a card into a new template at random | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/TransformCardAtRandomAbilityEffectTemplate.cs` |
| `TransformCardIntoReplicaAbilityEffectTemplate` | Transform a card into a replica of itself | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/TransformCardIntoReplicaAbilityEffectTemplate.cs` |
| `TransformSelfAbilityEffectTemplate` | Transform self into a new template | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/TransformSelfAbilityEffectTemplate.cs` |
| `TunnelAbilityEffectTemplate` | Tunnel | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/TunnelAbilityEffectTemplate.cs` |
| `UntapCardAbilityEffectTemplate` | Ready a card | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/UntapCardAbilityEffectTemplate.cs` |
| `VerdictAbilityEffectTemplate` | Verdict choice creation effet | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/VerdictAbilityEffectTemplate.cs` |
| `VoidCardAbilityEffectTemplate` | Send a card to the void | `Assembly-CSharp-firstpass/Game/Shared/Mechanics/Abilities/VoidCardAbilityEffectTemplate.cs` |
