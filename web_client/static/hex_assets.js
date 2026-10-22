// Browser side of the Hex client install. The player points the page at their
// HEX SHARDS OF FATE folder once; card art, card-play sounds and battleboards
// are decoded from that folder in this page and kept in private browser storage
// (cache.js), so later visits use the saved folder and the stored data. Nothing
// is uploaded. app.js and board3d.js use the functions on window.HexAssets.

import { HexInstall, folderTree, fileListTree } from "./unity/install.js";
import { cacheGet, cachePut } from "./cache.js";

const ART = /^a\d{7}$/;
const FOLDER_KEY = "folder";
const state = {
  install: null,
  art: new Map(),        // art number -> object URL, once ready
  artAsked: new Set(),
  clips: new Map(),      // "art|clip" -> Promise<AudioBuffer | null>
  context: null,
  gain: null,            // the output gain every clip passes through
  volume: 0.5,           // sounds play at half volume unless the player changes it
};

// Opens a folder tree (from pickFolder or useSavedFolder) and reads its index.
async function openFolder(tree, onProgress) {
  const install = await HexInstall.open(tree);
  await install.indexArt(onProgress);
  state.install = install;
  return install;
}

// Asks for the folder with the native picker when the browser has one, and
// otherwise with a folder-upload input (read locally, never sent anywhere).
// Returns { tree, handle }, or null when the player cancelled. The handle is
// saved so the next visit can reuse the folder without choosing it again.
async function pickFolder(fallbackInput) {
  if (window.showDirectoryPicker) {
    try {
      const handle = await window.showDirectoryPicker({ mode: "read" });
      await cachePut(FOLDER_KEY, handle);
      return { tree: folderTree(handle), handle };
    } catch (err) {
      if (err.name === "AbortError") return null;
      throw err;
    }
  }
  return new Promise((resolve) => {
    fallbackInput.onchange = () => {
      const files = [...fallbackInput.files];
      fallbackInput.value = "";
      resolve(files.length ? { tree: fileListTree(files), handle: null } : null);
    };
    fallbackInput.click();
  });
}

// True when a folder from an earlier visit is saved in this browser.
async function hasSavedFolder() {
  return !!(await cacheGet(FOLDER_KEY));
}

// Reuses the saved folder. Browsers ask again for read access on each visit,
// so this must run from a click. Returns a tree, or null when access is refused.
async function useSavedFolder() {
  const handle = await cacheGet(FOLDER_KEY);
  if (!handle || !handle.requestPermission) return null;
  return (await handle.requestPermission({ mode: "read" })) === "granted" ? folderTree(handle) : null;
}

// Battleboard keys the folder has, from storage when this folder was read before.
async function boardNames() {
  const stored = await cacheGet("boards7");
  if (stored) return stored;
  if (!state.install) return [];
  const names = await state.install.boardNames();
  await cachePut("boards7", names);
  return names;
}

// Encodes RGBA pixels as a PNG blob, for storage.
async function pngOf(pixels) {
  const canvas = document.createElement("canvas");
  canvas.width = pixels.width;
  canvas.height = pixels.height;
  canvas.getContext("2d").putImageData(new ImageData(pixels.rgba, pixels.width, pixels.height), 0, 0);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
}

// The card art as an object URL, from storage or decoded from the folder.
// Returns null until it is ready; onReady then runs so the caller can redraw.
function artUrl(art, onReady) {
  if (!ART.test(art || "")) return null;
  if (state.art.has(art)) return state.art.get(art);
  if (!state.artAsked.has(art)) {
    state.artAsked.add(art);
    loadArt(art).then((url) => {
      if (!url) return;
      state.art.set(art, url);
      if (onReady) onReady();
    }).catch(() => {});
  }
  return null;
}

async function loadArt(art) {
  let blob = await cacheGet(`art:${art}`);
  if (!blob && state.install) {
    blob = await pngOf(await state.install.artPixels(art));
    if (blob) await cachePut(`art:${art}`, blob);
  }
  return blob ? URL.createObjectURL(blob) : null;
}

// The client's card face atlases ({ Frames, Elements, Icons }, each an image
// and its sprite rects), from storage or the folder. Returns null until they
// are ready; onReady then runs so the caller can repaint its cards.
const CHROME_KEY = "chrome1";
// Resolves once the card frames are loaded, so the lobby can wait for them and the
// first battle is drawn in the client's frames. Gives up after a while, since a
// folder without frames would otherwise hold the lobby forever.
function preloadCardChrome(timeoutMs = 60000) {
  if (state.chrome) return Promise.resolve();
  const loaded = new Promise((resolve) => cardChrome(resolve));
  const timeout = new Promise((resolve) => setTimeout(resolve, timeoutMs));
  return Promise.race([loaded, timeout]);
}

