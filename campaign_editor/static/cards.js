// The card editor, opened from the map page in its own tab (cards.html).
// It keeps its own copies of the small helpers app.js has, and reads the
// session from /api/me like the map page does.
//
// Layout: the cards of the chosen set on the left, painted small by the battle
// board's painter (CardFace.paintFace); the open card on the right, painted large,
// with its fields and its abilities. The ability editor is at the end of this file.

const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function getJSON(url) {
  const response = await fetch(url);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || response.statusText);
  return body;
}

// Rejects with the server's message; the error keeps the body (a rejected ability
// save lists its problems there).
async function postJSON(url, body) {
  const response = await fetch(url, { method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" } });
  const result = await response.json();
  if (!response.ok) throw Object.assign(new Error(result.error || response.statusText), { body: result });
  return result;
}

const els = { status: document.getElementById("status") };
let account = null;   // /api/me

window.addEventListener("DOMContentLoaded", async () => {
  try {
    account = await getJSON("/api/me");
    if (!account.logged_in) {
      els.status.textContent = "Log in on the campaign editor first, then reopen this tab";
      return;
    }
    els.status.textContent = account.admin ? "admin: editing the shared card data" : "read-only";
    showCardsTab();
  } catch (error) {
    els.status.textContent = error.message;
  }
});

// ---------------------------------------------------------------- cards
let cardsState = null;      // {cards, options} from /api/cards
let cardOpen = null;        // the /api/card payload open in the form, or a blank new card
let cardLinks = [];         // the open card's ability links [{guid, name, source, overrides}], saved with the card
const cardChecked = new Set();
let cardsSearchTimer = null;

const canEditCards = () => Boolean(account && account.logged_in && account.admin);

function showCardsTab() {
  const body = document.getElementById("tab-cards");
  if (!body.dataset.ready) {
    body.dataset.ready = "1";
    body.innerHTML = `<div class="cards-layout">
      <aside class="cards-browse" aria-label="Cards">
        <div class="cards-head">
          <select id="cards-set" aria-label="Card set"></select>
          <span class="muted" id="cards-set-count"></span>
          <button type="button" id="cards-publish" title="Publish this set's .incdata to the server, which sends it to each client at login">Publish to server</button>
          <button type="button" id="cards-incdata" title="Download a client .incdata of this set: a custom set's Done cards, or a Records set's edited Done cards, with the abilities they use">Build .incdata</button>
        </div>
        <div class="cards-head">
          <input id="cards-search" type="search" placeholder="Find card (name or GUID)" autocomplete="off">
          <button type="button" id="cards-new" hidden>New card</button>
          <button type="button" id="cards-folder" title="Read the client's card frames and art from your Hex folder">Hex folder</button>
          <input type="file" id="cards-folder-input" webkitdirectory multiple hidden>
        </div>
        <div class="cards-assign" id="cards-assign" hidden>
          <select id="cards-assign-set" aria-label="Card set"></select>
          <button type="button" id="cards-assign-go">Assign checked</button>
          <span class="muted" id="cards-assign-count"></span>
        </div>
        <p class="cards-frames-note" id="cards-frames-note" hidden></p>
        <section class="cards-set-icon" id="cards-set-icon" aria-label="Set icon" hidden></section>
        <section class="cards-packs" id="cards-packs" aria-label="Store packs" hidden></section>
        <ul class="cards-grid" id="cards-list"></ul>
      </aside>
      <section class="cards-detail" id="card-form" aria-label="Open card">
        <p class="muted">Choose a card on the left.</p>
      </section>
    </div>
    <section class="effect-reference" id="effect-reference" aria-label="Effect templates">
      <h2>Effect templates</h2>
      <p class="muted small-text">Each kind of effect a card ability is built from, with how many Records templates use it.
        A card-modifier effect is listed by the modifier it applies.</p>
      <div id="effect-reference-body"><p class="muted small-text">Loading…</p></div>
    </section>`;
    body.addEventListener("input", (event) => {
      if (event.target.closest("#card-edit")) {
        scheduleFramePreview();
        return;
      }
      if (event.target.id === "dsl-text") {
        dslTextInput();
        return;
      }
      if (event.target.matches("[data-dsl-search]")) {
        dslSearchInput(event.target);
        return;
      }
      if (event.target.matches("[data-picker]")) {
        schedulePickerSearch(event.target);
        return;
      }
      if (event.target.id !== "cards-search") return;
      clearTimeout(cardsSearchTimer);
      cardsSearchTimer = setTimeout(() => loadCardList().catch(reportCardError), 200);
    });
    body.addEventListener("change", (event) => {
      if (event.target.id === "cards-set") {
        loadCardList().catch(reportCardError);
        loadStorePacks().catch(reportCardError);
        loadSetIcon().catch(reportCardError);
        return;
      }
      if (event.target.matches("[data-art-file]")) {
        const file = event.target.files[0];
        event.target.value = "";
        if (file) uploadArtFile(event.target.dataset.artFile, event.target.dataset.guid, file).catch(reportCardError);
        return;
      }
      if (event.target.closest("#card-edit")) scheduleFramePreview();
      if (event.target.matches("[data-card-check]")) {
        if (event.target.checked) cardChecked.add(event.target.dataset.cardCheck);
        else cardChecked.delete(event.target.dataset.cardCheck);
        document.getElementById("cards-assign-count").textContent = `${cardChecked.size} checked`;
      }
    });
    body.addEventListener("click", (event) => onCardsClick(event).catch(reportCardError));
    // Enter in the chain's number box uses the number.
    body.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || !event.target.matches("[data-dsl-number]")) return;
      event.preventDefault();
      try {
        dslUseNumber();
        dslAfterChoice().catch(reportCardError);
      } catch (error) {
        reportCardError(error);
      }
    });
    // The faces repaint once the client's frames or a card's art have loaded.
    window.addEventListener("hex-assets-ready", onFramesLoaded);
    watchCardFrames();
    body.addEventListener("paste", (event) => {
      const slot = event.target.closest && event.target.closest("[data-art-paste]");
      const file = slot && imageFromClipboard(event);
      if (!file) return;
      event.preventDefault();
      uploadArtFile(slot.dataset.artPaste, slot.dataset.guid, file).catch(reportCardError);
    });
    body.addEventListener("submit", (event) => {
      if (event.target.id === "store-packs-form") {
        event.preventDefault();
        saveStorePacks(event.target).catch(reportCardError);
        return;
      }
      if (event.target.id !== "card-edit") return;
      event.preventDefault();
      saveCard(event.target).catch(reportCardError);
    });
  }
  loadCardList().catch(reportCardError);
  loadEffectReference().catch(reportCardError);
}

