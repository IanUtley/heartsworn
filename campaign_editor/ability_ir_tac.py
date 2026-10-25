"""The client's TAC codec: a TAC payload (``m_SerializedTAC``) read into a dict and written back.

A payload is version 2 (int16), then entries of ``(int32 key, value)`` ending in a zero key. The
key is the first four bytes of the MD5 of the attribute's ASCII name (the two bytes at 0 and 3
forced non-zero), read big-endian. The value depends on the attribute's kind: a length-prefixed
string, an int32, a nested TAC with no version, or a nested TAC for each entry of a list.

The name tables are generated from hex-private/HexClient/Assembly-CSharp-firstpass/Game/Shared/
Mechanics (every ``new IntAttrs/StringAttrs/TACAttrs/ListAttrs("name"[, "plural"])``). Over the
Records, every one of the 9,248 payloads decodes and re-encodes to the same bytes.
"""

from __future__ import annotations

import base64
import hashlib
import struct
from typing import Any

INT_NAMES = (
    "AIMustPlay",
    "ActionsCast",
    "ActivationCost",
    "ActivationCostModifier",
    "ActiveCollections",
    "AdditionalResourcesPlayableOnYourTurn",
    "AllowYardInspire",
    "AlternateArt",
    "AmountIsNumMatchingCost",
    "AnyPlayerMayActivate",
    "AppendToPlayer",
    "Armor",
    "ArmorUsed",
    "Assault",
    "Attack",
    "AttackedThisTurn",
    "BasicActionSpeed",
    "Blood",
    "Boon",
    "Boost",
    "BriarLegionsPlayedThisGame",
    "BuryBonus",
    "CEActiveInAllZones",
    "CEsCreated",
    "CanIgnoreActivatedAbilitiesThresholds",
    "CanIgnoreCardsThresholds",
    "CanPlayCardsForFree",
    "CanPlayOpposingTopOfDeck",
    "CanPlayTopOfDeck",
    "CanSeeOpponentsHand",
    "CanSeeOpponentsTopOfDeck",
    "CanSeeTopOfDeck",
    "CanSeeUndergroundTroops",
    "CantActivateAbilities",
    "CantAddDazedCounters",
    "CantAttack",
    "CantBeBlocked",
    "CantBeBlockedByBeasts",
    "CantBeExhaustedByOpponent",
    "CantBeInterrupted",
    "CantBePlayed",
    "CantBlock",
    "CantCreateCopies",
    "CantCreateReplicas",
    "CantDrawCards",
    "CantGainHealth",
    "CantGoFirst",
    "CantHealAtEndOfTurn",
    "CantIncludeInDeck",
    "CantLoseHealth",
    "CantPayHealth",
    "CantPlayCards",
    "CantPlayCardsFromHand",
    "CantReady",
    "CantReadyNormally",
    "CantReadyNormallyHidden",
    "CantRemoveDazedCounters",
    "CantReplenishResources",
    "CantRevert",
    "CantUseChargePowers",
    "CantUsePaymentPowers",
    "CardCollection",
    "CardsCast",
    "CardsCreated",
    "CardsDestroyed",
    "CardsDiscarded",
    "CardsDrawn",
    "ChampionClass",
    "ChampionGender",
    "ChampionLevel",
    "ChampionRace",
    "ChanceToHappen",
    "ChanceToPreventCombatDamage",
    "ChanceToPreventNonCombatDamage",
    "ChargePointBonus",
    "ChargePointCost",
    "ChargePointCostModifier",
    "ChargePointsDrained",
    "ChargePointsGained",
    "ChargePointsSpent",
    "ChargePowerIsFree",
    "ChargePowersUsed",
    "Charges",
    "CombatDamageChampionModifier",
    "CombatDamageDealtToOpponent",
    "CombatDamageMultiplier",
    "CombatDamageReceivedModifier",
    "CombatDamageReceivedMultiplier",
    "CombatDamageTaken",
    "Conscript",
    "ConscriptUnderworldBonus",
    "ContingentEffect",
    "Cooldown",
    "CopyGems",
    "Cost",
    "Cost1CreationBonus",
    "CountersRemoved",
    "CreateRandomRobotInsteadOfWorkerBotIfThisIsInPlay",
    "CreateShinhareMilitiaInsteadOfBattleHopper",
    "Crush",
    "CurrentResources",
    "DamageChampionModifier",
    "DamageChampionMultiplier",
    "DamageDealt",
    "DamageDealtToOpponent",
    "DamageModifierDwarfOrArtifact",
    "DamageMultiplier",
    "DamagePrevented",
    "DamageReceivedModifier",
    "DamageReceivedMultiplier",
    "DamageTaken",
    "DealtDamageAlreadyThisCombat",
    "DeathSentence",
    "Deathcries",
    "Deathcry",
    "DeckbuildingRequires20DifferentNonResources",
    "DeckbuildingRequiresMinimumAmount",
    "DeckbuildingRequiresMinimumDifferentAmount",
    "Defense",
    "Defensive",
    "Deploy",
    "DeployHidden",
    "Diamond",
    "Diligence",
    "DungeonConseqLosses",
    "DungeonLives",
    "DungeonLosses",
    "DungeonWins",
    "EffectSourceCardId",
    "EffectsRun",
    "EggCreationBonus",
    "EntersPlayAttacking",
    "EntersPlayExhausted",
    "Enthralled",
    "Escalation",
    "ExcessDamageDealt",
    "ExhaustsOnUse",
    "FaceDown",
    "Faction",
    "Fateweave",
    "Feral",
    "Flight",
    "Frostform",
    "Fusible",
    "Gladiator",
    "GladiatorBoth",
    "GrantsHiddenAbility",
    "HalfOfDeck",
    "HasCost",
    "HasXCost",
    "HealthCost",
    "HealthGained",
    "HiddenAbility",
    "HighestCostCardsCast",
    "Id",
    "IdOfCardThatMovedMeLast",
    "IgnoresChain",
    "IgnoresDefensive",
    "Illuminate",
    "Illuminated",
    "Immediate",
    "Immortality",
    "IncludeSourceCardGuid",
    "Indexed",
    "InputState",
    "Inspire",
    "InspireCount",
    "Invincible",
    "Irreversible",
    "IsCopy",
    "IsDungeon",
    "IsDungeonBoss",
    "IsManual",
    "IsReplica",
    "LearnSpellCount",
    "Lethal",
    "LethalDamageTaken",
    "LifeDrain",
    "LifeGainModifier",
    "LifeGainMultiplier",
    "Lifebound",
    "Marked",
    "MaxCardsDrawablePerTurn",
    "MaxICanBlock",
    "MaximumHandSizeModifiers",
    "Meditating",
    "MinConstructedDeckSize",
    "MinLimitedDeckSize",
    "Mobilize",
    "ModifierMustBePermanent",
    "Momentum",
    "MustAttack",
    "MustBeBlocked",
    "MustBeInvoked",
    "MustBlock",
    "MustGoFirst",
    "NonCombatDamageDealtToOpponent",
    "NonCombatDamageModifier",
    "NonCombatDamageMultiplier",
    "NonCombatDamageReceivedModifier",
    "NonCombatDamageReceivedMultiplier",
    "NonCombatDamageReduction",
    "NonResourceCardsCast",
    "NumAttackers",
    "NumCardsEnteredCrypt",
    "NumCardsEnteredCryptFromPlay",
    "NumTroopsEnteredPlay",
    "Offset",
    "OpposingCryptPowersCantBeUsed",
    "OpposingDeathcriesCantTrigger",
    "OpposingDeploysCantTrigger",
    "OpposingNonArdentTroopsEnterPlayExhausted",
    "OpposingTroopsDontHealAtEndOfTurn",
    "OpposingTroopsEnterPlayExhausted",
    "Option",
    "OriginalIsReplica",
    "OwnerCanPlayForFree",
    "PlayedFromHand",
    "Portal",
    "PreEventTrigger",
    "PreventCombatDamage",
    "PreventDamageFromCardsThatHaveOwnersThreshold",
    "PreventMyCombatDamage",
    "PreventMyNonCombatDamage",
    "PreventNonCombatDamage",
    "Prophecy",
    "Prophesied",
    "Quick",
    "Rabid",
    "RabidNotOneShot",
    "Rage",
    "ReadyYourTroops",
    "Rebirth",
    "RecalculateTargets",
    "RefusesToBlock",
    "ReplenishResourcesEachTurn",
    "ResourceCardsCast",
    "ResourcesDepleted",
    "ResourcesGained",
    "ResourcesLost",
    "ResourcesReplenished",
    "Rowdy",
    "Ruby",
    "Runic",
    "SameName",
    "SameOwner",
    "Sapphire",
    "Scrounge",
    "SecondaryTargetIndex",
    "Secret",
    "SettlersCreationBonus",
    "Shell",
    "ShinhareCreationBonus",
    "SkipsAttackPhase",
    "SkipsDrawPhase",
    "SkyGuard",
    "Soulcursed",
    "SourceDestination",
    "Spectral",
    "Speed",
    "SpellPointCost",
    "SpellPointCostModifier",
    "SpellPointsGained",
    "SpellPointsSpent",
    "Spellshield",
    "SporeaticGrowth",
    "StartingDeckSize",
    "StartingHandSizeModifiers",
    "StartingHealth",
    "Steadfast",
    "StoreInAbility",
    "Stun",
    "Swiftstrike",
    "Tamed",
    "TargetMustBeInPlay",
    "TimesAttacked",
    "TimesDied",
    "TimesDiscarded",
    "TimesExhausted",
    "TimesHealthGained",
    "TotalResources",
    "Toxified",
    "Trained",
    "TroopsCast",
    "TroopsCastLimitPerTurn",
    "Tunneling",
    "TurnCreated",
    "TurnsTaken",
    "Unique",
    "Unlimited",
    "UnlimitedHandSize",
    "Unlucky",
    "Untamed",
    "UseSourceDestination",
    "UseStoredTarget",
    "UsesPerGame",
    "UsesPerTurn",
    "Valorous",
    "Verdict",
    "VerdictAncient",
    "VerdictChoice",
    "WarlordCount",
    "Wild",
    "XCostMinimum",
    "YouCanActivateYourChargePowersAsThoughTheyWereQuick",
)

