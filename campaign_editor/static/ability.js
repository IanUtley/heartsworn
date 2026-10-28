// The ability editor: the ability as compact pseudocode, beside its sentence text.
//
// The pseudocode is the semantic IR (campaign_editor/ability_ir_semantic.py), one line per
// clause, each value an inline control. Editing the pseudocode asks the server for the
// sentences; editing the sentences asks the server for the IR. Nothing is stored until
// Save, which goes through the DSL's own checks.
//
// Controls write straight into the clause objects the server sent, so fields this page
// does not know about stay in the IR untouched.

const $ = (id) => document.getElementById(id);
const DEBOUNCE_MS = 350;

// Clause fields this page lays out itself; any other field is shown under "more".
const LAID_OUT = new Set(["shape", "target", "amount", "count", "duration", "condition",
  "contingent", "layer", "recalculate", "new_target"]);
const SINGULAR = new Set(["a", "an", "1", "+1", "one"]);   // as ability_dsl renders {s}

let vocab = { triggers: [], shapes: [], durations: [] };
let ir = null;          // the semantic form being edited
let guid = "";          // the ability's GUID, or "" for a new ability
let timer = null;
let quiet = false;      // set while the server's text is written into the sentence pane
const expanded = new Set();   // clause objects whose "more" fields are showing

// Inside the card page: hide the page title and follow the card page's theme.
if (window.self !== window.top) {
  document.documentElement.classList.add("embedded");
  try {
    const theme = window.parent.document.documentElement.getAttribute("data-theme");
    if (theme) document.documentElement.setAttribute("data-theme", theme);
  } catch (_) { /* another origin: keep the system theme */ }
}

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  let data = {};
  try { data = await response.json(); } catch (_) { data = { error: `HTTP ${response.status}` }; }
  if (response.status === 401) {
    // The editor keeps sessions in memory, so a restart signs everyone out.
    data = { error: "Not logged in. Log in at http://localhost:8765/ in this browser, then reload this page." };
    setStatus(data.error, "bad");
  }
  return { ok: response.ok, data };
}

function setStatus(text, kind = "") {
  const status = $("status");
  status.textContent = text;
  status.className = `status ${kind}`;
}

function showProblems(problems) {
  $("problems").replaceChildren(...(problems || []).filter(Boolean).map((problem) => el("li", "", problem)));
}

function el(tag, className = "", text = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text) element.textContent = text;
  return element;
}

function option(value, label) {
  const element = document.createElement("option");
  element.value = value;
  element.textContent = label;
  return element;
}

// A select whose current value is always one of its options, so opening an ability with
// a value outside the vocabulary shows it rather than silently blanking it.
function fillSelect(select, choices, value) {
  const options = choices.map(([v, label]) => option(v, label));
  if (value && !choices.some(([v]) => v === value)) options.push(option(value, `${value} (?)`));
  select.replaceChildren(...options);
  select.value = value ?? "";
  select.classList.toggle("empty", !select.value);
}

function setFormEnabled(enabled) {
  $("form").classList.toggle("hidden", !enabled);
  $("form-empty").classList.toggle("hidden", enabled);
  $("save").disabled = !enabled;
  if (!enabled) $("text-pane").open = true;
}

// ---- inline controls -------------------------------------------------------------

// Size an inline input to its text, so a line reads like a sentence.
function fit(input) {
  const text = input.value || input.placeholder || "";
  input.size = Math.min(60, Math.max(2, text.length + 1));
  input.classList.toggle("empty", !input.value);
}

// An inline input bound to clause[key]; a number stays a number.
// Completion: a text box offers the phrases the DSL knows for it (a datalist).
const SUGGESTIONS = { condition: "dl-conditions", count: "dl-counts", requires: "dl-conditions", settings: "dl-settings" };

function suggestFrom(input, key) {
  if (SUGGESTIONS[key]) input.setAttribute("list", SUGGESTIONS[key]);
  return input;
}

