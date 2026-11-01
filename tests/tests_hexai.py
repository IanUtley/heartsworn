"""Tests for the offline HexAI capture/train/agent loop.

These do not need a database: the feature encoder, capture writer, trainer and
runtime agent are all pure Python.  The runtime hook in ``pvp_autoplay`` is
covered here with a fake evaluator, which is the same interface the real
``ai_eval.CardEvaluator`` exposes.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from hexai import capture, candidates, features
from hexai.agent import Agent
from hexai.model import Model
from hexai import train as train_mod


class FakeCard:
    def __init__(self, uid, template, *, cost=1, card_type="Troop",
                 value=1.0, resource=False, attack=1, defense=1):
        self.card_uid = int(uid)
        self.template_guid = template
        self.cost = int(cost)
        self.card_type = card_type
        self.attributes = 0
        self.card_state = 0
        self.has_variable_cost = False
        self.value = float(value)
        self._resource = bool(resource)
        self._attack = int(attack)
        self._defense = int(defense)

    def is_resource(self):
        return self._resource

    def is_troop(self):
        return "troop" in str(self.card_type).lower()

    def is_quick_action(self):
        return False

    def effective_attack(self, in_play=True):
        return self._attack

    def effective_defense(self, in_play=True):
        return self._defense


class FakeEvaluator:
    """Minimal surface used by ``features`` and ``candidates``."""

    def __init__(self, hand):
        self.hand = list(hand)
        self.ai_warzone = []
        self.player_warzone = []
        self.player_hand_count = 3
        self.resources = 4
        self.total_resources = 4
        self.threshold = {}
        self.ai_health = 20
        self.player_health = 20

    def is_playable(self, card):
        return "True"

    def is_board_builder(self, card, pre_combat):
        return True

    def choose_action_targets(self, card):
        return []

    def choose_action_target(self, card):
        return None

    def preferred_x_cost(self, card):
        return 0

    def get_card_value(self, card):
        return card.value


def _state():
    return {"turn_number": 2, "turn_pid": 1, "phase": 10,
            "hp_1": 20, "hp_2": 20}


def test_feature_ids_are_stable_and_deduplicated():
    assert features.feature_id("turn:1") == features.feature_id("turn:1")
    assert features.feature_id("turn:1") != features.feature_id("turn:2")
    ids = features.feature_ids(["a", "a", "b"])
    assert len(ids) == 2
    # The value must stay inside a signed int64 / JSON exact range.
    assert all(0 <= int(fid) < (1 << 63) for fid in ids)


def test_candidates_exclude_resources():
    resource = FakeCard(1, "res", card_type="Resource", resource=True)
    troop = FakeCard(2, "troop", card_type="Troop")
    evaluator = FakeEvaluator([resource, troop])
    out = candidates.main_phase_candidates(evaluator, pre_combat=True)
    keys = [candidate["key"] for candidate in out]
    assert "card:res" not in keys
    assert "card:troop" in keys


def test_agent_overrides_only_with_a_choice():
    first = FakeCard(11, "aaa", card_type="Troop", value=1.0)
    second = FakeCard(22, "bbb", card_type="Troop", value=2.0)
    evaluator = FakeEvaluator([first, second])
    state = _state()
    greedy = {"kind": "card", "card": second, "reason": "greedy_b"}
    zero = Model(dim=8, embedding={}, value_w=[0.0] * 8)

    agent = Agent(zero, heuristic_mix=0.0)
    # Candidates are ordered by hand; index 0 is ``first``.  A zero model with
    # no heuristic prior always takes index 0, i.e. the non-greedy card.
    replacement = agent.choose_main(
        evaluator, state, 1, 2, greedy, pre_combat=True, session=None)
    assert replacement is not None
    assert replacement["kind"] == "card"
    assert replacement["card"] is first

    # If the heuristic already picked the model's top choice, keep it.
    greedy_first = {"kind": "card", "card": first, "reason": "greedy_a"}
    assert agent.choose_main(
        evaluator, state, 1, 2, greedy_first, pre_combat=True,
        session=None) is None

    # A single candidate is never reranked.
    single = FakeEvaluator([first])
    assert agent.choose_main(
        single, state, 1, 2, greedy_first, pre_combat=True,
        session=None) is None


def test_capture_roundtrip_and_dedupe():
    directory = tempfile.mkdtemp(prefix="hexai-capture-")
    old = os.environ.get("HEXAI_CAPTURE_DIR")
    os.environ["HEXAI_CAPTURE_DIR"] = directory
    capture.close()
    try:
        assert capture.init()
        assert capture.enabled()
        capture.begin_game("g1", 900, [1, 2], {"1": {"name": "A"}})
        state_ids = [features.feature_id("s1")]
        cands = [{"key": "card:a", "kind": "card", "score": 2.0,
                  "action_ids": [features.feature_id("a1")]},
                 {"key": "pass", "kind": "pass", "score": 0.0,
                  "action_ids": [features.feature_id("pass")]}]
        for _ in range(2):  # identical consecutive decisions dedupe
            capture.record_decision(
                seat_pid=1, opponent_pid=2, turn=1, kind="main",
                feature_ids=state_ids, candidates=cands, chosen="card:a")
        capture.record_decision(
            seat_pid=1, opponent_pid=2, turn=1, kind="main",
            feature_ids=state_ids, candidates=cands, chosen="pass")
        capture.end_game("g1", 1, turns=3, health={"1": 5, "2": 0})
        shard = capture.path()
    finally:
        capture.close()
        if old is None:
            os.environ.pop("HEXAI_CAPTURE_DIR", None)
        else:
            os.environ["HEXAI_CAPTURE_DIR"] = old

    assert shard and os.path.exists(shard)
    records = [json.loads(line) for line in open(shard, encoding="utf-8")]
    decisions = [r for r in records if r["type"] == "decision"]
    assert len(decisions) == 2, decisions
    assert [r["chosen"] for r in decisions] == ["card:a", "pass"]
    end = [r for r in records if r["type"] == "end"]
    assert end and end[0]["winner"] == 1
    games = train_mod.read_shards([shard])
    assert games["g1"]["winner"] == 1


def _write_toy_shard(directory):
    """Two games where the first candidate is always the heuristic choice."""
    path = os.path.join(directory, "toy.jsonl")
    state_ids = [features.feature_id("toy:state:1"),
                 features.feature_id("toy:state:2")]
    a_ids = [features.feature_id("toy:action:a")]
    b_ids = [features.feature_id("toy:action:b")]
    for game_index in range(2):
        game_id = f"toy-{game_index}"
        pids = [10, 20]
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "game", "game": game_id, "seed": game_index,
                "pids": pids, "decks": {}}) + "\n")
            for turn in range(1, 9):
                handle.write(json.dumps({
                    "type": "decision", "game": game_id,
                    "seat": 10, "opp": 20, "turn": turn, "kind": "main",
                    "features": state_ids,
                    "candidates": [
                        {"key": "card:a", "kind": "card", "score": 1.0,
                         "action_ids": a_ids},
                        {"key": "card:b", "kind": "card", "score": 2.0,
                         "action_ids": b_ids},
                    ],
                    "chosen": "card:a", "value_seat": 10, "meta": {}}) + "\n")
            handle.write(json.dumps({
                "type": "end", "game": game_id, "winner": 10,
                "pids": pids, "turns": 8,
                "health": {"10": 4, "20": 0}}) + "\n")
    return path


def test_training_learns_the_toy_policy():
    directory = tempfile.mkdtemp(prefix="hexai-train-")
    _write_toy_shard(directory)
    out = os.path.join(directory, "model.json")
    code = train_mod.main([
        "--data", directory, "--out", out, "--dim", "8", "--epochs", "40",
        "--holdout", "0.5", "--target", "chosen", "--quiet",
    ])
    assert code == 0
    model = Model.load(out)
    state_ids = [features.feature_id("toy:state:1"),
                 features.feature_id("toy:state:2")]
    action_candidates = [
        {"key": "card:a",
         "action_ids": [features.feature_id("toy:action:a")]},
        {"key": "card:b",
         "action_ids": [features.feature_id("toy:action:b")]},
    ]
    index, probabilities, value = model.choose(state_ids, action_candidates)
    assert index == 0, probabilities
    assert probabilities[0] > probabilities[1]
    # The value head learned the +1 (win) target from the finished game.
    assert value > 0.0, value


def test_torch_backend_when_available():
    try:
        import torch  # noqa: F401
    except ImportError:
        return  # numpy-only test environment; the GPU backend is optional
    directory = tempfile.mkdtemp(prefix="hexai-torch-")
    _write_toy_shard(directory)
    out = os.path.join(directory, "model.json")
    code = train_mod.main([
        "--data", directory, "--out", out, "--dim", "8", "--epochs", "20",
        "--holdout", "0.5", "--target", "chosen", "--backend", "torch",
        "--device", "cpu", "--quiet",
    ])
    assert code == 0
    model = Model.load(out)
    candidates_ = [
        {"key": "card:a",
         "action_ids": [features.feature_id("toy:action:a")]},
        {"key": "card:b",
         "action_ids": [features.feature_id("toy:action:b")]},
    ]
    index, probabilities, _value = model.choose(
        [features.feature_id("toy:state:1"),
         features.feature_id("toy:state:2")], candidates_)
    assert index == 0, probabilities


if __name__ == "__main__":
    test_feature_ids_are_stable_and_deduplicated()
    test_candidates_exclude_resources()
    test_agent_overrides_only_with_a_choice()
    test_capture_roundtrip_and_dedupe()
    test_training_learns_the_toy_policy()
    test_torch_backend_when_available()
    print("PASS hexai")
