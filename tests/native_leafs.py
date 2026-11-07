"""Call a Records effect type through the native RulesPort dispatcher using
the historical nine-argument leaf call shape that older fixtures use."""


class NativeLeafs:
    """``LEAFS[effect_type](game, session, db, handler, pl_t, ai_t, bstate,
    effect_guid, param)`` backed by ``rules_port.native_leaves.dispatch``."""

    def __contains__(self, effect_type):
        from rules_port.coverage import NATIVE_EFFECTS
        return effect_type in NATIVE_EFFECTS

    def __getitem__(self, effect_type):
        def call(game, session, db, handler, pl_t, ai_t, bstate,
                 effect_guid="", param=None, **kwargs):
            from rules_port import native_leaves as native_effects
            from rules_port.context import EffectContext

            if bstate is None:
                bstate = {}
            bstate["_rules_port_native_effect"] = True
            context = EffectContext.from_rules_port(
                game, session, db, handler, pl_t, ai_t, bstate,
                effect_guid, param or "")
            return native_effects.dispatch(
                effect_type, context, {"param": param or ""})
        return call


LEAFS = NativeLeafs()
