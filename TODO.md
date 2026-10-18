# TODO

## FRA / encounter champion ability audit — remaining divergences

Found while auditing every Frost Ring Arena and encounter champion against the
C# client (`HexClient`). The eight bugs confirmed during that audit were fixed
in `rules_port/`; the items below are the verified divergences that were left
in place because each needs a design decision or a larger change.

### Deliberately left (in scope for FRA/encounter champions)

- [ ] **TransformCard to a ChampionTemplate/MercenaryTemplate is a no-op.**
      `rules_port/transform_effects.py:49` resolves only `card_templates`;
      C# `TransformCardAbilityEffectTemplate.Apply` falls through
      CardTemplate -> ChampionTemplate -> MercenaryTemplate and synthesizes the
      champion's card via `ChampionTemplate.GetCardTemplate()` (keeping the
      target's name). Affects FRA **Tormented Locksmith/Elite** (`9484265f`),
      **Midnight Gatherer Elite** (`78c19418`), encounter **Wormoid Queen**
      (`609f5a87`).
      them into their deck").
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
      only clears stat mods via `db_reset_card_modifiers`; C# `Card.Revert`
      also clears damage, counters, dynamic abilities, damage shields,
      uses/cooldowns, card integer variables, and emits `CardReverted`
      (`rules_port/context.py:2926`).
- [ ] `MoveCardToZoneEffectTemplate`: `m_ControlGivenToTargetIndex=-2` and
      `m_UseSourceDestination` are unhandled (`rules_port/context.py`).
- [ ] `ActivateAbilityEffectTemplate` resolves the child inline instead of
      pushing it on the chain after the parent, so ordering differs when the
      parent has later effects (Rhiannon of Flame, The Nameless Knight,
      Calilac).
- [ ] `TransformSelfAbilityEffectTemplate` non-PlantGarden path does not copy
      the target's gems/boons/counters/damage; `TransformCardToTarget` moves
      the source to the warzone instead of keeping its zone
      (`rules_port/context.py:1418`).
- [ ] `RevealCardsAbilityEffectTemplate` broadcasts `Opponents` reveals
      publicly in PvP (`rules_port/reveal_effects.py`).
- [ ] `StoreTargetsAbilityEffectTemplate` with TAC `StoreInAbility` stores in
      the persisted battle state keyed by ability GUID; C# stores on the
      ability instance and drops it with the activation (needs an explicit
      `ForgetAllCards` today).
- [ ] `tests/tests_leaves.py::test_revert_mods` fixture `make_db()` lacks
      `game_cards.original_template_guid`, so the test errors before exercising
      the leaf.
