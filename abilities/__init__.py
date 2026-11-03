"""Ability helpers shared by the RulesPort runtime.

Ability resolution itself is owned by ``rules_port`` (``rules_port.resolution``
and ``rules_port.ir_exec``).  This package holds the effect-leaf helpers,
conditions, targeting, statics and trigger projections that the native
runtime and the protocol host still call; import them from
``abilities.framework.<module>`` directly.
"""
