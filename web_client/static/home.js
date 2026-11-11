/* The main page (Battle, Collection, Settings) and the collection and deck page.

   Uses the globals app.js defines: S, $, api, storage, esc, loadCatalog, cardHTML, toast.
   Views are the sections in index.html: home, lobby (battle setup), battle and collection. showView() shows one.
*/

const VIEW_IDS = ["home", "lobby", "battle", "collection"];

function showView(name) {
  for (const id of VIEW_IDS) $(`#${id}`).classList.toggle("hidden", id !== name);
  if (name !== "home") disposeHomeScene();
  if (name === "home") applyHomeBackground();
  if (name === "collection") openCollection();
}

/* ------------------------------------------------------------------ */
/* Main page: the background, chosen in Settings                       */
/* ------------------------------------------------------------------ */

// The client's Settings > Background list: BackgroundPicker.BackgroundEnum in the Assembly-CSharp source. The
// battle's gameboard is chosen separately (Gameboard in the lobby).
const BACKGROUNDS = ["Latest", "Black", "HexLogo", "Cosmic", "Set04", "Set05", "Set06", "Set07", "Set08", "Set09"];
const DEFAULT_BACKGROUND = "Latest";

function backgroundChoice() {
  const key = storage("hex.background");
  return BACKGROUNDS.includes(key) ? key : DEFAULT_BACKGROUND;
}

// The Settings background names -> the Background_* prefab that draws them. Latest is the Doombringer scene (Set09).
const BACKGROUND_PREFAB = {
  Latest: "Background_Set09", Black: "Background_Black", HexLogo: "Background_HexLogo", Cosmic: "Background_Cosmic",
  Set04: "Background_Set04", Set05: "Background_Set05", Set06: "Background_Set06", Set07: "Background_Set07",
  Set08: "Background_Set08", Set09: "Background_Set09",
};
const homeScene = { scene: null, token: 0 };

// Draws the chosen background behind the page. Without the Hex folder there is no art, so the page keeps its plain
// background; a choice that has no layers (Black, HexLogo) is the plain background too.
async function applyHomeBackground() {
  const key = backgroundChoice();
  $("#home").dataset.background = key;
  const token = ++homeScene.token;
  const layers = window.HexAssets ? await HexAssets.background(BACKGROUND_PREFAB[key]) : null;
  if (token !== homeScene.token) return;
  const drawable = layers && (layers.parts.length || layers.emitters.length || layers.clearColor) ? layers : null;
  disposeHomeScene();
  $("#home-scene").classList.toggle("hidden", !drawable);
  // data-scene names the background that is now drawn, so a test can wait for it.
  if (!drawable) { $("#home").dataset.scene = key; return; }
  const { BackgroundScene } = await import("/static/background3d.js");
  if (token === homeScene.token) {
    homeScene.scene = new BackgroundScene($("#home-scene"), drawable);
    $("#home").dataset.scene = key;
  }
}

function disposeHomeScene() {
  if (homeScene.scene) homeScene.scene.dispose();
  homeScene.scene = null;
}

// The backgrounds are drawn from the Hex folder, so Settings says whether it has been read, and offers to choose it.
async function renderFolderStatus() {
  if (!window.HexAssets) return;
  const status = $("#settings-folder-status"), button = $("#settings-folder");
  if (HexAssets.ready()) {
    status.textContent = "Backgrounds are drawn from your Hex folder.";
    button.textContent = "Choose a different Hex folder\u2026";
    return;
  }
  const saved = await HexAssets.hasSavedFolder();
  status.textContent = saved
    ? "Your Hex folder from an earlier visit is saved. Use it to draw the backgrounds."
    : "Choose your HEX SHARDS OF FATE folder to draw the backgrounds. Nothing is uploaded.";
  button.textContent = saved ? "Use saved Hex folder\u2026" : "Choose Hex folder\u2026";
}

function openSettings(open) {
  $("#settings-panel").classList.toggle("hidden", !open);
  if (!open) return;
  renderFolderStatus();
  $("#home-background").innerHTML = BACKGROUNDS.map((key) => `<option value="${key}">${key}</option>`).join("");
  $("#home-background").value = backgroundChoice();
}

function chooseBackground(key) {
  storage("hex.background", key);
  applyHomeBackground();
}

/* ------------------------------------------------------------------ */
/* Collection and deck page                                            */
/* ------------------------------------------------------------------ */

