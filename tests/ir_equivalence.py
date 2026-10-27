"""Equivalence harness: each covered ability, run on the live engine and on the IR interpreter from the
same starting battle, must leave the same battle state and game_cards rows.

Run as a script from the repo root:

    python3 tests/ir_equivalence.py

Each case names an ability and an event. The harness finds a real card template that carries the
ability, places it in the warzone of its owner, runs the event through the live trigger engine
(``dispatch_native_trigger``) on one fresh database, and runs the interpreter (``run_ability``) on
another fresh database built the same way. It then compares the battle snapshot. Abilities the
interpreter does not cover are listed as uncovered, not run.
"""

import copy
import functools
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import tests.tests_builtin_counter_rules as scenarios  # noqa: E402  (sets up the test database first)
import db as dbmod  # noqa: E402
from tests.tests_combat import SessionStub, add_card, make_db  # noqa: E402

TROOP_OWNER = 5          # the player (owns the player champion)
OPPOSING_OWNER = 0       # the AI (owns the AI champion)


def _any_template(db, ability_guid):
    """A troop-first card template that carries the ability, whatever else it carries. For a direct resolution
    (no trigger dispatch), the card's other abilities do not run, so only the ability itself matters."""
    row = db.execute("SELECT guid FROM card_templates WHERE abilities_json LIKE ? "
                     "ORDER BY (card_type='Troop') DESC LIMIT 1", (f"%{ability_guid}%",)).fetchone()
    return row[0] if row else None


_EVENTS = {}


def _event_of(ability_guid):
    """The trigger event an ability names (its short name), or "" for a static ability. The events of all abilities
    are read once, so a lookup costs a dictionary access."""
    from gamedata import DEFAULT_RECORD_STORE as STORE
    if not _EVENTS:
        for record in STORE.load("AbilityTemplate"):
            raw = record.to_dict(include_metadata=True)
            _EVENTS[record.guid.lower()] = str(
                (raw.get("m_TriggerEventType") or {}).get("m_InternalType") or "").rsplit(".", 1)[-1]
    return _EVENTS.get(ability_guid, "")


# Events whose cards are rare enough that a card carrying another triggered ability is the only one that can stand in
# for them (a charge power's card, an inspiring troop's, a battle's, a destroyed artifact's).
RELAXED_TEMPLATE_EVENTS = {"CardActivatedEvent", "CardBattledEvent", "GainThresholdEvent"}


def _template_for(db, ability_guid, event):
    """A troop-first card template for this ability. Another triggered ability on the card can fire from this
    ability's effects (an Inspire on a summoning troop, say), and the live side would then run it too, so a
    card with any other triggered ability cannot stand for this ability alone. None when no usable card exists."""
    rows = db.execute(
        "SELECT guid, abilities_json FROM card_templates WHERE abilities_json LIKE ? "
        "ORDER BY (card_type='Troop') DESC",
        (f"%{ability_guid}%",)).fetchall()
    for guid, abilities in rows:
        others = [item for item in json.loads(abilities or "[]") if item.lower() != ability_guid]
        # The harness dispatches one event per case, so only another ability that fires on the same event would also
        # run from it; an ability on another event is left to the live engine on both sides.
        strict = event not in RELAXED_TEMPLATE_EVENTS
        if (all(not _event_of(item.lower()) for item in others) if strict
                else all(_event_of(item.lower()) != event for item in others)):
            return guid
    return None


MOVED_UID = 503


SOURCE_UID = 900
OTHER_UID = 901


@functools.lru_cache(maxsize=None)
def _test_champion_guid():
    """A champion template with a subtype line, for the battle's champions (the same one every time)."""
    from gamedata import DEFAULT_RECORD_STORE as STORE
    for record in sorted(STORE.load("ChampionTemplate"), key=lambda r: r.guid.lower()):
        if str(record.to_dict(include_metadata=True).get("m_SubType") or ""):
            return record.guid.lower()
    return None


class _SeededRng:
    """The battle's session RNG (``next(bound)`` and ``next(min, max)``, as the kernel's), seeded per ability so both
    sides draw the same numbers."""

    def __init__(self, seed):
        import random
        self._random = random.Random(seed)

    def next(self, *bounds):
        if len(bounds) == 2:
            return self._random.randrange(int(bounds[0]), int(bounds[1]))
        return self._random.randrange(bounds[0])


def _scenario(ability_guid, opposing_troops, moved=None, extra=(), champion=False, any_template=False):
    """A fresh battle with the ability's troop in play for its owner, and the opposing troops.
    ``extra`` places more cards: (uid, owner, zone, permanent_buffs)."""
    import zlib
    # The previous scenario's database is closed, so a long run does not keep every copy in memory.
    previous = dbmod._db
    dbmod._db = make_db()
    db = dbmod._db
    if previous is not None and previous is not db:
        previous.close()
    pl_t, ai_t, game, handler, bstate, player_champ, ai_champ = scenarios._setup(db)
    bstate["_rules_rng"] = _SeededRng(zlib.crc32(ability_guid.encode()))
    # The battle's champions have templates, as a live battle's do: a subtype filter reads its champion's subtype.
    if getattr(handler, "_ai_champ_guid", None) is None:
        handler._ai_champ_guid = _test_champion_guid()
        handler._player_champ_guid = _test_champion_guid()
    for uid, owner, zone, buffs in extra:
        add_card(db, uid, owner, scenarios.TPL_GLADIATOR, loc=zone)
        db.execute("UPDATE game_cards SET permanent_buffs=? WHERE card_uid=?", (buffs, uid))
    if champion:
        # A champion built-in: the champions carry the ability, so no troop is placed for it. A moved card
        # (a troop that dies, say) still exists in the battle, in the zone it moved to.
        if moved and moved[0] != 900:
            uid, owner, source, destination = moved[:4]
            add_card(db, uid, owner, _moved_template(moved), loc=source)
            db.execute("UPDATE game_cards SET location=? WHERE card_uid=?", (destination, uid))
        db.commit()
        return db, pl_t, ai_t, game, handler, bstate, player_champ, ai_champ
    template = (_any_template(db, ability_guid) if any_template
                else _template_for(db, ability_guid, _event_of(ability_guid)))
    if template is None:
        return None
    # The ability's troop starts where a moved case says it does, when that case moves it.
    own_start = moved[2] if moved and moved[0] == 900 else "warzone"
    add_card(db, 900, TROOP_OWNER, template, loc=own_start)
    for offset, (uid, damage) in enumerate(opposing_troops):
        add_card(db, uid, OPPOSING_OWNER, scenarios.TPL_GLADIATOR, loc="warzone")
        if damage:
            db.execute("UPDATE game_cards SET card_damage=? WHERE card_uid=?", (damage, uid))
    if moved and moved[0] != 900:
        uid, owner, source, destination = moved[:4]
        add_card(db, uid, owner, _moved_template(moved), loc=source)
        db.execute("UPDATE game_cards SET location=? WHERE card_uid=?", (destination, uid))
    elif moved:
        db.execute("UPDATE game_cards SET location=? WHERE card_uid=900", (moved[3],))
    if not moved:
        _satisfy_own_conditions(db, bstate, ability_guid, template)
    db.commit()
    return db, pl_t, ai_t, game, handler, bstate, player_champ, ai_champ


