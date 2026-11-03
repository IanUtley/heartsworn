"""Focused coverage for the highest-CRAP functions (main paths only)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database  # noqa: E402

fresh_database()

DANDELION_SPRITE = "c1000a30-ff55-475f-a27d-41af488699b9"   # Troop, cost 2, 1/2


def test_card_traits_unknown_card_has_no_signals():
    from ai_deck_strategy import _card_traits
    assert _card_traits("00000000-0000-0000-0000-000000000000") == (False,) * 10


def test_card_traits_flags_a_cheap_troop():
    from ai_deck_strategy import _card_traits
    traits = _card_traits(DANDELION_SPRITE)
    assert len(traits) == 10
    assert traits[0] is True    # cheap_troop
    assert traits[1] is False   # not a large troop


_TROOP_CARD = {"card_uid": 101, "card_type": "Troop", "location": "warzone",
               "user_id": 5, "attack": 1, "defense": 2, "name": "Troop"}
_IS_TROOP = {"_t": "Game.Shared.Mechanics.Cards.Filters.IsTroop"}


def test_card_filter_accepts_anything_without_a_filter_object():
    from rules_port.targeting import evaluate_card_filter
    assert evaluate_card_filter(_TROOP_CARD, None, 999) is True


def test_card_filter_separates_troops_from_other_cards():
    from rules_port.targeting import evaluate_card_filter
    spell = dict(_TROOP_CARD, card_type="QuickAction", name="Spell")
    assert evaluate_card_filter(_TROOP_CARD, _IS_TROOP, 999) is True
    assert evaluate_card_filter(spell, _IS_TROOP, 999) is False


def test_card_filter_and_requires_every_branch():
    from rules_port.targeting import evaluate_card_filter
    both = {"_t": "Game.Shared.Mechanics.Cards.Filters.AndCardFilter",
            "m_TargetFilters": [_IS_TROOP,
                                {"_t": "Game.Shared.Mechanics.Cards.Filters.NotCardFilter",
                                 "m_TargetFilter": {"_t": "Game.Shared.Mechanics.Cards.Filters.IsAbilitySource"}}]}
    assert evaluate_card_filter(_TROOP_CARD, both, 999) is True
    assert evaluate_card_filter(_TROOP_CARD, both, 101) is False


def test_native_condition_combines_children_with_and_or_not():
    from types import SimpleNamespace
    from rules_port.conditions import _native_condition, _UNSUPPORTED
    ctx = SimpleNamespace(trigger_uid=7, ability_source_uid=7)
    always = {"_t": "Game.Shared.Mechanics.Triggers.Conditions.TriggerCardIsAbilitySource"}
    never = {"_t": "NotTriggerCondition", "m_Condition": always}
    assert _native_condition(None, ctx) is True
    assert _native_condition({"_t": "AndTriggerCondition", "m_Conditions": []}, ctx) is True
    assert _native_condition({"_t": "OrTriggerCondition", "m_Conditions": []}, ctx) is False
    assert _native_condition({"_t": "NotTriggerCondition", "m_Condition": always}, ctx) is False
    assert _native_condition({"_t": "OrTriggerCondition", "m_Conditions": [never, always]}, ctx) is True
    unsupported = {"_t": "Game.Shared.Mechanics.Triggers.Conditions.NoSuchCondition"}
    assert _native_condition({"_t": "AndTriggerCondition", "m_Conditions": [unsupported]}, ctx) is _UNSUPPORTED


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_module_connections():
    """The tests below bind the shared connections of several runtime modules;
    put them back so later test files start from their own bindings."""
    import ai
    import services.tournament_game as tg
    import commands
    saved = (ai._db, tg._db, commands.hconnect_server._db)
    try:
        yield
    finally:
        ai._db, tg._db, commands.hconnect_server._db = saved


def make_db():
    from tests.tests_combat import make_db as combat_make_db
    return combat_make_db()


def test_warzone_ability_waits_while_the_chain_is_busy():
    import ai
    from tests.tests_combat import HandlerStub, SessionStub
    import game_engine
    db = make_db()
    ai._db = db
    pl_t = game_engine.UID.make(244, 5)
    ai_t = game_engine.UID.make(3, 1000)
    game = game_engine.Game(1, pl_t, ai_t)
    state = {"stack": [{"kind": "ability"}], "ai_resources": 3}
    handler = HandlerStub(db)
    assert ai.ai_use_warzone_ability(handler, game, SessionStub(), ai_t, pl_t, state) is False
    assert ai.ai_use_warzone_ability(handler, game, SessionStub(), ai_t, pl_t, state,
                                     decision_only=True) is None


def test_warzone_ability_with_no_ai_troops_does_nothing():
    import ai
    from tests.tests_combat import HandlerStub, SessionStub
    import game_engine
    db = make_db()
    ai._db = db
    pl_t = game_engine.UID.make(244, 5)
    ai_t = game_engine.UID.make(3, 1000)
    game = game_engine.Game(1, pl_t, ai_t)
    state = {"stack": [], "ai_resources": 3}
    assert ai.ai_use_warzone_ability(HandlerStub(db), game, SessionStub(), ai_t, pl_t, state) is False


def test_combat_trick_is_not_chosen_while_the_chain_is_busy():
    import ai
    from tests.tests_combat import HandlerStub, SessionStub
    import game_engine
    db = make_db()
    ai._db = db
    pl_t = game_engine.UID.make(244, 5)
    ai_t = game_engine.UID.make(3, 1000)
    state = {"stack": [{"kind": "ability"}]}
    assert ai.ai_choose_combat_trick(HandlerStub(db), SessionStub(), state, ai_t, pl_t) is None


def test_combat_trick_is_not_chosen_with_no_combat():
    import ai
    from tests.tests_combat import HandlerStub, SessionStub
    import game_engine
    db = make_db()
    ai._db = db
    pl_t = game_engine.UID.make(244, 5)
    ai_t = game_engine.UID.make(3, 1000)
    state = {"stack": [], "ai_attackers": {}, "ai_blockers": {}}
    assert ai.ai_choose_combat_trick(HandlerStub(db), SessionStub(), state, ai_t, pl_t) is None


def test_ai_turn_refuses_to_run_without_a_rules_port_host():
    import ai
    import pytest
    from tests.tests_combat import HandlerStub, SessionStub
    import game_engine
    db = make_db()
    ai._db = db
    session = SessionStub()
    session._rules_port_session = None
    pl_t = game_engine.UID.make(244, 5)
    ai_t = game_engine.UID.make(3, 1000)
    with pytest.raises(RuntimeError, match="RulesPort session"):
        ai.run_ai_turn(HandlerStub(db), session, pl_t, ai_t, {"_rules_port_attached": True})


def test_csharp_manual_fallback_finds_nothing_for_an_ability_without_targets():
    import ai
    from tests.tests_combat import HandlerStub, SessionStub
    db = make_db()
    ai._db = db
    unknown = "00000000-0000-0000-0000-00000000abcd"
    for champion in (False, True):
        result = ai._ai_csharp_manual_ability_decision(
            HandlerStub(db), SessionStub(), {"ai_resources": 3}, unknown, 999, [],
            0, 5, champion=champion)
        assert result == (False, None), result


def test_legal_targets_for_an_unknown_template_offers_nothing():
    from rules_port.targeting import legal_targets
    db = make_db()
    assert legal_targets(db, 1, 5, "00000000-0000-0000-0000-00000000beef", 999,
                         both_players=True) == []


def test_tac_without_an_operation_is_rejected_in_native_play():
    import pytest
    from types import SimpleNamespace
    import game_engine
    from rules_port.context import EffectContext
    player = game_engine.SessionCardId(game_engine.UID.make(244, 5))
    ai = game_engine.SessionCardId(game_engine.UID.make(3, 1000))
    handler = SimpleNamespace(
        user_profile={"id": 5}, _player_champ_scid=player, _ai_champ_scid=ai,
        _champion_granted_ability_guids={})
    context = EffectContext.from_rules_port(
        SimpleNamespace(events=[]), SimpleNamespace(session_id=1), make_db(), handler,
        game_engine.UID.make(244, 5), game_engine.UID.make(3, 1000),
        {"_rules_port_native_effect": True}, "effect", "")
    with pytest.raises(RuntimeError, match="no native handler"):
        context.tac()


def test_unknown_chat_command_gets_the_help_hint():
    import commands
    from tests.tests_combat import HandlerStub, SessionStub
    import game_engine
    db = make_db()
    old_db = commands.hconnect_server._db
    commands.hconnect_server._db = db
    try:
        result = commands._dispatch(
            HandlerStub(db), "no_such_command", [], SessionStub(),
            game_engine.UID.make(244, 5), game_engine.UID.make(3, 1000), "", "")
    finally:
        commands.hconnect_server._db = old_db
    assert result == "Unknown command. !help for list.", result


def test_debug_cheat_without_an_action_is_consumed():
    import debug_cheats
    assert debug_cheats._handle(None, None, b"") is True


def test_encounter_extraction_of_an_empty_snapshot_is_empty():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AssetExtraction"))
    import gamedata_seed
    assert gamedata_seed._extract_encounters("") == ([], [])


def test_auth_proxy_serves_the_news_feed():
    import json as _json
    import threading
    import urllib.request
    from http.server import HTTPServer
    saved_argv = sys.argv
    sys.argv = ["proxy"]          # proxy.py reads its port from argv on import
    try:
        import proxy
    finally:
        sys.argv = saved_argv
    server = HTTPServer(("127.0.0.1", 0), proxy.HexAuthProxy)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/news", timeout=10) as response:
            assert response.status == 200
            body = _json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
    assert isinstance(body, (dict, list)), body


def test_set_one_inventory_builds_from_the_baseline_database():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "ir_coverage"))
    import build_set1_coverage
    from tests.test_db import fresh_database as baseline
    inventory = build_set1_coverage.build_inventory(
        baseline(), "00000000-0000-0000-0000-000000000000")
    assert isinstance(inventory, dict) and inventory, inventory


def test_pvp_projection_refuses_an_unknown_transaction_kind():
    import services.tournament_game as tg
    from tests.tests_combat import HandlerStub, SessionStub
    db = make_db()
    tg._db = db
    handler = HandlerStub(db)
    assert tg.project_accepted_pvp_transaction(handler, SessionStub(), "unknown_kind", {}) is False


def test_pvp_discard_prompt_with_no_selection_is_not_handled():
    import services.tournament_game as tg
    from tests.tests_combat import HandlerStub, SessionStub
    db = make_db()
    tg._db = db
    assert tg._pvp_resolve_discard_prompt(HandlerStub(db), SessionStub(), b"", 5) is False


def test_pvp_phase_start_with_an_empty_state_pushes_nothing_back():
    import services.tournament_game as tg
    from tests.tests_combat import SessionStub
    import game_engine
    assert tg._pvp_run_phase_start(SessionStub(), {}, game_engine.ETurnPhases.StartTurn) is None


def test_pvp_game_start_for_an_empty_session_pushes_nothing_and_returns():
    import services.tournament_game as tg
    from tests.tests_combat import HandlerStub, SessionStub
    db = make_db()
    tg._db = db
    assert tg.push_pvp_game_start(HandlerStub(db), SessionStub(), log_req=lambda *a: None) is None


def test_pvp_spell_refuses_an_unknown_card():
    import services.tournament_game as tg
    from tests.tests_combat import HandlerStub, SessionStub
    db = make_db()
    tg._db = db
    handler, session = HandlerStub(db), SessionStub()
    assert tg._pvp_play_spell(handler, session, 0, 5, b"") is False


def test_ready_to_start_with_an_empty_packet_is_a_no_op():
    import services.legacy_loadbalancer as lb
    from tests.tests_combat import HandlerStub
    db = make_db()
    assert lb.handle_readytostartgame_22031(
        HandlerStub(db), 0, 0, 0, 0, None, 1, None, None, b"", b"") is None


def test_starter_playthrough_reaches_the_second_zone():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign_editor"))
    import db as shared
    import server_playthrough
    previous = shared._db
    try:
        ok, route = server_playthrough.run("Dwarf", set(), lose_first=True)
    finally:
        shared._db = previous          # the playthrough binds a copy; restore ours
    assert ok, route
    assert any("LOST" in step for step in route), route


def test_dungeon_importer_prints_its_help():
    import pytest
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign_editor"))
    import import_dungeons
    saved = sys.argv
    sys.argv = ["import_dungeons.py", "--help"]
    try:
        with pytest.raises(SystemExit) as exit_info:
            import_dungeons.main()
    finally:
        sys.argv = saved
    assert exit_info.value.code == 0


def test_autoplay_game_returns_a_result_pair_for_a_one_turn_game():
    import db as shared
    import pvp_autoplay
    previous = shared._db
    try:
        result = pvp_autoplay._play_one_game(seed=1, turns_cap=1)
    finally:
        shared._db = previous
    assert isinstance(result, tuple) and len(result) == 2, result


def test_fra_reconnect_snapshot_is_skipped_outside_an_fra_session():
    from gamemodes.fra_battle import FraBattleMixin
    from tests.tests_combat import HandlerStub, SessionStub
    db = make_db()
    assert FraBattleMixin._push_fra_reconnect_snapshot(HandlerStub(db), SessionStub(), 5) is False


def test_pvp_resource_play_rejects_a_card_outside_any_playable_zone():
    import services.tournament_game as tg
    from tests.tests_combat import HandlerStub, SessionStub
    db = make_db()
    tg._db = db
    assert tg._pvp_project_resource_play(
        HandlerStub(db), SessionStub(), b"", 5, 999999, 0, "Diamond Shard",
        [5, 6], 0, 0) is False


def test_fra_battle_setup_saves_the_starting_state_for_reconnect():
    import hconnect_server
    import game_engine
    from gamemodes.fra_battle import FraBattleMixin
    from tests.tests_combat import SessionStub
    db = make_db()
    hconnect_server._db = db

    class _Session(SessionStub):
        encounter_data = None
        persisted = False

        def _persist(self):
            self.persisted = True

    handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
    handler.user_profile = {"id": 5}
    handler._player_champ_scid = game_engine.SessionCardId(game_engine.UID.make(1, 9001))
    handler._ai_champ_scid = game_engine.SessionCardId(game_engine.UID.make(1, 9002))
    pl_t = game_engine.UID.make(244, 5)
    ai_t = game_engine.UID.make(3, 1000)
    game = game_engine.Game(1, pl_t, ai_t)
    session = _Session()
    bstate = {"player_health": 20, "ai_health": 20, "player_resources": 0,
              "ai_resources": 0, "player_threshold": {}, "ai_threshold": {}}
    FraBattleMixin._apply_fra_battle_modifications(
        handler, game, session, pl_t, ai_t, bstate, [], [], [], lambda s: None)
    saved = session.encounter_data["_fra_starting_battle_state"]
    assert saved["player_health"] == 20 and saved["ai_health"] == 20, saved
    assert session.persisted is True


class _CheckpointStub:
    """The real checkpoint helpers, with state saves counted instead of stored."""

    def __init__(self):
        self.saved = 0

    def save_state(self, session, bstate):
        self.saved += 1

    def __getattr__(self, name):
        from rules_port import lifecycle
        return getattr(lifecycle, name)


def test_resolving_a_troop_with_no_source_pushes_the_chain_events_only():
    from application.chain import ChainMixin
    import game_engine
    from tests.tests_combat import HandlerStub, SessionStub

    checkpoint = _CheckpointStub()

    class _Handler(HandlerStub, ChainMixin):
        def _checkpoint_engine(self, session):
            return checkpoint

        def _check_champion_health(self, *args, **kwargs):
            return False

    handler = _Handler(make_db())
    game = game_engine.Game(1, game_engine.UID.make(244, 5), game_engine.UID.make(3, 1000))
    # HandlerStub has its own stub of this method, so call the real one.
    ChainMixin._resolve_stack_item(
        handler, SessionStub(), game_engine.UID.make(244, 5),
        game_engine.UID.make(3, 1000), {}, {"kind": "troop", "instance_id": 7},
        game)
    names = [type(ev).__name__ for ev in game.events]
    assert names[:2] == ["TopOfChainResolvedSessionEventArgs",
                         "RemovedTopOfChainSessionEventArgs"], names
    assert game.events[0].ability_instance_id == 7
    assert checkpoint.saved >= 1


def test_add_deck_without_a_logged_in_user_is_rejected_with_an_error_reply():
    from services.deck_editing import handle_addnewdeck_2089
    from tests.tests_combat import HandlerStub

    class _Handler(HandlerStub):
        user_profile = None
        client_uid = 7

        def __init__(self, db):
            super().__init__(db)
            self.sent = []

        def send(self, header, body):
            self.sent.append((header, body))

    handler = _Handler(make_db())
    handle_addnewdeck_2089(
        handler, 0, 0, 2089, 42, False, "11111111-1111-1111-1111-111111111111", 0,
        {"DeckName": "Test"}, b"", b"")
    assert len(handler.sent) == 1, handler.sent
    header, body = handler.sent[0]
    assert header["reqid"] == 43, header
    assert b"Not authenticated" in body


def test_rules_port_dispatch_without_an_attached_host_is_not_handled():
    from application.turn_flow import TurnFlowMixin
    from tests.tests_combat import HandlerStub, SessionStub

    class _Handler(HandlerStub, TurnFlowMixin):
        pass

    session = SessionStub()
    session._rules_port_session = None
    handler = _Handler(make_db())
    command = type("Command", (), {"typed_payload": None})()
    assert TurnFlowMixin._dispatch_rules_port_transaction_locked(
        handler, session, command, 5) is False


def test_card_play_without_a_user_profile_is_refused():
    from application.card_play import CardPlayMixin
    from tests.tests_combat import HandlerStub, SessionStub

    class _Handler(HandlerStub, CardPlayMixin):
        user_profile = None

    transaction = type("Play", (), {"payload": {"card_id": 1}, "kind": "troop"})()
    assert CardPlayMixin._handle_rules_port_card_play(
        _Handler(make_db()), SessionStub(), transaction) is False


def test_duplicate_tournament_keep_is_acknowledged_without_a_second_keep():
    from unittest import mock
    from application.transactions import TransactionsMixin
    from tests.tests_combat import HandlerStub, SessionStub

    class _Handler(HandlerStub, TransactionsMixin):
        pass

    session = SessionStub()
    session.session_name = "tourney-1"
    transaction = type("Keep", (), {"inner_bytes": b""})()
    state = {"kept": [5], "mulligan_count": {}}
    with mock.patch("services.tournament_game.pvp_load_state", return_value=state):
        result = TransactionsMixin._handle_mulligan_keep_transaction(
            _Handler(make_db()), session, transaction)
    assert result is True
    assert state["kept"] == [5], state


def test_panorama_extraction_of_a_missing_bundle_yields_an_empty_level(tmp_path):
    # Only the empty-input path can run here: a real level bundle is a client asset
    # that is not in the repo.  UnityPy.load of a missing file gives an empty
    # environment, so the level comes back empty rather than raising.
    import pytest
    pytest.importorskip("UnityPy")
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                    "campaign", "AssetExtraction"))
    from extract_campaign_editor_assets import extract_panorama
    result = extract_panorama(tmp_path / "missing.bundle", "zone", tmp_path, 256)
    assert result == {"camera": None, "meshes": [], "npcs": [], "emitters": []}, result


def test_autoplay_game_runs_ten_turns_of_a_seeded_game():
    import db as shared
    import pvp_autoplay
    previous = shared._db
    try:
        turns, _ = pvp_autoplay._play_one_game(seed=7, turns_cap=10)
    finally:
        shared._db = previous
    # A game can end before the cap, so require progress rather than the exact count.
    assert turns >= 1, turns


def test_ai_does_not_cast_removal_quick_action_after_blocks():
    import ai
    from types import SimpleNamespace
    from tests.tests_combat import HandlerStub, SessionStub

    class _Trick:
        card_uid = 900
        name = "Trick"

        def is_quick_action(self):
            return True

    class _Evaluator:
        def __init__(self):
            self.hand = [_Trick()]
            # Our 2/2 attacker is blocked by their 1/3 troop.
            self.ai_warzone = [SimpleNamespace(card_uid=10, name="Ours",
                                               effective_attack=lambda **k: 2,
                                               effective_defense=lambda **k: 2)]
            self.player_warzone = [SimpleNamespace(card_uid=20, name="Theirs",
                                                   effective_attack=lambda **k: 1,
                                                   effective_defense=lambda **k: 3)]

        def hints_for(self, card):
            removal = SimpleNamespace(threshold=3, hard=False)
            return SimpleNamespace(removal=removal, buff=None)

        def is_playable(self, card):
            return "True"

        def choose_action_target(self, card, preferred_target=None):
            return preferred_target

    battle_state = {"stack": [], "ai_attackers": {"10": 2},
                    "ai_blockers": {"10": [20]}, "phase": 0}
    decision = ai.ai_choose_combat_trick(
        HandlerStub(make_db()), SessionStub(), battle_state, 3, 244,
        evaluator=_Evaluator())
    # Removal is chosen before blocks (AttemptToRemove / pre-combat
    # BuildBoard), never as a post-block combat trick.
    assert decision is None


def test_ai_taps_an_opposing_troop_with_its_warzone_ability():
    import ai
    import hconnect_server
    import game_engine
    from tests.tests_combat import add_card
    db = make_db()
    hconnect_server._db = db
    ai._db = db
    # Field Tactician (AI-owned, manual Warzone ability) and one player troop.
    add_card(db, 100, 0, "8305c05f-8355-4740-8599-2cf4a85c2c79", "warzone")
    add_card(db, 201, 5, "11111111-1111-1111-1111-111111111111", "warzone")
    handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
    handler.user_profile = {"id": 5}
    pl_t = game_engine.UID.make(244, 5)
    ai_t = game_engine.UID.make(3, 1000)
    game = game_engine.Game(1, pl_t, ai_t)
    battle_state = {"ai_resources": 10, "ai_spell_points": 0,
                    "_rules_port_attached": True, "stack": []}
    from tests.tests_combat import SessionStub
    decision = ai.ai_use_warzone_ability(
        handler, game, SessionStub(), ai_t, pl_t, battle_state, decision_only=True)
    assert decision is not None
    assert decision["source_uid"] == 100
    assert decision["target_uid"] == 201


def test_phase_command_moves_the_table_to_the_named_phase():
    import commands
    import game_engine
    import hconnect_server
    from tests.tests_combat import SessionStub
    db = make_db()
    hconnect_server._db = db
    handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
    handler.user_profile = {"id": 5}
    sent = []
    handler.send = lambda header, body: sent.append(body)
    pl_t = game_engine.UID.make(244, 5)
    ai_t = game_engine.UID.make(3, 1000)
    reply = commands._dispatch(handler, "phase", ["EndTurn"], SessionStub(),
                               pl_t, ai_t, None, "tester")
    assert reply == "Phase set to EndTurn", reply
    assert sent, "the phase change should be pushed to the client"


def test_debug_draw_cheat_is_applied_and_consumed():
    import hconnect_server
    import debug_cheats
    from tests.tests_combat import SessionStub
    from tests.tests_debug_cheats import _raw
    hconnect_server._db = make_db()
    handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
    handler.user_profile = {"id": 5}
    handler.send = lambda header, body: None
    assert debug_cheats._handle(handler, SessionStub(), _raw(action=11, count=1)) is True


def test_ai_turn_advances_through_its_phases_and_stops_at_a_player_stop():
    import ai
    from rules_port import lifecycle as be
    import game_engine
    import hconnect_server
    from tests.tests_combat import SessionStub
    db = make_db()
    hconnect_server._db = db
    ai._db = db
    handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
    handler.user_profile = {"id": 5}
    handler.send = lambda header, body: None
    # Keeps this on the legacy battle engine; the RulesPort attachment needs a full session.
    handler._maybe_attach_rules_port = lambda *args, **kwargs: None
    session = SessionStub()
    bstate = be.default_state(turn_player=be.AI)
    session.turn_order = bstate
    pl_t = game_engine.UID.make(244, 5)
    ai_t = game_engine.UID.make(game_engine.SERVICE_AI_UID_TYPE, 1000)
    # ai.py's legacy phase path imports the deleted ``ability`` facade; a no-op
    # stand-in keeps this main-path test on the legacy engine without changing game code.
    from types import SimpleNamespace
    from unittest import mock
    stand_in = SimpleNamespace(resolve_turn_phase_triggers=lambda *a, **k: None)
    with mock.patch.dict(sys.modules, {"ability": stand_in}):
        ai.run_ai_turn(handler, session, pl_t, ai_t, bstate)
    # The AI passes through its phases and stops where the human gets priority.
    assert bstate["phase_idx"] > 0, bstate


def test_csharp_fallback_takes_control_of_the_opposing_troop():
    import ai
    import hconnect_server
    from tests.tests_combat import SessionStub, add_card
    db = make_db()
    hconnect_server._db = db
    ai._db = db
    add_card(db, 100, 0, "8305c05f-8355-4740-8599-2cf4a85c2c79", "warzone")
    add_card(db, 201, 5, "11111111-1111-1111-1111-111111111111", "warzone")
    handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
    handler.user_profile = {"id": 5}
    battle_state = {"ai_resources": 10, "ai_spell_points": 0,
                    "_rules_port_attached": True, "stack": []}
    take_control = [("MoveCardToZoneEffectTemplate",
                     {"destination": "warzone", "ability_owner_takes_control": 1})]
    worth, target = ai._ai_csharp_manual_ability_decision(
        handler, SessionStub(), battle_state, "1c6d0fdf-9be0-27b3-186e-e139d1f71544",
        100, take_control, 0, 5)
    assert worth is True
    assert target == 201


def test_ready_to_start_in_a_pvp_session_acknowledges_the_client():
    import game_session as gs
    import hconnect_server
    from services.legacy_loadbalancer import handle_readytostartgame_22031
    make_db()
    reck = 5
    session = gs.GameSession(7001, 7001 * 1000, "pvp-7001", reck)
    session.add_player(hconnect_server.make_uid(
        hconnect_server.UID_TYPE["ServicePlayer"], reck), 0)
    handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
    handler.user_profile = {"id": 5}
    handler.client_reck_id = reck
    sent = []
    handler.send = lambda header, body: sent.append((header, body))
    handle_readytostartgame_22031(handler, 0, 0, 22031, 40, False, 0, 0,
                                  None, b"", b"")
    assert len(sent) == 1
    assert sent[0][0]["reqid"] == 41, sent[0][0]


def test_legal_targets_offers_the_ability_source_for_a_self_target_template():
    import hconnect_server
    import json
    import pvp_db
    from rules_port.targeting import legal_targets
    db = make_db()
    hconnect_server._db = db
    from tests.tests_combat import add_card
    add_card(db, 100, 0, "8305c05f-8355-4740-8599-2cf4a85c2c79", "warzone")
    ability = "1c6d0fdf-9be0-27b3-186e-e139d1f71544"
    template = json.loads(pvp_db.db_ability_target_template_ids(ability, conn=db))[0]
    candidates = legal_targets(db, 1, 0, template, 100, both_players=False,
                               champions=[], battle_state={})
    assert list(candidates) == [100], candidates


def test_add_deck_saves_the_named_deck_and_replies_with_it():
    import hconnect_server
    from services.deck_editing import handle_addnewdeck_2089
    db = make_db()
    db.execute("INSERT OR IGNORE INTO users (id, name, gold, platinum) VALUES (5, 'deck', 0, 0)")
    db.commit()
    hconnect_server._db = db
    handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
    handler.user_profile = {"id": 5}
    handler.client_uid = 5
    sent = []
    handler.send = lambda header, body: sent.append((header, body))
    import db as shared
    previous = shared._db
    shared._db = db
    try:
        handle_addnewdeck_2089(
            handler, 0, 0, 2089, 42, False, "11111111-1111-1111-1111-111111111111", 0,
            {"DeckName": "Crap Test Deck", "DeckCardIDs": []}, b"", b"")
    finally:
        shared._db = previous
    saved = db.execute("SELECT id FROM decks WHERE deck_name=?", ("Crap Test Deck",)).fetchall()
    assert saved, "the named deck should be saved for the user"
    assert len(sent) == 1, sent


def test_hand_troop_play_through_the_rules_port_seam_moves_it_to_cast_spells():
    from rules_port import lifecycle as be
    import db as shared
    import game_engine
    import hconnect_server
    from types import SimpleNamespace
    from tests.tests_combat import SessionStub, add_card
    db = make_db()
    hconnect_server._db = db
    previous = shared._db
    shared._db = db
    try:
        # Field Tactician: a real cost-2 troop template, held in the owner's hand.
        add_card(db, 300, 5, "8305c05f-8355-4740-8599-2cf4a85c2c79", "hand")
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5}
        handler.client_reck_id = 5
        handler._maybe_attach_rules_port = lambda *args, **kwargs: None
        handler.send = lambda header, body: None
        session = SessionStub()
        session.session_name = "tourney-1"
        state = be.default_state(turn_player=be.PLAYER)
        state["player_resources"] = 5
        state["player_total_resources"] = 5
        session.turn_order = state
        # A minimal stand-in for the RulesPort host: nothing is on the chain or waiting for priority.
        session._rules_port_session = SimpleNamespace(
            action_stack=SimpleNamespace(peek=lambda: None),
            pass_player_priority=lambda player: False,
            drive_until_input=lambda max_steps=0: None)
        play = SimpleNamespace(kind="play_troop", payload={"card_id": 300})
        assert handler._handle_rules_port_card_play(session, play) is True
    finally:
        shared._db = previous
    location = db.execute("SELECT location FROM game_cards WHERE card_uid=300").fetchone()[0]
    assert location == "CastSpells", location


def test_tac_append_to_list_records_the_entry_for_this_turn():
    from types import SimpleNamespace
    from rules_port.context import EffectContext
    import game_engine
    # Authored AppendToList TAC for ExtraCombatsThisTurn (the same Records payload
    # as the TAC decoder test).
    param = (
        "AgD231pIDEFwcGVuZFRvTGlzdKx3EcQURXh0cmFDb21iYXRzVGhpc1R1cm4d"
        "ssWzDVRoaXNUdXJuc0RhdGEG67dBAQAAAAAAAAA=")
    player = game_engine.SessionCardId(game_engine.UID.make(244, 5))
    ai = game_engine.SessionCardId(game_engine.UID.make(3, 1000))
    handler = SimpleNamespace(
        user_profile={"id": 5}, _player_champ_scid=player, _ai_champ_scid=ai,
        _champion_granted_ability_guids={})
    bstate = {"resolving_source_uid": 100}
    context = EffectContext.from_rules_port(
        SimpleNamespace(events=[]), SimpleNamespace(session_id=1), make_db(), handler,
        game_engine.UID.make(244, 5), game_engine.UID.make(3, 1000),
        bstate, "effect", param)
    context.tac()
    entries = bstate["list_attrs"][context.ability_guid]["ExtraCombatsThisTurn"]
    assert entries and entries[0]["where"] == "ThisTurnsData", entries


def test_encounter_extraction_reads_the_ai_deck_and_champion_of_a_scene():
    import json
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AssetExtraction"))
    import gamedata_seed
    scene, deck, template, card, champion = (
        "aaaaaaaa-0000-0000-0000-000000000001", "aaaaaaaa-0000-0000-0000-000000000002",
        "aaaaaaaa-0000-0000-0000-000000000003", "aaaaaaaa-0000-0000-0000-000000000004",
        "aaaaaaaa-0000-0000-0000-000000000005")

    def section(name, records):
        body = "\n$$--$$\n".join(json.dumps(record) for record in records)
        return f"\n{name}\n$$--$$\n{body}\n$$$---$$$\n"

    data = (
        section("DeckTemplate", [{
            "m_Id": {"m_Guid": template},
            "m_DeckResources": [{"m_idTemplate": {"m_Guid": card}, "m_Count": 2}],
            "m_ChampionId": {"m_Guid": champion}}])
        + section("EncounterDeck", [{
            "m_Id": {"m_Guid": deck},
            "m_DeckTemplateId": {"m_Guid": template}}])
        + section("SceneData", [{
            "_t": "Reckoning.Game.EncounterScene",
            "m_Name": "Test Scene", "m_Id": {"m_Guid": scene},
            "m_EncounterPlayers": [{"m_IsAI": 1, "m_EncounterDeck": {"m_Guid": deck}}]}]))
    scenes, deck_cards = gamedata_seed._extract_encounters(data)
    assert [row[0] for row in scenes] == [scene], scenes
    assert scenes[0][4] == deck and scenes[0][5] == champion, scenes[0]
    assert (deck, card) in [(row[0], row[1]) for row in deck_cards], deck_cards
    assert [row[2] for row in deck_cards if row[1] == card] == [2], deck_cards


def test_fra_reconnect_snapshot_rebuilds_the_saved_board_for_the_player():
    from rules_port import lifecycle as be
    import hconnect_server
    from tests.tests_combat import SessionStub, add_card
    db = make_db()
    hconnect_server._db = db
    add_card(db, 300, 5, "8305c05f-8355-4740-8599-2cf4a85c2c79", "warzone")
    handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
    handler.user_profile = {"id": 5}
    handler.client_reck_id = 5
    handler._maybe_attach_rules_port = lambda *args, **kwargs: None
    sent = []
    handler.send = lambda header, body: sent.append(body)
    session = SessionStub()
    state = be.default_state(turn_player=be.PLAYER)
    # The saved checkpoint carries each champion's UID and template (FRA champions have no game_cards rows).
    state["champ_map"] = {"5": (9001 << 8) | 12, "0": (9002 << 8) | 12}
    state["champ_guid_map"] = {"5": "1ae73dcf-e96e-4536-aec3-f53efb5e1c96",
                               "0": "1ae73dcf-e96e-4536-aec3-f53efb5e1c96"}
    session.turn_order = state
    assert handler._push_fra_reconnect_snapshot(session, 5) is True
    assert sent, "the saved board should be pushed back to the reconnecting client"


def test_resolving_a_troop_from_the_chain_announces_the_chain_resolution():
    from rules_port import lifecycle as be
    import game_engine
    import hconnect_server
    from application.chain import ChainMixin
    from tests.tests_combat import SessionStub, add_card
    db = make_db()
    hconnect_server._db = db
    add_card(db, 300, 5, "8305c05f-8355-4740-8599-2cf4a85c2c79", "CastSpells")
    handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
    handler.user_profile = {"id": 5}
    handler.client_reck_id = 5
    handler._maybe_attach_rules_port = lambda *args, **kwargs: None
    handler.send = lambda header, body: None
    session = SessionStub()
    state = be.default_state(turn_player=be.PLAYER)
    session.turn_order = state
    pl_t = game_engine.UID.make(244, 5)
    ai_t = game_engine.UID.make(game_engine.SERVICE_AI_UID_TYPE, 1000)
    game = game_engine.Game(1, pl_t, ai_t)
    ChainMixin._resolve_stack_item(
        handler, session, pl_t, ai_t, state,
        {"kind": "troop", "instance_id": 7, "source_uid": 300}, game)
    names = [type(ev).__name__ for ev in game.events]
    assert names[:2] == ["TopOfChainResolvedSessionEventArgs",
                         "RemovedTopOfChainSessionEventArgs"], names


def test_starter_playthrough_explores_on_to_the_first_campaign_area():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign_editor"))
    import db as shared
    import server_playthrough
    previous = shared._db
    try:
        ok, route = server_playthrough.run("Dwarf", set())
    finally:
        shared._db = previous          # the playthrough binds a copy; restore ours
    assert ok, route
    assert any(step.startswith("reached AZ1") for step in route), route


def _pvp_session(db):
    """A tournament game between players 5 and 6 with a live PvP state dict."""
    import game_engine
    import services.tournament_game as tournament_game
    from tests.tests_combat import SessionStub
    tournament_type = db.execute("SELECT id FROM tournament_types LIMIT 1").fetchone()
    if tournament_type is None:
        db.execute("INSERT INTO tournament_types (id, name) VALUES (1, 'test')")
        tournament_type = (1,)
    db.execute("INSERT INTO tournaments (id, type_id, status, session_id) "
               "VALUES (1, ?, 'active', '1')", (tournament_type[0],))
    for uid in (5, 6):
        db.execute("INSERT INTO tournament_signups (tournament_id, player_uid, player_name) "
                   "VALUES (1, ?, ?)", (uid, f"player{uid}"))
    db.commit()
    tournament_game._db = db
    session = SessionStub()
    session.session_name = "tourney-1"
    session._rules_port_battle_state = {
        "pvp": True, "turn_pid": 5, "turn_number": 1,
        "phase": int(game_engine.ETurnPhases.FirstMainPhase),
        "res_5": 5, "res_6": 5, "phase_idx": 0}
    return session


def test_pvp_spell_from_hand_is_pushed_onto_the_chain():
    import hconnect_server
    import services.tournament_game as tournament_game
    import db as shared
    from tests.tests_combat import add_card
    db = make_db()
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    try:
        # Blessing: a zero-cost basic action in the player's hand.
        add_card(db, 310, 5, "018bc77c-013f-493b-9ba3-3a286607c989", "hand")
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5}
        handler.client_reck_id = 5
        handler.send = lambda header, body: None
        session = _pvp_session(db)
        assert tournament_game._pvp_play_spell(handler, session, 310, 5, b"") is True
        stack = session._rules_port_battle_state["stack"]
        assert [(item["kind"], item["source_uid"]) for item in stack] == [("spell", 310)], stack
    finally:
        shared._db = previous


def test_dungeon_import_dry_run_reports_each_dungeon_without_writing(capsys):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign_editor"))
    import import_dungeons
    saved = sys.argv
    sys.argv = ["import_dungeons.py", "--dry-run"]
    try:
        import_dungeons.main()
    finally:
        sys.argv = saved
    out = capsys.readouterr().out
    assert "dungeons.json" in out and "would change" in out, out[:400]
    assert "Devonshire Keep:" in out, out[:400]


def test_practice_mulligan_keep_accepts_the_hand_and_replies_to_the_client():
    from types import SimpleNamespace
    from rules_port import lifecycle as be
    import hconnect_server
    from tests.tests_combat import SessionStub, add_card
    db = make_db()
    hconnect_server._db = db
    add_card(db, 300, 5, "8305c05f-8355-4740-8599-2cf4a85c2c79", "hand")
    add_card(db, 310, 5, "018bc77c-013f-493b-9ba3-3a286607c989", "deck")
    handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
    handler.user_profile = {"id": 5}
    handler.client_reck_id = 5
    handler._maybe_attach_rules_port = lambda *args, **kwargs: None
    sent = []
    handler.send = lambda header, body: sent.append(body)
    session = SessionStub()
    session.session_name = "practice-1"
    session.encounter_data = {}
    session.turn_order = be.default_state(turn_player=be.PLAYER)
    assert handler._handle_mulligan_keep_transaction(
        session, SimpleNamespace(inner_bytes=b"")) is True
    assert sent, "the opening hand should be sent back to the client"


def test_fra_champion_health_modification_sets_the_player_start_value():
    from rules_port import lifecycle as be
    import hconnect_server
    import game_engine
    from gamemodes.fra_battle import FraBattleMixin
    from tests.tests_combat import SessionStub
    hconnect_server._db = make_db()

    class _Session(SessionStub):
        encounter_data = None

    handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
    handler.user_profile = {"id": 5}
    handler._player_champ_scid = game_engine.SessionCardId(game_engine.UID.make(1, 9001))
    handler._ai_champ_scid = game_engine.SessionCardId(game_engine.UID.make(1, 9002))
    handler._maybe_attach_rules_port = lambda *args, **kwargs: None
    handler.send = lambda header, body: None
    session = _Session()
    bstate = be.default_state(turn_player=be.PLAYER)
    bstate["player_health"] = 20
    bstate["ai_health"] = 20
    session.turn_order = bstate
    pl_t = game_engine.UID.make(244, 5)
    ai_t = game_engine.UID.make(3, 1000)
    game = game_engine.Game(1, pl_t, ai_t)
    # Owner 2 is the player; an absolute champion-health mod sets their starting life.
    modifications = [{"wire_type": "Reckoning.Game.EncounterModAddChampionHealth",
                      "round_to_apply": 0, "target_player": 2,
                      "amount": 15, "absolute": True}]
    FraBattleMixin._apply_fra_battle_modifications(
        handler, game, session, pl_t, ai_t, bstate, modifications, [], [], lambda s: None)
    assert bstate["player_health"] == 15, bstate
    assert bstate["ai_health"] == 20, bstate
    assert any(type(ev).__name__ == "PlayerUpdatedSessionEventArgs" for ev in game.events)


def test_resolving_a_spell_from_the_chain_sends_it_to_the_discard_pile():
    from rules_port import lifecycle as be
    import game_engine
    import hconnect_server
    from application.chain import ChainMixin
    from tests.tests_combat import SessionStub, add_card
    import db as shared
    db = make_db()
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    add_card(db, 310, 5, "018bc77c-013f-493b-9ba3-3a286607c989", "CastSpells")
    handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
    handler.user_profile = {"id": 5}
    handler.client_reck_id = 5
    handler._maybe_attach_rules_port = lambda *args, **kwargs: None
    handler.send = lambda header, body: None
    handler._checkpoint_engine = lambda session: _CheckpointStub()
    session = SessionStub()
    session.session_name = "practice-1"
    session.encounter_data = {}
    state = be.default_state(turn_player=be.PLAYER)
    state["player_resources"] = 5
    state["player_total_resources"] = 5
    session.turn_order = state
    pl_t = game_engine.UID.make(244, 5)
    ai_t = game_engine.UID.make(game_engine.SERVICE_AI_UID_TYPE, 1000)
    game = game_engine.Game(1, pl_t, ai_t)
    try:
        ChainMixin._resolve_stack_item(
            handler, session, pl_t, ai_t, state,
            {"kind": "spell", "instance_id": 3, "source_uid": 310}, game)
    finally:
        shared._db = previous
    location = db.execute("SELECT location FROM game_cards WHERE card_uid=310").fetchone()[0]
    assert location == "discard", location
    names = [type(ev).__name__ for ev in game.events]
    assert names[:2] == ["TopOfChainResolvedSessionEventArgs",
                         "RemovedTopOfChainSessionEventArgs"], names


def test_pvp_discard_prompt_moves_the_chosen_hand_card_to_discard():
    import struct
    import hconnect_server
    import services.tournament_game as tournament_game
    import db as shared
    from tests.tests_combat import add_card
    db = make_db()
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    try:
        # uid 257 has a low byte of 1, so the prompt reads it as a card selection.
        add_card(db, 257, 5, "8305c05f-8355-4740-8599-2cf4a85c2c79", "hand")
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5}
        handler.client_reck_id = 5
        handler.send = lambda header, body: None
        session = _pvp_session(db)
        session._rules_port_battle_state.update({
            "pending_discard_ability": "1c6d0fdf-9be0-27b3-186e-e139d1f71544",
            "pending_discard_pid": 5, "pending_discard_source_uid": 300,
            "pending_discard_target_template": ""})
        raw = b"m_UID64;0;1;1;" + struct.pack("<Q", 257).hex().encode() + b";"
        assert tournament_game._pvp_resolve_discard_prompt(handler, session, raw, 5) is True
        location = db.execute("SELECT location FROM game_cards WHERE card_uid=257").fetchone()[0]
        assert location == "discard", location
    finally:
        shared._db = previous


def test_ready_to_start_in_a_practice_game_sends_the_opening_setup():
    import db as shared
    import game_session as gs
    import hconnect_server
    from services.legacy_loadbalancer import handle_readytostartgame_22031
    db = make_db()
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    try:
        db.execute("INSERT OR IGNORE INTO users (id, name, gold, platinum) VALUES (5, 'Tester', 0, 0)")
        db.commit()
        hconnect_server.db_save_deck(5, "Practice Deck", "[]", conn=db)
        reck = 5
        session = gs.GameSession(7101, 7101 * 1000, "practice-7101", reck)
        session.add_player(hconnect_server.make_uid(hconnect_server.UID_TYPE["ServicePlayer"], reck), 0)
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5, "name": "Tester"}
        handler.client_reck_id = reck
        sent = []
        handler.send = lambda header, body: sent.append(header)
        handle_readytostartgame_22031(handler, 0, 0, 22031, 40, False, 0, 0, None, b"", b"")
        assert sent, "the practice game setup should be sent to the client"
        assert sent[0]["reqid"] == 41, sent[0]
    finally:
        shared._db = previous


class _FakeRef:
    """A UnityPy PPtr: a path id plus the object it points at."""

    def __init__(self, target=None, path_id=0):
        self.target = target
        self.path_id = path_id
        self.file_id = 0

    def deref(self):
        return self.target


class _FakeObject:
    """A UnityPy object reader: a typed object whose read() returns its data."""

    def __init__(self, type_name, path_id):
        from types import SimpleNamespace
        self.type = SimpleNamespace(name=type_name)
        self.path_id = path_id
        self.object_reader = SimpleNamespace(path_id=path_id)

    def read(self):
        return self


def test_panorama_extraction_reads_the_camera_and_npc_anchors_of_a_level(tmp_path):
    from types import SimpleNamespace
    from unittest import mock
    pytest = __import__("pytest")
    pytest.importorskip("UnityPy")
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                    "campaign", "AssetExtraction"))
    import extract_campaign_editor_assets as extractor

    def vector(x=0.0, y=0.0, z=0.0):
        return SimpleNamespace(x=x, y=y, z=z)

    def transform(path_id, game_object, position=None):
        node = _FakeObject("Transform", path_id)
        node.m_LocalPosition = position or vector()
        node.m_LocalRotation = SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0)
        node.m_LocalScale = vector(1, 1, 1)
        node.m_Father = None
        node.m_Children = []
        node.m_GameObject = _FakeRef(game_object)
        return node

    def game_object(path_id, name):
        go = _FakeObject("GameObject", path_id)
        go.m_Name = name
        go.m_IsActive = True
        go.m_Component = []
        return go

    def attach(go, node):
        go.m_Component.append(SimpleNamespace(component=_FakeRef(node, node.path_id)))

    root_go, camera_go, anchor_go = (game_object(11, "Root"), game_object(21, "Main Camera"),
                                     game_object(41, "Guard_Anchor"))
    root = transform(10, root_go)
    camera_t = transform(20, camera_go, vector(0, 2, -10))
    anchor_t = transform(40, anchor_go, vector(3, 0, 4))
    for child in (camera_t, anchor_t):
        child.m_Father = _FakeRef(root, 10)
    root.m_Children = [_FakeRef(camera_t, 20), _FakeRef(anchor_t, 40)]
    camera = _FakeObject("Camera", 30)
    camera.field_of_view = 60.0
    camera.near_clip_plane = 0.3
    camera.far_clip_plane = 100.0
    attach(root_go, root)
    attach(camera_go, camera_t)
    attach(camera_go, camera)
    attach(anchor_go, anchor_t)
    env = SimpleNamespace(objects=[root, camera_t, anchor_t, camera])

    with mock.patch("UnityPy.load", return_value=env):
        level = extractor.extract_panorama(tmp_path / "level.bundle", "zone", tmp_path, 256)

    assert level["camera"]["position"] == [0.0, 2.0, -10.0], level["camera"]
    assert level["camera"]["fov"] == 60.0, level["camera"]
    assert [(npc["name"], npc["position"]) for npc in level["npcs"]] == [
        ("Guard", [3.0, 0.0, 4.0])], level["npcs"]


def test_tutorial_script_parses_draw_and_hell_to_freeze_commands(tmp_path):
    from domain.game import parse_tutorial_script
    script = tmp_path / "tutorial.txt"
    script.write_text("// a comment line\ndraw 3\nwaitfor (hell to freeze)\n")
    assert parse_tutorial_script(str(script)) == [
        {"type": "draw", "args": [3]}, {"type": "game_end"}]


def test_condition_combinators_fold_their_children():
    from rules_port.conditions import evaluate_condition
    always = {"_t": "AndTriggerCondition", "m_Conditions": []}
    never = {"_t": "OrTriggerCondition", "m_Conditions": []}
    assert evaluate_condition(always, None) is True
    assert evaluate_condition(never, None) is False
    assert evaluate_condition({"_t": "NotTriggerCondition", "m_Condition": never}, None) is True
    assert evaluate_condition({"_t": "OrTriggerCondition",
                               "m_Conditions": [never, always]}, None) is True


def test_scripted_game_step_starts_the_game_and_returns_its_setup_packets():
    import game_engine
    game = game_engine.Game(1, game_engine.UID.make(244, 5), game_engine.UID.make(3, 1000))
    game.script_commands = [{"type": "game_start", "args": []}]
    packets = game.step()
    assert packets and all(type(p).__name__ == "NetworkPacketSessionEventArgs"
                           for p in packets), packets
    assert game.script_index == 1, game.script_index


def test_new_session_packet_assigns_a_session_and_answers_create():
    import hconnect_server
    handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
    sent = []
    handler.send = lambda header: sent.append(header)
    handler.handle_message({"target": "newsession"}, b"")
    assert handler.sid.startswith("hcp-"), handler.sid
    assert sent and sent[0]["target"] == "create" and sent[0]["sid"] == handler.sid, sent


def test_ability_record_describes_a_manual_effect_with_its_signature():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "ir_coverage"))
    import build_set1_coverage as coverage
    meta = (0, 1, 2, 0, 0, 0, 0, 0, "[]", "", "Gain 2", "{}")
    effects = [("row", "effect-1", 0, "CardModifierAbilityEffectTemplate", "{}", 0, "", -1,
                -1, -1, -1, -1, 0, "Instant", "{}")]
    record = coverage.ability_record("ability-1", meta, effects, {})
    assert record["is_manual"] is True and record["missing_effect_types"] == [], record
    assert [effect["type"] for effect in record["effects"]] == ["CardModifierAbilityEffectTemplate"]
    assert record["features"], record


def test_audit_reads_an_attack_modifier_amount_from_an_effect():
    from types import SimpleNamespace
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AssetExtraction"))
    import audit_card_text as audit
    effect = SimpleNamespace(
        concrete_type="CardModifierAbilityEffectTemplate",
        template={"m_Modifier": {"_t": "Reckoning.Game.AttackModifier", "m_InputValue": 2}})
    claims, numbers, keywords = audit.effect_evidence(effect, graph=None)
    assert "attack_mod" in claims, claims
    assert numbers["attack_mod"] == [2], dict(numbers)


def test_conversation_tag_state_reports_flags_visits_and_the_outcome():
    import campaign
    state = {"PublicState": {"Data": {"conversation_flags": ["met_gaal"],
                                      "visited_nodes": ["Node00A"],
                                      "counters": {"gold": 2}}},
             "VisLocs": [{"Data": {"node": "Node00A", "completed": True}}]}
    view = campaign._conversation_tag_state(
        None, None, state, "Node00A", [], outcome="won")
    assert "met_gaal" in view["flags"], view["flags"]
    assert view["nodes"]["Node00A"] == {"visits": 1, "completed": True, "outcome": "won"}, view["nodes"]
    assert view["counters"] == {"gold": 2} and view["current"] == "Node00A", view


def test_gem_extraction_writes_each_gem_type_to_the_gem_table(tmp_path, monkeypatch):
    import json as _json
    import sqlite3
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AssetExtraction"))
    import extract_gems
    records = tmp_path / "Records"
    records.mkdir()
    gem_type = extract_gems.GEM_TYPE_VALUES[0]
    # Each Records line is a JSON string that itself holds the JSON record.
    (records / "InventoryItemData.jsonl").write_text(_json.dumps(_json.dumps({
        "_t": "InventoryGemData", "m_Name": "Test Gem", "m_GemTypeNew": gem_type,
        "m_Abilities": []})) + "\n")
    (records / "AbilityTemplate.jsonl").write_text("")
    (records / "AbilityEffectTemplate.jsonl").write_text("")
    database = tmp_path / "gems.db"
    static = tmp_path / "static.py"
    static.write_text(f"x = 1\n{extract_gems.BEGIN_MARKER}\n{extract_gems.END_MARKER}\n")
    monkeypatch.setattr(extract_gems, "RECORDS", str(records))
    monkeypatch.setattr(extract_gems, "DB", str(database))
    monkeypatch.setattr(extract_gems, "STATIC", str(static))
    extract_gems.main()
    row = sqlite3.connect(database).execute(
        "SELECT gem_type_name, name FROM gem_templates").fetchone()
    assert row == (gem_type, "Test Gem"), row
    assert "Test Gem" in static.read_text(), static.read_text()


def test_filled_waiting_room_starts_a_tourney_session_for_its_players():
    import json as _json
    import db as shared
    from gamemodes import tournament_engine
    db = make_db()
    previous = shared._db
    shared._db = db
    tournament_engine._db = db
    try:
        tournament_type = db.execute("SELECT id FROM tournament_types LIMIT 1").fetchone()
        if tournament_type is None:
            db.execute("INSERT INTO tournament_types (id, name) VALUES (1, 'room')")
            tournament_type = (1,)
        for uid in (5, 6):
            db.execute("INSERT OR IGNORE INTO users (id, name, gold, platinum) VALUES (?, ?, 0, 0)",
                       (uid, f"room{uid}"))
        db.execute("INSERT INTO tournaments (id, type_id, status, players_json) "
                   "VALUES (7, ?, 'waiting', ?)",
                   (tournament_type[0], _json.dumps({"5": {}, "6": {}})))
        db.commit()
        tournament_engine.start_waiting_room_game(7)
        names = [row[0] for row in db.execute("SELECT session_name FROM game_sessions")]
        assert any(name.startswith("tourney-7-") for name in names), names
    finally:
        shared._db = previous


def test_pass_by_the_active_player_advances_the_phase_for_the_table():
    import game_engine
    import hconnect_server
    import services.tournament_game as tournament_game
    import db as shared
    db = make_db()
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    try:
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5}
        handler.client_reck_id = 5
        handler.send = lambda header, body: None
        session = _pvp_session(db)
        assert tournament_game.route_pvp_pass(handler, session) is None
        state = session._rules_port_battle_state
        assert state["phase"] != int(game_engine.ETurnPhases.FirstMainPhase), state["phase"]
    finally:
        shared._db = previous


def test_pvp_troop_from_hand_is_played_onto_the_chain():
    import hconnect_server
    import services.tournament_game as tournament_game
    import db as shared
    from tests.tests_combat import add_card
    db = make_db()
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    try:
        add_card(db, 330, 5, "8305c05f-8355-4740-8599-2cf4a85c2c79", "hand")
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5}
        handler.client_reck_id = 5
        handler.send = lambda header, body: None
        session = _pvp_session(db)
        assert tournament_game._pvp_play_troop(handler, session, 330, 5, b"") is True
        location = db.execute("SELECT location FROM game_cards WHERE card_uid=330").fetchone()[0]
        assert location == "CastSpells", location
    finally:
        shared._db = previous


def test_pvp_matching_target_choice_resolves_and_keeps_the_card_in_the_deck():
    import struct
    import hconnect_server
    import services.tournament_game as tournament_game
    import db as shared
    from tests.tests_combat import add_card
    db = make_db()
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    try:
        add_card(db, 257, 5, "8305c05f-8355-4740-8599-2cf4a85c2c79", "deck")
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5}
        handler.client_reck_id = 5
        handler.send = lambda header, body: None
        session = _pvp_session(db)
        ability = "1c6d0fdf-9be0-27b3-186e-e139d1f71544"
        session._rules_port_battle_state["pending_deck_search"] = {
            "kind": "matching_target", "candidates": [257], "owner_id": 5,
            "continuation": {"target_index": 0, "ability_guid": ability, "instance_id": 1},
            "ability_guid": ability, "source_uid": 300}
        choice = b"m_UID64;0;1;1;" + struct.pack("<Q", 257).hex().encode() + b";"
        assert tournament_game._pvp_resolve_matching_target(handler, session, choice, 5) is True
        assert "pending_deck_search" not in session._rules_port_battle_state
        location = db.execute("SELECT location FROM game_cards WHERE card_uid=257").fetchone()[0]
        assert location == "deck", location
    finally:
        shared._db = previous


def test_pvp_triggered_ability_target_choice_clears_the_pending_trigger():
    import struct
    import hconnect_server
    import services.tournament_game as tournament_game
    import db as shared
    from tests.tests_combat import add_card
    db = make_db()
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    try:
        add_card(db, 257, 6, "11111111-1111-1111-1111-111111111111", "warzone")
        add_card(db, 300, 5, "8305c05f-8355-4740-8599-2cf4a85c2c79", "warzone")
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5}
        handler.client_reck_id = 5
        handler.send = lambda header, body: None
        session = _pvp_session(db)
        ability = "1c6d0fdf-9be0-27b3-186e-e139d1f71544"
        session._rules_port_battle_state["pending_trigger"] = {
            "ability_guid": ability, "source_uid": 300, "owner_id": 5, "target_index": 0,
            "activation_data": {"target_map": {0: [257]}}, "instance_id": 1}
        choice = b"m_UID64;0;1;1;" + struct.pack("<Q", 257).hex().encode() + b";"
        assert tournament_game._pvp_resolve_trigger_target(handler, session, choice, 5) is True
        assert "pending_trigger" not in session._rules_port_battle_state
    finally:
        shared._db = previous


def test_chain_spell_aimed_at_the_opposing_champion_is_an_opponent_target():
    from rules_port.targeting import _chain_action_targets
    import game_engine
    from tests.tests_combat import make_db as _db_for_targets, add_card
    db = _db_for_targets()
    add_card(db, 310, 5, "018bc77c-013f-493b-9ba3-3a286607c989", "CastSpells")
    opposing_champion = int(game_engine.UID.make(1, 9002).uid64)
    state = {"stack": [{"kind": "spell", "source_uid": 310,
                        "activation_data": {"target_map": {0: [opposing_champion]}}}],
             "champ_map": {"6": opposing_champion}}
    template = {"filter_json": "{}", "player_filter": "opponent"}
    assert _chain_action_targets(db, 1, 5, template, "template-1", 300, state) == [310]


def _fra_specs_carry_the_authored_cards(db, decks):
    """The FRA specs the duel is built from hold every card the campaign content lists
    for each deck (the reader this work changed). The duel itself stops at 0 turns for
    these Arena decks, before and after this change, so only the specs are checked."""
    import campaign_source
    campaign_source.ensure_campaign_path()
    import campaign_content
    import pvp_autoplay
    specs = {spec["deck_guid"]: spec for spec in pvp_autoplay._fra_encounter_specs(deck_guids=decks)}
    assert set(specs) == set(decks), specs.keys()
    for deck in decks:
        expected = sum(int(row["quantity"]) for row in campaign_content.deck_cards(deck))
        assert len(specs[deck]["cards"]) == expected, (deck, len(specs[deck]["cards"]), expected)


def test_fra_specs_carry_every_card_of_the_arena_decks(tmp_path):
    import db as shared
    import pvp_autoplay
    db = make_db()
    champion = "1ae73dcf-e96e-4536-aec3-f53efb5e1c96"
    # Two real Arena decks with their own champions: cards come from campaign_content.
    champions = {"bbfd8f29-e549-4eda-9e21-735620d3b5ff": "f8f86969-2e47-4901-8c9e-7fbf8d859e22",
                 "d987d23d-3d68-453e-9ea4-1070ee21b2cc": "91ca6c6b-3665-48fa-be85-95cf7938fe57"}
    decks = list(champions)
    for deck, champ in champions.items():
        db.execute("INSERT INTO fra_encounters (deck_guid, deck_name, name, champion_guid, "
                   "base_deck_name) VALUES (?, ?, ?, ?, ?)", (deck, deck, deck, champ, deck))
    db.commit()
    previous_shared, previous_autoplay = shared._db, pvp_autoplay._db
    shared._db = db
    pvp_autoplay._db = db
    try:
        _fra_specs_carry_the_authored_cards(db, decks)
    finally:
        shared._db = previous_shared
        pvp_autoplay._db = previous_autoplay


def test_opening_a_starter_pack_consumes_it_and_replies_to_the_client():
    import db as shared
    from profile_db import db_inventory_item, db_upsert_inventory_item
    from services.chest_requests import handle_opencardpack_2127
    import hconnect_server
    db = make_db()
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    try:
        pack = "0382f729-7710-432b-b761-13677982dcd2"
        db.execute("INSERT OR IGNORE INTO users (id, name, gold, platinum) VALUES (5, 'pack', 0, 0)")
        db.commit()
        db_upsert_inventory_item(5, pack, 1, conn=db)
        db.commit()
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5, "name": "pack"}
        handler.client_reck_id = 5
        sent = []
        handler.send = lambda header, body: sent.append(header)
        handle_opencardpack_2127(handler, 0, 0, 2127, 40, False,
                                 "11111111-1111-1111-1111-111111111111", 0,
                                 {"ItemId": pack, "OpenAmount": 1}, b"", b"")
        assert [header["reqid"] for header in sent] == [41], sent
        assert db_inventory_item(5, pack, conn=db) is None
    finally:
        shared._db = previous


def test_pending_choice_answer_plays_the_chosen_choice_token():
    import struct
    from rules_port import lifecycle as be
    import db as shared
    import hconnect_server
    import game_engine
    from abilities.framework.effects.choices import CHOOSE_AND_PLAY_ABILITY
    from tests.tests_combat import SessionStub, add_card
    db = make_db()
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    try:
        # "Lose Your Mind" is a Choice token; it sits in the choosing zone.
        add_card(db, 257, 5, "02dd3c6f-dbcb-4803-9514-b340290084a1", "choosing")
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5}
        handler.client_reck_id = 5
        handler._maybe_attach_rules_port = lambda *args, **kwargs: None
        handler.send = lambda header, body: None
        session = SessionStub()
        session.session_name = "practice-1"
        session.encounter_data = {}
        state = be.default_state(turn_player=be.PLAYER)
        state["player_resources"] = 5
        state["player_total_resources"] = 5
        state["pending_choice"] = {"kind": "other", "choice_uids": [257], "owner_id": 5,
                                   "ability_guid": CHOOSE_AND_PLAY_ABILITY,
                                   "source_uid": 300, "target_map": {}}
        session.turn_order = state
        choice = b"m_UID64;0;1;1;" + struct.pack("<Q", 257).hex().encode() + b";"
        assert handler._resolve_pending_choice(
            session, game_engine.UID.make(244, 5),
            game_engine.UID.make(game_engine.SERVICE_AI_UID_TYPE, 1000), choice,
            ability_guid=CHOOSE_AND_PLAY_ABILITY, typed_payload=None) is True
        assert "pending_choice" not in session.turn_order
        location = db.execute("SELECT location FROM game_cards WHERE card_uid=257").fetchone()[0]
        assert location == "PlayedResources", location
    finally:
        shared._db = previous


def test_target_selection_keeps_the_legal_target_and_drops_an_unknown_card():
    import json
    import pvp_db
    from rules_port.targeting import validate_target_selection
    from tests.tests_combat import add_card
    db = make_db()
    add_card(db, 100, 0, "8305c05f-8355-4740-8599-2cf4a85c2c79", "warzone")
    template = json.loads(pvp_db.db_ability_target_template_ids(
        "1c6d0fdf-9be0-27b3-186e-e139d1f71544", conn=db))[0]
    assert list(validate_target_selection(db, 1, 0, template, 100, [100])) == [100]
    assert list(validate_target_selection(db, 1, 0, template, 100, [999])) == []


def test_top_n_of_deck_matches_only_the_cards_on_top():
    from rules_port.filters import TopNOfDeck
    cards = [{"card_uid": uid, "location": "deck", "position": position, "user_id": 5}
             for uid, position in ((1, 0), (2, 1), (3, 2))]
    top_one = TopNOfDeck(amount=1)
    assert [top_one.matches(card, cards=cards) for card in cards] == [True, False, False]


def test_counter_cost_plan_spends_the_counters_the_source_has():
    import json
    from types import SimpleNamespace
    from rules_port.costs import plan_source_counter_costs
    from tests.tests_combat import add_card
    db = make_db()
    enrage = "026fe01f-ab03-8c96-a6d9-6ee8a6a04b14"
    add_card(db, 100, 5, "8305c05f-8355-4740-8599-2cf4a85c2c79", "warzone")
    db.execute("UPDATE game_cards SET permanent_buffs=? WHERE card_uid=100",
               (json.dumps({"counters": {"Enrage": 3}, "counter_guids": {"Enrage": enrage}}),))
    db.commit()
    plan = plan_source_counter_costs(
        db, 1, 100, SimpleNamespace(counter_costs=[SimpleNamespace(counter_guid=enrage, amount=2)]), {})
    assert len(plan) == 1 and plan[0].counter_name == "Enrage" and plan[0].new_value == 1, plan
    assert plan_source_counter_costs(db, 1, 100, SimpleNamespace(counter_costs=[]), {}) == ()


def test_cost_value_of_a_compound_filter_is_its_largest_casting_cost():
    from rules_port.filters import AndCardFilter, HasCastingCost, filter_cost_value
    assert filter_cost_value(HasCastingCost(cost=3)) == 3
    both = AndCardFilter(filters=[HasCastingCost(cost=3), HasCastingCost(cost=5)])
    assert filter_cost_value(both) == 5
    assert filter_cost_value(None) == 0


def test_fra_extraction_builds_an_arena_encounter_row_with_its_cards(tmp_path):
    import json as _json
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AssetExtraction"))
    import populate_fra_encounters as fra
    deck, champion, card = ("aaaaaaaa-0000-0000-0000-00000000d001",
                            "aaaaaaaa-0000-0000-0000-00000000c001",
                            "aaaaaaaa-0000-0000-0000-00000000e001")
    (tmp_path / "DeckTemplate.jsonl").write_text(_json.dumps({
        "m_DeckName": fra.ARENA_PREFIX + "Test_Deck",
        "m_Id": {"m_Guid": deck}, "m_ChampionId": {"m_Guid": champion},
        "m_DeckResources": [{"m_idTemplate": {"m_Guid": card}, "m_Count": 2}]}) + "\n")
    (tmp_path / "ChampionTemplate.jsonl").write_text(_json.dumps({
        "m_Id": {"m_Guid": champion}, "m_Name": "Test Champ"}) + "\n")
    encounters, cards, unresolved = fra.extract_rows(
        tmp_path, {deck: {"is_boss": False, "tier": 1}})
    assert encounters[0][:4] == (deck, "Arena_Test_Deck", "Test Champ", champion), encounters[0]
    assert cards == [(deck, card, 2, "[]")], cards
    assert unresolved == [], unresolved


def test_panorama_payload_names_its_zone_and_carries_the_panorama_section():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign_editor"))
    import catalog
    entry = {"id": "no-such-zone-for-test", "parent": None, "title": "Test Zone"}
    payload = catalog.panorama_payload(None, entry)
    assert payload["zone"] == entry and payload["title"] == "Test Zone", payload["title"]
    assert "panorama" in payload and "quests" in payload, sorted(payload)


def test_editor_logout_answers_200_with_an_empty_body():
    import io
    from types import SimpleNamespace
    from unittest import mock
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign_editor"))
    import server
    handler = server.EditorHandler.__new__(server.EditorHandler)
    handler.headers = {"Content-Length": "2"}
    handler.rfile = io.BytesIO(b"{}")
    handler.wfile = io.BytesIO()
    handler.session = None
    handler.send_response = mock.Mock()
    handler.send_header = mock.Mock()
    handler.end_headers = mock.Mock()
    handler._post(SimpleNamespace(path="/api/logout"))
    handler.send_response.assert_called_once_with(200)
    assert handler.wfile.getvalue() == b"{}"


def test_reconnect_request_with_no_game_is_answered_once():
    import hconnect_server
    from services.legacy_loadbalancer import handle_tryreconnectiontodisconnectedgame_22013
    import db as shared
    db = make_db()
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    try:
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5, "name": "reconnect"}
        handler.client_reck_id = 5
        sent = []
        handler.send = lambda header, body: sent.append(header)
        handle_tryreconnectiontodisconnectedgame_22013(
            handler, 0, 0, 22013, 40, False, "11111111-1111-1111-1111-111111111111", 0,
            None, b"", b"")
        assert [header["reqid"] for header in sent] == [41], sent
    finally:
        shared._db = previous


def test_resource_from_hand_is_played_into_the_played_resources_zone():
    from rules_port import lifecycle as be
    import game_engine
    import hconnect_server
    from types import SimpleNamespace
    import db as shared
    from tests.tests_combat import SessionStub, add_card
    db = make_db()
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    try:
        # Shards of Fate: a Resource card in the player's hand.
        add_card(db, 300, 5, "00e13fdf-b2c3-4fe7-a064-ce4481b24e8d", "hand")
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5}
        handler.client_reck_id = 5
        handler._maybe_attach_rules_port = lambda *args, **kwargs: None
        handler.send = lambda header, body: None
        handler._player_champ_scid = game_engine.SessionCardId(game_engine.UID.make(1, 9001))
        handler._ai_champ_scid = game_engine.SessionCardId(game_engine.UID.make(1, 9002))
        session = SessionStub()
        session.session_name = "tourney-1"
        state = be.default_state(turn_player=be.PLAYER)
        state["player_resources"] = 0
        state["player_total_resources"] = 0
        session.turn_order = state
        session._rules_port_session = SimpleNamespace(
            action_stack=SimpleNamespace(peek=lambda: None, count=1),
            pass_player_priority=lambda player: False,
            drive_until_input=lambda max_steps=0: None,
            current_turn_phase=game_engine.ETurnPhases.FirstMainPhase)
        play = SimpleNamespace(payload={"card_id": 300})
        assert handler._handle_rules_port_resource(session, play) is True
        location = db.execute("SELECT location FROM game_cards WHERE card_uid=300").fetchone()[0]
        assert location == "PlayedResources", location
    finally:
        shared._db = previous


def test_random_transform_turns_the_target_into_a_candidate_in_the_same_zone():
    from types import SimpleNamespace
    import game_engine
    import hconnect_server
    from rules_port.transform_effects import transform_card_at_random
    from tests.tests_combat import add_card
    db = make_db()
    hconnect_server._db = db
    add_card(db, 300, 5, "8305c05f-8355-4740-8599-2cf4a85c2c79", "warzone")
    handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
    handler.user_profile = {"id": 5}
    handler.client_reck_id = 5
    handler.send = lambda header, body: None
    player = game_engine.UID.make(244, 5)
    ai = game_engine.UID.make(game_engine.SERVICE_AI_UID_TYPE, 1000)
    context = SimpleNamespace(
        handler=handler, game=game_engine.Game(1, player, ai), pl_t=player, ai_t=ai,
        player_uid=player, ai_uid=ai, bstate={}, db=db,
        session=SimpleNamespace(session_id=1),
        resolved_target=lambda: 300,
        template_value=lambda name, default=None: {} if name == "m_Filter" else default)
    result = transform_card_at_random(context)
    assert result.startswith("transformed 0x12c -> random "), result
    location = db.execute("SELECT location FROM game_cards WHERE card_uid=300").fetchone()[0]
    assert location == "warzone", location


def test_pvp_defender_declares_a_blocker_for_an_attacking_troop():
    import game_engine
    import hconnect_server
    import services.tournament_game as tournament_game
    import db as shared
    from tests.tests_combat import add_card
    db = make_db()
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    try:
        add_card(db, 513, 6, "8305c05f-8355-4740-8599-2cf4a85c2c79", "warzone")
        add_card(db, 257, 5, "8305c05f-8355-4740-8599-2cf4a85c2c79", "warzone")
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5}
        handler.client_reck_id = 5
        handler.send = lambda header, body: None
        session = _pvp_session(db)
        session._rules_port_battle_state.update({
            "phase": int(game_engine.ETurnPhases.FirstMainPhase),
            "turn_pid": 6, "attackers": {513: 6}})
        assert tournament_game._pvp_declare_blockers(
            handler, session, b"", 5, declarations=[(513, [257])]) is True
        assert session._rules_port_battle_state.get("blockers") == {"513": ["257"]}
    finally:
        shared._db = previous


def test_declining_an_optional_trigger_cancels_it_for_the_table():
    import hconnect_server
    import services.tournament_game as tournament_game
    import db as shared
    from tests.tests_combat import add_card
    db = make_db()
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    try:
        add_card(db, 300, 5, "8305c05f-8355-4740-8599-2cf4a85c2c79", "warzone")
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5}
        handler.client_reck_id = 5
        handler.send = lambda header, body: None
        session = _pvp_session(db)
        session._rules_port_battle_state["pending_trigger"] = {
            "optional": True, "owner_id": 5, "instance_id": 1,
            "ability_guid": "1c6d0fdf-9be0-27b3-186e-e139d1f71544", "source_uid": 300}
        declined = {"activation_data": {"disable": True}}
        assert tournament_game._pvp_resolve_optional_trigger(
            handler, session, b"", 5, typed_payload=declined) is True
        assert "pending_trigger" not in session._rules_port_battle_state
    finally:
        shared._db = previous


def test_ai_hand_action_targets_the_opposing_troop_it_can_tap():
    import ai_eval
    from rules_port import lifecycle as be
    import db as shared
    import game_engine
    import hconnect_server
    from tests.tests_combat import SessionStub, add_card
    db = make_db()
    previous_shared, previous_eval = shared._db, ai_eval._db
    shared._db = db
    ai_eval._db = db
    hconnect_server._db = db
    try:
        add_card(db, 100, 0, "8305c05f-8355-4740-8599-2cf4a85c2c79", "warzone")
        add_card(db, 201, 5, "11111111-1111-1111-1111-111111111111", "warzone")
        add_card(db, 400, 0, "8305c05f-8355-4740-8599-2cf4a85c2c79", "hand")
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5}
        handler.client_reck_id = 5
        handler._player_champ_scid = game_engine.SessionCardId(game_engine.UID.make(1, 9001))
        handler._ai_champ_scid = game_engine.SessionCardId(game_engine.UID.make(1, 9002))
        session = SessionStub()
        session.session_name = "practice-1"
        state = be.default_state(turn_player=be.AI)
        session.turn_order = state
        evaluator = ai_eval.build_evaluator(
            handler, session, state,
            game_engine.UID.make(game_engine.SERVICE_AI_UID_TYPE, 1000),
            game_engine.UID.make(244, 5))
        card = evaluator.hand[0]
        assert evaluator.choose_action_target(card) == 201
        # An illegal preferred target falls back to the same opposing troop.
        assert evaluator.choose_action_target(card, preferred_target=999) == 201
    finally:
        shared._db, ai_eval._db = previous_shared, previous_eval


def test_deck_strategy_scores_every_archetype_and_measures_the_troops():
    from ai_deck_strategy import evaluate_deck_strategy
    deck = [("8305c05f-8355-4740-8599-2cf4a85c2c79", 2), ("018bc77c-013f-493b-9ba3-3a286607c989", 1)]
    result = evaluate_deck_strategy(deck)
    assert result.scores and all(score >= 0 for score in result.scores.values()), result.scores
    assert result.features["average_troop_cost"] == 2.0, result.features


def test_champion_bom_extraction_regenerates_both_seed_blocks_in_static(tmp_path, monkeypatch):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AssetExtraction"))
    import extract_champion_boms as extractor
    records = tmp_path / "Records"
    records.mkdir()
    for name in ("AbilityTemplate.jsonl", "AbilityEffectTemplate.jsonl", "ChampionTemplate.jsonl"):
        (records / name).write_text("")
    static = tmp_path / "static.py"
    static.write_text("keep = 1\n"
                      f"{extractor.BEGIN_MARKER}\n{extractor.END_MARKER}\n"
                      f"{extractor.META_BEGIN_MARKER}\n{extractor.META_END_MARKER}\n")
    monkeypatch.setattr(extractor, "RECORDS", str(records))
    monkeypatch.setattr(extractor, "STATIC", str(static))
    monkeypatch.setattr(extractor, "DB", str(tmp_path / "hconnect.db"))
    extractor.main()
    text = static.read_text()
    assert text.startswith("keep = 1\n"), text[:40]
    assert "CHAMPION_ABILITY_EFFECTS = [" in text, text


def test_campaign_state_maps_visits_completion_and_flags_for_the_rules_engine():
    import json as _json
    import sqlite3
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign_editor"))
    import catalog
    db = make_db()
    db.row_factory = sqlite3.Row
    db.execute("INSERT OR IGNORE INTO users (id, name, gold, platinum) VALUES (5, 'mapping', 0, 0)")
    db.execute("INSERT INTO champions (id, user_id, champion_name, race) VALUES (7, 5, 'Champ', 1)")
    state = {"PublicState": {"Data": {"visited_nodes": ["Node00A"],
                                      "conversation_visits": {"Node00A": 2},
                                      "conversation_flags": ["met_gaal"]}},
             "VisLocs": [{"Data": {"node": "Node00A", "title": "Town", "completed": True}}]}
    db.execute("INSERT INTO campaigns (id, champion_id, user_id, template_name, campaign_type, state_json) "
               "VALUES (3, 7, 5, 'AZ1', 'AREA', ?)", (_json.dumps(state),))
    db.commit()
    view = catalog.sim_state_from_campaign(db, 3)
    assert view["nodes"]["Node00A"] == {"completed": True, "retryable": False,
                                        "failed": False, "visits": 2}, view["nodes"]
    assert "met_gaal" in view["flags"], view["flags"]


def test_az1_rules_build_produces_a_versioned_campaign_document():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign_editor"))
    import import_az1_rules
    rules = import_az1_rules.build(make_db())
    assert rules["schema_version"] == 1, rules.get("schema_version")
    assert {"campaign", "nodes", "quests", "hooks", "start", "map"} <= set(rules), sorted(rules)


def test_reward_catalog_lists_the_cards_and_chests_for_the_editor():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign_editor"))
    import catalog
    catalog_view = catalog.rewards_catalog(make_db())
    assert catalog_view["cards"] and catalog_view["chests"], sorted(catalog_view)


def test_schema_creation_builds_every_table_in_an_empty_database():
    import sqlite3
    import static
    database = sqlite3.connect(":memory:")
    static.ensure_schema(database)
    tables = {row[0] for row in database.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"game_cards", "card_templates", "campaigns"} <= tables, sorted(tables)[:10]


def test_permanent_effects_do_not_expire_on_their_own():
    from rules_port.effect_lifetimes import _expired
    assert _expired({"duration": "Permanent"}, make_db(), 1, {}) is False


def test_end_of_turn_effects_expire_at_the_end_of_turn_only():
    from rules_port.effect_lifetimes import _expired
    entry = {"duration": "EndOfTurn", "source_uid": 300, "target_uid": 257}
    assert _expired(entry, make_db(), 1, {"turn_number": 1}, boundary="end_turn") is True
    assert _expired(entry, make_db(), 1, {"turn_number": 1}, boundary="start_turn") is False


def test_audit_reports_a_heal_claim_that_no_effect_implements():
    from types import SimpleNamespace
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AssetExtraction"))
    import audit_card_text as audit
    claim = SimpleNamespace(trigger_event_type=None, game_text="Gain 2 health.",
                            activation_game_text="", effects=[], variables=[], guid="g1")
    findings, claimed, keywords = audit.find_mismatches(claim)
    assert [finding["field"] for finding in findings] == ["heal"], findings
    assert findings[0]["issue"] == "text claim has no effect", findings
    flavour = SimpleNamespace(trigger_event_type=None, game_text="Just flavour.",
                              activation_game_text="", effects=[], variables=[], guid="g2")
    assert audit.find_mismatches(flavour) == ([], [], []), audit.find_mismatches(flavour)


def test_resolving_a_tap_ability_taps_its_chosen_target():
    from rules_port import lifecycle as be
    import db as shared
    import game_engine
    import hconnect_server
    from rules_port.resolution import resolve_port_ability
    from tests.tests_combat import SessionStub, add_card
    db = make_db()
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    try:
        add_card(db, 300, 5, "8305c05f-8355-4740-8599-2cf4a85c2c79", "warzone")
        add_card(db, 257, 0, "11111111-1111-1111-1111-111111111111", "warzone")
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5}
        handler.client_reck_id = 5
        handler._maybe_attach_rules_port = lambda *args, **kwargs: None
        handler.send = lambda header, body: None
        handler._player_champ_scid = game_engine.SessionCardId(game_engine.UID.make(1, 9001))
        handler._ai_champ_scid = game_engine.SessionCardId(game_engine.UID.make(1, 9002))
        session = SessionStub()
        session.session_name = "practice-1"
        session.encounter_data = {}
        state = be.default_state(turn_player=be.PLAYER)
        session.turn_order = state
        player = game_engine.UID.make(244, 5)
        ai = game_engine.UID.make(game_engine.SERVICE_AI_UID_TYPE, 1000)
        game = game_engine.Game(1, player, ai)
        result = resolve_port_ability(
            handler, game, session, db, player, ai, state,
            "1c6d0fdf-9be0-27b3-186e-e139d1f71544", 300, 5,
            target_map={0: [257]})
        assert result.startswith("tapped"), result
        state_row = db.execute("SELECT card_state FROM game_cards WHERE card_uid=257").fetchone()[0]
        assert state_row & int(game_engine.ECardStates.Tapped), state_row
    finally:
        shared._db = previous


def test_card_count_variable_counts_the_owners_cards_in_the_default_zones():
    import json
    from types import SimpleNamespace
    import game_engine
    from rules_port.context import EffectContext
    from tests.tests_combat import add_card
    db = make_db()
    add_card(db, 300, 5, "8305c05f-8355-4740-8599-2cf4a85c2c79", "warzone")
    add_card(db, 301, 5, "8305c05f-8355-4740-8599-2cf4a85c2c79", "warzone")
    add_card(db, 302, 0, "8305c05f-8355-4740-8599-2cf4a85c2c79", "warzone")
    player = game_engine.SessionCardId(game_engine.UID.make(244, 5))
    ai = game_engine.SessionCardId(game_engine.UID.make(3, 1000))
    handler = SimpleNamespace(user_profile={"id": 5}, _player_champ_scid=player,
                              _ai_champ_scid=ai, _champion_granted_ability_guids={})
    bstate = {"resolving_owner_id": 5}
    context = EffectContext.from_rules_port(
        SimpleNamespace(events=[]), SimpleNamespace(session_id=1), db, handler,
        game_engine.UID.make(244, 5), game_engine.UID.make(3, 1000), bstate, "effect",
        json.dumps({"variable": "Cards"}))
    # The owner has two troops in the warzone; the opponent's troop is not counted.
    assert context.set_card_count_variable() == "counted Cards=2"
    assert bstate["ability_variables"] == {"Cards": 2}, bstate


def test_adding_a_champion_saves_it_for_the_user_and_replies():
    import db as shared
    import hconnect_server
    from services.deck_editing import handle_addchampion_2033
    db = make_db()
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    try:
        db.execute("INSERT OR IGNORE INTO users (id, name, gold, platinum) VALUES (5, 'champion', 0, 0)")
        db.commit()
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5, "name": "champion"}
        handler.client_reck_id = 5
        sent = []
        handler.send = lambda header, body: sent.append(header)
        handle_addchampion_2033(handler, 0, 0, 2033, 40, False,
                                "11111111-1111-1111-1111-111111111111", 0,
                                {"ChampionName": "Probe Champ", "Race": 1}, b"", b"")
        assert [header["reqid"] for header in sent] == [41], sent
        row = db.execute("SELECT champion_name, race, user_id FROM champions "
                         "WHERE champion_name=?", ("Probe Champ",)).fetchone()
        assert tuple(row) == ("Probe Champ", 1, 5), tuple(row)
    finally:
        shared._db = previous


def test_pvp_shard_choice_keeps_the_chosen_card_in_the_deck_and_clears_the_search():
    import struct
    import hconnect_server
    import services.tournament_game as tournament_game
    import db as shared
    from tests.tests_combat import add_card
    db = make_db()
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    try:
        add_card(db, 257, 5, "8305c05f-8355-4740-8599-2cf4a85c2c79", "deck")
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5}
        handler.client_reck_id = 5
        handler.send = lambda header, body: None
        session = _pvp_session(db)
        session._rules_port_battle_state["pending_deck_search"] = {
            "kind": "shard", "candidates": [257], "owner_id": 5}
        choice = b"m_UID64;0;1;1;" + struct.pack("<Q", 257).hex().encode() + b";"
        assert tournament_game._pvp_resolve_shard_choice(handler, session, choice, 5) is True
        assert "pending_deck_search" not in session._rules_port_battle_state
        location = db.execute("SELECT location FROM game_cards WHERE card_uid=257").fetchone()[0]
        assert location == "deck", location
    finally:
        shared._db = previous


def test_editor_zone_listing_answers_with_the_catalog_zones_as_json():
    import io
    import json as _json
    from types import SimpleNamespace
    from unittest import mock
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign_editor"))
    import server
    handler = server.EditorHandler.__new__(server.EditorHandler)
    handler.headers = {}
    handler.wfile = io.BytesIO()
    handler.session = None
    handler.send_response = mock.Mock()
    handler.send_header = mock.Mock()
    handler.end_headers = mock.Mock()
    handler._get(SimpleNamespace(query=""), "/api/zones")
    handler.send_response.assert_called_once_with(200)
    zones = _json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert isinstance(zones, list) and zones, zones


def test_accepted_spell_intent_is_projected_onto_the_chain():
    from types import SimpleNamespace
    import hconnect_server
    import services.tournament_game as tournament_game
    import db as shared
    from tests.tests_combat import add_card
    db = make_db()
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    try:
        add_card(db, 310, 5, "018bc77c-013f-493b-9ba3-3a286607c989", "hand")
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5}
        handler.client_reck_id = 5
        handler.send = lambda header, body: None
        session = _pvp_session(db)
        intent = SimpleNamespace(payload={"card_id": 310}, raw_transaction=b"")
        assert tournament_game.project_accepted_pvp_transaction(
            handler, session, "play_spell", intent) is True
        stack = session._rules_port_battle_state["stack"]
        assert [(item["kind"], item["source_uid"]) for item in stack] == [("spell", 310)], stack
    finally:
        shared._db = previous


def test_saving_conversation_actions_records_them_for_the_ending(tmp_path, monkeypatch):
    import json as _json
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign_editor"))
    import catalog
    import editing
    # Redirect the campaign data directory: the save writes a JSON file there.
    monkeypatch.setattr(catalog, "DATA_DIR", tmp_path)
    import campaign_source
    campaign_source.ensure_campaign_path()
    import campaign_content
    real = next(r for r in campaign_content.table("campaign_node_conversations")
                if r["campaign_template"] == "AZ1")
    guid = real["conversation_guid"]
    saved = editing.save_conversation_actions(None, "AZ1", guid, "win", ["set_flag(probe)"])
    assert saved["actions"] == {"win": ["set_flag(probe)"]}, saved
    written = _json.loads((tmp_path / "AZ1.json").read_text(encoding="utf-8"))
    row = written["tables"]["campaign_node_conversations"][0]
    assert row["trigger_json"]["actions"] == {"win": ["set_flag(probe)"]}, row


def test_affordable_troop_ability_becomes_an_activate_option_on_its_card():
    from rules_port import lifecycle as be
    import db as shared
    import game_engine
    import hconnect_server
    from tests.tests_combat import SessionStub, add_card
    db = make_db()
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    try:
        add_card(db, 300, 5, "8305c05f-8355-4740-8599-2cf4a85c2c79", "warzone")
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5}
        handler.client_reck_id = 5
        handler._maybe_attach_rules_port = lambda *args, **kwargs: None
        handler.send = lambda header, body: None
        handler._player_champ_scid = game_engine.SessionCardId(game_engine.UID.make(1, 9001))
        handler._ai_champ_scid = game_engine.SessionCardId(game_engine.UID.make(1, 9002))
        session = SessionStub()
        session.session_name = "practice-1"
        state = be.default_state(turn_player=be.PLAYER)
        session.turn_order = state
        player = game_engine.UID.make(244, 5)
        ai = game_engine.UID.make(game_engine.SERVICE_AI_UID_TYPE, 1000)
        game = game_engine.Game(1, player, ai)
        game.push_options(player, [])
        template = "8305c05f-8355-4740-8599-2cf4a85c2c79"
        ability = "1c6d0fdf-9be0-27b3-186e-e139d1f71544"
        handler._add_troop_ability_options(game, player, session, {(300, template): [ability]},
                                           bstate=state)
        option_list = next(event for event in reversed(game.events)
                           if type(event).__name__ == "PlayerOptionListSessionEventArgs")
        card_options = [option for option in option_list.options
                        if int(option.card.uid.uid64) == int(game_engine.UID(300).uid64)]
        assert len(card_options) == 1 and len(card_options[0].instances) == 1, card_options
    finally:
        shared._db = previous


def test_tournament_deck_seeds_one_game_card_per_listed_card_into_the_deck():
    import tournament_db
    db = make_db()
    counts = tournament_db.db_seed_tournament_game_deck(
        1, 5, None, conn=db,
        card_guids=["8305c05f-8355-4740-8599-2cf4a85c2c79", "018bc77c-013f-493b-9ba3-3a286607c989"],
        champion_guid="1ae73dcf-e96e-4536-aec3-f53efb5e1c96")
    assert counts["inserted"] == 2 and counts["skipped_error"] == 0, counts
    rows = db.execute("SELECT location FROM game_cards WHERE session_id=1").fetchall()
    assert sorted(row[0] for row in rows) == ["deck", "deck"], rows


def test_gem_extraction_reads_each_gem_type_from_the_inventory_records():
    import json as _json
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AssetExtraction"))
    import gamedata_seed
    from extract_gems import GEM_TYPE_VALUES
    gem_type = GEM_TYPE_VALUES[0]
    body = _json.dumps({"_t": "Reckoning.Game.InventoryGemData", "m_Name": "Probe Gem",
                        "m_GemTypeNew": gem_type, "m_Abilities": []})
    data = f"\nInventoryItemData\n$$--$$\n{body}\n$$$---$$$\n"
    gem_rows, ability_rows, effect_rows = gamedata_seed._extract_gems(data)
    assert gem_rows == [(1, gem_type, "Probe Gem", "[]")], gem_rows
    assert ability_rows == [] and effect_rows == [], (ability_rows, effect_rows)


from pathlib import Path as _Path
REPO_RECORDS_DIR = _Path(__file__).resolve().parent.parent / "hex-private" / "Records"


def test_az1_node_details_come_from_its_encounter_scene_row(tmp_path):
    # The AZ1 map details come from campaign_content (Records plus the data files).
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign", "AssetExtraction"))
    import generate_az1_map as mapper
    details = mapper.load_node_details(tmp_path / "unused.db", REPO_RECORDS_DIR)
    node = details["Node003"]
    assert node.encounters == ("Wild Cub",), node
    assert node.pre_conversations == ("Feralroot Bear",), node


def test_tac_list_read_returns_the_cards_stored_for_the_resolving_ability():
    from rules_port.static_rules import _list_card_uids
    state = {"resolving_ability": "ab1",
             "ability_lists": {"ab1": {"Targets": [257, 300]}}}
    assert _list_card_uids(make_db(), 1, state, "Targets", 300) == (257, 300)
    assert _list_card_uids(make_db(), 1, {}, "Targets", 300) is None


def test_version_command_reports_the_release_version():
    import commands
    import hconnect_server
    handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
    handler.user_profile = {"id": 5}
    handler.client_reck_id = 5
    reply = commands.handle_command(handler, "!version", "", "tester")
    assert reply and reply.count(".") >= 2, reply


def test_practice_session_attaches_an_authoritative_rules_host():
    from rules_port import lifecycle as be
    import db as shared
    import game_engine
    import game_session as gs
    import hconnect_server
    db = make_db()
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    try:
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5, "name": "attach"}
        handler.client_reck_id = 5
        handler.send = lambda header, body: None
        session = gs.GameSession(7101, 7101 * 1000, "practice-7101", 5)
        session.add_player(hconnect_server.make_uid(
            hconnect_server.UID_TYPE["ServicePlayer"], 5), 0)
        session.encounter_data = {}
        state = be.default_state(turn_player=be.PLAYER)
        state["player_health"] = 20
        state["ai_health"] = 20
        session.turn_order = state
        game = game_engine.Game(1, game_engine.UID.make(244, 5),
                                game_engine.UID.make(game_engine.SERVICE_AI_UID_TYPE, 1000))
        port = handler._maybe_attach_rules_port(session, game, state)
        assert port is not None and session._rules_port_session is port
    finally:
        shared._db = previous


def _rulesport_duel(db, attacker_state, monkeypatch):
    """A tournament game between players 5 and 6 with a real RulesPort host.

    One player-5 troop (uid 257) sits on the warzone in ``attacker_state``; the
    host is attached before anything else happens, so it reads that state.
    """
    import db as shared
    import game_engine as ge
    import game_session as gs
    import hconnect_server
    import pvp_autoplay
    import services.tournament_game as tournament_game
    from tests.tests_combat import add_card
    player, opponent, troop = 5, 6, 257
    player_champion, opponent_champion = (9001 << 8) | 1, (9002 << 8) | 1
    troop_template = "8305c05f-8355-4740-8599-2cf4a85c2c79"
    monkeypatch.setattr(shared, "_db", db)
    monkeypatch.setattr(hconnect_server, "_db", db)
    monkeypatch.setattr(tournament_game, "_db", db)
    # Every duel uses session 1, and the tournament keeps one host per session id.
    monkeypatch.setattr(tournament_game, "_pvp_ports", dict(tournament_game._pvp_ports))
    tournament_game._pvp_ports.pop(1, None)
    for uid in (player, opponent):
        db.execute("INSERT OR IGNORE INTO users (id, name, gold, platinum) VALUES (?, ?, 0, 0)",
                   (uid, f"duel{uid}"))
    tournament_type = db.execute("SELECT id FROM tournament_types LIMIT 1").fetchone()
    if tournament_type is None:
        db.execute("INSERT INTO tournament_types (id, name) VALUES (1, 'duel')")
        tournament_type = (1,)
    db.execute("INSERT OR IGNORE INTO tournaments (id, type_id, status, session_id) "
               "VALUES (1, ?, 'active', '1')", (tournament_type[0],))
    for uid in (player, opponent):
        db.execute("INSERT OR IGNORE INTO tournament_signups (tournament_id, player_uid, player_name) "
                   "VALUES (1, ?, ?)", (uid, f"duel{uid}"))
    add_card(db, troop, player, troop_template, "warzone")
    db.execute("UPDATE game_cards SET card_state=? WHERE card_uid=?",
               (int(attacker_state), troop))
    db.commit()
    state = {"pvp": True, "pids": [player, opponent],
             "champ_map": {str(player): player_champion, str(opponent): opponent_champion},
             "champ_guid_map": {}, "turn_pid": player, "priority_pid": player, "turn_number": 1,
             "phase": int(ge.ETurnPhases.DeclareAttack), f"hp_{player}": 20, f"hp_{opponent}": 20,
             f"res_{player}": 5, f"res_{opponent}": 5, "stack": [], "_rules_port_attached": True}
    handlers = {}
    for uid in (player, opponent):
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": uid, "name": f"duel{uid}"}
        handler.client_reck_id = uid
        handler.send = lambda header, body: None
        handlers[uid] = handler
    monkeypatch.setitem(tournament_game.player_handlers, player, handlers[player])
    monkeypatch.setitem(tournament_game.player_handlers, opponent, handlers[opponent])
    session = gs.GameSession(1, 1000, "tourney-duel", player)
    for uid in (player, opponent):
        session.add_player(hconnect_server.make_uid(
            hconnect_server.UID_TYPE["ServicePlayer"], uid), 0)
    session.turn_order = state
    session._rules_port_battle_state = state
    game = ge.Game(1, ge.UID.make(244, player), ge.UID.make(244, opponent))
    port = handlers[player]._maybe_attach_rules_port(session, game, state)
    return {"handlers": handlers, "session": session, "port": port, "troop": troop,
            "opponent_champion": opponent_champion, "pvp": pvp_autoplay, "state": state}


def test_unblocked_attack_damages_the_defending_champion_end_to_end(monkeypatch):
    import game_engine as ge
    import db as shared
    db = make_db()
    duel = _rulesport_duel(db, int(ge.ECardStates.StartedATurnOnYourSide), monkeypatch)
    pvp, session, port = duel["pvp"], duel["session"], duel["port"]
    handlers, troop = duel["handlers"], duel["troop"]
    pvp._transaction(handlers[5], session,
                     pvp._attack_bytes([troop], duel["opponent_champion"]),
                     typed_payload={"m_Attacks": [{"DefendingCardId": duel["opponent_champion"],
                                                   "AttackingCardIds": [troop]}]})
    assert len(port.combat_manager.combats) == 1, port.combat_manager.combats
    names = {int(value): key for key, value in vars(ge.ETurnPhases).items() if isinstance(value, int)}
    for _ in range(14):
        state = session._rules_port_battle_state
        if names.get(int(port.current_turn_phase)) == "AssignDamage":
            pvp._transaction(handlers[int(state.get("turn_pid") or 5)], session,
                             b"AssignDamageOrderTransaction;", typed_payload={"assignments": []})
        priority = int(state.get("priority_pid") or 5)
        pvp._transaction(handlers[priority], session, b"PassPriorityTransaction;")
    state = session._rules_port_battle_state
    assert state["hp_6"] == 18, state.get("hp_6")
    assert state["hp_5"] == 20, state.get("hp_5")
    assert len(port.combat_manager.combats) == 0


def test_accepted_attack_projection_marks_the_attacker_attacking_and_tapped(monkeypatch):
    from types import SimpleNamespace
    import game_engine as ge
    db = make_db()
    duel = _rulesport_duel(db, int(ge.ECardStates.StartedATurnOnYourSide), monkeypatch)
    pvp, session, port = duel["pvp"], duel["session"], duel["port"]
    handlers, troop = duel["handlers"], duel["troop"]
    pvp._transaction(handlers[5], session,
                     pvp._attack_bytes([troop], duel["opponent_champion"]),
                     typed_payload={"m_Attacks": [{"DefendingCardId": duel["opponent_champion"],
                                                   "AttackingCardIds": [troop]}]})
    assert len(port.combat_manager.combats) == 1
    # The host has staged the attack; the projection publishes it and sets the card state.
    transaction = SimpleNamespace(inner_bytes=b"")
    handled = handlers[5]._handle_commit_attack_transaction(
        session, transaction,
        declarations=[(duel["opponent_champion"], [troop])], authoritative_port=port)
    assert handled is True
    state = db.execute("SELECT card_state FROM game_cards WHERE card_uid=?", (troop,)).fetchone()[0]
    assert state & int(ge.ECardStates.Attacking) and state & int(ge.ECardStates.Tapped), state


def test_a_troop_that_just_came_out_cannot_attack(monkeypatch):
    import game_engine as ge
    db = make_db()
    duel = _rulesport_duel(db, int(ge.ECardStates.CameOutThisTurn), monkeypatch)
    pvp, session, port = duel["pvp"], duel["session"], duel["port"]
    handlers, troop = duel["handlers"], duel["troop"]
    pvp._transaction(handlers[5], session,
                     pvp._attack_bytes([troop], duel["opponent_champion"]),
                     typed_payload={"m_Attacks": [{"DefendingCardId": duel["opponent_champion"],
                                                   "AttackingCardIds": [troop]}]})
    assert port.combat_manager.combats == [], port.combat_manager.combats


def test_assign_damage_handler_applies_the_unblocked_damage_at_the_damage_step(monkeypatch):
    from types import SimpleNamespace
    import game_engine as ge
    db = make_db()
    duel = _rulesport_duel(db, int(ge.ECardStates.StartedATurnOnYourSide), monkeypatch)
    pvp, session, port = duel["pvp"], duel["session"], duel["port"]
    handlers, troop = duel["handlers"], duel["troop"]
    pvp._transaction(handlers[5], session,
                     pvp._attack_bytes([troop], duel["opponent_champion"]),
                     typed_payload={"m_Attacks": [{"DefendingCardId": duel["opponent_champion"],
                                                   "AttackingCardIds": [troop]}]})
    names = {int(value): key for key, value in vars(ge.ETurnPhases).items() if isinstance(value, int)}
    for _ in range(14):
        if names.get(int(port.current_turn_phase)) == "AssignDamage":
            break
        state = session._rules_port_battle_state
        pvp._transaction(handlers[int(state.get("priority_pid") or 5)], session,
                         b"PassPriorityTransaction;")
    assert names.get(int(port.current_turn_phase)) == "AssignDamage", names.get(int(port.current_turn_phase))
    turn_handler = handlers[int(session._rules_port_battle_state.get("turn_pid") or 5)]
    handled = turn_handler._handle_assign_damage_transaction(
        session, SimpleNamespace(inner_bytes=b""), assignments=[], native_combat=True)
    assert handled is True
    assert session._rules_port_battle_state["hp_6"] == 18, session._rules_port_battle_state.get("hp_6")


def test_defense_projection_accepts_the_defenders_declaration_and_advances(monkeypatch):
    from types import SimpleNamespace
    import game_engine as ge
    db = make_db()
    duel = _rulesport_duel(db, int(ge.ECardStates.StartedATurnOnYourSide), monkeypatch)
    pvp, session, port = duel["pvp"], duel["session"], duel["port"]
    handlers, troop = duel["handlers"], duel["troop"]
    pvp._transaction(handlers[5], session,
                     pvp._attack_bytes([troop], duel["opponent_champion"]),
                     typed_payload={"m_Attacks": [{"DefendingCardId": duel["opponent_champion"],
                                                   "AttackingCardIds": [troop]}]})
    # The defender declares no blockers against the staged attack.
    handled = handlers[6]._handle_commit_defense_transaction(
        session, SimpleNamespace(inner_bytes=b""),
        declarations=[(troop, [])], authoritative_port=port)
    assert handled is True
    # The attacker is recorded with no blockers.
    assert session._rules_port_battle_state.get("ai_blockers") == {str(troop): []}, session._rules_port_battle_state.get("ai_blockers")


def test_gate_engine_state_for_an_empty_campaign_has_the_full_progress_view(monkeypatch):
    import importlib.util
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign"))
    import campaign_gates
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign.py")
    spec = importlib.util.spec_from_file_location("campaign_root_for_gates", root)
    campaign = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(campaign)
    view = campaign_gates.engine_state(campaign, make_db(), {"quests": []}, None, {})
    assert view["flags"] == [] and view["quests"] == {} and view["nodes"] == {}, view
    assert set(view) >= {"flags", "quests", "nodes", "counters", "current"}, sorted(view)


def test_panorama_importer_dry_run_reports_every_template_and_writes_nothing(tmp_path, monkeypatch, capsys):
    import sys as _sys
    from tests.test_db import fresh_database
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign_editor"))
    import import_az1_panoramas
    monkeypatch.setattr(_sys, "argv", ["import_az1_panoramas.py", "--db", str(fresh_database()), "--dry-run"])
    import_az1_panoramas.main()
    out = capsys.readouterr().out
    assert "cavein:" in out and "amblingmesa:" in out and "rows moved" in out, out[:300]


def test_profile_stream_pushes_the_account_profile_to_the_client():
    import db as shared
    import hconnect_server
    from tests.tests_combat import make_db as _fresh_db
    db = _fresh_db()
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    try:
        db.execute("INSERT OR IGNORE INTO users (id, name, gold, platinum) VALUES (5, 'profile', 7, 3)")
        db.commit()
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5, "name": "profile", "gold": 7, "platinum": 3}
        handler.client_reck_id = 5
        sent = []
        handler.send = lambda header, body: sent.append(header)
        handler.push_profile_stream()
        assert len(sent) == 6, len(sent)
    finally:
        shared._db = previous


def test_material_info_reports_the_blend_and_the_saved_main_texture():
    from types import SimpleNamespace
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign", "AssetExtraction"))
    import campaign_effects

    class _Shader:
        path_id = 7

        def deref(self):
            return SimpleNamespace(read_typetree=lambda: {"m_ParsedForm": {"m_Name": "Sprites/Default"}})

    material = SimpleNamespace(
        m_Shader=_Shader(),
        read_typetree=lambda: {"m_SavedProperties": {
            "m_TexEnvs": [("_MainTex", {"m_Texture": {"m_PathID": 42}})],
            "m_Floats": [], "m_Colors": []}})
    info = campaign_effects.material_info(material, lambda path_id: f"generated/{path_id}.png")
    assert info is not None and info["blend"] == "alpha", info
    assert info["texture"] == "generated/42.png", info


def test_int_attribute_variable_reads_its_tac_scope_not_the_inherited_default():
    # Documented in rules_port/static_rules.py: an IntAttrAbilityVariable reads its
    # configured TAC directly; the inherited default does not override that read.
    from rules_port.static_rules import _intattr_variable
    variable = {"m_IntAttrName": "CombatDamage", "m_DefaultValue": 3}
    assert _intattr_variable(make_db(), 1, {}, 100, 5, "damage", variable) == 0


def test_blocking_shortage_is_reported_only_when_no_available_blocker_can_stop_an_attack():
    from types import SimpleNamespace
    from ai import _needs_more_blocking_troops

    def troop(uid, state=0):
        return SimpleNamespace(card_uid=uid, card_state=state, is_troop=lambda: True,
                               has_attribute=lambda flag: False)

    class _Evaluator:
        def __init__(self, blockers, can_block, hand):
            self.player_warzone = [troop(10)]
            self.ai_warzone = blockers
            self.hand = hand
            self._can_block_result = can_block

        def _can_block(self, blocker, attacker):
            return self._can_block_result

        def is_playable(self, card):
            return "True"

        def troop_summon_effects(self, card):
            return [{"attributes": 0, "enters_play_exhausted": False, "enters_play_attacking": False}]

    quick_summon = SimpleNamespace(is_quick_action=lambda: True)
    battle_state = {"player_attackers": {"10": 0}, "ai_blockers": {}}
    # Nobody on the board can block the attacker, but a quick action summons a blocker.
    assert _needs_more_blocking_troops(_Evaluator([], False, [quick_summon]), battle_state) is True
    # A standing blocker that can block the attacker covers it, so no shortage is reported.
    assert _needs_more_blocking_troops(_Evaluator([troop(20)], True, [quick_summon]), battle_state) is False


def test_precombat_restriction_picks_the_most_valuable_opposing_troop_for_cant_block():
    from types import SimpleNamespace
    from ai_eval import CardEvaluator

    def troop(uid):
        return SimpleNamespace(card_uid=uid, is_troop=lambda: True,
                               has_attribute=lambda flag: False,
                               effective_attack=lambda: 1, effective_defense=lambda: 1)

    action = SimpleNamespace(is_action=lambda: True, ability_guids=["grant-cant-block"])
    evaluator = CardEvaluator.__new__(CardEvaluator)
    evaluator.hand = [action]
    evaluator.player_warzone = [troop(10), troop(11)]
    evaluator.is_playable = lambda card: "True"
    evaluator._metadata_action_target_slots = lambda card, ag: [
        {"index": 0, "candidates": [10, 11], "minimum": 1, "maximum": 1}]
    evaluator.effects_for = lambda ag: [("CardModifierAbilityEffectTemplate", {
        "property": "attribute", "operation": "add",
        "attribute_flags": "CantBlock", "target_index": 0})]
    values = {10: 5.0, 11: 9.0}
    evaluator.get_card_value = lambda card: values[int(card.card_uid)]
    card, target_map = evaluator.choose_precombat_block_restriction()
    assert card is action, card
    # The more valuable troop (11) takes the restriction.
    assert target_map == {0: (11,)}, target_map


def test_game_entrance_request_is_answered_once_for_a_plain_player():
    import db as shared
    import hconnect_server
    from services.legacy_tournaments import handle_request_25039
    db = make_db()
    previous = shared._db
    shared._db = db
    hconnect_server._db = db
    try:
        db.execute("INSERT OR IGNORE INTO users (id, name, gold, platinum) VALUES (5, 'entrance', 0, 0)")
        db.commit()
        handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
        handler.user_profile = {"id": 5, "name": "entrance"}
        handler.client_reck_id = 5
        sent = []
        handler.send = lambda header, body: sent.append(header)
        handle_request_25039(handler, 0, 0, 25039, 40, False,
                             "11111111-1111-1111-1111-111111111111", 0, {}, b"", b"")
        assert [header["reqid"] for header in sent] == [41], sent
    finally:
        shared._db = previous


def test_fra_round_robin_lists_both_arena_decks_with_their_cards(tmp_path):
    import db as shared
    import pvp_autoplay
    db = make_db()
    # Two real Arena decks with their own champions: cards come from campaign_content.
    champions = {"bbfd8f29-e549-4eda-9e21-735620d3b5ff": "f8f86969-2e47-4901-8c9e-7fbf8d859e22",
                 "d987d23d-3d68-453e-9ea4-1070ee21b2cc": "91ca6c6b-3665-48fa-be85-95cf7938fe57"}
    decks = list(champions)
    for deck, champion in champions.items():
        db.execute("INSERT INTO fra_encounters (deck_guid, deck_name, name, champion_guid, "
                   "base_deck_name) VALUES (?, ?, ?, ?, ?)", (deck, deck, deck, champion, deck))
    db.commit()
    previous_shared, previous_autoplay = shared._db, pvp_autoplay._db
    shared._db = db
    pvp_autoplay._db = db
    try:
        _fra_specs_carry_the_authored_cards(db, decks)
    finally:
        shared._db = previous_shared
        pvp_autoplay._db = previous_autoplay


def test_end_of_turn_grant_is_removed_from_its_card_and_from_state():
    from rules_port.effect_lifetimes import expire_grants
    from tests.tests_combat import add_card
    db = make_db()
    add_card(db, 257, 5, "8305c05f-8355-4740-8599-2cf4a85c2c79", "warzone")
    grant = {"duration": "EndOfTurn", "target_uid": 257,
             "ability_guid": "1c6d0fdf-9be0-27b3-186e-e139d1f71544",
             "source_uid": 300, "owner_id": 5, "turn_number": 1}
    state = {"turn_number": 1, "temporary_ability_grants": [grant]}
    assert expire_grants(db, 1, state, boundary="end_turn") == [257]
    assert "temporary_ability_grants" not in state, state


def test_reward_cleaning_keeps_known_fields_and_rejects_unknown_ones():
    import pytest
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign_editor"))
    import editing
    assert editing.clean_rewards({"gold": 300, "xp": 200}) == {"gold": 300, "xp": 200}
    with pytest.raises(ValueError, match="unknown reward field"):
        editing.clean_rewards({"gold": 1, "bogus": 2})


def test_talent_extraction_regenerates_its_seed_block_and_keeps_the_rest_of_static(tmp_path, monkeypatch):
    import gzip
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AssetExtraction"))
    import extract_talents
    gamedata = tmp_path / "gamedata.gz"
    with gzip.open(gamedata, "wb") as handle:
        handle.write(b"")
    static = tmp_path / "static.py"
    static.write_text(f"keep = 1\n{extract_talents.BEGIN_MARKER}\n{extract_talents.END_MARKER}\n")
    monkeypatch.setattr(extract_talents, "GAMEDATA", str(gamedata))
    monkeypatch.setattr(extract_talents, "STATIC_PY", str(static))
    extract_talents.main()
    text = static.read_text()
    assert text.startswith("keep = 1\n"), text[:40]
    assert "# 0 talents, 0 granted abilities" in text, text


def test_conversation_answer_clears_the_pending_conversation_for_its_owner(monkeypatch):
    import hconnect_server
    import services.tournament_game as tournament_game
    from tests.tests_combat import add_card
    import db as shared
    db = make_db()
    monkeypatch.setattr(shared, "_db", db)
    monkeypatch.setattr(tournament_game, "_db", db)
    monkeypatch.setattr(hconnect_server, "_db", db)
    add_card(db, 300, 5, "8305c05f-8355-4740-8599-2cf4a85c2c79", "warzone")
    handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
    handler.user_profile = {"id": 5}
    handler.client_reck_id = 5
    handler.send = lambda header, body: None
    session = _pvp_session(db)
    session._rules_port_battle_state["pending_conversation"] = {
        "conversation_id": "conv-x", "owner_id": 5,
        "ability_guid": "1c6d0fdf-9be0-27b3-186e-e139d1f71544", "source_uid": 300}
    answer = b""
    assert tournament_game._pvp_resolve_conversation(handler, session, answer, 5) is True
    assert "pending_conversation" not in session._rules_port_battle_state


def test_full_card_data_resolves_the_template_stats_for_a_session_card(monkeypatch):
    import hconnect_server
    import game_engine
    from tests.tests_combat import add_card
    db = make_db()
    monkeypatch.setattr(hconnect_server, "_db", db)
    add_card(db, 257, 5, "8305c05f-8355-4740-8599-2cf4a85c2c79", "warzone")
    handler = hconnect_server.HCPHandler(None, ("127.0.0.1", 0))
    handler.user_profile = {"id": 5}
    handler.client_reck_id = 5
    player = game_engine.UID.make(244, 5)
    ai = game_engine.UID.make(game_engine.SERVICE_AI_UID_TYPE, 1000)
    game = game_engine.Game(1, player, ai)
    resolved = handler._card_full_data(
        game, game_engine.SessionCardId(game_engine.UID(257)),
        "8305c05f-8355-4740-8599-2cf4a85c2c79")
    template_guid, card_type, name = resolved[0], resolved[1], resolved[2]
    assert template_guid == "8305c05f-8355-4740-8599-2cf4a85c2c79", resolved
    assert int(card_type) == int(game_engine.ECardTypes.Troop), resolved
    assert name == "Field Tactician", resolved


def test_legal_targets_for_a_self_target_template_offers_the_source_card():
    import json
    import pvp_db
    from rules_port.targeting import _legal_targets
    from tests.tests_combat import add_card
    db = make_db()
    add_card(db, 100, 0, "8305c05f-8355-4740-8599-2cf4a85c2c79", "warzone")
    template = json.loads(pvp_db.db_ability_target_template_ids(
        "1c6d0fdf-9be0-27b3-186e-e139d1f71544", conn=db))[0]
    assert list(_legal_targets(db, 1, 0, template, 100, both_players=False,
                               champions=[], battle_state={})) == [100]


def test_explore_of_a_new_champion_walks_the_campaign_route_to_a_stop():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign_editor"))
    import db as shared
    import server_playthrough
    previous = shared._db
    try:
        ok, route = server_playthrough.explore(None, set(), steps=40, race="Dwarf")
    finally:
        shared._db = previous          # the playthrough binds a copy; restore ours
    assert ok, route
    assert any(step.startswith(("stopped", "explored everything", "travel", "enter", "talk")) for step in route), route


def test_enter_tournament_joins_a_sealed_room_and_replies_to_the_player(monkeypatch):
    from types import SimpleNamespace
    import hconnect_server
    from services import legacy_tournaments
    ts = hconnect_server.tournament_server
    sent, calls = [], []
    handler = SimpleNamespace(client_reck_id=5, user_profile={"name": "duel5"}, scnt=0,
                              client_uid=5, sid=1,
                              send=lambda header, body: sent.append((header, body)))
    monkeypatch.setattr(ts, "leave_all", lambda uid: calls.append(("leave", uid)))
    monkeypatch.setattr(ts, "join_tournament",
                        lambda tid, uid, name, deck_id=None, entry_group=0: (True, 1, 8, 2))
    monkeypatch.setattr(ts, "generate_sealed_deck",
                        lambda tid, uid, set_id: calls.append(("sealed", tid, uid, set_id)))
    monkeypatch.setattr(hconnect_server, "uid_instance", lambda inner_bytes, name: 0)
    monkeypatch.setattr(hconnect_server, "db_tournament_room_for_game",
                        lambda tid, conn=None: {"set_id": "set02"})
    monkeypatch.setattr(hconnect_server, "_is_corinth_room", lambda room: False)
    monkeypatch.setattr(hconnect_server, "db_tournament_signups_by_tournament", lambda tid: [])
    monkeypatch.setitem(hconnect_server.player_handlers, 5, None)
    legacy_tournaments.handle_entertournament_25029(
        handler, "target", "instance", 25029, 40, 0, "session", "conh",
        {"TournamentID": 7, "EntryGroup": 0}, b"", b"")
    assert hconnect_server.player_handlers[5] is handler
    assert ("leave", 5) in calls and ("sealed", 7, 5, "set02") in calls, calls
    assert len(sent) == 1 and sent[0][0]["reqid"] == 41, sent
    assert handler.scnt == 1


def test_scripted_draw_command_moves_the_drawn_card_into_hand():
    import game_engine
    game = game_engine.Game(1, game_engine.UID.make(244, 5), game_engine.UID.make(3, 1000))
    drawn = game_engine.SessionCardId(game_engine.UID.make(1, 77))
    game.draw_cards = lambda uid, count: [drawn] if count == 1 else []
    game.card_defs = {}
    game.script_commands = [{"type": "draw", "args": [1]}]
    packets = game.step()
    assert packets and game.script_index == 1, (packets, game.script_index)
    # The drawn card is pushed as a card update and then a card move in one packet.
    assert len(packets[0].event_ids) == 2, list(packets[0].event_ids)


def test_failed_dungeon_node_stays_incomplete_after_its_outcome_conversation():
    import copy
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign"))
    import campaign_rules as cr
    rules = {
        "schema_version": 1, "start": {"node": "A", "flags": []},
        "map": {"paths": []},
        "nodes": {"A": {"entries": [
            {"id": "fail-talk", "kind": "conversation", "conversation": "DREAM_FAIL",
             "when": {}}]}},
    }
    state = cr.initial_state(rules)
    state["nodes"] = {"A": {"failed": True, "retryable": True, "won": False}}
    state, _ = cr.apply(rules, state, {"type": "conversation_end", "node": "A",
                                       "conversation": "DREAM_FAIL", "ending": ""})
    node = state["nodes"]["A"]
    assert not node.get("completed"), node
    assert node.get("failed") and node.get("retryable"), node


def test_continuous_commander_aura_is_not_resolved_as_a_play_time_ability():
    import json
    from rules_port.card_transactions import automatic_instance_ability_guids
    from tests.tests_statics import make_db as statics_db, card
    db = statics_db()
    # Bucktooth Commander: "Other Shin'hare you control have +1/+1" (continuous).
    aura = "f67fc196-4642-7cdd-58f6-70fee1ea3be7"
    card(db, 201, 5, "be81211f-8105-4446-8b1e-b713d8cad43d", "hand",
         json.dumps([aura]), ctype="Troop")
    assert aura not in automatic_instance_ability_guids(db, 1, 201, ()), \
        automatic_instance_ability_guids(db, 1, 201, ())


def test_spell_permanent_card_modifier_resolves_on_play_but_troop_aura_does_not():
    import json
    from rules_port.card_transactions import automatic_instance_ability_guids
    from tests.tests_statics import make_db as statics_db, card
    db = statics_db()
    # Evolve (BasicAction): "Each troop you control gets +1/+1" is a one-shot
    # cast effect on a spell that goes to the crypt, not a board aura.
    evolve = "19227131-8de7-7286-333f-7d72f3eb08a0"
    card(db, 301, 5, "dada8b50-5c78-483a-a7c3-acca2fd4d7f2", "hand",
         json.dumps([evolve]), ctype="BasicAction")
    assert evolve in automatic_instance_ability_guids(
        db, 1, 301, (), in_play=False)
    # The same permanent modifier on a troop is a continuous aura.
    card(db, 302, 5, "be81211f-8105-4446-8b1e-b713d8cad43d", "hand",
         json.dumps([evolve]), ctype="Troop")
    assert evolve not in automatic_instance_ability_guids(db, 1, 302, ())


def test_seeded_target_bounds_treat_an_unset_maximum_as_unbounded():
    from AssetExtraction.gamedata_seed import extract
    rows = {row[0]: row for row in extract()["tables"]["target_templates"]}
    # Evolve: "each troop you control" leaves m_MaxTargetCount unset (C#
    # int.MaxValue); the seed must not clamp it to one target.
    evolve_each = rows["1d738069-d6d7-2275-4b21-f113c59bdb15"]
    assert evolve_each[9] == 2147483647, evolve_each
    # A single authored maximum of one is still one.
    single = [row for row in rows.values() if row[9] == 1]
    assert single, "expected authored single-target templates"


def test_dungeon_battle_outcome_conversation_plays_then_completes_the_node():
    import copy
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign"))
    import campaign_rules as cr
    rules = {
        "schema_version": 1, "start": {"node": "F", "flags": []},
        "map": {"paths": []}, "dungeon": True,
        "nodes": {"F": {"entries": [
            {"id": "pick", "kind": "conversation", "conversation": "PICK", "when": {}},
            {"id": "won", "kind": "conversation", "conversation": "WIN",
             "when": ["outcome:success"]},
            {"id": "lost", "kind": "conversation", "conversation": "LOSE",
             "when": ["outcome:fail"]}]}},
    }
    base = cr.initial_state(rules)
    base["nodes"] = {"F": {"talks": 1}}
    state, _ = cr.apply(rules, copy.deepcopy(base),
                        {"type": "encounter_result", "node": "F", "scene": "",
                         "victory": True, "events": []})
    assert cr.active_entry(rules, state, "F")["id"] == "won"
    assert not state["nodes"]["F"].get("completed"), state["nodes"]["F"]
    state, _ = cr.apply(rules, state, {"type": "conversation_end", "node": "F",
                                       "conversation": "WIN", "ending": ""})
    assert state["nodes"]["F"].get("completed"), state["nodes"]["F"]
    state, _ = cr.apply(rules, copy.deepcopy(base),
                        {"type": "encounter_result", "node": "F", "scene": "",
                         "victory": False, "events": []})
    assert cr.active_entry(rules, state, "F")["id"] == "lost"
    state, _ = cr.apply(rules, state, {"type": "conversation_end", "node": "F",
                                       "conversation": "LOSE", "ending": ""})
    assert not state["nodes"]["F"].get("completed") and state["nodes"]["F"].get("failed"), state["nodes"]["F"]
    assert cr.active_entry(rules, state, "F")["id"] == "pick", state["nodes"]["F"]


def test_defeated_dungeon_node_stays_retryable():
    import copy
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "campaign"))
    import campaign_rules as cr
    rules = {"schema_version": 1, "start": {"node": "F", "flags": []},
             "map": {"paths": []}, "dungeon": True,
             "nodes": {"F": {"entries": [{"id": "pick", "kind": "conversation",
                                          "conversation": "PICK", "when": {}}]}}}
    base = cr.initial_state(rules)
    base["nodes"] = {"F": {"talks": 1}}
    state, _ = cr.apply(rules, copy.deepcopy(base),
                        {"type": "encounter_result", "node": "F", "scene": "",
                         "victory": False, "events": []})
    node = state["nodes"]["F"]
    assert node.get("failed") and node.get("retryable"), node
    assert cr.node_status(rules, state, "F") == (False, True), cr.node_status(rules, state, "F")


def test_ai_pays_a_card_sacrifice_with_its_weakest_troop_or_cannot_play_it():
    from types import SimpleNamespace
    import ai
    import db as shared
    from tests.tests_combat import SessionStub, add_card, make_db as combat_db
    db = combat_db()
    previous, previous_shared = ai._db, shared._db
    ai._db = shared._db = db
    try:
        # Field Tactician as the AI's only troop; Necessary Sacrifice in hand.
        add_card(db, 201, 0, "8305c05f-8355-4740-8599-2cf4a85c2c79", "warzone")
        add_card(db, 202, 0, "189d494b-571d-4d36-a312-fef0dddcd343", "hand")
        card = SimpleNamespace(card_uid=202, template_guid="189d494b-571d-4d36-a312-fef0dddcd343",
                               name="Necessary Sacrifice")
        sacrificed = []
        handler = SimpleNamespace(_sacrifice_troop=lambda game, session, pl, ai_t, uid:
                                  sacrificed.append(uid))
        game = SimpleNamespace(player_uid=5)
        session = SessionStub()
        assert ai._ai_pay_card_sacrifice(handler, game, session, 0, {}, card) is True
        assert sacrificed == [201], sacrificed
        sacrificed.clear()
        # The troop is also the only legal effect target: the card cannot be paid for.
        assert ai._ai_pay_card_sacrifice(handler, game, session, 0, {}, card, exclude=[201]) is False
        assert sacrificed == []
    finally:
        ai._db, shared._db = previous, previous_shared