// The bottom of the page: what each kind of effect template does, for reference.
async function loadEffectReference() {
  const { effects } = await getJSON("/api/effect-reference");
  const rows = effects.map((effect) => `<tr>
      <td><code>${esc(effect.key)}</code></td>
      <td class="num">${esc(effect.count)}</td>
      <td>${esc(effect.description)}</td>
      <td class="muted small-text">${esc(effect.example)}</td>
    </tr>`).join("");
  document.getElementById("effect-reference-body").innerHTML = `<table class="effect-table">
    <thead><tr><th>Effect template</th><th>Records</th><th>What it does</th><th>Example text</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

// ---- art: set icons and pack or store artwork, from a file or a paste ----

function imageFromClipboard(event) {
  for (const item of (event.clipboardData && event.clipboardData.items) || []) {
    if (item.type.startsWith("image/")) return item.getAsFile();
  }
  return null;
}

function readAsDataURL(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("the file could not be read"));
    reader.readAsDataURL(file);
  });
}

// The artwork is for a custom set's icon ("set-icon"), a pack's artwork ("pack") or its
// store artwork ("store"). The saved file is shown again, so each save bumps its URL.
async function uploadArtFile(kind, guid, file) {
  if (!file.type.startsWith("image/")) throw new Error("choose an image file");
  const image = await readAsDataURL(file);
  if (kind === "set-icon") {
    renderSetIcon(await postJSON("/api/edit/set-icon", { set_guid: guid, image }));
    document.getElementById("cards-set-icon").querySelector("[data-art-status]").textContent = "Saved.";
    return;
  }
  await postJSON("/api/edit/pack-art", { pack_guid: guid, kind, image });
  await loadStorePacks();
  document.getElementById("store-packs-status").textContent = "Art saved.";
}

function artSlot(kind, guid, url, label) {
  const shown = url ? `<img src="${esc(url)}?v=${Date.now()}" alt="${esc(label)}" width="48" height="48">`
    : `<span class="muted small-text">no ${esc(label)}</span>`;
  return `<div class="art-slot" data-art-paste="${esc(kind)}" data-guid="${esc(guid)}" tabindex="0"
      title="Paste an image (Ctrl+V) with this selected, or upload one">${shown}
    <label class="art-upload">Upload<input type="file" accept="image/*" data-art-file="${esc(kind)}" data-guid="${esc(guid)}" hidden></label></div>`;
}

async function loadSetIcon() {
  const set = document.getElementById("cards-set").value;
  const block = document.getElementById("cards-set-icon");
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(set)) {
    block.hidden = true;
    return;
  }
  renderSetIcon(await getJSON(`/api/set-icon?set=${encodeURIComponent(set)}`));
}

function renderSetIcon(icon) {
  const block = document.getElementById("cards-set-icon");
  block.hidden = icon.source !== "custom";
  if (block.hidden) return;
  const editable = canEditCards();
  block.innerHTML = `<h3>Set icon: ${esc(icon.set_name)}</h3>
    <div class="art-slot art-slot-large" data-art-paste="set-icon" data-guid="${esc(icon.set_guid)}" tabindex="0"
        title="Paste an image (Ctrl+V) with this selected, or upload one">
      ${icon.url ? `<img src="${esc(icon.url)}?v=${Date.now()}" alt="${esc(icon.set_name)} icon" width="64" height="64">`
        : `<span class="muted small-text">No icon yet</span>`}
      <label class="art-upload"${editable ? "" : " hidden"}>Upload<input type="file" accept="image/*" data-art-file="set-icon" data-guid="${esc(icon.set_guid)}" hidden></label>
    </div>
    <p class="muted small-text">A square picture of at least 32 pixels. It is saved as a ${icon.size} × ${icon.size} PNG and keeps its
      transparency. This is for the editor's preview: the client's frames read set icons from their atlas (SetIcon, by set number),
      and this set has none there yet, so the game shows no icon for it.</p>
    <p class="small-text" data-art-status></p>`;
}

// The store listing of a Records card set: which of its packs the store sells, and for how
// much. A custom set's packs are not offered (the server reads Records cards only).
// The client's booster icons, for comparison when uploading pack art (loaded once).
let packArtReference = [];
// The reference is optional: if it cannot be loaded, the panel still shows.
async function loadPackArtReference() {
  if (packArtReference.length) return;
  try {
    packArtReference = (await getJSON("/api/pack-art-reference")).icons;
  } catch (error) {
    packArtReference = [];
  }
}

async function loadStorePacks() {
  const panel = document.getElementById("cards-packs");
  const set = document.getElementById("cards-set").value;
  panel.hidden = !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(set);
  if (panel.hidden) return;
  await loadPackArtReference();
  renderStorePacks(await getJSON(`/api/store-listing?set=${encodeURIComponent(set)}`));
}

function renderStorePacks(listing) {
  const panel = document.getElementById("cards-packs");
  panel.dataset.set = listing.set_guid;
  const disabled = canEditCards() ? "" : " disabled";
  const create = listing.source === "custom" && canEditCards()
    ? `<p><button type="button" data-create-pack="Booster">Add booster</button> ` +
      `<button type="button" data-create-pack="FullSet">Add Full Pack</button></p>` : "";
  const options = (values, chosen) => values.map((v) =>
    `<option value="${esc(v)}"${v === chosen ? " selected" : ""}>${esc(v)}</option>`).join("");
  const rows = listing.packs.map((pack) => {
    const row = pack.listing || {};
    return `<tr data-pack-guid="${esc(pack.pack_guid)}">
      <td><input type="checkbox" data-field="listed"${pack.listing ? " checked" : ""}${disabled}></td>
      <td><span title="${esc(pack.pack_guid)}">${esc(pack.records_name)}</span><br><span class="muted small-text">${esc(pack.type_label)}</span>
        ${pack.source === "custom" ? `<br><span class="muted small-text" title="Pack artwork GUID">art ${esc(pack.pack_art_guid)}</span>
        <br><span class="muted small-text" title="Store artwork GUID">store ${esc(pack.store_art_guid)}</span>` : ""}</td>
      <td><input data-field="name" value="${esc(row.name || pack.records_name)}"${disabled}>
        <input type="hidden" data-field="short_desc" value="${esc(row.short_desc || "")}"></td>
      <td><input data-field="price" type="number" min="0" max="1000000" step="1" value="${esc(row.price ?? 100)}"${disabled}></td>
      <td><select data-field="currency"${disabled}>${options(listing.currencies, row.currency || "Gold")}</select></td>
      <td><select data-field="store_tab"${disabled}>${options(listing.store_tabs, row.store_tab || "ShopBoosterTab")}</select></td>
      <td>${pack.source === "custom" ? `${artSlot("pack", pack.pack_guid, pack.pack_art_url, "pack art")}${artSlot("store", pack.pack_guid, pack.store_art_url, "store art")}` : ""}</td>
    </tr>`;
  }).join("");
  const reference = packArtReference.length
    ? `<details class="pack-art-reference"><summary class="small-text">Existing pack icons (client, 512 × 512 px square)</summary>
        <div class="pack-art-strip">${packArtReference.map((icon) =>
          `<img src="${esc(icon.url)}" alt="${esc(icon.name)}" title="${esc(icon.name)}" width="48" height="48">`).join("")}</div></details>`
    : "";
  panel.innerHTML = `<h3>Store packs: ${esc(listing.set_name)}</h3>${create}${reference}` +
    (rows ? `<form id="store-packs-form"><table class="store-packs"><thead><tr>
        <th>Sell</th><th>Pack</th><th>Name</th><th>Price</th><th>Currency</th><th>Tab</th><th>Art</th>
      </tr></thead><tbody>${rows}</tbody></table>
      <p class="muted small-text">A Full Pack opens 4 of every card in the set. Changes apply when the server restarts.</p>
      <button type="submit"${disabled}>Save store packs</button>
      <span class="muted" id="store-packs-status"></span></form>`
      : `<p class="muted">This set has no card packs yet.</p>`);
}

async function createStorePack(packType) {
  const listing = await postJSON("/api/edit/store-pack",
    { set_guid: document.getElementById("cards-packs").dataset.set, pack_type: packType });
  renderStorePacks(listing);
}

// Zooms the graph in or out (delta), or back to 100% (delta 0).
function zoomGraph(button, delta) {
  const view = button.closest(".ability-block");
  const stage = view && view.querySelector("[data-graph-stage]");
  const graph = view && view.querySelector("[data-graph]");
  if (!stage || !graph) return;
  const scale = delta === 0 ? 1 : Math.min(2.5, Math.max(0.4, Number(graph.dataset.scale || 1) + delta));
  graph.dataset.scale = String(scale);
  graph.style.transform = `scale(${scale})`;
  graph.style.transformOrigin = "0 0";
  stage.style.width = `${Number(stage.dataset.w) * scale}px`;
  stage.style.height = `${Number(stage.dataset.h) * scale}px`;
  view.querySelector("[data-graph-scale]").textContent = `${Math.round(scale * 100)}%`;
}

async function saveStorePacks(form) {
  const packs = [...form.querySelectorAll("tbody tr")].map((row) => {
    const field = (name) => row.querySelector(`[data-field="${name}"]`);
    return { pack_guid: row.dataset.packGuid, listed: field("listed").checked, name: field("name").value,
      price: field("price").value, currency: field("currency").value, store_tab: field("store_tab").value,
      short_desc: field("short_desc").value };
  });
  const listing = await postJSON("/api/edit/store-listing",
    { set_guid: document.getElementById("cards-packs").dataset.set, packs });
  renderStorePacks(listing);
  document.getElementById("store-packs-status").textContent = "Saved. Restart the server to apply it to the store.";
}

function reportCardError(error) {
  els.status.textContent = error.message;
}

async function loadCardList() {
  const text = document.getElementById("cards-search").value.trim();
  const set = document.getElementById("cards-set").value;
  cardsState = await getJSON(`/api/cards?q=${encodeURIComponent(text)}&set=${encodeURIComponent(set)}&limit=1000`);
  const editable = canEditCards();
  document.getElementById("cards-new").hidden = !editable;
  document.getElementById("cards-publish").hidden = !editable;
  document.getElementById("cards-assign").hidden = !editable;
  document.getElementById("cards-set").innerHTML = `<option value="">All cards</option>` +
    `<option value="none"${set === "none" ? " selected" : ""}>No set</option>` +
    cardsState.options.sets.map((s) => `<option value="${esc(s.guid)}" title="${esc(s.file)}"${s.guid === set ? " selected" : ""}>` +
      `${esc(s.name)} (${s.source === "custom" ? "custom" : "Records"})</option>`).join("");
  const shown = cardsState.cards.length, total = cardsState.total;
  document.getElementById("cards-set-count").textContent = shown < total ? `${shown} of ${total} cards` : `${total} card(s)`;
  document.getElementById("cards-assign-set").innerHTML = `<option value="">No set</option>` +
    cardsState.options.sets.map((s) => `<option value="${esc(s.guid)}">${esc(s.name)}</option>`).join("");
  renderCardList();
}

// The list is a grid of small painted cards. Each tile gets its thumbnail when it
// scrolls into view; thumbnails are cached by their face, so a new search reuses them.
function renderCardList() {
  const editable = canEditCards();
  const open = cardOpen && cardOpen.card.guid;
  const list = document.getElementById("cards-list");
  list.innerHTML = cardsState.cards.map((c, index) =>
    `<li class="card-tile${open === c.guid ? " open" : ""}" data-index="${index}">` +
    `<button type="button" class="card-tile-open" data-card-open="${esc(c.guid)}" title="${esc(`${c.name} · ${c.rarity} · ${c.card_type} · ${c.cost}${c.set_name ? ` · ${c.set_name}` : ""}`)}">` +
      `<span class="card-thumb-slot"></span></button>` +
    `<span class="card-tile-caption">` +
      (editable ? `<input type="checkbox" data-card-check="${esc(c.guid)}"${cardChecked.has(c.guid) ? " checked" : ""} aria-label="Select ${esc(c.name)}">` : "") +
      `<span class="card-tile-name" title="${esc(c.name)}">${esc(c.name)}</span>` +
      (c.source !== "records" ? `<span class="card-source card-source-${esc(c.source)}" title="${esc(c.source)}">${esc(c.source[0])}</span>` : "") +
    `</span></li>`).join("") || `<li class="muted">No card matches.</li>`;
  document.getElementById("cards-assign-count").textContent = `${cardChecked.size} checked`;
  observeTiles(list);
}

function markOpenTile() {
  const open = cardOpen && cardOpen.card.guid;
  for (const tile of document.querySelectorAll(".card-tile")) {
    const button = tile.querySelector("[data-card-open]");
    tile.classList.toggle("open", Boolean(button && button.dataset.cardOpen === open));
  }
}

// ---------------------------------------------------------------- thumbnails
const THUMB_W = 160, THUMB_H = 224;   // canvas pixels; CSS sizes the tile
const thumbCache = new Map();         // face key -> canvas
let framesGeneration = 0;             // bumped when the client frames load, so faces repaint
let tileObserver = null;

function observeTiles(list) {
  if (tileObserver) tileObserver.disconnect();
  if (!("IntersectionObserver" in window)) {
    list.querySelectorAll(".card-tile").forEach(fillTile);
    return;
  }
  tileObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      tileObserver.unobserve(entry.target);
      fillTile(entry.target);
    }
  }, { root: list, rootMargin: "200px" });
  list.querySelectorAll(".card-tile").forEach((tile) => tileObserver.observe(tile));
}

function fillTile(tile) {
  const card = cardsState.cards[Number(tile.dataset.index)];
  const slot = tile.querySelector(".card-thumb-slot");
  if (!card || !slot || !window.CardFace) return;
  slot.replaceChildren(thumbFor(card));
}

function thumbFor(card) {
  const face = card.frame ? faceFromFields(card, card.frame, card) : null;
  const key = `${framesGeneration}|${JSON.stringify(face)}`;
  let thumb = thumbCache.get(key);
  if (!thumb) {
    thumb = document.createElement("canvas");
    thumb.width = THUMB_W;
    thumb.height = THUMB_H;
    thumb.className = "card-thumb";
    thumb.setAttribute("role", "img");
    thumb.setAttribute("aria-label", card.name);
    drawThumb(thumb, face);
    thumbCache.set(key, thumb);
  }
  // A canvas sits in one place only; a card listed again after a search moves it.
  return thumb;
}

function drawThumb(thumb, face) {
  const painted = CardFace.paintFace(face, () => drawThumb(thumb, face), window.HexAssets);
  const ctx = thumb.getContext("2d");
  ctx.clearRect(0, 0, THUMB_W, THUMB_H);
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(painted, 0, 0, THUMB_W, THUMB_H);
}

function framesLoaded() {
  return Boolean(window.HexAssets && window.HexAssets.cardChrome && window.HexAssets.cardChrome());
}

// The client frames come from this browser's storage (a folder read on an earlier
// visit) or from the Hex folder button; until then the painter draws its own face.
function watchCardFrames() {
  if (window.HexAssets && window.HexAssets.cardChrome) window.HexAssets.cardChrome(onFramesLoaded);
  showFramesNote();
}

function showFramesNote() {
  const note = document.getElementById("cards-frames-note");
  if (!note) return;
  note.hidden = framesLoaded();
  note.textContent = "The client card frames are not loaded: cards are drawn with the web " +
    "painter's stand-in face. Use Hex folder to load the frames and art from your install.";
}

function onFramesLoaded() {
  framesGeneration += 1;
  thumbCache.clear();
  showFramesNote();
  if (cardsState) document.querySelectorAll(".card-tile").forEach(fillTile);
  if (faceInputs) paintCardFace(faceInputs.fields, faceInputs.frame, faceInputs.card);
}

// ---------------------------------------------------------------- the open card
async function openCard(guid) {
  cardOpen = await getJSON(`/api/card?guid=${encodeURIComponent(guid)}`);
  cardLinks = (cardOpen.ability_links || []).map((link) => ({ ...link }));
  abilityOpen = null;
  renderCardForm();
  markOpenTile();
}

function openNewCard() {
  // A new card starts in the set being browsed; "All cards" and "No set" leave it unset.
  const picked = document.getElementById("cards-set").value;
  const setGuid = picked && picked !== "none" ? picked : "";
  cardOpen = {
    card: { guid: "", name: "", set_guid: setGuid, rarity: "Common", card_type: "Troop", cost: 0,
      attack: 0, defense: 0, subtype: "", threshold: [], variable_cost: false, abilities: [] },
    source: "new", set_name: "", can_revert: false, options: cardsState.options,
  };
  cardLinks = [];
  abilityOpen = null;
  renderCardForm();
  markOpenTile();
  scheduleFramePreview();
}

function thresholdRow(entry, shards, disabled) {
  const options = shards.map((s) =>
    `<option value="${esc(s)}"${s === entry.shard ? " selected" : ""}>${esc(s)}</option>`).join("");
  return `<div class="threshold-row">
      <select data-shard${disabled}>${options}</select>
      <input type="number" data-count min="1" max="99" value="${esc(entry.count)}"${disabled}>
      ${disabled ? "" : `<button type="button" data-threshold-remove>Remove</button>`}
    </div>`;
}

// The card's keywords (CardTemplate m_AttributeFlags), one toggle per ECardAttributes
// member, named by its ToolName (cards.options: the server reads the client enum).
// An editor server started before keywords existed sends no lists: say so rather
// than show empty controls whose values it would ignore.
const STALE_SERVER = `<p class="muted small-text">The editor server is older than this page: restart it to edit these.</p>`;

function keywordsFieldset(card, options, disabled) {
  if (!options.keywords) return `<fieldset class="card-keywords"><legend>Keywords</legend>${STALE_SERVER}</fieldset>`;
  const have = new Set(card.keywords || []);
  const boxes = (options.keywords || []).map((k) => `<label class="toggle" title="${esc(k.name)}${
      k.uses ? ` (${k.uses} Records card${k.uses === 1 ? "" : "s"})` : ""}">
      <input type="checkbox" data-keyword="${esc(k.name)}"${have.has(k.name) ? " checked" : ""}${disabled}> ${esc(k.label)}</label>`).join("");
  return `<fieldset class="card-keywords"><legend>Keywords</legend><div class="keyword-grid">${boxes}</div></fieldset>`;
}

function intAttributeRow(name, value, disabled) {
  return `<div class="threshold-row int-attr-row" data-int-attr="${esc(name)}">
      <span class="int-attr-name">${esc(name)}</span>
      <input type="number" step="1" data-int-value value="${esc(value)}" aria-label="${esc(name)}"${disabled}>
      ${disabled ? "" : `<button type="button" data-int-remove>Remove</button>`}
    </div>`;
}

// The card's integer attributes (Lethal, Rage...: its m_SerializedTAC); the ones to
// add are those Records cards carry (cards.options), most used first.
function intAttributesFieldset(card, options, disabled, editable) {
  if (!options.int_attributes) return `<fieldset class="card-threshold card-int-attrs"><legend>Integer attributes</legend>${STALE_SERVER}</fieldset>`;
  const rows = Object.entries(card.int_attributes || {}).map(([name, value]) => intAttributeRow(name, value, disabled)).join("");
  const add = editable ? `<div class="threshold-row"><select data-int-add aria-label="Integer attribute to add">${
      (options.int_attributes || []).map((a) => `<option value="${esc(a.name)}">${esc(a.name)} (${esc(a.uses)} card${a.uses === 1 ? "" : "s"})</option>`).join("")}</select>
      <button type="button" data-int-attr-add>Add</button></div>` : "";
  return `<fieldset class="card-threshold card-int-attrs"><legend>Integer attributes</legend>
      <div id="card-int-rows">${rows || `<p class="muted small-text" data-int-none>None.</p>`}</div>${add}</fieldset>`;
}

// The equipped alternate of a base card: one dropdown per equipment slot. Read-only until
// equipment editing is added, so a save never changes the slots.
function equipmentFieldset(slots, items) {
  const byGuid = new Map(items.map((item) => [item.guid, item.name]));
  const choose = (current) => `<option value="">(none)</option>` + items.map((item) =>
    `<option value="${esc(item.guid)}"${item.guid === current ? " selected" : ""}>${esc(item.name)}</option>`).join("") +
    (current && !byGuid.has(current) ? `<option value="${esc(current)}" selected>${esc(current)} (missing from Records)</option>` : "");
  return `<fieldset class="card-equipment" title="The equipment this card's alternate needs. Shown for reference; editing comes later.">
      <legend>Equipment</legend>
      ${slots.map((guid, i) => `<label class="field">Slot ${i + 1} <select name="equipment_${i}" disabled>${choose(guid)}</select></label>`).join("")}
    </fieldset>`;
}

function renderCardForm() {
  const { card, source, options, can_revert: canRevert } = cardOpen;
  const editable = canEditCards();
  const disabled = editable ? "" : " disabled";
  const choose = (values, current) => values.map((v) =>
    `<option value="${esc(v)}"${v === current ? " selected" : ""}>${esc(v)}</option>`).join("");
  const sets = `<option value="">No set</option>` + options.sets.map((s) =>
    `<option value="${esc(s.guid)}"${s.guid === card.set_guid ? " selected" : ""}>${esc(s.name)}</option>`).join("");
  document.getElementById("card-form").innerHTML = `<div class="card-detail-top">
      <div class="card-face-col">
        <img id="card-face-img" class="card-face-img" alt="Card face of ${esc(card.name || "the new card")}">
        <div id="card-frame" class="card-frame"></div>
      </div>
      <form id="card-edit" class="card-edit">
      <h3>${esc(card.name || "New card")} <span class="card-source card-source-${esc(source)}">${esc(source)}</span></h3>
      <p class="muted small-text">GUID ${esc(card.guid || "assigned on save")}</p>
      <label class="field">Name <input name="name" value="${esc(card.name)}" required${disabled}></label>
      <label class="field">Set <select name="set_guid"${disabled}>${sets}</select></label>
      <label class="field">Rarity <select name="rarity"${disabled}>${choose(options.rarities, card.rarity)}</select></label>
      <label class="field">Type <select name="card_type"${disabled}>${choose(options.card_types, card.card_type)}</select></label>
      ${options.impl_stages ? `<label class="field" title="The client's ECardImplStage: it loads no card at Needs Scripting or earlier, and players can own only Done cards. Build .incdata takes Done cards.">Stage
        <select name="impl_stage"${disabled}>${options.impl_stages.map((s) =>
          `<option value="${esc(s.name)}"${s.name === (card.impl_stage || options.impl_stages[0].name) ? " selected" : ""}>${esc(s.label)}</option>`).join("")}</select></label>` : ""}
      <label class="field">Cost <input type="number" name="cost" min="0" max="99" value="${esc(card.cost)}"${disabled}></label>
      <label class="field">Attack <input type="number" name="attack" min="0" max="99" value="${esc(card.attack)}"${disabled}></label>
      <label class="field">Defense <input type="number" name="defense" min="0" max="99" value="${esc(card.defense)}"${disabled}></label>
      <label class="field">Subtype <input name="subtype" value="${esc(card.subtype)}"${disabled}></label>
      <label class="field field-wide" title="The card's sockets in order, each Major or Minor (Records writes them as SOCKETABLE tokens in the game text).">Sockets
        <input name="sockets" value="${esc((card.sockets || []).join(", "))}" placeholder="e.g. Major, Major, Minor"${disabled}></label>
      <label class="field field-wide">Flavour text <textarea name="flavor" rows="2" maxlength="500"
        placeholder="Empty keeps the card's own flavour"${disabled}>${esc(card.flavor || "")}</textarea></label>
      <label class="toggle"><input type="checkbox" name="variable_cost"${card.variable_cost ? " checked" : ""}${disabled}> Variable cost (X)</label>
      <fieldset class="card-threshold"><legend>Threshold</legend>
        <div id="card-threshold-rows">${card.threshold.map((e) => thresholdRow(e, options.shards, disabled)).join("")}</div>
        ${editable ? `<button type="button" data-threshold-add>Add colour</button>` : ""}
      </fieldset>
      ${keywordsFieldset(card, options, disabled)}
      ${intAttributesFieldset(card, options, disabled, editable)}
      ${cardOpen.equipment_modified ? equipmentFieldset(cardOpen.equipment_slots || [], options.equipment || []) : ""}
      ${editable ? `<div class="actions"><button type="submit" class="primary">Save card</button>` +
        (canRevert ? `<button type="button" data-card-revert>Revert to Records</button>` : "") + `</div>` : ""}
      </form>
    </div>
    <section class="card-abilities" id="card-abilities" aria-label="Abilities"></section>
    <section class="ability-editor" id="ability-editor" hidden></section>`;
  renderFrame(cardOpen.frame, "Set a name, type and threshold to see the frame.");
  if (cardOpen.frame) {
    paintCardFace(readCardFields(document.getElementById("card-edit")), cardOpen.frame, cardOpen.card);
  }
  renderCardLinks();
}

// The client's frame for the card (board3d.js BuildFrame), as sprite names.
function renderFrame(frame, emptyMessage = "") {
  const box = document.getElementById("card-frame");
  if (!box) return;
  if (!frame) {
    box.innerHTML = `<p class="muted small-text">${esc(emptyMessage)}</p>`;
    return;
  }
  if (frame.frame === null) {
    box.innerHTML = `<p class="small-text">Frame: none. Resource cards are frameless.</p>` +
      `<p class="small-text">Rarity: ${esc(frame.rarity_sprite)}</p>`;
    return;
  }
  const bars = frame.threshold_sprites.map((t) => `${esc(t.sprite)} ×${esc(t.count)}`).join(", ") || "none";
  box.innerHTML = `<details><summary class="small-text">Frame sprites (${esc(frame.frame)})</summary><dl class="frame-facts">
      <dt>Frame</dt><dd>${esc(frame.frame)} (${esc(frame.frame_sprites.join(", "))})</dd>
      <dt>Title bar</dt><dd>${esc(frame.title_bar)}</dd>
      <dt>Thresholds</dt><dd>${bars}</dd>
      <dt>Faction</dt><dd>${esc(frame.faction_sprite)}</dd>
      <dt>Rarity</dt><dd>${esc(frame.rarity_sprite)}</dd>
      <dt>Set badge</dt><dd>${esc(frame.set_sprite)}</dd>
      <dt>Layout</dt><dd>${esc(frame.layout)}${frame.sockets ? `, ${esc(frame.sockets)} socket(s)` : ""}</dd>
    </dl></details>`;
}

// The card face as the client paints it: CardFace.paintFace, the battle board's own
// painter, from the form's values, the card's text and art, and the frame.
const SHARD_COLOUR = { Blood: "#9b4dd1", Ruby: "#e0473a", Sapphire: "#3a8ae0", Wild: "#45a852", Diamond: "#e9dca6" };
let faceInputs = null;   // the last {fields, frame, card} painted, repainted when art or frames load

function faceFromFields(fields, frame, card) {
  const thresholds = {};
  for (const t of fields.threshold) thresholds[t.shard] = t.count;
  const shards = Object.keys(thresholds);
  const types = fields.card_type.split("|").filter(Boolean);
  const cost = fields.variable_cost ? `${fields.cost}X` : `${fields.cost}`;
  return {
    name: fields.name,
    cost: fields.card_type === "Resource" ? "" : cost,
    costCls: "",
    type: [fields.card_type, fields.subtype].filter(Boolean).join(" — "),
    types,
    subtype: fields.subtype || "",
    rarity: fields.rarity || "Common",
    faction: shards[0] || "Colorless",
    house: card.faction || "None",
    set: frame.set_number,
    pve: !!frame.pve,
    immortal: false,
    unique: !!card.unique,
    unlimited: false,
    resources: null,
    sockets: frame.sockets || 0,
    // The form's flavour while it is set; otherwise the card's own (overlay, Records or custom).
    flavor: (fields.flavor || "").trim() || card.flavor || "",
    thresholds,
    text: card.text || "",
    shard: shards.length > 1 ? "#c9a14a" : (SHARD_COLOUR[shards[0]] || "#8d939c"),
    glyph: "",
    art: card.art_url || null,
    keywords: [],
    counters: 0,
    showStats: types.includes("Troop") || types.includes("Champion"),
    attack: fields.attack,
    defense: fields.defense,
    atkCls: "",
    defCls: "",
  };
}

function paintCardFace(fields, frame, card) {
  faceInputs = { fields, frame, card };
  const img = document.getElementById("card-face-img");
  if (!img || !window.CardFace) return;
  const face = faceFromFields(fields, frame, card);
  img.src = CardFace.paintFace(face, () => paintCardFace(fields, frame, card), window.HexAssets).toDataURL("image/png");
}

// Reads the client folder (saved on an earlier visit, or picked) and opens the card frames.
async function chooseCardFolder() {
  if (!window.HexAssets) {
    els.status.textContent = "The card painter did not load";
    return;
  }
  try {
    let tree = await HexAssets.useSavedFolder();
    if (!tree) {
      const picked = await HexAssets.pickFolder(document.getElementById("cards-folder-input"));
      if (!picked) return;
      tree = picked.tree;
    }
    els.status.textContent = "Reading the client assets…";
    await HexAssets.openFolder(tree, (text) => { els.status.textContent = `${text}…`; });
    watchCardFrames();
    els.status.textContent = "Client card frames loaded";
  } catch (error) {
    els.status.textContent = error.message;
  }
}

let framePreviewTimer = null;
function scheduleFramePreview() {
  clearTimeout(framePreviewTimer);
  framePreviewTimer = setTimeout(() => {
    previewFrame().catch((error) => renderFrame(null, error.message));
  }, 250);
}

async function previewFrame() {
  const form = document.getElementById("card-edit");
  if (!form) return;
  const fields = readCardFields(form);
  fields.socket_count = cardOpen.card.socket_count || 0;
  // The text the client would print for the form as it stands (keywords, sockets, stage).
  const shown = await postJSON("/api/card-text", { guid: cardOpen.card.guid || "", fields });
  const card = { ...cardOpen.card, text: shown.text };
  const frame = await postJSON("/api/card-frame", { fields, faction: cardOpen.card.faction,
    pve: cardOpen.card.pve });
  renderFrame(frame);
  paintCardFace(fields, frame, card);
}

async function onCardsClick(event) {
  if (event.target.dataset.createPack) {
    createStorePack(event.target.dataset.createPack).catch(reportCardError);
    return;
  }
  const target = event.target.closest("button, [data-card-open]") || event.target;
  if (target.matches("[data-card-open]")) {
    await openCard(target.dataset.cardOpen);
  } else if (target.id === "cards-new") {
    openNewCard();
  } else if (target.id === "cards-folder") {
    await chooseCardFolder();
  } else if (target.id === "cards-incdata") {
    await buildIncdata();
  } else if (target.id === "cards-publish") {
    await publishIncdata();
  } else if (target.matches("[data-graph-zoom]")) {
    zoomGraph(target, Number(target.dataset.graphZoom));
  } else if (target.matches("[data-threshold-add]")) {
    const shards = cardOpen.options.shards;
    const used = [...document.querySelectorAll("[data-shard]")].map((s) => s.value);
    const free = shards.find((s) => !used.includes(s)) || shards[0];
    document.getElementById("card-threshold-rows").insertAdjacentHTML("beforeend",
      thresholdRow({ shard: free, count: 1 }, shards, ""));
    scheduleFramePreview();
  } else if (target.matches("[data-threshold-remove]")) {
    target.closest(".threshold-row").remove();
    scheduleFramePreview();
  } else if (target.matches("[data-int-attr-add]")) {
    const name = document.querySelector("[data-int-add]").value;
    const rows = document.getElementById("card-int-rows");
    const have = [...rows.querySelectorAll("[data-int-attr]")].find((row) => row.dataset.intAttr === name);
    if (have) {
      have.querySelector("[data-int-value]").focus();
    } else {
      rows.querySelector("[data-int-none]")?.remove();
      rows.insertAdjacentHTML("beforeend", intAttributeRow(name, 1, ""));
    }
  } else if (target.matches("[data-int-remove]")) {
    target.closest(".int-attr-row").remove();
  } else if (target.matches("[data-card-revert]")) {
    const saved = await postJSON("/api/edit/card-revert", { guid: cardOpen.card.guid });
    await loadCardList();
    cardOpen = saved;
    cardLinks = (saved.ability_links || []).map((link) => ({ ...link }));
    renderCardForm();
    els.status.textContent = `Reverted to Records: ${saved.card.name}`;
  } else if (target.id === "cards-assign-go") {
    await assignChecked();
  } else if (target.closest("#card-abilities, #ability-editor")) {
    await onAbilityClick(target);
  }
}

function readCardFields(form) {
  return {
    name: form.elements.name.value,
    set_guid: form.elements.set_guid.value,
    rarity: form.elements.rarity.value,
    card_type: form.elements.card_type.value,
    cost: form.elements.cost.value,
    attack: form.elements.attack.value,
    defense: form.elements.defense.value,
    subtype: form.elements.subtype.value,
    variable_cost: form.elements.variable_cost.checked,
    flavor: form.elements.flavor.value,
    sockets: form.elements.sockets ? form.elements.sockets.value.split(",").map((s) => s.trim()).filter(Boolean) : [],
    threshold: [...form.querySelectorAll("#card-threshold-rows .threshold-row")].map((row) => ({
      shard: row.querySelector("[data-shard]").value,
      count: Number(row.querySelector("[data-count]").value),
    })),
    ...(form.elements.impl_stage ? { impl_stage: form.elements.impl_stage.value } : {}),
    // Checked on save against the client's ECardAttributes and IntAttrs names. Sent
    // only when the form shows them, so a save never blanks what it did not show.
    ...(form.querySelector("[data-keyword]") ? {
      keywords: [...form.querySelectorAll("[data-keyword]:checked")].map((box) => box.dataset.keyword) } : {}),
    ...(form.querySelector(".card-int-attrs #card-int-rows") ? {
      int_attributes: Object.fromEntries([...form.querySelectorAll("[data-int-attr]")].map((row) =>
        [row.dataset.intAttr, row.querySelector("[data-int-value]").value.trim()])) } : {}),
  };
}

async function saveCard(form) {
  const fields = readCardFields(form);
  fields.abilities = cardLinks.map((link) => ({ guid: link.guid, overrides: link.overrides || [] }));
  const saved = await postJSON("/api/edit/card", { guid: cardOpen.card.guid, fields });
  await loadCardList();
  await openCard(saved.card.guid);
  els.status.textContent = `Card saved: ${saved.card.name}`;
}

// Downloads the selected set's .incdata (/api/card-incdata) and says what it holds.
// Publishes the set's .incdata for the server to send to clients at login.
async function publishIncdata() {
  const set = document.getElementById("cards-set").value;
  if (!set || set === "none") {
    els.status.textContent = "Choose a card set first";
    return;
  }
  const published = await postJSON("/api/edit/publish-incdata", { set_guid: set });
  const skipped = Object.entries(published.summary.skipped_not_done || {}).map(([stage, n]) => `${n} ${stage}`).join(", ");
  els.status.textContent = `Published ${published.file}: ${published.summary.cards} card(s)` +
    (skipped ? `; left out, not Done: ${skipped}` : "") + ". Clients get it at their next login.";
}

async function buildIncdata() {
  const set = document.getElementById("cards-set").value;
  if (!set || set === "none") {
    els.status.textContent = "Choose a card set first";
    return;
  }
  els.status.textContent = "Building .incdata…";
  const response = await fetch(`/api/card-incdata?set=${encodeURIComponent(set)}`);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error || `Build failed (${response.status})`);
  }
  const summary = JSON.parse(response.headers.get("X-Incdata-Summary") || "{}");
  const name = (/filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "") || [])[1] || "set.incdata";
  const link = document.createElement("a");
  link.href = URL.createObjectURL(await response.blob());
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 10000);
  const skipped = Object.entries(summary.skipped_not_done || {}).map(([stage, n]) => `${n} ${stage}`).join(", ");
  els.status.textContent = `${name}: ${summary.cards} card(s)` +
    (summary.records ? ` (${Object.entries(summary.records).map(([k, n]) => `${n} ${k}`).join(", ")})` : "") +
    (skipped ? `; left out, not Done: ${skipped}` : "") + ". Put it in the client's Data folder.";
}

async function assignChecked() {
  const guids = [...cardChecked];
  if (!guids.length) {
    els.status.textContent = "Check one or more cards first";
    return;
  }
  const setGuid = document.getElementById("cards-assign-set").value;
  const result = await postJSON("/api/edit/card-set", { guids, set_guid: setGuid });
  cardChecked.clear();
  await loadCardList();
  if (cardOpen && cardOpen.card.guid) await openCard(cardOpen.card.guid);
  els.status.textContent = `Assigned ${result.assigned} card(s) to ${result.set_name || "no set"}`;
}

// ---------------------------------------------------------------- abilities
// A card's root abilities (AbilityTemplate records), attached and detached here and
// saved with the card. One root at a time opens in the ability editor, with its
// effect entries (m_AbilityEffectList), its targets (m_AbilityTargetTemplateIds) and
// the child records they name. Saving the ability writes the root and every child
// opened or created in the editor to the ability overlay, after the server's checks.

const ZERO_GUID = "00000000-0000-0000-0000-000000000000";
const SECTION_LABEL = { AbilityTemplate: "ability", AbilityEffectTemplate: "effect",
  AbilityEffectConditionTemplate: "condition", AbilityTargetTemplate: "target" };
let abilityOptions = null;   // /api/ability-options
let abilityOpen = null;      // {root, children: {guid: payload}, touched: Set, problems: []}

const shortType = (type) => String(type || "").split(".").pop();
const refGuid = (value) => String((value && value.m_Guid) || "").toLowerCase();
const isSet = (guid) => Boolean(guid) && guid !== ZERO_GUID;
const clone = (value) => JSON.parse(JSON.stringify(value));

async function loadAbilityOptions() {
  if (!abilityOptions) abilityOptions = await getJSON("/api/ability-options");
  return abilityOptions;
}

function renderCardLinks() {
  const box = document.getElementById("card-abilities");
  if (!box) return;
  const editable = canEditCards();
  const rows = cardLinks.map((link, index) => `<li class="ability-link${abilityOpen && abilityOpen.root.guid === link.guid ? " open" : ""}">
      <span class="ability-link-name">${esc(link.name || "(no name)")}</span>
      <span class="card-source card-source-${esc(link.source)}">${esc(link.source)}</span>
      <code class="small-text">${esc(link.guid)}</code>
      ${link.overrides && link.overrides.length ? `<span class="muted small-text">${link.overrides.length} override(s)</span>` : ""}
      <span class="row-actions">
        <button type="button" data-link-edit="${index}">${editable ? "Edit" : "View"}</button>
        ${editable ? `<button type="button" data-link-move="${index}" data-step="-1" aria-label="Move up">↑</button>
        <button type="button" data-link-move="${index}" data-step="1" aria-label="Move down">↓</button>
        <button type="button" data-link-detach="${index}">Detach</button>` : ""}
      </span>
    </li>`).join("");
  box.innerHTML = `<h4>Abilities</h4>
    <ul class="ability-links">${rows || `<li class="muted">No ability linked.</li>`}</ul>
    ${editable ? `<div class="picker-row">
        ${pickerInput("link-attach", "AbilityTemplate", "Find an ability to attach (name or GUID)")}
        <button type="button" data-link-attach>Attach</button>
        <button type="button" data-ability-new>New ability</button>
      </div>
      <p class="muted small-text">Attach, detach and order are saved with the card (Save card).</p>` : ""}`;
}

// A search box over one ability section, filled from /api/abilities as the user types.
function pickerInput(id, section, placeholder, value = "") {
  return `<input id="${esc(id)}" class="picker" data-picker="${esc(section)}" list="${esc(id)}-list"
      placeholder="${esc(placeholder)}" value="${esc(value)}" autocomplete="off">
    <datalist id="${esc(id)}-list"></datalist>`;
}

let pickerTimer = null;
function schedulePickerSearch(input) {
  clearTimeout(pickerTimer);
  pickerTimer = setTimeout(async () => {
    const found = await getJSON(`/api/abilities?section=${encodeURIComponent(input.dataset.picker)}` +
      `&q=${encodeURIComponent(input.value.replace(/\s+[—-]\s+[0-9a-f-]{36}$/i, ""))}&limit=30`);
    const list = document.getElementById(input.getAttribute("list"));
    if (list) list.innerHTML = found.results.map((r) =>
      `<option value="${esc(`${r.name} — ${r.guid}`)}">${esc(r.type)} · ${esc(r.source)}</option>`).join("");
  }, 200);
}

function pickedGuid(id) {
  const input = document.getElementById(id);
  const found = input && input.value.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
  return found ? found[0].toLowerCase() : "";
}

async function onAbilityClick(target) {
  const data = target.dataset;
  if (data.linkEdit !== undefined) {
    await openAbility(cardLinks[Number(data.linkEdit)].guid);
  } else if (data.linkMove !== undefined) {
    moveItem(cardLinks, Number(data.linkMove), Number(data.step));
    renderCardLinks();
  } else if (data.linkDetach !== undefined) {
    const [removed] = cardLinks.splice(Number(data.linkDetach), 1);
    if (abilityOpen && abilityOpen.root.guid === removed.guid) closeAbility();
    renderCardLinks();
    els.status.textContent = `Detached ${removed.name || removed.guid}: save the card to keep it`;
  } else if (target.matches("[data-link-attach]")) {
    await attachAbility(pickedGuid("link-attach"));
  } else if (target.matches("[data-ability-new]")) {
    await newAbility();
  } else {
    await onAbilityEditorClick(target);
  }
}

function moveItem(list, index, step) {
  const to = index + step;
  if (to < 0 || to >= list.length) return false;
  [list[index], list[to]] = [list[to], list[index]];
  return true;
}

async function attachAbility(guid) {
  if (!guid) {
    els.status.textContent = "Pick an ability from the list first";
    return;
  }
  if (cardLinks.some((link) => link.guid === guid)) {
    els.status.textContent = "That ability is already linked";
    return;
  }
  const found = await getJSON(`/api/ability-record?section=AbilityTemplate&guid=${guid}`);
  cardLinks.push({ guid, name: found.record.m_Name || "", source: found.source, overrides: [] });
  renderCardLinks();
  els.status.textContent = `Attached ${found.record.m_Name}: save the card to keep it`;
}

// The ability editor (ability.html: the ability as pseudocode, with its sentences) is a
// column of the card layout, beside the card fields: the pane moves into .cards-layout and
// the layout gains a column for it (cards.html styles .with-ability).
function showAbilityPane(query) {
  const pane = document.getElementById("ability-pane");
  const frame = document.getElementById("ability-frame");
  if (!pane || !frame) return;
  const layout = document.querySelector("#tab-cards .cards-layout");
  if (layout && pane.parentElement !== layout) layout.append(pane);
  if (layout) layout.classList.add("with-ability");
  document.body.classList.add("ability-open");
  pane.hidden = false;
  frame.src = `/ability${query}`;
  const close = document.getElementById("ability-close");
  if (close && !close.dataset.wired) {
    close.dataset.wired = "1";
    close.addEventListener("click", hideAbilityPane);
  }
}

function hideAbilityPane() {
  const pane = document.getElementById("ability-pane");
  const frame = document.getElementById("ability-frame");
  if (pane) pane.hidden = true;
  if (frame) frame.src = "about:blank";
  const layout = document.querySelector("#tab-cards .cards-layout");
  if (layout) layout.classList.remove("with-ability");
  document.body.classList.remove("ability-open");
}

async function openAbility(guid) {
  showAbilityPane(`?guid=${encodeURIComponent(guid)}`);
}

async function newAbility() {
  showAbilityPane("?new=1");   // a blank ability, in the ability editor beside the card fields
}

async function newAbilityOld() {
  await Promise.all([loadAbilityOptions(), loadDslOptions()]);
  const root = await getJSON(`/api/ability-new?section=AbilityTemplate&type=${encodeURIComponent(abilityOptions.types.AbilityTemplate[0])}`);
  abilityOpen = { root, children: {}, touched: new Set(), problems: [],
    dsl: { text: "", supported: true, exact: false, reasons: [] }, filledFrom: "" };
  renderAbility();
  els.status.textContent = "New ability: fill it in and save it, then it is attached to the card";
}

function closeAbility() {
  abilityOpen = null;
  const box = document.getElementById("ability-editor");
  if (box) {
    box.hidden = true;
    box.innerHTML = "";
  }
  renderCardLinks();
}

// ---- the editor

// Root fields shown in their own sections rather than as plain fields.
const ROOT_SPECIAL = new Set(["m_AbilityTemplateId", "m_AbilityEffectList", "m_AbilityTargetTemplateIds"]);
const MAPPING_SPECIAL = new Set(["m_EffectTemplateId", "m_ConditionId", "m_TargetTemplateIndex"]);
const OWN_ID = new Set(["m_AbilityTemplateId", "m_TemplateId"]);

function renderAbility() {
  const box = document.getElementById("ability-editor");
  if (!box || !abilityOpen) return;
  const { root } = abilityOpen;
  const record = root.record;
  const editable = canEditCards();
  box.hidden = false;
  box.innerHTML = `<div class="ability-editor-head">
      <h4>${esc(record.m_Name || "Ability")} <span class="card-source card-source-${esc(root.source)}">${esc(root.source)}</span></h4>
      <code class="small-text">${esc(root.guid)}</code>
      <span class="row-actions">
        <button type="button" data-ability-check>Check</button>
        ${editable ? `<button type="button" class="primary" data-ability-save>Save ability</button>` : ""}
        ${editable && root.can_revert ? `<button type="button" data-ability-revert="${esc(root.guid)}" data-section="AbilityTemplate">Revert to Records</button>` : ""}
        <button type="button" data-ability-close>Close</button>
      </span>
    </div>
    <div class="ability-problems" id="ability-problems"></div>
    ${dslBlock()}
    <div id="ability-fields">${abilityFieldsHTML()}</div>`;
  showProblems();
  dslStart().catch(reportCardError);
}

// The root's fields, its targets and its effect entries (redrawn alone when the
// sentences fill them, so the text box keeps its place).
// What the ability does, in words: its trigger, each effect with its target, the ability a
// grant gives, and the variables with the effects that read them (read-only; see the fields below).
function summaryHTML(summary) {
  if (!summary) return "";
  const effects = summary.effects.map((effect) => {
    const granted = effect.granted
      ? `<div class="small-text">grants "${esc(effect.granted.text)}"${effect.granted.effects.length
        ? `: ${esc(effect.granted.effects.join("; "))}` : ""}</div>` : "";
    const when = effect.contingent >= 0 ? ", only if the step above was done" : "";
    return `<li><b>${esc(effect.text)}</b> <span class="muted small-text">(${esc(effect.duration)}${esc(when)})</span>
      <div class="small-text">target: ${esc(effect.target)}</div>${granted}</li>`;
  }).join("");
  const variables = summary.variables.length
    ? `<table class="summary-vars"><thead><tr><th>Variable</th><th>Default</th><th>Used by</th></tr></thead><tbody>${
      summary.variables.map((v) => `<tr><td>${esc(v.name)}</td><td>${esc(v.default)}</td><td>${
        v.used_by.map((i) => `effect ${i + 1}`).join(", ") || "-"}</td></tr>`).join("")}</tbody></table>`
    : `<p class="muted small-text">No variables.</p>`;
  return `<p><span class="field-name">Trigger</span> ${esc(summary.trigger)}</p>
    <ol class="summary-effects">${effects || `<li class="muted">No effects.</li>`}</ol>${variables}`;
}

// The ability as a graph (read-only): its records as boxes, the references between them as arrows.
// A record used more than once is drawn once, with an arrow from each user.
function graphHTML(graph) {
  if (!graph || !graph.nodes.length) return "";
  const tools = `<div class="graph-tools"><button type="button" data-graph-zoom="-0.2">−</button>
    <span class="small-text" data-graph-scale>100%</span>
    <button type="button" data-graph-zoom="0.2">+</button>
    <button type="button" data-graph-zoom="0">Reset</button></div>`;
  const depth = { [graph.root]: 0 };
  for (let pass = 0; pass < graph.nodes.length; pass++) {
    for (const edge of graph.edges) {
      if (depth[edge.from] === undefined) continue;
      const next = depth[edge.from] + 1;
      if (depth[edge.to] === undefined || depth[edge.to] < next) depth[edge.to] = next;
    }
  }
  const columns = {};
  for (const node of graph.nodes) {
    const d = depth[node.id] === undefined ? 0 : depth[node.id];
    (columns[d] ||= []).push(node);
  }
  const BOX_W = 260, BOX_H = 84, COL = 320, ROW = 112;
  const place = {};
  let rows = 0;
  for (const [d, list] of Object.entries(columns)) {
    list.forEach((node, i) => { place[node.id] = { x: Number(d) * COL + 10, y: i * ROW + 10 }; });
    rows = Math.max(rows, list.length);
  }
  const width = (Math.max(0, ...Object.keys(columns).map(Number)) + 1) * COL + 20;
  const height = rows * ROW + 20;
  const boxes = graph.nodes.map((node) => {
    const p = place[node.id];
    return `<div class="graph-node graph-${esc(node.kind)}" style="left:${p.x}px;top:${p.y}px" title="${esc(node.label)}">
      <span class="graph-kind">${esc(node.kind)}</span><span class="graph-label">${esc(node.label)}</span></div>`;
  }).join("");
  const lines = graph.edges.filter((e) => place[e.from] && place[e.to]).map((e) => {
    const a = place[e.from], b = place[e.to];
    const x1 = a.x + BOX_W, y1 = a.y + BOX_H / 2, x2 = b.x, y2 = b.y + BOX_H / 2;
    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
    return `<path d="M${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}" marker-end="url(#graph-arrow)"/>
      <text x="${mx}" y="${my - 3}">${esc(e.label)}</text>`;
  }).join("");
  return `${tools}<div class="graph-scroll"><div class="graph-stage" data-graph-stage data-w="${width}" data-h="${height}"
      style="width:${width}px;height:${height}px"><div class="graph" data-graph style="width:${width}px;height:${height}px">
    <svg width="${width}" height="${height}"><defs><marker id="graph-arrow" viewBox="0 0 10 10" refX="9" refY="5"
      markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10 z"/></marker></defs>${lines}</svg>${boxes}</div></div></div>`;
}

