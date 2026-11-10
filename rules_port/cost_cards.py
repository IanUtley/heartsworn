"""Cards an ability's cost takes (exhausted, voided, sacrificed, discarded), read from the activation's lists."""

from __future__ import annotations

from .runtime_helpers import raw_uid


# C# PayCardActionCost appends each paid card to the ability instance list
# that list-attr variables read ("for each troop exhausted this way",
# "equal to the voided troop's [ATK]").
_COST_LIST_NAMES = {
    "exhaust": "ExhaustedCards",
    "void": "VoidedCards",
    "sacrifice": "SacrificedCards",
    "discard": "DiscardedCards",
}


def _cost_card_lists(ability):
    """Return ``{list_name: [uid, ...]}`` for every paid card-cost kind."""
    out = {}
    for kind, list_name in _COST_LIST_NAMES.items():
        uids = _exhausted_cost_cards(ability, kind=kind)
        if uids:
            out[list_name] = uids
    return out


def _exhausted_cost_cards(ability, kind="exhaust"):
    """Cards paid for one kind of ``ability``'s additional costs.

    C# records exhausted cards in the ability instance's ``ExhaustedCards``
    list, which CountListAttr variables read ("for each troop exhausted this
    way" on the Construction Plans); other card costs have their own lists
    (``_COST_LIST_NAMES``).  The activation keeps each cost's selection by its
    index in the graph's additional cost targets.
    """
    metadata = getattr(ability, "metadata", None)
    graph = getattr(metadata, "graph", None)
    activation = getattr(ability, "activation", None)
    costs = tuple(getattr(graph, "additional_cost_targets", ()) or ())
    if not costs or activation is None:
        return []
    cost_map = getattr(activation, "cost_target_map", {}) or {}
    target_map = getattr(activation, "target_map", {}) or {}
    out = []
    for index, (cost_kind, _guid) in enumerate(costs):
        if str(cost_kind).lower() != kind:
            continue
        selected = cost_map.get(index, cost_map.get(str(index)))
        if not selected and kind == "exhaust":
            # Mono flattens exhaust selections into TargetMap; other kinds
            # are only trusted from the dedicated cost map.
            selected = target_map.get(index, target_map.get(str(index)))
        if selected is None:
            continue
        if not isinstance(selected, (list, tuple, set)):
            selected = (selected,)
        for value in selected:
            try:
                uid = raw_uid(value)
            except (TypeError, ValueError):
                continue
            if uid not in out:
                out.append(uid)
    return out

