"""Card abilities as plain sentences: a small DSL, its compiler and its decompiler.

An author writes what an ability does, one clause per line::

    Settings: cost 2, exhausts this            (optional; root options)
    When this enters play, deal 2 damage to target opposing troop.
    Then draw a card if you have 1 blood threshold.

and ``compile_text`` turns it into the four Records sections: one ``AbilityTemplate``
root, and the ``AbilityEffectTemplate``, ``AbilityEffectConditionTemplate`` and
``AbilityTargetTemplate`` records it names. ``decompile`` reads an existing ability
back into the same sentences, and says when it cannot.

How Records stores an ability (read from Records; ``coverage`` checks it)
-------------------------------------------------------------------------

The root holds the order and the numbers. Effects, conditions and targets are
shared library records: one "You" target serves about 2,600 abilities, and one
"destroy" effect serves every "Destroy ...".

* ``m_AbilityEffectList[i]`` is clause *i*: ``m_EffectInstanceId`` = *i*,
  ``m_EffectGroupId`` = *i* + 1, ``m_TargetTemplateIndex`` indexes
  ``m_AbilityTargetTemplateIds`` (targets in order of first use; a later clause
  on the same chosen target shares its slot, as 536 of 539 do in Records).
  ``m_RecalculateTargets`` is ``True`` for an auto target and ``UseDefault`` for
  a chosen one. ``m_EffectDuration`` is the clause's duration, and
  ``m_ConditionId`` its condition.
* An amount is a root variable that the effect names: the effect holds
  ``EffectInputVariable{m_InputVariableName: "2"}`` and the root's ``m_Variables``
  holds ``AbilityConstant{m_Name: "2", m_DefaultValue: 2}``, one binding per
  clause that reads it. "for each X" is a ``CardCountAbilityVariable``.
* A trigger is ``m_TriggerEventType`` plus a ``m_TriggerCondition`` tree.

Each row gives the sentence, what it stores, and the number of Records
card-ability roots that round-trip exactly and use it. There are 6,232 roots in
all; 4,021 are exact and none are read without being reproduced. ``T`` is a
target, ``N`` an amount, and CM is ``CardModifierAbilityEffectTemplate`` with
the modifier named::

  Sentence                                Stored as                                     Roots
  T gets +N attack / +N defense           CM AttackModifier / DefenseModifier{m_InputValue} 839/706
  T gets K / T loses K (K keywords)       CM AttributeModifier{m_AttributeFlags, Add/Remove} 555/0
  use the ability "A" on T                ActivateAbilityEffectTemplate{m_AbilityToInvoke}   384
  T gets the [unique] ability "A"         GrantAbilityEffectTemplate{m_GrantedAbilityTemplateId} 381/16
  summon N "Card"                         SummonTokenTroop{m_CardTemplateId, Warzone}        278
  deal N damage to T                      CM DamageModifier{m_InputValue}                    218
  draw N cards / T draws N cards          DrawNCardsAbilityEffectTemplate{m_InputValue}      180
  set / add to T's V (a card value)       CM IntAttrModifier{m_Attribute, Set/Add, m_Value}  163/107
  put T into your hand                    MoveCardToZone{Hand, m_AbilityOwnerTakesControl}   158
  T gets +N cost                          CM CardCostModifier                                147
  put "Card" into the choice zone / your
    hand / shuffle it into your deck      SummonTokenTroop{Choosing / Hand / Deck}      129/112/71
  put T into your played resources        MoveCardToZone{PlayedResources, owner}             129
  T transforms into "Card"                TransformCardAbilityEffectTemplate                 123
  destroy / void / exhaust / ready /
    sacrifice / discard T                 Destroy/VoidCard/TapCard/UntapCard/Sacrifice/Discard 105/84/56/53/24/18
  T gains N health / gain N health        CM HealHeroModifier                                104
  revert T                                RevertPermanentModifications                       102
  put T into play [under your control]    MoveCardToZone{Warzone[, owner]}                   100/49
  fire the E event                        FireEventEffectTemplate{m_TriggerType}             93
  add / remove N "Counter" counters       CM CounterModifier{m_CardCounterTemplateId}        77/6
  bury the top N cards of T's deck        BuryCardAbilityEffectTemplate{m_Amount}            65
  T gets +N [maximum] resources           CM CurrentResourceModifier / TotalResourceModifier 64/64
  T gains N charges                       CM ChargePointsModifier                            57
  put T into your deck / crypt; return
    T to its owner's hand / deck          MoveCardToZone{Deck/Discard/Hand/Deck[, owner]} 38/2/24/6
  T gets +N Shard threshold               CM ThresholdModifier{m_ThresholdColor}             38
  look at / reveal T                      RevealCards{m_PlayerRevealTargets Self/Everyone}   21/20
  create N "Card"                         SummonTokenTroop{None}                             17
  remember T                              StoreTargetsAbilityEffectTemplate                  9
  T loses N health                        CM LoseLifeModifier                                5
  T gets V (an on/off card value)         CM IntAttrModifier{m_Attribute, Set, m_Value 1}    50
  your health becomes N                   CM SetHeroHealthModifier                           15
  your [maximum] resources become N       CM Current/TotalResourceModifier{m_Set}
  summon N random F / put N random F
    into your hand                        SummonTokenTroop{m_CardFilter[, Hand]}
  start the conversation "C"              ConversationAbilityEffectTemplate{m_ConversationId}
  put the deck "D" into your deck         LoadPlayerDeckAbilityEffectTemplate{m_DeckTemplateId}

"T gets V" takes only the card values ``card_attributes.flag_attributes`` names
(every Records card that carries one holds it at 1, as with Lethal); any other
value is "set T's V to N". F is a random summon's card filter, named by its
effects' words after "random" (the ``filters`` vocabulary; a filter that reads a
variable has none). Conversations, decks and champions (a champion is a card for
"transforms into") are named by their Records ``m_Name``/``m_DeckName``, read as
text. A keyword may be written by its ToolName ("Crush", "Swift Strike"); it reads
back by member name. (Counts in these rows are over all 8,994 Records
AbilityTemplate roots, of which 5,869 round-trip exactly and none inexactly.)

Replacements ("if X would happen, do Y instead")
------------------------------------------------

A replacement is an ordinary root whose ``m_TriggerEventType`` is a "would" event
(``REPLACEMENT_EVENT``: CardWouldEnterZoneEvent, CardWouldBeDamagedEvent,
CardWouldDealDamageEvent, CardWouldBeDrawnEvent, ChampionWouldLoseEvent). Its
``m_TriggerCondition`` says which card or player and zone (TriggerCardIsAbilitySource,
TriggerCardEnteredZone{m_DestinationCollection, m_SourceCollection, m_Opposing},
TriggerPlayerControlsAbilitySource with TriggerCardMatchesFilter IsHero for "you",
TriggerCardMatchesFilter{m_TriggerTest TriggerTarget} for what damage is dealt to,
TriggerEventIsCombatDamage), and its effects are what happens instead. All 38
Records replacement roots set ``m_IgnoresChain``; the four on a card's own "would
enter a zone" work from every zone (``m_TriggerCollectionFlags``). The sentence is
the trigger, read from the roots' text ("if this would die"), then the clauses with
"instead" ending the last one::

    If this would enter a hand or crypt, void this.
    Then reveal this (keeping targets).
    Then deal 4 damage to you instead.

The root values a replacement trigger's Records roots hold are written unsaid with
it (``_root_defaults``). "for each damage that would be dealt" is the event's
``TriggerEventDamageProperty``. 33 of the 38 round-trip, the 10 encounter scripts
("if you would lose the game": health, conversation, resources, random summons, a
champion transform, a new deck) among them. The 5 others have one each: variables
read outside the effects, a constant amount, a secondary target, a dungeon-long
duration, and a trigger whose words hold a colon ("if there are twenty or more
cards in your crypt: if you would draw a card").

The parts of a clause:

* T, a target phrase: an ``AbilityTargetTemplate``. The phrase is the record's
  own ``m_GameText``; Records has one record content per text (2 of 1,002 texts
  are shared, and those go to the content that uses them most). That gives
  1,310 phrases. "<target|each> [opposing] <troop|champion|artifact|card|
  constant> [you control]" is also built when Records lacks it, word order
  being filter order, and ``built_target_mismatches`` checks it against every
  such phrase Records has. Placeholders read as words ("#TRIGGER_SOURCE#" is
  "the trigger source").
* The trigger, "When this attacks, ...": ``m_TriggerEventType`` and
  ``m_TriggerCondition``, named by the roots' text before the first comma
  (435 phrases; a replacement's "If this would die, ..." too). "Deploy" and
  "Deathcry" also read as "when this enters play" and "when this dies", and
  "Shell" as "if damage would be dealt to this".
* "... if C": an ``AbilityEffectConditionTemplate``, named by the run of the
  root's text its ``m_Name`` was made from (482 phrases). "you have N Shard
  threshold" is also built.
* "... for each X": a ``CardCountAbilityVariable`` or ``TriggerEventDamageProperty``
  (``FOR_EACH``), named by its ``m_Name``.
* "... this turn" and the other ``DURATIONS``: ``m_EffectDuration``. The usual
  duration of a shape in Records is not written.
* "(layer N)", "(recalculating targets)", "(keeping targets)" and
  "(new target)": ``m_Layer``, an unusual ``m_RecalculateTargets``, and a clause
  of its own target slot.
* "Settings: ...": the root's costs, uses and flags (``SETTINGS``) and its cost
  targets ("sacrifice T", ``COST_TARGETS``). "Requires: you have N Shard
  threshold": ``m_AbilityCondition``.

Not yet supported, so these abilities stay in the field editor (the first reason
of the 2,211 others): triggers whose cards print no sentence (380); expression
and other computed amounts (about 500 in all); random, copy, play, counterspell
and conscript effects and summons of a random card (about 350); secondary
targets (156) and "if you do" contingencies (114); root conditions other than
thresholds (72); counter costs.

What the round trip ignores
---------------------------

It ignores GUIDs: records are compared by content, so a reused Records target
matches the original's twin. It ignores the presentation fields ``m_Name``,
``m_GameText``, ``m_ActivationGameText`` and ``m_SerializedTAC``: the compiler
writes a name and text from the sentence, and an edit keeps the original's. It
ignores fields at their client default. It also ignores the names of variables:
each read of a variable is compared as the value it holds, so ``"A" = 1`` and
``"1" = 1`` are the same.

Records is never written: ``save_text`` stores through
``card_abilities.save_records`` (its checks, lock and atomic overlay write). The
game server does not read the overlay yet.
"""

from __future__ import annotations

import copy
import json
import re
import threading
import uuid
from collections import Counter, defaultdict
from typing import Any

from paths import RECORDS  # noqa: E402  (also sets sys.path)
import campaign_data  # noqa: E402
import card_abilities as abilities  # noqa: E402
import card_attributes  # noqa: E402
import client_schema  # noqa: E402
from gamedata.records import deserialize_line
import ability_targets  # noqa: E402  (target rows: words and records)  # noqa: E402

ZERO = abilities.ZERO_GUID
NS = "Game.Shared.Mechanics."
ROOT_TYPE = "Reckoning.Game.AbilityTemplate"
CONDITION_TYPE = NS + "Abilities.AbilityEffectConditionTemplate"
INPUT = NS + "Abilities.EffectInputVariable"
CONSTANT = NS + "Abilities.AbilityConstant"
COUNT = NS + "Abilities.CardCountAbilityVariable"
# Variables an amount reads as "for each X": a count of cards, or the damage a
# replacement trigger's event would deal ("for each damage that would be dealt").
FOR_EACH = (COUNT, NS + "Abilities.TriggerEventDamageProperty")
THRESHOLD = NS + "Abilities.Conditions.AbilityControllerHasThresholdAbilityCondition"
# A replacement trigger fires on an event that has not happened yet; its effects
# happen instead (CardWouldEnterZoneEvent, CardWouldDealDamageEvent, ...).
REPLACEMENT_EVENT = re.compile(r"\.\w*Would[A-Z]\w*Event$")
# Root fields a replacement trigger's Records roots agree on, written unsaid with it.
REPLACEMENT_ROOT = ("m_IgnoresChain", "m_TriggerCollectionFlags")
INSTEAD = "instead"
# Fields that only name or describe a record; the round trip does not compare them.
PRESENTATION = ("m_Name", "m_GameText", "m_ActivationGameText", "m_SerializedTAC")
OWN_ID = {"AbilityTemplate": "m_AbilityTemplateId"}
# The root fields the sentences say. Any other non-default root field is unsupported.
DSL_ROOT = ("m_TriggerEventType", "m_TriggerCondition", "m_AbilityEffectList",
            "m_AbilityTargetTemplateIds", "m_Variables")
# Root values the compiler writes when the sentence says nothing (the Records majority:
# 6,222 of 6,232 card roots work from Champions|Warzone).
ROOT_DEFAULTS = {"m_TriggerCollectionFlags": "Champions|Warzone", "m_CastingBehavior": "QuickAction"}

# "Settings:" items, one per root option: (field, words, kind). ``{n}`` is a number.
SETTINGS = (
    ("m_ActivationCost", "cost {n}", "int"),
    ("m_ExhaustsCardOnUse", "exhausts this", "flag"),
    ("m_LifeCost", "life cost {n}", "int"),
    ("m_ChargePointCost", "charge cost {n}", "int"),
    ("m_IsChargePower", "charge power", "flag"),
    ("m_VariableActivationCost", "variable cost", "flag"),
    ("m_UsesPerTurn", "{n} per turn", "int"),
    ("m_UsesPerGame", "{n} per game", "int"),
    ("m_Optional", "optional", "flag"),
    ("m_Manual", "manual", "flag"),
    ("m_IgnoresChain", "ignores the chain", "flag"),
    ("m_UsesPreviousState", "uses the previous state", "flag"),
    ("m_CastingBehavior", "speed {value}", "enum"),
    ("m_TriggerCollectionFlags", "works in {value}", "flags"),
)

# m_EffectDuration -> the words after a clause. A shape's usual duration is unsaid.
DURATIONS = {
    "Instant": "at once", "Permanent": "permanently", "EndOfTurn": "this turn",
    "WhileCardInPlay": "while this is in play",
    "BeginningOfOwnersTurn": "until the start of your next turn",
    "AfterCardsReadyOnPlayersTurn": "until your next ready step",
    "EndOfNextTurn": "until the end of your next turn",
    "WhileCardTapped": "while this is exhausted",
    "UntilAttacksOrBlocks": "until it attacks or blocks",
    "WhileTargetInPlay": "while the target is in play",
    "BeginningOfOpponentsTurn": "until the start of the next opposing turn",
    "EndOfGame": "until the end of the game",
}
# A trigger whose cards print a keyword instead of a sentence: the keyword is accepted
# and the sentence shown (both read from the roots' text in Records).
KEYWORD_TRIGGERS = {"deploy": "when this enters play", "deathcry": "when this dies"}
KEYWORD_RENDER = {trigger: keyword.capitalize() for keyword, trigger in KEYWORD_TRIGGERS.items()}


def _is_keyword_trigger(trigger: str) -> bool:
    """A trigger that is one keyword word ("diligence"), not a sentence ("when ...")."""
    return bool(re.fullmatch(r"[a-z]+", trigger or "")) and trigger not in ("if", "at")
# Trigger sentences Records has no root for: a phase start is a TurnPhaseEvent that names
# its phase (ETurnPhases.Draw), and "each champion's" needs no owner condition.
AUTHORED_TRIGGERS = {
    "at the start of each champion's draw phase": (
        NS + "TurnPhaseEvent", {"_t": NS + "Triggers.Conditions.TurnPhaseCondition", "m_TurnPhase": "Draw"}),
}
# "you may" opens the first clause (the root's m_Optional flag); "If you do," opens a
# clause that runs only if the clause before it was performed (m_ContingentEffectInstanceId).
YOU_MAY = re.compile(r"you may ", re.I)
IF_YOU_DO = re.compile(r"if you do, ", re.I)
NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
           "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
SHARDS = ("Blood", "Diamond", "Ruby", "Sapphire", "Wild")

# ---------------------------------------------------------------- effect shapes
#
# A shape is a sentence and the effect record it stores. In ``record``, "$amount"
# is the name of the root variable that holds the clause's amount, and "$card",
# "$counter" and "$keyword" are the other slots. ``you`` is the sentence used when
# the target is "you"; a shape with only ``you`` needs that target.

_MOD = NS + "Modifiers."
_ABL = NS + "Abilities."
_CM = _ABL + "CardModifierAbilityEffectTemplate"
_AMOUNT = {"_t": INPUT, "m_InputVariableName": "$amount"}


