"use strict";
/* Heartsworn browser battle client.
 *
 * The server sends the same session events the Unity client receives
 * (CardUpdated, PlayerUpdated, TurnPhaseUpdated, PlayerOptionList, ...) as
 * JSON packets. This file keeps a client state cache built only from those
 * events, renders the battle board from it, and turns clicks into the
 * decisions the server's current prompt allows.
 */

const PLAY_ABILITY = "5a8783b0-e420-4f41-b2a1-96f70b0cd851";
const NO_GUID = "00000000-0000-0000-0000-000000000000";
const COLL = { Deck: 1, Hand: 2, Champions: 4, Warzone: 8, Discard: 16, Void: 32,
  PlayedResources: 64, CastSpells: 128, Underground: 256, Choosing: 512 };
const CSTATE = { Tapped: 1, Blocking: 2, Attacking: 4, Dead: 64 };
const USAGE = { Play: 1, Activate: 2, Attack: 4, Defend: 8 };
const CTYPE = { Champion: 1, Troop: 2, BasicAction: 8, Resource: 16, Artifact: 32, QuickAction: 64, Constant: 2048 };
const SHARD_NAMES = ["Colorless", "Blood", "Ruby", "Sapphire", "Wild", "Diamond"];
const KEYWORDS = [[2, "Flight"], [4, "Speed"], [1, "Lifedrain"], [16384, "Swiftstrike"],
  [128, "Spellshield"], [32, "Steadfast"], [16, "Crush"], [64, "Invincible"], [1048576, "Double Strike"],
  [32768, "Rage"], [2048, "Defensive"], [8, "Skyguard"], [131072, "Unblockable"], [65536, "Must Block"],
  [512, "Can't Attack"], [1024, "Can't Block"], [16777216, "Inspire"], [33554432, "Escalation"]];
const PHASE_NAMES = { 3: "Choose play/draw", 4: "Mulligan", 5: "Start", 6: "Start turn", 7: "Ready",
  8: "Prep", 9: "Draw", 10: "First main", 11: "Before combat", 12: "Declare attack", 13: "Attack response",
  14: "Declare blockers", 15: "Block response", 16: "Swiftstrike damage", 17: "Swiftstrike response",
  18: "Combat damage", 19: "Second main", 20: "End phase", 21: "Discard", 22: "End turn", 24: "Game over" };
const PHASE_TRACK = [[7, "Ready"], [9, "Draw"], [10, "Main"], [12, "Attack"], [14, "Block"],
  [18, "Damage"], [19, "Main 2"], [20, "End"]];
const PASS_LABELS = { 2: "Ready", 4: "Continue to combat", 6: "Resolve", 7: "Continue to second main",
  9: "End turn", 11: "End turn", 14: "Resolve combat", 15: "Pass", 16: "Pass" };
const SLEEVES = ["classic", "blood", "ruby", "sapphire", "wild", "diamond", "cosmic"];
// The battleboards are the AssetBundles/gameboards/*.ab files in the Hex folder.
// The lobby fills this list once the folder is read.
let boards = [];
const boardLabel = (key) => key.charAt(0).toUpperCase() + key.slice(1).replace(/_/g, " ");
const AXE = `<svg viewBox="0 0 32 32" width="38" height="38"><path fill="currentColor" d="M15 3h2v27h-2z"/><path fill="currentColor" d="M17 7c5 0 9-2 11-4 1 5 1 13-1 18-2-2-6-3.5-10-3.5zM15 7C10 7 6 5 4 3c-1 5-1 13 1 18 2-2 6-3.5 10-3.5z"/></svg>`;
const HAND_W = 112, HAND_H = 157;   // --hand-w / --hand-h

const $ = (sel) => document.querySelector(sel);
const boardChoice = () => { const b = storage("hex.board"); return boards.includes(b) ? b : (boards[0] || "basic"); };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function storage(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch (e) { /* private window */ }
  return null;
}

/* ------------------------------------------------------------------ */
/* State cache                                                         */
/* ------------------------------------------------------------------ */

const S = {
  matchId: null, info: null, after: 0, me: 0, opp: 0,
  cards: new Map(), players: {}, champions: {}, championTemplates: {}, names: {},
  phase: {}, greenlight: {}, chain: [], options: new Map(), combats: new Map(), decks: {},
  prompt: null, busy: true, error: null, ended: false,
  ui: { mode: null }, catalog: { cards: {}, abilities: {} }, wanted: { cards: new Set(), abilities: new Set() },
  log: [], prevLife: {}, rendered: new Set(), flying: new Set(), lastChain: 0,
};

function resetState() {
  Object.assign(S, {
    after: 0, cards: new Map(), players: {}, champions: {}, championTemplates: {}, names: {},
    phase: {}, greenlight: {}, chain: [], options: new Map(), combats: new Map(), decks: {},
    prompt: null, busy: true, error: null, ended: false, ui: { mode: null }, log: [], prevLife: {},
    rendered: new Set(), flying: new Set(), lastChain: 0,
  });
  pendingSounds.clear();
}

function want(section, guid) {
  if (!guid || guid === NO_GUID) return;
  guid = guid.toLowerCase();
  if (!(guid in S.catalog[section])) S.wanted[section].add(guid);
}

function applyEvent(ev) {
  switch (ev.type) {
    case "GameStarted":
      ev.turn_order.forEach((pid, i) => {
        S.championTemplates[pid] = ev.champion_template_ids[i];
        if (!S.names[pid]) S.names[pid] = ev.champion_names[i];
        want("cards", ev.champion_template_ids[i]);
      });
      break;
    case "PlayerUpdated":
      S.players[ev.player_id] = ev;
      if (ev.champion_id) S.champions[ev.player_id] = ev.champion_id;
      break;
    case "ChampionCardPlayed":
      S.champions[ev.player_id] = ev.session_card_id;
      if (ev.player_name && !S.names[ev.player_id]) S.names[ev.player_id] = ev.player_name;
      break;
    case "CardUpdated": {
      const prev = S.cards.get(ev.session_card_id);
      const card = { ...ev, uid: ev.session_card_id };
      if (prev && card.card_id === NO_GUID && prev.card_id !== NO_GUID &&
          card.collection === prev.collection) {
        card.card_id = prev.card_id;   // a hidden refresh of a card we saw
      }
      S.cards.set(card.uid, card);
      want("cards", card.card_id);
      (card.abilities || []).forEach((a) => want("abilities", a));
      flushCardSounds();
      break;
    }
    case "DeckCreated":
      S.decks[ev.player_id] = { sleeve: ev.deck_sleeve_id, board: ev.gameboard_id, coin: ev.coin_id };
      break;
    case "TurnPhaseUpdated":
      S.phase = ev;
      break;
    case "GreenLight":
      S.greenlight[ev.player_id] = ev.context;
      break;
    case "PlayerOptionList":
      if (ev.player_id === S.me) {
        S.options = new Map(ev.options.map((o) => [o.card, o]));
        ev.options.forEach((o) => o.instances.forEach((i) => want("abilities", i.opt_id)));
      }
      break;
    case "AbilityPushedOnChain":
      if (!ev.ignores_chain) {
        S.chain.push({ id: ev.ability_instance_id, source: ev.source_card_id,
          ability: ev.ability_template_id, targets: ev.target_card_ids });
        want("abilities", ev.ability_template_id);
      }
      break;
    case "TopOfChainResolved":
    case "RemovedTopOfChain":
    case "AbilityCancelled":
      S.chain = S.chain.filter((c) => c.id !== ev.ability_instance_id);
      break;
    case "ChainEmpty":
      S.chain = [];
      break;
    case "AttackDeclared":
      S.combats.set(ev.attacking_card_id, { attacker: ev.attacking_card_id, defender: ev.defending_card_id, blockers: [] });
      break;
    case "CombatListing":
      S.combats = new Map(ev.combats.map((c) => [c.attacker, { attacker: c.attacker, blockers: c.blockers || [] }]));
      break;
    case "BlockersAssigned": {
      const combat = S.combats.get(ev.attacker_id) || { attacker: ev.attacker_id };
      combat.blockers = ev.blocking_card_ids || [];
      S.combats.set(ev.attacker_id, combat);
      break;
    }
    case "EndCombatResolution":
      S.combats.clear();
      break;
    case "ChampionHealthChanged": {
      const delta = ev.new_damage_value - ev.old_damage_value;
      if (delta) floatAtChampion(ev.player_id, delta > 0 ? `-${delta}` : `+${-delta}`, delta > 0 ? "damage" : "heal");
      break;
    }
    case "TroopCardPlayed": case "SpellCardPlayed": case "ArtifactCardPlayed": case "ResourceCardPlayed":
      logLine((names) => `${who(ev.player_id)} played ${names(ev.session_card_id)}`);
      pendingSounds.add(ev.session_card_id);
      flushCardSounds();
      break;
    case "CardDestroyed":
      logLine((names) => `${names(ev.session_card_id)} was destroyed`);
      break;
    case "GameEnded":
      S.ended = true;
      break;
    default:
      break;
  }
}

