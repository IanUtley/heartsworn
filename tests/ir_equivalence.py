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
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import tests.tests_builtin_counter_rules as scenarios  # noqa: E402  (sets up the test database first)
import db as dbmod  # noqa: E402
from tests.tests_combat import SessionStub, add_card, make_db  # noqa: E402

TROOP_OWNER = 5          # the player (owns the player champion)
OPPOSING_OWNER = 0       # the AI (owns the AI champion)


def _template_for(db, ability_guid):
    row = db.execute(
        "SELECT guid FROM card_templates WHERE abilities_json LIKE ? "
        "ORDER BY (card_type='Troop') DESC LIMIT 1",
        (f"%{ability_guid}%",)).fetchone()
    return row[0] if row else None


MOVED_UID = 503


SOURCE_UID = 900


def _scenario(ability_guid, opposing_troops, moved=None, extra=(), champion=False):
    """A fresh battle with the ability's troop in play for its owner, and the opposing troops.
    ``extra`` places more cards: (uid, owner, zone, permanent_buffs)."""
    dbmod._db = make_db()
    db = dbmod._db
    pl_t, ai_t, game, handler, bstate, player_champ, ai_champ = scenarios._setup(db)
    for uid, owner, zone, buffs in extra:
        add_card(db, uid, owner, scenarios.TPL_GLADIATOR, loc=zone)
        db.execute("UPDATE game_cards SET permanent_buffs=? WHERE card_uid=?", (buffs, uid))
    if champion:
        # A champion built-in: the champions carry the ability, so no troop is placed for it. A moved card
        # (a troop that dies, say) still exists in the battle, in the zone it moved to.
        if moved and moved[0] != 900:
            uid, owner, source, destination = moved
            add_card(db, uid, owner, scenarios.TPL_GLADIATOR, loc=source)
            db.execute("UPDATE game_cards SET location=? WHERE card_uid=?", (destination, uid))
        db.commit()
        return db, pl_t, ai_t, game, handler, bstate, player_champ, ai_champ
    template = _template_for(db, ability_guid)
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
        uid, owner, source, destination = moved
        add_card(db, uid, owner, scenarios.TPL_GLADIATOR, loc=source)
        db.execute("UPDATE game_cards SET location=? WHERE card_uid=?", (destination, uid))
    elif moved:
        db.execute("UPDATE game_cards SET location=? WHERE card_uid=900", (moved[3],))
    db.commit()
    return db, pl_t, ai_t, game, handler, bstate, player_champ, ai_champ


def _snapshot(db, bstate, player_champ, ai_champ):
    rows = db.execute(
        "SELECT card_uid, user_id, location, card_damage, permanent_buffs FROM game_cards "
        "ORDER BY card_uid").fetchall()
    return {
        "player_health": bstate.get("player_health"),
        "ai_health": bstate.get("ai_health"),
        "champion_counters": copy.deepcopy(bstate.get("champion_counters") or {}),
        "cards": [(uid, owner, location, damage, json.loads(buffs or "{}"))
                  for uid, owner, location, damage, buffs in rows],
    }


def _live(case):
    from rules_port.context import EffectContext  # noqa: F401
    from rules_port.triggers import dispatch_native_trigger
    setup = _scenario(case["ability"], case["opposing"], case.get("moved"),
                      case.get("extra", ()), case.get("champion", False))
    if setup is None:
        return None
    db, pl_t, ai_t, game, handler, bstate, player_champ, ai_champ = setup
    case["prepare"](db, bstate, player_champ, ai_champ)
    source = player_champ if case.get("champion") else SOURCE_UID
    from rules_port.resolution import resolve_port_trigger
    if case.get("moved"):
        uid, owner, zone_from, zone_to = case["moved"]
        dispatch_native_trigger(db=db, handler=handler, game=game, session=SessionStub(),
                                player_uid=pl_t, ai_uid=ai_t, battle_state=bstate,
                                event_type=case["event"], source_card_id=uid,
                                source_player_id=owner,
                                data={"event_source_collection": zone_from,
                                      "event_destination_collection": zone_to})
    else:
        dispatch_native_trigger(db=db, handler=handler, game=game, session=SessionStub(),
                                player_uid=pl_t, ai_uid=ai_t, battle_state=bstate,
                                event_type=case["event"], source_card_id=source,
                                source_player_id=TROOP_OWNER)
    # The live trigger goes on the battle stack; the chain resolves it (and anything it queues).
    for _ in range(8):
        if not bstate.get("stack"):
            break
        resolve_port_trigger(handler, game, SessionStub(), db, pl_t, ai_t, bstate,
                             bstate["stack"].pop())
    return _snapshot(db, bstate, player_champ, ai_champ)