// EDeckFormat names. '' is a deck the Unity client saved, which has no format.
const FORMAT_NAMES = [["standard", "Standard"], ["immortal", "Immortal"], ["pve", "PvE"], ["siege", "Siege"], ["", "Not chosen"]];
const DECK_MINIMUM = 60;   // the client's c_MinimumDeckSize: a smaller main deck is saved as a draft
const RESERVE_LIMIT = 15;  // the client's Format.cs reserve limit
const ERROR_TEXT = {
  Format: "Choose a format.",
  NoChampionSelected: "Choose a champion.",
  ChampionIDNotFound: "That champion cannot be played.",
  TooFewCards: (a) => `The main deck has ${a.count} cards; it needs ${a.minimum} to play.`,
  TooManyReserves: (a) => `${a.count} reserves; the limit is ${a.maximum}.`,
  TooManyOfOneCard: (a) => `${a.count} copies of ${a.card}; the limit is ${a.maximum}.`,
  NotOwned: (a) => `You own ${a.owned} copies of a card in the deck, and it needs ${a.needed}.`,
  InvalidCards: (a) => (a.reason === "pve" ? "Some cards are PvE-only and cannot go in this format."
                                          : "Some cards have no Standard version."),
  CardNotLoaded: () => "Some cards are not in the game data.",
  SiegeRulesNotChecked: () => "Siege rules are not checked yet.",
};
const deckPage = { loaded: false, cards: [], decks: [], champions: [], gems: [], draft: null };
let validateTimer = null;
let validateToken = 0;

// A draft's cards: guid -> { main, reserve } copies.
function emptyDraft() {
  return { id: null, name: "New deck", format: "standard", champion_guid: "", cards: new Map() };
}

function ownedCopies(guid) {
  const card = deckPage.cards.find((c) => c.guid === guid);
  return card ? card.quantity : 0;
}

function copiesOf(guid) {
  const entry = deckPage.draft.cards.get(guid);
  return entry ? entry.main + entry.reserve : 0;
}

function totals() {
  let main = 0, reserve = 0;
  for (const entry of deckPage.draft.cards.values()) { main += entry.main; reserve += entry.reserve; }
  return { main, reserve };
}

function cardName(guid) {
  const tpl = S.catalog.cards[guid];
  return (tpl && tpl.name) || guid.slice(0, 8);
}

function errorText(error) {
  const text = ERROR_TEXT[error.code];
  if (typeof text === "function") return text(error.args || {});
  return text || error.code;
}

async function openCollection() {
  if (!deckPage.loaded) {
    try {
      const [col, decks, champs] = await Promise.all([
        api("/api/collection"), api("/api/profile/decks"), api("/api/profile/champions")]);
      deckPage.cards = col.cards;
      deckPage.decks = decks.decks;
      deckPage.champions = champs.champions;
      deckPage.gems = (await api("/api/profile/gems")).gems;
      for (const c of [...col.cards, ...champs.champions]) S.wanted.cards.add(c.guid);
      await loadCatalog();
      deckPage.loaded = true;
    } catch (err) {
      toast(`Could not load the collection: ${err.message}`);
      return;
    }
    deckPage.draft = deckPage.draft || emptyDraft();
  }
  renderCollection();
}

function loadDraft(id) {
  const saved = deckPage.decks.find((d) => d.id === id);
  if (!saved) { deckPage.draft = emptyDraft(); return renderCollection(); }
  deckPage.draft = {
    id: saved.id, name: saved.name, format: saved.format || "", champion_guid: saved.champion_guid,
    cards: new Map(saved.cards.map((c) => [c.guid, { main: c.quantity - (c.reserves || 0), reserve: c.reserves || 0,
      gems: savedGems(c) }])),
  };
  renderCollection();
}

// The gems a saved card's copies carry, by part: the first copy of each part (the page sets one gem layout per part).
function savedGems(card) {
  const reserves = card.reserves || 0;
  const copies = card.gems || [];
  return { main: (copies.slice(reserves)[0] || []).slice(), reserve: (copies.slice(0, reserves)[0] || []).slice() };
}

function sockets(guid) {
  const card = deckPage.cards.find((c) => c.guid === guid);
  return card ? card.sockets || 0 : 0;
}

function addCopy(guid, part = "main") {
  if (copiesOf(guid) >= ownedCopies(guid)) return toast("No more copies of that card are owned.");
  const entry = deckPage.draft.cards.get(guid) || { main: 0, reserve: 0, gems: { main: [], reserve: [] } };
  entry[part] += 1;
  deckPage.draft.cards.set(guid, entry);
  renderCollection();
}

function removeCopy(guid, part = "main") {
  const entry = deckPage.draft.cards.get(guid);
  if (!entry || entry[part] <= 0) return;
  entry[part] -= 1;
  if (entry.main + entry.reserve === 0) deckPage.draft.cards.delete(guid);
  renderCollection();
}