function abilityFieldsHTML() {
  const { root } = abilityOpen;
  const record = root.record;
  return `<details open class="ability-block"><summary>What it does</summary>${summaryHTML(root.summary)}</details>
    <details class="ability-block"><summary>Graph of the records (read-only)</summary>${graphHTML(root.graph)}</details>
    <details open class="ability-block"><summary>Ability fields <span class="muted small-text">${esc(shortType(record._t))}</span></summary>
      ${recordFields(root, ROOT_SPECIAL)}
    </details>
    <details open class="ability-block"><summary>Targets (m_AbilityTargetTemplateIds)</summary>
      ${targetRows(record)}
    </details>
    <details open class="ability-block"><summary>Effects in order (m_AbilityEffectList)</summary>
      ${effectRows(record)}
    </details>`;
}

// One record's plain fields, by the client class's own field types.
function recordFields(payload, skip = new Set(), prefix = "") {
  const disabled = canEditCards() ? "" : " disabled";
  const scope = `${payload.section}|${payload.guid}`;
  const rows = payload.schema.filter((f) => !skip.has(f.name) && !OWN_ID.has(f.name))
    .map((field) => fieldRow(scope, prefix + field.name, field, valueAt(payload.record, prefix + field.name), disabled));
  return `<div class="ability-fields" data-scope="${esc(scope)}">${rows.join("")}</div>`;
}