def _nodes(value):
    """Every Records object under ``value``, the node itself first."""
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _nodes(item)
    elif isinstance(value, list):
        for item in value:
            yield from _nodes(item)


def _template_with_faction(db, faction):
    """A troop template of ``faction`` that the test database holds, from the Records (None when there is none)."""
    from gamedata import DEFAULT_RECORD_STORE as STORE
    for record in STORE.load("CardTemplate"):
        data = record.to_dict()
        if data.get("m_Faction") == faction and data.get("m_CardType") == "Troop" and \
                db.execute("SELECT 1 FROM card_templates WHERE guid=?", (record.guid.lower(),)).fetchone():
            return record.guid.lower()
    return None


def _satisfy_own_conditions(db, bstate, ability_guid, template):
    """Make the battle meet the ability's own conditions, as a battle in play would. The conditions are read from the
    ability's stored Records, not from a card name:

    - a count of the controller's cards (RequiresCardsControlled) gets that many copies of the source's template in
      hand, so the count can pass a filter the source itself fails;
    - a source-zone test (RequiresSourcePassesFilterCondition with InZone) puts the source in that zone;
    - a this-turn champion stat ("You>..." IntAttrFilter, above zero) is recorded with the live stat writer.

    The live path (rules_port.resolution) and the interpreter then both see a battle where the ability may run."""
    from game_engine import SHARD_TO_FLAG
    from rules_port import ir_exec
    from rules_port.runtime_helpers import participant_side
    from rules_port.statistics import add_champion_card_stat
    root = ir_exec._load("AbilityTemplate", ability_guid)
    conditions = [node.to_record() for node in (root.m_AbilityCondition, root.m_TriggerCondition)
                  if node is not None]
    copies = 0
    for condition in conditions:
        for node in _nodes(condition):
            kind = str(node.get("_t", "")).rsplit(".", 1)[-1]
            if kind == "RequiresCardsControlled" and str(node.get("m_PlayerFilter")) == "Self":
                # A copy of a template of the filter's faction, when it names one: the source may not be that faction.
                faction = next((item.get("m_Faction") for item in _nodes(node.get("m_CardFilter"))
                                if str(item.get("_t", "")).rsplit(".", 1)[-1] == "InFaction"), None)
                copy_of = (_template_with_faction(db, faction) if faction else None) or template
                for _ in range(int(node.get("m_RequiredQuantity") or 1)):
                    copies += 1
                    add_card(db, 2000 + copies, TROOP_OWNER, copy_of, loc="hand")
            if kind == "AbilityControllerHasThresholdAbilityCondition":
                # The controller's threshold, in the side key the live condition reads.
                side = participant_side(bstate, TROOP_OWNER)
                flag = int(SHARD_TO_FLAG[str(node["m_ColorFlags"]).lower()])
                thresholds = dict(bstate.get(f"{side}_threshold") or {})
                thresholds[flag] = int(node.get("m_RequiredQuantity") or 1)
                bstate[f"{side}_threshold"] = thresholds
            if kind == "RequiresSourcePassesFilterCondition":
                for zone in _nodes(node.get("m_Filter")):
                    if str(zone.get("_t", "")).rsplit(".", 1)[-1] == "InZone":
                        db.execute("UPDATE game_cards SET location=? WHERE card_uid=900",
                                   (str(zone["m_Collection"]).lower(),))
            if kind == "IntAttrFilter" and str(node.get("m_Attribute", "")).startswith("You>") and \
                    node.get("m_ComparisonOp") in ("GreaterThan", "GreaterThanOrEqual"):
                add_champion_card_stat(bstate, TROOP_OWNER, str(node["m_Attribute"]).split(">")[-1], 1)


# Chain bookkeeping: the ability's place on the chain, which the engine (not the effect) sends. The effects are
# what the comparison is about, so these are left out of both sides.
CHAIN_EVENTS = {"AbilityPushedOnChainSessionEventArgs", "ChainEmptySessionEventArgs",
                "RemovedTopOfChainSessionEventArgs", "TopOfChainResolvedSessionEventArgs",
                "AbilityCancelledSessionEventArgs"}


def _client_events(game, start=0):
    """The effects' events the game sent the client after ``start`` (its index before the run), in order: each
    one's class and its scalar attributes."""
    out = []
    for event in (getattr(game, "events", []) or [])[start:]:
        if type(event).__name__ in CHAIN_EVENTS:
            continue
        scalars = {key: value for key, value in vars(event).items()
                   if isinstance(value, (int, str, bool)) or value is None}
        out.append((type(event).__name__, sorted(scalars.items(), key=lambda kv: kv[0])))
    return out


def _snapshot(db, bstate, player_champ, ai_champ, game=None, start_events=0):
    rows = db.execute(
        "SELECT card_uid, user_id, location, card_damage, permanent_buffs FROM game_cards "
        "ORDER BY card_uid").fetchall()
    return {
        "events": _client_events(game, start_events),
        "player_health": bstate.get("player_health"),
        "ai_health": bstate.get("ai_health"),
        "champion_counters": copy.deepcopy(bstate.get("champion_counters") or {}),
        "cards": [(uid, owner, location, damage, json.loads(buffs or "{}"))
                  for uid, owner, location, damage, buffs in rows],
    }


# (live, interpreter) seconds for each case both sides ran and agreed on.
TIMINGS: list[tuple[float, float]] = []

# Seconds the last _live run spent dispatching the trigger and resolving its chain (set by _live).
LAST_TIMING = [0.0]