function who(pid) { return pid === S.me ? "You" : (S.names[pid] || "Opponent"); }
function cardName(uid) {
  const c = S.cards.get(uid);
  const t = c && S.catalog.cards[(c.card_id || "").toLowerCase()];
  return t ? t.name : "a card";
}
function logLine(line) {
  // Lines name cards when rendered: the card's identity often arrives in a
  // CardUpdated later in the same packet.
  S.log.unshift(line);
  S.log.length = Math.min(S.log.length, 12);
}

/* ------------------------------------------------------------------ */
/* Server                                                              */
/* ------------------------------------------------------------------ */

async function api(path, body, method) {
  const res = await fetch(path, {
    method: method || (body ? "POST" : "GET"),
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

let polling = false;
async function poll() {
  if (polling) return;
  polling = true;
  try {
    while (S.matchId) {
      const id = S.matchId;
      const snap = await api(`/api/matches/${id}?after=${S.after}&wait=20`);
      if (id !== S.matchId) break;
      S.info = snap.match;
      for (const packet of snap.packets) {
        S.after = packet.seq;
        (packet.events || []).forEach(applyEvent);
        if (packet.notice) toast(packet.notice);
      }
      S.busy = snap.busy;
      S.prompt = snap.prompt;
      if (snap.error && snap.error !== S.error) {
        S.error = snap.error;
        if (snap.error.includes("Traceback")) showError(snap.error); else toast(snap.error);
      }
      await loadCatalog();
      if (!S.busy) S.ui = freshUi(S.ui);
      render();
      if (!snap.busy && !snap.packets.length) break;
      if (!snap.busy) continue;   // drain any packets published with the prompt
    }
  } catch (err) {
    toast(String(err.message || err));
  } finally {
    polling = false;
  }
}

async function act(action) {
  S.ui = { mode: null };
  S.busy = true;
  render();
  try {
    await api(`/api/matches/${S.matchId}/action`, action);
  } catch (err) {
    toast(err.message);
  }
  poll();
}

async function loadCatalog() {
  const cards = [...S.wanted.cards], abilities = [...S.wanted.abilities];
  if (!cards.length && !abilities.length) return;
  S.wanted.cards.clear(); S.wanted.abilities.clear();
  try {
    const found = await api("/api/catalog", { cards, abilities });
    cards.forEach((g) => { S.catalog.cards[g] = found.cards[g] || null; });
    abilities.forEach((g) => { S.catalog.abilities[g] = found.abilities[g] || null; });
  } catch (err) { /* names fill in on the next packet */ }
  flushCardSounds();
}

/* ------------------------------------------------------------------ */
/* Card text and frames                                                */
/* ------------------------------------------------------------------ */

function gameText(raw) {
  let s = esc(raw || "");
  s = s.replace(/&lt;p&gt;/gi, "<br>").replace(/&lt;br\s*\/?&gt;/gi, "<br>")
    .replace(/&lt;(\/?)(b|i)&gt;/gi, "<$1$2>")
    .replace(/&lt;\/?[a-z][^&]*?&gt;/gi, "");
  s = s.replace(/\[(BLOOD|RUBY|SAPPHIRE|WILD|DIAMOND|COLORLESS)\]/g, (_, g) => `<span class="gem gem-${g.toLowerCase()}"></span>`)
    .replace(/\[\(?(\d+|X)\)?\]/g, (_, n) => `<span class="cost-icon">${n}</span>`)
    .replace(/\[ARROWR\]/g, "&rarr;").replace(/\[BASIC\]/g, "<b>Basic</b>")
    .replace(/\[(ATTACK|ATK)\]/g, "&#9876;").replace(/\[(DEFENSE|DEF)\]/g, "&#128737;")
    .replace(/\[EXHAUST\]/g, "&#10549;").replace(/\[CHARGE\]/g, "&#9889;")
    .replace(/\[([A-Z]+)\]/g, (_, w) => w.charAt(0) + w.slice(1).toLowerCase());
  return s.replace(/(<br>){3,}/g, "<br><br>");
}

function shardClass(tpl) {
  const colors = Object.keys((tpl && tpl.thresholds) || {}).filter((c) => c !== "Colorless");
  if (!colors.length && tpl && tpl.type === "Resource") {
    const m = /(Blood|Ruby|Sapphire|Wild|Diamond)/.exec(tpl.name || "");
    if (m) return `shard-${m[1].toLowerCase()}`;
  }
  if (colors.length > 1) return "shard-multi";
  return colors.length ? `shard-${colors[0].toLowerCase()}` : "";
}

const TYPE_GLYPH = { Troop: "&#9876;", Resource: "&#9670;", Artifact: "&#9881;", Constant: "&#8734;",
  BasicAction: "&#10022;", QuickAction: "&#9889;", Champion: "&#9819;" };

const TYPE_CHAR = { Troop: "\u2694", Resource: "\u25c6", Artifact: "\u2699", Constant: "\u221e",
  BasicAction: "\u2726", QuickAction: "\u26a1", Champion: "\u265b" };
const SHARD_HEX = { blood: "#9b4dd1", ruby: "#e0473a", sapphire: "#3a8ae0", wild: "#45a852", diamond: "#e9dca6", multi: "#c9a14a" };

// Card art decoded from the player's client install (hex_assets.js). It is
// null until the decode finishes, and then the card is drawn again.
function artUrl(tpl) {
  if (!tpl || !/^a\d{7}$/.test(tpl.art || "") || !window.HexAssets) return null;
  return HexAssets.artUrl(tpl.art, () => render());
}

function artStyle(tpl) {
  const url = artUrl(tpl);
  return url ? ` style="background-image:url('${url}')"` : "";
}

function thresholdGems(tpl) {
  const out = [];
  for (const [color, n] of Object.entries((tpl && tpl.thresholds) || {})) {
    for (let i = 0; i < n; i++) out.push(`<span class="gem gem-${color.toLowerCase()}"></span>`);
  }
  return out.join("");
}

function keywordsOf(card) {
  return KEYWORDS.filter(([flag]) => (card.attributes & flag) !== 0).map(([, name]) => name);
}

function freshClass(card) {
  if (typeof card.uid === "string") return "";   // preview / flying copies
  const key = `${card.uid}:${card.collection}`;
  if (S.rendered.has(key)) return "";
  S.rendered.add(key);
  return "fresh";
}

function cardHTML(card, extra = "", style = "") {
  const tpl = S.catalog.cards[(card.card_id || "").toLowerCase()];
  const hidden = !card.card_id || card.card_id === NO_GUID;
  const styleAttr = style ? ` style="${style}"` : "";
  if (hidden) return `<div class="card back ${sleeveOf(card.controller)} ${freshClass(card)} ${extra}" data-uid="${card.uid}"${styleAttr}></div>`;
  const classes = ["card", shardClass(tpl), freshClass(card), extra];
  // Basic resources render frameless: just the shard art, no card chrome.
  if (tpl && tpl.type === "Resource") classes.push("resource");
  if (card.state & CSTATE.Tapped) classes.push("tapped");
  if (card.state & CSTATE.Attacking || S.combats.has(card.uid)) classes.push("attacking");
  if (card.state & CSTATE.Blocking) classes.push("blocking");
  const name = tpl ? tpl.name : "…";
  const typeName = tpl ? [tpl.type, card.sub_type || tpl.subtype].filter(Boolean).join(" — ") : "";
  const showStats = (card.card_type & CTYPE.Troop) || (tpl && tpl.type === "Troop");
  const baseAtk = tpl ? tpl.attack : card.attack, baseDef = tpl ? tpl.defense : card.defense;
  const atkCls = card.attack > baseAtk ? "buffed" : "";
  const defCls = card.defense < baseDef ? "hurt" : (card.defense > baseDef ? "buffed" : "");
  const kws = keywordsOf(card).slice(0, 3).map((k) => `<span class="kw">${k}</span>`).join("");
  const counters = (card.counter_counts || []).map((n) => n > 0 ? `<span class="counter">${n}</span>` : "").join("");
  const cost = tpl && tpl.type !== "Resource" ? `<div class="cost">${card.collection === COLL.Hand ? card.cost : (tpl ? tpl.cost : card.cost)}</div>` : "";
  const text = tpl ? `<div class="text-box">${gameText(tpl.text)}${tpl.flavor ? `<div class="flavor">${esc(tpl.flavor)}</div>` : ""}</div>` : "";
  const faction = Object.keys((tpl && tpl.thresholds) || {}).find((color) => color !== "Colorless") || "Colorless";
  const rarity = tpl && tpl.rarity ? tpl.rarity : "Common";
  return `<div class="${classes.join(" ")}" data-uid="${card.uid}"${styleAttr}>
    ${cost}<div class="name">${esc(name)}</div>
    <div class="art"${artStyle(tpl)}>${artStyle(tpl) ? "" : (TYPE_GLYPH[tpl && tpl.type] || "")}</div>
    <div class="thresholds">${thresholdGems(tpl)}</div>
    <div class="type-bar"><span class="faction faction-${faction.toLowerCase()}" title="${esc(faction)}">${faction === "Colorless" ? "◇" : "✦"}</span><div class="type-line">${esc(typeName)}</div><span class="rarity rarity-${rarity.toLowerCase()}" title="${esc(rarity)}">${rarity === "Legendary" ? "✦" : rarity === "Rare" ? "◆" : rarity === "Uncommon" ? "●" : "○"}</span></div>
    ${text}<div class="keywords">${kws}</div><div class="counters">${counters}</div>
    ${showStats ? `<div class="stats"><span class="stat atk ${atkCls}">${card.attack}</span><span class="stat def ${defCls}">${card.defense}</span></div>` : ""}
  </div>`;
}

// Hand, preview and mulligan cards show the card as the board paints it (board3d.js
// cardImage), so they match the cards on the board. The state classes keep their glow.
function faceHTML(card, extra = "", style = "") {
  const styleAttr = style ? ` style="${style}"` : "";
  const classes = ["card", "painted", freshClass(card), extra];
  if (card.state & CSTATE.Tapped) classes.push("tapped");
  if (card.state & CSTATE.Attacking || S.combats.has(card.uid)) classes.push("attacking");
  if (card.state & CSTATE.Blocking) classes.push("blocking");
  const src = window.Board3D ? window.Board3D.cardImage(faceOf(card), () => render()) : "";
  return `<div class="${classes.join(" ")}" data-uid="${card.uid}"${styleAttr}><img src="${src}" alt=""></div>`;
}

function hashIndex(guid, n) {
  let h = 0;
  for (const ch of String(guid || "")) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return h % n;
}
function sleeveOf(pid) {
  if (pid === S.me) return `sleeve-${storage("hex.sleeve") || "classic"}`;
  const deck = S.decks[pid];
  return `sleeve-${SLEEVES[hashIndex(deck && deck.sleeve, SLEEVES.length)]}`;
}

/* ------------------------------------------------------------------ */
/* Rendering                                                           */
/* ------------------------------------------------------------------ */

function cardsIn(pid, collection) {
  const champs = new Set(Object.values(S.champions));
  return [...S.cards.values()]
    .filter((c) => c.controller === pid && c.collection === collection && !champs.has(c.uid))
    .sort((a, b) => a.uid - b.uid);
}

/* ------------------------------------------------------------------ */
/* 3D board                                                            */
/* ------------------------------------------------------------------ */

function board3d() {
  // The three.js board (board3d.js) draws the arena, warzones and chain when
  // WebGL is available; otherwise the flat HTML board below is used.
  if (S.board3d === undefined && window.Board3D) {
    S.board3d = new URLSearchParams(location.search).has("flat") ? null : Board3D.create($("#board3d"), {
      onClick: (uid) => {
        if (S.ui.mode === "modal") { S.ui = { mode: null }; $("#modal").classList.add("hidden"); }
        clickCard(uid);
      },
      onHover: (uid) => { if (uid) showPreview(uid); else $("#preview").innerHTML = ""; },
    });
    document.body.classList.toggle("webgl", !!S.board3d);
  }
  return S.board3d || null;
}

function faceOf(card) {
  const tpl = S.catalog.cards[(card.card_id || "").toLowerCase()];
  if (!tpl || card.card_id === NO_GUID) return { hidden: true, sleeve: sleeveOf(card.controller).slice("sleeve-".length) };
  const baseAtk = tpl.attack ?? card.attack, baseDef = tpl.defense ?? card.defense;
  const cost = card.collection === COLL.Hand ? card.cost : tpl.cost;
  return {
    name: tpl.name,
    cost: tpl.type === "Resource" || tpl.kind === "champion" ? "" : (tpl.variable && !cost ? tpl.variable : `${cost}${tpl.variable || ""}`),
    // The client tints a changed cost: red when raised, green when lowered.
    costCls: cost > tpl.cost ? "hurt" : (cost < tpl.cost ? "buffed" : ""),
    type: [tpl.type, card.sub_type || tpl.subtype].filter(Boolean).join(" \u2014 "),
    types: tpl.types || String(tpl.type || "").split("|").filter(Boolean),
    subtype: card.sub_type || tpl.subtype || "",
    rarity: tpl.rarity || "Common",
    faction: Object.keys(tpl.thresholds || {}).find((color) => color !== "Colorless") || "Colorless",
    house: tpl.faction || "None",
    set: tpl.set || 0, pve: !!tpl.pve, immortal: !!tpl.immortal,
    unique: !!tpl.unique, unlimited: !!tpl.unlimited,
    resources: tpl.resources || null, sockets: tpl.sockets || 0, flavor: tpl.flavor || "",
    thresholds: tpl.thresholds || {}, text: tpl.text || "",
    shard: SHARD_HEX[shardClass(tpl).replace("shard-", "")] || "#8d939c",
    glyph: TYPE_CHAR[tpl.type] || "",
    art: artUrl(tpl),
    keywords: keywordsOf(card).slice(0, 3),
    counters: (card.counter_counts || []).reduce((a, b) => a + b, 0),
    showStats: !!((card.card_type & CTYPE.Troop) || tpl.type === "Troop"),
    attack: card.attack, defense: card.defense,
    atkCls: card.attack > baseAtk ? "buffed" : "",
    defCls: card.defense < baseDef ? "hurt" : (card.defense > baseDef ? "buffed" : ""),
  };
}

function boardEntry(card, zone, index, count, plan) {
  let highlight = interactive(card.uid);
  if (!highlight && (card.state & CSTATE.Attacking || S.combats.has(card.uid))) highlight = "attacking";
  if (!highlight && card.state & CSTATE.Blocking) highlight = "blocking";
  return { uid: card.uid, zone, index, count, highlight, hidden: S.flying.has(card.uid),
    tapped: zone !== "chain" && !!(card.state & CSTATE.Tapped),
    attacking: plan.attackers.has(card.uid), blocking: plan.blockers.has(card.uid),
    face: faceOf(card) };
}

// The combat as the board draws it: every declared attacker with the blockers
// assigned to it, plus the attack or block the player is still choosing.
function combatPlan() {
  const attackers = new Map();   // attacker uid -> [blocker uid]
  for (const [uid, combat] of S.combats) attackers.set(uid, [...(combat.blockers || [])]);
  const ui = S.ui;
  if (ui.mode === "attack") for (const uid of ui.chosen) if (!attackers.has(uid)) attackers.set(uid, []);
  if (ui.mode === "defend") {
    for (const uid of (S.prompt && S.prompt.attackers) || []) if (!attackers.has(uid)) attackers.set(uid, []);
    for (const [attacker, blockers] of Object.entries(ui.blocks)) {
      const uid = Number(attacker);
      attackers.set(uid, [...new Set([...(attackers.get(uid) || []), ...blockers])]);
    }
  }
  const blockers = new Set([...attackers.values()].flat());
  return { attackers: new Set(attackers.keys()), blockers, plan: attackers };
}

// Card-play sounds. The gamedata names each card's clip (catalog `sound`). An
// opponent's card may still be hidden when it is played, so its sound waits for
// the identity the server sends once the card leaves the hand.
const pendingSounds = new Set();
function flushCardSounds() {
  for (const uid of [...pendingSounds]) {
    const card = S.cards.get(uid);
    if (!card || card.card_id === NO_GUID) continue;
    const tpl = S.catalog.cards[card.card_id];
    if (tpl === undefined) continue;               // the catalog entry is still on its way
    pendingSounds.delete(uid);
    if (tpl && tpl.sound && window.HexAssets) HexAssets.playClip(tpl.art, tpl.sound);
  }
}

// The lobby's battleboard, from the client install or private storage.
function syncGameboard(b) {
  const key = boardChoice();
  if (b.gameboardKey === key || !window.HexAssets) return;
  b.gameboardKey = key;
  HexAssets.gameboard(key).then((board) => { if (board) b.setGameboard(board); });
}

function renderBoard3d(b) {
  syncGameboard(b);
  b.setCombat(document.body.dataset.combat === "1");
  b.setPhase(S.phase.turn_phase);
  const entries = [];
  const plan = combatPlan();
  for (const [pid, zone] of [[S.opp, "opp"], [S.me, "mine"]]) {
    const war = [...cardsIn(pid, COLL.Warzone), ...cardsIn(pid, COLL.Underground)];
    war.forEach((c, i) => entries.push(boardEntry(c, zone, i, war.length, plan)));
  }
  S.chain.forEach((item, i) => {
    const src = S.cards.get(item.source) || { uid: item.source, controller: 0, card_id: NO_GUID };
    const entry = { ...boardEntry(src, "chain", i, S.chain.length, plan), top: i === S.chain.length - 1 };
    if (entry.face.hidden) {
      const ability = S.catalog.abilities[(item.ability || "").toLowerCase()];
      entry.face = { name: ability ? ability.name : "Ability", cost: "", type: "Ability", shard: "#7a6ca8", glyph: "\u2726" };
    }
    entries.push(entry);
  });
  b.update(entries);
  b.setArcs(boardArcs(plan));
}

function portrait(pid) {
  return document.querySelector(pid === S.me ? "#my-champion .portrait" : "#opp-champion .portrait");
}

// The portrait of the champion a uid names, or the uid itself for a card.
function arcEnd(uid) {
  const owner = Object.entries(S.champions).find(([, champion]) => champion === uid);
  return owner ? portrait(Number(owner[0])) : uid;
}

// Attack and block arcs for the declared combat, and gold arcs for the chain's targets.
// A blocked attacker draws no arc to the champion: its blockers' red arcs stand instead.
function boardArcs(plan) {
  const arcs = [];
  for (const [attacker, blockers] of plan.plan) {
    if (!blockers.length) {
      const controller = (S.cards.get(attacker) || {}).controller;
      arcs.push({ from: attacker, to: portrait(controller === S.me ? S.opp : S.me), kind: "attack" });
    }
    for (const blocker of blockers) arcs.push({ from: blocker, to: attacker, kind: "block" });
  }
  for (const item of S.chain) {
    for (const target of item.targets || []) arcs.push({ from: item.source, to: arcEnd(target), kind: "target" });
  }
  return arcs;
}

// Golden outline for the chain's targets wherever they show as cards in a panel.
function markChainTargets() {
  const targets = new Set(S.chain.flatMap((item) => item.targets || []));
  document.querySelectorAll("#modal [data-uid], #board [data-uid]").forEach((el) => {
    el.classList.toggle("chain-target", targets.has(Number(el.dataset.uid)));
  });
}

function render() {
  if (!S.info) return;
  const before = handRects();
  const phase = S.phase.turn_phase;
  document.body.dataset.board = boardChoice();
  document.body.dataset.combat = phase >= 11 && phase <= 18 ? "1" : "";
  document.body.classList.toggle("chain-active", S.chain.length > 0);
  renderChampion(S.opp, "#opp-champion");
  renderChampion(S.me, "#my-champion");
  renderHands();
  renderZones();
  renderPiles(S.opp, "#opp-piles");
  renderPiles(S.me, "#my-piles");
  renderChain();
  renderPhases();
  renderActions();
  renderModal();
  markChainTargets();
  const b = board3d();
  if (b) renderBoard3d(b);
  $("#log").innerHTML = S.log.map((l) => `<div>${esc(l(cardName))}</div>`).join("");
  animateMoves(before);
  hideFlying();
}

function interactive(uid) {
  const ui = S.ui;
  if (ui.mode === "target") {
    const step = ui.steps[ui.step];
    const picked = (ui.picked[step.key] || []).includes(uid);
    if (picked) return "selected";
    return step.candidates.has(uid) ? "selectable" : "";
  }
  if (ui.mode === "attack") {
    if (ui.chosen.has(uid)) return "chosen-attacker";
    const o = S.options.get(uid);
    return o && (o.state & USAGE.Attack) ? "selectable" : "";
  }
  if (ui.mode === "defend") {
    if (ui.blocker === uid) return "selected";
    const blocked = Object.values(ui.blocks).some((b) => b.includes(uid));
    if (blocked) return "blocking";
    const o = S.options.get(uid);
    if (o && (o.state & USAGE.Defend)) return "selectable";
    if (ui.blocker && S.prompt && (S.prompt.attackers || []).includes(uid)) return "selectable";
    return "";
  }
  if (S.prompt && S.prompt.kind === "choose") {
    return (S.prompt.candidates || []).includes(uid) ? "selectable" : "";
  }
  if (S.prompt && S.prompt.kind === "priority" && !S.busy) {
    const o = S.options.get(uid);
    if (o && o.instances.length && (o.state & (USAGE.Play | USAGE.Activate))) return "playable";
  }
  return "";
}

function renderChampion(pid, sel) {
  const el = $(sel);
  const uid = S.champions[pid];
  const champCard = uid ? S.cards.get(uid) : null;
  // A hidden opposing champion reports NO_GUID as its card, so fall back to the
  // template the game started with (its art and name come from that catalog entry).
  const shownGuid = champCard && champCard.card_id !== NO_GUID ? champCard.card_id : null;
  const tplGuid = shownGuid || S.championTemplates[pid];
  const tpl = S.catalog.cards[(tplGuid || "").toLowerCase()];
  const p = S.players[pid] || {};
  const life = p.health ?? (tpl ? tpl.health : 20);
  const prev = S.prevLife[pid];
  S.prevLife[pid] = life;
  const thresholds = (p.threshold_values || []).map((n, i) => n > 0
    ? `<span title="${SHARD_NAMES[i]} threshold">${`<span class="gem gem-${SHARD_NAMES[i].toLowerCase()}"></span>`.repeat(n)}</span>` : "").join("");
  const active = S.phase.active_player_id === pid ? "active" : "";
  const priority = S.phase.priority_player_id === pid && !S.ended ? "priority" : "";
  const name = tpl ? tpl.name : "Champion";
  const words = name.split(/\s+/).filter((w) => /^[A-Z]/.test(w));
  const initials = (words[0] || name)[0] + (words.length > 1 ? words[words.length - 1][0] : "");
  const portraitCls = uid ? interactive(uid) : "";
  const powers = (champCard ? champCard.abilities : []).map((g) => {
    const a = S.catalog.abilities[(g || "").toLowerCase()];
    if (!a || !a.manual) return "";
    const ready = pid === S.me && activatablePower(uid, g) ? "ready" : "";
    const cost = a.costs || {};
    const price = cost.charge_points ? `&#9889;${cost.charge_points}` : (cost.activation ? `<span class="cost-icon">${cost.activation}</span>` : "");
    return `<button class="power ${ready}" data-power="${esc(g)}" data-uid="${uid}" title="${esc(a.text.replace(/<[^>]+>/g, ""))}">${price} ${gameText(a.text).split("<br>")[0].replace(/^.*?&rarr;\s*/, "")}</button>`;
  }).join("");
  el.innerHTML = `<div class="champion ${active} ${priority}" style="--shard:${shardColor(tpl)}">
    <div class="portrait ${portraitCls}" data-uid="${uid || ""}"${artStyle(tpl)}>${artStyle(tpl) ? "" : esc(initials)}</div>
    <div class="life ${prev !== undefined && prev !== life ? "hit" : ""}">${life}</div>
    <div class="champ-name">${esc(name)}</div>
    <div class="player-name">${esc(pid === S.me ? (S.names[pid] || "You") : "Server AI")}</div>
    <div class="champ-stats">
      <div class="pill" title="Resources available / total"><span>&#9670;</span><b>${p.resources ?? 0}/${p.total_resources ?? 0}</b></div>
      <div class="pill" title="Charges"><span>&#9889;</span><b>${p.charges ?? 0}</b></div>
    </div>
    <div class="thresholds-row">${thresholds}</div>
    <div class="champ-powers">${powers}</div>
  </div>`;
}

function shardColor(tpl) {
  const c = Object.keys((tpl && tpl.thresholds) || {})[0];
  return c ? `var(--${c.toLowerCase()})` : "#7a6ca8";
}

function fan(cards, sign) {
  // A hand fans around a point below (or above) the screen edge.
  const spread = Math.min(3.5, 30 / Math.max(cards.length, 1));
  return cards.map((c, i) => {
    const off = i - (cards.length - 1) / 2;
    const style = `--r:${(sign * off * spread).toFixed(2)}deg;--y:${(sign * off * off * spread * 0.6).toFixed(1)}px`;
    return faceHTML(c, interactive(c.uid), style);
  }).join("");
}

function renderHands() {
  const opp = cardsIn(S.opp, COLL.Hand);
  $("#opp-hand").innerHTML = fan(opp, -1) + `<span class="hand-count">${opp.length} in hand</span>`;
  $("#my-hand").innerHTML = fan(cardsIn(S.me, COLL.Hand), 1);
}

function renderZones() {
  for (const [pid, prefix] of [[S.opp, "opp"], [S.me, "my"]]) {
    const war = [...cardsIn(pid, COLL.Warzone), ...cardsIn(pid, COLL.Underground)];
    $(`#${prefix}-warzone`).innerHTML = war.map((c) => cardHTML(c, interactive(c.uid))).join("");
    // PlayedResources is a hidden zone, so its cards are not drawn on the board.
    // The resource count is shown in the champion panel instead.
    $(`#${prefix}-resources`).innerHTML = "";
  }
}

function renderPiles(pid, sel) {
  const deck = cardsIn(pid, COLL.Deck).length;
  const grave = cardsIn(pid, COLL.Discard).length;
  const voided = cardsIn(pid, COLL.Void).length;
  $(sel).innerHTML = `
    <div class="pile deck ${sleeveOf(pid)}" title="Deck"><div><b>${deck}</b>Deck</div></div>
    <div class="pile graveyard" data-pile="${COLL.Discard}" data-owner="${pid}" title="Graveyard"><div><b>${grave}</b>Graveyard</div></div>
    <div class="pile void" data-pile="${COLL.Void}" data-owner="${pid}" title="Void"><div><b>${voided}</b>Void</div></div>`;
}

function renderChain() {
  $("#chain").innerHTML = S.chain.map((item, i) => {
    const src = S.cards.get(item.source);
    const tpl = src && S.catalog.cards[(src.card_id || "").toLowerCase()];
    const ability = S.catalog.abilities[(item.ability || "").toLowerCase()];
    const mine = src && src.controller === S.me;
    const title = tpl ? tpl.name : (ability ? ability.name : "Ability");
    const isPlay = item.ability === PLAY_ABILITY;
    const text = isPlay ? (tpl ? tpl.text : "") : (ability ? ability.text : "");
    const plain = gameText(text).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
    const targets = (item.targets || []).map(cardName).join(", ");
    const caption = targets ? `\u2192 ${targets}` : (isPlay ? (mine ? "You cast" : "Opponent casts") : plain);
    const face = tpl ? cardHTML(src, "chain-card")
      : `<div class="card blank chain-card" data-uid="${item.source}"><div class="name">${esc(title)}</div></div>`;
    return `<div class="chain-entry ${mine ? "mine" : "theirs"} ${i === S.chain.length - 1 ? "top" : ""}"
        data-uid="${item.source}" title="${esc(`${title}: ${plain}`)}">${face}<div class="caption">${esc(caption)}</div></div>`;
  }).join("");
}

function renderPhases() {
  const phase = S.phase.turn_phase;
  const theirs = S.phase.active_player_id === S.opp ? "theirs" : "";
  $("#phase-track").innerHTML = PHASE_TRACK.map(([value, label], i) => {
    const next = PHASE_TRACK[i + 1] ? PHASE_TRACK[i + 1][0] : 99;
    const current = phase >= value && phase < next ? "current" : "";
    return `<span class="phase ${current} ${theirs}">${label}</span>`;
  }).join("");
  const turn = S.phase.active_player_id === S.me ? "Your turn" : (S.phase.active_player_id ? "Opponent's turn" : "");
  $("#phase-label").innerHTML = `<small>${turn}</small><b>${esc(PHASE_NAMES[phase] || "")}</b>`;
  $("#medallion-glyph").innerHTML = document.body.dataset.combat ? AXE : "&#9733;";
}

function passLabel() {
  if (S.chain.length) return "Resolve";
  const ctx = S.greenlight[S.me];
  return PASS_LABELS[ctx] || (S.phase.active_player_id === S.me ? "Pass" : "Pass");
}

function renderActions() {
  const text = $("#prompt-text"), buttons = $("#action-buttons");
  const p = S.prompt;
  const ui = S.ui;
  let html = "", label = "";
  const phaseName = PHASE_NAMES[S.phase.turn_phase] || "";
  if (S.busy || !p) {
    label = `<span class="thinking">${p && p.kind === "waiting" ? "Opponent is acting" : "Resolving"}</span>`;
  } else if (ui.mode === "target") {
    const step = ui.steps[ui.step];
    label = `${esc(step.label)} (${(ui.picked[step.key] || []).length}/${step.max})`;
    html = `<button data-act="confirm-targets">Confirm</button><button class="secondary" data-act="cancel">Cancel</button>`;
  } else if (p.kind === "priority") {
    label = S.chain.length ? "Respond, or let the chain resolve" : "";
    html = `<button data-act="pass">${passLabel()}</button>`;
  } else if (p.kind === "attack") {
    label = "Choose attackers, then attack.";
    html = `<button class="urgent" data-act="commit-attack">${ui.chosen && ui.chosen.size ? `Attack with ${ui.chosen.size}` : "No attack"}</button>`;
  } else if (p.kind === "defend") {
    label = ui.blocker ? "Now pick the attacker to block." : "Pick a blocker, then the attacker it blocks.";
    html = `<button data-act="commit-defense">Confirm blocks</button>`;
  } else if (p.kind === "choose") {
    label = p.candidates.length ? "Choose a target for the ability." : "That ability has no legal target.";
    html = p.candidates.length ? "" : `<button data-act="choose-none">Continue</button>`;
  } else if (p.kind === "ended") {
    label = p.you_won ? "Victory!" : "Defeat.";
    html = `<button data-act="new-match">New battle</button>`;
  } else if (p.kind === "waiting") {
    label = `<span class="thinking">Opponent is acting</span>`;
  } else {
    label = phaseName;
  }
  text.innerHTML = label;
  buttons.innerHTML = html;
}

function renderModal() {
  const p = S.prompt;
  const modal = $("#modal"), body = $("#modal-body");
  if (S.ui.mode === "modal") return;   // a user-opened view (pile, abilities)
  let html = "";
  if (p && !S.busy && p.kind === "goes_first") {
    html = `<h2>You won the coin toss</h2><p>Do you want to play first or draw first?</p>
      <div class="buttons"><button data-act="play_first">Play first</button><button class="secondary" data-act="draw_first">Draw first</button></div>`;
  } else if (p && !S.busy && p.kind === "mulligan") {
    const hand = cardsIn(S.me, COLL.Hand);
    html = `<h2>Starting hand</h2><div class="row">${hand.map((c) => faceHTML(c)).join("")}</div>
      <div class="buttons"><button data-act="keep">Keep</button><button class="secondary" data-act="mulligan">Mulligan</button></div>`;
  } else if (p && !S.busy && p.kind === "choose" && p.candidates.some((uid) => !onBoard(uid))) {
    html = `<h2>Choose a card</h2><div class="row">${p.candidates.map((uid) => {
      const c = S.cards.get(uid) || { uid, card_id: NO_GUID, controller: S.me };
      return cardHTML(c, "selectable");
    }).join("")}</div>`;
  } else if (p && !S.busy && p.kind === "ended") {
    html = `<h2>${p.you_won ? "Victory" : "Defeat"}</h2>
      <p>${p.you_won ? "The opposing champion has fallen." : "Your champion has fallen."}</p>
      <div class="buttons"><button data-act="new-match">New battle</button></div>`;
  }
  modal.classList.toggle("hidden", !html);
  if (html) body.innerHTML = html;
}

function onBoard(uid) {
  const c = S.cards.get(uid);
  return c && [COLL.Hand, COLL.Warzone, COLL.PlayedResources, COLL.Underground].includes(c.collection) &&
    !(c.collection === COLL.Hand && c.controller !== S.me) || Object.values(S.champions).includes(uid);
}

function openModal(html) {
  S.ui = { mode: "modal" };
  $("#modal-body").innerHTML = html + `<div class="buttons"><button class="secondary" data-act="close-modal">Close</button></div>`;
  $("#modal").classList.remove("hidden");
}

/* ------------------------------------------------------------------ */
/* Interaction                                                         */
/* ------------------------------------------------------------------ */

function freshUi(ui) {
  const kind = S.prompt && S.prompt.kind;
  if (kind === "attack") return ui.mode === "attack" ? ui : { mode: "attack", chosen: new Set() };
  if (kind === "defend") return ui.mode === "defend" ? ui : { mode: "defend", blocker: null, blocks: {} };
  if (ui.mode === "modal") return ui;
  return { mode: null };
}

function optionInstance(uid, abilityGuid) {
  const o = S.options.get(uid);
  if (!o) return null;
  return o.instances.find((i) => (i.opt_id || "").toLowerCase() === String(abilityGuid).toLowerCase()) || null;
}

// A charge power's option when it can be used now: a priority window, nothing in flight.
// Its text box shows as ready, and clicking it starts the power, only under this condition.
function activatablePower(uid, abilityGuid) {
  if (!S.prompt || S.prompt.kind !== "priority" || S.busy) return null;
  return optionInstance(uid, abilityGuid);
}

function startAction(uid, instance) {
  const card = S.cards.get(uid);
  const isPlay = instance.opt_id === PLAY_ABILITY;
  const action = isPlay ? { type: "play", card: uid } : { type: "activate", card: uid, ability: instance.opt_id };
  const steps = [];
  let costIndex = 0;
  (instance.target_instances || []).forEach((ti, i) => {
    const isCost = ti.cls === 66;
    const index = isCost ? costIndex++ : (ti.target_index ?? i);
    const max = isCost ? ti.max_target_count : (instance.max_target_counts[i] ?? 1);
    const min = isCost ? ti.min_target_count : (instance.min_target_counts[i] ?? 1);
    steps.push({ key: `${isCost ? "c" : "t"}${index}`, kind: isCost ? "costs" : "targets", index,
      candidates: new Set(ti.targets || []), min, max: Math.max(1, max),
      label: isCost ? "Choose a card to pay the cost" : "Choose a target" });
  });
  const tpl = card && S.catalog.cards[(card.card_id || "").toLowerCase()];
  if (isPlay && tpl && /\[X\]|\[\(X\)\]/.test(tpl.text || "")) {
    const x = parseInt(window.prompt("Choose a value for X", "1") || "0", 10);
    action.x = Number.isFinite(x) ? x : 0;
  }
  if (!steps.length) return act(action);
  S.ui = { mode: "target", action, steps, step: 0, picked: {} };
  render();
}

function finishTargets() {
  const ui = S.ui;
  const step = ui.steps[ui.step];
  const picked = ui.picked[step.key] || [];
  if (picked.length < step.min) return toast(`Choose at least ${step.min}.`);
  if (ui.step + 1 < ui.steps.length) { ui.step += 1; return render(); }
  const action = { ...ui.action, targets: {}, costs: {} };
  for (const s of ui.steps) action[s.kind][s.index] = ui.picked[s.key] || [];
  act(action);
}

function clickCard(uid) {
  const p = S.prompt;
  const ui = S.ui;
  if (S.busy || !p) return;
  if (ui.mode === "target") {
    const step = ui.steps[ui.step];
    if (!step.candidates.has(uid)) return;
    const list = ui.picked[step.key] || (ui.picked[step.key] = []);
    const at = list.indexOf(uid);
    if (at >= 0) list.splice(at, 1);
    else { if (list.length >= step.max) list.shift(); list.push(uid); }
    if (step.max === 1 && list.length === 1 && step.min <= 1) return finishTargets();
    return render();
  }
  if (p.kind === "choose") {
    if ((p.candidates || []).includes(uid)) act({ type: "choose", card: uid });
    return;
  }
  if (ui.mode === "attack") {
    const o = S.options.get(uid);
    if (!o || !(o.state & USAGE.Attack)) return;
    ui.chosen.has(uid) ? ui.chosen.delete(uid) : ui.chosen.add(uid);
    return render();
  }
  if (ui.mode === "defend") {
    const o = S.options.get(uid);
    if (o && (o.state & USAGE.Defend)) {
      for (const list of Object.values(ui.blocks)) {
        const at = list.indexOf(uid);
        if (at >= 0) list.splice(at, 1);
      }
      ui.blocker = ui.blocker === uid ? null : uid;
      return render();
    }
    if (ui.blocker && (p.attackers || []).includes(uid)) {
      (ui.blocks[uid] = ui.blocks[uid] || []).push(ui.blocker);
      ui.blocker = null;
      return render();
    }
    return;
  }
  if (p.kind === "priority") {
    const o = S.options.get(uid);
    if (!o || !o.instances.length) return;
    if (o.instances.length === 1) return startAction(uid, o.instances[0]);
    chooseAbility(uid, o.instances);
  }
}

function chooseAbility(uid, instances) {
  openModal(`<h2>${esc(cardName(uid))}</h2><div class="champ-powers">${instances.map((inst, i) => {
    const a = S.catalog.abilities[(inst.opt_id || "").toLowerCase()];
    const label = inst.opt_id === PLAY_ABILITY ? "Play this card" : (a ? gameText(a.text) : "Activate");
    return `<button class="power ready" data-choose-instance="${i}" data-uid="${uid}">${label}</button>`;
  }).join("")}</div>`);
}

function showPile(owner, collection) {
  const cards = cardsIn(Number(owner), Number(collection));
  const title = Number(collection) === COLL.Void ? "Void" : "Graveyard";
  openModal(`<h2>${who(Number(owner))} — ${title}</h2><div class="row">${cards.map((c) => cardHTML(c)).join("") || "<p>Empty</p>"}</div>`);
}

/* ------------------------------------------------------------------ */
/* Card flights                                                        */
/* ------------------------------------------------------------------ */

function handRects() {
  const out = new Map();
  document.querySelectorAll("#my-hand [data-uid], #opp-hand [data-uid]").forEach((el) => {
    const r = el.getBoundingClientRect();
    if (r.width) out.set(Number(el.dataset.uid), r);
  });
  return out;
}

function centrePoint() {
  if (S.board3d) return S.board3d.centreScreen();
  const r = $("#centre").getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
}

function burstAt(p) {
  if (S.board3d) S.board3d.burst();
  const el = document.createElement("div");
  el.className = "burst";
  el.style.left = `${p.x}px`;
  el.style.top = `${p.y}px`;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 850);
}

function animateMoves(before) {
  if (S.chain.length < S.lastChain) burstAt(centrePoint());   // something resolved
  S.lastChain = S.chain.length;
  for (const [uid, from] of before) {
    const card = S.cards.get(uid);
    if (card && card.collection !== COLL.Hand && !S.flying.has(uid)) flyCard(card, from);
  }
}

function landingOf(card) {
  const side = card.controller === S.me ? "my" : "opp";
  switch (card.collection) {
    // A resource played by either player flies through the centre, then settles in its row.
    case COLL.PlayedResources: return { el: $(`#${side}-resources [data-uid="${card.uid}"]`), viaCentre: true };
    case COLL.Deck: return { el: $(`#${side}-piles .deck`), viaCentre: false, fade: true };
    case COLL.Discard: return { el: $(`#${side}-piles .graveyard`), viaCentre: true, fade: true };
    case COLL.Void: return { el: $(`#${side}-piles .void`), viaCentre: true, fade: true };
    default: return S.board3d ? { rect: S.board3d.screenRect(card.uid), viaCentre: true }
      : { el: document.querySelector(`#table [data-uid="${card.uid}"]`), viaCentre: true };
  }
}

function flyCard(card, from) {
  // A card leaving a hand flies from where it was drawn to the centre of the
  // board, flashes, then settles where the server put it.
  const land = landingOf(card);
  const holder = document.createElement("div");
  holder.innerHTML = cardHTML({ ...card, uid: `f${card.uid}`, state: 0 }, "flying");
  const el = holder.firstElementChild;
  el.style.setProperty("--card-w", `${HAND_W}px`);
  el.style.setProperty("--card-h", `${HAND_H}px`);
  document.body.appendChild(el);
  S.flying.add(card.uid);

  const pose = (x, y, s, extra = {}) => ({ transform: `translate(${x - HAND_W / 2}px, ${y - HAND_H / 2}px) scale(${s})`, ...extra });
  const c = centrePoint();
  const frames = [pose(from.left + from.width / 2, from.top + from.height / 2, from.width / HAND_W)];
  if (land.viaCentre) {
    frames.push(pose(c.x, c.y, 1.55, { offset: 0.36, filter: "brightness(1.6)" }),
      pose(c.x, c.y, 1.5, { offset: 0.62, filter: "brightness(1)" }));
  }
  const to = land.rect || (land.el && land.el.getBoundingClientRect());
  if (to && to.width) {
    frames.push(pose(to.left + to.width / 2, to.top + to.height / 2, to.width / HAND_W, { opacity: land.fade ? 0 : 1 }));
  } else {
    frames.push(pose(c.x, c.y, 0.7, { opacity: 0 }));
  }
  const duration = land.viaCentre ? 1300 : 600;
  if (land.viaCentre) setTimeout(() => burstAt(c), duration * 0.36);
  const done = () => {
    el.remove();
    S.flying.delete(card.uid);
    document.querySelectorAll(`[data-uid="${card.uid}"].in-flight`).forEach((e) => e.classList.remove("in-flight"));
    if (S.board3d) renderBoard3d(S.board3d);
  };
  el.animate(frames, { duration, easing: "cubic-bezier(.3,.7,.3,1)" }).finished.then(done, done);
}

function hideFlying() {
  for (const uid of S.flying) {
    document.querySelectorAll(`#board [data-uid="${uid}"]`).forEach((e) => e.classList.add("in-flight"));
  }
}

function toast(text) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = text;
  $("#board").appendChild(el);
  setTimeout(() => el.remove(), 2600);
}

function showError(text) {
  openModal(`<h2>The server hit an error</h2><p>The match cannot continue from here.</p><pre>${esc(text)}</pre>
    <div class="buttons"><button data-act="new-match">Back to the lobby</button></div>`);
}

function floatAtChampion(pid, text, kind) {
  const slot = $(pid === S.me ? "#my-champion" : "#opp-champion");
  if (!slot) return;
  const r = slot.getBoundingClientRect(), b = $("#board").getBoundingClientRect();
  const el = document.createElement("div");
  el.className = `float ${kind}`;
  el.textContent = text;
  el.style.left = `${r.left - b.left + r.width - 40}px`;
  el.style.top = `${r.top - b.top + 20}px`;
  $("#board").appendChild(el);
  setTimeout(() => el.remove(), 1300);
}

function showPreview(uid) {
  const c = S.cards.get(uid);
  if (!c || !c.card_id || c.card_id === NO_GUID) { $("#preview").innerHTML = ""; return; }
  $("#preview").innerHTML = faceHTML({ ...c, state: 0, uid: `p${c.uid}` }, "preview-card");
}

/* ------------------------------------------------------------------ */
/* Drag to play: a hand card released above the hand plays its Play option */
/* ------------------------------------------------------------------ */

const DRAG_THRESHOLD = 6;      // px of movement before a press becomes a drag
const DRAG_CLICK_WINDOW = 400; // ms after a drop in which the trailing click is ignored
const drag = { uid: 0, el: null, startX: 0, startY: 0, ghost: null, active: false, endedAt: 0 };

// The play area is everything on the board above the hand row.
function overPlayArea(x, y) {
  if (y >= $("#my-hand").getBoundingClientRect().top) return false;
  const under = document.elementFromPoint(x, y);
  return !!(under && under.closest("#board"));
}

function moveDragGhost(x, y) {
  drag.ghost.style.left = `${x}px`;
  drag.ghost.style.top = `${y}px`;
  $("#board").classList.toggle("drop-target", overPlayArea(x, y));
}

function beginDrag(x, y) {
  drag.active = true;
  drag.ghost = drag.el.cloneNode(true);
  drag.ghost.removeAttribute("data-uid");
  drag.ghost.classList.add("drag-ghost");
  document.body.appendChild(drag.ghost);
  moveDragGhost(x, y);
}

function endDrag() {
  if (drag.ghost) drag.ghost.remove();
  $("#board").classList.remove("drop-target");
  drag.el = null;
  drag.ghost = null;
  drag.active = false;
}

function playDropped(uid) {
  const p = S.prompt;
  if (S.busy || !p || p.kind !== "priority") return;
  if (S.ui && S.ui.mode) return;
  const instance = optionInstance(uid, PLAY_ABILITY);
  if (instance) startAction(uid, instance);
}

document.addEventListener("pointerdown", (e) => {
  if (e.button !== 0) return;
  const el = e.target.closest("#my-hand [data-uid]");
  if (!el || !el.dataset.uid) return;
  drag.uid = Number(el.dataset.uid);
  drag.el = el;
  drag.startX = e.clientX;
  drag.startY = e.clientY;
});

document.addEventListener("pointermove", (e) => {
  if (!drag.el) return;
  if (!drag.active) {
    if (Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY) < DRAG_THRESHOLD) return;
    beginDrag(e.clientX, e.clientY);
  }
  moveDragGhost(e.clientX, e.clientY);
});

