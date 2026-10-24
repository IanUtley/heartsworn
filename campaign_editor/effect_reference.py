"""Reference for the effect templates ("clauses") that card abilities are built from.

Each Records AbilityEffectTemplate is one kind of effect. A card-modifier effect
(``CardModifierAbilityEffectTemplate``) is grouped by the modifier it applies, so its key
is ``CardModifierAbilityEffectTemplate/<Modifier>``. ``summary()`` counts the Records
templates of each kind, gives one example of the text they print, and the description
below. Records is read, never written.
"""

from __future__ import annotations

import threading
from collections import Counter
from typing import Any

import card_abilities as abilities  # noqa: E402

CARD_MODIFIER = "CardModifierAbilityEffectTemplate"

# What each kind of effect does, by its key (see the module docstring).
DESCRIPTIONS = {
    "BlockEffectTemplate": "Blocks an attacker or sets up a block.",
    "BlockImmunityExceptionModifier": "Lets the card be blocked only by the listed troops.",
    "BlockImmunityModifier": "Stops the card being blocked by troops with less attack than it.",
    "BlockRestrictionModifier": "Restricts which troops may block the card.",
    "BuiltInPlayCardAbilityEffectTemplate": "The built-in step that plays a card as the engine resolves it.",
    "BuryCardAbilityEffectTemplate": "Moves cards from the top of a deck into its crypt.",
    "Battle2CardsAbilityEffectTemplate": "Two troops fight: each deals damage equal to its attack to the other.",
    "CardCostModifier": "Changes a card's cost (added to or set by the modifier).",
    "CardThresholdModifier": "Changes the threshold a card needs to be played.",
    "ChargePointsModifier": "Gives or removes charge points on the card.",
    "CounterSpellAbilityEffectTemplate": "Cancels a spell or ability being resolved.",
    "CreateAndCastSpellAbilityEffectTemplate": "Creates a copy of a spell and casts it.",
    "CreateTokenCopyAbilityEffectTemplate": "Creates copies of cards, optionally with their equipment.",
    "CreateTokenMatchingTargetAbilityEffectTemplate": "Creates a card matching the target's shard or type.",
    "ConscriptAbilityEffectTemplate": "Conscript: puts a card of the chosen kind from the deck into play under your control.",
    "ConversationAbilityEffectTemplate": "Starts an encounter conversation.",
    "CurrentResourceModifier": "Changes the card's current resources.",
    "DamageImmunityModifier": "Prevents damage the card would be dealt, of the listed kinds.",
    "DamageMultiplierModifier": "Multiplies the damage the card deals.",
    "DamageShieldModifier": "Prevents the next damage the card would be dealt.",
    "DestroyCardAbilityEffectTemplate": "Destroys a card.",
    "DestroyCardByDefenseAbilityEffectTemplate": "Destroys a card whose defence is at or below a value.",
    "DiscardCardAbilityEffectTemplate": "Discards a card from a hand.",
    "DiscardOrSacrificeCardAbilityEffectTemplate": "Makes a player discard or sacrifice a card of their choice.",
    "DoubleChoiceAbilityEffectTemplate": "Offers a choice between two options.",
    "DrawNCardsAbilityEffectTemplate": "Draws a number of cards.",
    "FinishMovingCardToWarzoneEffectTemplate": "Completes a card's move into play.",
    "FinishResolvingCardAbilityEffectTemplate": "Completes resolving a card.",
    "FireEventEffectTemplate": "Fires a game event, which other abilities may listen for.",
    "GiveBonusTurnAbilityEffectTemplate": "Gives the player an extra turn.",
    "HealHeroModifier": "Gains or loses health on a champion.",
    "AttackImmunityModifier": "Stops the card being attacked by the listed troops.",
    "AttributeModifier": "Adds or removes keywords (attributes) on the card.",
    "ActivateTriggeredAbilityEffectTemplate": "Triggers another ability on the target, such as its Deathcry.",
    "LoseLifeModifier": "Makes a champion lose health.",
    "LoseThresholdAbilityEffectTemplate": "Makes a player lose threshold (shard) counts.",
    "PlayCardAbilityEffectTemplate": "Plays a card, such as from the choice zone.",
    "PutTopOfDeckIntoHandAbilityEffectTemplate": "Moves the top card of a deck into a hand.",
    "RandomizeVariableEffectTemplate": "Picks a random value for a variable.",
    "RegisterTriggerAbilityEffectTemplate": "Registers a delayed trigger, such as one that fires at end of turn.",
    "RemoveCardFromCombatAbilityEffectTemplate": "Removes an attacking troop from combat.",
    "RepeatingAbilityEffectTemplate": "Repeats a set of effects a number of times.",
    "ReplenishResourcesAbilityEffectTemplate": "Restores a player's resources.",
    "RememberKeywordPowersEffectTemplate": "Stores the keyword abilities of targets for later effects.",
    "RevealCardsAbilityEffectTemplate": "Reveals or looks at cards.",
    "RevertPermanentModificationsAbilityEffectTemplate": "Removes the permanent changes a card has had.",
    "RevokeAbilityEffectTemplate": "Removes an ability from a card.",
    "SacrificeCardAbilityEffectTemplate": "Sacrifices a card.",
    "SetCardIntegerVariableEffectTemplate": "Sets an integer variable on a card, such as a bolded number.",
    "SetHeroHealthModifier": "Sets a champion's health to a value.",
    "SpellPointsModifier": "Gives or removes spell points.",
    "StoreListAttrAbilityEffectTemplate": "Stores a chosen attribute, such as a shard, for later effects.",
    "StoreNameAbilityEffectTemplate": "Stores a card's name for later effects.",
    "StoreTargetsAbilityEffectTemplate": "Stores the targets chosen for later effects.",
    "SubTypeModifier": "Changes the card's subtype.",
    "SwapHealthAbilityEffectTemplate": "Swaps two champions' health.",
    "TACAbilityEffectTemplate": "Moves or changes cards that share a name or other property.",
    "TapCardAbilityEffectTemplate": "Exhausts a card (taps it).",
    "TargetingImmunityModifier": "Stops the card being targeted by listed cards.",
    "ThresholdModifier": "Adds or removes shard threshold on the card.",
    "TotalResourceModifier": "Changes the card's maximum resources.",
    "TransformCardAtRandomAbilityEffectTemplate": "Transforms a card into a random card of a kind.",
    "TransformCardIntoReplicaAbilityEffectTemplate": "Transforms a card into a replica of another card.",
    "TransformSelfAbilityEffectTemplate": "Transforms this card into a replica of another card.",
    "TunnelAbilityEffectTemplate": "Tunnels a card (moves it underground).",
    "UntapCardAbilityEffectTemplate": "Readies a card (untaps it).",
    "VerdictAbilityEffectTemplate": "Resolves a verdict between cards.",
    "VoidCardAbilityEffectTemplate": "Voids a card (removes it to the void).",
    "AnimationTriggerEffectTemplate": "Plays a presentation animation on the client.",
    "SummonTokenTroopAbilityEffectTemplate": "Summons a token troop (a card created by the effect).",
    "ActivateAbilityEffectTemplate": "Uses another ability on the target.",
    "GrantAbilityEffectTemplate": "Gives the target an ability.",
    "MoveCardToZoneEffectTemplate": "Moves a card to another zone, such as the hand, deck or play.",
    "DamageModifier": "Deals damage to the target.",
    "TransformCardAbilityEffectTemplate": "Transforms a card into another card.",
    "CounterModifier": "Adds or removes counters on the card.",
    "AttackModifier": "Changes the card's attack.",
    "DefenseModifier": "Changes the card's defence.",
    "IntAttrModifier": "Sets or adds to an integer card value, such as a number on the card.",
    "LoadPlayerDeckAbilityEffectTemplate": "Loads a deck into a player's deck.",
    "CopyAbilityEffectTemplate": "Copies a card or ability.",
}


_LOCK = threading.Lock()
_SUMMARY: list[dict[str, Any]] | None = None


def _key(record: dict[str, Any]) -> str:
    kind = str(record.get("_t") or "").rsplit(".", 1)[-1]
    if kind == CARD_MODIFIER:
        return f"{kind}/{str((record.get('m_Modifier') or {}).get('_t') or '').rsplit('.', 1)[-1]}"
    return kind


def summary() -> list[dict[str, Any]]:
    """``[{key, count, description, example}]`` for every effect kind in Records, most used first."""
    global _SUMMARY
    with _LOCK:
        if _SUMMARY is None:
            counts: Counter = Counter()
            examples: dict[str, str] = {}
            for record in abilities._iter_records("AbilityEffectTemplate"):
                key = _key(record)
                counts[key] += 1
                examples.setdefault(key, str(record.get("m_GameText") or record.get("m_Name") or ""))
            rows = []
            for key, count in counts.most_common():
                short = key.rsplit("/", 1)[-1]
                rows.append({"key": key, "count": count,
                             "description": DESCRIPTIONS.get(key) or DESCRIPTIONS.get(short, ""),
                             "example": examples.get(key, "")})
            _SUMMARY = rows
        return [dict(row) for row in _SUMMARY]
