# TODO

## FRA / encounter champion ability audit — remaining divergences

Found while auditing every Frost Ring Arena and encounter champion against the
C# client (`HexClient`). The eight bugs confirmed during that audit were fixed
in `rules_port/`; the items below are the verified divergences that were left
in place because each needs a design decision or a larger change.

### Deliberately left (in scope for FRA/encounter champions)

- [ ] **TransformCard to a MercenaryTemplate is a no-op.** Champion targets
      now work (`pvp_db.db_copy_template_payload` falls back to the
      champion's ability list), but C#
      `TransformCardAbilityEffectTemplate.Apply` also falls through to
      MercenaryTemplate, which the port does not resolve.
- [ ] **CreateAndCastSpell resolves the copy inline and ignores locks.**
      `rules_port/effects.py:436` resolves the created spell synchronously
      instead of pushing it onto the chain, has no `CantCreateCopies` check,
      and clamps a typed amount of 0 up to 1; C#
      `CreateAndCastSpellAbilityEffectTemplate.Apply` -> `CopyCardAndPutOnChain`
      gives the copy a normal response window. Encounter
      **Periwinkle/Elite** ("when you play a card with cost 5 or greater,
      copy it").

### Related port gaps surfaced by the same audit (not FRA-champion-specific)

- [ ] `GrantAbilityEffectTemplate`'s random-Inspire pool is not format/rarity
      filtered (C# `GetRandomInspirePower` uses `setsValidInFormat`;
      `rules_port/effects.py` ~243).
- [ ] `RevertPermanentModificationsAbilityEffectTemplate` non-transform path
      (`rules_port/context.py` `revert_modifications`) now clears counters,
      escalation, card variables, damage shields, damage, uses/cooldowns, and
      restores authored abilities, and emits `CardReverted`. Still open vs C#
      `Card.Revert`: `RemovePermanentAndTargetedAbilities`/`RemoveCardCreatedAbilities`
      and trigger unregistration are not run; `bstate["card_integer_variables"]`
      is keyed by name only, so a revert cannot clear one card's values there;
      `IntAttrs.Charges`/`ChargePointsSpent`/`ChargePointsGained` are not reset.
- [ ] `MoveCardToZoneEffectTemplate`: `m_ControlGivenToTargetIndex=-2` and
      `m_UseSourceDestination` are unhandled (`rules_port/context.py`).
- [ ] `ActivateAbilityEffectTemplate` resolves the child inline instead of
      pushing it on the chain after the parent, so ordering differs when the
      parent has later effects (Rhiannon of Flame, The Nameless Knight,
      Calilac).
- [ ] `StoreTargetsAbilityEffectTemplate` with TAC `StoreInAbility` stores in
      the persisted battle state keyed by ability GUID; C# stores on the
      ability instance and drops it with the activation (needs an explicit
      `ForgetAllCards` today).
