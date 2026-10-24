// The ability editor: a sentence pane and a form pane that update each other.
//
// The form is the semantic IR (campaign_editor/ability_ir_semantic.py). Editing the form
// asks the server for the sentences; editing the sentences asks the server for the form.
// Nothing is stored until Save, which goes through the DSL's own checks.

const $ = (id) => document.getElementById(id);
const DEBOUNCE_MS = 350;

let vocab = { triggers: [], shapes: [], durations: [] };
let ir = null;          // the semantic form being edited
let guid = "";          // the ability's GUID, or "" for a new ability
let timer = null;
let quiet = false;      // set while the server's text is written into the sentence pane

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  let data = {};
  try { data = await response.json(); } catch (_) { data = { error: `HTTP ${response.status}` }; }
  return { ok: response.ok, data };
}

function setStatus(text, kind = "") {
  const status = $("status");
  status.textContent = text;
  status.className = `status ${kind}`;
}

function showProblems(problems) {
  const list = $("problems");
  list.replaceChildren(...(problems || []).map((problem) => {
    const item = document.createElement("li");
    item.textContent = problem;
    return item;
  }));
}

function option(value, label) {
  const element = document.createElement("option");
  element.value = value;
  element.textContent = label;
  return element;
}

function fillSelects() {
  const trigger = $("trigger");
  trigger.replaceChildren(option("", "(none)"), ...vocab.triggers.map((t) => option(t, t)));
}

function shapeLabel(id) {
  const shape = vocab.shapes.find((s) => s.id === id);
  return shape ? `${id}: ${shape.phrase}` : id;
}

function setFormEnabled(enabled) {
  $("form").classList.toggle("hidden", !enabled);
  $("form-empty").classList.toggle("hidden", enabled);
  $("save").disabled = !enabled;
}

// ---- form -> state -------------------------------------------------------------

function readForm() {
  if (!ir) return;
  ir.trigger = $("trigger").value;
  ir.settings = $("settings").value.split(",").map((s) => s.trim()).filter(Boolean);
  ir.requires = $("requires").value.trim();
  document.querySelectorAll("#clauses .clause").forEach((box) => {
    const index = Number(box.dataset.index);
    const clause = ir.clauses[index];
    const field = (name) => box.querySelector(`[data-field="${name}"]`);
    clause.shape = field("shape").value;
    clause.target = field("target").value.trim();
    clause.amount = field("amount").value.trim();
    clause.count = field("count").value.trim();
    clause.duration = field("duration").value;
    clause.condition = field("condition").value.trim();
    clause.contingent = field("contingent").checked;
  });
}

// ---- state -> form -------------------------------------------------------------

function clauseBox(clause, index, count) {
  const box = document.createElement("div");
  box.className = "clause";
  box.dataset.index = String(index);

  const head = document.createElement("header");
  head.className = "tools";
  head.style.display = "flex";
  const title = document.createElement("strong");
  title.textContent = `Clause ${index + 1}`;
  const tools = document.createElement("span");
  tools.style.marginLeft = "auto";
  tools.style.display = "flex";
  tools.style.gap = "4px";
  const move = (delta) => () => {
    readForm();
    const target = index + delta;
    if (target < 0 || target >= ir.clauses.length) return;
    [ir.clauses[index], ir.clauses[target]] = [ir.clauses[target], ir.clauses[index]];
    renderForm();
    scheduleToText();
  };
  const remove = () => {
    readForm();
    ir.clauses.splice(index, 1);
    renderForm();
    scheduleToText();
  };
  for (const [label, handler, disabled] of [
    ["↑", move(-1), index === 0], ["↓", move(1), index === count - 1], ["✕", remove, false],
  ]) {
    const button = document.createElement("button");
    button.textContent = label;
    button.disabled = disabled;
    button.addEventListener("click", handler);
    tools.append(button);
  }
  head.append(title, tools);
  box.append(head);

  const row = (label, control) => {
    const wrapper = document.createElement("div");
    wrapper.className = "row";
    const text = document.createElement("span");
    text.textContent = label;
    wrapper.append(text, control);
    return wrapper;
  };
  const input = (name, value, placeholder = "") => {
    const element = document.createElement("input");
    element.type = "text";
    element.dataset.field = name;
    element.value = value ?? "";
    element.placeholder = placeholder;
    element.addEventListener("input", onFormInput);
    return element;
  };
  const select = (name, choices, value) => {
    const element = document.createElement("select");
    element.dataset.field = name;
    element.replaceChildren(...choices);
    element.value = value ?? "";
    element.addEventListener("change", onFormInput);
    return element;
  };

  box.append(row("Shape", select("shape", vocab.shapes.map((s) => option(s.id, shapeLabel(s.id))), clause.shape)));
  box.append(row("Target", input("target", clause.target, "target opposing troop")));
  box.append(row("Amount", input("amount", clause.amount, "2")));
  box.append(row("Count", input("count", clause.count, "for each ...")));
  box.append(row("Duration", select("duration",
    [option("", "(usual)"), ...vocab.durations.map((d) => option(d.value, d.label))], clause.duration)));
  box.append(row("Condition", input("condition", clause.condition, "you have 1 ruby threshold")));
  const contingent = document.createElement("input");
  contingent.type = "checkbox";
  contingent.dataset.field = "contingent";
  contingent.checked = Boolean(clause.contingent);
  contingent.addEventListener("change", onFormInput);
  box.append(row("If you do", contingent));
  return box;
}

