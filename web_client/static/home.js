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
  const drawable = layers && (layers.parts.length || layers.emitters.length) ? layers : null;
  disposeHomeScene();
  $("#home-scene").classList.toggle("hidden", !drawable);
  if (!drawable) return;
  const { BackgroundScene } = await import("/static/background3d.js");
  if (token === homeScene.token) homeScene.scene = new BackgroundScene($("#home-scene"), drawable);
}

function disposeHomeScene() {
  if (homeScene.scene) homeScene.scene.dispose();
  homeScene.scene = null;
}

function openSettings(open) {
  $("#settings-panel").classList.toggle("hidden", !open);
  if (!open) return;
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

const deckPage = { loaded: false, cards: [], decks: [], champions: [], draft: null };
const DECK_MINIMUM = 60;   // the client's c_MinimumDeckSize: a smaller deck is saved as a draft

function emptyDraft() {
  return { id: null, name: "New deck", champion_guid: "", cards: new Map() };
}

function ownedCopies(guid) {
  const card = deckPage.cards.find((c) => c.guid === guid);
  return card ? card.quantity : 0;
}

function deckCopies() {
  return [...deckPage.draft.cards.values()].reduce((sum, n) => sum + n, 0);
}

function cardName(guid) {
  const tpl = S.catalog.cards[guid];
  return (tpl && tpl.name) || guid.slice(0, 8);
}

async function openCollection() {
  if (!deckPage.loaded) {
    try {
      const [col, decks, champs] = await Promise.all([
        api("/api/collection"), api("/api/profile/decks"), api("/api/profile/champions")]);
      deckPage.cards = col.cards;
      deckPage.decks = decks.decks;
      deckPage.champions = champs.champions;
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
    id: saved.id, name: saved.name, champion_guid: saved.champion_guid,
    cards: new Map(saved.cards.map((c) => [c.guid, c.quantity])),
  };
  renderCollection();
}

function addCopy(guid) {
  const draft = deckPage.draft;
  const have = draft.cards.get(guid) || 0;
  if (have >= ownedCopies(guid)) return toast("No more copies of that card are owned.");
  draft.cards.set(guid, have + 1);
  renderCollection();
}

function removeCopy(guid) {
  const draft = deckPage.draft;
  const have = draft.cards.get(guid) || 0;
  if (have <= 1) draft.cards.delete(guid); else draft.cards.set(guid, have - 1);
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
    const inDeck = deckPage.draft.cards.get(c.guid) || 0;
    // The grid uses the battle's card renderer; a pseudo card carries the template and a unique uid for the slot.
    const card = cardHTML({ uid: 900000 + i, card_id: c.guid, controller: S.me, state: 0 }, "owned-card");
    return `<div class="owned" data-guid="${c.guid}" title="${esc(c.name)}">${card}
      <span class="owned-count">${inDeck}/${c.quantity}</span></div>`;
  }).join("");
}

function renderDeckPanel() {
  const draft = deckPage.draft;
  const select = $("#deck-select");
  select.innerHTML = `<option value="">New deck</option>` + deckPage.decks.map((d) =>
    `<option value="${d.id}" ${d.id === draft.id ? "selected" : ""}>${esc(d.name)}</option>`).join("");
  select.value = draft.id ?? "";
  if (document.activeElement !== $("#deck-name")) $("#deck-name").value = draft.name;
  $("#deck-champion").innerHTML = `<option value="">No champion</option>` + deckPage.champions.map((c) =>
    `<option value="${c.guid}" ${c.guid === draft.champion_guid ? "selected" : ""}>${esc(cardName(c.guid))}</option>`).join("");
  const copies = deckCopies();
  $("#deck-count").textContent = `${copies} cards (at least ${DECK_MINIMUM} to play)`;
  $("#deck-count").className = `status${copies < DECK_MINIMUM ? " short" : ""}`;
  $("#deck-delete").hidden = draft.id === null;
  const rows = [...draft.cards.entries()].sort(([a], [b]) => cardName(a).localeCompare(cardName(b)));
  $("#deck-list").innerHTML = rows.length
    ? rows.map(([guid, n]) => `<button class="deck-row" data-remove="${guid}" title="Remove one copy">
        <span>${esc(cardName(guid))}</span><span>×${n}</span></button>`).join("")
    : `<p class="status">Click cards in the collection to add them.</p>`;
}

function deckPayload() {
  const draft = deckPage.draft;
  return {
    id: draft.id,
    name: $("#deck-name").value.trim() || "New deck",
    champion_guid: $("#deck-champion").value,
    cards: [...draft.cards.entries()].map(([guid, quantity]) => ({ guid, quantity })),
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
$("#home-background").addEventListener("change", (e) => chooseBackground(e.target.value));
$("#lobby-home").addEventListener("click", () => showView("home"));
$("#collection-home").addEventListener("click", () => showView("home"));
$("#collection-search").addEventListener("input", renderCollectionGrid);
$("#collection-sort").addEventListener("change", renderCollectionGrid);
$("#collection-grid").addEventListener("click", (e) => {
  const owned = e.target.closest("[data-guid]");
  if (owned) addCopy(owned.dataset.guid);
});
$("#deck-list").addEventListener("click", (e) => {
  const row = e.target.closest("[data-remove]");
  if (row) removeCopy(row.dataset.remove);
});
$("#deck-select").addEventListener("change", (e) => {
  $("#deck-status").textContent = "";
  e.target.value === "" ? (deckPage.draft = emptyDraft(), renderCollection()) : loadDraft(Number(e.target.value));
});
$("#deck-new").addEventListener("click", () => { deckPage.draft = emptyDraft(); $("#deck-status").textContent = ""; renderCollection(); });
$("#deck-name").addEventListener("input", (e) => { deckPage.draft.name = e.target.value; });
$("#deck-champion").addEventListener("change", (e) => { deckPage.draft.champion_guid = e.target.value; });
$("#deck-save").addEventListener("click", saveDeck);
$("#deck-delete").addEventListener("click", deleteDeck);

showView("home");