def _modifier(name: str, **fields: Any) -> dict[str, Any]:
    return {"_t": _CM, "m_Modifier": dict({"_t": _MOD + name}, **fields)}


def _move(collection: str, yours: bool) -> dict[str, Any]:
    record = {"_t": _ABL + "MoveCardToZoneEffectTemplate", "m_DestinationCollection": collection,
              "m_DestinationLocation": "Unknown"}
    if yours:
        record["m_AbilityOwnerTakesControl"] = 1
    return record


SHAPES: tuple[dict[str, Any], ...] = (
    {"id": "damage", "label": "Deal damage", "phrase": "deal {amount} damage to {target}",
     "record": _modifier("DamageModifier", m_InputValue=_AMOUNT)},
    {"id": "heal", "label": "Gain health", "phrase": "{target} gains {amount} health",
     "you": "gain {amount} health", "record": _modifier("HealHeroModifier", m_InputValue=_AMOUNT)},
    {"id": "lose_health", "label": "Lose health", "phrase": "{target} loses {amount} health",
     "you": "lose {amount} health", "record": _modifier("LoseLifeModifier", m_InputValue=_AMOUNT)},
    {"id": "set_health", "label": "Set health", "phrase": "{target}'s health becomes {amount}",
     "you": "your health becomes {amount}", "record": _modifier("SetHeroHealthModifier", m_InputValue=_AMOUNT)},
    {"id": "attack", "label": "Change attack", "phrase": "{target} gets {amount} attack", "signed": True,
     "record": _modifier("AttackModifier", m_InputValue=_AMOUNT)},
    {"id": "defense", "label": "Change defense", "phrase": "{target} gets {amount} defense", "signed": True,
     "record": _modifier("DefenseModifier", m_InputValue=_AMOUNT)},
    {"id": "keyword", "label": "Gain a keyword", "phrase": "{target} gets {keyword}",
     "record": _modifier("AttributeModifier", m_AttributeFlags="$keyword", m_Operation="Add")},
    {"id": "lose_keyword", "label": "Lose a keyword", "phrase": "{target} loses {keyword}",
     "record": _modifier("AttributeModifier", m_AttributeFlags="$keyword", m_Operation="Remove")},
    {"id": "draw", "label": "Draw cards", "phrase": "{target} draws {amount} card{s}",
     "you": "draw {amount} card{s}", "article": True,
     "record": {"_t": _ABL + "DrawNCardsAbilityEffectTemplate", "m_InputValue": _AMOUNT}},
    {"id": "charges", "label": "Gain charges", "phrase": "{target} gains {amount} charge{s}",
     "you": "gain {amount} charge{s}", "article": True,
     "record": _modifier("ChargePointsModifier", m_InputValue=_AMOUNT)},
    {"id": "add_counters", "label": "Add counters", "article": True,
     "phrase": "add {amount} {counter} counter{s} to {target}",
     "record": _modifier("CounterModifier", m_InputValue=_AMOUNT,
                         m_CardCounterTemplateId={"m_Guid": "$counter"}, m_Operation="Add")},
    {"id": "remove_counters", "label": "Remove counters", "article": True,
     "phrase": "remove {amount} {counter} counter{s} from {target}",
     "record": _modifier("CounterModifier", m_InputValue=_AMOUNT,
                         m_CardCounterTemplateId={"m_Guid": "$counter"}, m_Operation="Remove")},
    {"id": "summon", "label": "Summon a token", "you": "summon {amount} {card}", "article": True,
     "phrase": "{target} summons {amount} {card}",
     "record": {"_t": _ABL + "SummonTokenTroopAbilityEffectTemplate",
                "m_CardTemplateId": {"m_Guid": "$card"}, "m_CardCollection": "Warzone",
                "m_CardLocation": "Unknown", "m_Amount": 1, "m_AmountField": _AMOUNT}},
    # A random card that matches a filter, named by the words its Records effects use
    # ("random non-unique blood gnoll or wild gnoll"; the ``filters`` vocabulary).
    {"id": "summon_random", "label": "Summon random cards", "article": True,
     "you": "summon {amount} random {filter}", "phrase": "{target} summons {amount} random {filter}",
     "record": {"_t": _ABL + "SummonTokenTroopAbilityEffectTemplate", "m_CardFilter": "$filter",
                "m_CardCollection": "Warzone", "m_CardLocation": "Unknown", "m_Amount": 1, "m_AmountField": _AMOUNT}},
    {"id": "summon_random_hand", "label": "Put random new cards into a hand", "article": True,
     "you": "put {amount} random {filter} into your hand", "phrase": "put {amount} random {filter} into {target}'s hand",
     "record": {"_t": _ABL + "SummonTokenTroopAbilityEffectTemplate", "m_CardFilter": "$filter",
                "m_CardCollection": "Hand", "m_CardLocation": "Unknown", "m_Amount": 1, "m_AmountField": _AMOUNT}},
    {"id": "bury", "label": "Bury from the deck", "article": True,
     "phrase": "bury the top {amount} card{s} of {target}'s deck",
     "you": "bury the top {amount} card{s} of your deck",
     "record": {"_t": _ABL + "BuryCardAbilityEffectTemplate", "m_Amount": _AMOUNT}},
    {"id": "destroy", "label": "Destroy", "phrase": "destroy {target}",
     "record": {"_t": _ABL + "DestroyCardAbilityEffectTemplate"}},
    {"id": "exhaust", "label": "Exhaust", "phrase": "exhaust {target}",
     "record": {"_t": _ABL + "TapCardAbilityEffectTemplate"}},
    {"id": "ready", "label": "Ready", "phrase": "ready {target}",
     "record": {"_t": _ABL + "UntapCardAbilityEffectTemplate"}},
    {"id": "void", "label": "Void", "phrase": "void {target}",
     "record": {"_t": _ABL + "VoidCardAbilityEffectTemplate"}},
    {"id": "discard", "label": "Discard", "phrase": "discard {target}",
     "record": {"_t": _ABL + "DiscardCardAbilityEffectTemplate"}},
    {"id": "sacrifice", "label": "Sacrifice", "phrase": "sacrifice {target}",
     "record": {"_t": _ABL + "SacrificeCardAbilityEffectTemplate"}},
    {"id": "to_your_hand", "label": "Put into your hand", "phrase": "put {target} into your hand",
     "record": _move("Hand", True)},
    {"id": "to_owners_hand", "label": "Return to hand", "phrase": "return {target} to its owner's hand",
     "record": _move("Hand", False)},
    {"id": "to_play", "label": "Put into play", "phrase": "put {target} into play",
     "record": _move("Warzone", False)},
    {"id": "to_your_play", "label": "Put into play under your control",
     "phrase": "put {target} into play under your control", "record": _move("Warzone", True)},
    {"id": "to_your_deck", "label": "Put into your deck", "phrase": "put {target} into your deck",
     "record": _move("Deck", True)},
    {"id": "to_owners_deck", "label": "Put into its owner's deck",
     "phrase": "put {target} into its owner's deck", "record": _move("Deck", False)},
    {"id": "to_your_crypt", "label": "Put into your crypt", "phrase": "put {target} into your crypt",
     "record": _move("Discard", True)},
    {"id": "to_resources", "label": "Put into your resources",
     "phrase": "put {target} into your played resources", "record": _move("PlayedResources", True)},
    {"id": "cost", "label": "Change cost", "phrase": "{target} gets {amount} cost", "signed": True,
     "record": _modifier("CardCostModifier", m_InputValue=_AMOUNT)},
    {"id": "cost_more", "label": "Cost more", "phrase": "{target} cost {amount} more",
     "record": _modifier("CardCostModifier", m_InputValue=_AMOUNT)},
    {"id": "resources", "label": "Change current resources", "signed": True,
     "phrase": "{target} gets {amount} resources", "you": "you get {amount} resources",
     "record": _modifier("CurrentResourceModifier", m_InputValue=_AMOUNT)},
    {"id": "max_resources", "label": "Change maximum resources", "signed": True,
     "phrase": "{target} gets {amount} maximum resources", "you": "you get {amount} maximum resources",
     "record": _modifier("TotalResourceModifier", m_InputValue=_AMOUNT)},
    # Resources set to a number (m_Set), as encounter scripts do: "your resources become 0".
    {"id": "set_resources", "label": "Set current resources", "phrase": "{target}'s resources become {amount}",
     "you": "your resources become {amount}",
     "record": _modifier("CurrentResourceModifier", m_InputValue=_AMOUNT, m_Set=1)},
    {"id": "set_max_resources", "label": "Set maximum resources",
     "phrase": "{target}'s maximum resources become {amount}", "you": "your maximum resources become {amount}",
     "record": _modifier("TotalResourceModifier", m_InputValue=_AMOUNT, m_Set=1)},
    {"id": "threshold", "label": "Gain threshold", "signed": True,
     "phrase": "{target} gets {amount} {shard} threshold", "you": "you get {amount} {shard} threshold",
     "record": _modifier("ThresholdModifier", m_InputValue=_AMOUNT, m_ThresholdColor="$shard")},
    # An on/off card value (card_attributes.flag_attributes, such as Lethal) set to 1.
    {"id": "gets_flag", "label": "Gain an on/off card value", "phrase": "{target} gets {attribute}",
     "you": "you get {attribute}", "flag": True,
     "record": _modifier("IntAttrModifier", m_Attribute="$attribute", m_Operation="Set", m_Value=1)},
    {"id": "set_attribute", "label": "Set a card value", "phrase": "set {target}'s {attribute} to {number}",
     "you": "set your {attribute} to {number}",
     "record": _modifier("IntAttrModifier", m_Attribute="$attribute", m_Operation="Set", m_Value="$number")},
    {"id": "add_attribute", "label": "Add to a card value", "phrase": "add {number} to {target}'s {attribute}",
     "you": "add {number} to your {attribute}",
     "record": _modifier("IntAttrModifier", m_Attribute="$attribute", m_Operation="Add", m_Value="$number")},
    {"id": "summon_hand", "label": "Put a new card into a hand", "article": True,
     "phrase": "put {amount} {card} into {target}'s hand", "you": "put {amount} {card} into your hand",
     "record": {"_t": _ABL + "SummonTokenTroopAbilityEffectTemplate",
                "m_CardTemplateId": {"m_Guid": "$card"}, "m_CardCollection": "Hand",
                "m_CardLocation": "Unknown", "m_Amount": 1, "m_AmountField": _AMOUNT}},
    {"id": "summon_deck", "label": "Shuffle a new card into a deck", "article": True,
     "phrase": "shuffle {amount} {card} into {target}'s deck", "you": "shuffle {amount} {card} into your deck",
     "record": {"_t": _ABL + "SummonTokenTroopAbilityEffectTemplate",
                "m_CardTemplateId": {"m_Guid": "$card"}, "m_CardCollection": "Deck",
                "m_CardLocation": "Unknown", "m_Amount": 1, "m_AmountField": _AMOUNT}},
    {"id": "summon_choice", "label": "Offer a new card to choose", "article": True,
     "phrase": "put {amount} {card} into {target}'s choice zone",
     "you": "put {amount} {card} into the choice zone",
     "record": {"_t": _ABL + "SummonTokenTroopAbilityEffectTemplate",
                "m_CardTemplateId": {"m_Guid": "$card"}, "m_CardCollection": "Choosing",
                "m_CardLocation": "Unknown", "m_Amount": 1, "m_AmountField": _AMOUNT}},
    {"id": "grant", "label": "Grant an ability", "phrase": "{target} gets the ability {ability}",
     "record": {"_t": _ABL + "GrantAbilityEffectTemplate", "m_GrantedAbilityTemplateId": {"m_Guid": "$ability"}}},
    {"id": "grant_unique", "label": "Grant a unique ability", "phrase": "{target} gets the unique ability {ability}",
     "record": {"_t": _ABL + "GrantAbilityEffectTemplate", "m_GrantedAbilityTemplateId": {"m_Guid": "$ability"},
                "m_AbilityIsUnique": 1}},
    {"id": "activate", "label": "Use another ability", "phrase": "use the ability {ability} on {target}",
     "record": {"_t": _ABL + "ActivateAbilityEffectTemplate", "m_AbilityToInvoke": {"m_Guid": "$ability"}}},
    {"id": "create", "label": "Create a card (no zone)", "article": True,
     "phrase": "create {amount} {card} for {target}", "you": "create {amount} {card}",
     "record": {"_t": _ABL + "SummonTokenTroopAbilityEffectTemplate",
                "m_CardTemplateId": {"m_Guid": "$card"}, "m_CardCollection": "None",
                "m_CardLocation": "Unknown", "m_Amount": 1, "m_AmountField": _AMOUNT}},
    {"id": "transform", "label": "Transform", "phrase": "{target} transforms into {card}",
     "you": "you transform into {card}",
     "record": {"_t": _ABL + "TransformCardAbilityEffectTemplate", "m_CardTemplateId": {"m_Guid": "$card"}}},
    {"id": "remember", "label": "Remember a target", "phrase": "remember {target}",
     "record": {"_t": _ABL + "StoreTargetsAbilityEffectTemplate"}},
    {"id": "revert", "label": "Revert changes", "phrase": "revert {target}",
     "record": {"_t": _ABL + "RevertPermanentModificationsAbilityEffectTemplate"}},
    {"id": "reveal", "label": "Reveal to everyone", "phrase": "reveal {target}",
     "record": {"_t": _ABL + "RevealCardsAbilityEffectTemplate", "m_PlayerRevealTargets": "Everyone"}},
    {"id": "look", "label": "Look at", "phrase": "look at {target}",
     "record": {"_t": _ABL + "RevealCardsAbilityEffectTemplate", "m_PlayerRevealTargets": "Self"}},
    {"id": "load_deck", "label": "Shuffle a whole deck in", "phrase": "put the deck {deck} into {target}'s deck",
     "you": "put the deck {deck} into your deck",
     "record": {"_t": _ABL + "LoadPlayerDeckAbilityEffectTemplate", "m_DeckTemplateId": {"m_Guid": "$deck"}}},
    {"id": "conversation", "label": "Start a conversation", "phrase": "start the conversation {conversation} for {target}",
     "you": "start the conversation {conversation}",
     "record": {"_t": _ABL + "ConversationAbilityEffectTemplate", "m_ConversationId": {"m_Guid": "$conversation"}}},
    {"id": "fire_event", "label": "Fire an event", "phrase": "fire the {event} event for {target}",
     "you": "fire the {event} event",
     "record": {"_t": _ABL + "FireEventEffectTemplate", "m_TriggerType": "$event"}},
    # Play a card for free (the card the target names). m_JustActivate is the one field the
    # sentence does not say, so a template that sets it does not match.
    {"id": "play", "label": "Play for free", "phrase": "play {target} for free",
     "record": {"_t": _ABL + "PlayCardAbilityEffectTemplate", "m_JustActivate": 0}},
)
SHAPE_BY_ID = {shape["id"]: shape for shape in SHAPES}

_LOCK = threading.Lock()
_CATALOGUE: dict[str, Any] = {}


class Unsupported(ValueError):
    """An ability, or a sentence, the DSL cannot express; ``reasons`` lists why."""

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons[:4]) + (" ..." if len(reasons) > 4 else ""))


# ---------------------------------------------------------------- normal form

def _default(spec: dict[str, str]) -> Any:
    return _canon(client_schema.default_value(spec), spec["type"])


def _canon(value: Any, declared: str = "") -> Any:
    """``value`` with every field at its client default dropped, and every empty GUID
    written as ``{m_Guid: 0...}``: two records that the client loads alike compare equal."""
    if isinstance(value, list):
        item = re.fullmatch(r"List<(\w+)>", declared or "")
        return [_canon(entry, item.group(1) if item else "") for entry in value]
    if not isinstance(value, dict):
        return value
    if "m_Guid" in value and set(value) <= {"m_Guid", "_t"}:
        return {"m_Guid": str(value["m_Guid"] or ZERO).lower()}
    type_name = str(value.get("_t") or declared or "")
    spec = client_schema.fields(type_name) if type_name and "<" not in type_name else {}
    out = {}
    for key, item in value.items():
        if key == "_v" or item is None:
            continue
        if key in spec:
            item = _canon(item, spec[key]["type"])
            default = _default(spec[key])
            if item == default or (item in ([], {}) and default in (None, [], {})):
                continue
        else:
            item = _canon(item)
        out[key] = item
    return out


def _own_field(section: str) -> str:
    return OWN_ID.get(section, "m_TemplateId")


