// The Hex engine in a Web Worker: Pyodide (CPython in WebAssembly) running
// demo/bridge.py. The page talks to it through fetch-shim.js.
//
// Nothing is shipped but the code. The page sends the player's own
// ``Data/gamedata`` (read from their Hex folder); the worker builds the match
// database from it, reporting each step, and caches the finished database in
// IndexedDB. The cache key is the gamedata plus the engine build, so a later
// visit with the same folder opens the cached database at once.
import { loadPyodide } from "../pyodide/pyodide.mjs";

const SITE = new URL("../", import.meta.url);
const CACHE_DB = "hex-demo";
const CACHE_STORE = "databases";

let py = null;
let built = false;
let markRuntime;
const runtime = new Promise((resolve) => { markRuntime = resolve; });
let markReady;
const ready = new Promise((resolve) => { markReady = resolve; });
let engineZip = null;

async function boot() {
  py = await loadPyodide({ indexURL: new URL("pyodide/", SITE).href });
  const res = await fetch(new URL("hex-py.zip", SITE));
  if (!res.ok) throw new Error(`hex-py.zip: ${res.status}`);
  engineZip = new Uint8Array(await res.arrayBuffer());
  py.FS.mkdirTree("/app");
  py.FS.writeFile("/app/hex-py.zip", engineZip);
  py.FS.mkdirTree("/data");
  py.runPython(`
import sys, zipfile
zipfile.ZipFile("/app/hex-py.zip").extractall("/app")
sys.path[:0] = ["/app"]
import demo.bridge as bridge
`);
  markRuntime();
}

function progress(text) {
  postMessage({ progress: text });
}

async function digest(bytes) {
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function openCache() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(CACHE_DB, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(CACHE_STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

// IndexedDB failures (private windows, blocked storage) only cost the cache.
async function cacheGet(key) {
  try {
    const cache = await openCache();
    return await new Promise((resolve, reject) => {
      const request = cache.transaction(CACHE_STORE).objectStore(CACHE_STORE).get(key);
      request.onsuccess = () => resolve(request.result || null);
      request.onerror = () => reject(request.error);
    });
  } catch (err) {
    return null;
  }
}

async function cachePut(key, bytes) {
  try {
    const cache = await openCache();
    await new Promise((resolve, reject) => {
      const tx = cache.transaction(CACHE_STORE, "readwrite");
      tx.objectStore(CACHE_STORE).put(bytes, key);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } catch (err) {
    progress("The database could not be cached; it will be rebuilt next visit.");
  }
}

// A cached database for this gamedata and engine build, or a fresh build cached
// for next time.
async function prepareDatabase(gamedata) {
  progress("Checking the cache...");
  // "v2" versions the key: databases saved before the encounter-deck check are ignored.
  const key = `v2-${await digest(gamedata)}-${await digest(engineZip)}`;
  py.FS.writeFile("/data/gamedata", gamedata);
  const cached = await cacheGet(key);
  if (cached) {
    progress("Opening the saved database...");
    py.FS.writeFile("/data/hex.db", cached);
    if (py.globals.get("bridge").open_database() > 0) return;
    progress("The saved database is incomplete; building it again...");
  }
  py.globals.get("bridge").build_database(progress);
  progress("Saving the database for next time...");
  await cachePut(key, py.FS.readFile("/data/hex.db"));
}

self.onmessage = async (event) => {
  const data = event.data;
  if (data.type === "gamedata") {
    await runtime;
    if (built) return;
    try {
      await prepareDatabase(data.bytes);
      built = true;
      markReady();
      postMessage({ ready: true });
    } catch (err) {
      postMessage({ ready: false, error: String(err) });
    }
    return;
  }
  const { id, method, path, query, body } = data;
  try {
    await ready;
    const route = py.globals.get("bridge").route;
    const result = route(method, path, query, body);
    const [status, text] = result.toJs();
    result.destroy();
    postMessage({ id, status, text });
  } catch (err) {
    postMessage({ id, status: 500, text: JSON.stringify({ error: String(err) }) });
  }
};

boot().catch((err) => postMessage({ ready: false, error: String(err) }));
