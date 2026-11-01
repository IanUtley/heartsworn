// Private browser storage for the Hex client data: the saved folder handle and
// what has been extracted from it (art, card sounds, battleboards). It lives in
// this origin's IndexedDB, so it stays on this browser only. Every call is
// best effort: when storage is unavailable the page reads the folder again.

const DB_NAME = "hex-client";
const STORE = "kv";
let opened = null;

function database() {
  if (!opened) {
    opened = new Promise((resolve, reject) => {
      if (!window.indexedDB) { reject(new Error("no IndexedDB")); return; }
      const request = indexedDB.open(DB_NAME, 1);
      request.onupgradeneeded = () => request.result.createObjectStore(STORE);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }
  return opened;
}

async function run(mode, action) {
  const db = await database();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(STORE, mode);
    const request = action(transaction.objectStore(STORE));
    transaction.oncomplete = () => resolve(request ? request.result ?? null : null);
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

export async function cacheGet(key) {
  try {
    return await run("readonly", (store) => store.get(key));
  } catch (err) {
    return null;
  }
}

export async function cachePut(key, value) {
  try {
    await run("readwrite", (store) => store.put(value, key));
    return true;
  } catch (err) {
    return false;
  }
}
