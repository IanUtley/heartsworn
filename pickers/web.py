"""The web picker's message: the same request as JSON, for web_client/. The web client answers with the chosen UIDs,
which the engine checks against the same candidates and bounds as the Unity answer."""

from __future__ import annotations

from typing import Any

from pickers.request import PickRequest


def to_web_message(request: PickRequest) -> dict[str, Any]:
    return {
        "type": "pick",
        "kind": request.prompt_kind,
        "player": request.player_id,
        "ability": request.prompt_ability,
        "source": request.source_uid,
        "instance": request.instance_id,
        "targetIds": list(request.option_target_ids()),
        "reveal": None if request.reveal is None else [uid for uid, _pos in request.reveal],
        "targets": [
            {
                "index": slot.index,
                "template": slot.template_id,
                "candidates": list(slot.candidates),
                "minimum": slot.minimum,
                "maximum": slot.maximum,
            }
            for slot in request.targets
        ],
    }