document.addEventListener("pointerup", (e) => {
  if (!drag.el) return;
  if (drag.active) {
    if (overPlayArea(e.clientX, e.clientY)) playDropped(drag.uid);
    drag.endedAt = performance.now();
  }
  endDrag();
});

document.addEventListener("pointercancel", () => { if (drag.el) endDrag(); });

document.addEventListener("click", (e) => {
  if (drag.endedAt && performance.now() - drag.endedAt < DRAG_CLICK_WINDOW) {
    drag.endedAt = 0;
    return;
  }
  const actEl = e.target.closest("[data-act]");
  if (actEl) return handleAct(actEl.dataset.act);
  const inst = e.target.closest("[data-choose-instance]");
  if (inst) {
    const uid = Number(inst.dataset.uid);
    const o = S.options.get(uid);
    S.ui = { mode: null };
    $("#modal").classList.add("hidden");
    return startAction(uid, o.instances[Number(inst.dataset.chooseInstance)]);
  }
  const power = e.target.closest("[data-power]");
  if (power) {
    const uid = Number(power.dataset.uid);
    const instance = activatablePower(uid, power.dataset.power);
    if (instance) startAction(uid, instance);
    else if (power.closest("#my-champion")) toast("That charge power cannot be used right now.");
    return;
  }
  const pile = e.target.closest("[data-pile]");
  if (pile) return showPile(pile.dataset.owner, pile.dataset.pile);
  const cardEl = e.target.closest("[data-uid]");
  if (cardEl && cardEl.dataset.uid) {
    if (S.ui.mode === "modal") { S.ui = { mode: null }; $("#modal").classList.add("hidden"); }
    clickCard(Number(cardEl.dataset.uid));
  }
});