function valueAt(record, path) {
  return path.split(".").reduce((value, key) => (value == null ? undefined : value[key]), record);
}

function setValueAt(record, path, value) {
  const keys = path.split(".");
  const last = keys.pop();
  const holder = keys.reduce((value, key) => value[key], record);
  holder[last] = value;
}

function fieldRow(scope, path, field, value, disabled) {
  const name = path.split(".").pop();
  const attrs = `data-field="${esc(path)}" data-kind="${esc(field.kind)}"${disabled}`;
  let input;
  if (field.kind === "bool") {
    input = `<input type="checkbox" ${attrs}${value ? " checked" : ""}>`;
  } else if (field.kind === "int" || field.kind === "number") {
    input = `<input type="number" ${attrs} value="${esc(value ?? 0)}"${field.kind === "int" ? ` step="1"` : ""}>`;
  } else if (field.kind === "enum") {
    const values = field.values.includes(value) || value == null ? field.values : [value, ...field.values];
    input = `<select ${attrs}>${values.map((v) => `<option${v === value ? " selected" : ""}>${esc(v)}</option>`).join("")}</select>`;
  } else if (field.kind === "flags") {
    const chosen = new Set(String(value || "").split("|").map((v) => v.trim()));
    const empty = field.values.includes("None") ? "None" : "";
    input = `<span class="flag-set" data-empty="${esc(empty)}" ${attrs}>${field.values.map((v) =>
      `<label class="flag"><input type="checkbox" value="${esc(v)}"${chosen.has(v) ? " checked" : ""}${disabled}>${esc(v)}</label>`).join("")}</span>`;
  } else if (field.kind === "guid") {
    const guid = refGuid(value);
    input = `<input ${attrs} value="${esc(isSet(guid) ? guid : "")}" placeholder="none" class="guid-input">`;
  } else if (field.kind === "text" && /GameText|AIHints/.test(name)) {
    input = `<textarea ${attrs} rows="2">${esc(value ?? "")}</textarea>`;
  } else if (field.kind === "text" || field.kind === "type_name") {
    const list = name === "m_InternalType" ? ` list="trigger-events"` : "";
    input = `<input ${attrs} value="${esc(value ?? "")}"${list}>`;
  } else {
    // Nested objects, lists of objects and dictionaries: their JSON, checked by the server.
    const text = JSON.stringify(value ?? null, null, 1);
    input = `<textarea ${attrs} class="json-input" rows="${Math.min(12, Math.max(2, text.split("\n").length))}">${esc(text)}</textarea>`;
  }
  // A GUID field names the record it points at when the editor has it loaded.
  const named = field.kind === "guid" && abilityOpen && abilityOpen.children[refGuid(value)];
  const hint = named ? `${field.cs_type}: ${named.record.m_Name || shortType(named.record._t)}`
    : field.kind === "enum" || field.kind === "flags" ? field.enum : field.cs_type;
  return `<div class="ability-field">
      <label><span class="field-name" title="${esc(field.cs_type)}">${esc(name)}</span>
        <span class="muted small-text">${esc(hint)}</span></label>
      ${input}
      ${triggerEventList(name)}
      <span class="field-error" data-error="${esc(scope)}|${esc(path)}"></span>
    </div>`;
}