STRING_NAMES = (
    "AddNoOpTarget",
    "AdditionCardText",
    "BaseVersionGuid",
    "CompareWith",
    "DuplicateAbilityText",
    "FunctionName",
    "GainedCounterType",
    "Guid",
    "InputVariableName",
    "ListName",
    "Name",
    "RandomPowerTemplate",
    "RemovedCounterType",
    "SourceCardGuid",
    "StoredName",
    "UniqueTag",
    "Where",
)

TAC_NAMES = (
    "CardGameStats",
    "CardStatsThisTurn",
    "CardStatsWithSpecificDuration",
    "Condition",
    "DataToAppend",
    "HasAsSubset",
    "MinimumValues",
    "PermanentData",
    "PlayerGameStats",
    "PlayerHighestTurnStats",
    "PlayerStatsThisTurn",
    "ThisTurnsData",
)

LIST_NAMES = (
    "Abilities",
    "AlternateVersions",
    "BuriedCards",
    "CardsPutIntoDeck",
    "CardsPutIntoHand",
    "CardsThatDied",
    "ChampionTalents",
    "ChosenShards",
    "Conditions",
    "CounteredCards",
    "CreatedCards",
    "DamagedCards",
    "DestroyedCards",
    "DiscardedCards",
    "DrawnCards",
    "ExhaustedCards",
    "ExtraCombatsThisTurn",
    "FusedComponents",
    "GUIDs",
    "GrantedPowers",
    "InterruptedCards",
    "MovedCards",
    "PlayerOptions",
    "RequiredEquipment",
    "RequiredTalents",
    "RevealedCards",
    "RevertedCards",
    "SacrificedCards",
    "SpellPointCostModifiers",
    "StoredTargets",
    "TamedCards",
    "VoidedCards",
)

