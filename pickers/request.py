"""A choice the player must make, described without any client wire types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# The block declaration's ability: each defender troop's Defend option names it, and its target slot takes the attackers it
# may block (C# BlockAbility; the client's blocker picker).
BLOCK_DECLARATION_TEMPLATE = "83659505-152d-4ddc-89df-7c29bdfba16d"


@dataclass(frozen=True)
class PickTarget:
    index: int                           # the authored target index this slot answers
    template_id: str                     # the target template GUID for this slot
    candidates: tuple[int, ...]          # legal card UIDs for this slot (computed by the server)
    minimum: int                         # fewest cards the player must pick in this slot
    maximum: int                         # most cards the player may pick in this slot


@dataclass(frozen=True)
class PickOption:
    """One option in an option list: a card, the state it is offered in, and the instance (ability and target slots)
    the player may pick. ``prompt_ability`` None means a plain declaration with no instance (an attack)."""
    card_uid: int                        # the card the option sits on
    state: str                           # the ECardUsage member name: "Activate", "Attack" or "Defend"
    prompt_ability: str | None = None    # the instance's ability (option) GUID
    targets: tuple[PickTarget, ...] = ()
    # The parallel target-template GUIDs on the option. Empty means one per slot, in slot order.
    target_ids: tuple[str, ...] = ()

    def option_target_ids(self) -> tuple[str, ...]:
        return self.target_ids or tuple(target.template_id for target in self.targets)


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
    # card choice or a revealed choice, which the client configures as a built-in ability).
    prompt_kind: str = "triggered"
    # The revealed cards the Unity picker shows as a reveal, as (uid, position) pairs. None means no reveal is sent
    # here (the caller already sent one). An existing reveal is reused when its cards are exactly ``reveal_ids``.
    reveal: tuple[tuple[int, int], ...] | None = None
    reveal_ids: tuple[int, ...] = ()

    def option_target_ids(self) -> tuple[str, ...]:
        return self.target_ids or tuple(target.template_id for target in self.targets)

    def as_option(self) -> PickOption:
        """The option this request asks the player to pick: the source card, offered as Activate with its instance."""
        return PickOption(card_uid=self.source_uid, state="Activate", prompt_ability=self.prompt_ability,
                          targets=self.targets, target_ids=self.target_ids)