function triggerEventList(name) {
  if (name !== "m_TriggerEventType" || document.getElementById("trigger-events")) return "";
  return `<datalist id="trigger-events">${abilityOptions.trigger_events.map((t) => `<option value="${esc(t)}">`).join("")}</datalist>`;
}

// Reads a rendered record's fields back onto a copy of the record (other keys kept).
function readRecord(payload) {
  const scope = `${payload.section}|${payload.guid}`;
  const record = clone(payload.record);
  const problems = [];
  // The root's fields and each effect entry's fields are separate containers of one scope.
  const inputs = document.querySelectorAll(`.ability-fields[data-scope="${CSS.escape(scope)}"] [data-field]`);
  for (const input of inputs) {
    const path = input.dataset.field;
    try {
      setValueAt(record, path, readInput(input, valueAt(payload.record, path)));
    } catch (error) {
      problems.push({ section: payload.section, guid: payload.guid, path, text: error.message, level: "error" });
    }
  }
  return { record, problems };
}

function readInput(input, before) {
  const kind = input.dataset.kind;
  if (kind === "bool") return input.checked ? 1 : 0;
  if (kind === "int" || kind === "number") {
    const number = Number(input.value);
    if (input.value === "" || Number.isNaN(number)) throw new Error("must be a number");
    return number;
  }
  if (kind === "flags") {
    const chosen = [...input.querySelectorAll("input:checked")].map((c) => c.value);
    return chosen.length ? chosen.join("|") : input.dataset.empty;
  }
  if (kind === "guid") {
    const guid = input.value.trim().toLowerCase() || ZERO_GUID;
    return Object.assign({}, before && typeof before === "object" ? before : {}, { m_Guid: guid });
  }
  if (kind === "json" || kind === "guid_list") {
    try {
      return JSON.parse(input.value || "null");
    } catch (error) {
      throw new Error(`not valid JSON: ${error.message}`);
    }
  }
  return input.value;
}

// ---- targets

function targetRows(record) {
  const editable = canEditCards();
  const targets = record.m_AbilityTargetTemplateIds || [];
  const rows = targets.map((ref, index) => {
    const guid = refGuid(ref);
    const child = abilityOpen.children[guid];
    return `<li class="child-row" data-target-row="${index}">
        <div class="child-head">
          <strong>#${index}</strong>
          <span>${esc(child ? child.record.m_Name || "(no name)" : "(missing)")}</span>
          <span class="muted small-text">${esc(child ? shortType(child.record._t) : "")}</span>
          <code class="small-text">${esc(guid)}</code>
          <span class="row-actions">
            <button type="button" data-child-edit="AbilityTargetTemplate|${esc(guid)}">${editable ? "Edit" : "View"}</button>
            ${editable ? `<button type="button" data-target-move="${index}" data-step="-1" aria-label="Move up">↑</button>
            <button type="button" data-target-move="${index}" data-step="1" aria-label="Move down">↓</button>
            <button type="button" data-target-remove="${index}">Remove</button>` : ""}
          </span>
        </div>
        <span class="field-error" data-error="AbilityTemplate|${esc(abilityOpen.root.guid)}|m_AbilityTargetTemplateIds.${index}"></span>
        ${childEditor("AbilityTargetTemplate", guid)}
      </li>`;
  });
  return `<ol class="child-list" start="0">${rows.join("") || `<li class="muted">No targets.</li>`}</ol>
    ${editable ? `<div class="picker-row">
        ${pickerInput("target-add", "AbilityTargetTemplate", "Find a target template")}
        <button type="button" data-target-add>Add target</button>
        ${newTypeSelect("AbilityTargetTemplate")}
        <button type="button" data-child-new="AbilityTargetTemplate">New target</button>
      </div>` : ""}`;
}

function newTypeSelect(section) {
  const types = abilityOptions.types[section] || [];
  if (types.length < 2) return "";
  const engine = new Set(abilityOptions.engine_effects);
  return `<select data-new-type="${esc(section)}" aria-label="New ${esc(SECTION_LABEL[section])} type">${types.map((t) =>
    `<option value="${esc(t)}">${esc(shortType(t))}${section === "AbilityEffectTemplate" && !engine.has(t) ? " (no server executor)" : ""}</option>`).join("")}</select>`;
}

// ---- effect entries

function effectRows(record) {
  const editable = canEditCards();
  const disabled = editable ? "" : " disabled";
  const entries = record.m_AbilityEffectList || [];
  const targets = record.m_AbilityTargetTemplateIds || [];
  const scope = `AbilityTemplate|${abilityOpen.root.guid}`;
  const engine = new Set(abilityOptions.engine_effects);
  const rows = entries.map((entry, index) => {
    const effectGuid = refGuid(entry.m_EffectTemplateId);
    const conditionGuid = refGuid(entry.m_ConditionId);
    const effect = abilityOpen.children[effectGuid];
    const condition = abilityOpen.children[conditionGuid];
    const path = `m_AbilityEffectList.${index}`;
    const targetOptions = targets.map((ref, t) => {
      const child = abilityOpen.children[refGuid(ref)];
      return `<option value="${t}"${t === entry.m_TargetTemplateIndex ? " selected" : ""}>#${t} ${esc(child ? child.record.m_Name : refGuid(ref))}</option>`;
    }).join("") || `<option value="0">#0 (no targets)</option>`;
    const mappingFields = abilityOptions.mapping_schema.filter((f) => !MAPPING_SPECIAL.has(f.name))
      .map((field) => fieldRow(scope, `${path}.${field.name}`, field, entry[field.name], disabled)).join("");
    const supported = effect && engine.has(effect.record._t);
    return `<li class="child-row effect-row" data-effect-row="${index}">
        <div class="child-head">
          <strong>${index + 1}.</strong>
          <span>${esc(effect ? effect.record.m_Name || "(no name)" : isSet(effectGuid) ? "(missing effect)" : "(choose an effect)")}</span>
          <span class="muted small-text">${esc(effect ? shortType(effect.record._t) : "")}</span>
          ${effect && !supported ? `<span class="badge-warn" title="rules_port/coverage.py lists no executor for this type">no server executor</span>` : ""}
          <span class="row-actions">
            ${isSet(effectGuid) ? `<button type="button" data-child-edit="AbilityEffectTemplate|${esc(effectGuid)}">${editable ? "Edit effect" : "View effect"}</button>` : ""}
            ${editable ? `<button type="button" data-effect-move="${index}" data-step="-1" aria-label="Move up">↑</button>
            <button type="button" data-effect-move="${index}" data-step="1" aria-label="Move down">↓</button>
            <button type="button" data-effect-remove="${index}">Remove</button>` : ""}
          </span>
        </div>
        <div class="ability-fields" data-scope="${esc(scope)}">
          <div class="ability-field"><label><span class="field-name">m_EffectTemplateId</span><span class="muted small-text">AbilityEffectTemplate</span></label>
            <input data-field="${path}.m_EffectTemplateId" data-kind="guid" class="guid-input" value="${esc(isSet(effectGuid) ? effectGuid : "")}"${disabled}>
            <span class="field-error" data-error="${esc(scope)}|${path}.m_EffectTemplateId"></span></div>
          <div class="ability-field"><label><span class="field-name">m_TargetTemplateIndex</span><span class="muted small-text">target</span></label>
            <select data-field="${path}.m_TargetTemplateIndex" data-kind="int"${disabled}>${targetOptions}</select>
            <span class="field-error" data-error="${esc(scope)}|${path}.m_TargetTemplateIndex"></span></div>
          <div class="ability-field"><label><span class="field-name">m_ConditionId</span><span class="muted small-text">${esc(condition ? condition.record.m_Name : isSet(conditionGuid) ? "(missing condition)" : "none")}</span></label>
            <input data-field="${path}.m_ConditionId" data-kind="guid" class="guid-input" value="${esc(isSet(conditionGuid) ? conditionGuid : "")}" placeholder="none"${disabled}>
            ${isSet(conditionGuid) ? `<button type="button" data-child-edit="AbilityEffectConditionTemplate|${esc(conditionGuid)}">${editable ? "Edit condition" : "View condition"}</button>` : ""}
            <span class="field-error" data-error="${esc(scope)}|${path}.m_ConditionId"></span></div>
          ${mappingFields}
        </div>
        ${editable ? `<div class="picker-row">
            ${pickerInput(`effect-pick-${index}`, "AbilityEffectTemplate", "Use another effect")}
            <button type="button" data-effect-set="${index}">Use effect</button>
            ${pickerInput(`condition-pick-${index}`, "AbilityEffectConditionTemplate", "Find a condition")}
            <button type="button" data-condition-set="${index}">Use condition</button>
          </div>` : ""}
        ${isSet(effectGuid) ? childEditor("AbilityEffectTemplate", effectGuid) : ""}
        ${isSet(conditionGuid) ? childEditor("AbilityEffectConditionTemplate", conditionGuid) : ""}
      </li>`;
  });
  return `<ol class="child-list">${rows.join("") || `<li class="muted">No effects.</li>`}</ol>
    ${editable ? `<div class="picker-row">
        ${pickerInput("effect-add", "AbilityEffectTemplate", "Find an effect to add")}
        <button type="button" data-effect-add>Add effect</button>
        ${newTypeSelect("AbilityEffectTemplate")}
        <button type="button" data-child-new="AbilityEffectTemplate">New effect</button>
      </div>` : ""}`;
}

// A child record's own fields, shown once its Edit button was pressed.
function childEditor(section, guid) {
  const child = abilityOpen.children[guid];
  if (!child || !abilityOpen.touched.has(guid)) return "";
  const editable = canEditCards();
  return `<div class="child-editor">
      <p class="small-text"><strong>${esc(SECTION_LABEL[section])}</strong> ${esc(child.record.m_Name || "")}
        <span class="card-source card-source-${esc(child.source)}">${esc(child.source)}</span>
        <span class="muted">${esc(shortType(child.record._t))}</span>
        ${editable && child.can_revert ? `<button type="button" data-ability-revert="${esc(guid)}" data-section="${esc(section)}">Revert to Records</button>` : ""}
        <button type="button" data-child-close="${esc(guid)}">Hide</button></p>
      ${recordFields(child)}
    </div>`;
}

// ---- editor actions

// Copies every rendered field into the records, so a re-render keeps unsaved edits.
function syncFromForm() {
  const problems = [];
  const root = readRecord(abilityOpen.root);
  abilityOpen.root.record = root.record;
  problems.push(...root.problems);
  for (const guid of abilityOpen.touched) {
    const child = abilityOpen.children[guid];
    if (!child) continue;
    const read = readRecord(child);
    child.record = read.record;
    problems.push(...read.problems);
  }
  return problems;
}