def _uses_trigger_target(ability_guid):
    """Whether the ability names its trigger target card (a TriggerTarget selector on one of its target templates)."""
    from gamedata import DEFAULT_RECORD_STORE as STORE
    record = STORE.get("AbilityTemplate", ability_guid)
    for ref in (record.m_AbilityTargetTemplateIds or []) if record is not None else []:
        template = STORE.get("AbilityTargetTemplate", str(ref.get("m_Guid")).lower())
        if template is not None and "TriggerTarget" in str(getattr(template, "m_TriggerSelector", "") or ""):
            return True
    return False


CHARGE_POWER_GUID = "81e34ad9-77eb-6e79-0778-ef1463e4fee2"


def _is_charge_power_trigger(ability_guid):
    """Whether the ability's trigger condition is "the activated ability is a charge power"."""
    from gamedata import DEFAULT_RECORD_STORE as STORE
    record = STORE.get("AbilityTemplate", ability_guid)
    return record is not None and "TriggerAbilityIsChargePower" in repr(record.to_dict(include_metadata=True))


_THRESHOLD_SHARDS = {"BLOOD": "blood", "RUBY": "ruby", "SAPPHIRE": "sapphire", "WILD": "wild", "DIAMOND": "diamond"}


def _threshold_color(case):
    """The colour a GainThresholdEvent carries: the one its ability names ("When you gain [WILD]"), else wild. Other
    events carry no colour."""
    if case["event"] != "GainThresholdEvent":
        return None
    import re
    import game_engine
    match = re.search(r"gain \[(\w+)\]", str(case.get("name") or ""), re.IGNORECASE)
    shard = _THRESHOLD_SHARDS.get(match.group(1).upper() if match else "WILD", "wild")
    return int(game_engine.SHARD_TO_FLAG[shard])


def _live(case):
    from rules_port.context import EffectContext  # noqa: F401
    from rules_port.triggers import dispatch_native_trigger
    import time
    setup = _scenario(case["ability"], case["opposing"], case.get("moved"),
                      case.get("extra", ()), case.get("champion", False))
    if setup is None:
        return None
    db, pl_t, ai_t, game, handler, bstate, player_champ, ai_champ = setup
    case["prepare"](db, bstate, player_champ, ai_champ)
    start_events = len(game.events)
    if case["event"] == "CardActivatedEvent" and _is_charge_power_trigger(case["ability"]):
        # The activation that fires "When you use a charge power" names the charge power the player used.
        bstate["activated_ability_guid"] = CHARGE_POWER_GUID
    source = player_champ if case.get("champion") else SOURCE_UID
    # An event sourced on a champion names the champion, not the ability's card, and belongs to the champion's
    # owner: Session.AddChargePoints and Session.HealChampion set SourcePlayerId from the champion's controller.
    event_card, event_owner = source, TROOP_OWNER
    if case.get("event_source"):
        own = case["event_source"] == "player"
        event_card, event_owner = (player_champ if own else ai_champ), (TROOP_OWNER if own else OPPOSING_OWNER)
    if "another" in str(case.get("name") or "").lower() and not case.get("champion") and not case.get("event_source"):
        # "When another troop ..." is about a different card: a second troop does the event, and the ability's card
        # watches it.
        db.execute("INSERT INTO game_cards (session_id, user_id, card_uid, template_guid, card_template_id, location,"
                   " position, card_state, card_abilities, card_type, card_attributes, card_attack_mod, card_defense_mod,"
                   " card_cost_mod, card_damage, permanent_buffs, temporary_buffs, card_uses, resolved_at,"
                   " original_template_guid) VALUES (1,?,?,?,?,'warzone',0,0,'[]','Troop',0,0,0,0,0,'{}','{}','{}',0,?)",
                   (TROOP_OWNER, OTHER_UID, scenarios.TPL_GLADIATOR, scenarios.TPL_GLADIATOR, scenarios.TPL_GLADIATOR))
        db.commit()
        event_card, event_owner = OTHER_UID, TROOP_OWNER
    if case["event"] in SETUP_ZONES and not case.get("champion"):
        # Encounter setup abilities live on a card in the hidden mod collection, which is the only place their trigger
        # collection (Mod) admits them, as the live setup finds them.
        from gamedata import DEFAULT_RECORD_STORE, ability_graph
        if "Mod" in str(ability_graph(DEFAULT_RECORD_STORE, case["ability"]).trigger_collection_flags or ""):
            db.execute("UPDATE game_cards SET location='mod' WHERE card_uid=?", (SOURCE_UID,))
            db.commit()
    from rules_port.resolution import resolve_port_trigger
    started = time.perf_counter()
    if case.get("moved"):
        uid, owner, zone_from, zone_to = case["moved"][:4]
        dispatch_native_trigger(db=db, handler=handler, game=game, session=SessionStub(),
                                player_uid=pl_t, ai_uid=ai_t, battle_state=bstate,
                                event_type=case["event"], source_card_id=uid,
                                source_player_id=owner,
                                data={"event_source_collection": zone_from,
                                      "event_destination_collection": zone_to})
    else:
        # An ability whose selector is the event's target card ("create a copy of it" on a played troop) gets the
        # event's card as its target, as the live event names it.
        target_card = source if _uses_trigger_target(case["ability"]) else None
        dispatch_native_trigger(db=db, handler=handler, game=game, session=SessionStub(),
                                player_uid=pl_t, ai_uid=ai_t, battle_state=bstate,
                                event_type=case["event"], source_card_id=event_card,
                                source_player_id=event_owner, target_card_id=target_card,
                                target_player_id=case.get("target_player"),
                                data={"event_int_attribute": case.get("int_attribute"),
                                      "gain_threshold_color": _threshold_color(case),
                                      "zones": SETUP_ZONES.get(case["event"])})
    # The live trigger goes on the battle stack; the chain resolves it (and anything it queues).
    for _ in range(8):
        if not bstate.get("stack"):
            break
        resolve_port_trigger(handler, game, SessionStub(), db, pl_t, ai_t, bstate,
                             bstate["stack"].pop())
    LAST_TIMING[0] = time.perf_counter() - started
    return _snapshot(db, bstate, player_champ, ai_champ, game, start_events)


class NotRunByInterpreter(Exception):
    """The engine switch was on, but the interpreter did not run the ability (the live path resolved it)."""