document.addEventListener("mouseover", (e) => {
  const cardEl = e.target.closest("#board [data-uid], #modal [data-uid]");
  if (cardEl && cardEl.dataset.uid) showPreview(Number(cardEl.dataset.uid));
  else if (!e.target.closest("#preview, #board3d")) $("#preview").innerHTML = "";
});

function handleAct(name) {
  const ui = S.ui;
  switch (name) {
    case "pass": return act({ type: "pass" });
    case "play_first": case "draw_first": case "keep": case "mulligan": return act({ type: name });
    case "commit-attack": return act({ type: "attack", attackers: [...(ui.chosen || [])] });
    case "commit-defense": return act({ type: "defend", blocks: ui.blocks || {} });
    case "choose-none": return act({ type: "choose", card: null });
    case "confirm-targets": return finishTargets();
    case "cancel": S.ui = { mode: null }; return render();
    case "close-modal": S.ui = { mode: null }; $("#modal").classList.add("hidden"); return render();
    case "new-match": return backToLobby();
    default: return null;
  }
}

/* ------------------------------------------------------------------ */
/* Lobby                                                               */
/* ------------------------------------------------------------------ */

// The lobby and in-game menu offer the boards the folder has.
function boardOptions() {
  return boards.map((v) => `<option value="${v}" ${v === boardChoice() ? "selected" : ""}>${boardLabel(v)}</option>`).join("");
}

