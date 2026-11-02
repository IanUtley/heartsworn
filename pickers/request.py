"""A choice the player must make, described without any client wire types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PickTarget:
    index: int                           # the authored target index this slot answers
    template_id: str                     # the target template GUID for this slot
    candidates: tuple[int, ...]          # legal card UIDs for this slot (computed by the server)
    minimum: int                         # fewest cards the player must pick in this slot
    maximum: int                         # most cards the player may pick in this slot


@dataclass(frozen=True)
class PickRequest:
    player_id: Any                       # the player who chooses (the client's player UID)
    prompt_ability: str                  # the ability GUID the option names
    source_uid: int                      # the card that asks
    instance_id: int                     # the ability instance the answer belongs to
    targets: tuple[PickTarget, ...]      # one slot per target the ability asks for, in authored order
    # The parallel target-template GUIDs on the option. Empty means one per slot, in slot order.
    # Matching-target picks name the template the ability itself uses at the slot.
    target_ids: tuple[str, ...] = ()
    # Which client request asks for the answer: "triggered" (class 39, TriggeredAbilityActivationDataRequired, for a
    # triggered ability's target picker) or "ability" (class 23, AbilityActivationDataRequired, for a DoubleChoice
    # card choice, which the client configures as a built-in ability).
    prompt_kind: str = "triggered"

    def option_target_ids(self) -> tuple[str, ...]:
        return self.target_ids or tuple(target.template_id for target in self.targets)
