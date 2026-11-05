from __future__ import annotations
from .runtime_helpers import as_int
import json
from dataclasses import dataclass, replace
from typing import Any
from .damage_effects import assign_damage_shield_order, serialized_damage
from rules_port.card_view import CardView
import rules_port.context as _context


class VariableEffectsMixin:
    """Methods of EffectContext grouped by variables.

    Mixed into EffectContext; attribute access goes through self, so the
    class composition is unchanged.
    """

    def randomize_variable(self) -> str:
        """Roll a typed random variable into the active ability state."""
        template = self.template_value("m_VariableName", "RandomNumber")
        name = str(template or "RandomNumber")
        try:
            minimum = int(self.value("m_MinValue", 1) or 0)
        except (TypeError, ValueError):
            minimum = 0
        try:
            maximum = int(self.value("m_MaxValue", minimum) or minimum)
        except (TypeError, ValueError):
            maximum = minimum
        # ``m_MaxValueField`` is the typed dynamic upper bound (an
        # EffectInputVariable such as "EqualToTheNumberOfLearnSpells...").
        # C# evaluates it before the bounds swap.
        max_field = self.template_value("m_MaxValueField", None)
        if max_field:
            from rules_port.fields import resolve_field
            maximum = int(resolve_field(
                max_field, self.bstate.get("ability_variables") or {},
                self.bstate.get("effect_outputs") or {}, self.bstate,
                maximum) or maximum)
        # C# swaps inverted bounds rather than clamping.
        if minimum > maximum:
            minimum, maximum = maximum, minimum
        span = max(0, maximum - minimum) + 1
        rng = self.bstate.get("_rules_rng")

        def roll():
            if rng is not None and hasattr(rng, "next"):
                # Use the session RNG so replays match the client.
                return minimum + int(rng.next(span)) % span
            import random
            return random.randint(minimum, maximum)

        value = roll()
        variables = self.bstate.setdefault("ability_variables", {})
        variables[name] = value
        # ``m_SecondValue`` rolls a second, distinct value into "<name>2".
        # C# only guarantees distinctness when the range holds at least two
        # values; a one-value range leaves it rolled but not stored.
        if self.template_value("m_SecondValue", False):
            second = value
            if maximum - minimum >= 2:
                attempts = 0
                while second == value and attempts < 100:
                    second = roll()
                    attempts += 1
            if second != value:
                variables[name + "2"] = second
        return f"randomized {name}={value}"

    def clear_stored(self) -> str:
        """Clear the source card's permanent and this-turn StoredTargets."""
        source_uid = self.bstate.get("resolving_source_uid")
        if source_uid is None:
            return "clear stored targets: no source card"
        uid = str(int(source_uid))
        self.bstate.setdefault("stored_targets_by_card", {}).pop(uid, None)
        self.bstate.setdefault("stored_targets_by_card_this_turn", {}).pop(
            uid, None)
        from rules_port.statistics import clear_tac_list
        for scope in ("PermanentData", "ThisTurnsData"):
            clear_tac_list(
                self.bstate, "cards", int(source_uid), scope,
                "StoredTargets")
        return "cleared stored targets"

    def set_responsible_player(self) -> str:
        """Mirror SetResponsiblePlayer by adopting the target's controller."""
        target = self.resolved_target()
        owner = self.target_owner(target, default=None)
        if target is None or owner is None:
            return "set responsible player: no target"
        self.bstate["resolving_owner_id"] = int(owner)
        self.bstate["resolving_responsible_player_id"] = int(owner)
        return f"responsible player {int(owner)}"

    def copy_ability_variable(self) -> str:
        """Copy one typed EffectField into the destination variable."""
        from rules_port.fields import resolve_field
        source = self.template_value("m_Source", None)
        destination = self.template_value("m_Destination", None)
        def variable_name(value):
            if hasattr(value, "field"):
                for key in ("m_InputVariableName", "m_VariableName",
                            "variable", "name"):
                    found = value.field(key, None)
                    if found:
                        return str(found)
            if isinstance(value, dict):
                for key in ("m_InputVariableName", "m_VariableName",
                            "variable", "name"):
                    if value.get(key):
                        return str(value[key])
            return str(value or "")
        dest_name = variable_name(destination)
        variables = self.bstate.setdefault("ability_variables", {})
        value = resolve_field(source, variables,
                              self.bstate.get("effect_outputs") or {},
                              self.bstate, 0)
        if not dest_name:
            return "copy ability variable: missing destination"
        variables[dest_name] = int(value or 0)
        return f"copied {dest_name}={variables[dest_name]}"

    def set_card_count_variable(self) -> str:
        """Count typed cards across the requested player collections."""
        import json
        from rules_port.targeting import evaluate_card_filter
        from rules_port.fields import effect_template

        template = effect_template(self.ability, self.effect_guid) or {}
        name = template.get("m_VariableName") or ""
        if not name and self.param:
            try:
                name = (json.loads(self.param) or {}).get("variable", "")
            except (TypeError, ValueError, json.JSONDecodeError):
                name = ""
        if not name:
            return "card count: missing variable"
        collection = template.get("m_CollectionFlags", "Deck|Hand|Warzone|Discard|Void")
        if isinstance(collection, (list, tuple, set)):
            collections = {str(item).rsplit(".", 1)[-1].lower()
                           for item in collection}
        else:
            collections = {part.strip().rsplit(".", 1)[-1].lower()
                           for part in str(collection).replace(",", "|").split("|")
                           if part.strip()}
        location_names = {"deck": "deck", "hand": "hand", "warzone": "warzone",
                          "discard": "discard", "void": "void",
                          "playedresources": "playedresources"}
        locations = [location_names[item] for item in collections if item in location_names]
        if not locations:
            self.bstate.setdefault("ability_variables", {})[str(name)] = 0
            return f"counted {name}=0"
        player_filter = str(template.get("m_PlayerFilter", "Self")).rsplit(".", 1)[-1].lower()
        owner = int(self.bstate.get("resolving_owner_id", 0) or 0)
        if player_filter in ("multipleopponents", "singleopponent"):
            owners = [int(pid) for pid in (self.bstate.get("player_ids") or [])
                      if int(pid) != owner]
            if not owners:
                owners = [int(self.bstate.get("opponent_id", 0) or 0)]
        elif player_filter in ("multipleplayers", "allplayers"):
            owners = [int(pid) for pid in (self.bstate.get("player_ids") or [])]
        else:
            owners = [owner]
        card_filter = template.get("m_CardFilter") or {}
        to_dict = getattr(card_filter, "to_dict", None)
        if callable(to_dict):
            card_filter = to_dict()
        from pvp_db import db_cards_for_filter
        rows = db_cards_for_filter(
            self.session.session_id, owners, locations, conn=self.db)
        count = 0
        for row in rows:
            card = CardView.from_count_row(row).as_shape("count_card")
            try:
                matched = evaluate_card_filter(
                    card, card_filter, self.bstate.get("resolving_source_uid"),
                    ability_state=self.bstate, db=self.db)
            except (TypeError, ValueError, KeyError):
                matched = False
            if matched:
                count += 1
        self.bstate.setdefault("ability_variables", {})[str(name)] = count
        return f"counted {name}={count}"

    def set_card_integer_variable(self) -> str:
        """Apply the client Add/Remove/Set operation to source card data."""
        name = str(self.template_value("m_VariableName", "") or "")
        if not name and self.param:
            try:
                name = str((json.loads(self.param) or {}).get("variable", ""))
            except (TypeError, ValueError, json.JSONDecodeError):
                name = ""
        source = self.bstate.get("resolving_source_uid")
        if not name or source is None:
            return "card integer variable: missing name/source"
        operation = str(self.template_value("m_Operation", "Set")).rsplit(".", 1)[-1].lower()
        value = int(self.value("m_InputValue", 0) or 0)
        buffs = self._card_buffs(int(source), "permanent_buffs")
        values = buffs.setdefault("card_integer_variables", {})
        old = int(values.get(name, 0) or 0)
        new = old + value if operation == "add" else old - value if operation == "remove" else value
        values[name] = new
        self._save_card_buffs(int(source), buffs, "permanent_buffs")
        self.bstate.setdefault("card_integer_variables", {})[name] = new
        self.bstate.setdefault("ability_variables", {})[name] = new
        return f"card variable {name}={new}"

    def set_constant_value_variable(self) -> str:
        """Set an ability variable from the deprecated typed m_Value field."""
        name = str(self.template_value("m_VariableName", "") or "")
        value = self.template_value("m_Value", None)
        if self.param:
            try:
                payload = json.loads(self.param)
                name = name or str(payload.get("variable", ""))
                if value is None:
                    value = payload.get("value", 0)
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        if not name:
            return "constant variable: missing name"
        value = int(value or 0)
        self.bstate.setdefault("ability_variables", {})[name] = value
        return f"constant variable {name}={value}"

    def counter(self, target: int | None, name: str,
                counter_guid: str | None = None, amount: int = 1,
                operation: str = "add") -> tuple[int, int]:
        """Change one card/champion counter and publish its client events.

        Counter persistence differs by target: ordinary cards use the
        ``game_cards.permanent_buffs`` JSON, while champions use the
        persisted battle-state map.  Keeping both behind this operation lets
        simple effect leaves describe the rule without knowing either storage
        format or event sequence.
        """
        if target is None:
            return 0, 0
        from rules_port.counter_effects import change_counter
        return change_counter(self, target, name, counter_guid, amount,
                              str(operation or "add").lower())

    def store_target(self, target: int | None = None) -> str:
        """Remember one resolved target for later effects in this ability."""
        target = self.resolved_target() if target is None else target
        if target is None:
            return "store targets: none"
        # C# StoreTargets honors m_SetTargets (replace the list) and
        # m_OnlyUntilEndOfTurn (store in ThisTurnsData rather than
        # PermanentData), and de-duplicates. StoreInAbility is a TAC operand;
        # without it the client stores on the source card, where any ability
        # on that source can later read the target.
        set_targets = bool(self.template_value("m_SetTargets", False))
        only_turn = bool(self.template_value("m_OnlyUntilEndOfTurn", False))
        from rules_port.tac import tac_int
        serialized = self.template_value("m_SerializedTAC", None)
        if hasattr(serialized, "field"):
            serialized = serialized.field("data", "")
        elif isinstance(serialized, dict):
            serialized = serialized.get("data")
        store_in_ability = bool(tac_int(serialized, "StoreInAbility", 0))
        if store_in_ability:
            # The C# instance list is not card-duration scoped, even when the
            # shared effect template's OnlyUntilEndOfTurn field is set.
            store = self.bstate.setdefault("stored_targets", {})
            bucket = store.setdefault(self.ability_guid, [])
        else:
            source_uid = self.bstate.get("resolving_source_uid")
            if source_uid is None:
                return "store targets: no source card"
            key = ("stored_targets_by_card_this_turn" if only_turn else
                   "stored_targets_by_card")
            store = self.bstate.setdefault(key, {})
            bucket = store.setdefault(str(int(source_uid)), [])
        if set_targets:
            if int(target) in bucket:
                return f"stored {hex(int(target))}"
            bucket.clear()
        if int(target) not in bucket:
            bucket.append(int(target))
        return f"stored {hex(int(target))}"

    def store_name(self, target: int | None = None) -> str:
        """Remember a target card's authoritative template name."""
        target = self.resolved_target() if target is None else target
        if target is None:
            return "store name: no target"
        from pvp_db import db_card_zone_details, db_template_name
        details = db_card_zone_details(
            self.session.session_id, int(target), conn=self.db)
        name = db_template_name(details[0], conn=self.db) if details else ""
        self.bstate.setdefault("stored_names", {}).setdefault(
            self.ability_guid, []).append(name)
        return f"stored name '{name}'"

    def store_list_attr(self, list_name: str, attr_name: str, value: int,
                        *, set_list: bool = False,
                        until_end_of_turn: bool = False) -> str:
        """Persist one typed TAC list entry in resolver state."""
        list_name = str(list_name or "")
        attr_name = str(attr_name or "")
        if not list_name:
            return "store list: no list name"
        lists = self.bstate.setdefault("list_attrs", {}).setdefault(
            self.ability_guid, {})
        if set_list:
            lists[list_name] = []
        lists.setdefault(list_name, []).append({
            "name": attr_name,
            "value": int(value or 0),
            "until_end_of_turn": bool(until_end_of_turn),
        })
        return f"stored {attr_name} in {list_name}"

    def remember_keyword_powers(self) -> str:
        """Remember target abilities matching the typed keyword metadata."""
        target = self.resolved_target()
        if target is None:
            return "remember powers: no target"
        keyword = self.template_value("m_Keyword", "") or ""
        all_powers = bool(self.template_value("m_AllPowers", False))
        from pvp_db import db_card_ability_payload
        from rules_port.trigger_discovery import ability_matches_keyword
        try:
            card_abilities = json.loads(db_card_ability_payload(
                self.session.session_id, int(target), conn=self.db) or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            card_abilities = []

        remembered = self.bstate.setdefault(
            "remembered_powers", {}).setdefault(self.ability_guid, [])
        for ability_guid in card_abilities:
            matches = ability_matches_keyword(ability_guid, keyword)
            if all_powers or matches:
                if ability_guid not in remembered:
                    remembered.append(ability_guid)
        return f"remembered {len(remembered)} {keyword} power(s)"

    def update_card_state(self, target: int, *, add: int = 0,
                          remove: int = 0,
                          trigger: str | None = None,
                          commit: bool = True) -> int | None:
        """Apply card-state bits, publish ``CardUpdated``, and optionally
        dispatch the authored state-change trigger.

        This deliberately does not choose targets or interpret "each". Those
        are leaf-level orchestration concerns; this method owns only the
        shared persistence and event boundary for one card.
        """
        import game_engine

        target = int(target)
        from pvp_db import db_card_owner_zone_state, db_set_card_state_exact
        row = db_card_owner_zone_state(
            self.session.session_id, target, conn=self.db)
        if not row:
            return None
        # Older lightweight adapters return only (owner, state); the domain
        # helper returns (owner, location, state).
        previous_state = int(row[2] if len(row) > 2 else row[0] or 0)
        row_owner = row[0] if len(row) > 2 else row[1]
        state = (previous_state | int(add or 0)) & ~int(remove or 0)
        db_set_card_state_exact(
            self.session.session_id, target, state, conn=self.db)
        if commit:
            self.db.commit()
        from rules_port.card_projection import push_card_state
        push_card_state(self.game, self.session, self.db, self.handler,
                        self.player_uid, self.ai_uid, target, state,
                        self.bstate)
        if trigger:
            self._emit_trigger(trigger, target, int(row_owner or 0))
        elif (int(remove or 0) & int(game_engine.ECardStates.Tapped)
              and previous_state & int(game_engine.ECardStates.Tapped)):
            self._emit_trigger("CardReadiedEvent", target,
                               int(row_owner or 0))
        return state