function fillBoardSelects() {
  $("#board-choice").innerHTML = boardOptions();
  $("#board-choice").value = boardChoice();
}

// The Hex client folder: the match cannot start until its art and sounds are
// read, because the cards and boards are drawn from them. A folder chosen on an
// earlier visit is offered again first and falls back to the picker.
let savedFolder = false;
async function chooseHexFolder() {
  const status = $("#folder-status");
  try {
    let tree = savedFolder ? await HexAssets.useSavedFolder() : null;
    if (!tree) {
      const picked = await HexAssets.pickFolder($("#folder-input"));
      if (!picked) return;
      tree = picked.tree;
    }
    status.textContent = "Reading the client assets\u2026";
    await HexAssets.openFolder(tree, (text) => { status.textContent = `${text}\u2026`; });
    boards = await HexAssets.boardNames();
    fillBoardSelects();
    // Load the card frames now, while the lobby shows, so the first battle is not
    // drawn first in the fallback frame.
    status.textContent = "Loading card frames…";
    await HexAssets.preloadCardChrome();
    status.textContent = "Client assets ready: card art, card sounds and battleboards.";
    $("#pick-folder").textContent = "Choose another folder\u2026";
    $("#start-match").disabled = false;
    $("#lobby-status").textContent = "";
  } catch (err) {
    status.textContent = err.message;
  }
}