def _content(section: str, record: dict[str, Any]) -> dict[str, Any]:
    """A record's normal form without its own GUID and presentation fields."""
    out = _canon(record)
    for field in (_own_field(section),) + PRESENTATION:
        out.pop(field, None)
    return out


def _unordered(value: Any) -> Any:
    """A value with the parts of every AND or OR filter sorted: those parts are unordered."""
    if isinstance(value, dict):
        out = {key: _unordered(item) for key, item in value.items()}
        if isinstance(out.get("m_TargetFilters"), list):
            out["m_TargetFilters"] = sorted((_unordered(x) for x in out["m_TargetFilters"]),
                                            key=lambda x: json.dumps(x, sort_keys=True))
        return out
    if isinstance(value, list):
        return [_unordered(item) for item in value]
    return value


def _key(value: Any) -> str:
    return json.dumps(_unordered(value), sort_keys=True)


def _variable_uses(value: Any, out: list[str]) -> list[str]:
    """The variable names an effect uses: what it reads (inputs) and what it sets (randomized
    or set variables, by ``m_VariableName``)."""
    if isinstance(value, dict):
        if value.get("m_VariableName"):
            out.append(str(value["m_VariableName"]))
        out.extend(_input_names(value, []))
        for item in value.values():
            _variable_uses(item, out)
    elif isinstance(value, list):
        for item in value:
            _variable_uses(item, out)
    return out


def _input_names(value: Any, out: list[str]) -> list[str]:
    """The variable names an effect reads, in order."""
    if isinstance(value, dict):
        if value.get("_t") == INPUT and value.get("m_InputVariableName"):
            out.append(str(value["m_InputVariableName"]))
        for item in value.values():
            _input_names(item, out)
    elif isinstance(value, list):
        for item in value:
            _input_names(item, out)
    return out


def _rename(value: Any, names: dict[str, str]) -> Any:
    if isinstance(value, dict):
        out = {k: _rename(v, names) for k, v in value.items()}
        if out.get("_t") == INPUT and out.get("m_InputVariableName") in names:
            out["m_InputVariableName"] = names[out["m_InputVariableName"]]
        return out
    if isinstance(value, list):
        return [_rename(item, names) for item in value]
    return value


# ---------------------------------------------------------------- text helpers

# The client's text placeholders, as words.
PLACEHOLDERS = {"#self#": "this", "#trigger_source#": "the trigger source",
                "#trigger_target#": "the trigger target", "#created_cards#": "the created cards",
                "#stored_target#": "the stored target", "#stored_targets#": "the stored targets",
                "#previous_target#": "the previous target", "#drawn_cards#": "the drawn cards",
                "#comma#": ","}


def _clean(text: Any) -> str:
    """Display text: tags, card links and placeholders out, lower case, single spaces."""
    text = re.sub(r"<[^>]+>", "", str(text or "")).replace('"', "'")
    text = re.sub(r"#\w+#", lambda found: PLACEHOLDERS.get(found.group(0).lower(), found.group(0)), text)
    text = re.sub(r"\s+", " ", text).strip().rstrip(".").strip()
    return text.lower()


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _phrase_in_text(name: str, text: str) -> str:
    """The run of words in ``text`` that a record's CamelCase ``m_Name`` was made from
    ("YouControlNoBcandlekinb" -> "you control no Candlekin"), or ""."""
    want = _squash(name)
    words = str(text or "").split()
    if not want:
        return ""
    for start in range(len(words)):
        built = ""
        for end in range(start, len(words)):
            built += _squash(words[end])
            if built == want:
                return _clean(" ".join(words[start:end + 1]).strip(",.;:"))
            if not want.startswith(built):
                break
    return ""


def _decamel(name: str) -> str:
    return " ".join(w.lower() for w in re.findall(r"[A-Z][a-z']*|[a-z']+|\d+", name or ""))


def camel(text: str) -> str:
    """A record name in the Records style: "Deal 2 damage" -> "Deal2Damage"."""
    text = re.sub(r"<[^>]+>", "", text).replace("+", " P").replace("-", " M")
    return "".join(word[:1].upper() + word[1:] for word in re.findall(r"[A-Za-z0-9]+", text))


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:] + ("" if text.endswith(".") else ".")


# ---------------------------------------------------------------- the catalogue

def _iter(section: str):
    return abilities._iter_records(section)


def _names_from_file(section: str, own: str) -> dict[str, str]:
    """``{GUID: m_Name}`` of a Records file whose records carry their id in ``own``."""
    out = {}
    path = RECORDS / f"{section}.jsonl"
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if '"_t' not in line:
                continue
            try:
                record = deserialize_line(line).to_dict()
            except Exception:   # a few Records lines do not decode; they name nothing here
                continue
            guid = str((record.get(own) or {}).get("m_Guid") or "").lower()
            if guid:
                out[guid] = str(record.get("m_Name") or "")
    return out