def _interpreted(case):
    """The interpreter side: the same trigger dispatch as the live side, with the engine switch on, so the chain
    protocol is identical and only the effects differ. Raises NotRunByInterpreter when the interpreter did not run."""
    from rules_port import ir_exec
    runs = []
    original = ir_exec.run_ability

    def counting(*args, **kwargs):
        runs.append(args[0])
        return original(*args, **kwargs)
    previous = ir_exec.ENGINE_ON
    ir_exec.set_engine(True)
    ir_exec.run_ability = counting
    try:
        snapshot = _live(case)
    finally:
        ir_exec.run_ability = original
        ir_exec.set_engine(previous)
    if not runs:
        raise NotRunByInterpreter("the interpreter did not run this ability")
    return snapshot


def _nothing(db, bstate, player_champ, ai_champ):
    return None


def _three_blood(db, bstate, player_champ, ai_champ):
    import game_engine
    bstate["player_threshold"] = {game_engine.SHARD_TO_FLAG["blood"]: 3}


CASES = [
    {"name": "Blood threshold 3 met: 1 damage to each opposing champion",
     "ability": "481e2f72", "event": "TurnStartedEvent",
     "opposing": [], "prepare": _three_blood},
    {"name": "Spitfire Elemental, start of turn: 1 to each opposing champion and troop",
     "ability": "f875adf6", "event": "TurnStartedEvent",
     "opposing": [(502, 0)], "prepare": _nothing},
    {"name": "Start of turn: 1 damage to each opposing champion",
     "ability": "34729a26", "event": "TurnStartedEvent",
     "opposing": [], "prepare": _nothing},
    {"name": "Start of turn: 1 damage to your champion",
     "ability": "dcfce519", "event": "TurnStartedEvent",
     "opposing": [], "prepare": _nothing},
    {"name": "Start of turn: add an ammo counter to this",
     "ability": "0ac2a1e4", "event": "TurnStartedEvent",
     "opposing": [], "prepare": _nothing},
]


def _find_abilities(prefixes):
    from gamedata import DEFAULT_RECORD_STORE as STORE
    found = {}
    for record in STORE.load("AbilityTemplate"):
        for prefix in prefixes:
            if record.guid.lower().startswith(prefix):
                found[prefix] = record.guid.lower()
    return found


BURNING = "3932ad3a-9807-de2a-e1b9-a8b50e81def9"
DAZED_COUNTER = "9c98dace-4cac-0fda-8242-f7ef2c1d3cf0"
VULNERABLE_COUNTER = "b01d4e3c-bae6-c55f-ff4e-db0513d74484"
CURSED = json.dumps({"int_attrs": {"Soulcursed": 1}})


def _burning(db, bstate, player_champ, ai_champ):
    bstate["champion_counters"] = {str(player_champ): {BURNING: 2}, str(ai_champ): {BURNING: 2}}


def _dazed(db, bstate, player_champ, ai_champ):
    bstate["champion_counters"] = {str(player_champ): {DAZED_COUNTER: 2}}


def _vulnerable(db, bstate, player_champ, ai_champ):
    bstate["champion_counters"] = {str(player_champ): {VULNERABLE_COUNTER: 2}}


def _cursed_death(db, bstate, player_champ, ai_champ):
    db.execute("UPDATE game_cards SET permanent_buffs=? WHERE card_uid=301", (CURSED,))


CHAMPION_CASES = [
    {"name": "Champion built-in Burning: end of your turn", "ability": "82598d69",
     "event": "TurnEndedEvent", "champion": True, "opposing": [], "prepare": _burning},
    {"name": "Champion built-in Dazed removal: end of your turn", "ability": "fa620e3b",
     "event": "TurnEndedEvent", "champion": True, "opposing": [], "prepare": _dazed},
    {"name": "Champion built-in Vulnerable removal: end of your turn", "ability": "1c938dd8",
     "event": "TurnEndedEvent", "champion": True, "opposing": [], "prepare": _vulnerable},
    {"name": "Champion built-in Soulcursed: a cursed troop dies",
     "ability": "e2464b7a", "event": "CardEnteredZoneEvent", "champion": True,
     "moved": (301, TROOP_OWNER, "warzone", "discard"),
     "extra": [(302, TROOP_OWNER, "warzone", CURSED)], "opposing": [], "prepare": _cursed_death},
]

TURN_EVENTS = {"TurnStartedEvent", "TurnEndedEvent", "CardEnteredZoneEvent"}
# Events whose own source card is a champion (Session.AddChargePoints, Session.HealChampion, and a Fateweave cast by
# the champion): the ability that reacts is another card's, and the event belongs to the champion's owner.
CHAMPION_SOURCE_EVENTS = {"GainChargeEvent", "ChampionHealedEvent", "FateweavedEvent", "GainThresholdEvent"}
# The zones each setup event is dispatched over, as the live setup calls dispatch them (services/tournament_game.py).
SETUP_ZONES = {"GameStartedEvent": ("hand", "warzone"), "PreGameEvent": ("deck",)}
# The live events the interpreter runs beyond the turn and zone events (rules_port/ir_triggers.py).
from rules_port.ir_triggers import TRIGGER_EVENTS  # noqa: E402
# Turn-event abilities with a card template that the interpreter refuses (name, missing constructs).
SKIPPED_BY_COVERAGE = []


def _trigger_condition_parts(raw):
    """Every trigger-condition part of a record (the nested AND/OR parts flattened), as dicts."""
    out = []

    def walk(value):
        if isinstance(value, dict):
            if "_t" in value:
                out.append(value)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(raw.get("m_TriggerCondition"))
    return out


def _one_zone(flags, fallback, source=None):
    """One collection from a flags string. A condition may name a mask ("anywhere") because it accepts any of those
    zones, but a move lands in exactly one collection, so pick the one a real move of that kind uses."""
    zones = [zone.strip().lower() for zone in str(flags or "").split("|")
             if zone.strip() and zone.strip().lower() != "none"]
    if not zones:
        return fallback
    for preferred in ("discard", "void", "deck", "hand", "warzone"):
        if preferred in zones and preferred != source:
            return preferred
    return zones[0]