async function ensureChild(section, guid) {
  if (!isSet(guid) || abilityOpen.children[guid]) return abilityOpen.children[guid];
  const found = await getJSON(`/api/ability-record?section=${section}&guid=${guid}`);
  abilityOpen.children[guid] = found;
  return found;
}

async function onAbilityEditorClick(target) {
  if (!abilityOpen) return;
  const data = target.dataset;
  const text = document.getElementById("dsl-text");
  if (text) abilityOpen.dsl.text = text.value;
  if (target.closest(".dsl-block") && await onDslClick(target)) return;
  const record = () => abilityOpen.root.record;
  const formProblems = syncFromForm();
  if (data.abilityClose !== undefined) {
    closeAbility();
    return;
  }
  if (data.abilityCheck !== undefined) {
    await checkAbility(formProblems);
    return;
  }
  if (data.abilitySave !== undefined) {
    await saveAbility(formProblems);
    return;
  }
  if (data.abilityRevert) {
    const reverted = await postJSON("/api/edit/ability-revert", { section: data.section, guid: data.abilityRevert });
    if (data.section === "AbilityTemplate") await openAbility(data.abilityRevert);
    else abilityOpen.children[data.abilityRevert] = reverted;
    els.status.textContent = `Reverted to Records: ${reverted.record.m_Name || reverted.guid}`;
  } else if (data.childEdit) {
    const [section, guid] = data.childEdit.split("|");
    await ensureChild(section, guid);
    abilityOpen.touched.add(guid);
  } else if (data.childClose) {
    abilityOpen.touched.delete(data.childClose);
  } else if (data.childNew) {
    await newChild(data.childNew);
  } else if (data.targetAdd !== undefined) {
    const guid = pickedGuid("target-add");
    if (!guid) throw new Error("Pick a target template from the list first");
    await ensureChild("AbilityTargetTemplate", guid);
    (record().m_AbilityTargetTemplateIds ||= []).push({ m_Guid: guid });
  } else if (data.targetMove !== undefined) {
    moveTarget(Number(data.targetMove), Number(data.step));
  } else if (data.targetRemove !== undefined) {
    removeTarget(Number(data.targetRemove));
  } else if (data.effectAdd !== undefined) {
    const guid = pickedGuid("effect-add");
    if (!guid) throw new Error("Pick an effect from the list first");
    await ensureChild("AbilityEffectTemplate", guid);
    addEntry(guid);
  } else if (data.effectSet !== undefined) {
    const guid = pickedGuid(`effect-pick-${data.effectSet}`);
    if (!guid) throw new Error("Pick an effect from the list first");
    await ensureChild("AbilityEffectTemplate", guid);
    record().m_AbilityEffectList[Number(data.effectSet)].m_EffectTemplateId = { m_Guid: guid };
  } else if (data.conditionSet !== undefined) {
    const guid = pickedGuid(`condition-pick-${data.conditionSet}`);
    if (!guid) throw new Error("Pick a condition from the list first");
    await ensureChild("AbilityEffectConditionTemplate", guid);
    record().m_AbilityEffectList[Number(data.conditionSet)].m_ConditionId = { m_Guid: guid };
  } else if (data.effectMove !== undefined) {
    moveItem(record().m_AbilityEffectList, Number(data.effectMove), Number(data.step));
  } else if (data.effectRemove !== undefined) {
    record().m_AbilityEffectList.splice(Number(data.effectRemove), 1);
  } else {
    return;
  }
  abilityOpen.problems = [];
  renderAbility();
}

// A new effect entry: the client's AbilityEffectTargetMapping defaults, the next
// free instance id, and the first target.
function addEntry(effectGuid) {
  const entries = (abilityOpen.root.record.m_AbilityEffectList ||= []);
  const entry = {};
  for (const field of abilityOptions.mapping_schema) entry[field.name] = clone(field.default);
  entry.m_EffectTemplateId = { m_Guid: effectGuid };
  entry.m_EffectInstanceId = entries.reduce((top, e) => Math.max(top, Number(e.m_EffectInstanceId) || 0), -1) + 1;
  entry.m_TargetTemplateIndex = 0;
  entries.push(entry);
}

async function newChild(section) {
  const select = document.querySelector(`[data-new-type="${section}"]`);
  const type = select ? select.value : abilityOptions.types[section][0];
  const created = await getJSON(`/api/ability-new?section=${section}&type=${encodeURIComponent(type)}`);
  abilityOpen.children[created.guid] = created;
  abilityOpen.touched.add(created.guid);
  if (section === "AbilityTargetTemplate") {
    (abilityOpen.root.record.m_AbilityTargetTemplateIds ||= []).push({ m_Guid: created.guid });
  } else {
    addEntry(created.guid);
  }
}

// Moving or removing a target renumbers the entries' m_TargetTemplateIndex to match.
function moveTarget(index, step) {
  const record = abilityOpen.root.record;
  if (!moveItem(record.m_AbilityTargetTemplateIds, index, step)) return;
  const other = index + step;
  for (const entry of record.m_AbilityEffectList || []) {
    if (entry.m_TargetTemplateIndex === index) entry.m_TargetTemplateIndex = other;
    else if (entry.m_TargetTemplateIndex === other) entry.m_TargetTemplateIndex = index;
  }
}

function removeTarget(index) {
  const record = abilityOpen.root.record;
  record.m_AbilityTargetTemplateIds.splice(index, 1);
  for (const entry of record.m_AbilityEffectList || []) {
    // An entry on the removed target keeps its index, so the check reports it.
    if (entry.m_TargetTemplateIndex > index) entry.m_TargetTemplateIndex -= 1;
  }
}

// The records a save sends: the root, and every child opened or created here.
function recordsToSave() {
  const records = [{ section: "AbilityTemplate", record: abilityOpen.root.record }];
  for (const guid of Object.keys(abilityOpen.children)) {
    const child = abilityOpen.children[guid];
    if (abilityOpen.touched.has(guid) || child.source === "unsaved") {
      records.push({ section: child.section, record: child.record });
    }
  }
  return records;
}

async function checkAbility(formProblems) {
  const result = formProblems.length ? { problems: formProblems }
    : await postJSON("/api/ability-check", { records: recordsToSave() });
  abilityOpen.problems = result.problems;
  showProblems();
  const errors = result.problems.filter((p) => p.level === "error").length;
  els.status.textContent = errors ? `${errors} problem(s) to fix before saving` :
    `No errors${result.problems.length ? ` (${result.problems.length} warning(s))` : ""}`;
}

async function saveAbility(formProblems) {
  if (formProblems.length) {
    abilityOpen.problems = formProblems;
    showProblems();
    els.status.textContent = "Fix the marked fields first";
    return;
  }
  let result;
  try {
    result = await postJSON("/api/edit/ability", { records: recordsToSave() });
  } catch (error) {
    abilityOpen.problems = (error.body && error.body.problems) || [];
    showProblems();
    throw error;
  }
  const guid = abilityOpen.root.guid;
  const isNew = !cardLinks.some((link) => link.guid === guid);
  await openAbility(guid);
  abilityOpen.problems = result.problems;
  showProblems();
  if (isNew) {
    cardLinks.push({ guid, name: abilityOpen.root.record.m_Name || "", source: abilityOpen.root.source, overrides: [] });
    renderCardLinks();
    els.status.textContent = `Ability saved and attached: save the card to keep the link`;
  } else {
    els.status.textContent = `Ability saved (${result.saved.length} record(s))`;
  }
}

// Puts each problem next to its field: the longest rendered path that prefixes it.
function showProblems() {
  const box = document.getElementById("ability-problems");
  if (!box || !abilityOpen) return;
  document.querySelectorAll("#ability-editor .field-error").forEach((span) => { span.textContent = ""; });
  const unplaced = [];
  for (const problem of abilityOpen.problems || []) {
    const scope = `${problem.section}|${problem.guid}`;
    const parts = String(problem.path || "").split(".");
    let span = null;
    for (let n = parts.length; n > 0 && !span; n -= 1) {
      span = document.querySelector(`#ability-editor [data-error="${CSS.escape(`${scope}|${parts.slice(0, n).join(".")}`)}"]`);
    }
    const text = `${problem.level === "warning" ? "Warning: " : ""}${problem.path && span ? `${problem.path}: ` : ""}${problem.text}`;
    if (span) {
      span.textContent += (span.textContent ? "; " : "") + text;
      span.classList.toggle("warning", problem.level === "warning" && !span.classList.contains("error"));
      if (problem.level === "error") span.classList.add("error");
    } else {
      unplaced.push(`<li class="${esc(problem.level)}">${esc(SECTION_LABEL[problem.section] || "")} ${esc(problem.name || problem.guid)}: ${esc(problem.path || "record")}: ${esc(problem.text)}</li>`);
    }
  }
  box.innerHTML = unplaced.length ? `<ul>${unplaced.join("")}</ul>` : "";
}

// ---- sentences (campaign_editor/ability_dsl.py)
//
// The open ability as plain sentences, one clause per line ("When this enters play,
// deal 2 damage to target opposing troop."). Preview compiles the text on the server
// without storing anything, and Save sentences stores the records it makes (the same
// checks and overlay as the fields below, which stay the full editor for what the
// sentences cannot say).
//
// The builder (the "chain") writes the text a step at a time: trigger, effect, target,
// the effect's slots, duration, condition, then "and then" for the next clause. Each
// step offers only what the server lists for it (/api/ability-dsl-builder, -targets and
// -names): phrases it has checked to read back, so the chain cannot offer a choice the
// compiler rejects. The chosen-so-far line shows the sentence with each part a button
// that reopens its step. A template starts the chain with blanks to fill. The textarea
// stays: what is typed there is read back into the chain (/api/ability-dsl-read), and
// each choice in the chain rewrites it.

let dslBuilder = null;   // /api/ability-dsl-builder
const dslTargets = {};   // shape id -> /api/ability-dsl-targets rows
// {settings, requires, trigger, clauses, at: {clause, step}, note, undo, stale, keywords}.
// A part left undefined is still to choose; "" is a choice too (no trigger, the usual
// duration, no condition).
let dslChain = null;
let dslPartial = null;   // the readable parts of text that does not parse
let dslStepChoices = [];  // the open step's choices [{value, label, hint, kind}]
let dslReadTimer = null, dslCheckTimer = null, dslNameTimer = null, dslSeq = 0;
// Step names, as the chain says them.
const DSL_STEPS = { trigger: "Trigger", shape: "Effect", target: "Target", amount: "Amount", card: "Card",
  counter: "Counter", ability: "Ability", conversation: "Conversation", deck: "Deck", keyword: "Keyword",
  shard: "Shard", attribute: "Card value", number: "Number", event: "Event", filter: "Random cards",
  duration: "Duration", condition: "Only if" };
const DSL_NAMED = new Set(["card", "counter", "ability", "conversation", "deck"]);
const DSL_LIST_MAX = 40;   // a longer list shows a search box and its first matches

async function loadDslOptions() {
  if (!dslBuilder) dslBuilder = await getJSON("/api/ability-dsl-builder");
  return dslBuilder;
}

async function dslLoadTargets(shape) {
  if (!dslTargets[shape]) dslTargets[shape] = (await getJSON(`/api/ability-dsl-targets?shape=${encodeURIComponent(shape)}`)).targets;
  return dslTargets[shape];
}

const dslShape = (id) => (dslBuilder ? dslBuilder.shapes.find((s) => s.id === id) : null);

function dslBlock() {
  const dsl = abilityOpen.dsl || { text: "", exact: false, reasons: [] };
  const editable = canEditCards();
  const note = abilityOpen.root.source === "unsaved" ? "Build the ability below, pick a template, or type the sentences, then save them."
    : dsl.exact ? "These sentences compile back to exactly this ability."
    : `The sentences cannot show this ability yet (${(dsl.reasons || []).slice(0, 3).join("; ")}). Edit it with the fields below.`;
  return `<details open class="ability-block dsl-block"><summary>Sentences</summary>
      <p class="muted small-text">${esc(note)}</p>
      ${editable ? `<div class="dsl-chain" id="dsl-chain"></div>` : ""}
      <textarea id="dsl-text" class="dsl-text" rows="4" spellcheck="false"${editable ? "" : " readonly"}
        aria-label="Sentences" placeholder="When this enters play, deal 2 damage to target opposing troop.">${esc(dsl.text || "")}</textarea>
      <div class="dsl-parse" id="dsl-parse" hidden></div>
      <span class="row-actions">
        <button type="button" data-dsl-preview>Preview records</button>
        ${editable ? `<button type="button" class="primary" data-dsl-save>Save sentences</button>` : ""}
      </span>
      <div class="dsl-result" id="dsl-result"></div>
    </details>`;
}

// Opens the chain on the ability's text (after renderAbility).
async function dslStart() {
  if (!document.getElementById("dsl-chain")) return;
  const text = abilityOpen.dsl.text || "";
  // A redraw of the same ability keeps the chain, unfinished clause and all.
  if (dslChain && dslChain.guid === abilityOpen.root.guid && dslChainText(dslChain) === text.trim()) {
    await dslRender();
    return;
  }
  dslChain = dslFresh();
  if (text.trim()) {
    const read = await postJSON("/api/ability-dsl-read", { text });
    if (read.ok) dslChain = dslFromParsed(read.parsed);
    else {
      dslShowParseError(read);
      dslChain.stale = true;
    }
  }
  dslChain.guid = abilityOpen.root.guid;
  await dslRender();
}

function dslFresh(settings = [], requires = "") {
  return { guid: abilityOpen ? abilityOpen.root.guid : "", settings, requires, trigger: undefined, clauses: [{}], at: { clause: 0, step: "trigger" },
    note: "", undo: null, stale: false, keywords: [] };
}

// The chain for parsed parts (read_text): every part read is chosen; a part the
// server could not read (null) is left to choose.
function dslFromParsed(parsed) {
  const chain = dslFresh(parsed.settings || [], parsed.requires || "");
  chain.trigger = parsed.trigger === null ? undefined : parsed.trigger;
  chain.clauses = parsed.clauses.map((read) => {
    const clause = { shape: read.shape, extra: { layer: read.layer || 0, recalculate: read.recalculate || "", new_target: Boolean(read.new_target) } };
    for (const step of ["target", "card", "counter", "ability", "conversation", "deck", "keyword", "shard", "attribute",
      "event", "filter", "duration", "condition"]) {
      if (read[step] !== null && read[step] !== undefined) clause[step] = read[step];
    }
    if (read.number !== undefined && read.number !== null) clause.number = Number(read.number);
    if (read.count) clause.count = read.count;
    if (read.amount_value !== undefined) clause.amount = read.amount_value;
    return clause;
  });
  if (!chain.clauses.length) chain.clauses = [{}];
  chain.at = dslNextStep(chain);
  return chain;
}

