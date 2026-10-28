// The IR editor: the IR as code (ability_ir_text.py). Names and completions come from
// /api/ability-ir-code-names; the code is loaded, checked and compiled by the /api/ability-ir-code routes.
// Text from the server is written with textContent, never as HTML.
(function () {
  const text = document.getElementById("text");
  const guid = document.getElementById("guid");
  const menu = document.getElementById("menu");
  const statusEl = document.getElementById("status");
  const layersEl = document.getElementById("layers");
  const compiled = document.getElementById("compiled");
  let names = [];
  let items = [];
  let active = 0;
  let timer = null;

  function setStatus(message, kind = "") {
    statusEl.textContent = message;
    statusEl.className = kind;
  }

  async function getJson(url, body) {
    const options = body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    };
    const response = await fetch(url, { credentials: "same-origin", ...options });
    const data = await response.json();
    return { ok: response.ok, data };
  }

  // The name the caret is in: letters, digits, underscores and dots before the caret.
  function currentWord() {
    const before = text.value.slice(0, text.selectionStart);
    const match = /[A-Za-z_][A-Za-z0-9_.]*$/.exec(before);
    return match ? { word: match[0], start: text.selectionStart - match[0].length } : null;
  }

  function hideMenu() {
    menu.style.display = "none";
    items = [];
  }

  function showMenu() {
    menu.replaceChildren();
    items.forEach((item, index) => {
      const row = document.createElement("div");
      row.setAttribute("role", "option");
      row.className = index === active ? "active" : "";
      const label = document.createElement("span");
      label.textContent = item.name;
      const type = document.createElement("span");
      type.className = "type";
      type.textContent = item.layer;
      row.append(label, type);
      row.addEventListener("mousedown", (event) => {
        event.preventDefault();
        active = index;
        insert();
      });
      menu.append(row);
    });
    menu.style.display = items.length ? "block" : "none";
  }

  function insert() {
    const found = currentWord();
    const chosen = items[active];
    if (!found || !chosen) return hideMenu();
    const before = text.value.slice(0, found.start);
    const after = text.value.slice(text.selectionStart);
    const call = `${chosen.name}()`;
    text.value = before + call + after;
    const caret = before.length + call.length - 1;   // inside the parentheses
    text.setSelectionRange(caret, caret);
    hideMenu();
    scheduleCheck();
  }

  // Names that start with the typed word come first, then names that contain it.
  function complete() {
    const found = currentWord();
    if (!found || found.word.length < 2) return hideMenu();
    const needle = found.word.toLowerCase();
    const starts = names.filter((item) => item.name.toLowerCase().startsWith(needle));
    const contains = names.filter((item) => !item.name.toLowerCase().startsWith(needle)
                                             && item.name.toLowerCase().includes(needle));
    items = starts.concat(contains).slice(0, 40);
    active = 0;
    showMenu();
  }

  function scheduleComplete() {
    clearTimeout(timer);
    timer = setTimeout(complete, 100);
  }

  let checkTimer = null;
  function scheduleCheck() {
    clearTimeout(checkTimer);
    checkTimer = setTimeout(check, 600);
  }

  async function check() {
    if (!text.value.trim()) {
      compiled.textContent = "Empty.";
      return setStatus("Empty.");
    }
    const { ok, data } = await getJson("/api/ability-surface-parse", { text: text.value });
    if (ok) {
      compiled.textContent = JSON.stringify(data.records, null, 2);
      setStatus("Compiles to a Records ability.", "ok");
    } else {
      compiled.textContent = data.error || "Not valid code.";
      setStatus(data.error || "Not valid code.", "bad");
    }
  }

  async function load() {
    const id = guid.value.trim();
    if (!id) return setStatus("Enter an ability GUID.", "bad");
    const { ok, data } = await getJson(`/api/ability-surface-code?guid=${encodeURIComponent(id)}`);
    if (!ok) return setStatus(data.error || "Could not load the ability.", "bad");
    text.value = data.text;
    setStatus(`Loaded ${id}.`, "ok");
    check();
  }

  text.addEventListener("input", () => { scheduleComplete(); scheduleCheck(); });
  text.addEventListener("click", hideMenu);
  text.addEventListener("keydown", (event) => {
    if (menu.style.display !== "block") return;
    if (event.key === "ArrowDown") { active = (active + 1) % items.length; showMenu(); }
    else if (event.key === "ArrowUp") { active = (active - 1 + items.length) % items.length; showMenu(); }
    else if (event.key === "Tab" || event.key === "Enter") { event.preventDefault(); insert(); }
    else if (event.key === "Escape") { hideMenu(); }
    else return;
    event.preventDefault();
  });
  guid.addEventListener("keydown", (event) => { if (event.key === "Enter") load(); });
  document.getElementById("load").addEventListener("click", load);
  document.getElementById("check").addEventListener("click", check);

  // The side panel: every name, grouped by layer, with its fields and an insert button.
  function renderNames() {
    const byLayer = {};
    names.forEach((item) => { (byLayer[item.layer] ||= []).push(item); });
    Object.keys(byLayer).sort().forEach((layer) => {
      const heading = document.createElement("h2");
      heading.textContent = layer;
      layersEl.append(heading);
      byLayer[layer].forEach((item) => {
        const row = document.createElement("div");
        row.className = "name";
        const label = document.createElement("span");
        label.textContent = item.name;
        label.title = item.class || item.meaning || "";
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = "insert";
        button.addEventListener("click", () => {
          const start = text.selectionStart;
          const call = `${item.name}()`;
          text.value = text.value.slice(0, start) + call + text.value.slice(text.selectionEnd);
          text.setSelectionRange(start + call.length - 1, start + call.length - 1);
          text.focus();
          scheduleCheck();
        });
        row.append(label, button);
        layersEl.append(row);
        if (item.fields.length) {
          const fields = document.createElement("div");
          fields.className = "fields";
          item.fields.slice(0, 12).forEach((field) => {
            const chip = document.createElement("span");
            chip.textContent = field;
            fields.append(chip);
          });
          layersEl.append(fields);
        }
      });
    });
  }

  async function start() {
    try {
      const { ok, data } = await getJson("/api/ability-ir-code-names");
      if (!ok) throw new Error(data.error || "no names");
      names = data.names;
      renderNames();
      setStatus(`${names.length} names loaded. Load an ability by GUID, or write code.`, "ok");
      // Opened from the ability editor: load the ability it names (?guid=).
      const params = new URLSearchParams(location.search);
      if (params.get("guid")) {
        guid.value = params.get("guid");
        load();
      }
    } catch (error) {
      setStatus(`Could not load the names: ${error.message}`, "bad");
    }
  }

  start();
})();