async function loadLobby() {
  const sleeve = $("#sleeve-choice");
  sleeve.innerHTML = SLEEVES.map((s) => `<option value="${s}">${s[0].toUpperCase() + s.slice(1)}</option>`).join("");
  sleeve.value = storage("hex.sleeve") || "classic";
  $("#player-name").value = storage("hex.name") || "You";
  // Sounds default to half volume; the slider is saved and applied as it moves.
  const volume = $("#volume-choice");
  volume.value = storage("hex.volume") ?? "50";
  const applyVolume = () => {
    $("#volume-value").textContent = `${volume.value}%`;
    if (window.HexAssets) HexAssets.setVolume(volume.value / 100);
  };
  applyVolume();
  volume.oninput = () => { storage("hex.volume", volume.value); applyVolume(); };
  try {
    const { decks } = await api("/api/decks");
    const opts = decks.map((d) => `<option value="${d.deck_guid}">${esc(d.name.replace(/^Arena_/, "").replace(/_/g, " "))}${d.champion ? ` — ${esc(d.champion)}` : ""}</option>`).join("");
    $("#deck-mine").innerHTML = opts;
    $("#deck-theirs").innerHTML = opts;
    $("#deck-mine").value = storage("hex.deck") || decks[0].deck_guid;
    $("#deck-theirs").value = storage("hex.opponent") || decks[Math.min(2, decks.length - 1)].deck_guid;
  } catch (err) {
    $("#lobby-status").textContent = `Could not load decks: ${err.message}`;
  }
}