def _interpreted(case):
    from rules_port.context import EffectContext
    from rules_port.ir_exec import run_ability
    setup = _scenario(case["ability"], case["opposing"], case.get("moved"),
                      case.get("extra", ()), case.get("champion", False))
    db, pl_t, ai_t, game, handler, bstate, player_champ, ai_champ = setup
    source = player_champ if case.get("champion") else SOURCE_UID
    case["prepare"](db, bstate, player_champ, ai_champ)
    context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
    if case.get("moved"):
        uid, owner, zone_from, zone_to = case["moved"]
        event = {"player": owner, "card": uid, "from": zone_from, "to": zone_to}
    else:
        event = {"player": TROOP_OWNER}
    run_ability(case["ability"], case["event"], context=context, source_uid=source,
                owner_uid=TROOP_OWNER, event=event)
    return _snapshot(db, bstate, player_champ, ai_champ)


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
# Turn-event abilities with a card template that the interpreter refuses (name, missing constructs).
SKIPPED_BY_COVERAGE = []


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
        if event not in TURN_EVENTS or _template_for(db, record.guid.lower()) is None:
            continue
        missing = coverage(record.guid.lower())
        if missing:
            SKIPPED_BY_COVERAGE.append((f"{event}: {raw.get('m_GameText', '')[:70]}", missing))
            continue
        moved = None
        if event == "CardEnteredZoneEvent":
            # "When this enters play" moves the troop itself; other zone triggers move another card.
            if "TriggerCardIsAbilitySource" in json.dumps(record.to_dict(include_metadata=True)):
                moved = (900, TROOP_OWNER, "hand", "warzone")
            else:
                moved = (MOVED_UID, TROOP_OWNER, "warzone", "discard")
        cases.append({"name": f"{event}: {raw.get('m_GameText', '')[:70]}",
                      "ability": record.guid.lower(), "event": event,
                      "opposing": [], "prepare": _nothing, "moved": moved})
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

    for record in STORE.load("AbilityTemplate"):
        if record.guid.lower() == ability_guid:
            walk(record.to_dict(include_metadata=True))
            break
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


def main():
    from rules_port.ir_exec import coverage, clear_cards
    from_cards = os.environ.get("IR_EQ_CARDS") == "1"
    card_roots = _card_roots() if from_cards else {}
    resolved = _find_abilities([case["ability"][:8] for case in CASES + CHAMPION_CASES])
    passed, failed, uncovered = [], [], []
    turn_cases = auto_cases(int(os.environ.get("IR_EQ_LIMIT", "40")))
    random_count = int(os.environ.get("IR_EQ_RANDOM", "0"))
    seed = os.environ.get("IR_EQ_SEED", "0")
    expanded = []
    for case in turn_cases:
        expanded.extend(random_cases(case, random_count, seed) if random_count else [case])
    for case in CHAMPION_CASES + CASES + expanded:
        case["ability"] = resolved.get(case["ability"][:8], case["ability"])
        if from_cards and case["ability"] not in card_roots:
            uncovered.append((case["name"], ["no card file for this ability"]))
            continue
        missing = coverage(case["ability"])
        if missing:
            uncovered.append((case["name"], missing))
            continue
        live = _live(case)
        if live is None:
            uncovered.append((case["name"], ["no card template carries this ability"]))
            continue
        try:
            interpreted = _interpreted(case)
        except NotImplementedError as exc:
            uncovered.append((case["name"], [str(exc)]))
            continue
        except Exception as exc:  # a crash is a failure, reported with its cause
            failed.append((case["name"], live, f"{type(exc).__name__}: {exc}"))
            continue
        (passed if live == interpreted else failed).append((case["name"], live, interpreted))
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
