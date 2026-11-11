/* The battle state reducer.

   reduceEvent(state, ev) applies one server event to the state and returns the effects the page should perform: sounds,
   float text, catalog wants. It touches no DOM and reads no page globals, so a captured session can be replayed in Node
   or in the browser and always reach the same state. state is the battle part of the page state (see createState).
*/
(function (root) {
  const NO_GUID = "00000000-0000-0000-0000-000000000000";
  const LOG_LIMIT = 12;

  function createState(me) {
    return {
      me, cards: new Map(), players: {}, champions: {}, championTemplates: {}, names: {}, phase: {}, greenlight: {},
      chain: [], options: new Map(), combats: new Map(), decks: {}, ended: false,
      pendingSounds: new Set(), log: [],
    };
  }

  function want(effects, section, guid) {
    if (!guid || guid === NO_GUID) return;
    effects.push({ type: "want", section, guid: guid.toLowerCase() });
  }

  function logLine(state, entry) {
    // Entries name cards when rendered: a card's identity often arrives in a CardUpdated later in the same packet.
    state.log.unshift(entry);
    state.log.length = Math.min(state.log.length, LOG_LIMIT);
  }

  function reduceEvent(state, ev) {
    const fx = [];
    switch (ev.type) {
      case "GameStarted":
        fx.push({ type: "sfx", name: "Aud_FX_Game_Start", group: "game" });
        ev.turn_order.forEach((pid, i) => {
          state.championTemplates[pid] = ev.champion_template_ids[i];
          if (!state.names[pid]) state.names[pid] = ev.champion_names[i];
          want(fx, "cards", ev.champion_template_ids[i]);
        });
        break;
      case "PlayerUpdated":
        state.players[ev.player_id] = ev;
        if (ev.champion_id) state.champions[ev.player_id] = ev.champion_id;
        break;
      case "ChampionCardPlayed":
        state.champions[ev.player_id] = ev.session_card_id;
        if (ev.player_name && !state.names[ev.player_id]) state.names[ev.player_id] = ev.player_name;
        break;
      case "CardUpdated": {
        const prev = state.cards.get(ev.session_card_id);
        const card = { ...ev, uid: ev.session_card_id };
        if (prev && card.card_id === NO_GUID && prev.card_id !== NO_GUID && card.collection === prev.collection) {
          card.card_id = prev.card_id;   // a hidden refresh of a card we saw
        }
        state.cards.set(card.uid, card);
        want(fx, "cards", card.card_id);
        (card.abilities || []).forEach((a) => want(fx, "abilities", a));
        fx.push({ type: "flush_sounds" });
        break;
      }
      case "DeckCreated":
        state.decks[ev.player_id] = { sleeve: ev.deck_sleeve_id, board: ev.gameboard_id, coin: ev.coin_id };
        break;
      case "TurnPhaseUpdated": {
        const was = state.phase.turn_phase;
        state.phase = ev;
        if (ev.turn_phase !== was) fx.push({ type: "phase", phase: ev.turn_phase, active: ev.active_player_id });
        break;
      }
      case "GreenLight":
        state.greenlight[ev.player_id] = ev.context;
        break;
      case "PlayerOptionList":
        if (ev.player_id === state.me) {
          state.options = new Map(ev.options.map((o) => [o.card, o]));
          ev.options.forEach((o) => o.instances.forEach((i) => want(fx, "abilities", i.opt_id)));
        }
        break;
      case "AbilityPushedOnChain":
        if (!ev.ignores_chain) {
          state.chain.push({ id: ev.ability_instance_id, source: ev.source_card_id,
            ability: ev.ability_template_id, targets: ev.target_card_ids });
          want(fx, "abilities", ev.ability_template_id);
        }
        break;
      case "TopOfChainResolved":
      case "RemovedTopOfChain":
      case "AbilityCancelled":
        state.chain = state.chain.filter((c) => c.id !== ev.ability_instance_id);
        break;
      case "ChainEmpty":
        state.chain = [];
        break;
      case "AttackDeclared":
        state.combats.set(ev.attacking_card_id, { attacker: ev.attacking_card_id, defender: ev.defending_card_id, blockers: [] });
        break;
      case "CombatListing":
        state.combats = new Map(ev.combats.map((c) => [c.attacker, { attacker: c.attacker, blockers: c.blockers || [] }]));
        break;
      case "BlockersAssigned": {
        const combat = state.combats.get(ev.attacker_id) || { attacker: ev.attacker_id };
        combat.blockers = ev.blocking_card_ids || [];
        fx.push({ type: "sfx", name: "Aud_FX_Card_Declare_Block", group: "combat" });
        state.combats.set(ev.attacker_id, combat);
        break;
      }
      case "EndCombatResolution":
        state.combats.clear();
        break;
      case "ChampionHealthChanged": {
        const delta = ev.new_damage_value - ev.old_damage_value;
        if (delta > 0) fx.push({ type: "sfx", name: "Aud_FX_Card_Single_Melee_Damage_Medium_01", group: "combat" });
        if (delta < 0) fx.push({ type: "sfx", name: "Aud_FX_Champion_Life_Up", group: "combat" });
        if (delta) fx.push({ type: "float", player: ev.player_id, text: delta > 0 ? `-${delta}` : `+${-delta}`,
          kind: delta > 0 ? "damage" : "heal" });
        break;
      }
      case "TroopCardPlayed": case "SpellCardPlayed": case "ArtifactCardPlayed": case "ResourceCardPlayed":
        logLine(state, { kind: "played", player: ev.player_id, card: ev.session_card_id });
        state.pendingSounds.add(ev.session_card_id);
        fx.push({ type: "flush_sounds" });
        break;
      case "CardDestroyed":
        logLine(state, { kind: "destroyed", card: ev.session_card_id });
        break;
      case "GameEnded":
        state.ended = true;
        break;
      default:
        break;
    }
    return fx;
  }

  const api = { NO_GUID, createState, reduceEvent };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.BattleState = api;
})(typeof window !== "undefined" ? window : globalThis);