function inlineInput(clause, key, placeholder, onChange) {
  const input = el("input");
  input.type = "text";
  input.value = clause[key] ?? "";
  input.placeholder = placeholder;
  suggestFrom(input, key);
  input.title = key;
  input.setAttribute("aria-label", key);
  fit(input);
  input.addEventListener("input", () => {
    if (typeof clause[key] === "number") {
      const number = Number(input.value);
      clause[key] = input.value.trim() === "" ? 0 : (Number.isFinite(number) ? number : input.value);
    } else {
      clause[key] = input.value;
    }
    fit(input);
    if (onChange) onChange();
    scheduleToText();
  });
  return input;
}

function inlineCheck(clause, key) {
  const box = el("input");
  box.type = "checkbox";
  box.checked = Boolean(clause[key]);
  box.title = key;
  box.addEventListener("change", () => { clause[key] = box.checked; scheduleToText(); });
  return box;
}

function toolButton(label, title, handler, disabled = false) {
  const button = el("button", "", label);
  button.type = "button";
  button.title = title;
  button.disabled = disabled;
  button.addEventListener("click", handler);
  return button;
}

// A shape's phrase as words and {slot} names, e.g. "deal {amount} damage to {target}".
function phraseParts(shapeId) {
  const shape = vocab.shapes.find((s) => s.id === shapeId);
  const phrase = shape ? shape.phrase : "{target}";
  return phrase.split(/(\{\w+\})/).filter(Boolean).map((part) => {
    const slot = part.match(/^\{(\w+)\}$/);
    return slot ? { slot: slot[1] } : { text: part.trim() };
  }).filter((part) => part.slot || part.text);
}

const SLOT_HINTS = { target: "target", amount: "1", card: "card", counter: "counter", ability: "ability",
  conversation: "conversation", deck: "deck", keyword: "keyword", shard: "shard", attribute: "attribute",
  number: "0", event: "event", filter: "filter" };

// ---- targets: a choice from the targets each shape reads back with ---------------------

const targetCache = {};   // shape id -> [{phrase, uses, all_uses}], filled once per shape

async function loadTargets(shape) {
  if (!shape || targetCache[shape]) return;
  targetCache[shape] = [];   // marks the shape as being loaded
  const { ok, data } = await api(`/api/ability-dsl-targets?shape=${encodeURIComponent(shape)}`);
  targetCache[shape] = ok ? (data.targets || []) : [];
  renderForm();
}

// A target is a dropdown of the phrases that shape reads back with. A target the list does
// not hold (an older ability's wording) still shows, marked, so opening it changes nothing.
function targetSelect(clause) {
  const select = el("select", "target");
  select.setAttribute("aria-label", "target");
  const current = clause.target || "";
  const choices = (targetCache[clause.shape] || []).map((t) => [t.phrase, t.phrase]);
  if (current && !choices.some(([phrase]) => phrase === current)) {
    choices.push([current, `${current} (not in list)`]);
  }
  fillSelect(select, [["", "choose a target…"], ...choices], current);
  select.classList.toggle("empty", !current);
  select.addEventListener("change", () => {
    clause.target = select.value;
    select.classList.toggle("empty", !select.value);
    scheduleToText();
  });
  loadTargets(clause.shape);
  return select;
}

// ---- state -> pseudocode -----------------------------------------------------------

