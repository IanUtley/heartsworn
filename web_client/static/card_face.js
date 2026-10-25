// The card face painter: draws a card the way the client does, from the client's
// own card atlases when they are loaded, and as a drawn fallback when they are not.
//
// Shared by the 3D battle board (board3d.js) and the campaign editor's card view.
// paintFace(face, onArt, assets) takes the asset source as `assets` (the object with
// cardChrome(onArt) and the art helpers), defaulting to window.HexAssets. The face
// descriptor is the one app.js builds for a card (faceOf).
//
// shade() here mixes in plain sRGB. The board's THREE-based shade is separate and
// stays in board3d.js.

export const SLEEVES = {
  classic: ["#1d2c55", "#101a3a", "#c9a24c"], blood: ["#6d2a8f", "#2a0d3b", "#c88cf0"],
  ruby: ["#b83224", "#3d0b06", "#ffb38a"], sapphire: ["#2b6fbf", "#0a1d3b", "#9ad1ff"],
  wild: ["#3c8f3a", "#0d2a0c", "#b8f08c"], diamond: ["#f6f0d4", "#9a8a55", "#fffbe9"],
  cosmic: ["#3a1670", "#050a2b", "#e6c8ff"],
};

export function canvas(w, h = w) {
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  return [c, c.getContext("2d")];
}

// Mixes a #rrggbb colour towards white (amount > 0) or black (amount < 0), in sRGB.
function shade(hex, amount) {
  const n = parseInt(hex.slice(1), 16);
  const target = amount > 0 ? 255 : 0, k = Math.abs(amount);
  const mix = (c) => Math.round(c + (target - c) * k);
  const r = mix((n >> 16) & 255), g = mix((n >> 8) & 255), b = mix(n & 255);
  return "#" + [r, g, b].map((c) => c.toString(16).padStart(2, "0")).join("");
}

export function ellipsize(ctx, text, width) {
  if (ctx.measureText(text).width <= width) return text;
  while (text.length > 1 && ctx.measureText(text + "…").width > width) text = text.slice(0, -1);
  return text + "…";
}

export const artImages = new Map();
export function artImage(url, onLoad) {
  if (!url) return null;
  let entry = artImages.get(url);
  if (!entry) {
    entry = { img: new Image(), ready: false, waiting: [] };
    entry.img.onload = () => { entry.ready = true; entry.waiting.forEach((f) => f()); entry.waiting = []; };
    entry.img.src = url;
    artImages.set(url, entry);
  }
  if (!entry.ready) entry.waiting.push(onLoad);
  return entry.ready ? entry.img : null;
}

export function paintSleeve(ctx, W, H, name) {
  const [a, b, edge] = SLEEVES[name] || SLEEVES.classic;
  const g = ctx.createRadialGradient(W / 2, H * 0.42, 10, W / 2, H / 2, H * 0.7);
  g.addColorStop(0, a); g.addColorStop(1, b);
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = edge; ctx.lineWidth = 6;
  ctx.strokeRect(10, 10, W - 20, H - 20);
  ctx.lineWidth = 4; ctx.globalAlpha = 0.8;
  ctx.beginPath(); ctx.ellipse(W / 2, H / 2, W * 0.3, H * 0.33, 0, 0, Math.PI * 2); ctx.stroke();
  ctx.fillStyle = edge; ctx.globalAlpha = 1;
  ctx.beginPath(); ctx.arc(W / 2, H / 2, W * 0.09, 0, Math.PI * 2); ctx.fill();
}

/* ------------------------------------------------------------------ */
/* Card faces from the client's own card atlases                       */
/* ------------------------------------------------------------------ */

