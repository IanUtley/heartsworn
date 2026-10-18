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
const PASS_LABELS = { 2: "Ready", 4: "Go to combat", 6: "Resolve", 7: "To second main",
  9: "End turn", 11: "End turn", 14: "Resolve combat", 15: "Pass", 16: "Pass" };
const SLEEVES = ["classic", "blood", "ruby", "sapphire", "wild", "diamond", "cosmic"];
const BOARDS = [["shards", "Shards of Fate"], ["ardent", "Ardent Sky"], ["underworld", "Underworld"],
  ["frostring", "Frost Ring Arena"], ["shinhare", "Shin'hare Wilds"], ["aria", "Aria Shores"]];

const $ = (sel) => document.querySelector(sel);
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
  artOk: null, log: [], prevLife: {}, rendered: new Set(),
};

function resetState() {
  Object.assign(S, {
    after: 0, cards: new Map(), players: {}, champions: {}, championTemplates: {}, names: {},
    phase: {}, greenlight: {}, chain: [], options: new Map(), combats: new Map(), decks: {},
    prompt: null, busy: true, error: null, ended: false, ui: { mode: null }, log: [], prevLife: {},
    rendered: new Set(),
  });
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
  probeArt();
}

function probeArt() {
  if (S.artOk !== null) return;
  const sample = Object.values(S.catalog.cards).find((c) => c && /^a\d{7}$/.test(c.art || ""));
  if (!sample) return;
  S.artOk = false;
  const img = new Image();
  img.onload = () => { S.artOk = true; render(); };
  img.src = `/art/${sample.art}.jpg`;
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

function artStyle(tpl) {
  if (S.artOk && tpl && /^a\d{7}$/.test(tpl.art || "")) return ` style="background-image:url('/art/${tpl.art}.jpg')"`;
  return "";
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
  const key = `${card.uid}:${card.collection}`;
  if (S.rendered.has(key)) return "";
  S.rendered.add(key);
  return "fresh";
}

function cardHTML(card, extra = "") {
  const tpl = S.catalog.cards[(card.card_id || "").toLowerCase()];
  const hidden = !card.card_id || card.card_id === NO_GUID;
  if (hidden) return `<div class="card back ${sleeveOf(card.controller)} ${freshClass(card)} ${extra}" data-uid="${card.uid}"></div>`;
  const classes = ["card", shardClass(tpl), freshClass(card), extra];
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
  return `<div class="${classes.join(" ")}" data-uid="${card.uid}">
    ${cost}<div class="name">${esc(name)}</div>
    <div class="art"${artStyle(tpl)}>${artStyle(tpl) ? "" : (TYPE_GLYPH[tpl && tpl.type] || "")}</div>
    <div class="type-line">${esc(typeName)} ${thresholdGems(tpl)}</div>
    ${text}<div class="keywords">${kws}</div><div class="counters">${counters}</div>
    ${showStats ? `<div class="stats"><span class="stat atk ${atkCls}">${card.attack}</span><span class="stat def ${defCls}">${card.defense}</span></div>` : ""}
  </div>`;
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

function render() {
  if (!S.info) return;
  document.body.dataset.board = storage("hex.board") || "shards";
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
  $("#log").innerHTML = S.log.map((l) => `<div>${esc(l(cardName))}</div>`).join("");
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
  const tplGuid = (champCard && champCard.card_id) || S.championTemplates[pid];
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
    const ready = pid === S.me && optionInstance(uid, g) ? "ready" : "";
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

function renderHands() {
  const opp = cardsIn(S.opp, COLL.Hand);
  $("#opp-hand").innerHTML = opp.map((c) => cardHTML(c, interactive(c.uid))).join("") +
    `<span class="hand-count">${opp.length} in hand</span>`;
  const mine = cardsIn(S.me, COLL.Hand);
  $("#my-hand").innerHTML = mine.map((c) => cardHTML(c, interactive(c.uid))).join("");
}

function renderZones() {
  for (const [pid, prefix] of [[S.opp, "opp"], [S.me, "my"]]) {
    const war = [...cardsIn(pid, COLL.Warzone), ...cardsIn(pid, COLL.Underground)];
    $(`#${prefix}-warzone`).innerHTML = war.map((c) => cardHTML(c, interactive(c.uid))).join("");
    const res = cardsIn(pid, COLL.PlayedResources);
    $(`#${prefix}-resources`).innerHTML = res.map((c) => {
      const tpl = S.catalog.cards[(c.card_id || "").toLowerCase()];
      const shard = shardClass(tpl).replace("shard-", "");
      return `<div class="resource-chip ${c.state & CSTATE.Tapped ? "tapped" : ""} ${interactive(c.uid)}" data-uid="${c.uid}"
        style="--shard:var(--${shard || "colorless"})" title="${esc(tpl ? tpl.name : "Resource")}">&#9670;</div>`;
    }).join("");
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
    const text = item.ability === PLAY_ABILITY ? (tpl ? tpl.text : "") : (ability ? ability.text : "");
    const targets = (item.targets || []).map(cardName).join(", ");
    return `<div class="chain-item ${mine ? "mine" : "theirs"} ${i === S.chain.length - 1 ? "top" : ""}"
        data-uid="${item.source}" style="--shard:${shardColor(tpl)}">
      <div class="thumb"${artStyle(tpl)}></div>
      <div class="ci-name">${esc(title)}</div>
      <div class="ci-text">${targets ? `&rarr; ${esc(targets)}<br>` : ""}${gameText(text)}</div>
    </div>`;
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
    label = `${p.your_turn ? "Your turn" : "Opponent's turn"} · ${phaseName}`;
    html = `<button data-act="pass">${passLabel()}</button>`;
  } else if (p.kind === "attack") {
    label = "Choose attackers, then attack.";
    html = `<button data-act="commit-attack">${ui.chosen && ui.chosen.size ? `Attack with ${ui.chosen.size}` : "No attack"}</button>`;
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
    html = `<h2>Starting hand</h2><div class="row">${hand.map((c) => cardHTML(c)).join("")}</div>
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
  $("#preview").innerHTML = cardHTML({ ...c, state: 0, uid: `p${c.uid}` }, "preview-card");
}

document.addEventListener("click", (e) => {
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
    const instance = optionInstance(Number(power.dataset.uid), power.dataset.power);
    if (instance && S.prompt && S.prompt.kind === "priority" && !S.busy) startAction(Number(power.dataset.uid), instance);
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
  else if (!e.target.closest("#preview")) $("#preview").innerHTML = "";
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

async function loadLobby() {
  const sleeve = $("#sleeve-choice"), board = $("#board-choice");
  sleeve.innerHTML = SLEEVES.map((s) => `<option value="${s}">${s[0].toUpperCase() + s.slice(1)}</option>`).join("");
  board.innerHTML = BOARDS.map(([v, n]) => `<option value="${v}">${n}</option>`).join("");
  sleeve.value = storage("hex.sleeve") || "classic";
  board.value = storage("hex.board") || "shards";
  $("#player-name").value = storage("hex.name") || "You";
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
$("#menu-button").addEventListener("click", () => {
  openModal(`<h2>Menu</h2>
    <div class="lobby-panel" style="background:none;border:0;box-shadow:none;padding:0">
      <label>Card sleeve <select id="menu-sleeve">${SLEEVES.map((s) => `<option ${s === (storage("hex.sleeve") || "classic") ? "selected" : ""}>${s}</option>`).join("")}</select></label>
      <label>Gameboard <select id="menu-board">${BOARDS.map(([v, n]) => `<option value="${v}" ${v === (storage("hex.board") || "shards") ? "selected" : ""}>${n}</option>`).join("")}</select></label>
    </div>
    <div class="buttons"><button class="secondary" data-act="new-match">Concede and leave</button></div>`);
  $("#menu-sleeve").addEventListener("change", (e) => { storage("hex.sleeve", e.target.value); render(); });
  $("#menu-board").addEventListener("change", (e) => { storage("hex.board", e.target.value); render(); });
});
document.body.dataset.board = storage("hex.board") || "shards";
loadLobby();