function clauseLine(clause, index, count) {
  const line = el("div", "line clause-line");
  const tools = el("span", "tools");
  const move = (delta) => () => {
    const to = index + delta;
    if (to < 0 || to >= ir.clauses.length) return;
    [ir.clauses[index], ir.clauses[to]] = [ir.clauses[to], ir.clauses[index]];
    renderForm();
    scheduleToText();
  };
  tools.append(
    toolButton("↑", "Move up", move(-1), index === 0),
    toolButton("↓", "Move down", move(1), index === count - 1),
    toolButton("⋯", "More fields", () => {
      if (expanded.has(clause)) expanded.delete(clause); else expanded.add(clause);
      renderForm();
    }),
    toolButton("✕", "Remove clause", () => {
      ir.clauses.splice(index, 1);
      expanded.delete(clause);
      renderForm();
      scheduleToText();
    }),
  );

  const stmt = el("span", "stmt");
  // How the clause joins the one before: "then", or "if you do" (contingent).
  if (index > 0) {
    const join = el("select", "kw");
    join.setAttribute("aria-label", "joins the clause before");
    join.append(option("then", "then"), option("if you do", "if you do"));
    join.value = clause.contingent ? "if you do" : "then";
    join.addEventListener("change", () => { clause.contingent = join.value === "if you do"; scheduleToText(); });
    stmt.append(join);
  } else if (clause.contingent) {
    stmt.append(inlineCheck(clause, "contingent"), el("span", "kw", "if you do"));
  }

  const shape = el("select", "shape");
  shape.setAttribute("aria-label", "clause shape");
  fillSelect(shape, vocab.shapes.map((s) => [s.id, s.id]), clause.shape);
  shape.title = vocab.shapes.find((s) => s.id === clause.shape)?.phrase || "clause shape";
  shape.addEventListener("change", () => {
    clause.shape = shape.value;
    // Give each slot of the new phrase a value so the IR is complete.
    for (const part of phraseParts(clause.shape)) {
      if (part.slot && part.slot !== "s" && clause[part.slot] === undefined) clause[part.slot] = "";
    }
    renderForm();
    scheduleToText();
  });
  stmt.append(shape);

  const plurals = [];
  const replural = () => {
    for (const span of plurals) span.textContent = SINGULAR.has(String(clause.amount ?? "").trim()) ? "" : "s";
  };
  const inPhrase = new Set();
  for (const part of phraseParts(clause.shape)) {
    if (part.text) {
      stmt.append(el("span", "word", part.text));
    } else if (part.slot === "s") {
      const span = el("span", "word");
      span.style.marginLeft = "-5px";
      plurals.push(span);
      stmt.append(span);
    } else if (part.slot === "target") {
      inPhrase.add(part.slot);
      stmt.append(targetSelect(clause));
    } else {
      inPhrase.add(part.slot);
      stmt.append(inlineInput(clause, part.slot, SLOT_HINTS[part.slot] || part.slot,
        part.slot === "amount" ? replural : null));
    }
  }
  replural();

  // The optional parts, in the order the sentence says them.
  if (!inPhrase.has("count")) {
    stmt.append(el("span", clause.count ? "kw" : "faint", "for each"),
      inlineInput(clause, "count", "…"));
  }
  const duration = el("select");
  duration.setAttribute("aria-label", "duration");
  // The IR holds a duration as its words ("this turn"), as the sentences say it.
  fillSelect(duration, [["", "(usual duration)"], ...vocab.durations.map((d) => [d.label, d.label])], clause.duration || "");
  duration.addEventListener("change", () => {
    clause.duration = duration.value;
    duration.classList.toggle("empty", !duration.value);
    scheduleToText();
  });
  stmt.append(duration);
  stmt.append(el("span", clause.condition ? "kw" : "faint", "if"), inlineInput(clause, "condition", "condition"));
  // A shape whose phrase has no amount: show one only if the clause carries one.
  if (!inPhrase.has("amount") && clause.amount !== undefined && clause.amount !== "") {
    stmt.append(el("span", "faint", "amount"), inlineInput(clause, "amount", "—", replural));
  }

  if (expanded.has(clause)) stmt.append(extraFields(clause, inPhrase));
  line.append(tools, stmt);
  return line;
}