VERSION = 2


def attribute_hash(name: str) -> int:
    """The key the client writes for an attribute name."""
    digest = bytearray(hashlib.md5(name.encode("ascii")).digest()[:4])
    if digest[0] == 0:
        digest[0] = 1
    if digest[3] == 0:
        digest[3] = 1
    return struct.unpack(">i", bytes(digest))[0]


def _table() -> dict[int, tuple[str, str]]:
    table: dict[int, tuple[str, str]] = {}
    for kind, names in (("ints", INT_NAMES), ("strings", STRING_NAMES), ("tacs", TAC_NAMES),
                        ("lists", LIST_NAMES)):
        for name in names:
            table[attribute_hash(name)] = (kind, name)
    return table


KEYS = _table()
NAME_KEYS = {name: key for key, (_, name) in KEYS.items()}


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def int32(self) -> int:
        value = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return value

    def string(self) -> str:
        length, shift = 0, 0
        while True:   # the .NET 7-bit length prefix
            byte = self.data[self.pos]
            self.pos += 1
            length |= (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                break
        text = self.data[self.pos:self.pos + length].decode("utf-8")
        self.pos += length
        return text


def _read_entries(reader: _Reader, out: dict[str, Any]) -> dict[str, Any]:
    while True:
        key = reader.int32()
        if key == 0:
            return out
        if key not in KEYS:
            raise ValueError(f"unknown TAC attribute key {key}")
        kind, name = KEYS[key]
        if kind == "strings":
            out[name] = reader.string()
        elif kind == "ints":
            out[name] = reader.int32()
        elif kind == "tacs":
            out.setdefault(name, {}).update(_read_entries(reader, {}))
        else:
            out.setdefault(name, []).append(_read_entries(reader, {}))


def decode(payload: str) -> dict[str, Any]:
    """The attributes of a base64 TAC payload, in the order they are stored."""
    reader = _Reader(base64.b64decode(payload))
    version = struct.unpack_from("<h", reader.data, 0)[0]
    reader.pos = 2
    if version != VERSION:
        raise ValueError(f"TAC version {version} is not read here")
    return _read_entries(reader, {})


def _write_string(out: bytearray, text: str) -> None:
    raw = text.encode("utf-8")
    length = len(raw)
    while True:
        byte = length & 0x7F
        length >>= 7
        if length:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    out.extend(raw)


def _write_entries(out: bytearray, values: dict[str, Any]) -> None:
    for name, value in values.items():
        key = NAME_KEYS[name]
        kind = KEYS[key][0]
        if kind == "lists":
            for item in value:
                out.extend(struct.pack("<i", key))
                _write_entries(out, item)
        elif kind == "strings":
            out.extend(struct.pack("<i", key))
            _write_string(out, value)
        elif kind == "ints":
            out.extend(struct.pack("<i", key))
            out.extend(struct.pack("<i", value))
        else:
            out.extend(struct.pack("<i", key))
            _write_entries(out, value)
    out.extend(struct.pack("<i", 0))


def encode(values: dict[str, Any]) -> str:
    """The base64 TAC payload for a dict from ``decode``."""
    out = bytearray(struct.pack("<h", VERSION))
    _write_entries(out, values)
    return base64.b64encode(bytes(out)).decode("ascii")