function renderForm() {
  if (!ir) return;
  $("trigger").value = ir.trigger || "";
  $("settings").value = (ir.settings || []).join(", ");
  $("requires").value = ir.requires || "";
  const clauses = $("clauses");
  clauses.replaceChildren(...ir.clauses.map((clause, index) =>
    clauseBox(clause, index, ir.clauses.length)));
}

// ---- the two panes stay in step --------------------------------------------------

function onFormInput() {
  readForm();
  scheduleToText();
}

function scheduleToText() {
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
    setStatus("Form updated from the sentences.");
  }, DEBOUNCE_MS);
}

async function refreshProblems() {
  if (!ir) return;
  const { data } = await api("/api/ability-ir-check", { ir });
  showProblems(data.problems);
  setStatus("Form and sentences agree.", data.problems && data.problems.length ? "bad" : "ok");
}

// ---- open, new, save ----------------------------------------------------------

async function openAbility() {
  const value = $("guid").value.trim();
  if (!value) return setStatus("Enter an ability GUID first.", "bad");
  setStatus("Opening…");
  const { ok, data } = await api(`/api/ability-ir?guid=${encodeURIComponent(value)}`);
  if (!ok) return setStatus(data.error || "could not open the ability", "bad");
  guid = value;
  if (!data.semantic) {
    ir = null;
    setFormEnabled(false);
    $("reasons").replaceChildren(...(data.reasons || []).map((r) => {
      const item = document.createElement("li");
      item.textContent = r;
      return item;
    }));
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
}

function newAbility() {
  guid = "";
  ir = { trigger: "", settings: [], requires: "", clauses: [] };
  setFormEnabled(true);
  renderForm();
  quiet = true;
  $("sentences").value = "";
  quiet = false;
  showProblems([]);
  setStatus("New ability: add a trigger or a clause, then save.");
}

async function save() {
  if (!ir) return;
  readForm();
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
}

// ---- wiring ---------------------------------------------------------------------

$("open").addEventListener("click", openAbility);
$("new").addEventListener("click", newAbility);
$("save").addEventListener("click", save);
$("add-clause").addEventListener("click", () => {
  if (!ir) return;
  readForm();
  ir.clauses.push({ shape: vocab.shapes[0]?.id || "", target: "you", amount: "1", count: "",
    duration: "", condition: "", contingent: false, layer: 0, recalculate: "", new_target: false });
  renderForm();
  scheduleToText();
});
$("trigger").addEventListener("change", onFormInput);
$("settings").addEventListener("input", onFormInput);
$("requires").addEventListener("input", onFormInput);
$("sentences").addEventListener("input", () => { if (!quiet) scheduleFromText(); });

(async function start() {
  const { ok, data } = await api("/api/ability-ir-vocab");
  if (!ok) return setStatus(data.error || "could not load the editor's vocabulary", "bad");
  vocab = data;
  fillSelects();
  const params = new URLSearchParams(location.search);
  if (params.get("guid")) { $("guid").value = params.get("guid"); openAbility(); }
})();