// The fields the line does not show: layer, recalculate, new target, and anything else
// the server sent with the clause.
function extraFields(clause, inPhrase) {
  const box = el("span", "extras");
  const labelled = (text, control) => {
    const label = el("label");
    label.append(el("span", "", text), control);
    return label;
  };
  box.append(labelled("layer", inlineInput(clause, "layer", "0")));
  box.append(labelled("recalculate", inlineInput(clause, "recalculate", "—")));
  box.append(labelled("new target", inlineCheck(clause, "new_target")));
  if (index0(clause)) box.append(labelled("if you do", inlineCheck(clause, "contingent")));
  for (const key of Object.keys(clause)) {
    if (LAID_OUT.has(key) || inPhrase.has(key)) continue;
    const value = clause[key];
    if (typeof value === "boolean") box.append(labelled(key, inlineCheck(clause, key)));
    else if (value === null || typeof value !== "object") box.append(labelled(key, inlineInput(clause, key, "—")));
    else box.append(labelled(key, el("code", "", JSON.stringify(value))));   // kept as sent
  }
  return box;
}

function index0(clause) {
  return ir && ir.clauses[0] === clause && !clause.contingent;
}

function renderForm() {
  if (!ir) return;
  fillSelect($("trigger"), [["", "(no trigger)"], ...vocab.triggers.map((t) => [t, t])], ir.trigger || "");
  $("settings").value = (ir.settings || []).join(", ");
  $("requires").value = ir.requires || "";
  const clauses = ir.clauses || (ir.clauses = []);
  for (const clause of [...expanded]) if (!clauses.includes(clause)) expanded.delete(clause);
  const lines = clauses.map((clause, index) => clauseLine(clause, index, clauses.length));
  if (!lines.length) lines.push(el("div", "faint empty-note", "do nothing"));
  $("clauses").replaceChildren(...lines);
}

// ---- the two views stay in step ------------------------------------------------------

function scheduleToText() {
  if (!ir) return;
  clearTimeout(timer);
  timer = setTimeout(async () => {
    const { ok, data } = await api("/api/ability-ir-text", { ir });
    if (!ok) return setStatus(data.error || "could not render", "bad");
    quiet = true;
    $("sentences").value = data.text;
    quiet = false;
    refreshProblems();
  }, DEBOUNCE_MS);
}

function scheduleFromText() {
  clearTimeout(timer);
  timer = setTimeout(async () => {
    const { ok, data } = await api("/api/ability-ir-from-text", { text: $("sentences").value });
    if (!ok || data.error) {
      showProblems([data.error || "the sentences could not be read"]);
      return;
    }
    ir = data.ir;
    renderForm();
    showProblems(data.problems);
    setStatus("Pseudocode updated from the sentences.");
  }, DEBOUNCE_MS);
}

async function refreshProblems() {
  if (!ir) return;
  const { data } = await api("/api/ability-ir-check", { ir });
  showProblems(data.problems);
  const bad = data.problems && data.problems.length;
  setStatus(bad ? "The pseudocode has problems (listed below)." : "Pseudocode and sentences agree.", bad ? "bad" : "ok");
}

// ---- open, new, save ----------------------------------------------------------------

async function openAbility() {
  const value = $("guid").value.trim();
  if (!value) return setStatus("Enter an ability GUID first.", "bad");
  setStatus("Opening…");
  const { ok, data } = await api(`/api/ability-ir?guid=${encodeURIComponent(value)}`);
  if (!ok) return setStatus(data.error || "could not open the ability", "bad");
  guid = value;
  expanded.clear();
  if (!data.semantic) {
    ir = null;
    setFormEnabled(false);
    $("reasons").replaceChildren(...(data.reasons || []).map((r) => el("li", "", r)));
    quiet = true;
    $("sentences").value = data.text || "";
    quiet = false;
    showProblems([]);
    return setStatus("Read only: this ability has no form.", "bad");
  }
  ir = data.ir;
  setFormEnabled(true);
  renderForm();
  quiet = true;
  $("sentences").value = data.text;
  quiet = false;
  showProblems([]);
  setStatus("Opened.", "ok");
  syncIrLink();
}

