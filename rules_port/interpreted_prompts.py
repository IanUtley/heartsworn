"""Presents an interpreter pause to the player: the revealed-card, deck-search, choice-zone and zone pickers, and
the event fields a chain descriptor carries. The answers resume the interpreter (application/pending_choices.py
and services/pvp_flow.py)."""

from __future__ import annotations

from .picks import INTERPRETED_CHOICE_ZONE


def _ai_revealed_pick(handler, session, battle_state, ai_uid, player_uid, candidates):
    """The card the AI picks from a reveal: its most valuable card by the AI's card evaluation, then cost, then uid
    (the order the AI uses to rank cards elsewhere). Falls back to the first candidate the evaluator cannot read."""
    from ai_eval import build_evaluator
    evaluator = build_evaluator(handler, session, battle_state, ai_uid, player_uid)
    cards = [card for card in (evaluator.card_for_uid(int(uid)) for uid in candidates) if card is not None]
    if not cards:
        return int(candidates[0])
    best = max(cards, key=lambda card: (evaluator.get_card_value(card), int(card.cost or 0), int(card.card_uid)))
    return int(best.card_uid)


def prompt_interpreted_reveal(handler, game, session, db, player_uid, ai_uid, battle_state, pause):
    """Show the player an interpreted ability's pick: among the cards a reveal showed, through the client's
    revealed-card picker; or, from the deck, through the deck-search picker (Darkspire Priestess's search). The
    interpreter's continuation is kept with the prompt. The answer resumes the interpreter (see
    application/pending_choices.py). Returns "waiting"."""
    continuation = pause.continuation
    # The AI chose this pick (its ability was cast by the AI in PvE), so it answers the pick itself and the
    # interpreter carries on. Only the player's own picks are shown to the player.
    # The player's uid is a UID whose instance is the user id; the continuation's owner is that user id.
    player_id = player_uid.instance_id if hasattr(player_uid, "instance_id") else int(player_uid)
    if pause.revealed and not battle_state.get("pvp") and int(continuation["owner_uid"]) != int(player_id) \
            and pause.candidates:
        from rules_port.context import EffectContext
        from . import ir_exec
        pick = _ai_revealed_pick(handler, session, battle_state, ai_uid, player_uid, pause.candidates)
        battle_state.pop("resolution_paused", None)
        try:
            ir_exec.resume_ability(
                continuation, pick,
                context=EffectContext.from_rules_port(game, session, db, handler, player_uid, ai_uid,
                                                      battle_state, ""))
        except ir_exec.NeedsChoice as next_pause:
            return prompt_interpreted_reveal(handler, game, session, db, player_uid, ai_uid, battle_state,
                                             next_pause)
        return "interpreter"
    target_index = int(continuation["target_index"])
    native_shape = {
        "ability_guid": continuation["ability_guid"], "source_uid": continuation["source_uid"],
        "owner_id": continuation["owner_uid"], "target_index": target_index, "target_map": {},
        "variables": {}, "resume_effect_order": int(continuation["position"]), "ability_instance_id": 1,
        "interpreter": continuation,
    }
    from .picks import CHOICE_ZONE, REVEALED, ZONE_PICK, pick_kind
    kind = pick_kind(authored_zone=pause.authored_zone, revealed=pause.revealed, match=pause.match)
    if kind == CHOICE_ZONE:
        return _prompt_interpreted_choice_zone(handler, game, session, player_uid, ai_uid, battle_state, pause)
    if kind == ZONE_PICK:
        return _prompt_interpreted_zone_pick(handler, game, session, player_uid, ai_uid, battle_state, pause)
    if kind == REVEALED:
        prompt = getattr(handler, "_prompt_revealed_choice", None)
        if not callable(prompt):
            raise RuntimeError("the interpreter paused for a revealed choice, with no picker to show it")
        prompt(game, session, player_uid, ai_uid, battle_state, continuation["ability_guid"],
               int(continuation["source_uid"]), int(continuation["owner_uid"]),
               list(pause.candidates), [int(uid) for uid in (battle_state.get("revealed_cards") or [])],
               optional=bool(pause.optional), continuation=native_shape,
               minimum=pause.minimum, maximum=pause.maximum)
    else:
        prompt = getattr(handler, "_prompt_deck_search", None)
        if not callable(prompt):
            raise RuntimeError("the interpreter paused for a deck choice, with no deck-search picker to show it")
        # A chosen deck match uses the matching-target mode (the child's own target, as the native path shows it).
        prompt(game, session, player_uid, ai_uid, battle_state, continuation["ability_guid"],
               int(continuation["source_uid"]), int(continuation["owner_uid"]),
               list(pause.candidates), kind="matching_target" if pause.match else "search",
               continuation=native_shape, minimum=pause.minimum, maximum=pause.maximum,
               **({"zone": pause.zone} if pause.match else {}))
    battle_state["resolution_paused"] = True
    return "waiting"


