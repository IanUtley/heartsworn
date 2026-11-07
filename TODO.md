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
      `rules_port/ir_event_effects.py` (`create_and_cast_spell`) resolves the created spell synchronously
      instead of pushing it onto the chain, has no `CantCreateCopies` check,
      and clamps a typed amount of 0 up to 1; C#
      `CreateAndCastSpellAbilityEffectTemplate.Apply` -> `CopyCardAndPutOnChain`
      gives the copy a normal response window. Encounter
      **Periwinkle/Elite** ("when you play a card with cost 5 or greater,
      copy it").

### Related port gaps surfaced by the same audit (not FRA-champion-specific)

- [ ] `GrantAbilityEffectTemplate`'s random-Inspire pool is not format/rarity
      filtered (C# `GetRandomInspirePower` uses `setsValidInFormat`;
      `rules_port/ir_event_effects.py` (`grant_ability`)).
- [ ] `RegisterTriggerAbilityEffectTemplate` writes
      `bstate["registered_triggers"]` but nothing reads it, so registered
      triggers never fire (C# `Session.RegisterTriggerEventHandler`).
- [x] `GrantAbilityEffectTemplate` caches the random power in
      `bstate["random_power_template"]` for the whole battle instead of per
      activation. Fixed: each activation draws its own power (not yet tested in
      a game).
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
- [ ] `tests/tests_leaves.py::test_revert_mods` fixture `make_db()` lacks
      `game_cards.original_template_guid`, so the test errors before exercising
      the leaf.

## Campaign node OnMove text — investigate

Client shows OnMove text in the node panel only through `Completed_OnMove`
(`hex-private/HexClient/Assembly-CSharp/UICampaignNodeInfoPanel.cs:66-73`),
and only when the node is completed or repeatable. A plain `OnMove` entry is
never requested. About 124 dungeon-template nodes have plain `OnMove` with no
`Completed_OnMove`, including most of AZ1's overworld (Howling Plains: 69 of 85
nodes), so their text should be visible in play but is not.

- [ ] **Confirm in game.** Stand on a visited Howling Plains node and check
      whether the plain OnMove text appears. If it does, the static read is
      wrong somewhere, so find the path that shows it.
- [ ] **Decide the fix if it is confirmed.** The server cannot supply the text.
      Options are a `Completed_OnMove` entry per node in the client gamedata
      (client-side, so out of server scope), or accepting the current display.

Sources: `hex-private/Records/SceneData.jsonl` (Howling Plains and The Smoldering
Dead entries), `campaign.py` (~1110-1150, summary TypeInfo),
`UIDungeonZoneViewModel.cs:1208` (`CollectItemData`).