function moveCopy(guid, to) {
  const entry = deckPage.draft.cards.get(guid);
  const from = to === "reserve" ? "main" : "reserve";
  if (!entry || entry[from] <= 0) return;
  entry[from] -= 1;
  entry[to] += 1;
  renderCollection();
}

function renderCollection() {
  if (!deckPage.loaded || !deckPage.draft) return;
  renderCollectionGrid();
  renderDeckPanel();
}

function renderCollectionGrid() {
  const query = $("#collection-search").value.trim().toLowerCase();
  const sort = $("#collection-sort").value;
  const cards = deckPage.cards
    .filter((c) => !query || c.name.toLowerCase().includes(query))
    .sort((a, b) => sort === "cost" ? (a.cost ?? 0) - (b.cost ?? 0) || a.name.localeCompare(b.name) : a.name.localeCompare(b.name));
  if (!cards.length) {
    $("#collection-grid").innerHTML = `<p class="status">${deckPage.cards.length ? "No cards match." : "No cards in this collection yet."}</p>`;
    return;
  }
  $("#collection-grid").innerHTML = cards.map((c, i) => {
    // The grid uses the battle's card renderer; a pseudo card carries the template and a unique uid for the slot.
    const card = cardHTML({ uid: 900000 + i, card_id: c.guid, controller: S.me, state: 0 }, "owned-card");
    return `<div class="owned" data-guid="${c.guid}" title="${esc(c.name)}">${card}
      <span class="owned-count">${copiesOf(c.guid)}/${c.quantity}</span></div>`;
  }).join("");
}

// One picker per socket of a card; the gems chosen apply to every copy in this part of the deck.
function socketPickers(guid, part, gems) {
  const options = (selected) => deckPage.gems.map((g) =>
    `<option value="${esc(g.name)}" ${g.name === selected ? "selected" : ""}>${esc(g.name)}</option>`).join("");
  return Array.from({ length: sockets(guid) }, (_, slot) => `<label class="socket">Socket ${slot + 1}
      <select data-gem-guid="${guid}" data-part="${part}" data-slot="${slot}">
        <option value="">Empty</option>${options(gems[slot])}</select></label>`).join("");
}

function deckRows(part, others) {
  const rows = [...deckPage.draft.cards.entries()].filter(([, e]) => e[part] > 0)
    .sort(([a], [b]) => cardName(a).localeCompare(cardName(b)));
  return rows.map(([guid, e]) => `<div class="deck-item">
    <div class="deck-row">
      <button class="deck-name" data-remove="${guid}" data-part="${part}" title="Remove one copy">${esc(cardName(guid))}</button>
      <span>×${e[part]}</span>
      <button class="secondary" data-move="${guid}" data-to="${others}">${others === "reserve" ? "To reserves" : "To main"}</button>
    </div>
    ${sockets(guid) ? `<div class="socket-row">${socketPickers(guid, part, e.gems[part] || [])}</div>` : ""}
    </div>`).join("");
}

function renderDeckList() {
  const { main, reserve } = totals();
  const mainRows = deckRows("main", "reserve");
  const reserveRows = deckRows("reserve", "main");
  $("#deck-list").innerHTML =
    `<h3 class="deck-heading">Main deck (${main})</h3>` +
    (mainRows || `<p class="status">Click cards in the collection to add them.</p>`) +
    `<h3 class="deck-heading">Reserves (${reserve}/${RESERVE_LIMIT})</h3>` +
    (reserveRows || `<p class="status">Move copies here to keep them in reserve.</p>`);
}

function renderStats() {
  const costs = Array(7).fill(0);
  const types = {};
  for (const [guid, entry] of deckPage.draft.cards) {
    const card = deckPage.cards.find((c) => c.guid === guid);
    const copies = entry.main;   // reserves are not in the main deck's curve
    if (!card || !copies) continue;
    costs[Math.min(6, Number(card.cost) || 0)] += copies;
    types[card.type || "Other"] = (types[card.type || "Other"] || 0) + copies;
  }
  const curve = costs.map((n, i) => `${i === 6 ? "6+" : i}: ${n}`).join(" · ");
  const kinds = Object.entries(types).map(([name, n]) => `${esc(name)} ${n}`).join(" · ") || "none yet";
  $("#deck-stats").innerHTML = `<p><strong>Cost</strong> ${curve}</p><p><strong>Types</strong> ${kinds}</p>`;
}

