"""The Unity picker sends one option list, then the activation request its prompt kind names, then a GreenLight."""

import game_engine
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