def _zone_move(db, raw):
    """The card move a zone trigger needs, read from its own condition: the ability's source moves when the
    condition names it (a "dies" or "enters play" ability), else another card moves. The zones are the
    condition's source and destination collections; the owner is the controller unless the condition names
    the opponent; a card filter's subtype picks the moved card's template."""
    parts = _trigger_condition_parts(raw)
    names = {part["_t"].rsplit(".", 1)[-1] for part in parts}
    zone = next((part for part in parts if part["_t"].endswith("TriggerCardEnteredZone")), {})
    source = _one_zone(zone.get("m_SourceCollection"), "hand")
    destination = _one_zone(zone.get("m_DestinationCollection"), "warzone", source=source)
    opposing = bool(zone.get("m_Opposing")) and not zone.get("m_Your")
    owner = OPPOSING_OWNER if opposing else TROOP_OWNER
    mover = 900 if "TriggerCardIsAbilitySource" in names else MOVED_UID
    subtype = ""
    for part in parts:
        if part["_t"].endswith("TriggerCardMatchesFilter"):
            for filt in _trigger_condition_parts({"m_TriggerCondition": part.get("m_CardFilter")}):
                if filt["_t"].endswith("IsSubType"):
                    subtype = str(filt.get("m_SubType") or "")
    template = _subtype_template(db, subtype) if subtype else None
    return (mover, owner, source, destination) + ((template,) if template else ())


def _subtype_template(db, subtype):
    """A troop template with the subtype (a card a subtype filter can match), or None."""
    row = db.execute("SELECT guid FROM card_templates WHERE card_type='Troop' AND subtype LIKE ? "
                     "ORDER BY guid LIMIT 1", (f"%{subtype}%",)).fetchone()
    return row[0] if row else None


def _moved_template(moved):
    return moved[4] if len(moved) > 4 and moved[4] else scenarios.TPL_GLADIATOR


def _prepare_spec(prepare):
    """A case's setup as plain data (it is cached as JSON): None for nothing, or the threshold it sets."""
    return None if prepare is _nothing else list(getattr(prepare, "threshold", ()))


def _prepare_from_spec(spec):
    if not spec:
        return _nothing
    flag, required = spec

    def prepare(db, bstate, player_champ, ai_champ):
        bstate["player_threshold"] = {flag: required}
    return prepare


def _threshold_prepare(raw):
    """A threshold ability's requirement: the controller has the required count of the shard. Other abilities
    need nothing set up."""
    from gamedata import DEFAULT_RECORD_STORE  # noqa: F401  (the record is already in raw)
    import game_engine
    condition = raw.get("m_AbilityCondition") or {}
    if not str(condition.get("_t") or "").endswith("AbilityControllerHasThresholdAbilityCondition"):
        return _nothing
    flag = game_engine.SHARD_TO_FLAG[str(condition.get("m_ColorFlags") or "").lower()]
    required = int(condition.get("m_RequiredQuantity") or 0)

    def prepare(db, bstate, player_champ, ai_champ):
        bstate["player_threshold"] = {flag: required}
    prepare.threshold = (flag, required)
    return prepare


def _case_cache_path(limit):
    """Where the listed cases are cached: keyed by the harness, the interpreter's coverage code and the limit, so a
    change to either lists the cases again."""
    import hashlib
    import tempfile
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    digest = hashlib.sha256()
    for path in (os.path.abspath(__file__), os.path.join(root, "rules_port", "ir_exec.py")):
        with open(path, "rb") as stream:
            digest.update(stream.read())
    digest.update(str(limit).encode())
    return os.path.join(tempfile.gettempdir(), f"hex-ir-equivalence-cases-{digest.hexdigest()[:16]}.json")


# The case fields a run reads, all of them plain data (``prepare`` is stored as the spec it is rebuilt from).
_CASE_FIELDS = ("name", "ability", "event", "champion", "any_template", "target_player",
                "int_attribute", "event_source")


def _case_spec(case):
    """A case as JSON: every field it carries, so the cache cannot lose one (it dropped the event's target player
    and the attribute a CardGainedIntAttrEvent names before, which silently changed the battle a case ran on)."""
    spec = {field: case[field] for field in _CASE_FIELDS if field in case}
    spec["opposing"] = [list(entry) for entry in case["opposing"]]
    if case.get("extra"):
        spec["extra"] = [list(entry) for entry in case["extra"]]
    spec["prepare"] = _prepare_spec(case["prepare"])
    spec["moved"] = list(case["moved"]) if case.get("moved") else None
    return spec


def _case_from_spec(spec):
    case = {field: spec[field] for field in _CASE_FIELDS if field in spec}
    case["opposing"] = [tuple(entry) for entry in spec["opposing"]]
    if spec.get("extra"):
        case["extra"] = tuple(tuple(entry) for entry in spec["extra"])
    case["prepare"] = _prepare_from_spec(spec["prepare"])
    case["moved"] = tuple(spec["moved"]) if spec.get("moved") else None
    return case


def cached_auto_cases(limit):
    """auto_cases, from a cache keyed by the harness and the coverage code. Listing the cases reads and indexes the
    whole Records, which is most of a run's startup; each chunk of a run loads the cached list instead."""
    import json
    path = _case_cache_path(limit)
    if os.path.exists(path):
        with open(path) as stream:
            stored = json.load(stream)
        SKIPPED_BY_COVERAGE[:] = [tuple(item) for item in stored["skipped"]]
        return [_case_from_spec(item) for item in stored["cases"]]
    cases = auto_cases(limit)
    stored = {"skipped": [list(item) for item in SKIPPED_BY_COVERAGE],
              "cases": [_case_spec(case) for case in cases]}
    with open(path, "w") as stream:
        json.dump(stored, stream)
    return cases


