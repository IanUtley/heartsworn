"""The Unity picker: the client's events for one choice (the behaviour the server has always sent).

A pick sends an optional CardsRevealed (the revealed cards the client turns into a picker), a PlayerOptionList (one
option, with one target instance per slot and each slot's min/max), then the request that asks the client for the
answer, then a GreenLight to the chooser. The request is class 39 (TriggeredAbilityActivationDataRequired) for a
triggered ability, or class 23 (AbilityActivationDataRequired) for a DoubleChoice card choice or a revealed choice.

An option list (present_options) sends only the PlayerOptionList: the declarations of attacks and blocks, where the caller
pushes the rest of the window itself.
"""

from __future__ import annotations

from pickers.request import PickOption, PickRequest


def _present_reveal(game, request: PickRequest, ge) -> None:
    """Reuse the reveal the game already holds for these cards, or queue one, and tag it with the ability instance.

    The client only turns a reveal into a picker when the same packet carries the class-23 request for the revealing
    instance (CombineRevealAndChoose). Without the shared instance id it keeps an informational Coverflow whose OK button
    cannot select a card, and no picker opens.
    """
    if request.reveal is None:
        return
    revealed_ids = {int(uid) for uid in request.reveal_ids}
    reveal_event = None
    for event in getattr(game, "events", ()):
        if not isinstance(event, ge.CardsRevealedSessionEventArgs):
            continue
        event_ids = {int(card.uid.uid64) for card in (event.session_card_ids or [])}
        if event_ids == revealed_ids:
            reveal_event = event
            break
    if reveal_event is None:
        count = len(request.reveal)
        reveal_event = game._make_event(ge.CardsRevealedSessionEventArgs)
        reveal_event.player_id = request.player_id
        reveal_event.session_card_ids = [
            ge.SessionCardId(ge.UID(int(uid))) for uid, _pos in request.reveal]
        reveal_event.collections = [ge.ECardCollections.Deck] * count
        reveal_event.owning_players = [request.player_id] * count
        reveal_event.positions = [int(pos or 0) for _uid, pos in request.reveal]
        game._push(reveal_event)
    reveal_event.ability_instance_id = int(request.instance_id)


def _build_option(game, ge, option: PickOption):
    """One PlayerOptionSessionEventArgs: the card and its state, with the instance when the option has one."""
    opt = game._make_event(ge.PlayerOptionSessionEventArgs)
    opt.card = ge.SessionCardId(ge.UID(int(option.card_uid)))
    opt.state = getattr(ge.ECardUsage, option.state)
    if option.prompt_ability is None:
        return opt
    inst = game._make_event(ge.OptionInstanceSessionEventArgs)
    inst.opt_id = ge.ResourceId.from_str(option.prompt_ability)
    # ConfigureAbility matches target instances through this parallel target-template list (see HOWTO.md).
    inst.target_ids.extend((ge.ResourceId.from_str(parallel_id)) for parallel_id in (option.option_target_ids()))
    for slot in option.targets:
        tgt = game._make_event(ge.TargetInstanceSessionEventArgs)
        tgt.target_index = slot.index
        tgt.target_id = ge.ResourceId.from_str(slot.template_id)
        tgt.targets = [ge.SessionCardId(ge.UID(int(uid))) for uid in slot.candidates]
        inst.target_instances.append(tgt)
        inst.min_target_counts.append(int(slot.minimum))
        inst.max_target_counts.append(int(slot.maximum))
    opt.instances.append(inst)
    return opt


def present_options(game, player_id, options) -> None:
    """Push one PlayerOptionList carrying ``options`` (possibly none). No request and no GreenLight: the caller owns the
    rest of the window."""
    import game_engine as ge
    ev = game._make_event(ge.PlayerOptionListSessionEventArgs)
    ev.player_id = player_id
    ev.options.extend((_build_option(game, ge, option)) for option in (options))
    game._push(ev)


def present_pick(game, request: PickRequest) -> None:
    import game_engine as ge
    _present_reveal(game, request, ge)
    ev = game._make_event(ge.PlayerOptionListSessionEventArgs)
    ev.player_id = request.player_id
    ev.options.append(_build_option(game, ge, request.as_option()))
    game._push(ev)
    if request.prompt_kind == "ability":
        # The class-23 request is UIBattle.OnAbilityActivationDataRequested's picker for one ability instance. For a
        # DoubleChoice it tells the client to push BattleStateUseTriggeredAbility for the built-in ChooseAndPlay ability:
        # without this event the options only update the cache and no picker state is pushed.
        req = game._make_event(ge.AbilityActivationDataRequiredSessionEventArgs)
        req.player_id = request.player_id
        req.ability_instance_id = int(request.instance_id)
        req.ability_parent_id = 0
        req.source_card_id = ge.SessionCardId(ge.UID(int(request.source_uid)))
        req.ability_template_id = ge.ResourceId.from_str(request.prompt_ability)
        req.effect_group_id = 1
        # The client reads this effect-instance list while it builds the configure state. An empty list can leave the
        # picker with no selectable target, even though the option list holds the cards.
        req.effect_instance_ids = [0]
        req.resolve_chain = False
        game._push(req)
    else:
        ev39 = ge.TriggeredAbilityActivationDataRequiredSessionEventArgs()
        ev39.player_id = request.player_id
        ev39.ability_instance_ids = [request.instance_id]
        ev39.ability_template_ids = [ge.ResourceId.from_str(request.prompt_ability)]
        ev39.source_card_ids = [ge.SessionCardId(ge.UID(int(request.source_uid)))]
        game._push(ev39)
    game.push_green_light(request.player_id, ge.EPriorityContext.Normal)
