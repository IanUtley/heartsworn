"""The Unity picker: the client's option-list events for one choice (the behaviour the server has always sent).

Order of events: PlayerOptionList (one option, with one target instance per slot and each slot's min/max), then the
request that asks the client for the answer, then a GreenLight to the chooser. The request is class 39
(TriggeredAbilityActivationDataRequired) for a triggered ability, or class 23 (AbilityActivationDataRequired) for a
DoubleChoice card choice.
"""

from __future__ import annotations

from pickers.request import PickRequest


def present_pick(game, request: PickRequest) -> None:
    import hconnect_server as _hc
    ge = _hc.game_engine
    ev = game._make_event(ge.PlayerOptionListSessionEventArgs)
    ev.player_id = request.player_id
    opt = game._make_event(ge.PlayerOptionSessionEventArgs)
    opt.card = ge.SessionCardId(ge.UID(int(request.source_uid)))
    opt.state = ge.ECardUsage.Activate
    inst = game._make_event(ge.OptionInstanceSessionEventArgs)
    inst.opt_id = ge.ResourceId.from_str(request.prompt_ability)
    # ConfigureAbility matches target instances through this parallel target-template list (see HOWTO.md).
    for parallel_id in request.option_target_ids():
        inst.target_ids.append(ge.ResourceId.from_str(parallel_id))
    for slot in request.targets:
        tgt = game._make_event(ge.TargetInstanceSessionEventArgs)
        tgt.target_index = slot.index
        tgt.target_id = ge.ResourceId.from_str(slot.template_id)
        tgt.targets = [ge.SessionCardId(ge.UID(int(uid))) for uid in slot.candidates]
        inst.target_instances.append(tgt)
        inst.min_target_counts.append(int(slot.minimum))
        inst.max_target_counts.append(int(slot.maximum))
    opt.instances.append(inst)
    ev.options.append(opt)
    game._push(ev)
    if request.prompt_kind == "ability":
        # DoubleChoice is resolved by the client's normal triggered-ability configuration flow: the option list supplies
        # the generated cards, then this class-23 event tells UIBattle to push BattleStateUseTriggeredAbility for the
        # built-in ChooseAndPlay ability. Without this event the options only update the cache; no picker state is
        # pushed.
        req = game._make_event(ge.AbilityActivationDataRequiredSessionEventArgs)
        req.player_id = request.player_id
        req.ability_instance_id = int(request.instance_id)
        req.ability_parent_id = 0
        req.source_card_id = ge.SessionCardId(ge.UID(int(request.source_uid)))
        req.ability_template_id = ge.ResourceId.from_str(request.prompt_ability)
        req.effect_group_id = 1
        # ChooseAndPlay has one explicit target effect. An empty effect list leaves the class-23 state with no
        # selectable threshold, even though the option list holds the choice cards.
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