async function startMatch() {
  const button = $("#start-match");
  button.disabled = true;
  $("#lobby-status").textContent = "Setting up the battle…";
  storage("hex.sleeve", $("#sleeve-choice").value);
  storage("hex.board", $("#board-choice").value);
  storage("hex.name", $("#player-name").value);
  storage("hex.deck", $("#deck-mine").value);
  storage("hex.opponent", $("#deck-theirs").value);
  try {
    const { id } = await api("/api/matches", {
      deck: $("#deck-mine").value, opponent_deck: $("#deck-theirs").value, name: $("#player-name").value });
    resetState();
    S.matchId = id;
    const first = await api(`/api/matches/${id}?after=0&wait=0`);
    S.info = first.match;
    S.me = S.info.player_uid;
    S.opp = S.info.opponent_uid;
    S.names[S.me] = $("#player-name").value;
    $("#lobby").classList.add("hidden");
    $("#battle").classList.remove("hidden");
    poll();
  } catch (err) {
    $("#lobby-status").textContent = `Could not start: ${err.message}`;
  } finally {
    button.disabled = false;
  }
}

function backToLobby() {
  if (S.matchId) api(`/api/matches/${S.matchId}`, null, "DELETE").catch(() => {});
  S.matchId = null;
  $("#modal").classList.add("hidden");
  $("#battle").classList.add("hidden");
  $("#lobby").classList.remove("hidden");
  $("#lobby-status").textContent = "";
}