def auto_cases(limit):
    """Covered abilities with a turn trigger that a card carries, as cases (the event is their own)."""
    from gamedata import DEFAULT_RECORD_STORE as STORE
    from rules_port.ir_exec import coverage
    from tests.tests_combat import make_db as _make
    cases = []
    db = _make()
    for record in STORE.load("AbilityTemplate"):
        raw = record.to_dict(include_metadata=True)
        event = str((raw.get("m_TriggerEventType") or {}).get("m_InternalType") or "").rsplit(".", 1)[-1]
        if event not in TURN_EVENTS and event not in TRIGGER_EVENTS:
            continue
        if _template_for(db, record.guid.lower(), event) is None:
            SKIPPED_BY_COVERAGE.append((f"{event}: {raw.get('m_GameText', '')[:70]}",
                                        ["no card template where it fires alone"]))
            continue
        missing = coverage(record.guid.lower())
        if missing:
            SKIPPED_BY_COVERAGE.append((f"{event}: {raw.get('m_GameText', '')[:70]}", missing))
            continue
        moves = [None]
        if event in ("CardEnteredZoneEvent", "CardExitedZoneEvent"):
            move = _zone_move(db, raw)
            moves = [move]
            if event == "CardExitedZoneEvent" and move[0] != 900:
                # Another card leaves. Run the move both ways: only the ability's own condition says which
                # controller makes it fire ("when an opposing troop leaves play" against "when a troop leaves your
                # crypt"), and the condition is exactly what the two sides must agree on.
                moves.append((move[0], OPPOSING_OWNER if move[1] == TROOP_OWNER else TROOP_OWNER) + tuple(move[2:]))
        # An event sourced on a champion (a charge gained, a champion healed) belongs to that champion's owner, so
        # it runs for the player's champion and then for the opponent's.
        variants = ([{"event_source": "player"}, {"event_source": "opposing"}]
                    if event in CHAMPION_SOURCE_EVENTS else [{}])
        # A damage event names the damaged card's controller: the player's own troop's owner and the opposing one,
        # so a "when you are dealt damage" ability runs on one and not the other.
        targets = ([TROOP_OWNER, OPPOSING_OWNER]
                   if event in TRIGGER_EVENTS and event not in CHAMPION_SOURCE_EVENTS else [None])
        # An attribute gain names the attribute its ability's TriggerEventIntAttribute tests.
        attribute = next((part.get("m_Attribute") for part in _trigger_condition_parts(raw)
                          if part.get("_t", "").endswith("TriggerEventIntAttribute")), None)
        for moved in moves:
            for variant in variants:
                for target in targets:
                    cases.append({"name": f"{event}: {raw.get('m_GameText', '')[:70]}",
                                  "ability": record.guid.lower(), "event": event,
                                  "opposing": [], "prepare": _threshold_prepare(raw), "moved": moved,
                                  "target_player": target, "int_attribute": attribute, **variant})
        if len(cases) >= limit:
            break
    return cases


def _card_roots():
    """The ability GUIDs of the card files in ir_cards/, each loaded and registered for the interpreter."""
    import glob
    from ir_cards.load import load_card
    from rules_port import ir_exec
    roots = {}
    paths = glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ir_cards", "*.py"))
    paths += glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ir_cards", "surface", "*.py"))
    for path in sorted(paths):
        if os.path.basename(path) == "__init__.py" or os.path.basename(path) == "load.py":
            continue
        names = load_card(path)
        ir_exec.register_card(names)
        for value in names.values():
            if type(value).__name__ == "AbilityTemplate":
                roots[str(value.m_AbilityTemplateId["m_Guid"]).lower()] = os.path.basename(path)
    return roots


def _counter_guids_of(ability_guid):
    """Every counter template the ability names (a variable's or a modifier's counter), as GUIDs."""
    from gamedata import DEFAULT_RECORD_STORE as STORE
    found = set()

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("m_CardCounterTemplateId", "m_CounterType") and isinstance(item, dict):
                    found.add(str(item.get("m_Guid") or "").lower())
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    record = STORE.get("AbilityTemplate", ability_guid)
    if record is not None:
        walk(record.to_dict(include_metadata=True))
    return sorted(guid for guid in found if guid and guid != "00000000-0000-0000-0000-000000000000")


def random_cases(case, count, seed):
    """``count`` randomised battles for one case: random counters on the champions and on a few other
    cards in each zone, and random opposing troops, all from ``seed``. Every battle is checked the same way."""
    import random
    from rules_port.bom_fields import counter_template_name
    rng = random.Random(f"{seed}:{case['ability']}")
    counters = _counter_guids_of(case["ability"])
    cases = []
    for index in range(count):
        def value():
            return rng.choice([0, 0, 1, 2, 3])
        champion_counters = {}
        for guid in counters:
            if rng.random() < 0.7:
                champion_counters.setdefault("player", {})[guid] = value()
                champion_counters.setdefault("ai", {})[guid] = value()
        extra = []
        for offset in range(rng.randint(0, 3)):
            uid = 600 + offset
            owner = rng.choice([TROOP_OWNER, OPPOSING_OWNER])
            zone = rng.choice(["warzone", "warzone", "hand", "discard", "deck", "void"])
            named = {}
            for guid in counters:
                if rng.random() < 0.5:
                    named[counter_template_name(guid).lower() or guid] = value()
            buffs = json.dumps({"counters": named, "counter_guids": {
                counter_template_name(guid).lower() or guid: guid for guid in counters}}) if named else "{}"
            extra.append((uid, owner, zone, buffs))
        opposing = [(510 + offset, rng.choice([0, 0, 1, 2])) for offset in range(rng.randint(0, 2))]
        cases.append({**case, "name": f"{case['name']} [random {index}]", "extra": extra,
                      "opposing": opposing, "random_counters": champion_counters,
                      "prepare": _random_prepare(champion_counters)})
    return cases


def _random_prepare(champion_counters):
    def prepare(db, bstate, player_champ, ai_champ):
        bstate["champion_counters"] = {
            str(player_champ): dict(champion_counters.get("player", {})),
            str(ai_champ): dict(champion_counters.get("ai", {})),
        }
    return prepare


CASE_SECONDS = int(os.environ.get("IR_EQ_CASE_SECONDS", "20"))


class CaseTimeout(BaseException):
    """Not an Exception, so the port's broad handlers cannot swallow it and carry on."""
    pass


# Cards one case may generate (summoned tokens and created cards). A summon loop that keeps making cards is
# reported as a case that did not settle, not left to run until memory runs out.
CASE_CARD_LIMIT = int(os.environ.get("IR_EQ_CARD_LIMIT", "300"))
_generated = [0]


def _count_generated_cards():
    """Count the cards the live path generates, per case, on both sides."""
    import pvp_db
    original = pvp_db.db_insert_generated_card

    def counting(*args, **kwargs):
        _generated[0] += 1
        if _generated[0] > CASE_CARD_LIMIT:
            raise CaseTimeout()
        return original(*args, **kwargs)
    pvp_db.db_insert_generated_card = counting


class _time_limit:
    """Raise CaseTimeout after ``seconds`` (the harness runs one case at a time, on the main thread)."""

    def __init__(self, seconds):
        self.seconds = seconds

    def __enter__(self):
        import signal

        def handler(signum, frame):
            raise CaseTimeout()
        self._old = signal.signal(signal.SIGALRM, handler)
        signal.alarm(self.seconds)

    def __exit__(self, *exc):
        import signal
        signal.alarm(0)
        signal.signal(signal.SIGALRM, self._old)
        return False