function cardChrome(onReady) {
  if (state.chrome) return state.chrome;
  if (onReady) (state.chromeWaiting ||= []).push(onReady);
  if (!state.chromeLoad) {
    state.chromeLoad = loadChrome().then((chrome) => {
      if (!chrome) { state.chromeLoad = null; return; }   // try again once a folder is opened
      state.chrome = chrome;
      const waiting = state.chromeWaiting || [];
      state.chromeWaiting = [];
      waiting.forEach((f) => f());
    }).catch(() => { state.chromeLoad = null; });
  }
  return null;
}

async function loadChrome() {
  let packed = await cacheGet(CHROME_KEY);
  if (!packed && state.install) {
    const decoded = await state.install.cardChrome();
    if (!decoded) return null;
    packed = {};
    for (const [name, atlas] of Object.entries(decoded)) {
      packed[name] = { png: await pngOf(atlas), sprites: atlas.sprites };
    }
    cachePut(CHROME_KEY, packed);
  }
  if (!packed) return null;
  const chrome = {};
  for (const [name, atlas] of Object.entries(packed)) {
    chrome[name] = { image: await createImageBitmap(atlas.png), sprites: atlas.sprites };
  }
  return chrome;
}

// Every clip passes one gain node, so the player's volume setting applies to all of them.
function audioContext() {
  if (!state.context) {
    state.context = new (window.AudioContext || window.webkitAudioContext)();
    state.gain = state.context.createGain();
    state.gain.gain.value = state.volume;
    state.gain.connect(state.context.destination);
  }
  return state.context;
}

// Sets the sound volume, 0 to 1 (the lobby slider is 0 to 100 percent).
function setVolume(level) {
  state.volume = Math.min(1, Math.max(0, Number(level) || 0));
  if (state.gain) state.gain.gain.value = state.volume;
}

// Plays a card-play clip from the card's own art bundle. The clip name comes
// from the gamedata (m_SoundEventOnSpawn). Errors are silent: a missing sound
// should not interrupt a battle.
async function playClip(art, clip) {
  if (!ART.test(art || "") || !clip) return;
  const key = `${art}|${clip}`;
  if (!state.clips.has(key)) {
    state.clips.set(key, (async () => {
      let bytes = await cacheGet(`clip:${key}`);
      if (!bytes && state.install) {
        bytes = await state.install.clipBytes(art, clip);
        if (bytes) cachePut(`clip:${key}`, bytes);
      }
      if (!bytes) return null;
      return audioContext().decodeAudioData(bytes.slice().buffer);
    })().catch(() => null));
  }
  const buffer = await state.clips.get(key);
  if (!buffer) return;
  const context = audioContext();
  if (context.state === "suspended") context.resume();
  const source = context.createBufferSource();
  source.buffer = buffer;
  source.connect(state.gain);
  source.start();
}

// A battleboard, from storage when it was saved, otherwise read from the folder
// and then saved. Textures are stored as PNGs and come back as bitmaps.
async function gameboard(key) {
  const stored = await cacheGet(`board9:${key}`);
  if (stored) return unpackBoard(stored);
  if (!state.install) return null;
  const board = await state.install.gameboard(key);
  // Saving is best effort: a failure to store must not hide the board.
  if (board) packBoard(board).then((packed) => cachePut(`board9:${key}`, packed)).catch(() => {});
  return board;
}

async function packBoard(board) {
  const memo = new Map();
  const pack = async (pixels) => {
    if (!pixels) return null;
    if (!memo.has(pixels)) memo.set(pixels, pngOf(pixels).then((png) => ({ width: pixels.width, height: pixels.height, png })));
    return memo.get(pixels);
  };
  const parts = [];
  for (const part of board.parts) parts.push({ ...part, texture: await pack(part.texture) });
  const emitters = [];
  for (const emitter of board.emitters) emitters.push({ ...emitter, texture: await pack(emitter.texture) });
  return { key: board.key, name: board.name, surface: board.surface, parts, emitters };
}

async function unpackBoard(packed) {
  const memo = new Map();
  const unpack = async (texture) => {
    if (!texture) return null;
    if (!memo.has(texture)) {
      memo.set(texture, createImageBitmap(texture.png).then((bitmap) => ({ width: texture.width, height: texture.height, bitmap })));
    }
    return memo.get(texture);
  };
  const parts = [];
  for (const part of packed.parts) parts.push({ ...part, texture: await unpack(part.texture) });
  const emitters = [];
  for (const emitter of packed.emitters) emitters.push({ ...emitter, texture: await unpack(emitter.texture) });
  return { key: packed.key, name: packed.name, surface: packed.surface, parts, emitters };
}

window.HexAssets = {
  get ready() { return !!state.install; },
  pickFolder,
  hasSavedFolder,
  useSavedFolder,
  openFolder,
  boardNames,
  artUrl,
  cardChrome,
  preloadCardChrome,
  playClip,
  setVolume,
  gameboard,
};