$("#start-match").addEventListener("click", startMatch);
$("#start-match").disabled = true;
$("#pick-folder").addEventListener("click", chooseHexFolder);
// Module scripts run before DOMContentLoaded, so window.HexAssets exists here.
document.addEventListener("DOMContentLoaded", async () => {
  if (!(await HexAssets.hasSavedFolder())) return;
  savedFolder = true;
  $("#pick-folder").textContent = "Use saved Hex folder\u2026";
  $("#folder-status").textContent = "Your Hex folder from an earlier visit is saved in this browser.";
  boards = await HexAssets.boardNames();
  fillBoardSelects();
});
$("#menu-button").addEventListener("click", () => {
  openModal(`<h2>Menu</h2>
    <div class="lobby-panel" style="background:none;border:0;box-shadow:none;padding:0">
      <label>Card sleeve <select id="menu-sleeve">${SLEEVES.map((s) => `<option ${s === (storage("hex.sleeve") || "classic") ? "selected" : ""}>${s}</option>`).join("")}</select></label>
      <label>Gameboard <select id="menu-board">${boardOptions()}</select></label>
      <label>Sound volume <input type="range" id="menu-volume" min="0" max="100" step="5" value="${storage("hex.volume") ?? 50}"> <span id="menu-volume-value">${storage("hex.volume") ?? 50}%</span></label>
    </div>
    <div class="buttons"><button class="secondary" data-act="new-match">Concede and leave</button></div>`);
  $("#menu-sleeve").addEventListener("change", (e) => { storage("hex.sleeve", e.target.value); render(); });
  $("#menu-board").addEventListener("change", (e) => { storage("hex.board", e.target.value); render(); });
  $("#menu-volume").addEventListener("input", (e) => {
    $("#menu-volume-value").textContent = `${e.target.value}%`;
    storage("hex.volume", e.target.value);
    if (window.HexAssets) HexAssets.setVolume(e.target.value / 100);
  });
});
document.body.dataset.board = boardChoice();
loadLobby();
