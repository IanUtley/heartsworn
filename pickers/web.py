"""The web picker's message: the same request as JSON, for web_client/. The web client answers with the chosen UIDs,
which the engine checks against the same candidates and bounds as the Unity answer."""

from __future__ import annotations

from typing import Any

from pickers.request import PickRequest


def to_web_message(request: PickRequest) -> dict[str, Any]:
    return {
        "type": "pick",
        "player": request.player_id,
        "ability": request.prompt_ability,
        "source": request.source_uid,
        "instance": request.instance_id,
        "targetIndex": request.target_index,
        "targetTemplate": request.target_template_id,
        "candidates": list(request.candidates),
        "minimum": request.minimum,
        "maximum": request.maximum,
        "matchingTargets": list(request.matching_targets),
    }
