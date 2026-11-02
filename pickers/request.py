"""A choice the player must make, described without any client wire types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PickRequest:
    player_id: int                       # the player who chooses
    prompt_ability: str                  # the ability GUID the option names
    source_uid: int                      # the card that asks
    instance_id: int                     # the ability instance the answer belongs to
    target_index: int                    # which target template of the ability is being answered
    target_template_id: str              # that target's template GUID
    candidates: tuple[int, ...]          # legal card UIDs (computed by the server)
    minimum: int                         # fewest cards the player must pick
    maximum: int                         # most cards the player may pick
    matching_targets: tuple[str, ...] = ()   # parallel target-template GUIDs (matching-target picks)