// The client (GoCardBuilder) builds each card from NGUI atlas sprites. Every
// sprite's padding is its margin on a card-sized canvas, so the padding alone
// places it; the faces here are laid out on the Elements atlas canvas.
const FACE_W = 448, FACE_H = 625;
const FACE_FONT = "Arial, 'Liberation Sans', Helvetica, sans-serif";
const THRESHOLD_ORDER = ["Blood", "Diamond", "Ruby", "Sapphire", "Wild"];   // BuildThreshold order
const FACTION_SPRITE = { Aria: "Faction_Ardent", Underworld: "Faction_Underworld" };
const KEYWORD = "#3ee23e", QUICK = "#ff9c1a";
const STAT_TINT = { buffed: "#00dd00", hurt: "#dd0000" };   // UpdateLabels colours

function chromeSprite(chrome, name) {
  for (const atlas of Object.values(chrome)) if (atlas.sprites[name]) return [atlas.image, atlas.sprites[name]];
  return [null, null];
}

// Draws a sprite where the client puts it, or into rect [x, y, w, h].
function drawSprite(ctx, chrome, name, { rect = null, dx = 0, dy = 0, flip = false } = {}) {
  const [image, s] = chromeSprite(chrome, name);
  if (!s) return null;
  const k = FACE_W / (s.left + s.w + s.right);
  let [x, y, w, h] = rect || [s.left * k, s.top * k, s.w * k, s.h * k];
  if (flip && !rect) x = FACE_W - x - w;
  x += dx; y += dy;
  ctx.save();
  ctx.translate(flip ? x + w : x, y);
  if (flip) ctx.scale(-1, 1);
  if (s.rotated) {
    // Stored a quarter turn clockwise, so it is turned back here.
    ctx.translate(0, h); ctx.rotate(-Math.PI / 2);
    ctx.drawImage(image, s.x, s.y, s.h, s.w, 0, 0, h, w);
  } else {
    ctx.drawImage(image, s.x, s.y, s.w, s.h, 0, 0, w, h);
  }
  ctx.restore();
  return [x, y, w, h];
}

// A 9-slice sprite stretched over a rect, keeping its borders unscaled.
function drawSliced(ctx, chrome, name, x, y, w, h) {
  const [image, s] = chromeSprite(chrome, name);
  if (!s) return;
  const [l, r, t, b] = s.border || [0, 0, 0, 0];
  const xs = [[s.x, x, l, l], [s.x + l, x + l, s.w - l - r, w - l - r], [s.x + s.w - r, x + w - r, r, r]];
  const ys = [[s.y, y, t, t], [s.y + t, y + t, s.h - t - b, h - t - b], [s.y + s.h - b, y + h - b, b, b]];
  for (const [sx, dx, sw, dw] of xs) for (const [sy, dy, sh, dh] of ys) {
    if (sw > 0 && sh > 0 && dw > 0 && dh > 0) ctx.drawImage(image, sx, sy, sw, sh, dx, dy, dw, dh);
  }
}

// BuildFrame: several shards frame as Multi, then artifacts, then colourless
// (Shardless), else the card's one shard. A champion counts as every shard.
function frameOf(face) {
  const types = face.types || [];
  const shards = Object.keys(face.thresholds || {}).filter((color) => color !== "Colorless");
  if (types.includes("Champion") || shards.length > 1) return "Multi";
  if (types.includes("Artifact")) return "Artifact";
  return shards.length ? shards[0] : "Shardless";
}

// The client's type line: flags in capitals (artifact troops read
// "ARTIFACT, TROOP"; QuickAction "QUICK ACTION"), then the subtype.
function typeLine(face) {
  const types = (face.types || []).filter((t) => t !== "Unknown");
  const order = ["Artifact", "Troop", "QuickAction", "BasicAction", "Constant", "Resource", "Champion"];
  const rank = (t) => (order.includes(t) ? order.indexOf(t) : order.length);
  types.sort((a, b) => rank(a) - rank(b));
  const words = types.map((t) => t.replace(/([a-z])([A-Z])/g, "$1 $2").toUpperCase());
  const sub = String(face.subtype || "").trim();
  return { words: words.join(", ") || String(face.type || "").toUpperCase(), sub };
}

