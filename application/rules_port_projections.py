"""Typed RulesPort projections: the callbacks the native port calls back into
the handler with (transactions it has validated, and the mutation to apply).

Each method is the body of a callback that ``_maybe_attach_rules_port`` used to
define inline. The callbacks there now pass their session (and port) through.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
import game_engine
from db import log_req


if TYPE_CHECKING:
    from application._host_surface import HostSurface as _HostBase
else:
    _HostBase = object


class RulesPortProjectionMixin(_HostBase):
    def _rp_card_projection(self, session, kind, transaction):
        """Dispatch only the explicit typed card-play mutations."""
        if kind in ("play_troop", "play_artifact", "play_spell",
                    "play_champion"):
            handled = self._handle_rules_port_card_play(
                session, transaction)
            if handled:
                session._rules_port_mutation_emitted = True
            return handled
        return False

    def _rp_resource_mutation(self, session, transaction):
        """Apply every resource through the port-owned seam."""
        handled = self._handle_rules_port_resource(
            session, transaction)
        if handled:
            session._rules_port_mutation_emitted = True
        return handled

    def _rp_discard_projection(self, session, transaction):
        """Consume a validated DiscardTransaction through the port."""
        original = getattr(session, "_rules_port_dispatch_command", None)
        if original is None:
            return False
        try:
            card_uid = (getattr(transaction, "payload", {}) or {}).get(
                "card_id")
            handled = bool(self._handle_discard_transaction(
                session, original, card_uid=card_uid))
            if handled:
                session._rules_port_mutation_emitted = True
            return handled
        except Exception as exc:
            log_req(f"    RulesPort discard mutation failed: {exc}")
            return False

    def _rp_hand_projection(self, session, kind, transaction):
        """Consume opening-hand mutations through the port boundary."""
        original = getattr(session, "_rules_port_dispatch_command", None)
        if original is None:
            return False
        try:
            if kind == "accept_starting_hand":
                handled = bool(self._handle_mulligan_keep_transaction(
                    session, original))
            elif kind == "mulligan":
                handled = bool(self._handle_mulligan_redraw_transaction(
                    session, original))
            else:
                handled = False
            if handled:
                session._rules_port_mutation_emitted = True
            return handled
        except Exception as exc:
            log_req(f"    RulesPort hand mutation failed: {exc}")
            return False

    def _rp_attack_projection(self, session, transaction, port):
        original = getattr(session, "_rules_port_dispatch_command", None)
        if original is None:
            return False
        try:
            handled = bool(self._handle_commit_attack_transaction(
                session, original,
                declarations=(getattr(transaction, "payload", {}) or
                              {}).get("declarations"),
                authoritative_port=port))
            if handled:
                session._rules_port_mutation_emitted = True
            return handled
        except Exception as exc:
            log_req(f"    RulesPort attack mutation failed: {exc}")
            return False

    def _rp_defense_projection(self, session, transaction, port):
        original = getattr(session, "_rules_port_dispatch_command", None)
        if original is None:
            return False
        try:
            handled = bool(self._handle_commit_defense_transaction(
                session, original,
                declarations=(getattr(transaction, "payload", {}) or
                              {}).get("declarations"),
                authoritative_port=port))
            if handled:
                session._rules_port_mutation_emitted = True
            return handled
        except Exception as exc:
            log_req(f"    RulesPort defense mutation failed: {exc}")
            return False

    def _rp_damage_projection(self, session, transaction):
        original = getattr(session, "_rules_port_dispatch_command", None)
        if original is None:
            return False
        try:
            handled = bool(self._handle_assign_damage_transaction(
                session, original,
                assignments=(getattr(transaction, "payload", {}) or
                             {}).get("assignments"),
                native_combat=True))
            if handled:
                session._rules_port_mutation_emitted = True
            return handled
        except Exception as exc:
            log_req(f"    RulesPort damage mutation failed: {exc}")
            return False

    def _rp_choice_projection(self, session, transaction):
        """Resume a metadata choice prompt through the port boundary."""
        original = getattr(session, "_rules_port_dispatch_command", None)
        if original is None:
            return False
        try:
            pl_t = game_engine.UID.make(244, int(self.client_reck_id))
            ai_t = game_engine.UID.make(game_engine.SERVICE_AI_UID_TYPE, 1000)
            handled = bool(self._resolve_pending_choice(
                session, pl_t, ai_t, original.inner_bytes,
                typed_payload=getattr(original, "typed_payload", None)))
            if handled:
                session._rules_port_mutation_emitted = True
            return handled
        except Exception as exc:
            log_req(f"    RulesPort choice continuation failed: {exc}")
            return False