function newAbility() {
  guid = "";
  $("guid").value = "";
  expanded.clear();
  ir = { trigger: "", settings: [], requires: "", clauses: [] };
  setFormEnabled(true);
  renderForm();
  quiet = true;
  $("sentences").value = "";
  quiet = false;
  showProblems([]);
  setStatus("New ability: pick a trigger or add a clause, then save.");
  syncIrLink();
}

async function save() {
  if (!ir) return;
  setStatus("Saving…");
  const { ok, data } = await api("/api/edit/ability-ir", { guid, ir });
  if (!ok) {
    showProblems(data.problems || [data.error]);
    return setStatus(data.error || "not saved", "bad");
  }
  guid = data.guid || guid;
  $("guid").value = guid;
  showProblems([]);
  setStatus("Saved.", "ok");
  syncIrLink();
}

// The IR editor opens the ability being edited (its ?guid= parameter), so the link follows the GUID.
function syncIrLink() {
  const link = $("ir-link");
  link.href = guid ? `/ir?guid=${encodeURIComponent(guid)}` : "/ir";
}

// ---- wiring -------------------------------------------------------------------------

$("open").addEventListener("click", openAbility);
$("guid").addEventListener("keydown", (event) => { if (event.key === "Enter") openAbility(); });
$("new").addEventListener("click", newAbility);
$("save").addEventListener("click", save);
$("add-clause").addEventListener("click", () => {
  if (!ir) return;
  (ir.clauses || (ir.clauses = [])).push({ shape: vocab.shapes[0]?.id || "", target: "you", amount: "1",
    count: "", duration: "", condition: "", contingent: false, layer: 0, recalculate: "", new_target: false });
  renderForm();
  scheduleToText();
});
$("trigger").addEventListener("change", () => {
  if (!ir) return;
  ir.trigger = $("trigger").value;
  $("trigger").classList.toggle("empty", !ir.trigger);
  scheduleToText();
});
$("settings").addEventListener("input", () => {
  if (!ir) return;
  ir.settings = $("settings").value.split(",").map((s) => s.trim()).filter(Boolean);
  scheduleToText();
});
$("requires").addEventListener("input", () => {
  if (!ir) return;
  ir.requires = $("requires").value.trim();
  scheduleToText();
});
$("sentences").addEventListener("input", () => { if (!quiet) scheduleFromText(); });

// The DSL's own phrase lists, for completion in the text boxes.
async function loadSuggestions() {
  const { ok, data } = await api("/api/ability-dsl-options");
  if (!ok) return;
  const lists = {
    "dl-conditions": (data.conditions || []).map((c) => c.phrase),
    "dl-counts": (data.counts || []).map((c) => c.phrase),
    "dl-settings": data.settings || [],
  };
  for (const [id, values] of Object.entries(lists)) {
    const list = document.createElement("datalist");
    list.id = id;
    list.append(...values.map((value) => {
      const item = document.createElement("option");
      item.value = value;
      return item;
    }));
    document.body.append(list);
  }
  for (const [id, key] of [["requires", "requires"], ["settings", "settings"]]) {
    const input = document.getElementById(id);
    if (input) suggestFrom(input, key);
  }
}

(async function start() {
  loadSuggestions();
  syncIrLink();
  const { ok, data } = await api("/api/ability-ir-vocab");
  if (!ok) return setStatus(data.error || "could not load the editor's vocabulary", "bad");
  vocab = { triggers: [], shapes: [], durations: [], ...data };
  fillSelect($("trigger"), [["", "(no trigger)"], ...vocab.triggers.map((t) => [t, t])], "");
  const params = new URLSearchParams(location.search);
  if (params.get("guid")) { $("guid").value = params.get("guid"); openAbility(); }
  else if (params.has("new")) newAbility();
})();