function renderErrors(result) {
  const items = result.errors.map((e) => `<li class="error">${esc(errorText(e))}</li>`)
    .concat(result.warnings.map((w) => `<li class="warning">${esc(errorText(w))}</li>`));
  $("#deck-errors").innerHTML = items.length ? items.join("") : `<li class="ok">The deck is valid for ${esc(FORMAT_NAMES.find(([v]) => v === deckPage.draft.format)?.[1] || "no format")}.</li>`;
}

// Live validation: asks the server (which owns the rules) after the edits settle; a stale answer is dropped.
function scheduleValidation() {
  clearTimeout(validateTimer);
  validateTimer = setTimeout(checkDeck, 250);
}

async function checkDeck() {
  const token = ++validateToken;
  try {
    const result = await api("/api/profile/decks/validate", deckPayload());
    if (token === validateToken) renderErrors(result);
  } catch (err) {
    if (token === validateToken) $("#deck-errors").innerHTML = `<li class="error">${esc(err.message)}</li>`;
  }
}

function renderDeckPanel() {
  const draft = deckPage.draft;
  const select = $("#deck-select");
  const formatDecks = deckPage.decks.filter((d) => (d.format || "") === (draft.format || ""));
  select.innerHTML = `<option value="">New deck</option>` + formatDecks.map((d) =>
    `<option value="${d.id}" ${d.id === draft.id ? "selected" : ""}>${esc(d.name)}</option>`).join("");
  select.value = draft.id ?? "";
  $("#deck-format").innerHTML = FORMAT_NAMES.map(([value, label]) => `<option value="${value}">${label}</option>`).join("");
  $("#deck-format").value = draft.format;
  if (document.activeElement !== $("#deck-name")) $("#deck-name").value = draft.name;
  $("#deck-champion").innerHTML = `<option value="">No champion</option>` + deckPage.champions.map((c) =>
    `<option value="${c.guid}" ${c.guid === draft.champion_guid ? "selected" : ""}>${esc(cardName(c.guid))}</option>`).join("");
  const { main, reserve } = totals();
  $("#deck-count").textContent = `${main} cards in the main deck (at least ${DECK_MINIMUM} to play), ${reserve} in reserve`;
  $("#deck-count").className = `status${main < DECK_MINIMUM ? " short" : ""}`;
  $("#deck-delete").hidden = draft.id === null;
  renderDeckList();
  renderStats();
  scheduleValidation();
}

function deckPayload() {
  const draft = deckPage.draft;
  const cards = [];
  for (const [guid, entry] of draft.cards) {
    // Each copy carries the gems its part's pickers chose (empty sockets are left out).
    const gemsFor = (part, copies) => Array.from({ length: copies }, () => (entry.gems[part] || []).filter(Boolean));
    if (entry.main > 0) cards.push({ guid, quantity: entry.main, reserve: false, gems: gemsFor("main", entry.main) });
    if (entry.reserve > 0) cards.push({ guid, quantity: entry.reserve, reserve: true, gems: gemsFor("reserve", entry.reserve) });
  }
  return {
    id: draft.id,
    name: $("#deck-name").value.trim() || "New deck",
    format: draft.format,
    champion_guid: $("#deck-champion").value,
    cards,
  };
}

async function saveDeck() {
  try {
    const saved = await api("/api/profile/decks", deckPayload());
    deckPage.decks = (await api("/api/profile/decks")).decks;
    deckPage.draft.id = saved.id;
    deckPage.draft.name = deckPayload().name;
    $("#deck-status").textContent = saved.valid ? "Saved." : `Saved as a draft: ${saved.cards} cards, below the minimum.`;
    renderCollection();
  } catch (err) {
    $("#deck-status").textContent = err.message;
  }
}

async function deleteDeck() {
  const id = deckPage.draft.id;
  if (id === null || !confirm("Delete this deck?")) return;
  try {
    await api(`/api/profile/decks/${id}`, null, "DELETE");
    deckPage.decks = (await api("/api/profile/decks")).decks;
    deckPage.draft = emptyDraft();
    $("#deck-status").textContent = "Deleted.";
    renderCollection();
  } catch (err) {
    $("#deck-status").textContent = err.message;
  }
}

/* ------------------------------------------------------------------ */
/* Wiring                                                              */
/* ------------------------------------------------------------------ */