def _names_from_text(section: str, field: str = "m_Name") -> dict[str, str]:
    """``{m_Id GUID: name}`` of a Records file read as text (``field`` holds the name):
    most ConversationTemplate and some ChampionTemplate lines do not decode, but name
    themselves plainly."""
    out = {}
    path = RECORDS / f"{section}.jsonl"
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if '"_t' not in line:
                continue
            try:
                text = json.loads(line)
            except ValueError:
                continue
            guid = re.search(r'"m_Id"\s*:\s*\{\s*"m_Guid"\s*:\s*"([0-9a-fA-F-]{36})"', text)
            name = re.search(rf'"{field}"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
            if guid and name:
                out[guid.group(1).lower()] = json.loads(f'"{name.group(1)}"')
    return out


class _Vocabulary:
    """Phrases for one kind of record content: ``phrase -> key`` (a shared phrase names the
    content that uses it most), ``key -> display phrase``, and the Records GUID to reuse."""

    def __init__(self) -> None:
        self.weights: dict[str, Counter] = defaultdict(Counter)   # key -> phrase weights
        self.keys: dict[str, set[str]] = defaultdict(set)          # phrase -> keys
        self.guids: dict[str, Counter] = defaultdict(Counter)      # key -> GUID uses
        self.content: dict[str, Any] = {}
        self.uses: Counter = Counter()                              # key -> root uses

    def add(self, key: str, content: Any, phrase: str, weight: int = 1, guid: str = "") -> None:
        self.content.setdefault(key, content)
        if phrase:
            self.weights[key][phrase] += weight
            self.keys[phrase].add(key)
        if guid:
            self.guids[key][guid] += weight

    def finish(self) -> None:
        # A phrase several contents share belongs to the one that uses it most.
        self.by_phrase = {p: max(sorted(k), key=lambda key: self.weights[key][p])
                          for p, k in self.keys.items()}
        self.display = {}
        for key, weights in self.weights.items():
            for phrase, _ in weights.most_common():
                if "#" not in phrase and self.by_phrase.get(phrase) == key:
                    self.display[key] = phrase
                    break

    def guid(self, key: str) -> str:
        return self.guids[key].most_common(1)[0][0] if self.guids.get(key) else ""

    def listing(self) -> list[dict[str, Any]]:
        rows = [{"phrase": phrase, "uses": self.uses.get(key, 0)}
                for key, phrase in self.display.items()]
        return sorted(rows, key=lambda row: (-row["uses"], row["phrase"]))


def _trigger_phrase(text: str, replacement: bool = False) -> str:
    """The trigger words that open a root's text. A replacement's text opens "if ...
    would ...," ("if this would die, void it instead")."""
    text = _clean(text)
    found = re.match(r"^((?:when|whenever|at|after)\b[^,]*),", text)
    if found:
        return found.group(1)
    found = re.match(r"^(if [^,:]*\bwould\b[^,:]*),", text) if replacement else None
    if found:
        return found.group(1)
    found = re.match(r"^([a-z][a-z' ]{2,24}) - ", text) or re.fullmatch(r"([a-z]+(?: [a-z]+)?)", text)
    return found.group(1) if found else ""   # a keyword ("deploy", "inspire")


def _filter_phrase(text: Any) -> str:
    """The card words of a random summon's text: "Summon three random Insects." ->
    "insects"; "Create a random ruby action and put it into your hand." -> "ruby action"."""
    found = re.search(r"\brandom (.+?)(?: and puts? (?:it|them) into [\w' ]+)?$", _clean(text))
    return found.group(1).strip() if found and '"' not in found.group(1) else ""


def _threshold_phrase(content: dict[str, Any]) -> str:
    """"you have N Shard threshold" for a single threshold condition, else ""."""
    condition = content.get("m_Condition") or {}
    if set(content) != {"_t", "m_Condition"} or condition.get("_t") != THRESHOLD:
        return ""
    if condition.get("m_ComparisonOp") != "GreaterThanOrEqual" or \
            condition.get("m_ColorFlags") not in SHARDS:
        return ""
    if set(condition) - {"_t", "m_ColorFlags", "m_RequiredQuantity", "m_ComparisonOp"}:
        return ""
    return f"you have {condition.get('m_RequiredQuantity', 0)} {condition['m_ColorFlags'].lower()} threshold"


def _effect_shape_key(content: dict[str, Any]) -> tuple[str, list[str]]:
    """An effect's content with its variable names blanked, and those names in order."""
    names = list(dict.fromkeys(_input_names(content, [])))
    return _key(_rename(content, dict.fromkeys(names, "$"))), names


def catalogue() -> dict[str, Any]:
    """Every phrase the DSL knows, read once from Records (under a lock)."""
    with _LOCK:
        if _CATALOGUE:
            return _CATALOGUE
        effects = {campaign_data.ability_record_guid("AbilityEffectTemplate", r): r
                   for r in _iter("AbilityEffectTemplate")}
        conditions = {campaign_data.ability_record_guid("AbilityEffectConditionTemplate", r): r
                      for r in _iter("AbilityEffectConditionTemplate")}
        targets = {campaign_data.ability_record_guid("AbilityTargetTemplate", r): r
                   for r in _iter("AbilityTargetTemplate")}
        effect_content = {guid: _content("AbilityEffectTemplate", r) for guid, r in effects.items()}
        effect_shape = {guid: (_shape_of(content) or ({"id": ""}, {}))[0]["id"]
                        for guid, content in effect_content.items()}
        target_uses, effect_uses, condition_text = Counter(), Counter(), defaultdict(Counter)
        triggers, counts = _Vocabulary(), _Vocabulary()
        summoned, durations, invoked, counted = Counter(), defaultdict(Counter), Counter(), Counter()
        conversed, decked = Counter(), Counter()
        ability_names: dict[str, str] = {}
        attributes = {str(((r.get("m_Modifier") or {}).get("m_Attribute")) or "") for r in effects.values()} - {""}
        events = {str(r.get("m_TriggerType") or "") for r in effects.values()
                  if str(r.get("_t", "")).endswith("FireEventEffectTemplate")} - {""}
        shape_targets: dict[str, Counter] = defaultdict(Counter)   # shape -> target GUID uses
        replacement_roots: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
        for root in _iter("AbilityTemplate"):
            text = str(root.get("m_GameText") or "")
            own = campaign_data.ability_record_guid("AbilityTemplate", root)
            ability_names[own] = _clean(text) or _decamel(str(root.get("m_Name") or ""))
            root_targets = [str(target.get("m_Guid") or "").lower()
                            for target in root.get("m_AbilityTargetTemplateIds") or []]
            target_uses.update(root_targets)
            for entry in root.get("m_AbilityEffectList") or []:
                effect = str((entry.get("m_EffectTemplateId") or {}).get("m_Guid") or "").lower()
                effect_uses[effect] += 1
                if effect_shape.get(effect):
                    durations[effect_shape[effect]][entry.get("m_EffectDuration") or "Instant"] += 1
                    position = entry.get("m_TargetTemplateIndex", 0)
                    if isinstance(position, int) and 0 <= position < len(root_targets):
                        shape_targets[effect_shape[effect]][root_targets[position]] += 1
                for field, tally in (("m_CardTemplateId", summoned), ("m_GrantedAbilityTemplateId", invoked),
                                     ("m_AbilityToInvoke", invoked), ("m_ConversationId", conversed),
                                     ("m_DeckTemplateId", decked)):
                    linked = (effects.get(effect) or {}).get(field)
                    if isinstance(linked, dict) and linked.get("m_Guid"):
                        tally[str(linked["m_Guid"]).lower()] += 1
                counter = ((effects.get(effect) or {}).get("m_Modifier") or {}).get("m_CardCounterTemplateId")
                if isinstance(counter, dict) and counter.get("m_Guid"):
                    counted[str(counter["m_Guid"]).lower()] += 1
                condition = str((entry.get("m_ConditionId") or {}).get("m_Guid") or "").lower()
                if condition in conditions:
                    name = str(conditions[condition].get("m_Name") or "")
                    condition_text[condition][_phrase_in_text(name, text) or _decamel(name)] += 1
            event = (root.get("m_TriggerEventType") or {}).get("m_InternalType")
            if event or root.get("m_TriggerCondition"):
                content = {"event": event, "condition": _canon(root.get("m_TriggerCondition"))}
                key = _key(content)
                replacement = bool(REPLACEMENT_EVENT.search(str(event or "")))
                triggers.add(key, content, _trigger_phrase(text, replacement))
                triggers.uses[key] += 1
                for field in REPLACEMENT_ROOT if replacement else ():
                    replacement_roots[key][field][_canon(root.get(field))] += 1
            for variable in root.get("m_Variables") or []:
                if variable.get("_t") not in FOR_EACH:
                    continue
                content = _canon(variable)
                name = str(content.pop("m_Name", "") or "")
                tail = name.split("ForEach", 1)[1] if "ForEach" in name else ""
                phrase = _phrase_in_text(tail, text) or _decamel(tail)
                key = _key(content)
                counts.add(key, content, phrase)
                counts.uses[key] += 1
        for phrase, (event, condition) in AUTHORED_TRIGGERS.items():
            content = {"event": event, "condition": condition}
            triggers.add(_key(content), content, phrase)
        for key, phrase, weight in list(_vocabulary_aliases(triggers)):
            triggers.add(key, triggers.content[key], phrase, weight + 1)   # the sentence wins
        for key in replacement_roots:
            # A replacement's "if ... would ..." sentence wins over a keyword its cards
            # print instead (Shell is "if damage would be dealt to this").
            weights = triggers.weights[key]
            for phrase in [p for p in weights if p.startswith("if ")]:
                triggers.add(key, triggers.content[key], phrase, sum(weights.values()))
        target_vocab = _Vocabulary()
        for guid, record in targets.items():
            content = _content("AbilityTargetTemplate", record)
            key = _key(content)
            target_vocab.add(key, content, _clean(record.get("m_GameText")),
                             target_uses[guid] + 1, guid)
            target_vocab.uses[key] += target_uses[guid]
        condition_vocab = _Vocabulary()
        for guid, record in conditions.items():
            content = _content("AbilityEffectConditionTemplate", record)
            key = _key(content)
            built = _threshold_phrase(content)
            phrases = condition_text.get(guid) or Counter({_decamel(record.get("m_Name") or ""): 0})
            for phrase, weight in phrases.items():
                condition_vocab.add(key, content, phrase, weight + 1, guid)
            if built:   # the built phrase wins the display
                condition_vocab.add(key, content, built, 10 ** 6, guid)
            condition_vocab.uses[key] += sum(phrases.values())
        # A random summon's card filter, named by its effects' words after "random".
        filter_vocab = _Vocabulary()
        for guid, content in effect_content.items():
            # A filter that reads a root variable ("cost X or less") is not a fixed phrase.
            if effect_shape.get(guid) in ("summon_random", "summon_random_hand") and \
                    not _input_names(content["m_CardFilter"], []):
                key = _key(content["m_CardFilter"])
                filter_vocab.add(key, content["m_CardFilter"], _filter_phrase(effects[guid].get("m_GameText")),
                                 effect_uses[guid] + 1)
                filter_vocab.uses[key] += effect_uses[guid]
        effect_index: dict[str, list[tuple[int, str, list[str]]]] = defaultdict(list)
        for guid, content in effect_content.items():
            shape_key, names = _effect_shape_key(content)
            effect_index[shape_key].append((effect_uses[guid], guid, names))
        for rows in effect_index.values():
            rows.sort(key=lambda row: (-row[0], row[1]))
        for vocab in (triggers, counts, target_vocab, condition_vocab, filter_vocab):
            vocab.finish()
        card_names = {guid: entry["name"] for guid, entry in abilities._records_index("CardTemplate").items()}
        # A champion is a card too: an encounter's champion "transforms into" one.
        champions = {guid: name for guid, name in _names_from_text("ChampionTemplate").items()
                     if guid not in card_names}
        counter_names = _names_from_file("CardCounterTemplate", "m_CardCounterId")
        # The builder orders a shape's targets by how often Records uses them with it.
        target_key = {guid: _key(_content("AbilityTargetTemplate", record)) for guid, record in targets.items()}
        by_shape = {shape: Counter() for shape in shape_targets}
        for shape, found in shape_targets.items():
            for guid, uses in found.items():
                if guid in target_key:
                    by_shape[shape][target_key[guid]] += uses
        _CATALOGUE.update(
            targets=target_vocab, shape_targets=by_shape, triggers=triggers, conditions=condition_vocab, counts=counts,
            effects=effect_index,
            durations={shape: found.most_common(1)[0][0] for shape, found in durations.items()},
            cards=_names("card", dict(card_names, **champions), summoned, later=set(champions)),
            counters=_names("counter", counter_names, counted),
            abilities=_names("ability", ability_names, invoked), attributes=sorted(attributes),
            conversations=_names("conversation", _names_from_text("ConversationTemplate"), conversed),
            decks=_names("deck", _names_from_text("DeckTemplate", "m_DeckName"), decked),
            filters=filter_vocab,
            events=sorted(events),
            # Replacement trigger -> the root values its Records roots mostly hold (all
            # 38 Records replacement roots ignore the chain; a card's own "would enter
            # a zone" works from every zone).
            trigger_defaults={key: {field: found.most_common(1)[0][0] for field, found in fields.items()}
                              for key, fields in replacement_roots.items()},
            keywords=(client_schema.enum("ECardAttributes") or {"values": []})["values"])
        return _CATALOGUE


def _trigger_key(trigger: str) -> str:
    return catalogue()["triggers"].by_phrase.get(trigger or "", "")


def is_replacement(trigger: str) -> bool:
    """Whether a trigger phrase is a replacement ("if this would die"): its last clause
    ends "instead"."""
    key = _trigger_key(trigger)
    event = catalogue()["triggers"].content[key]["event"] if key else ""
    return bool(REPLACEMENT_EVENT.search(str(event or "")))


def _root_defaults(trigger: str) -> dict[str, Any]:
    """The root values written unsaid: ``ROOT_DEFAULTS``, and for a replacement trigger
    the values its Records roots hold (``trigger_defaults``)."""
    found = catalogue()["trigger_defaults"].get(_trigger_key(trigger)) or {}
    return dict(ROOT_DEFAULTS, **{field: value for field, value in found.items() if value is not None})


def _vocabulary_aliases(triggers: _Vocabulary):
    """A keyword trigger (Deploy, Deathcry) is also known by its sentence."""
    for key, weights in triggers.weights.items():
        for phrase in list(weights):
            if phrase in KEYWORD_TRIGGERS:
                yield key, KEYWORD_TRIGGERS[phrase], weights[phrase]


def _names(kind: str, names: dict[str, str], uses: Counter, later: set[str] = frozenset()) -> dict[str, Any]:
    """A name lookup for cards or counters. A name several records share resolves to the
    most-used one (on a tie, not one of ``later``); the others are written
    ``"Name" [guid prefix]``."""
    by_name: dict[str, list[str]] = defaultdict(list)
    for guid, name in names.items():
        if name:
            by_name[name.lower()].append(guid)
    for guids in by_name.values():
        guids.sort(key=lambda guid: (-uses[guid], guid in later, guid))
    return {"kind": kind, "name": names, "by_name": dict(by_name), "uses": uses}


def _name_slot(table: dict[str, Any], guid: str) -> str:
    name = table["name"].get(guid, "")
    if not name:
        raise Unsupported([f"no Records {table['kind']} {guid}"])
    first = table["by_name"][name.lower()][0]
    return f'"{name}"' if first == guid else f'"{name}" [{guid[:8]}]'


def _name_lookup(table: dict[str, Any], name: str, prefix: str) -> str:
    guids = table["by_name"].get(re.sub(r"\s+", " ", name.lower().strip()), [])
    if prefix:
        guids = [guid for guid in guids if guid.startswith(prefix.lower())]
    if not guids:
        raise ValueError(f"no {table['kind']} named {name!r}" + (f" [{prefix}]" if prefix else ""))
    return guids[0]


# ---------------------------------------------------------------- decompile

# Cost targets a root names directly; "Settings: sacrifice another troop you control".
COST_TARGETS = (("m_SacrificeTarget", "sacrifice"), ("m_VoidTarget", "void"),
                ("m_ExhaustTarget", "exhaust"), ("m_DiscardTargets", "discard"))
AND_CONDITION = NS + "Abilities.AndAbilityCondition"
RECALCULATE = {"True": "recalculating targets", "False": "never recalculating targets",
               "UseDefault": "keeping targets"}
# A clause shares the latest target slot of an earlier clause with the same target
# (Records' way for chosen targets: 536 of 539); "(new target)" gives it its own slot.
NEW_TARGET = "new target"
# Slots a sentence shape can have: the regular expression that reads one.
_NAMED = r'"(?P<{0}>[^"]+)"(?:\s*\[(?P<{0}_id>[0-9a-f]{{1,32}})\])?'
SLOT_PATTERNS = {
    "target": r"(?P<target>.+?)",
    "amount": r"(?P<amount>[+-]?\d+|an?|one|two|three|four|five|six|seven|eight|nine|ten)",
    "card": _NAMED.format("card"), "counter": _NAMED.format("counter"),
    "ability": _NAMED.format("ability"), "conversation": _NAMED.format("conversation"),
    "deck": _NAMED.format("deck"),
    # A keyword is a member name or its ToolName ("Crush", "Swift Strike", "Can't Block").
    "keyword": r"(?P<keyword>[a-z][a-z' -]*?(?: and [a-z][a-z' -]*?)*)",
    "filter": r"(?P<filter>[^\"]+?)",
    "shard": r"(?P<shard>blood|diamond|ruby|sapphire|wild)",
    "attribute": r"(?P<attribute>[a-z0-9>_]+)",
    "number": r"(?P<number>-?\d+)",
    "event": r"(?P<event>[a-z]+)",
    "s": r"s?",
}
NAME_TABLES = {"card": "cards", "counter": "counters", "ability": "abilities", "conversation": "conversations",
               "deck": "decks"}


# ---------------------------------------------------------------- built targets
#
# A target phrase Records lacks can be built when it follows Records' own encoding of
# "<target|each> [opposing] <noun> [you control]" (checked against every such phrase
# Records has: ``built_target_mismatches``). Word order is filter order.

_TT = NS + "Abilities.TargetTemplates."
_CF = NS + "Cards.Filters."
_ALL_ZONES = "Deck|Hand|Champions|Warzone|Discard|Void|CastSpells|Underground|Choosing"
_ALL_TYPES = "Troop|BasicAction|Resource|Artifact|QuickAction|Constant|Bane|Choice"
TARGET_NOUNS = {   # noun -> (card filter, collections)
    "troop": ({"_t": _CF + "IsTroop"}, "Warzone"),
    "champion": ({"_t": _CF + "IsHero"}, _ALL_ZONES),
    "artifact": ({"_t": _CF + "IsArtifact"}, "Warzone"),
    "card": ({"_t": _CF + "IsType", "m_CardType": _ALL_TYPES}, "Warzone"),
    "constant": ({"_t": _CF + "IsType", "m_CardType": "Constant"}, "Warzone"),
}
_BUILT = re.compile(r"(target|each) (opposing )?(" + "|".join(TARGET_NOUNS) + r")( you control)?")
# "your <noun> in all zones": the Records form ("your elementals in all zones"), for the
# nouns that have one. The filter is the noun's, with IsControlledBy first, as Records has it.
ALL_ZONE_NOUNS = {"actions": {"_t": _CF + "IsType", "m_CardType": "BasicAction|QuickAction"}}
_ALL_ZONE = re.compile(r"your (" + "|".join(ALL_ZONE_NOUNS) + r") in all zones")


def _build_target(phrase: str) -> dict[str, Any] | None:
    """The target content for a phrase of the built grammar, or None."""
    all_zones = _ALL_ZONE.fullmatch(phrase)
    if all_zones:
        filters = [{"_t": _CF + "IsControlledBy"}, ALL_ZONE_NOUNS[all_zones.group(1)]]
        return _canon({"_t": _TT + "AbilityTargetTemplate", "m_IsAutoTarget": 1,
                       "m_CardFilter": {"_t": _CF + "AndCardFilter", "m_TargetFilters": filters},
                       "m_PlayerFilter": "MultiplePlayers", "m_CollectionFlags": _ALL_ZONES})
    found = _BUILT.fullmatch(phrase)
    if not found or (found.group(2) and found.group(4)):
        return None
    noun, zones = TARGET_NOUNS[found.group(3)]
    filters = ([{"_t": _CF + "IsNotControlledBy"}] if found.group(2) else []) + [noun] + \
        ([{"_t": _CF + "IsControlledBy"}] if found.group(4) else [])
    record: dict[str, Any] = {
        "_t": _TT + "AbilityTargetTemplate", "m_CollectionFlags": zones,
        "m_CardFilter": filters[0] if len(filters) == 1 else {"_t": _CF + "AndCardFilter", "m_TargetFilters": filters}}
    if found.group(1) == "target":
        one = {"_t": _TT + "TargetConstant", "m_Value": 1}
        record.update(m_PlayerFilter="SinglePlayer", m_Explicit=1, m_MinTargetCount=one, m_MaxTargetCount=dict(one))
    else:
        record.update(m_PlayerFilter="MultiplePlayers", m_IsAutoTarget=1)
    return _canon(record)


def built_target_mismatches() -> list[str]:
    """Built phrases whose Records target differs from the built one (should be none)."""
    vocab = catalogue()["targets"]
    out = []
    for quantity in ("target", "each"):
        for side in ("", "opposing "):
            for noun in TARGET_NOUNS:
                for control in ("", " you control"):
                    phrase = f"{quantity} {side}{noun}{control}"
                    built = _build_target(phrase)
                    if built is not None and phrase in vocab.by_phrase and \
                            _key(vocab.content[vocab.by_phrase[phrase]]) != _key(built):
                        out.append(phrase)
    return out


def _named_content(properties: dict[str, Any]) -> dict[str, Any]:
    """The content of the target record a row's properties describe (what Records compares)."""
    record = ability_targets.target_record("row", properties)
    return _content("AbilityTargetTemplate", record)


def _target_content(phrase: str) -> dict[str, Any] | None:
    """A target phrase's content: Records' own, else a built one."""
    vocab = catalogue()["targets"]
    if phrase in vocab.by_phrase:
        return vocab.content[vocab.by_phrase[phrase]]
    return _build_target(phrase)


def _record(section: str, guid: str, pending: dict | None = None,
            overlay: dict | None = None) -> dict[str, Any] | None:
    if pending and guid in pending.get(section, {}):
        return pending[section][guid]
    return abilities.lookup(section, guid, overlay)[0]


def _unify(template: Any, value: Any, slots: dict[str, Any]) -> bool:
    if isinstance(template, str) and template.startswith("$"):
        if template in slots and slots[template] != value:
            return False
        slots[template] = value
        return True
    if isinstance(template, dict):
        return isinstance(value, dict) and set(template) == set(value) and \
            all(_unify(template[k], value[k], slots) for k in template)
    if isinstance(template, list):
        return isinstance(value, list) and len(template) == len(value) and \
            all(_unify(t, v, slots) for t, v in zip(template, value))
    return template == value


_SHAPE_CANON: list[tuple[dict[str, Any], Any]] = []


def _shape_of(content: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The sentence shape an effect's content matches, with its slot values."""
    if not _SHAPE_CANON:
        _SHAPE_CANON.extend((shape, _canon(shape["record"])) for shape in SHAPES)
    for shape, template in _SHAPE_CANON:
        slots: dict[str, Any] = {}
        if _unify(template, content, slots) and _shape_accepts(shape, slots.get("$attribute")):
            return shape, slots
    return None


def _shape_accepts(shape: dict[str, Any], attribute: Any) -> bool:
    """Whether a shape takes this card value: an on/off shape only takes the values
    ``card_attributes.flag_attributes`` names ("gets Lethal", not "gets Rage")."""
    return not shape.get("flag") or str(attribute or "") in card_attributes.flag_attributes()


def _usual_durations() -> dict[str, str]:
    """Each shape's most common duration in Records: that one is left unsaid."""
    return catalogue()["durations"]


def _amount_text(shape: dict[str, Any], value: int) -> str:
    if shape.get("signed"):
        return f"{value:+d}"
    if shape.get("article") and value == 1:
        return "a"
    return str(value)


_WORD_AMOUNTS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")


def _render_clause(shape: dict[str, Any], clause: dict[str, Any], you: bool, instead: bool = False) -> str:
    phrase = shape.get("you") if you and shape.get("you") else shape["phrase"]
    amount = str(clause.get("amount") or "")
    if "{amount} card" in phrase and amount.isdigit() and 2 <= int(amount) <= 10:
        # Game text spells a small amount out before "cards" ("draw three cards")
        clause = dict(clause, amount=_WORD_AMOUNTS[int(amount)])
    amount = str(clause.get("amount") or "")
    text = phrase.replace("{target}", clause.get("target", ""))
    text = text.replace("{s}", "" if amount in ("a", "an", "1", "+1", "one") else "s")
    for slot in ("amount", "card", "counter", "ability", "conversation", "deck", "keyword", "shard", "attribute",
                 "number", "event", "filter"):
        text = text.replace("{" + slot + "}", str(clause.get(slot, "")))
    if clause.get("count"):
        text += " for each " + clause["count"]
    if clause.get("duration"):
        text += " " + clause["duration"]
    if clause.get("condition"):
        text += " if " + clause["condition"]
    if instead:   # a replacement's last clause
        text += " " + INSTEAD
    for suffix in ("layer", "recalculate"):   # notes in brackets come last
        if clause.get(suffix) not in ("", None, 0):
            text += f" ({'layer ' if suffix == 'layer' else ''}{clause[suffix]})"
    if clause.get("new_target"):
        text += f" ({NEW_TARGET})"
    return text


def render(parsed: dict[str, Any]) -> str:
    """The DSL text of a parsed ability."""
    catalogue()
    lines = []
    clauses = parsed.get("clauses") or []
    settings = list(parsed.get("settings") or [])
    may = bool(clauses) and clauses[0]["target"] == "you" and "optional" in settings
    if may:
        settings.remove("optional")
    if settings:
        lines.append("Settings: " + ", ".join(settings))
    if parsed.get("requires"):
        lines.append("Requires: " + parsed["requires"] + ".")
    if parsed.get("named_targets"):
        lines.append("Targets: " + ability_targets.render_definitions(parsed["named_targets"]) + ".")
    instead = bool(parsed.get("trigger")) and is_replacement(parsed["trigger"])
    for index, clause in enumerate(clauses):
        shape = SHAPE_BY_ID[clause["shape"]]
        text = _render_clause(shape, clause, clause["target"] == "you", instead and index == len(clauses) - 1)
        if index == 0 and may:
            text = "you may " + text
        if index == 0 and parsed.get("trigger"):
            trigger = parsed["trigger"]
            keyword = KEYWORD_RENDER.get(trigger) or (
                trigger[:1].upper() + trigger[1:] if _is_keyword_trigger(trigger) else "")
            # Game text names keyword triggers by keyword ("Deploy - draw a card.")
            text = f"{keyword} - {text}" if keyword and not may else trigger + ", " + text
        elif clause.get("contingent"):
            text = "if you do, " + text
        elif index:
            text = "then " + text
        lines.append(_sentence(text))
    if not clauses:
        nothing = "do nothing" + (" " + INSTEAD if instead else "")
        lines.append(_sentence(parsed["trigger"] + ", " + nothing) if parsed.get("trigger") else "Do nothing.")
    return "\n".join(lines)


def decompile(guid: str) -> dict[str, Any]:
    """An ability's sentences, the structure the form shows, and whether compiling the
    sentences gives the same records back (``exact``)."""
    guid = abilities._guid(guid)
    root = _record("AbilityTemplate", guid, overlay=campaign_data.load_ability_overlay())
    if root is None:
        raise LookupError(f"no ability {guid}")
    return describe_root(guid, root)


def describe_root(guid: str, root: dict[str, Any]) -> dict[str, Any]:
    """``decompile`` for a root record already in hand (the editor's tree, unsaved). Targets
    that can be written as words are read as named rows; if those rows do not compile back
    to the same records, the ability is described with its phrases instead."""
    overlay = campaign_data.load_ability_overlay()
    try:
        parsed = _read_root(root, overlay)
    except Unsupported as error:
        return {"guid": guid, "supported": False, "exact": False, "text": "",
                "reasons": error.reasons, "parsed": None}
    first_reasons: list[str] = []
    for candidate in (_with_target_rows(parsed, root, overlay), parsed):
        text = render(candidate)
        try:
            compiled = compile_text(text)
            differences = _differences(_tree(root, None, overlay),
                                       _tree(compiled["root"], compiled["pending"], overlay))
        except ValueError as error:
            first_reasons.append(f"the sentences do not compile back: {error}")
            continue
        if not differences:
            return {"guid": guid, "supported": True, "exact": True, "text": text,
                    "parsed": candidate, "reasons": []}
        first_reasons.append(f"differs at {differences[0]}")
    return {"guid": guid, "supported": True, "exact": False, "text": text, "parsed": parsed,
            "reasons": first_reasons[-2:]}




def _with_target_rows(parsed: dict[str, Any], root: dict[str, Any], overlay: dict) -> dict[str, Any]:
    """The same ability with each clause's target named as a row, where the target can be
    written as words and the row is used by a clause. Other targets keep their phrase."""
    names: dict[str, str] = {}
    rows: dict[str, Any] = {}
    for target in root.get("m_AbilityTargetTemplateIds") or []:
        guid = str((target or {}).get("m_Guid") or "").lower()
        record = _record("AbilityTargetTemplate", guid, overlay=overlay)
        if not record or not isinstance(record.get("m_CardFilter"), dict):
            continue
        phrase = _target_phrase(guid, overlay)
        if not phrase or phrase in names:
            continue
        properties = ability_targets.read_properties(record["m_CardFilter"])
        quantity = ability_targets.quantity_of(record)
        if not properties or quantity is None or properties.get("raw"):
            continue   # nothing to state as a row (no properties, or one the words cannot show)
        properties["quantity"] = list(quantity)
        flags = record.get("m_CollectionFlags")
        if flags and flags != ability_targets.ALL_ZONES and "|" not in flags:
            properties["searched"] = flags   # where it searches, when that is not every zone
        try:
            ability_targets.render_phrase(properties)
        except ValueError:
            continue
        names[phrase] = f"T{len(rows)}"
        rows[names[phrase]] = properties
    clauses = [dict(clause, target=names.get(clause["target"], clause["target"]))
               for clause in parsed["clauses"]]
    used = {clause["target"] for clause in clauses}
    named = {name: props for name, props in rows.items() if name in used}
    if not named:
        return parsed
    return dict(parsed, clauses=clauses, named_targets=named)


def _root_spec(field: str) -> dict[str, str]:
    return client_schema.fields(ROOT_TYPE)[field]


def _target_phrase(guid: str, overlay: dict, pending: dict | None = None) -> str:
    record = _record("AbilityTargetTemplate", guid, pending, overlay)
    if not record:
        return ""
    content = _content("AbilityTargetTemplate", record)
    phrase = catalogue()["targets"].display.get(_key(content), "")
    if not phrase and _build_target(_clean(record.get("m_GameText"))) == content:
        phrase = _clean(record.get("m_GameText"))   # a built target the overlay holds
    return phrase


def _read_root(root: dict[str, Any], overlay: dict) -> dict[str, Any]:
    """The DSL structure of a root, or ``Unsupported`` with every reason."""
    cat = catalogue()
    reasons: list[str] = []
    content = _content("AbilityTemplate", root)
    if content.get("_t") != ROOT_TYPE:
        reasons.append(f"root type {content.get('_t')}")
    trigger = ""
    if content.get("m_TriggerEventType") or content.get("m_TriggerCondition"):
        key = _key({"event": (content.get("m_TriggerEventType") or {}).get("m_InternalType"),
                    "condition": content.get("m_TriggerCondition")})
        trigger = cat["triggers"].display.get(key, "")
        if not trigger:
            reasons.append("the trigger has no sentence in Records")
    unsaid = _root_defaults(trigger)
    settings = []
    for field, words, kind in SETTINGS:
        value = content.get(field, _default(_root_spec(field)))
        usual = unsaid.get(field, _default(_root_spec(field)))
        if value != usual:
            if kind == "flag" and not value:   # a setting only says a flag is on
                reasons.append(f"root field {field} is off, which this trigger's sentences cannot say")
            settings.append(_setting_text(words, kind, value))
    for field, verb in COST_TARGETS:
        value = content.get(field)
        for target in (value if isinstance(value, list) else [value] if value else []):
            phrase = _target_phrase(target["m_Guid"], overlay)
            if phrase:
                settings.append(f"{verb} {phrase}")
            else:
                reasons.append(f"the {verb} cost's target has no phrase in Records")
    requires = ""
    if content.get("m_AbilityCondition"):
        requires = _requires_phrase(content["m_AbilityCondition"])
        if not requires:
            reasons.append("root field m_AbilityCondition is not a threshold the DSL can say")
    known = set(DSL_ROOT) | {"_t", "m_AbilityCondition"} | {s[0] for s in SETTINGS} | {c[0] for c in COST_TARGETS}
    reasons += [f"root field {field} is not in the DSL" for field in content if field not in known]
    target_guids = [str(t.get("m_Guid") or "").lower() for t in content.get("m_AbilityTargetTemplateIds") or []]
    variables: dict[str, list[dict]] = defaultdict(list)
    for variable in content.get("m_Variables") or []:
        variables[str(variable.get("m_Name") or "")].append(variable)
    clauses = []
    latest: dict[str, int] = {}   # target content -> the latest slot a clause used
    for index, entry in enumerate(content.get("m_AbilityEffectList") or []):
        try:
            clause = _read_entry(index, entry, target_guids, variables, overlay)
        except Unsupported as error:
            reasons += [f"clause {index + 1}: {reason}" for reason in error.reasons]
            continue
        slot = entry.get("m_TargetTemplateIndex", 0)
        used = {i for i in latest.values()}
        if clause["target"] in latest and slot != latest[clause["target"]]:
            if slot in used:
                reasons.append(f"clause {index + 1}: it reuses an older target slot")
            clause["new_target"] = True
        latest[clause["target"]] = slot
        clauses.append(clause)
    read = {name for entry in content.get("m_AbilityEffectList") or []
            for name in _variable_uses(_record("AbilityEffectTemplate", str(
                (entry.get("m_EffectTemplateId") or {}).get("m_Guid") or "").lower(), overlay=overlay) or {}, [])}
    reasons += [f"variable {name} is read outside the effects" for name in variables if name not in read]
    if reasons:
        raise Unsupported(reasons)
    return {"settings": settings, "requires": requires, "trigger": trigger, "clauses": clauses}


def _requires_phrase(condition: dict[str, Any]) -> str:
    """"you have N Shard threshold [and ...]" for a threshold ability condition, else ""."""
    parts = condition.get("m_Conditions") if condition.get("_t") == AND_CONDITION else [condition]
    if condition.get("_t") == AND_CONDITION and set(condition) != {"_t", "m_Conditions"}:
        return ""
    phrases = [_threshold_phrase({"_t": CONDITION_TYPE, "m_Condition": part}) for part in parts or []]
    return " and ".join(phrases) if phrases and all(phrases) else ""


def _setting_text(words: str, kind: str, value: Any) -> str:
    if kind == "flag":
        return words
    return words.replace("{n}", str(value)).replace("{value}", str(value))


def _read_entry(index: int, entry: dict[str, Any], target_guids: list[str],
                variables: dict[str, list[dict]], overlay: dict) -> dict[str, Any]:
    cat = catalogue()
    defaults = {spec["name"]: _canon(spec["default"], spec["cs_type"])
                for spec in abilities.schema_for(abilities.MAPPING_TYPE)}
    entry = dict(defaults, **entry)
    said = ("m_EffectTemplateId", "m_TargetTemplateIndex", "m_EffectInstanceId", "m_EffectGroupId",
            "m_EffectDuration", "m_ConditionId", "m_RecalculateTargets", "m_Layer",
            "m_ContingentEffectInstanceId")
    reasons = [f"{field} is not in the DSL" for field, value in sorted(entry.items())
               if value != defaults.get(field) and field not in said]
    reasons.extend(f"{field} is {entry[field]}, not {value}" for field, value in (("m_EffectInstanceId", index), ("m_EffectGroupId", index + 1)) if entry[field] != value)
    position = entry["m_TargetTemplateIndex"]
    if not 0 <= position < len(target_guids):
        raise Unsupported(reasons + ["the target index points at no target"])
    target_record = _record("AbilityTargetTemplate", target_guids[position], overlay=overlay)
    target = _target_phrase(target_guids[position], overlay)
    if not target:
        reasons.append("its target has no phrase in Records")
    usual_recalculate = "True" if (target_record or {}).get("m_IsAutoTarget") else "UseDefault"
    effect_guid = str((entry.get("m_EffectTemplateId") or {}).get("m_Guid") or "").lower()
    effect = _record("AbilityEffectTemplate", effect_guid, overlay=overlay)
    found = _shape_of(_content("AbilityEffectTemplate", effect)) if effect else None
    if not found:
        kind = str((effect or {}).get("_t") or effect_guid).rsplit(".", 1)[-1]
        modifier = str(((effect or {}).get("m_Modifier") or {}).get("_t") or "").rsplit(".", 1)[-1]
        raise Unsupported(reasons + [f"effect {kind}{'/' + modifier if modifier else ''} has no sentence shape"])
    shape, raw = found
    clause = {"shape": shape["id"], "target": target, "amount": "", "count": "", "duration": "",
              "condition": "", "layer": entry["m_Layer"],
              "recalculate": "" if entry["m_RecalculateTargets"] == usual_recalculate
              else RECALCULATE[entry["m_RecalculateTargets"]]}
    if "$amount" in raw:
        bound = variables.get(raw["$amount"]) or []
        kinds = {_key(dict(_canon(v), m_Name="")) for v in bound}
        variable = _canon(bound[0]) if len(kinds) == 1 else {}
        variable.pop("m_Name", None)
        if len(kinds) != 1:
            reasons.append(f"variable {raw['$amount']} is not one binding")
        elif variable.get("_t") == CONSTANT and set(variable) <= {"_t", "m_DefaultValue"}:
            clause["amount"] = _amount_text(shape, int(variable.get("m_DefaultValue", 0)))
        elif variable.get("_t") in FOR_EACH and _key(variable) in cat["counts"].display:
            clause["amount"] = _amount_text(shape, 1)
            clause["count"] = cat["counts"].display[_key(variable)]
        else:
            reasons.append(f"amount {variable.get('_t', '').rsplit('.', 1)[-1]} is not in the DSL")
    for slot, table in NAME_TABLES.items():
        if "$" + slot in raw:
            try:
                clause[slot] = _name_slot(cat[table], str(raw["$" + slot]).lower())
            except Unsupported as error:
                reasons += error.reasons
    if "$keyword" in raw:
        clause["keyword"] = " and ".join(str(raw["$keyword"]).split("|"))
    if "$filter" in raw:
        clause["filter"] = cat["filters"].display.get(_key(raw["$filter"]), "")
        if not clause["filter"]:
            reasons.append("its random card filter has no words in Records")
    for slot in ("shard", "attribute", "number", "event"):
        if "$" + slot in raw:
            clause[slot] = str(raw["$" + slot]).lower() if slot == "shard" else raw["$" + slot]
    if "$event" in raw:
        clause["event"] = str(raw["$event"]).rsplit(".", 1)[-1]   # the event class, by short name
    reasons.extend(f"{slot} {raw['$' + slot]} is not one Records uses" for slot, known in (("attribute", "attributes"), ("event", "events")) if "$" + slot in raw and raw["$" + slot] not in cat[known])
    duration = entry["m_EffectDuration"]
    if duration != _usual_durations().get(shape["id"], "Instant"):
        if duration not in DURATIONS:
            reasons.append(f"duration {duration} is not in the DSL")
        clause["duration"] = DURATIONS.get(duration, duration)
    condition_guid = str((entry.get("m_ConditionId") or {}).get("m_Guid") or ZERO).lower()
    contingent = entry["m_ContingentEffectInstanceId"]
    if contingent == index - 1 and index:
        clause["contingent"] = True
    elif contingent != -1:
        reasons.append(f"it is contingent on effect {contingent}, not the one before it")
    if condition_guid != ZERO:
        record = _record("AbilityEffectConditionTemplate", condition_guid, overlay=overlay)
        content = _content("AbilityEffectConditionTemplate", record) if record else {}
        clause["condition"] = cat["conditions"].display.get(_key(content), "") or _threshold_phrase(content)
        if not clause["condition"]:
            reasons.append("its condition has no phrase in Records")
    if reasons:
        raise Unsupported(reasons)
    return clause


# ---------------------------------------------------------------- parse

def _slot_pattern(phrase: str) -> re.Pattern:
    pattern = re.escape(phrase)
    for slot, regex in SLOT_PATTERNS.items():
        pattern = pattern.replace(re.escape("{" + slot + "}"), regex)
    return re.compile(pattern, re.I)


_PATTERNS: list[tuple[dict[str, Any], bool, re.Pattern]] = [
    (shape, you, _slot_pattern(shape["you"] if you else shape["phrase"]))
    for shape in SHAPES for you in (True, False) if (shape.get("you") if you else True)]
_DURATION_WORDS = {words: name for name, words in DURATIONS.items()}
_SUFFIX = re.compile(r"\s*\((layer -?\d+|" + "|".join(list(RECALCULATE.values()) + [NEW_TARGET]) + r")\)\s*$", re.I)


def _outside_quotes(text: str, needle: str) -> list[int]:
    """Where ``needle`` starts in ``text`` (case-insensitive), not inside double quotes."""
    out, quoted, lower = [], False, text.lower()
    for index, char in enumerate(text):
        if char == '"':
            quoted = not quoted
        elif not quoted and lower.startswith(needle, index):
            out.append(index)
    return out


def _split_clause(text: str):
    """Every reading of a clause as (effect, for-each, duration, condition), simplest first.
    A trailing "for you" is also read off, so "actions cost 2 more this turn for you" reads
    as "actions cost 2 more this turn" (the target is you either way)."""
    readings = [text] + ([re.sub(r"\s+for you$", "", text, flags=re.I)] if re.search(r"\s+for you$", text, re.I) else [])
    for source in readings:
        conditions = [(source, "")] + [(source[:at], source[at + 4:]) for at in _outside_quotes(source, " if ")]
        for body, condition in conditions:
            lower = body.lower()
            durations = [(body, "")] + [(body[:-len(words) - 1], words) for words in _DURATION_WORDS
                                         if lower.endswith(" " + words)]
            for rest, duration in durations:
                counts = [(rest, "")] + [(rest[:at], rest[at + 10:]) for at in _outside_quotes(rest, " for each ")]
                for effect, count in counts:
                    yield effect.strip(), count.strip().lower(), duration, condition.strip().lower()


def _parse_clause(text: str, instead: bool = False, names: frozenset = frozenset()) -> dict[str, Any]:
    """One clause. ``instead``: it is a replacement's last clause, which may end "instead"."""
    cat = catalogue()
    suffixes = {"layer": 0, "recalculate": "", "new_target": False}
    while _SUFFIX.search(text):
        found = _SUFFIX.search(text)
        word = found.group(1).lower()
        if word.startswith("layer"):
            suffixes["layer"] = int(word.split()[1])
        elif word == NEW_TARGET:
            suffixes["new_target"] = True
        else:
            suffixes["recalculate"] = word
        text = text[:found.start()]
    ends_instead = text.lower().rstrip().endswith(" " + INSTEAD)
    if instead and ends_instead:
        text = text.rstrip()[:-len(INSTEAD) - 1]
    problems = [] if instead or not ends_instead else [
        f"'{INSTEAD}' ends only the last clause of an 'If ... would ...' ability"]
    for effect, count, duration, condition in _split_clause(text):
        if count and count not in cat["counts"].by_phrase:
            problems.append(f"no 'for each' phrase {count!r}")
            continue
        if condition and condition not in cat["conditions"].by_phrase and not _parse_threshold(condition):
            problems.append(f"no condition {condition!r}")
            continue
        count = cat["counts"].display.get(cat["counts"].by_phrase.get(count), count)
        if condition in cat["conditions"].by_phrase:
            condition = cat["conditions"].display.get(cat["conditions"].by_phrase[condition], condition)
        for shape, you, pattern in _PATTERNS:
            found = pattern.fullmatch(effect)
            if not found:
                continue
            slots = found.groupdict()
            target = "you" if you else (slots.get("target") or "").lower()
            if target.upper() in names:   # a target row defined by the sentences' Targets line
                target = target.upper()
            elif target in cat["targets"].by_phrase:
                target = cat["targets"].display.get(cat["targets"].by_phrase[target], target)
            elif _build_target(target) is None:
                problems.append(f"no target {target!r}")
                continue
            clause = dict(suffixes, shape=shape["id"], target=target, amount=(slots.get("amount") or "").lower(),
                          count=count, duration=duration, condition=condition)
            try:
                _read_slots(clause, slots)
            except ValueError as error:
                problems.append(str(error))
                continue
            if not _shape_accepts(shape, clause.get("attribute")):
                problems.append(f"{clause['attribute']} is not an on/off card value: "
                                f"write \"set ...'s {clause['attribute']} to N\"")
                continue
            if count and clause["amount"] and _number(clause["amount"]) != 1:
                problems.append("'for each' needs the amount 1")
                continue
            return clause
    hint = problems[0] if problems else "no sentence shape matches"
    raise ValueError(f"cannot read {text!r}: {hint}")


def _read_slots(clause: dict[str, Any], slots: dict[str, Any]) -> None:
    """Resolve a clause's named and enumerated slots (``ValueError`` if one is unknown)."""
    cat = catalogue()
    for slot, table in NAME_TABLES.items():
        if slots.get(slot):
            clause[slot + "_guid"] = _name_lookup(cat[table], slots[slot], slots.get(slot + "_id") or "")
            clause[slot] = _name_slot(cat[table], clause[slot + "_guid"])
    if slots.get("keyword"):
        # A member name, or its display name (ToolName: "Crush" is Juggernaught); the
        # sentence is written with the member name.
        members = {m.lower(): m for m in cat["keywords"]}
        members.update({k["label"].lower(): k["name"] for k in card_attributes.keywords()
                        if k["label"].lower() not in members})
        words = [word.strip() for word in slots["keyword"].lower().split(" and ")]
        if not all(word in members for word in words):
            raise ValueError(f"no keyword {slots['keyword']!r}")
        clause["keyword"] = " and ".join(members[word] for word in words)
    if slots.get("filter"):
        phrase = re.sub(r"\s+", " ", slots["filter"].lower().strip())
        key = cat["filters"].by_phrase.get(phrase)
        if not key:
            raise ValueError(f"no random cards {slots['filter']!r} in Records")
        clause["filter"] = cat["filters"].display.get(key, phrase)
    if slots.get("shard"):
        clause["shard"] = slots["shard"].lower()
    if slots.get("attribute"):
        members = {m.lower(): m for m in cat["attributes"]}
        if slots["attribute"].lower() not in members:
            raise ValueError(f"no card value {slots['attribute']!r} in Records")
        clause["attribute"] = members[slots["attribute"].lower()]
    if slots.get("number") is not None:
        clause["number"] = int(slots["number"])
    if slots.get("event"):
        members = {m.rsplit(".", 1)[-1].lower(): m for m in cat["events"]}
        if slots["event"].lower() not in members:
            raise ValueError(f"no event {slots['event']!r} in Records")
        clause["event_type"] = members[slots["event"].lower()]
        clause["event"] = clause["event_type"].rsplit(".", 1)[-1]


def _number(text: str) -> int:
    text = str(text).strip().lower()
    return NUMBERS[text] if text in NUMBERS else int(text)


def _parse_threshold(phrase: str) -> dict[str, Any] | None:
    found = re.fullmatch(r"you have (\d+) (\w+) threshold", phrase.strip().lower())
    if not found or found.group(2).capitalize() not in SHARDS:
        return None
    return {"_t": CONDITION_TYPE, "m_Condition": {
        "_t": THRESHOLD, "m_ColorFlags": found.group(2).capitalize(),
        "m_RequiredQuantity": int(found.group(1)), "m_ComparisonOp": "GreaterThanOrEqual"}}


def _parse_requires(phrase: str) -> dict[str, Any]:
    parts = [_parse_threshold(part) for part in phrase.split(" and ")]
    if not parts or not all(parts):
        raise ValueError(f"Requires: {phrase!r} is not 'you have N Shard threshold [and ...]'")
    conditions = [part["m_Condition"] for part in parts]
    return conditions[0] if len(conditions) == 1 else {"_t": AND_CONDITION, "m_Conditions": conditions}


def _sentences(line: str) -> list[str]:
    """A line's sentences, split at ". " outside quotes."""
    out, start = [], 0
    for at in _outside_quotes(line, ". "):
        out.append(line[start:at])
        start = at + 2
    out.append(line[start:])
    return [re.sub(r"^(?:then,? |and then,? )", "", part.strip(), flags=re.I) for part in out if part.strip()]


def _line(line: str) -> str:
    """A text line with tags out, single spaces and no closing full stop."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", line)).strip().rstrip(".").strip()


def parse(text: str) -> dict[str, Any]:
    """The structure of DSL text: settings, requires, trigger and clauses (``ValueError``
    names the line it cannot read)."""
    settings, requires, trigger, clauses = [], "", "", []
    named: dict[str, Any] = {}
    pending: list[tuple[int, str]] = []   # (line number, clause text), read once all are known
    for number, line in enumerate(str(text or "").replace("\r", "").split("\n"), 1):
        line = _line(line)
        lower = line.lower()
        if not line or line.startswith("#"):
            continue
        try:
            if lower.startswith("targets:"):
                named.update(ability_targets.parse_definitions(line.split(":", 1)[1]))
                continue
            if lower.startswith("settings:"):
                settings += _split_settings(line.split(":", 1)[1])
                continue
            if lower.startswith("requires:"):
                requires = lower.split(":", 1)[1].strip().rstrip(".")
                _parse_requires(requires)
                continue
            line = re.sub(r"^(?:- |then,? |and then,? )", "", line, flags=re.I)
            if not pending and not trigger:
                trigger, line = _split_trigger(line)
            if line.lower() in ("", "do nothing", "do nothing " + INSTEAD):
                continue
            pending += [(number, part) for part in _sentences(line)]
        except ValueError as error:
            raise ValueError(f"line {number}: {error}") from None
    # A replacement's last clause ends "instead" (render writes it; it may be left out).
    instead = bool(trigger) and is_replacement(trigger)
    optional = False
    for index, (number, part) in enumerate(pending):
        try:
            contingent = IF_YOU_DO.match(part)
            if contingent:
                if index == 0:
                    raise ValueError("'If you do' needs an earlier clause")
                part = part[contingent.end():]
            may = YOU_MAY.match(part) if index == 0 else None
            if may:
                part = part[may.end():]
            clause = _parse_clause(part, instead and index == len(pending) - 1, frozenset(named))
            if may and clause["target"] != "you":
                raise ValueError("'you may' needs a clause for you")
            clause["contingent"] = bool(contingent)
            clauses.append(clause)
            optional = optional or bool(may)
        except ValueError as error:
            raise ValueError(f"line {number}: {error}") from None
    if optional and "optional" not in settings:
        settings.append("optional")
    return {"settings": settings, "requires": requires, "trigger": trigger, "clauses": clauses,
            "named_targets": named}


def _split_trigger(line: str) -> tuple[str, str]:
    """``(trigger, rest)`` of a first clause line; ``ValueError`` names a trigger Records lacks."""
    parts = re.split(r"(?<=\S)(?: - |[,:])", line, maxsplit=1)
    known = _trigger_named(parts[0].strip().lower())
    if known:
        return known, (parts[1] if len(parts) > 1 else "").strip()
    head = parts[0].strip().lower()
    if head.startswith(("when ", "whenever ", "at the ", "after ")) or \
            (head.startswith("if ") and " would " in head):
        raise ValueError(f"no trigger {parts[0].strip()!r} in Records")
    return "", line


def _trigger_named(head: str) -> str:
    """The catalogue phrase for a trigger as written ("this card" reads as "this")."""
    triggers = catalogue()["triggers"]
    for phrase in (head, head.replace("this card", "this"), KEYWORD_TRIGGERS.get(head, "")):
        if phrase and phrase in triggers.by_phrase:
            return triggers.display.get(triggers.by_phrase[phrase], phrase)
    generated = _subtype_trigger(head)
    return generated or ""


_SUBTYPE_TRIGGER = re.compile(r"^(when (?:a|an|another) )([a-z'-]+)((?: you control)? [a-z ]+)$")


def _subtype_trigger(head: str) -> str:
    """A trigger for any subtype Records has a trigger for: "when a warlock you control dies"
    from "when a cleric you control dies". The copy's subtype is the only change, so it reads
    back as the phrase it was made from. Registered in the catalogue for this process."""
    found = _SUBTYPE_TRIGGER.match(head)
    if not found:
        return ""
    skeleton = found.group(1) + "{X}" + found.group(3)
    triggers = catalogue()["triggers"]
    with _LOCK:
        if head in triggers.by_phrase:
            return triggers.display.get(triggers.by_phrase[head], head)
        subtypes: dict[str, str] = {}
        for content in triggers.content.values():
            for node in _walk_subtypes(content):
                subtypes.setdefault(str(node).lower(), str(node))
        if found.group(2) not in subtypes:
            return ""
        for phrase, key in triggers.by_phrase.items():
            other = _SUBTYPE_TRIGGER.match(phrase)
            if not other or other.group(1) + "{X}" + other.group(3) != skeleton:
                continue
            content = copy.deepcopy(triggers.content[key])
            if not _set_subtype(content, subtypes[found.group(2)]):
                continue
            new_key = f"{key}|{subtypes[found.group(2)]}"
            triggers.content[new_key] = content
            triggers.by_phrase[head] = new_key
            triggers.display[new_key] = head
            return head
    return ""


def _walk_subtypes(node: Any):
    if isinstance(node, dict):
        if node.get("_t", "").endswith(".IsSubType") and node.get("m_SubType"):
            yield node["m_SubType"]
        for value in node.values():
            yield from _walk_subtypes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_subtypes(item)


def _set_subtype(node: Any, value: str) -> bool:
    """Set the subtype of the first IsSubType filter in ``node``; False if there is none."""
    changed = False
    if isinstance(node, dict):
        if node.get("_t", "").endswith(".IsSubType"):
            node["m_SubType"] = value
            return True
        for item in node.values():
            changed = _set_subtype(item, value) or changed
    elif isinstance(node, list):
        for item in node:
            changed = _set_subtype(item, value) or changed
    return changed


def _split_settings(text: str) -> list[str]:
    """Comma-separated settings; a target phrase that holds a comma is joined back."""
    out, pending = [], ""
    for part in text.split(","):
        pending = f"{pending}, {part.strip()}" if pending else part.strip()
        try:
            _setting_value(pending.lower())
        except ValueError:
            continue
        out.append(pending.lower())
        pending = ""
    if pending:
        _setting_value(pending.lower())   # raises with the reason
    return out


def _setting_value(item: str) -> tuple[str, Any]:
    """``(root field, value)`` of one setting; a cost target's value is a target GUID."""
    item = item.strip()
    for field, verb in COST_TARGETS:
        if item.startswith(verb + " "):
            targets = catalogue()["targets"]
            phrase = item[len(verb) + 1:]
            if phrase in targets.by_phrase:   # a cost target must be a Records one
                return field, targets.guid(targets.by_phrase[phrase])
            raise ValueError(f"no target {phrase!r}")
    for field, words, kind in SETTINGS:
        pattern = re.escape(words).replace(re.escape("{n}"), r"(\d+)").replace(re.escape("{value}"), r"([\w|]+)")
        found = re.fullmatch(pattern, item, re.I)
        if not found:
            continue
        if kind == "flag":
            return field, 1
        if kind == "int":
            return field, int(found.group(1))
        spec = client_schema.describe(_root_spec(field)["type"])
        members = {m.lower(): m for m in spec.get("values", [])}
        parts = found.group(1).split("|")
        if not all(part.lower() in members for part in parts):
            raise ValueError(f"setting {item!r}: not a {spec.get('enum')}")
        return field, "|".join(members[part.lower()] for part in parts)
    raise ValueError(f"unknown setting {item!r}")


# ---------------------------------------------------------------- compile

def _fill(record: dict[str, Any]) -> dict[str, Any]:
    """``record`` with every client field it lacks at its default (nested objects too)."""
    type_name = str(record.get("_t") or "")
    out = {"_t": type_name} if type_name else {}
    for name, spec in client_schema.fields(type_name).items():
        out[name] = copy.deepcopy(client_schema.default_value(spec))
    for key, value in record.items():
        if isinstance(value, dict) and value.get("_t"):
            value = _fill(value)
        elif isinstance(value, list):
            value = [_fill(v) if isinstance(v, dict) and v.get("_t") else v for v in value]
        out[key] = value
    return out


def _substitute(template: Any, values: dict[str, Any]) -> Any:
    if isinstance(template, str) and template in values:
        return values[template]
    if isinstance(template, dict):
        return {k: _substitute(v, values) for k, v in template.items()}
    if isinstance(template, list):
        return [_substitute(v, values) for v in template]
    return template


def ir_problems(records: list[dict[str, Any]]) -> list[str]:
    """Round-trip each record through its IR layer; return what does not come back
    exactly. The IR rejects a field it does not map, so a problem here means the
    records hold something the IR layers cannot represent."""
    import ability_ir as ir
    import ability_ir_conditions as irk
    import ability_ir_effects as ire
    import ability_ir_root as irr

    def same(record: dict[str, Any], rebuilt: Any) -> bool:
        return rebuilt == record

    problems: list[str] = []
    for item in records:
        section, record = item["section"], item["record"]
        name = record.get("m_Name") or record.get("m_TemplateId") or section
        try:
            if section == "AbilityTemplate":
                ok = same(record, irr.node_to_record(irr.node_from_record(record)))
            elif section == "AbilityEffectTemplate":
                ok = same(record, ire.node_to_record(ire.node_from_record(record)))
            elif section == "AbilityTargetTemplate":
                ok = same(record, ir.target_to_record(ir.target_from_record(record)))
            elif section == "AbilityEffectConditionTemplate":
                ok = same(record, irk.node_to_record(irk.node_from_record(record)))
            else:
                continue
        except Exception as error:  # the IR raises on anything it does not map
            problems.append(f"{section} {name}: {error}")
            continue
        if not ok:
            problems.append(f"{section} {name}: does not round-trip through the IR")
    return problems


def compile_text(text: str, base: str = "") -> dict[str, Any]:
    """Records for DSL text: ``root``, the ``pending`` new child records by section, and
    ``records`` (root first) ready for ``card_abilities.save_records``. Library records
    whose content Records already has are reused, not copied. With ``base`` (a root
    GUID) the root keeps that GUID and its name, TAC and index."""
    cat = catalogue()
    parsed = parse(text)
    overlay = campaign_data.load_ability_overlay()
    pending: dict[str, dict[str, dict]] = {section: {} for section in abilities.SECTIONS}
    root = abilities.new_record("AbilityTemplate", ROOT_TYPE)["record"]
    original = None
    if base:
        base = abilities._guid(base)
        original = _record("AbilityTemplate", base, overlay=overlay)
        if original is None:
            raise LookupError(f"no ability {base}")
        root["m_AbilityTemplateId"] = {"m_Guid": base}
        for field in ("m_Name", "m_SerializedTAC", "m_AbilityIndex"):
            if field in original:
                root[field] = copy.deepcopy(original[field])
    root.update(_root_defaults(parsed["trigger"]))   # a replacement's unsaid values too
    for item in parsed["settings"]:
        field, value = _setting_value(item)
        if field == "m_DiscardTargets":
            root[field] = (root.get(field) or []) + [{"m_Guid": value}]
        elif field in dict(COST_TARGETS):
            root[field] = {"m_Guid": value}
        else:
            root[field] = value
    if parsed["requires"]:
        root["m_AbilityCondition"] = _fill_tree(_parse_requires(parsed["requires"]))
    if parsed["trigger"]:
        content = cat["triggers"].content[cat["triggers"].by_phrase[parsed["trigger"]]]
        root["m_TriggerEventType"] = {"m_InternalType": content["event"]} if content["event"] else None
        root["m_TriggerCondition"] = _fill_tree(content["condition"])
    entry_defaults = {spec["name"]: spec["default"] for spec in abilities.schema_for(abilities.MAPPING_TYPE)}
    targets: list[str] = []
    latest: dict[str, int] = {}
    entries, variables, bound = [], [], {}
    for index, clause in enumerate(parsed["clauses"]):
        shape = SHAPE_BY_ID[clause["shape"]]
        if clause["target"] in (parsed.get("named_targets") or {}):
            target_content = _named_content(parsed["named_targets"][clause["target"]])
        else:
            target_content = _target_content(clause["target"])
        target_key = _key(target_content)
        target_phrase = (ability_targets.render_phrase(parsed["named_targets"][clause["target"]])
                         if clause["target"] in (parsed.get("named_targets") or {}) else clause["target"])
        target_guid = _target_for(target_phrase, target_content, pending)
        if clause.get("new_target") or target_key not in latest:
            targets.append(target_guid)
            latest[target_key] = len(targets) - 1
        value = _amount_variable(clause, shape)
        effect_guid, names = _effect_for(shape, clause, value, bound, pending)
        for name in names:
            variables.append(dict(copy.deepcopy(value), m_Name=name))
            bound[name] = _key(value)
        condition_guid = _condition_for(clause["condition"], pending) if clause["condition"] else ZERO
        usual = "True" if target_content.get("m_IsAutoTarget") else "UseDefault"
        recalculate = {words: name for name, words in RECALCULATE.items()}.get(clause.get("recalculate"), usual)
        entries.append(dict(copy.deepcopy(entry_defaults), **{
            "m_EffectTemplateId": {"m_Guid": effect_guid},
            "m_TargetTemplateIndex": latest[target_key],
            "m_EffectInstanceId": index, "m_EffectGroupId": index + 1,
            "m_EffectDuration": _DURATION_WORDS.get(clause["duration"]) or
                                _usual_durations().get(shape["id"], "Instant"),
            "m_ConditionId": {"m_Guid": condition_guid}, "m_RecalculateTargets": recalculate,
            "m_Layer": int(clause.get("layer") or 0),
            "m_ContingentEffectInstanceId": index - 1 if clause.get("contingent") else -1,
            "m_SecondaryTargetIndex": -1}))
    root["m_AbilityEffectList"] = entries
    root["m_AbilityTargetTemplateIds"] = [{"m_Guid": guid} for guid in targets]
    root["m_Variables"] = variables
    lines = render(parsed).split("\n")
    sentence = " ".join(line for line in lines if not line.startswith(("Settings:", "Requires:")))
    if not base:
        root["m_Name"] = camel(sentence)[:200]
    root["m_GameText"] = sentence
    if original is not None and not _differences(_tree(original, None, overlay), _tree(root, pending, overlay)):
        root["m_GameText"] = original.get("m_GameText", sentence)   # unchanged: keep the printed text
    records = [{"section": "AbilityTemplate", "record": root}] + [
        {"section": section, "record": record}
        for section in abilities.SECTIONS[1:] for record in pending[section].values()]
    return {"root": root, "pending": pending, "records": records, "parsed": parsed,
            "text": render(parsed), "ir_problems": ir_problems(records),
            "new": [{"section": r["section"], "guid": campaign_data.ability_record_guid(r["section"], r["record"])}
                    for r in records[1:]]}


def _fill_tree(value: Any) -> Any:
    if isinstance(value, dict):
        filled = _fill(value) if value.get("_t") else dict(value)
        return {k: _fill_tree(v) for k, v in filled.items()}
    if isinstance(value, list):
        return [_fill_tree(v) for v in value]
    return value


def _amount_variable(clause: dict[str, Any], shape: dict[str, Any]) -> dict[str, Any] | None:
    """The root variable a clause's amount needs (without its name), or None."""
    if "$amount" not in json.dumps(shape["record"]):
        return None
    cat = catalogue()
    if clause.get("count"):
        return _fill(copy.deepcopy(cat["counts"].content[cat["counts"].by_phrase[clause["count"]]]))
    if not clause.get("amount"):
        raise ValueError(f"{shape['label']} needs an amount")
    return {"_t": CONSTANT, "m_DefaultValue": _number(clause["amount"])}


def _variable_name(value: dict[str, Any] | None, clause: dict[str, Any]) -> str:
    if value is None:
        return ""
    if value["_t"] == CONSTANT:
        number = value["m_DefaultValue"]
        return str(number) if number >= 0 else f"M{-number}"
    return "AForEach" + camel(clause["count"])


def _slot_values(clause: dict[str, Any]) -> dict[str, Any]:
    """The values a parsed clause gives a shape's "$" slots; the amount's name is "$"."""
    values = {"$" + slot: clause.get(slot + "_guid", "") for slot in NAME_TABLES}
    filters = catalogue()["filters"]
    key = filters.by_phrase.get(clause.get("filter") or "")
    values["$filter"] = copy.deepcopy(filters.content[key]) if key else ""
    keyword = clause.get("keyword") or ""
    values.update({"$keyword": "|".join(keyword.split(" and ")) if keyword else "",
                   "$shard": str(clause.get("shard") or "").capitalize(),
                   "$attribute": clause.get("attribute", ""), "$number": clause.get("number", 0),
                   "$event": clause.get("event_type", ""),
                   "$amount": "$"})
    return values


def _effect_for(shape: dict[str, Any], clause: dict[str, Any], value: dict | None,
                bound: dict[str, str], pending: dict) -> tuple[str, list[str]]:
    """The effect GUID for a clause and the variable names it reads: a Records effect
    of the same shape whose names do not clash with this root's, or a new one."""
    cat = catalogue()
    values = _slot_values(clause)
    shape_key, _ = _effect_shape_key(_canon(_substitute(shape["record"], values)))
    wanted = _key(value) if value is not None else None

    def free(names: list[str]) -> bool:
        return all(bound.get(name, wanted) == wanted for name in names)

    natural = _variable_name(value, clause)
    # Prefer an effect whose variable already has the natural name ("2" for 2), so the
    # root reads "2" = 2 rather than "4" = 2; then the most used one.
    candidates = sorted(cat["effects"].get(shape_key, []), key=lambda row: _names_differ(row[2], natural))
    for _, guid, names in candidates:
        if free(names):
            return guid, names
    for guid, record in pending["AbilityEffectTemplate"].items():
        key, names = _effect_shape_key(_content("AbilityEffectTemplate", record))
        if key == shape_key and free(names):
            return guid, names
    name = natural
    while name and not free([name]):
        name += "x"
    values["$amount"] = name
    guid = str(uuid.uuid4())
    you = clause["target"] == "you"
    words = _render_clause(shape, dict(clause, count="", duration="", condition="", layer=0, recalculate=""), you)
    pending["AbilityEffectTemplate"][guid] = _fill(dict(
        _substitute(shape["record"], values), m_TemplateId={"m_Guid": guid},
        m_Name=camel(words)[:200], m_GameText=_sentence(words)))
    return guid, [name] if name else []


def _target_for(phrase: str, content: dict[str, Any], pending: dict) -> str:
    """A Records target with this content, else a new one (made once per compile)."""
    vocab = catalogue()["targets"]
    key = _key(content)
    if vocab.guid(key):
        return vocab.guid(key)
    for guid, record in pending["AbilityTargetTemplate"].items():
        if _key(_content("AbilityTargetTemplate", record)) == key:
            return guid
    guid = str(uuid.uuid4())
    pending["AbilityTargetTemplate"][guid] = _fill_tree(dict(
        content, m_TemplateId={"m_Guid": guid}, m_Name=camel(phrase), m_GameText=phrase))
    return guid


def _names_differ(names: list[str], natural: str) -> bool:
    return bool(natural) and names != [natural]


def _condition_for(phrase: str, pending: dict) -> str:
    cat = catalogue()
    key = cat["conditions"].by_phrase.get(phrase)
    if key:
        return cat["conditions"].guid(key)
    content = _parse_threshold(phrase)
    if content is None:
        raise ValueError(f"no condition {phrase!r}")
    for guid, record in pending["AbilityEffectConditionTemplate"].items():
        if _content("AbilityEffectConditionTemplate", record) == _canon(content):
            return guid
    guid = str(uuid.uuid4())
    pending["AbilityEffectConditionTemplate"][guid] = _fill(dict(
        content, m_TemplateId={"m_Guid": guid}, m_Name=camel(phrase)))
    return guid


def save_text(text: str, base: str = "") -> dict[str, Any]:
    """Compile and store (``card_abilities.save_records``: checks, lock, atomic write).
    An existing ability is only rewritten when its sentences read it exactly."""
    if base:
        found = decompile(base)
        if not found["exact"]:
            raise Unsupported(["this ability has parts the sentences cannot show; "
                               "edit it in the field editor"] + found["reasons"])
    compiled = compile_text(text, base)
    if compiled["ir_problems"]:
        raise Unsupported(["the IR cannot represent this ability"] + compiled["ir_problems"])
    records = compiled["records"]
    if base:
        overlay = campaign_data.load_ability_overlay()
        original = abilities._records_record("AbilityTemplate", abilities._guid(base))
        if original is not None and not _differences(
                _tree(original, None, overlay), _tree(compiled["root"], compiled["pending"], overlay)):
            # The sentences say what Records says: store Records' own record, which
            # drops any shadow instead of writing an equivalent copy.
            records = [{"section": "AbilityTemplate", "record": original}]
    saved = abilities.save_records(records)
    guid = campaign_data.ability_record_guid("AbilityTemplate", compiled["root"])
    return dict(saved, guid=guid, text=compiled["text"],
                new=compiled["new"] if records is compiled["records"] else [])


def preview(text: str, base: str = "") -> dict[str, Any]:
    """What ``save_text`` would store, with the checks' problems; nothing is written."""
    compiled = compile_text(text, base)
    return {"text": compiled["text"], "parsed": compiled["parsed"], "records": compiled["records"],
            "new": compiled["new"], "problems": abilities.validate(compiled["records"])}


# ---------------------------------------------------------------- comparison

def _tree(root: dict[str, Any], pending: dict | None, overlay: dict) -> dict[str, Any]:
    """A root with its children's contents in place of their GUIDs and its variables as
    bindings in order of use: what the round trip compares."""
    tree = _content("AbilityTemplate", root)
    for field in ROOT_DEFAULTS:
        tree.setdefault(field, _default(_root_spec(field)))

    def target(reference: dict[str, Any]) -> Any:
        record = _record("AbilityTargetTemplate", reference["m_Guid"], pending, overlay)
        return _content("AbilityTargetTemplate", record) if record else reference

    tree["targets"] = [target(t) for t in tree.pop("m_AbilityTargetTemplateIds", [])]
    for field, _ in COST_TARGETS:
        if isinstance(tree.get(field), list):
            tree[field] = [target(t) for t in tree[field]]
        elif tree.get(field):
            tree[field] = target(tree[field])
    order: list[str] = []
    entries = []
    for entry in tree.pop("m_AbilityEffectList", []):
        entry = dict(entry)
        effect_guid = (entry.get("m_EffectTemplateId") or {}).get("m_Guid", ZERO)
        effect = _record("AbilityEffectTemplate", effect_guid, pending, overlay)
        entry["m_EffectTemplateId"] = _content("AbilityEffectTemplate", effect) if effect else effect_guid
        _input_names(entry["m_EffectTemplateId"], order)
        if "m_ConditionId" in entry:
            record = _record("AbilityEffectConditionTemplate", entry["m_ConditionId"]["m_Guid"], pending, overlay)
            entry["m_ConditionId"] = _content("AbilityEffectConditionTemplate", record) if record \
                else entry["m_ConditionId"]
        entries.append(entry)
    # Each read of a variable is replaced by what the variable holds: effects that read
    # "A" = 1 and "1" = 1 compare equal. Variables nothing reads keep their names.
    bindings: dict[str, list[str]] = defaultdict(list)
    for variable in tree.pop("m_Variables", []):
        variable = dict(variable)
        name = str(variable.pop("m_Name", ""))
        if _key(variable) not in bindings[name]:
            bindings[name].append(_key(variable))
    held = {name: "=" + "|".join(sorted(values)) for name, values in bindings.items()}
    tree["entries"] = _rename(entries, {name: held.get(name, "unbound:" + name) for name in order})
    tree["unread_variables"] = {name: sorted(values) for name, values in sorted(bindings.items())
                                if name not in order}
    return tree


def _differences(left: Any, right: Any, path: str = "") -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        out = []
        for key in sorted(set(left) | set(right)):
            out += _differences(left.get(key), right.get(key), f"{path}.{key}" if path else key)
        return out
    if path.endswith("m_TargetFilters") and isinstance(left, list) and isinstance(right, list) \
            and sorted(_key(x) for x in left) == sorted(_key(x) for x in right):
        return []   # the parts of an AND or OR are unordered
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        out = []
        for index, (a, b) in enumerate(zip(left, right)):
            out += _differences(a, b, f"{path}.{index}")
        return out
    return [] if left == right else [path or "record"]


def coverage(guids: list[str]) -> dict[str, Any]:
    """Round-trip ``guids`` (decompile, compile, compare): the exact ones, the ones read
    but not reproduced, the unsupported ones, the shapes in use and the first reasons."""
    exact, inexact, unsupported = [], [], []
    shapes, reasons = Counter(), Counter()
    for guid in guids:
        result = decompile(guid)
        if result["exact"]:
            exact.append(guid)
            shapes.update({clause["shape"] for clause in result["parsed"]["clauses"]})
        else:
            (inexact if result["supported"] else unsupported).append(guid)
            for reason in result["reasons"][:1]:
                reasons[re.sub(r"\d+", "N", re.sub(r"[0-9a-f-]{36}", "GUID", reason))] += 1
    return {"total": len(guids), "exact": exact, "inexact": inexact, "unsupported": unsupported,
            "shapes": shapes, "reasons": reasons}


# ---------------------------------------------------------------- the builder
#
# The card editor's guided builder (cards.js) offers a sentence a step at a time: the
# trigger, the effect (a shape), its target and slots, the duration and the condition.
# Each choice it offers is a catalogue phrase that reads back: put into a clause whose
# other parts are at a sample, the sentence parses to the same shape and renders to the
# same text (``_reads_back``). Its templates are sentences with blanks, offered only when,
# with each blank at its sample, they compile and round-trip exactly (``round_trip``).

# Ready-made sentences. ``None`` is a blank the author fills; a clause whose shape is
# None leaves the whole effect to the builder.
TEMPLATES: tuple[dict[str, Any], ...] = (
    {"clauses": [{"shape": "damage", "target": "target troop", "amount": None}]},
    {"clauses": [{"shape": "damage", "target": "target champion or troop", "amount": None}]},
    {"clauses": [{"shape": "draw", "target": "you", "amount": None}]},
    {"clauses": [{"shape": "heal", "target": "you", "amount": None}]},
    {"clauses": [{"shape": "attack", "target": "target troop", "amount": None}]},
    {"clauses": [{"shape": "attack", "target": "target troop", "amount": None, "duration": "this turn"}]},
    {"clauses": [{"shape": "defense", "target": "target troop", "amount": None}]},
    {"clauses": [{"shape": "keyword", "target": "target troop", "keyword": None}]},
    {"clauses": [{"shape": "summon", "target": "you", "amount": None, "card": None}]},
    {"clauses": [{"shape": "destroy", "target": "target troop"}]},
    {"clauses": [{"shape": "to_owners_hand", "target": "target troop"}]},
    {"trigger": "when this enters play", "clauses": [{"shape": None}]},
    {"trigger": "when this dies", "clauses": [{"shape": None}]},
    # Replacements, as Records writes them: "If this would die, void it instead."
    {"trigger": "if this would die", "clauses": [{"shape": "void", "target": "this"}]},
    {"trigger": "if this would enter your crypt", "clauses": [{"shape": "void", "target": "this"}]},
    {"trigger": "if damage would be dealt to this", "clauses": [{"shape": None}]},
    {"trigger": "if you would draw a card", "clauses": [{"shape": None}]},
    {"trigger": "if you would lose the game", "clauses": [{"shape": "set_health", "target": "you", "amount": None}]},
)
# A clause's parts besides its shape, as ``render`` reads them.
CLAUSE_PARTS = ("target", "amount", "count", "duration", "condition") + tuple(
    slot for slot in SLOT_PATTERNS if slot not in ("target", "amount", "s"))

_BUILDER_LOCK = threading.Lock()
_BUILDER: dict[str, Any] = {}
_BUILDER_TARGETS: dict[str, list[dict[str, Any]]] = {}


def _clause(**parts: Any) -> dict[str, Any]:
    return dict({"target": "", "amount": "", "count": "", "duration": "", "condition": "",
                 "layer": 0, "recalculate": ""}, **parts)


def _shape_slots(shape: dict[str, Any]) -> list[str]:
    """The slots a shape's sentences have, target and amount included, in phrase order."""
    words = shape["phrase"] + " " + shape.get("you", "")
    found = sorted((words.index("{" + slot + "}"), slot) for slot in SLOT_PATTERNS
                   if slot != "s" and "{" + slot + "}" in words)
    return [slot for _, slot in found]


def _reads_back(trigger: str, clause: dict[str, Any]) -> bool:
    """Whether the one-clause sentence parses to the same trigger and shape and renders
    to the same text."""
    text = render({"trigger": trigger, "clauses": [_clause(**clause)]})
    try:
        parsed = parse(text)
    except ValueError:
        return False
    if len(parsed["clauses"]) != 1 or parsed["trigger"] != trigger or render(parsed) != text:
        return False
    # The effect record it stores must read as the same shape (a slot value at its client
    # default, such as the keyword "Unknown", is dropped and the shape lost).
    read, shape = parsed["clauses"][0], SHAPE_BY_ID[clause["shape"]]
    found = _shape_of(_canon(_substitute(shape["record"], _slot_values(read))))
    return read["shape"] == shape["id"] and bool(found) and found[0]["id"] == shape["id"]


def _top_name(table: dict[str, Any]) -> str:
    uses = table["uses"]
    for guid in sorted(uses, key=lambda g: (-uses[g], g)):
        if table["name"].get(guid) and '"' not in table["name"][guid]:
            return _name_slot(table, guid)
    return ""


def _sample(shape_id: str) -> dict[str, Any]:
    """A full clause of the shape with each part at a sample (the Records favourite where
    there is one): the background against which one choice is checked."""
    cat = catalogue()
    shape = SHAPE_BY_ID[shape_id]
    used = cat["shape_targets"].get(shape_id) or Counter()
    target = next((cat["targets"].display[key] for key, _ in used.most_common()
                   if key in cat["targets"].display), "you" if shape.get("you") else "target troop")
    values = {"target": target, "amount": _amount_text(shape, 2), "card": _top_name(cat["cards"]),
              "counter": _top_name(cat["counters"]), "ability": _top_name(cat["abilities"]),
              "conversation": _top_name(cat["conversations"]),
              "deck": _top_name(cat["decks"]), "shard": SHARDS[0].lower(), "number": 1}
    clause = dict({slot: values.get(slot, "") for slot in _shape_slots(shape)}, shape=shape_id)
    # An enumerated slot takes the first value that reads back with the rest.
    for slot, members in (("keyword", cat["keywords"]), ("attribute", cat["attributes"]),
                          ("event", [e.rsplit(".", 1)[-1] for e in cat["events"]]),
                          ("filter", [row["phrase"] for row in cat["filters"].listing()])):
        if slot in clause:
            clause[slot] = next((v for v in members if _reads_back("", dict(clause, **{slot: v}))), "")
    return clause


def builder() -> dict[str, Any]:
    """What the builder offers at each step that does not depend on the target: the
    shapes (with their slots, usual duration and other durations), the triggers,
    conditions, 'for each' phrases and enumerated slot values that read back, and the
    templates. Built once, under a lock."""
    with _BUILDER_LOCK:
        if _BUILDER:
            return _BUILDER
    cat = catalogue()
    usual = _usual_durations()
    draw = _sample("draw")

    def checked(rows: list[dict[str, Any]], clause) -> list[dict[str, Any]]:
        return [row for row in rows if _reads_back(*clause(row["phrase"]))]

    def enum(slot: str, shape_id: str, values: list[str]) -> list[str]:
        sample = _sample(shape_id)
        return [value for value in values if _reads_back("", dict(sample, **{slot: value}))]

    shapes = []
    for shape in SHAPES:
        sample = _sample(shape["id"])
        if not _reads_back("", sample):
            continue   # a shape whose sample does not read back is not offered
        words = DURATIONS.get(usual.get(shape["id"], "Instant"), "at once")
        shapes.append({
            "id": shape["id"], "label": shape["label"], "phrase": shape["phrase"],
            "you": shape.get("you", ""), "signed": bool(shape.get("signed")),
            "article": bool(shape.get("article")),
            "slots": [slot for slot in _shape_slots(shape) if slot != "target"],
            "usual_duration": words,
            "durations": [w for w in DURATIONS.values()
                          if w != words and _reads_back("", dict(sample, duration=w))],
            # A shape that takes only some card values lists its own.
            **({"values": {"attribute": enum("attribute", shape["id"], cat["attributes"])}}
               if shape.get("flag") else {})})

    singular = dict(draw, amount=_amount_text(SHAPE_BY_ID["draw"], 1))
    built = {
        "shapes": shapes,
        # A replacement trigger ("if this would die") ends the sentence "instead".
        "triggers": [dict(row, replacement=is_replacement(row["phrase"]))
                     for row in checked(cat["triggers"].listing(), lambda p: (p, draw))],
        "conditions": checked(cat["conditions"].listing(), lambda p: ("", dict(draw, condition=p))),
        "counts": checked(cat["counts"].listing(), lambda p: ("", dict(singular, count=p))),
        "keywords": enum("keyword", "keyword", cat["keywords"]),
        "shards": enum("shard", "threshold", [shard.lower() for shard in SHARDS]),
        "attributes": enum("attribute", "set_attribute", cat["attributes"]),
        "events": enum("event", "fire_event", [e.rsplit(".", 1)[-1] for e in cat["events"]]),
        "filters": enum("filter", "summon_random", [row["phrase"] for row in cat["filters"].listing()]),
        # Display names (ToolName) of the keywords, for the builder's labels.
        "keyword_labels": {k["name"]: k["label"] for k in card_attributes.keywords()},
        "templates": templates(),
    }
    with _BUILDER_LOCK:
        _BUILDER.update(built)
        return _BUILDER


def builder_targets(shape_id: str) -> list[dict[str, Any]]:
    """The targets a shape reads back with: Records phrases and built ones, those Records
    uses with this shape first (``uses``), then by all their uses (``all_uses``)."""
    if shape_id not in SHAPE_BY_ID:
        raise LookupError(f"no shape {shape_id!r}")
    with _BUILDER_LOCK:
        if shape_id in _BUILDER_TARGETS:
            return _BUILDER_TARGETS[shape_id]
    cat = catalogue()
    vocab = cat["targets"]
    used = cat["shape_targets"].get(shape_id) or Counter()
    rows = [{"phrase": phrase, "uses": used.get(key, 0), "all_uses": vocab.uses.get(key, 0)}
            for key, phrase in vocab.display.items()]
    # Built phrases Records lacks. One whose content Records has under its own phrase
    # ("each artifact" is "all artifacts") would read back as that phrase, so it is left
    # to Records' phrase.
    for phrase in (f"{quantity} {side}{noun}{control}" for quantity in ("target", "each")
                   for side in ("", "opposing ") for noun in TARGET_NOUNS for control in ("", " you control")):
        content = _build_target(phrase)
        if phrase not in vocab.by_phrase and content is not None and _key(content) not in vocab.display:
            rows.append({"phrase": phrase, "uses": 0, "all_uses": 0})
    sample = _sample(shape_id)
    rows = [row for row in rows if _reads_back("", dict(sample, target=row["phrase"]))]
    rows.sort(key=lambda row: (-row["uses"], -row["all_uses"], row["phrase"]))
    with _BUILDER_LOCK:
        _BUILDER_TARGETS[shape_id] = rows
    return rows


def builder_names(kind: str, query: str = "", limit: int = 60) -> list[dict[str, Any]]:
    """Card, counter or ability names that read back in a slot, matching ``query``, the
    most used in Records first. Each value is the slot as written ("Name" [prefix])."""
    if kind not in NAME_TABLES:
        raise LookupError(f"no name kind {kind!r}")
    table = catalogue()[NAME_TABLES[kind]]
    query = re.sub(r"\s+", " ", query.strip().strip('"').lower())
    uses = table["uses"]
    found = sorted((guid for guid, name in table["name"].items() if name and query in name.lower()),
                   key=lambda g: (-uses[g], len(table["name"][g]), table["name"][g].lower(), g))
    out = []
    for guid in found:
        name = table["name"][guid]
        if '"' in name or re.search(r"<[^>]+>", name):
            continue   # the sentence could not hold it
        value = _name_slot(table, guid)
        prefix = value.rsplit("[", 1)[1].rstrip("]") if value.endswith("]") else ""
        try:
            if _name_lookup(table, name, prefix) != guid:
                continue
        except ValueError:
            continue
        out.append({"value": value, "uses": uses[guid]})
        if len(out) >= limit:
            break
    return out


def _blank_words(shape: dict[str, Any], slot: str) -> str:
    """How a template's label shows a blank: "N" for an amount, else "[slot]"."""
    if slot == "amount":
        return "+N" if shape.get("signed") else "N"
    return f"[{slot}]"


def _template_row(template: dict[str, Any]) -> dict[str, Any]:
    """A template as the builder takes it: ``parts`` with its blanks as None, the
    ``label`` with the blanks shown, and ``text``, the sentence with each blank at its
    sample (what ``round_trip`` checks)."""
    trigger = template.get("trigger", "")
    filled, labels = [], []
    for parts in template["clauses"]:
        if parts.get("shape") is None:
            filled.append(_sample("draw"))
            labels.append("...")
            continue
        shape = SHAPE_BY_ID[parts["shape"]]
        clause = dict(_sample(shape["id"]), **{k: v for k, v in parts.items() if v is not None})
        filled.append(clause)
        shown = dict(clause, **{k: _blank_words(shape, k) for k, v in parts.items() if v is None})
        labels.append(_render_clause(shape, _clause(**shown), shown["target"] == "you"))
    if trigger and is_replacement(trigger):
        labels[-1] += " " + INSTEAD
    label = ", ".join(([trigger] if trigger else []) + labels[:1]) + "".join(
        f". Then {words}" for words in labels[1:])
    return {"label": label[:1].upper() + label[1:], "parts": {"trigger": trigger, "clauses": list(template["clauses"])},
            "text": render({"trigger": trigger, "clauses": [_clause(**c) for c in filled]})}


def template_problems() -> dict[str, list[str]]:
    """``{label: reasons}`` for each template that does not compile and round-trip."""
    out = {}
    for template in TEMPLATES:
        row = _template_row(template)
        reasons = round_trip(row["text"])
        if reasons:
            out[row["label"]] = reasons
    return out


def templates() -> list[dict[str, Any]]:
    """The templates that compile and round-trip exactly (``template_problems``)."""
    rows = [_template_row(template) for template in TEMPLATES]
    return [row for row in rows if not round_trip(row["text"])]


def round_trip(text: str) -> list[str]:
    """Why ``text`` does not compile to records that read back to the same sentences and
    compile again to the same records; empty when it does. All in memory: the compiled
    new records are looked up from a copy of the overlay, and nothing is written."""
    try:
        compiled = compile_text(text)
    except ValueError as error:
        return [f"does not compile: {error}"]
    if compiled["text"] != text:
        return [f"compiles as {compiled['text']!r}"]
    stored = campaign_data.load_ability_overlay()
    overlay = {section: dict(stored.get(section, {}), **compiled["pending"].get(section, {}))
               for section in set(stored) | set(compiled["pending"])}
    try:
        again = render(_read_root(compiled["root"], overlay))
    except Unsupported as error:
        return error.reasons
    if again != text:
        return [f"reads back as {again!r}"]
    recompiled = compile_text(again)
    return [f"differs at {d}" for d in _differences(
        _tree(compiled["root"], compiled["pending"], overlay),
        _tree(recompiled["root"], recompiled["pending"], overlay))]


def read_text(text: str) -> dict[str, Any]:
    """The builder's reading of free text: ``parsed``, or the parse ``error`` with the
    ``partial`` parts it could read up to there, so the builder can open at the gap."""
    try:
        return {"ok": True, "parsed": _for_builder(parse(text))}
    except ValueError as error:
        return {"ok": False, "error": str(error), "partial": _for_builder(_read_partial(text))}


def _for_builder(parsed: dict[str, Any]) -> dict[str, Any]:
    """Parsed parts without their resolved GUIDs, each amount also as a number."""
    clauses = []
    for clause in parsed["clauses"]:
        clause = {k: v for k, v in clause.items() if not k.endswith("_guid") and k != "event_type"}
        if clause.get("amount"):
            clause["amount_value"] = _number(clause["amount"])
        clauses.append(clause)
    return dict(parsed, clauses=clauses)


def _read_partial(text: str) -> dict[str, Any]:
    """The parts of ``text`` before the first sentence that does not parse, and that
    sentence's readable parts (``_guess_clause``); an unknown trigger is None."""
    lines = str(text or "").replace("\r", "").split("\n")
    good: dict[str, Any] = {"settings": [], "requires": "", "trigger": "", "clauses": []}
    for count in range(len(lines)):
        try:
            good = parse("\n".join(lines[:count + 1]))
        except ValueError:
            break
    else:
        return good
    out = dict(good, clauses=list(good["clauses"]))
    line = re.sub(r"^(?:- |then,? |and then,? )", "", _line(lines[count]), flags=re.I)
    if line.lower().startswith(("settings:", "requires:")):
        return out
    if not out["clauses"] and not out["trigger"]:
        try:
            out["trigger"], line = _split_trigger(line)
        except ValueError:
            parts = re.split(r"(?<=\S)(?: - |[,:])", line, maxsplit=1)
            out["trigger"], line = None, (parts[1] if len(parts) > 1 else "").strip()
    for sentence in _sentences(line):
        try:
            out["clauses"].append(_parse_clause(sentence))
        except ValueError:
            guess = _guess_clause(sentence)
            if guess:
                out["clauses"].append(guess)
            break
    return out


def _guess_clause(text: str) -> dict[str, Any] | None:
    """An unreadable clause's readable parts: the first shape whose words match, each
    slot kept only when it resolves and None (a blank) when it does not."""
    cat = catalogue()
    text = re.sub(_SUFFIX.pattern, "", text, flags=re.I)
    text = re.sub(rf"\s+{INSTEAD}\s*$", "", text, flags=re.I)   # a replacement's last clause
    best, score = None, -1
    for effect, count, duration, condition in _split_clause(text):
        for shape, you, pattern in _PATTERNS:
            found = pattern.fullmatch(effect)
            if not found:
                continue
            slots = found.groupdict()
            clause: dict[str, Any] = _clause(shape=shape["id"], duration=duration)
            target = "you" if you else (slots.get("target") or "").lower()
            if target in cat["targets"].by_phrase:
                clause["target"] = cat["targets"].display.get(cat["targets"].by_phrase[target], target)
            else:
                clause["target"] = target if _build_target(target) is not None else None
            clause["amount"] = (slots.get("amount") or "").lower()
            if count:
                key = cat["counts"].by_phrase.get(count)
                clause["count"] = cat["counts"].display.get(key, count) if key else None
                clause["amount"] = clause["amount"] if key else None
            if condition:
                key = cat["conditions"].by_phrase.get(condition)
                clause["condition"] = cat["conditions"].display.get(key, condition) if key \
                    else condition if _parse_threshold(condition) else None
            for slot in _shape_slots(shape):
                if slot in ("target", "amount") or slots.get(slot) is None:
                    continue
                read = dict(clause)
                try:
                    _read_slots(read, {slot: slots[slot], slot + "_id": slots.get(slot + "_id")})
                    clause[slot] = read[slot]
                except ValueError:
                    clause[slot] = None
            # The reading that resolves the most parts wins; the simplest on a tie.
            resolved = sum(value not in (None, "") for value in clause.values())
            if resolved > score:
                best, score = clause, resolved
            break
    return best


def options() -> dict[str, Any]:
    """The dropdowns of the sentence form."""
    cat = catalogue()

    def named(table: str, limit: int) -> list[str]:
        uses = cat[table]["uses"]
        return [_name_slot(cat[table], guid) for guid in sorted(uses, key=lambda g: -uses[g])[:limit]
                if cat[table]["name"].get(guid)]

    return {
        "shapes": [{"id": s["id"], "label": s["label"], "phrase": s["phrase"], "you": s.get("you", ""),
                    "signed": bool(s.get("signed")),
                    "slots": [slot for slot in SLOT_PATTERNS if slot != "s" and "{" + slot + "}" in
                              s["phrase"] + s.get("you", "")]}
                   for s in SHAPES],
        "triggers": cat["triggers"].listing(),
        "targets": cat["targets"].listing(),
        "conditions": cat["conditions"].listing(),
        "counts": cat["counts"].listing(),
        "durations": list(DURATIONS.values()),
        "keywords": cat["keywords"],
        "attributes": cat["attributes"],
        "events": [event.rsplit(".", 1)[-1] for event in cat["events"]],
        "shards": [shard.lower() for shard in SHARDS],
        "cards": named("cards", 600),
        "counters": named("counters", 300),
        "abilities": named("abilities", 600),
        "settings": [words for _, words, _ in SETTINGS] + [verb + " {target}" for _, verb in COST_TARGETS],
        "recalculate": list(RECALCULATE.values()),
    }


def warm() -> None:
    """Build the catalogue and the builder's choices ahead of the first request."""
    catalogue()
    builder()