// The steps of clause i, in sentence order.
function dslSteps(chain, i) {
  const clause = chain.clauses[i];
  const steps = i === 0 ? ["trigger", "shape"] : ["shape"];
  const shape = dslShape(clause.shape);
  if (shape) steps.push("target", ...shape.slots, "duration", "condition");
  return steps;
}

function dslIsSet(chain, i, step) {
  const clause = chain.clauses[i];
  if (step === "trigger") return chain.trigger !== undefined;
  if (step === "amount" || step === "number") return Number.isInteger(clause[step]);
  if (step === "duration" || step === "condition") return clause[step] !== undefined;
  return Boolean(clause[step]);
}

// The first step still to choose, else "next" (add another clause).
function dslNextStep(chain) {
  for (let i = 0; i < chain.clauses.length; i++) {
    for (const step of dslSteps(chain, i)) if (!dslIsSet(chain, i, step)) return { clause: i, step };
  }
  return { clause: chain.clauses.length - 1, step: "next" };
}

// A clause can be written once every part but the duration and condition is chosen.
function dslComplete(chain, i) {
  return dslSteps(chain, i).every((step) => step === "trigger" || step === "duration" || step === "condition" || dslIsSet(chain, i, step));
}

// The amount as the sentence writes it (ability_dsl._amount_text).
function dslAmountText(shape, n) {
  if (shape.signed) return n >= 0 ? `+${n}` : String(n);
  return shape.article && n === 1 ? "a" : String(n);
}

// A clause as segments [{text, step, blank}]: literal words reopen the effect step,
// each part its own (ability_dsl._render_clause).
function dslSegments(clause) {
  const shape = dslShape(clause.shape);
  if (!shape) return [{ text: "[effect]", step: "shape", blank: true }];
  const you = clause.target === "you" && shape.you;
  const out = you ? [{ text: "(you)", step: "target", muted: true }] : [];
  const amount = Number.isInteger(clause.amount) ? dslAmountText(shape, clause.amount) : "";
  for (const piece of (you ? shape.you : shape.phrase).split(/(\{\w+\})/)) {
    const slot = (piece.match(/^\{(\w+)\}$/) || [])[1];
    if (!slot) {
      if (piece) out.push({ text: piece, step: "shape" });
    } else if (slot === "s") {
      if (!["a", "an", "1", "+1", "one"].includes(amount)) out.push({ text: "s", step: "shape" });
    } else {
      const value = slot === "amount" ? amount : clause[slot];
      out.push(value === undefined || value === "" ? { text: `[${DSL_STEPS[slot].toLowerCase()}]`, step: slot, blank: true }
        : { text: String(value), step: slot });
    }
  }
  if (clause.count) out.push({ text: " for each " }, { text: clause.count, step: "amount" });
  if (clause.duration) out.push({ text: " " }, { text: clause.duration, step: "duration" });
  if (clause.condition) out.push({ text: " if " }, { text: clause.condition, step: "condition" });
  return out;
}

// Whether the trigger is a replacement ("if this would die"): its last clause ends
// "instead" (ability_dsl.is_replacement; the builder marks each trigger).
function dslIsReplacement(trigger) {
  return Boolean(trigger && dslBuilder) && Boolean((dslBuilder.triggers.find((t) => t.phrase === trigger) || {}).replacement);
}

// The clause's text: the segments without the "(you)" marker, "instead" when it is a
// replacement's last clause, then the bracketed notes (ability_dsl._render_clause).
function dslClauseText(clause, instead = false) {
  let text = dslSegments(clause).filter((s) => !s.muted).map((s) => s.text).join("");
  if (instead) text += " instead";
  const extra = clause.extra || {};
  if (extra.layer) text += ` (layer ${extra.layer})`;
  if (extra.recalculate) text += ` (${extra.recalculate})`;
  if (extra.new_target) text += " (new target)";
  return text;
}

const dslSentence = (text) => text.charAt(0).toUpperCase() + text.slice(1) + (text.endsWith(".") ? "" : ".");

// The text the chain says (ability_dsl.render): settings, requires, then the leading
// run of complete clauses.
function dslChainText(chain) {
  const lines = [];
  if (chain.settings.length) lines.push(`Settings: ${chain.settings.join(", ")}`);
  if (chain.requires) lines.push(`Requires: ${chain.requires}.`);
  let written = 0;
  while (written < chain.clauses.length && dslComplete(chain, written)) written++;
  const instead = dslIsReplacement(chain.trigger);
  for (let i = 0; i < written; i++) {
    const text = dslClauseText(chain.clauses[i], instead && i === written - 1);
    lines.push(dslSentence(i ? `then ${text}` : chain.trigger ? `${chain.trigger}, ${text}` : text));
  }
  return lines.join("\n");
}

// ---- the chain's view

async function dslRender() {
  const box = document.getElementById("dsl-chain");
  if (!box || !dslChain) return;
  const b = dslBuilder;
  const templates = `<div class="dsl-templates"><span class="field-name">Start from a template</span>
      ${b.templates.map((t, i) => `<button type="button" data-dsl-template="${i}" title="${esc(t.text)}">${esc(t.label)}</button>`).join("")}</div>`;
  if (dslChain.stale) {
    box.innerHTML = `${templates}<p class="dsl-note">The text below does not read as sentences, so the chain is paused. Fix the text, or open its readable part in the chain.</p>`;
    return;
  }
  const at = dslChain.at;
  const line = dslChain.clauses.map((clause, i) => {
    const chip = (seg, j) => seg.step
      ? `<button type="button" class="dsl-part${seg.blank ? " blank" : ""}${seg.muted ? " muted" : ""}${at.clause === i && at.step === seg.step ? " active" : ""}" data-dsl-step="${i}|${seg.step}" title="Change the ${esc(DSL_STEPS[seg.step].toLowerCase())}">${esc(seg.text)}</button>`
      : `<span>${esc(seg.text)}</span>`;
    let head = "";
    if (i === 0) {
      const trigger = dslChain.trigger === undefined ? { text: "[trigger]", blank: true }
        : dslChain.trigger === "" ? { text: "when played or used", muted: true } : { text: dslChain.trigger };
      head = chip({ ...trigger, step: "trigger" }) + "<span>, </span>";
    } else {
      head = "<span>then </span>";
    }
    // A replacement's last clause says "instead" (written, not chosen).
    const instead = i === dslChain.clauses.length - 1 && dslIsReplacement(dslChain.trigger)
      ? `<span class="muted" title="The trigger is a replacement: this happens instead of the event"> instead</span>` : "";
    const parts = dslSegments(clause).map(chip).join("") + instead;
    const remove = dslChain.clauses.length > 1 || clause.shape
      ? ` <button type="button" class="link dsl-remove" data-dsl-remove="${i}" title="Remove this clause">remove</button>` : "";
    return `<div class="dsl-clause">${head}${parts}<span>.</span>${remove}</div>`;
  }).join("");
  box.innerHTML = `${templates}
    <div class="dsl-line" aria-label="Chosen so far">${line}</div>
    ${dslChain.note ? `<p class="dsl-note">${esc(dslChain.note)}${dslChain.undo ? ` <button type="button" class="link" data-dsl-undo>Undo</button>` : ""}</p>` : ""}
    <div class="dsl-step" id="dsl-step">${await dslStepHTML()}</div>
    <p class="dsl-check small-text" id="dsl-check"></p>`;
  dslCheckSoon();
}

// The open step: its title and its choices (a search box when there are many).
async function dslStepHTML() {
  const { clause: i, step } = dslChain.at;
  const clause = dslChain.clauses[i];
  const shape = dslShape(clause.shape);
  const b = dslBuilder;
  const count = (n) => (n ? `${n} use${n === 1 ? "" : "s"} in Records` : "");
  let title = DSL_STEPS[step] || "";
  let extra = "";
  dslStepChoices = [];
  if (step === "next") {
    return `<p class="dsl-step-title">The sentence is complete. Preview or save it, or add another clause.</p>
      <div class="dsl-choices"><button type="button" data-dsl-then>and then...</button></div>`;
  }
  if (step === "trigger") {
    title = "When does it happen?";
    // Replacement triggers ("if this would die, ... instead") follow the others.
    const ordered = [...b.triggers.filter((t) => !t.replacement), ...b.triggers.filter((t) => t.replacement)];
    dslStepChoices = [{ value: "", label: "None: when played or used" },
      ...ordered.map((t) => ({ value: t.phrase, label: t.phrase,
        hint: [t.replacement ? "replaces the event: ... instead" : "", count(t.uses)].filter(Boolean).join("; ") }))];
  } else if (step === "shape") {
    title = "What does it do?";
    dslStepChoices = b.shapes.map((s) => ({ value: s.id, label: s.label, hint: s.phrase.replace(/\{s\}/g, "s") }));
  } else if (step === "target") {
    title = `Who or what: ${shape.label.toLowerCase()}`;
    dslStepChoices = (await dslLoadTargets(shape.id)).map((t) => ({ value: t.phrase, label: t.phrase,
      hint: t.uses ? `${t.uses} with this effect in Records` : count(t.all_uses) }));
  } else if (step === "amount") {
    title = shape.signed ? "How much: +N or -N" : "How many";
    extra = `<span class="dsl-number"><input type="number" step="1"${shape.signed ? "" : ' min="0"'} data-dsl-number
        value="${Number.isInteger(clause.amount) && !clause.count ? clause.amount : ""}" placeholder="${shape.signed ? "+2" : "2"}" aria-label="Amount">
        <button type="button" data-dsl-use>Use</button></span>
      <p class="muted small-text">or an amount that counts: ${shape.signed ? "+1" : "1"} for each...</p>`;
    dslStepChoices = b.counts.map((c) => ({ value: c.phrase, label: `for each ${c.phrase}`, hint: count(c.uses), kind: "count" }));
  } else if (step === "number") {
    title = "To what number";
    extra = `<span class="dsl-number"><input type="number" step="1" data-dsl-number value="${Number.isInteger(clause.number) ? clause.number : ""}" aria-label="Number">
      <button type="button" data-dsl-use>Use</button></span>`;
  } else if (DSL_NAMED.has(step)) {
    title = `Which ${DSL_STEPS[step].toLowerCase()}`;
    dslStepChoices = await dslNames(step, "");
  } else if (step === "keyword") {
    title = "Which keywords (one or more)";
    dslChain.keywords = clause.keyword ? clause.keyword.split(" and ") : [];
    // Labelled by the client's display name (ToolName); the sentence writes the member name.
    const labels = b.keyword_labels || {};
    dslStepChoices = b.keywords.map((k) => ({ value: k, label: labels[k] && labels[k] !== k ? `${labels[k]} (${k})` : k, kind: "toggle" }));
    extra = `<p class="small-text">Chosen: <span id="dsl-keywords">${esc(dslChain.keywords.join(" and ") || "none yet")}</span>
      <button type="button" data-dsl-keywords>Use</button></p>`;
  } else if (["shard", "attribute", "event", "filter"].includes(step)) {
    title = `Which ${DSL_STEPS[step].toLowerCase()}`;
    // A shape that takes only some values lists its own (an on/off card value: Lethal).
    const values = (shape.values && shape.values[step]) || b[{ shard: "shards", attribute: "attributes", event: "events", filter: "filters" }[step]];
    dslStepChoices = values.map((v) => ({ value: v, label: v }));
  } else if (step === "duration") {
    title = "For how long";
    dslStepChoices = [{ value: "", label: `usual: ${shape.usual_duration}`, hint: "not written" },
      ...shape.durations.map((d) => ({ value: d, label: d }))];
  } else if (step === "condition") {
    title = "Only if (optional)";
    dslStepChoices = [{ value: "", label: "always" },
      ...b.conditions.map((c) => ({ value: c.phrase, label: `if ${c.phrase}`, hint: count(c.uses) }))];
  }
  const search = dslStepChoices.length > DSL_LIST_MAX || DSL_NAMED.has(step)
    ? `<input type="search" class="dsl-search" data-dsl-search placeholder="Find ${esc(title.toLowerCase())}" aria-label="Find" autocomplete="off">` : "";
  const none = step !== "number" && !dslStepChoices.length && !DSL_NAMED.has(step)
    ? `<p class="dsl-note">Nothing can follow here: no ${esc(DSL_STEPS[step].toLowerCase())} reads back with the choices before it. Change an earlier part.</p>` : "";
  return `<p class="dsl-step-title">${esc(title)}</p>${extra}${search}${none}
    <div class="dsl-choices" id="dsl-choices">${dslChoiceButtons("")}</div>`;
}

// The choices that match the search, at most DSL_LIST_MAX (names are searched on the server).
function dslChoiceButtons(query) {
  const words = query.trim().toLowerCase();
  const found = dslStepChoices.filter((c) => !words || c.label.toLowerCase().includes(words));
  const shown = dslChain.at.step === "shape" ? found : found.slice(0, DSL_LIST_MAX);   // every effect fits
  const current = dslCurrentValue();
  const buttons = shown.map((c) => {
    const on = c.kind === "toggle" ? dslChain.keywords.includes(c.value) : c.value === current;
    const attr = c.kind === "toggle" ? "data-dsl-toggle" : c.kind === "count" ? "data-dsl-count" : "data-dsl-pick";
    return `<button type="button" class="dsl-choice${on ? " on" : ""}" ${attr}="${esc(c.value)}">${esc(c.label)}${
      c.hint ? `<span class="muted small-text"> ${esc(c.hint)}</span>` : ""}</button>`;
  }).join("");
  const more = found.length > shown.length ? `<p class="muted small-text">${found.length - shown.length} more: type to narrow.</p>`
    : !found.length && words ? `<p class="muted small-text">No match.</p>` : "";
  return buttons + more;
}

function dslCurrentValue() {
  const { clause: i, step } = dslChain.at;
  if (step === "trigger") return dslChain.trigger;
  const clause = dslChain.clauses[i] || {};
  return step === "amount" ? clause.count : clause[step];
}

async function dslNames(kind, query) {
  const found = await getJSON(`/api/ability-dsl-names?kind=${encodeURIComponent(kind)}&q=${encodeURIComponent(query)}`);
  return found.names.map((n) => ({ value: n.value, label: n.value, hint: n.uses ? `${n.uses} use${n.uses === 1 ? "" : "s"} in Records` : "" }));
}