// Game text as runs: <p> breaks the line, <b> bolds, [SHARD] is a threshold
// gem, [(n)] a cost bubble and [ATK]/[DEF] the stat icons. Bold keywords that
// open the text (before the first break) are green, as on the client.
function textRuns(raw) {
  const runs = [];
  let bold = false;
  const parts = String(raw || "").replace(/<br\s*\/?>/gi, "<p>").split(/(<[^>]*>|\[[^\]]*\])/);
  for (const part of parts) {
    if (!part) continue;
    const tag = part.toLowerCase();
    if (tag === "<b>") bold = true;
    else if (tag === "</b>") bold = false;
    else if (tag === "<p>") { if (runs.length && !runs[runs.length - 1].br) runs.push({ br: true }); }
    else if (part[0] === "<") continue;
    else if (part[0] === "[") runs.push(textToken(part.slice(1, -1), bold));
    else runs.push({ text: part, bold });
  }
  const end = runs.findIndex((r) => r.br);
  const head = runs.slice(0, end < 0 ? runs.length : end);
  if (head.some((r) => r.bold && r.text) && head.every((r) => r.bold || /^[\s,]*$/.test(r.text || ""))) {
    head.forEach((r) => { if (r.bold) r.color = KEYWORD; });
  }
  return runs;
}

function textToken(token, bold) {
  const upper = token.toUpperCase();
  if (THRESHOLD_ORDER.some((s) => s.toUpperCase() === upper)) return { sprite: `Threshold_${upper[0]}${upper.slice(1).toLowerCase()}` };
  if (upper === "ATK" || upper === "ATTACK") return { sprite: "AttackIcon" };
  if (upper === "DEF" || upper === "DEFENSE") return { sprite: "DefenseIcon" };
  const n = /^\(?(\d+|X)\)?$/.exec(upper);
  if (n) return { bubble: n[1] };
  const symbols = { ARROWR: "→", EXHAUST: "⤵", ACT: "⤵", CHARGE: "⚡", BASIC: "Basic" };
  return { text: symbols[upper] || token.charAt(0) + token.slice(1).toLowerCase(), bold };
}

// Word-wraps runs into lines of placed items.
function wrapRuns(ctx, runs, width, size) {
  const lines = [[]];
  let x = 0;
  const place = (item, w, ink = w) => {
    if (x + ink > width && x > 0) { lines.push([]); x = 0; if (item.text && !item.text.trim()) return; }
    lines[lines.length - 1].push({ ...item, x, w });
    x += w;
  };
  for (const run of runs) {
    if (run.br) { lines.push([]); x = 0; continue; }
    if (run.text === undefined) { place(run, size * 1.15); continue; }
    ctx.font = `bold ${size}px ${FACE_FONT}`;
    for (const word of run.text.split(/(?<=\s)/)) place({ ...run, text: word }, ctx.measureText(word).width, ctx.measureText(word.trimEnd()).width);
  }
  return lines;
}