def _chosen_targets(ability_guid):
    """Indices of the ability's targets the player picks (non-automatic AbilityTargetTemplates)."""
    from gamedata import DEFAULT_RECORD_STORE as STORE
    record = STORE.get("AbilityTemplate", ability_guid)
    if record is None:
        return []
    indices = []
    for index, item in enumerate(record.to_dict(include_metadata=True).get("m_AbilityTargetTemplateIds") or []):
        target = STORE.get("AbilityTargetTemplate", str(item.get("m_Guid") or "").lower())
        if target is not None and target.to_dict(include_metadata=True).get("_t", "").endswith(".AbilityTargetTemplate") \
                and not target.to_dict(include_metadata=True).get("m_IsAutoTarget"):
            indices.append(index)
    return indices


def choice_parity(case, index):
    """A chosen target, answered: for each candidate the interpreter's pause offers, the interpreter resumed with
    that answer and the native resolver run with the same target map must leave the same battle and client events.
    Returns (passed, detail)."""
    from rules_port.context import EffectContext
    from rules_port.ir_exec import NeedsChoice, resume_ability, run_ability
    from rules_port.resolution import resolve_port_ability

    event = case.get("event_data") or {"player": TROOP_OWNER}

    def fresh():
        db, pl_t, ai_t, game, handler, bstate, player_champ, ai_champ = _scenario(
            case["ability"], case["opposing"], None, case.get("extra", ()), any_template=True)
        start = len(game.events)
        context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
        return db, pl_t, ai_t, game, handler, bstate, player_champ, ai_champ, start, context

    db, pl_t, ai_t, game, handler, bstate, player_champ, ai_champ, start, context = fresh()
    try:
        run_ability(case["ability"], case["event"], context=context, source_uid=SOURCE_UID,
                    owner_uid=TROOP_OWNER, event=event)
        return None, "the ability did not pause for a choice"
    except NeedsChoice as pause:
        candidates = list(pause.candidates)
    if not candidates:
        return None, "no candidate for the choice"
    for answer in candidates:
        db, pl_t, ai_t, game, handler, bstate, player_champ, ai_champ, start, context = fresh()
        try:
            run_ability(case["ability"], case["event"], context=context, source_uid=SOURCE_UID,
                        owner_uid=TROOP_OWNER, event=event)
        except NeedsChoice as again:
            continuation = again.continuation
        resume_ability(continuation, answer, context=context)
        interpreted = _snapshot(db, bstate, player_champ, ai_champ, game, start)

        db, pl_t, ai_t, game, handler, bstate, player_champ, ai_champ, start, context = fresh()
        resolve_port_ability(handler, game, SessionStub(), db, pl_t, ai_t, bstate, case["ability"],
                             SOURCE_UID, TROOP_OWNER, target_map={index: answer}, instance_id=1)
        native = _snapshot(db, bstate, player_champ, ai_champ, game, start)
        if interpreted != native:
            return False, f"answer {answer}: interpreter {interpreted['cards']} native {native['cards']}"
    return True, f"{len(candidates)} answers agree"


def choice_cases(limit):
    """Covered turn-event abilities with a chosen target, as cases (the first chosen target's index is used)."""
    from gamedata import DEFAULT_RECORD_STORE as STORE
    from rules_port.ir_exec import coverage
    from tests.tests_combat import make_db as _make
    cases = []
    db = _make()
    for record in STORE.load("AbilityTemplate"):
        raw = record.to_dict(include_metadata=True)
        event = str((raw.get("m_TriggerEventType") or {}).get("m_InternalType") or "").rsplit(".", 1)[-1]
        if event not in TURN_EVENTS:
            continue
        indices = _chosen_targets(record.guid.lower())
        if not indices or coverage(record.guid.lower()) or _any_template(db, record.guid.lower()) is None:
            continue
        cases.append((
            {"name": f"choice {event}: {raw.get('m_GameText', '')[:70]}", "ability": record.guid.lower(),
             "event": event, "opposing": [(502, 0), (503, 0)], "extra": [(600, TROOP_OWNER, "warzone", "{}"), (601, TROOP_OWNER, "warzone", "{}")],
             "event_data": ({"player": TROOP_OWNER, "card": SOURCE_UID, "from": "hand", "to": "warzone"}
                            if event == "CardEnteredZoneEvent" else None),
             "prepare": _nothing, "moved": None},
            indices[0]))
        if len(cases) >= limit:
            break
    return cases


# The work a parallel run shares with its forked workers (set before the pool starts).
_WORK: list = []


def _evaluate_case_in_worker(index):
    cases, from_cards, card_roots = _WORK
    return _evaluate_case(cases[index], from_cards, card_roots)


def _evaluate_case(case, from_cards, card_roots):
    """One case on both sides: ("uncovered", (name, reasons)), ("failed", (name, live, detail)) or
    ("passed", (name, live, interpreted, live_seconds, interpreted_seconds))."""
    from rules_port.ir_exec import coverage
    if from_cards and case["ability"] not in card_roots:
        return "uncovered", (case["name"], ["no card file for this ability"])
    missing = coverage(case["ability"])
    if missing:
        return "uncovered", (case["name"], missing)
    if os.environ.get("IR_EQ_PROGRESS") == "1":
        print("CASE", case["name"], flush=True)
    _generated[0] = 0
    try:
        with _time_limit(CASE_SECONDS):
            live = _live(case)
            live_seconds = LAST_TIMING[0]
            if live is None:
                return "uncovered", (case["name"], ["no card template carries this ability"])
            _generated[0] = 0
            interpreted = _interpreted(case)
            interpreted_seconds = LAST_TIMING[0]
    except CaseTimeout:
        # A chain that does not settle (a summon whose tokens summon more) is reported, not compared.
        return "uncovered", (case["name"], [f"the live chain did not settle within {CASE_SECONDS}s"])
    except NotImplementedError as exc:
        return "uncovered", (case["name"], [str(exc)])
    except NotRunByInterpreter as exc:
        return "uncovered", (case["name"], [str(exc)])
    except Exception as exc:  # a crash is a failure, reported with its cause
        return "failed", (case["name"], "", f"{type(exc).__name__}: {exc}")
    if live == interpreted:
        return "passed", (case["name"], live, interpreted, live_seconds, interpreted_seconds)
    return "failed", (case["name"], live, interpreted)


