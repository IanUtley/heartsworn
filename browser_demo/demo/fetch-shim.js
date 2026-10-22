// Routes the page's /api/ calls to the Hex engine in the worker, so the web
// client runs unchanged. Every other request goes to the network as usual.
//
// The engine's database is built from the player's own Hex folder: when the
// page opens that folder (HexAssets.openFolder), its ``Data/gamedata`` file is
// read and sent to the worker. The first folder opened builds the database.
(() => {
  // Relative to this script, so the demo works under a sub-path (GitHub Pages).
  const worker = new Worker(new URL("worker.js", document.currentScript.src), { type: "module" });
  const pending = new Map();
  let nextId = 1;
  let markReady;
  let failBoot;
  const ready = new Promise((resolve, reject) => { markReady = resolve; failBoot = reject; });
  let sentGamedata = false;

  // A status line over the page while the database is built, so the wait reads
  // as progress rather than a freeze. It is removed once the engine is ready.
  function showStatus(text) {
    if (!document.body) {
      document.addEventListener("DOMContentLoaded", () => showStatus(text), { once: true });
      return;
    }
    let el = document.getElementById("demo-status");
    if (!el) {
      el = document.createElement("div");
      el.id = "demo-status";
      el.style.cssText = "position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:999;" +
        "background:#1b1a2b;color:#eee;padding:8px 14px;border-radius:8px;font:14px sans-serif;" +
        "box-shadow:0 2px 8px rgba(0,0,0,.5)";
      document.body.appendChild(el);
    }
    el.textContent = text;
  }
  showStatus("Starting the Hex engine...");

  worker.onmessage = (event) => {
    const msg = event.data;
    if ("progress" in msg) return showStatus(msg.progress);
    if ("ready" in msg) {
      if (!msg.ready) return failBoot(new Error(msg.error));
      markReady();
      const el = document.getElementById("demo-status");
      if (el) el.remove();
      return;
    }
    const finish = pending.get(msg.id);
    pending.delete(msg.id);
    if (finish) finish(msg);
  };

  // Reads the gamedata from a Hex folder tree (see unity/install.js folderTree).
  async function sendGamedata(tree) {
    if (sentGamedata) return;
    sentGamedata = true;
    const file = await tree.file("Data/gamedata");
    const bytes = await file.read(0, file.size);
    worker.postMessage({ type: "gamedata", bytes }, [bytes.buffer]);
  }

  // The page's HexAssets module loads after this script, so wait for it, then
  // wrap openFolder: the folder the player picks supplies the gamedata.
  const hook = setInterval(() => {
    if (!window.HexAssets || !HexAssets.openFolder) return;
    clearInterval(hook);
    const openFolder = HexAssets.openFolder;
    HexAssets.openFolder = async (tree, progress) => {
      const opened = await openFolder(tree, progress);
      showStatus("Reading the Hex gamedata...");
      sendGamedata(tree).catch((err) => failBoot(err));
      return opened;
    };
  }, 50);

  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (!url.pathname.startsWith("/api/")) return realFetch(input, init);
    try {
      await ready;
    } catch (err) {
      showStatus(`The engine could not start: ${err.message}`);
      throw err;
    }
    const id = nextId++;
    const reply = await new Promise((resolve) => {
      pending.set(id, resolve);
      worker.postMessage({
        id,
        method: (init.method || "GET").toUpperCase(),
        path: url.pathname,
        query: url.search.slice(1),
        body: typeof init.body === "string" ? init.body : "",
      });
    });
    return new Response(reply.text, { status: reply.status, headers: { "Content-Type": "application/json" } });
  };
})();