def _prompt_interpreted_choice_zone(handler, game, session, player_uid, ai_uid, battle_state, pause):
    """An authored pick from the Choosing zone: the client's choice-zone picker over the candidates. The pending choice
    keeps the interpreter continuation, and the answer (application/pending_choices.py for PvE, services/pvp_flow.py for
    PvP) resumes the interpreter with the chosen card as the target. Nothing is played: the pick only names a target.
    """
    continuation = pause.continuation
    pending = {
        "kind": INTERPRETED_CHOICE_ZONE,
        "choice_uids": [int(uid) for uid in pause.candidates],
        "source_uid": int(continuation["source_uid"]),
        "owner_id": int(continuation["owner_uid"]),
        "instance_id": int(continuation.get("instance_id") or 1),
        "ability_guid": str(continuation["ability_guid"]),
        "target_index": int(continuation["target_index"]),
        "interpreter": continuation,
        "parent": {},
    }
    prompt = getattr(handler, "_prompt_choice_cards", None)
    if not callable(prompt):
        raise RuntimeError("the interpreter paused for a choice-zone pick, with no choice-zone picker to show it")
    battle_state["pending_choice"] = pending
    battle_state["resolution_paused"] = True
    prompt(game, session, player_uid, ai_uid, battle_state, pending)
    return "waiting"


def _prompt_interpreted_zone_pick(handler, game, session, player_uid, ai_uid, battle_state, pause):
    """An authored single-card pick from the hand (a child's discard, say). The native trigger-target prompt shows it:
    the same option list and class-39 request a triggered ability's target uses. The pending trigger keeps the
    interpreter continuation, and its answer (application/pending_choices.py for PvE, services/pvp_flow.py for PvP)
    resumes the interpreter with the chosen card as the target."""
    from gamedata import DEFAULT_RECORD_STORE, ability_graph
    continuation = pause.continuation
    graph = ability_graph(DEFAULT_RECORD_STORE, str(continuation["ability_guid"]))
    template_guid = str(graph.targets[int(continuation["target_index"])].guid)
    prompt = getattr(handler, "_prompt_trigger_targets", None)
    if not callable(prompt):
        raise RuntimeError("the interpreter paused for a hand pick, with no trigger-target prompt to show it")
    prompt(game, player_uid, ai_uid, session, battle_state, int(continuation["source_uid"]),
           str(continuation["ability_guid"]), (template_guid,), [[int(uid) for uid in pause.candidates]],
           owner_id=int(continuation["owner_uid"]), optional=bool(pause.optional),
           interpreter=continuation, bounds=(int(pause.minimum), int(pause.maximum)))
    battle_state["resolution_paused"] = True
    return "waiting"


def trigger_event_fields(pending):
    """The event fields a chain descriptor for a chosen trigger carries, from the pending trigger that kept the event
    the dispatch raised. The interpreter reads them when it resolves the ability, so every descriptor for a chosen
    trigger must include them; an empty set means the pending trigger kept no event."""
    event = (pending or {}).get("trigger_event") or {}
    return {
        "trigger_event_type": event.get("event_type"),
        "trigger_event_data": dict(event.get("data") or {}),
        "trigger_source_uid": event.get("source_card_id"),
        "trigger_source_player_uid": event.get("source_player_id"),
        "trigger_target_player_uid": event.get("target_player_id"),
    }

