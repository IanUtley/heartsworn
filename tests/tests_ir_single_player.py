"""A chosen target with the SinglePlayer filter is covered, and its candidates follow its card filter."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.tests_builtin_counter_rules import TPL_GLADIATOR, SessionStub, _setup  # noqa: E402
from tests.tests_combat import make_db, add_card  # noqa: E402  (the fixture reads make_db from this module)

# Deals 2 damage to one target champion or troop; its one chosen target uses the SinglePlayer filter.
SINGLE_PLAYER_ABILITY_GUID = "98d4ff63-0c21-e74b-0879-565a6e911e33"


def test_a_chosen_single_player_target_is_covered_and_offers_both_players(db):
    from rules_port import ir_exec
    from rules_port.context import EffectContext

    assert ir_exec.coverage(SINGLE_PLAYER_ABILITY_GUID) == []
    pl_t, ai_t, game, handler, bstate, _player_champ, _ai_champ = _setup(db)
    add_card(db, 700, 5, TPL_GLADIATOR, loc="warzone")
    add_card(db, 800, 0, TPL_GLADIATOR, loc="warzone")
    db.commit()
    context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
    scope = ir_exec.Scope(context=context, source_uid=700, owner_uid=5)
    template_guid = ir_exec._guid(ir_exec._load("AbilityTemplate", SINGLE_PLAYER_ABILITY_GUID)
                                  .m_AbilityTargetTemplateIds[0])
    template = ir_exec._load("AbilityTargetTemplate", template_guid)
    candidates = ir_exec._card_candidates(template, scope)
    # The card filter admits only opposing cards, so the opponent's troop is offered and the controller's is not.
    assert 800 in candidates and 700 not in candidates, candidates


if __name__ == "__main__":
    import db as dbmod
    dbmod._db = make_db()
    test_a_chosen_single_player_target_is_covered_and_offers_both_players(dbmod._db)
    print("ok")