$("#nav-battle").addEventListener("click", () => showView("lobby"));
$("#nav-collection").addEventListener("click", () => showView("collection"));
$("#nav-settings").addEventListener("click", () => openSettings($("#settings-panel").classList.contains("hidden")));
$("#settings-close").addEventListener("click", () => openSettings(false));
$("#settings-folder").addEventListener("click", () => chooseHexFolder(false));
// Reading the folder makes the backgrounds and the boards available: draw the main page again.
window.addEventListener("hex-assets-ready", () => {
  renderFolderStatus();
  if (!$("#home").classList.contains("hidden")) applyHomeBackground();
});
$("#home-background").addEventListener("change", (e) => chooseBackground(e.target.value));
$("#lobby-home").addEventListener("click", () => showView("home"));
$("#collection-home").addEventListener("click", () => showView("home"));
$("#collection-search").addEventListener("input", renderCollectionGrid);
$("#collection-sort").addEventListener("change", renderCollectionGrid);
$("#collection-grid").addEventListener("click", (e) => {
  const owned = e.target.closest("[data-guid]");
  if (owned) addCopy(owned.dataset.guid, "main");
});
$("#deck-list").addEventListener("change", (e) => {
  const picker = e.target.closest("[data-gem-guid]");
  if (!picker) return;
  const entry = deckPage.draft.cards.get(picker.dataset.gemGuid);
  if (!entry) return;
  entry.gems[picker.dataset.part][Number(picker.dataset.slot)] = picker.value;
  scheduleValidation();
});
$("#deck-list").addEventListener("click", (e) => {
  const remove = e.target.closest("[data-remove]");
  if (remove) return removeCopy(remove.dataset.remove, remove.dataset.part);
  const move = e.target.closest("[data-move]");
  if (move) moveCopy(move.dataset.move, move.dataset.to);
});
$("#deck-select").addEventListener("change", (e) => {
  $("#deck-status").textContent = "";
  e.target.value === "" ? (deckPage.draft = { ...emptyDraft(), format: deckPage.draft.format }, renderCollection())
                        : loadDraft(Number(e.target.value));
});
$("#deck-format").addEventListener("change", (e) => {
  // A saved deck belongs to its format: changing the format starts a new deck in it, keeping the cards chosen so far.
  deckPage.draft.format = e.target.value;
  deckPage.draft.id = null;
  $("#deck-status").textContent = "Saving will create a new deck in this format.";
  renderCollection();
});
$("#deck-new").addEventListener("click", () => {
  deckPage.draft = { ...emptyDraft(), format: deckPage.draft.format };
  $("#deck-status").textContent = "";
  renderCollection();
});
$("#deck-name").addEventListener("input", (e) => { deckPage.draft.name = e.target.value; });
$("#deck-champion").addEventListener("change", (e) => { deckPage.draft.champion_guid = e.target.value; scheduleValidation(); });
$("#deck-save").addEventListener("click", saveDeck);
$("#deck-delete").addEventListener("click", deleteDeck);

/* ------------------------------------------------------------------ */
/* Replay: a captured match run back through the battle reducer        */
/* ------------------------------------------------------------------ */

// ?replay=<match id> opens a match's capture. Each step rebuilds the battle state from the first packets, so the
// scrubber can go backwards as well as forwards.
const replay = { capture: null, step: 0 };

async function openReplay(matchId) {
  try {
    replay.capture = await api(`/api/matches/${matchId}/capture`);
  } catch (err) {
    toast(`Could not load the capture: ${err.message}`);
    return showView("home");
  }
  showView("battle");
  $("#replay-bar").classList.remove("hidden");
  $("#replay-step").max = replay.capture.packets.length;
  replayTo(replay.capture.packets.length);
}

function replayTo(step) {
  const capture = replay.capture;
  step = Math.max(0, Math.min(step, capture.packets.length));
  resetState();
  S.me = capture.match.player_uid;
  S.opp = capture.match.opponent_uid;
  S.info = capture.match;
  S.names[S.me] = "You";
  S.busy = false;
  for (const packet of capture.packets.slice(0, step)) {
    S.after = packet.seq;
    (packet.events || []).forEach(applyEvent);
  }
  replay.step = step;
  $("#replay-step").value = step;
  $("#replay-label").textContent = `Packet ${step} of ${capture.packets.length}`;
  loadCatalog().then(render).catch((err) => toast(err.message));
}

$("#replay-step").addEventListener("input", (e) => replayTo(Number(e.target.value)));
$("#replay-prev").addEventListener("click", () => replayTo(replay.step - 1));
$("#replay-next").addEventListener("click", () => replayTo(replay.step + 1));
$("#replay-exit").addEventListener("click", () => { $("#replay-bar").classList.add("hidden"); showView("home"); });

const replayId = new URLSearchParams(location.search).get("replay");
if (replayId && /^m\d+$/.test(replayId)) openReplay(replayId);
else showView("home");
