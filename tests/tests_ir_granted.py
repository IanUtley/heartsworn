"""The interpreter's granted abilities (GrantAbility, RevokeAbility, RevertPermanentModifications) reach the same
battle state as the native resolver, for abilities with no trigger."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import tests.ir_equivalence as H  # noqa: E402
from tests.tests_combat import SessionStub  # noqa: E402

IMMORTALITY = "6578f6c9-7be7-5b0c-d1f0-b3741a7f7fc0"  # EndOfTurn grant to the player's champion
CLEANSE = "692f5254-8e29-4363-ce7c-7fdbc87bce8c"  # RevertPermanentModifications on a target
OPPOSING = [(701, 0), (702, 0)]


def _state(ability_guid, mode):
    from rules_port import ir_exec
    from rules_port.context import EffectContext
    from rules_port.resolution import resolve_port_ability

    db, pl_t, ai_t, game, handler, bstate, _pc, _ac = H._scenario(ability_guid, OPPOSING, any_template=True)
    if mode == "interpreter":
        context = EffectContext.from_rules_port(game, SessionStub(), db, handler, pl_t, ai_t, bstate, "")
        ir_exec.run_ability(ability_guid, None, context=context, source_uid=900, owner_uid=5, event={"player": 5})
    else:
        resolve_port_ability(handler, game, SessionStub(), db, pl_t, ai_t, bstate, ability_guid, 900, 5,
                             target_map={})
    cards = db.execute("SELECT card_uid, card_abilities, location FROM game_cards ORDER BY card_uid").fetchall()
    return {
        "cards": [tuple(row) for row in cards],
        "grants": bstate.get("temporary_ability_grants"),
        "player_health": bstate.get("player_health"),
        "ai_health": bstate.get("ai_health"),
    }


def test_granted_ability_matches_native_grant():
    interpreted, native = _state(IMMORTALITY, "interpreter"), _state(IMMORTALITY, "native")
    assert interpreted["grants"] == native["grants"]
    assert interpreted["grants"], "the grant is recorded for its EndOfTurn lifetime"
    assert interpreted["cards"] == native["cards"]


def test_revert_modifications_matches_native():
    interpreted, native = _state(CLEANSE, "interpreter"), _state(CLEANSE, "native")
    assert interpreted == native


def test_grant_with_unsupported_power_source_is_refused():
    from rules_port import granted_abilities

    effect = {"m_GrantedAbilityTemplateId": {"m_Guid": "647fa8ff-fbe9-b701-0ab3-9f26c2e86166"},
              "m_AllSocketedPowersOfSource": 1}
    assert granted_abilities.gaps("GrantAbilityEffectTemplate", effect, "Permanent")
    assert granted_abilities.gaps("GrantAbilityEffectTemplate", {
        "m_GrantedAbilityTemplateId": {"m_Guid": "647fa8ff-fbe9-b701-0ab3-9f26c2e86166"}}, "EndOfTurn") == []


def test_grant_for_an_unknown_duration_is_refused():
    from rules_port import granted_abilities

    effect = {"m_GrantedAbilityTemplateId": {"m_Guid": "647fa8ff-fbe9-b701-0ab3-9f26c2e86166"}}
    assert granted_abilities.gaps("GrantAbilityEffectTemplate", effect, "EndOfNextTurn") == [
        "grant duration EndOfNextTurn"]