function dslSearchInput(input) {
  const step = dslChain && dslChain.at.step;
  const list = document.getElementById("dsl-choices");
  if (!list) return;
  if (!DSL_NAMED.has(step)) {
    list.innerHTML = dslChoiceButtons(input.value);
    return;
  }
  clearTimeout(dslNameTimer);
  dslNameTimer = setTimeout(async () => {
    dslStepChoices = await dslNames(step, input.value);
    if (dslChain.at.step === step) list.innerHTML = dslChoiceButtons("");
  }, 200);
}

// ---- choosing

// After a choice: the next step to choose, the text rewritten, the chain redrawn.
async function dslAfterChoice() {
  dslChain.at = dslNextStep(dslChain);
  dslWriteText();
  await dslRender();
}

function dslWriteText() {
  const box = document.getElementById("dsl-text");
  if (!box) return;
  box.value = abilityOpen.dsl.text = dslChainText(dslChain);
  dslHideParseError();
}

async function dslChoose(value) {
  const { clause: i, step } = dslChain.at;
  const clause = dslChain.clauses[i];
  dslChain.note = "";
  dslChain.undo = null;
  if (step === "trigger") dslChain.trigger = value;
  else if (step === "shape") await dslSetShape(i, value);
  else clause[step] = value;
  await dslAfterChoice();
}

// A new effect keeps the parts it still has, clears the rest, and says which.
async function dslSetShape(i, id) {
  const clause = dslChain.clauses[i];
  const old = dslShape(clause.shape), shape = dslShape(id);
  clause.shape = id;
  if (!old || old.id === id) return;
  const cleared = [];
  for (const slot of old.slots) {
    if (!shape.slots.includes(slot) && dslIsSet(dslChain, i, slot)) {
      cleared.push(DSL_STEPS[slot].toLowerCase());
      delete clause[slot];
      if (slot === "amount") delete clause.count;
    }
  }
  if (Number.isInteger(clause.amount) && clause.amount < 0 && !shape.signed) {
    cleared.push("amount (negative)");
    delete clause.amount;
  }
  if (clause.target && !(await dslLoadTargets(id)).some((t) => t.phrase === clause.target)) {
    cleared.push(`target (${clause.target})`);
    delete clause.target;
  }
  if (clause.duration && !shape.durations.includes(clause.duration)) {
    cleared.push(`duration (${clause.duration}${clause.duration === shape.usual_duration ? ", now the usual one" : ""})`);
    clause.duration = clause.duration === shape.usual_duration ? "" : undefined;
  }
  if (cleared.length) dslChain.note = `Changed the effect to "${shape.label}": cleared the ${cleared.join(", ")}, which no longer apply.`;
}

function dslUseNumber() {
  const input = document.querySelector("[data-dsl-number]");
  const value = Number(String(input ? input.value : "").replace(/^\+/, ""));
  if (!input || input.value.trim() === "" || !Number.isInteger(value)) throw new Error("Give a whole number");
  const { clause: i, step } = dslChain.at;
  const shape = dslShape(dslChain.clauses[i].shape);
  if (step === "amount" && value < 0 && !shape.signed) throw new Error(`${shape.label} takes no negative amount`);
  dslChain.clauses[i][step] = value;
  if (step === "amount") delete dslChain.clauses[i].count;
}

async function dslUseTemplate(index) {
  const template = dslBuilder.templates[index];
  const before = dslChain;
  dslChain = dslFresh(before.settings, before.requires);
  dslChain.trigger = template.parts.trigger || "";
  dslChain.clauses = template.parts.clauses.map((parts) => {
    if (!parts.shape) return {};
    const clause = { shape: parts.shape, duration: parts.duration || "", condition: "" };
    for (const [part, value] of Object.entries(parts)) if (value !== null && part !== "duration") clause[part] = value;
    return clause;
  });
  const replaced = before.clauses.filter((c) => c.shape).length;
  dslChain.note = `Started from "${template.label}": fill the marked blanks.` +
    (replaced ? ` This replaced ${replaced} clause${replaced === 1 ? "" : "s"}.` : "");
  dslChain.undo = replaced ? before : null;
  await dslAfterChoice();
}

// ---- the textarea

function dslTextInput() {
  clearTimeout(dslReadTimer);
  dslReadTimer = setTimeout(() => dslReadText().catch(reportCardError), 400);
}

// Reads the typed text into the chain, or shows why it cannot.
async function dslReadText() {
  const box = document.getElementById("dsl-text");
  if (!box || !document.getElementById("dsl-chain")) return;
  const seq = ++dslSeq;
  const read = await postJSON("/api/ability-dsl-read", { text: box.value });
  if (seq !== dslSeq) return;
  if (read.ok) {
    dslHideParseError();
    dslChain = box.value.trim() ? dslFromParsed(read.parsed) : dslFresh();
  } else {
    dslShowParseError(read);
    // Typing goes on: the readable part opens in the chain, so its next step still has
    // suggestions (the blank parts are chosen there). Text the chain cannot read stays stale.
    if (read.partial) {
      dslChain = dslFromParsed(read.partial);
    } else {
      dslChain.stale = true;
    }
  }
  await dslRender();
}

function dslShowParseError(read) {
  const box = document.getElementById("dsl-parse");
  if (!box) return;
  dslPartial = read.partial;
  box.hidden = false;
  box.innerHTML = `<span class="error">${esc(read.error)}</span>
    ${document.getElementById("dsl-chain") ? `<button type="button" data-dsl-open-partial>Open in the chain</button>` : ""}`;
}

function dslHideParseError() {
  const box = document.getElementById("dsl-parse");
  if (box) box.hidden = true;
  dslPartial = null;
  if (dslChain) dslChain.stale = false;
}

// Compiles what the chain says on the server (read-only), after a pause.
function dslCheckSoon() {
  clearTimeout(dslCheckTimer);
  dslCheckTimer = setTimeout(async () => {
    const line = document.getElementById("dsl-check");
    const text = dslChainText(dslChain);
    if (!line) return;
    if (!dslChain.clauses.some((_, i) => dslComplete(dslChain, i))) {
      line.textContent = "";
      return;
    }
    const base = abilityOpen.root.source === "unsaved" ? "" : abilityOpen.root.guid;
    try {
      const result = await postJSON("/api/ability-dsl-compile", { text, guid: base });
      const errors = result.problems.filter((p) => p.level === "error");
      line.className = `dsl-check small-text${errors.length ? " error" : ""}`;
      line.textContent = errors.length ? `Compiles, but the checks report: ${errors[0].text}` : "Compiles on the server.";
      if (!errors.length && await dslFillFields(result, text)) {
        line.textContent += " The fields below now hold these records (not saved yet).";
      }
    } catch (error) {
      line.className = "dsl-check small-text error";
      line.textContent = error.message;
    }
  }, 300);
}

// Fills the field editor with the records the sentences compile to: the root (on the
// open ability's own GUID), each new effect, condition or target as an unsaved child,
// and the Records ones they reuse. Save ability or Save sentences then stores them.
// Text the fields already show (as opened, or filled last) is left alone, so a field
// edited by hand is only replaced when the sentences change.
const dslSchemas = {};   // client type -> field schema (/api/ability-new)

async function dslFillFields(result, text) {
  if (!abilityOpen || abilityOpen.filledFrom === text) return false;
  // An existing ability the sentences cannot show exactly keeps its fields: Save
  // sentences refuses it, and filling would let Save ability store a simpler one.
  if (abilityOpen.root.source !== "unsaved" && !abilityOpen.dsl.exact) return false;
  const compiled = clone(result.records[0].record);
  if (abilityOpen.root.source === "unsaved") {
    // A new ability keeps the GUID the card will link (the compile made its own).
    compiled.m_AbilityTemplateId = { m_Guid: abilityOpen.root.guid };
  }
  const children = {};
  for (const { section, record } of result.records.slice(1)) {
    if (!dslSchemas[record._t]) {
      dslSchemas[record._t] = (await getJSON(`/api/ability-new?section=${section}&type=${encodeURIComponent(record._t)}`)).schema;
    }
    const guid = refGuid(record.m_TemplateId);
    children[guid] = { section, guid, record, source: "unsaved", schema: dslSchemas[record._t], can_revert: false };
  }
  // Children made by an earlier fill that these sentences no longer use are dropped,
  // so a save does not store them.
  for (const [guid, child] of Object.entries(abilityOpen.children)) {
    if (child.source === "unsaved" && !children[guid]) {
      delete abilityOpen.children[guid];
      abilityOpen.touched.delete(guid);
    }
  }
  Object.assign(abilityOpen.children, children);
  for (const entry of compiled.m_AbilityEffectList || []) {
    await ensureChild("AbilityEffectTemplate", refGuid(entry.m_EffectTemplateId));
    await ensureChild("AbilityEffectConditionTemplate", refGuid(entry.m_ConditionId));
  }
  for (const ref of compiled.m_AbilityTargetTemplateIds || []) await ensureChild("AbilityTargetTemplate", refGuid(ref));
  if (!abilityOpen || !dslChain || dslChainText(dslChain) !== text) {
    return false;   // the text moved on while the records loaded
  }
  abilityOpen.root.record = compiled;
  abilityOpen.filledFrom = text;
  const box = document.getElementById("ability-fields");
  if (box) box.innerHTML = abilityFieldsHTML();
  return true;
}

async function onDslClick(target) {
  const data = target.dataset;
  const box = document.getElementById("dsl-text");
  const base = abilityOpen.root.source === "unsaved" ? "" : abilityOpen.root.guid;
  if (data.dslPick !== undefined) {
    await dslChoose(data.dslPick);
    return true;
  }
  if (data.dslCount !== undefined) {
    Object.assign(dslChain.clauses[dslChain.at.clause], { count: data.dslCount, amount: 1 });
    await dslAfterChoice();
    return true;
  }
  if (data.dslUse !== undefined) {
    dslUseNumber();
    await dslAfterChoice();
    return true;
  }
  if (data.dslToggle !== undefined) {
    const list = dslChain.keywords;
    if (list.includes(data.dslToggle)) list.splice(list.indexOf(data.dslToggle), 1);
    else list.push(data.dslToggle);
    target.classList.toggle("on", list.includes(data.dslToggle));
    document.getElementById("dsl-keywords").textContent = list.join(" and ") || "none yet";
    return true;
  }
  if (data.dslKeywords !== undefined) {
    if (!dslChain.keywords.length) throw new Error("Choose a keyword");
    dslChain.clauses[dslChain.at.clause].keyword = dslChain.keywords.join(" and ");
    await dslAfterChoice();
    return true;
  }
  if (data.dslStep !== undefined) {
    const [clause, step] = data.dslStep.split("|");
    dslChain.at = { clause: Number(clause), step };
    await dslRender();
    document.querySelector("#dsl-step .dsl-search, #dsl-step [data-dsl-number]")?.focus();
    return true;
  }
  if (data.dslThen !== undefined) {
    dslChain.clauses.push({});
    dslChain.note = "";
    await dslAfterChoice();
    return true;
  }
  if (data.dslRemove !== undefined) {
    dslChain.clauses.splice(Number(data.dslRemove), 1);
    if (!dslChain.clauses.length) dslChain.clauses.push({});
    dslChain.note = "Removed a clause.";
    await dslAfterChoice();
    return true;
  }
  if (data.dslTemplate !== undefined) {
    await dslUseTemplate(Number(data.dslTemplate));
    return true;
  }
  if (data.dslUndo !== undefined && dslChain.undo) {
    dslChain = Object.assign(dslChain.undo, { note: "Undone.", undo: null });
    await dslAfterChoice();
    return true;
  }
  if (data.dslOpenPartial !== undefined && dslPartial) {
    dslChain = dslFromParsed(dslPartial);
    dslChain.note = "Opened the part of the text that reads; choose what is missing. A choice here rewrites the text.";
    await dslRender();
    return true;
  }
  if (data.dslPreview !== undefined) {
    const result = await postJSON("/api/ability-dsl-compile", { text: box.value, guid: base });
    showDslResult(result);
    return true;
  }
  if (data.dslSave !== undefined) {
    let saved;
    try {
      saved = await postJSON("/api/edit/ability-dsl", { text: box.value, guid: base });
    } catch (error) {
      abilityOpen.problems = (error.body && error.body.problems) || [];
      showProblems();
      throw error;
    }
    const isNew = !cardLinks.some((link) => link.guid === saved.guid);
    await openAbility(saved.guid);
    abilityOpen.problems = saved.problems;
    showProblems();
    if (isNew) {
      cardLinks.push({ guid: saved.guid, name: abilityOpen.root.record.m_Name || "", source: abilityOpen.root.source, overrides: [] });
      renderCardLinks();
    }
    els.status.textContent = `Sentences saved: ${saved.saved.length} record(s), ${saved.new.length} new${isNew ? "; save the card to keep the link" : ""}`;
    return true;
  }
  return false;
}

// The records a preview would store: the root, new records and the Records ones reused.
function showDslResult(result) {
  const box = document.getElementById("dsl-result");
  if (!box) return;
  const fresh = new Set(result.new.map((entry) => entry.guid));
  const root = result.records[0].record;
  const reused = [...(root.m_AbilityEffectList || []).map((e) => ["effect", e.m_EffectTemplateId.m_Guid]),
    ...(root.m_AbilityEffectList || []).map((e) => ["condition", e.m_ConditionId.m_Guid]),
    ...(root.m_AbilityTargetTemplateIds || []).map((t) => ["target", t.m_Guid])]
    .filter(([, guid]) => !/^0{8}-/.test(guid) && !fresh.has(guid));
  const errors = result.problems.filter((p) => p.level === "error");
  box.innerHTML = `<p class="small-text">Reads as: <em>${esc(result.text).replace(/\n/g, " ")}</em></p>
    <ul class="small-text">
      <li>ability ${esc(root.m_Name)} (${(root.m_AbilityEffectList || []).length} effect(s), ${(root.m_Variables || []).length} variable(s))</li>
      ${result.records.slice(1).map((r) => `<li>new ${esc(SECTION_LABEL[r.section])} <code>${esc(r.record.m_TemplateId.m_Guid)}</code> ${esc(r.record.m_Name || "")}</li>`).join("")}
      ${[...new Set(reused.map((r) => r.join(" ")))].map((r) => `<li class="muted">reuses Records ${esc(r.split(" ")[0])} <code>${esc(r.split(" ")[1])}</code></li>`).join("")}
    </ul>
    ${errors.length ? `<ul>${errors.map((p) => `<li class="error">${esc(p.path || "record")}: ${esc(p.text)}</li>`).join("")}</ul>` : `<p class="small-text">No errors.</p>`}`;
}
