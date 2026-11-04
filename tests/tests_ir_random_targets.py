"""A random target draws from the same pool, in the same order, as the live resolver, so the same RNG picks the same card."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.tests_builtin_counter_rules import SessionStub, _setup  # noqa: E402
from tests.tests_combat import make_db
import db as dbmod

# An Uncommon cost-2 troop: the random templates below filter on rarity, cost and the deck.
RARE_TROOP = "0059eda0-e9a0-472c-adc2-021850b98c89"
# Random auto-targets over every zone, and one that filters the controller's deck.
RANDOM_TEMPLATES = ("eef04fcf-8aa2-17cb-c547-45daf81893a3", "f672d0df-3fb7-32bf-806e-e2f594852562")


def test_a_random_pool_matches_the_live_pool_in_order(db):
    from rules_port import ir_exec
    from rules_port.context import EffectContext
    from rules_port.targeting import legal_targets, target_uses_both_players

    pl_t, ai_t, game, handler, bstate, player_champ, ai_champ = _setup(db)
    # The live handler names both champions' templates; the fixture does not, so name them here.
    champ_guid = db.execute("SELECT guid FROM champion_templates LIMIT 1").fetchone()[0]
    handler._ai_champ_guid = handler._player_champ_guid = champ_guid
    bstate.setdefault("champ_guid_map", {5: champ_guid, 0: champ_guid})
    # Deck cards from both players, out of position order, so the order is what is compared.
    for uid, owner, position in ((760, 5, 3), (761, 0, 1), (767, 0, 0), (768, 5, 1), (769, 0, 2), (770, 5, 5)):
        db.execute("INSERT INTO game_cards (session_id, user_id, card_uid, template_guid, card_template_id, location,"
                   " position, card_state, card_abilities, card_type, card_attributes, card_attack_mod, card_defense_mod,"
                   " card_cost_mod, card_damage, permanent_buffs, temporary_buffs, card_uses, resolved_at,"
                   " original_template_guid) VALUES (1,?,?,?,?,'deck',?,0,'[]','Troop',0,0,0,0,0,'{}','{}','{}',0,?)",
                   (owner, uid, RARE_TROOP, RARE_TROOP, position, RARE_TROOP))
    db.commit()
    context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
    champions = getattr(handler, "_champion_targets", lambda: [])() or []
    for guid in RANDOM_TEMPLATES:
        scope = ir_exec.Scope(context=context, source_uid=player_champ, owner_uid=5)
        interpreted = ir_exec._card_candidates(ir_exec._load("AbilityTargetTemplate", guid), scope)
        live = list(legal_targets(db, 1, 5, guid, player_champ, both_players=target_uses_both_players(db, guid),
                                  champions=champions, battle_state=bstate))
        assert interpreted == live, (guid, interpreted, live)


if __name__ == "__main__":
    import db as dbmod
    dbmod._db = make_db()
    test_a_random_pool_matches_the_live_pool_in_order(dbmod._db)
    print("ok")