// Rules text shrunk until it fits its box (the client's ShrinkContent).
function drawRules(ctx, chrome, runs, box, { size = 17, min = 9, align = "left", italic = false, color = "#fff" } = {}) {
  let lines;
  for (; size > min; size -= 0.5) {
    lines = wrapRuns(ctx, runs, box.w, size);
    if (lines.length * size * 1.18 <= box.h) break;
  }
  ctx.textBaseline = "middle"; ctx.textAlign = "left";
  ctx.shadowColor = "rgba(0,0,0,.9)"; ctx.shadowBlur = 3;
  lines.forEach((line, i) => {
    const y = box.y + (i + 0.5) * size * 1.18;
    const used = line.length ? line[line.length - 1].x + line[line.length - 1].w : 0;
    const x0 = box.x + (align === "center" ? (box.w - used) / 2 : 0);
    for (const item of line) {
      if (item.sprite) {
        drawSprite(ctx, chrome, item.sprite, { rect: [x0 + item.x + size * 0.07, y - size * 0.5, size, size] });
      } else if (item.bubble) {
        const cx = x0 + item.x + size * 0.55;
        ctx.fillStyle = "#e9e4d8"; ctx.beginPath(); ctx.arc(cx, y, size * 0.5, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = "#111"; ctx.textAlign = "center"; ctx.font = `bold ${size * 0.75}px ${FACE_FONT}`;
        ctx.fillText(item.bubble, cx, y + 1); ctx.textAlign = "left";
      } else {
        // The client sets all card text in its bold face; <b> only matters for keyword colour.
        ctx.font = `${italic ? "italic " : ""}bold ${size}px ${FACE_FONT}`;
        ctx.fillStyle = item.color || color;
        ctx.fillText(item.text, x0 + item.x, y);
      }
    }
  });
  ctx.shadowBlur = 0;
}

// Text with the client's label outline (UILabel.Effect.Outline).
function outlined(ctx, text, x, y, size, { fill = "#fff", outline = "#000", width = 4, align = "center", maxWidth = 0, italic = false } = {}) {
  ctx.font = `${italic ? "italic " : ""}bold ${size}px ${FACE_FONT}`;
  if (maxWidth) {
    while (size > 10 && ctx.measureText(text).width > maxWidth) ctx.font = `${italic ? "italic " : ""}bold ${--size}px ${FACE_FONT}`;
  }
  ctx.textAlign = align; ctx.textBaseline = "middle"; ctx.lineJoin = "round";
  if (width) { ctx.strokeStyle = outline; ctx.lineWidth = width; ctx.strokeText(text, x, y); }
  ctx.fillStyle = fill; ctx.fillText(text, x, y);
}

// Art scaled to cover a rect, centred.
function drawCover(ctx, img, x, y, w, h) {
  const s = Math.max(w / img.width, h / img.height);
  ctx.save(); ctx.beginPath(); ctx.rect(x, y, w, h); ctx.clip();
  ctx.drawImage(img, x + (w - img.width * s) / 2, y + (h - img.height * s) / 2, img.width * s, img.height * s);
  ctx.restore();
}

function drawArtOrGlyph(ctx, face, onArt, x, y, w, h) {
  const img = artImage(face.art, onArt);
  if (img) { drawCover(ctx, img, x, y, w, h); return; }
  const g = ctx.createRadialGradient(x + w / 2, y + h * 0.4, 10, x + w / 2, y + h / 2, h * 0.75);
  g.addColorStop(0, shade(face.shard || "#8d939c", -0.2)); g.addColorStop(1, "#0b0a09");
  ctx.fillStyle = g; ctx.fillRect(x, y, w, h);
  ctx.fillStyle = "rgba(255,255,255,.3)"; ctx.font = `110px serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(face.glyph || "", x + w / 2, y + h / 2);
}

// Rarity disc with the set badge on it (BuildSetRarity). Rarities the client
// has no disc for are shown as a dimmed Common.
function drawSetRarity(ctx, chrome, face, rect) {
  const known = ["Common", "Uncommon", "Rare", "Legendary"].includes(face.rarity);
  ctx.save();
  if (!known) ctx.filter = "brightness(0.5)";
  drawSprite(ctx, chrome, `Rarity_${known ? face.rarity : "Common"}`, rect && { rect });
  ctx.restore();
  const set = face.pve ? "Chapter01" : `SetIcon${String(Math.max(1, face.set || 1)).padStart(2, "0")}`;
  if (rect) {
    const [, s] = chromeSprite(chrome, set);
    if (s) { const k = rect[3] / 38; drawSprite(ctx, chrome, set, { rect: [rect[0] + (rect[2] - s.w * k) / 2, rect[1] + (rect[3] - s.h * k) / 2, s.w * k, s.h * k] }); }
  } else {
    drawSprite(ctx, chrome, set);
  }
}

function paintClientFace(ctx, face, chrome, onArt) {
  const types = face.types || [];
  ctx.fillStyle = "#050505"; ctx.fillRect(0, 0, FACE_W, FACE_H);
  if (types.includes("Resource")) { paintResourceFace(ctx, face, chrome, onArt); return; }
  const troop = !!face.showStats || types.includes("Troop") || types.includes("Champion");

  drawArtOrGlyph(ctx, face, onArt, 34, 46, 380, 382);
  const frame = frameOf(face);
  for (let i = 1; i <= 4; i++) drawSprite(ctx, chrome, `${frame}_${i}`);
  drawSprite(ctx, chrome, `${frame}_Text`);
  drawSprite(ctx, chrome, face.pve ? "TitleBarPVE_1" : "TitleBar");
  drawSprite(ctx, chrome, "ThresholdBar");
  drawSprite(ctx, chrome, "GemBar");
  for (let i = 0; i < Math.min(face.sockets || 0, 3); i++) drawSprite(ctx, chrome, "GemSocket_Major", { dy: i * 46 });
  drawSprite(ctx, chrome, "TypeBar");
  drawSprite(ctx, chrome, "BottomBar");

  // Thresholds stack down the left bar in the client's shard order.
  if (face.immortal || face.pve) drawSprite(ctx, chrome, face.pve ? "PVE" : "Immortal");
  let slot = 0;
  for (const shard of THRESHOLD_ORDER) {
    for (let i = 0; i < ((face.thresholds || {})[shard] || 0); i++) drawSprite(ctx, chrome, `Threshold_${shard}`, { dy: 25 * slot++ });
  }

  // Faction badge and set rarity sit at the two ends of the type bar.
  for (const x of [8, 395]) drawSprite(ctx, chrome, "FactionRarity", { rect: [x, 384, 45, 45] });
  drawSprite(ctx, chrome, FACTION_SPRITE[face.house] || "Faction_Neutral");
  drawSetRarity(ctx, chrome, face);

  // Cost gem and name on the title bar.
  if (face.cost !== "" && face.cost !== undefined) {
    drawSprite(ctx, chrome, types.includes("Bane") ? "BaneSkull" : face.immortal ? "ManaCostImmortal" : "ManaCost");
    const tint = STAT_TINT[face.costCls];
    // Immortal gems are silver with dark numbers (SetDynamicState).
    if (face.immortal) outlined(ctx, String(face.cost), 36.5, 36, 40, { fill: tint || "#000", width: 0, maxWidth: 44 });
    else outlined(ctx, String(face.cost), 36.5, 36, 40, { fill: tint || "#fff", width: 5, maxWidth: 44 });
  }
  ctx.shadowColor = "#000"; ctx.shadowBlur = 5;
  outlined(ctx, face.name || "", 80, 34, 28, { align: "left", width: 0, maxWidth: 335 });
  ctx.shadowBlur = 0;

  // Type line: QUICK in orange, then the rest, the subtype, and Unique/Unlimited on the right.
  const line = typeLine(face);
  const restriction = face.unique ? "Unique" : (face.unlimited ? "Unlimited" : "");
  ctx.shadowColor = "#000"; ctx.shadowBlur = 3;
  let tx = 58;
  const maxType = restriction ? 250 : 320;
  let typeSize = 16;
  const typeText = () => `${line.words.replace(/^QUICK /, "")}${line.sub ? ` — ${line.sub}` : ""}`;
  const quick = /^QUICK /.test(line.words) ? "QUICK " : "";
  ctx.font = `bold ${typeSize}px ${FACE_FONT}`;
  while (typeSize > 10 && ctx.measureText(quick + typeText()).width > maxType) ctx.font = `bold ${--typeSize}px ${FACE_FONT}`;
  if (quick) { outlined(ctx, quick, tx, 407, typeSize, { align: "left", width: 0, fill: QUICK }); tx += ctx.measureText(quick).width; }
  outlined(ctx, typeText(), tx, 407, typeSize, { align: "left", width: 0 });
  if (restriction) outlined(ctx, restriction, 388, 407, 15, { align: "right", width: 0, italic: true });
  ctx.shadowBlur = 0;

  // Rules text, then flavour at the foot (between the stat discs on troops).
  const flavor = String(face.flavor || "").replace(/<[^>]*>/g, (t) => (/^<(p|br)/i.test(t) ? "<p>" : ""));
  const rulesBottom = troop ? 548 : (flavor ? 560 : 596);
  drawRules(ctx, chrome, textRuns(face.text), { x: 26, y: 428, w: 396, h: rulesBottom - 428 });
  if (flavor) {
    const box = troop ? { x: 92, y: 552, w: 264, h: 50 } : { x: 34, y: 562, w: 380, h: 36 };
    drawRules(ctx, chrome, textRuns(flavor), box, { size: 13, min: 7, align: "center", italic: true });
  }

  if (troop) {
    drawSprite(ctx, chrome, "AtkDefFrame");
    drawSprite(ctx, chrome, "AtkDefFrame", { flip: true });
    drawSprite(ctx, chrome, "AttackIcon");
    drawSprite(ctx, chrome, "DefenseIcon");
    outlined(ctx, String(face.attack ?? ""), 42, 578, 44, { fill: STAT_TINT[face.atkCls] || "#fff", width: 5 });
    outlined(ctx, String(face.defense ?? ""), 406, 578, 44, { fill: STAT_TINT[face.defCls] || "#fff", width: 5 });
  }
  paintCounters(ctx, face, 380, 90, 24);
}

// Resource cards are full art with the name on top and the resource band at
// the foot (BuildResourceOrChoiceCard).
function paintResourceFace(ctx, face, chrome, onArt) {
  drawArtOrGlyph(ctx, face, onArt, 0, 0, FACE_W, FACE_H);
  const top = ctx.createLinearGradient(0, 0, 0, 90);
  top.addColorStop(0, "rgba(0,0,0,.55)"); top.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = top; ctx.fillRect(0, 0, FACE_W, 90);
  const foot = ctx.createLinearGradient(0, 430, 0, FACE_H);
  foot.addColorStop(0, "rgba(0,0,0,0)"); foot.addColorStop(0.35, "rgba(0,0,0,.6)"); foot.addColorStop(1, "rgba(0,0,0,.85)");
  ctx.fillStyle = foot; ctx.fillRect(0, 430, FACE_W, FACE_H - 430);

  ctx.shadowColor = "#000"; ctx.shadowBlur = 6;
  outlined(ctx, face.name || "", FACE_W / 2, 34, 27, { width: 0, maxWidth: 400 });
  ctx.shadowBlur = 0;

  drawSliced(ctx, chrome, "ResourceTitleBar", 112, 500, 330, 30);
  const basicOrMortal = !face.immortal;
  drawSprite(ctx, chrome, basicOrMortal ? "ResourceManaCost" : "ResourceManaCostImmortal");
  const [now, max] = face.resources || [0, 0];
  if (basicOrMortal) outlined(ctx, `${now} / ${max}`, 64.5, 515, 26, { width: 4 });
  else outlined(ctx, `${now} / ${max}`, 64.5, 515, 26, { fill: "#000", width: 0 });
  ctx.shadowColor = "#000"; ctx.shadowBlur = 3;
  const line = typeLine(face);
  outlined(ctx, `${line.words}${line.sub ? ` — ${line.sub}` : ""}`, 130, 515, 15, { align: "left", width: 0, maxWidth: 250 });
  ctx.shadowBlur = 0;
  drawSetRarity(ctx, chrome, face, [397, 498, 34, 34]);
  drawRules(ctx, chrome, textRuns(face.text), { x: 30, y: 545, w: 388, h: 70 }, { size: 16, align: "center" });
  paintCounters(ctx, face, 380, 90, 24);
}

function paintCounters(ctx, face, x, y, r) {
  if (!face.counters) return;
  ctx.fillStyle = "#2c1f45"; ctx.strokeStyle = "#b89cff"; ctx.lineWidth = 3;
  ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  outlined(ctx, String(face.counters), x, y + 1, r * 1.1, { width: 0 });
}

export function paintFace(face, onArt, assets = window.HexAssets) {
  const W = 256, H = 358, [c, ctx] = canvas(W * 2, H * 2);
  ctx.scale(2, 2);   // painted at double resolution so text stays sharp up close
  ctx.beginPath(); ctx.roundRect(0, 0, W, H, face.hidden ? 16 : 8); ctx.clip();
  if (face.hidden) { paintSleeve(ctx, W, H, face.sleeve); return c; }
  // The client's own card atlases when the install has been read; otherwise
  // the drawn face below.
  const chrome = assets && assets.cardChrome ? assets.cardChrome(onArt) : null;
  if (chrome) {
    ctx.scale(W / FACE_W, H / FACE_H);
    paintClientFace(ctx, face, chrome, onArt);
    return c;
  }
  const shard = face.shard || "#8d939c";
  const g = ctx.createLinearGradient(0, 0, W * 0.4, H);
  g.addColorStop(0, shade(shard, 0.15)); g.addColorStop(1, shade(shard, -0.6));
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = shade(shard, -0.45); ctx.lineWidth = 8; ctx.strokeRect(0, 0, W, H);

  // art window
  const ax = 14, ay = 46, aw = W - 28, ah = 186;
  const img = artImage(face.art, onArt);
  if (img) {
    const s = Math.max(aw / img.width, ah / img.height);
    ctx.save(); ctx.beginPath(); ctx.roundRect(ax, ay, aw, ah, 8); ctx.clip();
    ctx.drawImage(img, ax + (aw - img.width * s) / 2, ay + (ah - img.height * s) / 2, img.width * s, img.height * s);
    ctx.restore();
  } else {
    const ag = ctx.createRadialGradient(W / 2, ay + ah * 0.4, 10, W / 2, ay + ah / 2, ah * 0.75);
    ag.addColorStop(0, shade(shard, 0.05)); ag.addColorStop(1, "#14110d");
    ctx.fillStyle = ag; ctx.beginPath(); ctx.roundRect(ax, ay, aw, ah, 8); ctx.fill();
    ctx.fillStyle = "rgba(255,255,255,.35)"; ctx.font = "64px serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(face.glyph || "", W / 2, ay + ah / 2);
  }
  ctx.strokeStyle = "rgba(0,0,0,.6)"; ctx.lineWidth = 3;
  ctx.beginPath(); ctx.roundRect(ax, ay, aw, ah, 8); ctx.stroke();

  // name and cost
  ctx.textAlign = "left"; ctx.textBaseline = "middle";
  ctx.font = "bold 23px 'Trebuchet MS', sans-serif";
  ctx.fillStyle = "#fff"; ctx.shadowColor = "#000"; ctx.shadowBlur = 4;
  const nameX = face.cost !== "" ? 50 : 16;
  ctx.fillText(ellipsize(ctx, face.name || "", W - nameX - 12), nameX, 25);
  ctx.shadowBlur = 0;
  if (face.cost !== "") {
    const cg = ctx.createRadialGradient(24, 22, 2, 27, 25, 20);
    cg.addColorStop(0, "#fff9e0"); cg.addColorStop(1, "#c8a449");
    ctx.fillStyle = cg; ctx.beginPath(); ctx.arc(27, 25, 19, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#1d1606"; ctx.font = "bold 24px sans-serif"; ctx.textAlign = "center";
    ctx.fillText(String(face.cost), 27, 26);
  }

  // Type bar, faction and rarity sit between the art and rules text.
  ctx.fillStyle = "rgba(8,8,10,.94)"; ctx.fillRect(12, 240, W - 24, 27);
  ctx.strokeStyle = "rgba(220,196,142,.8)"; ctx.lineWidth = 1; ctx.strokeRect(12, 240, W - 24, 27);
  ctx.textBaseline = "middle"; ctx.textAlign = "left"; ctx.font = "bold 17px sans-serif";
  ctx.fillStyle = face.shard || "#d9c37f"; ctx.fillText(face.faction === "Colorless" ? "◇" : "✦", 19, 253);
  ctx.fillStyle = "rgba(255,255,255,.93)"; ctx.font = "15px 'Trebuchet MS', sans-serif";
  ctx.fillText(ellipsize(ctx, face.type || "", W - 87), 43, 253);
  ctx.textAlign = "right"; ctx.fillStyle = "#f4dc8b"; ctx.font = "bold 17px sans-serif";
  ctx.fillText(face.rarity === "Legendary" ? "✦" : face.rarity === "Rare" ? "◆" : face.rarity === "Uncommon" ? "●" : "○", W - 18, 253);
  const shardColors = { Blood: "#9b4dd1", Ruby: "#e0473a", Sapphire: "#3a8ae0", Wild: "#45a852", Diamond: "#e9dca6", Colorless: "#a7a7a7" };
  let ty = 55;
  for (const [color, count] of Object.entries(face.thresholds || {})) for (let i = 0; i < count; i++) {
    ctx.fillStyle = shardColors[color] || "#aaa"; ctx.beginPath(); ctx.arc(14, ty, 6, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = "rgba(0,0,0,.7)"; ctx.lineWidth = 2; ctx.stroke(); ty += 14;
  }
  ctx.fillStyle = "rgba(8,8,10,.78)"; ctx.fillRect(14, 272, W - 28, 41);
  ctx.fillStyle = "#f1eee6"; ctx.textAlign = "left"; ctx.textBaseline = "top"; ctx.font = "11px 'Trebuchet MS', sans-serif";
  const description = String(face.text || "").replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
  const words = description.split(" "); let line = "", textY = 277;
  for (const word of words) {
    const next = line ? `${line} ${word}` : word;
    if (ctx.measureText(next).width > W - 40) { if (textY > 300) break; ctx.fillText(line, 20, textY); textY += 12; line = word; }
    else line = next;
  }
  if (line && textY <= 300) ctx.fillText(line, 20, textY);
  if (face.counters) {
    ctx.fillStyle = "#2c1f45"; ctx.strokeStyle = "#b89cff"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(W - 30, 66, 16, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    ctx.fillStyle = "#fff"; ctx.textAlign = "center"; ctx.font = "bold 18px sans-serif";
    ctx.fillText(String(face.counters), W - 30, 67);
  }

  // stats
  if (face.showStats) {
    const stat = (x, value, top, bottom, cls) => {
      const sg = ctx.createLinearGradient(0, 300, 0, 348);
      sg.addColorStop(0, cls === "hurt" ? "#ff6a6a" : top); sg.addColorStop(1, cls === "hurt" ? "#7a1515" : bottom);
      ctx.fillStyle = sg; ctx.beginPath(); ctx.arc(x, 322, 25, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = "rgba(0,0,0,.5)"; ctx.lineWidth = 3; ctx.stroke();
      ctx.fillStyle = cls === "buffed" ? "#b6ff9a" : "#fff"; ctx.font = "bold 28px sans-serif"; ctx.textAlign = "center";
      ctx.shadowColor = "#000"; ctx.shadowBlur = 3;
      ctx.fillText(String(value), x, 323);
      ctx.shadowBlur = 0;
    };
    stat(36, face.attack, "#ff8a5b", "#a6321b", face.atkCls);
    stat(W - 36, face.defense, "#7dc6ff", "#24609c", face.defCls);
  }
  return c;
}
