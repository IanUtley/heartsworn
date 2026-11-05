"""The Unity picker sends one option list, then the activation request its prompt kind names, then a GreenLight."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import game_engine  # noqa: E402
from pickers import PickRequest, PickTarget, present_pick, to_web_message

CHOICE_TEMPLATE = "11111111-2222-3333-4444-555555555555"
ABILITY = "66666666-7777-8888-9999-aaaaaaaaaaaa"


def _request(kind):
    return PickRequest(
        player_id=game_engine.UID.make(244, 5), prompt_ability=ABILITY, source_uid=0x101,
        instance_id=7,
        targets=(PickTarget(index=0, template_id=CHOICE_TEMPLATE,
                            candidates=(0x201, 0x202), minimum=1, maximum=1),),
        prompt_kind=kind)


def test_present_pick_sends_option_list_then_the_kind_of_request_it_names():
    for kind, request_class in (
            ("ability", "AbilityActivationDataRequiredSessionEventArgs"),
            ("triggered", "TriggeredAbilityActivationDataRequiredSessionEventArgs")):
        request = _request(kind)
        game = game_engine.Game(1, request.player_id, game_engine.UID.make(244, 6))
        present_pick(game, request)
        names = [event.__class__.__name__ for event in game.events]
        assert names[-3:] == ["PlayerOptionListSessionEventArgs", request_class,
                              "GreenLightSessionEventArgs"], (kind, names)
        option = game.events[-3].options[0].instances[0]
        assert [int(c.uid.uid64) for c in option.target_instances[0].targets] == [0x201, 0x202]
        assert list(option.min_target_counts) == [1]
        assert list(option.max_target_counts) == [1]
        assert to_web_message(request)["kind"] == kind


def test_pvp_discard_prompt_sends_the_hand_as_candidates_only_to_its_controller():
    from unittest import mock
    from types import SimpleNamespace
    from services import tournament_game as tg

    session = SimpleNamespace(session_id=1)
    state = {"pending_discard_ability": ABILITY,
             "pending_discard_target_template": CHOICE_TEMPLATE}
    sent = []
    handler = object()
    with mock.patch.object(tg, "player_handlers", {5: handler}), \
            mock.patch.object(tg, "db_game_get_hand", return_value=[(0x301,), (0x302,)]), \
            mock.patch.object(tg, "_send_pvp_packet",
                              side_effect=lambda h, s, g, uid, kind: sent.append((h, uid, kind, g))):
        assert tg._pvp_push_discard_prompt(session, state, 5, 6, 0x101) is True
    (target, uid, kind, game), = sent
    assert target is handler and kind == "discard-prompt"
    names = [event.__class__.__name__ for event in game.events]
    assert names[-3:] == ["PlayerOptionListSessionEventArgs",
                          "AbilityActivationDataRequiredSessionEventArgs",
                          "GreenLightSessionEventArgs"], names
    option = game.events[-3].options[0].instances[0]
    assert [int(c.uid.uid64) for c in option.target_instances[0].targets] == [0x301, 0x302]


def test_option_list_declarations_carry_their_state_and_block_slots():
    from pickers import BLOCK_DECLARATION_TEMPLATE, PickOption, PickTarget, present_options

    pl_t = game_engine.UID.make(244, 5)
    game = game_engine.Game(1, pl_t, game_engine.UID.make(244, 6))
    present_options(game, pl_t, [
        PickOption(card_uid=0x101, state="Defend", prompt_ability=BLOCK_DECLARATION_TEMPLATE,
                   targets=(PickTarget(index=0, template_id=BLOCK_DECLARATION_TEMPLATE,
                                       candidates=(0x201, 0x202), minimum=0, maximum=2),)),
        PickOption(card_uid=0x102, state="Attack"),
    ])
    # A declaration is only the option list: no request and no GreenLight, the caller owns the rest of the window.
    assert [event.__class__.__name__ for event in game.events] == ["PlayerOptionListSessionEventArgs"]
    block, attack = game.events[0].options
    assert block.state == game_engine.ECardUsage.Defend
    assert list(block.instances[0].min_target_counts) == [0]
    assert list(block.instances[0].max_target_counts) == [2]
    assert [int(c.uid.uid64) for c in block.instances[0].target_instances[0].targets] == [0x201, 0x202]
    assert attack.state == game_engine.ECardUsage.Attack
    assert list(attack.instances) == []


if __name__ == "__main__":
    # Written in pytest style: run through pytest so each test executes and failures
    # set the exit status. A missing pytest fails loudly.
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