def main():
    from rules_port.ir_exec import coverage, clear_cards
    _count_generated_cards()
    from_cards = os.environ.get("IR_EQ_CARDS") == "1"
    card_roots = _card_roots() if from_cards else {}
    resolved = _find_abilities([case["ability"][:8] for case in CASES + CHAMPION_CASES])
    passed, failed, uncovered = [], [], []
    turn_cases = cached_auto_cases(int(os.environ.get("IR_EQ_LIMIT", "40")))
    random_count = int(os.environ.get("IR_EQ_RANDOM", "0"))
    seed = os.environ.get("IR_EQ_SEED", "0")
    expanded = []
    for case in turn_cases:
        expanded.extend(random_cases(case, random_count, seed) if random_count else [case])
    # IR_EQ_EVENTS="CardExitedZoneEvent,GainChargeEvent" runs only those events (one cluster at a time).
    events = {name for name in os.environ.get("IR_EQ_EVENTS", "").split(",") if name}
    if events:
        expanded = [case for case in expanded if case["event"] in events]
    # IR_EQ_CHUNK="start:end" runs a slice of the cases in this process (the port keeps memory across cases, so
    # a long run is split into several processes).
    start, end = (int(part) for part in os.environ.get("IR_EQ_CHUNK", f"0:{len(expanded) + 9}").split(":"))
    everything = CHAMPION_CASES + CASES + expanded
    if events:
        everything = [case for case in everything if case["event"] in events]
    print("CASES", len(everything), flush=True)
    cases = everything[start:end]
    for case in cases:
        case["ability"] = resolved.get(case["ability"][:8], case["ability"])
    workers = int(os.environ.get("IR_EQ_WORKERS", "1"))
    if workers > 1:
        # Forked workers inherit the built case list and the warm caches, so each case costs no startup.
        import multiprocessing
        # The cases hold closures, so the workers receive indices and read the cases they inherited at the fork.
        _WORK[:] = [cases, from_cards, card_roots]
        with multiprocessing.get_context("fork").Pool(workers) as pool:
            outcomes = list(pool.imap(_evaluate_case_in_worker, range(len(cases)), chunksize=4))
    else:
        outcomes = [_evaluate_case(case, from_cards, card_roots) for case in cases]
    for outcome, record in outcomes:
        if outcome == "uncovered":
            uncovered.append(record)
        elif outcome == "failed":
            failed.append(record)
        elif outcome == "passed":
            name, live, interpreted, live_seconds, interpreted_seconds = record
            passed.append((name, live, interpreted))
            TIMINGS.append((live_seconds, interpreted_seconds))
    for name, _live_state, _ir_state in passed:
        print("PASS", name)
    for name, live_state, ir_state in failed:
        print("FAIL", name)
        print("   live", live_state)
        print("   ir  ", ir_state)
    for name, missing in uncovered + SKIPPED_BY_COVERAGE:
        print("UNCOVERED", name, "-", "; ".join(missing))
    if from_cards:
        clear_cards()
        print(f"interpreter ran from {len(card_roots)} card file(s): {', '.join(sorted(set(card_roots.values())))}")
    print(f"{len(passed)} passed, {len(failed)} failed, {len(uncovered)} uncovered")
    choice_results = []
    for case, index in choice_cases(int(os.environ.get("IR_EQ_CHOICES", "40"))):
        try:
            choice_results.append((case["name"], *choice_parity(case, index)))
        except Exception as exc:  # reported with its cause
            choice_results.append((case["name"], False, f"{type(exc).__name__}: {exc}"))
    for name, ok, detail in choice_results:
        if ok is None:
            print("CHOICE SKIP", name, "-", detail)
        else:
            print("CHOICE", "PASS" if ok else "FAIL", name, "-", detail)
    if choice_results:
        print(f"choices: {sum(1 for _, ok, _ in choice_results if ok)} passed, "
              f"{sum(1 for _, ok, _ in choice_results if ok is False)} failed, "
              f"{sum(1 for _, ok, _ in choice_results if ok is None)} skipped")
    if TIMINGS:
        live_total = sum(live for live, _ in TIMINGS)
        interp_total = sum(interp for _, interp in TIMINGS)
        ratios = sorted(interp / live for live, interp in TIMINGS if live > 0)
        print(f"TIMING over {len(TIMINGS)} cases both sides ran (dispatch + chain, seconds):")
        print(f"  live total {live_total:.3f}s, interpreter total {interp_total:.3f}s, "
              f"ratio {interp_total / live_total:.2f}x")
        print(f"  per-case ratio: median {ratios[len(ratios) // 2]:.2f}x, "
              f"min {ratios[0]:.2f}x, max {ratios[-1]:.2f}x")


# Expected results for the custom (Python-only) cards in ir_cards/custom/: the source troop's card_damage
# after its root runs once for its trigger. A custom card has no live counterpart, so it is checked
# against these values, not side by side.
CUSTOM_EXPECTED = {
    "start_of_turn_two_damage": {"card_damage": 2},
}


def custom_cards():
    """Run each custom card's root once on a fresh battle and check it against its expected values."""
    import glob
    from ir_cards.load import load_card
    from rules_port import ir_exec
    from rules_port.context import EffectContext
    results = []
    for path in sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                              "ir_cards", "custom", "*.py"))):
        stem = os.path.basename(path)[:-3]
        names = load_card(path)
        ir_exec.register_card(names)
        try:
            root = next(value for value in names.values() if type(value).__name__ == "AbilityTemplate")
            guid = str(root.m_AbilityTemplateId["m_Guid"]).lower()
            event = str(root.m_TriggerEventType["m_InternalType"]).rsplit(".", 1)[-1]
            dbmod._db = make_db()
            db = dbmod._db
            pl_t, ai_t, game, handler, bstate, _player_champ, _ai = scenarios._setup(db)
            add_card(db, SOURCE_UID, TROOP_OWNER, scenarios.TPL_GLADIATOR, loc="warzone")
            db.commit()
            context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
            fired = ir_exec.run_ability(guid, event, context=context, source_uid=SOURCE_UID,
                                        owner_uid=TROOP_OWNER, event={"player": TROOP_OWNER})
            damage = db.execute("SELECT card_damage FROM game_cards WHERE card_uid=?", (SOURCE_UID,)).fetchone()[0]
            expected = CUSTOM_EXPECTED.get(stem, {})
            ok = fired and damage == expected.get("card_damage")
            results.append((stem, ok, f"fired={fired} card_damage={damage} expected={expected.get('card_damage')}"))
        finally:
            ir_exec.clear_cards()
    return results


if __name__ == "__main__":
    main()
    for stem, ok, detail in custom_cards():
        print("PYTHON-ONLY", "PASS" if ok else "FAIL", stem, "(no live counterpart; checked against expected)", detail)
