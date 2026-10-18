// 3D battle board drawn with three.js.
//
// app.js keeps the game state and the HUD (hands, champions, piles, prompts).
// This module draws the arena, both warzones and the chain, and reports clicks
// and hovers on cards by session card uid. Card faces arrive as plain
// descriptors from app.js and are painted onto canvas textures here.

import * as THREE from "./vendor/three.module.min.js";

const CARD_W = 1.2, CARD_H = 1.68, CARD_T = 0.03;
const FIELD_R = 4.3, RING_R = 6.5, RING_Y = 0.12;
const ROW_Z = 2.2, CHAIN_Y = 0.6, REST_Y = 0.14;

// Same palette as the CSS gameboards in style.css.
const THEMES = {
  basic: { outer: "#3c4a43", outer2: "#161d1b", stone: "#8f948a", stoneDark: "#4a4f49", field: "#5d8a31", field2: "#2c4a17", ground: "grass", sigil: null, arc: "#f2c94c", fire: "#ff9a2e" },
  forest: { outer: "#3b4a1d", outer2: "#10160a", stone: "#5a4a30", stoneDark: "#2a2214", field: "#6b5527", field2: "#2e2410", ground: "earth", sigil: "#ffcf5a", arc: "#ffd56b", fire: "#ffb347" },
  frostring: { outer: "#8fb6cc", outer2: "#1d3446", stone: "#c9dde8", stoneDark: "#6f8ea3", field: "#e4f2fa", field2: "#9cc0d6", ground: "snow", sigil: "#8fe3ff", arc: "#9fe8ff", fire: "#74d8ff" },
  badlands: { outer: "#7a3d1f", outer2: "#2a1208", stone: "#a8683e", stoneDark: "#5e3218", field: "#c9925a", field2: "#8a5530", ground: "sand", sigil: null, arc: "#ffd27a", fire: "#ff6a2a" },
  sanctum: { outer: "#2a1424", outer2: "#090408", stone: "#54334a", stoneDark: "#24121f", field: "#3a1428", field2: "#14060e", ground: "earth", sigil: "#ff3b5c", arc: "#ff5470", fire: "#ff2d55" },
  coastline: { outer: "#1f6382", outer2: "#082233", stone: "#d8c7a0", stoneDark: "#9a865e", field: "#79a95e", field2: "#3d6630", ground: "grass", sigil: null, arc: "#fff1b8", fire: "#ffcf6a" },
};

const SLEEVES = {
  classic: ["#1d2c55", "#101a3a", "#c9a24c"], blood: ["#6d2a8f", "#2a0d3b", "#c88cf0"],
  ruby: ["#b83224", "#3d0b06", "#ffb38a"], sapphire: ["#2b6fbf", "#0a1d3b", "#9ad1ff"],
  wild: ["#3c8f3a", "#0d2a0c", "#b8f08c"], diamond: ["#f6f0d4", "#9a8a55", "#fffbe9"],
  cosmic: ["#3a1670", "#050a2b", "#e6c8ff"],
};

const GLOW = { playable: "#7cf0ff", selectable: "#ffe066", selected: "#ff4fd8", "chosen-attacker": "#ff5a3c",
  attacking: "#ff5a3c", blocking: "#7ad3ff", top: "#ffd27a" };

const AXE = "M15 3h2v27h-2zM17 7c5 0 9-2 11-4 1 5 1 13-1 18-2-2-6-3.5-10-3.5zM15 7C10 7 6 5 4 3c-1 5-1 13 1 18 2-2 6-3.5 10-3.5z";
const ROOTS = "M80 80C72 72 64 67 54 59M62 65C58 57 57 50 51 42M54 59C46 57 39 53 30 51M45 56C41 62 35 65 28 71M51 42C49 36 45 31 40 25M51 42C55 36 55 30 59 23M30 51C25 47 21 47 15 44M28 71C24 76 19 77 14 82";

/* ------------------------------------------------------------------ */
/* Canvas painting                                                     */
/* ------------------------------------------------------------------ */

function canvas(w, h = w) {
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  return [c, c.getContext("2d")];
}

function texture(c, repeat) {
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 4;
  if (repeat) { t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(repeat, repeat); }
  return t;
}

function shade(hex, amount) {
  // amount > 0 lightens towards white, < 0 darkens towards black
  const c = new THREE.Color(hex);
  return "#" + c.lerp(new THREE.Color(amount > 0 ? "#ffffff" : "#000000"), Math.abs(amount)).getHexString();
}

function speckle(ctx, w, h, amount) {
  const img = ctx.getImageData(0, 0, w, h), d = img.data;
  for (let i = 0; i < d.length; i += 4) {
    const n = (Math.random() - 0.5) * amount;
    d[i] += n; d[i + 1] += n; d[i + 2] += n;
  }
  ctx.putImageData(img, 0, 0);
}

function groundTexture(theme) {
  const [c, ctx] = canvas(512);
  ctx.fillStyle = theme.outer;
  ctx.fillRect(0, 0, 512, 512);
  for (let i = 0; i < 900; i++) {
    ctx.fillStyle = Math.random() < 0.5 ? shade(theme.outer, -0.25) : shade(theme.outer, 0.12);
    ctx.globalAlpha = 0.25;
    ctx.beginPath();
    ctx.arc(Math.random() * 512, Math.random() * 512, 2 + Math.random() * 10, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1;
  speckle(ctx, 512, 512, 26);
  return texture(c, 6);
}

function ringTexture(theme) {
  // Stone tiles laid in courses around the ring, offset like brickwork.
  const size = 1024, [c, ctx] = canvas(size), mid = size / 2;
  const scale = mid / RING_R, rIn = FIELD_R * scale, rOut = RING_R * scale;
  ctx.fillStyle = theme.stoneDark;
  ctx.fillRect(0, 0, size, size);
  const courses = 6;
  for (let k = 0; k < courses; k++) {
    const r0 = rIn + (rOut - rIn) * k / courses, r1 = rIn + (rOut - rIn) * (k + 1) / courses;
    const tiles = Math.round(2 * Math.PI * r1 / 46);
    const off = (k % 2) * Math.PI / tiles;
    for (let t = 0; t < tiles; t++) {
      const a0 = off + t * 2 * Math.PI / tiles, a1 = a0 + 2 * Math.PI / tiles;
      ctx.beginPath();
      ctx.arc(mid, mid, r1 - 1.5, a0 + 0.004, a1 - 0.004);
      ctx.arc(mid, mid, r0 + 1.5, a1 - 0.004, a0 + 0.004, true);
      ctx.closePath();
      const g = ctx.createRadialGradient(mid, mid, r0, mid, mid, r1);
      const base = shade(theme.stone, (Math.random() - 0.5) * 0.25);
      g.addColorStop(0, shade(base, -0.12));
      g.addColorStop(1, base);
      ctx.fillStyle = g;
      ctx.fill();
    }
  }
  speckle(ctx, size, size, 30);
  return texture(c);
}

function fieldTexture(theme) {
  const size = 1024, [c, ctx] = canvas(size), mid = size / 2;
  const g = ctx.createRadialGradient(mid, mid * 0.95, 0, mid, mid, mid);
  g.addColorStop(0, theme.field);
  g.addColorStop(1, theme.field2);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  if (theme.ground === "grass") {
    ctx.lineCap = "round";
    for (let i = 0; i < 26000; i++) {
      const x = Math.random() * size, y = Math.random() * size;
      const a = -Math.PI / 2 + (Math.random() - 0.5) * 0.7, len = 5 + Math.random() * 9;
      ctx.strokeStyle = shade(theme.field, (Math.random() - 0.45) * 0.5);
      ctx.globalAlpha = 0.35;
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x + Math.cos(a) * len, y + Math.sin(a) * len);
      ctx.stroke();
    }
  } else {
    for (let i = 0; i < 2600; i++) {
      ctx.fillStyle = shade(theme.field, (Math.random() - 0.5) * 0.5);
      ctx.globalAlpha = 0.3;
      ctx.beginPath();
      ctx.ellipse(Math.random() * size, Math.random() * size, 1 + Math.random() * 6, 1 + Math.random() * 4, Math.random() * 3, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  ctx.globalAlpha = 1;
  speckle(ctx, size, size, 22);
  const v = ctx.createRadialGradient(mid, mid, mid * 0.6, mid, mid, mid);   // darken towards the wall
  v.addColorStop(0, "rgba(0,0,0,0)");
  v.addColorStop(1, "rgba(0,0,0,.45)");
  ctx.fillStyle = v;
  ctx.fillRect(0, 0, size, size);
  return texture(c);
}

function sigilTexture(color) {
  const size = 1024, [c, ctx] = canvas(size), k = size / 200;
  ctx.strokeStyle = color;
  ctx.lineCap = "round";
  ctx.shadowColor = color;
  ctx.shadowBlur = 18;
  const roots = new Path2D(ROOTS);
  ctx.save();
  ctx.scale(k, k);
  ctx.lineWidth = 1.6;
  for (let i = 0; i < 4; i++) {
    ctx.save();
    ctx.translate(100, 100); ctx.rotate(i * Math.PI / 2); ctx.translate(-100, -100);
    ctx.stroke(roots);
    ctx.restore();
  }
  for (const [r, w] of [[97, 1.4], [93, 0.7], [27, 1.2]]) {
    ctx.lineWidth = w;
    ctx.beginPath(); ctx.arc(100, 100, r, 0, Math.PI * 2); ctx.stroke();
  }
  ctx.restore();
  return texture(c);
}

function coinTexture(combat) {
  const size = 256, [c, ctx] = canvas(size), mid = size / 2;
  const g = ctx.createRadialGradient(mid * 0.8, mid * 0.7, 10, mid, mid, mid);
  g.addColorStop(0, "#f6d8a8"); g.addColorStop(0.6, "#b5773d"); g.addColorStop(1, "#6b3f19");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  ctx.strokeStyle = "#e8c48d"; ctx.lineWidth = 10;
  ctx.beginPath(); ctx.arc(mid, mid, mid - 8, 0, Math.PI * 2); ctx.stroke();
  ctx.fillStyle = "#fff4dc";
  ctx.shadowColor = "rgba(0,0,0,.5)"; ctx.shadowBlur = 6; ctx.shadowOffsetY = 3;
  if (combat) {
    ctx.save(); ctx.translate(mid - 72, mid - 72); ctx.scale(4.5, 4.5); ctx.fill(new Path2D(AXE)); ctx.restore();
  } else {
    ctx.beginPath();
    for (let i = 0; i < 10; i++) {
      const r = i % 2 ? 30 : 72, a = -Math.PI / 2 + i * Math.PI / 5;
      ctx.lineTo(mid + Math.cos(a) * r, mid + Math.sin(a) * r);
    }
    ctx.closePath(); ctx.fill();
  }
  return texture(c);
}

function softTexture(kind) {
  const size = 128, [c, ctx] = canvas(size), mid = size / 2;
  if (kind === "spark") {
    const g = ctx.createRadialGradient(mid, mid, 0, mid, mid, mid);
    g.addColorStop(0, "rgba(255,255,255,1)"); g.addColorStop(0.3, "rgba(255,255,255,.6)"); g.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = g; ctx.fillRect(0, 0, size, size);
  } else {   // card outline glow
    ctx.shadowColor = "#fff"; ctx.shadowBlur = 16;
    ctx.strokeStyle = "#fff"; ctx.lineWidth = 7;
    ctx.beginPath(); ctx.roundRect(18, 18, size - 36, size - 36, 10); ctx.stroke();
  }
  return texture(c);
}

function ellipsize(ctx, text, width) {
  if (ctx.measureText(text).width <= width) return text;
  while (text.length > 1 && ctx.measureText(text + "…").width > width) text = text.slice(0, -1);
  return text + "…";
}

const artImages = new Map();
function artImage(url, onLoad) {
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

function paintSleeve(ctx, W, H, name) {
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

function paintFace(face, onArt) {
  const W = 256, H = 358, [c, ctx] = canvas(W * 2, H * 2);
  ctx.scale(2, 2);   // painted at double resolution so text stays sharp up close
  ctx.beginPath(); ctx.roundRect(0, 0, W, H, 16); ctx.clip();
  if (face.hidden) { paintSleeve(ctx, W, H, face.sleeve); return c; }
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

  // type line and keywords
  ctx.textAlign = "left"; ctx.fillStyle = "rgba(255,255,255,.9)"; ctx.font = "16px 'Trebuchet MS', sans-serif";
  ctx.fillText(ellipsize(ctx, face.type || "", W - 30), 16, 252);
  ctx.font = "bold 14px 'Trebuchet MS', sans-serif";
  let kx = 14;
  for (const kw of face.keywords || []) {
    const w = ctx.measureText(kw).width + 14;
    if (kx + w > W - 14) break;
    ctx.fillStyle = "rgba(0,0,0,.6)"; ctx.beginPath(); ctx.roundRect(kx, 268, w, 22, 8); ctx.fill();
    ctx.fillStyle = "#ffe9a8"; ctx.fillText(kw, kx + 7, 280);
    kx += w + 5;
  }
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

/* ------------------------------------------------------------------ */
/* Fire                                                                */
/* ------------------------------------------------------------------ */

class Fire {
  // Additive particles rising from a point, or from along a line segment.
  constructor(color, { count = 70, spread = 0.2, height = 1.1, size = 0.45, from, to } = {}) {
    this.count = count; this.spread = spread; this.height = height; this.from = from; this.to = to;
    this.color = new THREE.Color(color);
    this.life = new Float32Array(count).map(() => Math.random());
    this.speed = new Float32Array(count).map(() => 0.6 + Math.random() * 0.8);
    this.base = new Float32Array(count * 3);
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(count * 3), 3));
    geo.setAttribute("color", new THREE.BufferAttribute(new Float32Array(count * 3), 3));
    this.points = new THREE.Points(geo, new THREE.PointsMaterial({
      size, map: Fire.sprite, vertexColors: true, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    }));
    this.points.frustumCulled = false;
    for (let i = 0; i < count; i++) this.respawn(i);
  }
  respawn(i) {
    const b = this.base;
    if (this.from) {
      const t = Math.random();
      b[i * 3] = this.from.x + (this.to.x - this.from.x) * t;
      b[i * 3 + 2] = this.from.z + (this.to.z - this.from.z) * t + (Math.random() - 0.5) * 0.1;
    } else {
      const a = Math.random() * Math.PI * 2, r = Math.sqrt(Math.random()) * this.spread;
      b[i * 3] = Math.cos(a) * r; b[i * 3 + 2] = Math.sin(a) * r;
    }
    b[i * 3 + 1] = 0;
  }
  update(dt, t) {
    const pos = this.points.geometry.attributes.position, col = this.points.geometry.attributes.color;
    const hot = new THREE.Color("#fff3c4"), c = new THREE.Color();
    for (let i = 0; i < this.count; i++) {
      this.life[i] += dt * this.speed[i];
      if (this.life[i] >= 1) { this.life[i] = 0; this.respawn(i); }
      const l = this.life[i];
      const sway = Math.sin(t * 3 + i) * 0.05 * l;
      pos.setXYZ(i, this.base[i * 3] * (1 - l * 0.7) + sway, l * this.height, this.base[i * 3 + 2] * (1 - l * 0.7));
      c.copy(hot).lerp(this.color, Math.min(1, l * 1.8)).multiplyScalar((1 - l) * 0.9);
      col.setXYZ(i, c.r, c.g, c.b);
    }
    pos.needsUpdate = true; col.needsUpdate = true;
  }
}

/* ------------------------------------------------------------------ */
/* Board                                                               */
/* ------------------------------------------------------------------ */

class Board3D {
  static create(container, callbacks) {
    try {
      return new Board3D(container, callbacks);
    } catch (err) {
      console.warn("3D board unavailable, using the flat board", err);
      return null;
    }
  }

  constructor(container, callbacks) {
    this.container = container;
    this.callbacks = callbacks;
    this.cards = new Map();      // uid -> { mesh, face, glow, target }
    this.fires = [];
    this.lights = [];
    this.hovered = null;
    this.combat = false;
    this.clock = new THREE.Clock();

    const renderer = this.renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    container.appendChild(renderer.domElement);

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(36, 1, 0.1, 80);
    this.raycaster = new THREE.Raycaster();
    Fire.sprite = Fire.sprite || softTexture("spark");
    this.glowTex = softTexture("glow");

    const hemi = new THREE.HemisphereLight("#fff6e6", "#1a1a22", 1.1);
    const sun = this.sun = new THREE.DirectionalLight("#fff1dc", 2.2);
    sun.position.set(-4, 12, 6);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    Object.assign(sun.shadow.camera, { left: -8, right: 8, top: 8, bottom: -8, near: 1, far: 30 });
    sun.shadow.bias = -0.0005;
    this.scene.add(hemi, sun);

    this.arena = new THREE.Group();
    this.scene.add(this.arena);
    this.cardGroup = new THREE.Group();
    this.scene.add(this.cardGroup);

    this.cardGeo = new THREE.BoxGeometry(CARD_W, CARD_T, CARD_H);
    this.glowGeo = new THREE.PlaneGeometry(CARD_W * 1.32, CARD_H * 1.26);

    this.burstSprite = new THREE.Sprite(new THREE.SpriteMaterial({
      map: Fire.sprite, color: "#ffe9b0", transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, opacity: 0 }));
    this.burstSprite.position.set(0, CHAIN_Y + 0.4, 0);
    this.burstLight = new THREE.PointLight("#ffe2a0", 0, 0, 2);
    this.burstLight.position.set(0, 1.4, 0);
    this.burstAge = 9;
    this.scene.add(this.burstSprite, this.burstLight);

    const el = renderer.domElement;
    el.addEventListener("pointermove", (e) => this.pointer(e, false));
    el.addEventListener("pointerleave", () => this.hover(null));
    el.addEventListener("click", (e) => this.pointer(e, true));
    new ResizeObserver(() => this.resize()).observe(container);
    this.resize();
    this.setTheme("basic");
    renderer.setAnimationLoop(() => this.frame());
  }

  resize() {
    const w = this.container.clientWidth || 1, h = this.container.clientHeight || 1;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    // Keep the whole field in view on narrow screens: pull back as the aspect narrows.
    const back = Math.max(1, 1.55 / this.camera.aspect);
    this.camera.position.set(0, 11.6 * back, 7.4 * back);
    this.camera.lookAt(0, 0, 0.55);
    this.camera.updateProjectionMatrix();
  }

  /* -- arena -------------------------------------------------------- */

  setTheme(name) {
    if (name === this.themeName) return;
    this.themeName = name;
    const theme = this.theme = THEMES[name] || THEMES.basic;
    for (const child of [...this.arena.children]) {
      this.arena.remove(child);
      child.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) [].concat(o.material).forEach((m) => { if (m.map) m.map.dispose(); m.dispose(); }); });
    }
    this.fires = []; this.lights = [];
    this.scene.background = new THREE.Color(theme.outer2);
    this.scene.fog = new THREE.Fog(theme.outer2, 16, 30);

    const ground = new THREE.Mesh(new THREE.PlaneGeometry(60, 60),
      new THREE.MeshStandardMaterial({ map: groundTexture(theme), roughness: 1 }));
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -0.02;
    ground.receiveShadow = true;

    const stone = new THREE.MeshStandardMaterial({ map: ringTexture(theme), roughness: 0.92 });
    const ring = new THREE.Mesh(new THREE.RingGeometry(FIELD_R, RING_R, 160, 1), stone);
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = RING_Y;
    ring.receiveShadow = true;
    const wallMat = new THREE.MeshStandardMaterial({ color: theme.stoneDark, roughness: 0.95, side: THREE.DoubleSide });
    const inner = new THREE.Mesh(new THREE.CylinderGeometry(FIELD_R, FIELD_R, RING_Y, 160, 1, true), wallMat);
    inner.position.y = RING_Y / 2;
    const outer = new THREE.Mesh(new THREE.CylinderGeometry(RING_R, RING_R + 0.18, RING_Y + 0.02, 160, 1, true), wallMat);
    outer.position.y = RING_Y / 2 - 0.01;

    const field = new THREE.Mesh(new THREE.CircleGeometry(FIELD_R, 160),
      new THREE.MeshStandardMaterial({ map: fieldTexture(theme), roughness: 0.95 }));
    field.rotation.x = -Math.PI / 2;
    field.receiveShadow = true;
    this.arena.add(ground, ring, inner, outer, field);

    if (theme.sigil) {
      this.sigil = new THREE.Mesh(new THREE.CircleGeometry(RING_R * 0.93, 96), new THREE.MeshBasicMaterial({
        map: sigilTexture(theme.sigil), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
      this.sigil.rotation.x = -Math.PI / 2;
      this.sigil.position.y = RING_Y + 0.004;
      this.arena.add(this.sigil);
    } else {
      this.sigil = null;
    }

    // Golden arcs on the near side of the medallion.
    for (const r of [1.65, 1.85, 2.05]) {
      const holder = new THREE.Group();
      for (const [tube, opacity] of [[0.02, 0.75], [0.07, 0.1]]) {
        const arc = new THREE.Mesh(new THREE.TorusGeometry(r, tube, 8, 120, Math.PI * 0.86),
          new THREE.MeshBasicMaterial({ color: theme.arc, transparent: true, opacity, depthWrite: false, blending: THREE.AdditiveBlending }));
        arc.rotation.z = Math.PI * 0.07;
        holder.add(arc);
      }
      holder.rotation.x = Math.PI / 2;
      holder.position.y = 0.015;
      this.arena.add(holder);
    }

    // Medallion: an octagonal stone plate with a bronze coin.
    const plate = new THREE.Mesh(new THREE.CylinderGeometry(1.0, 1.08, 0.2, 8),
      new THREE.MeshStandardMaterial({ map: ringTexture({ ...theme, stone: "#a9a89e", stoneDark: "#77766e" }), roughness: 0.9 }));
    plate.rotation.y = Math.PI / 8;
    plate.position.y = 0.1;
    plate.castShadow = plate.receiveShadow = true;
    const bronze = new THREE.MeshStandardMaterial({ color: "#a86c35", metalness: 0.75, roughness: 0.35 });
    this.coinTop = new THREE.MeshStandardMaterial({ map: coinTexture(this.combat), metalness: 0.6, roughness: 0.4 });
    const coin = new THREE.Mesh(new THREE.CylinderGeometry(0.52, 0.52, 0.07, 64), [bronze, this.coinTop, bronze]);
    coin.position.y = 0.235;
    coin.castShadow = true;
    this.arena.add(plate, coin);

    // Braziers around the arena, each a real light source.
    for (const [x, z] of [[-7.5, -2.4], [7.5, -2.4], [-7.2, 2.4], [7.2, 2.4]]) {
      const bowl = new THREE.Mesh(new THREE.CylinderGeometry(0.42, 0.24, 0.4, 24, 1, true),
        new THREE.MeshStandardMaterial({ color: "#2b2522", roughness: 0.6, metalness: 0.4, side: THREE.DoubleSide }));
      bowl.position.set(x, 0.55, z);
      const stand = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.2, 0.4, 12),
        new THREE.MeshStandardMaterial({ color: "#1d1917", roughness: 0.7 }));
      stand.position.set(x, 0.2, z);
      const fire = new Fire(theme.fire);
      fire.points.position.set(x, 0.62, z);
      const light = new THREE.PointLight(theme.fire, 14, 0, 2);
      light.position.set(x, 1.3, z);
      this.fires.push(fire); this.lights.push(light);
      this.arena.add(bowl, stand, fire.points, light);
    }

    // Combat fire across the board.
    this.combatFires = [
      new Fire("#ff7a1a", { count: 150, height: 0.5, size: 0.32, from: { x: -FIELD_R + 0.35, z: 0 }, to: { x: -1.25, z: 0 } }),
      new Fire("#ff7a1a", { count: 150, height: 0.5, size: 0.32, from: { x: 1.25, z: 0 }, to: { x: FIELD_R - 0.35, z: 0 } }),
    ];
    for (const f of this.combatFires) { f.points.visible = this.combat; this.arena.add(f.points); }
  }

  setCombat(combat) {
    if (combat === this.combat) return;
    this.combat = combat;
    if (this.coinTop) { this.coinTop.map.dispose(); this.coinTop.map = coinTexture(combat); this.coinTop.needsUpdate = true; }
    for (const f of this.combatFires || []) f.points.visible = combat;
  }

  /* -- cards -------------------------------------------------------- */

  slot(entry) {
    // World position for a card: warzone rows on either side, chain across the middle.
    const n = entry.count, spacing = Math.min(1.42, (entry.zone === "chain" ? 7 : 8.2) / Math.max(n, 1));
    const x = (entry.index - (n - 1) / 2) * spacing;
    if (entry.zone === "chain") return new THREE.Vector3(x, CHAIN_Y + (entry.top ? 0.12 : 0), 0);
    const side = entry.zone === "mine" ? 1 : -1;
    const lunge = entry.highlight === "attacking" || entry.highlight === "chosen-attacker" ? -side * 0.55 : 0;
    return new THREE.Vector3(x, REST_Y, side * ROW_Z + lunge);
  }

  update(entries) {
    const seen = new Set();
    for (const entry of entries) {
      const key = `${entry.zone}:${entry.uid}`;
      seen.add(key);
      let rec = this.cards.get(key);
      if (!rec) {
        rec = this.makeCard(entry);
        this.cards.set(key, rec);
      }
      const faceKey = JSON.stringify(entry.face);
      if (faceKey !== rec.faceKey) this.paint(rec, entry.face, faceKey);
      rec.entry = entry;
      rec.target = this.slot(entry);
      if (rec.fresh) { rec.mesh.position.copy(rec.target); rec.fresh = false; }
      rec.mesh.visible = !entry.hidden;
      const glow = GLOW[entry.highlight] || (entry.top ? GLOW.top : null);
      rec.glow.visible = !!glow;
      if (glow) rec.glow.material.color.set(glow);
      rec.mesh.material[2].color.setScalar(entry.tapped ? 0.62 : 1);
    }
    for (const [key, rec] of this.cards) {
      if (seen.has(key)) continue;
      this.cardGroup.remove(rec.mesh);
      rec.mesh.material.forEach((m) => { if (m.map) m.map.dispose(); m.dispose(); });
      rec.glow.material.dispose();
      this.cards.delete(key);
      if (this.hovered === rec) this.hovered = null;
    }
  }

  makeCard(entry) {
    const edge = new THREE.MeshStandardMaterial({ color: "#2a2620", roughness: 0.7 });
    const top = new THREE.MeshStandardMaterial({ roughness: 0.55 });
    const back = new THREE.MeshStandardMaterial({ roughness: 0.6, color: "#222" });
    // BoxGeometry faces: +x, -x, +y (face), -y (back), +z, -z
    const mesh = new THREE.Mesh(this.cardGeo, [edge, edge, top, back, edge, edge]);
    mesh.castShadow = true;
    mesh.userData.uid = entry.uid;
    const glow = new THREE.Mesh(this.glowGeo, new THREE.MeshBasicMaterial({
      map: this.glowTex, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
    glow.rotation.x = -Math.PI / 2;
    glow.position.y = -CARD_T;
    glow.visible = false;
    mesh.add(glow);
    this.cardGroup.add(mesh);
    return { mesh, glow, fresh: true, faceKey: null };
  }

  paint(rec, face, faceKey) {
    rec.faceKey = faceKey;
    const repaint = () => { if (rec.faceKey === faceKey) this.paint(rec, face, faceKey + " "); };
    const top = rec.mesh.material[2];
    if (top.map) top.map.dispose();
    top.map = texture(paintFace(face, repaint));
    top.needsUpdate = true;
    rec.mesh.material.slice(0, 2).concat(rec.mesh.material.slice(4)).forEach((m) =>
      m.color.set(face.hidden ? (SLEEVES[face.sleeve] || SLEEVES.classic)[1] : shade(face.shard || "#8d939c", -0.55)));
  }

  /* -- interaction -------------------------------------------------- */

  pick(e) {
    const r = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    this.raycaster.setFromCamera(ndc, this.camera);
    const hits = this.raycaster.intersectObjects([...this.cards.values()].filter((c) => c.mesh.visible).map((c) => c.mesh), false);
    if (!hits.length) return null;
    return [...this.cards.values()].find((c) => c.mesh === hits[0].object) || null;
  }

  pointer(e, click) {
    const rec = this.pick(e);
    if (click) { if (rec) this.callbacks.onClick(rec.entry.uid); return; }
    this.hover(rec);
  }

  hover(rec) {
    if (rec === this.hovered) return;
    this.hovered = rec;
    this.renderer.domElement.style.cursor = rec ? "pointer" : "";
    this.callbacks.onHover(rec ? rec.entry.uid : null);
  }

  /* -- geometry for the HTML layer ---------------------------------- */

  project(v) {
    const r = this.renderer.domElement.getBoundingClientRect();
    const p = v.clone().project(this.camera);
    return { x: r.left + (p.x + 1) / 2 * r.width, y: r.top + (1 - p.y) / 2 * r.height };
  }

  centreScreen() {
    return this.project(new THREE.Vector3(0, CHAIN_Y, 0));
  }

  screenRect(uid) {
    // Where a card will rest, on screen, so HTML flights can land on it.
    const rec = [...this.cards.values()].find((c) => c.entry.uid === uid);
    if (!rec) return null;
    const t = rec.target, xs = [], ys = [];
    for (const [dx, dz] of [[-0.5, -0.5], [0.5, -0.5], [-0.5, 0.5], [0.5, 0.5]]) {
      const p = this.project(new THREE.Vector3(t.x + dx * CARD_W, t.y, t.z + dz * CARD_H));
      xs.push(p.x); ys.push(p.y);
    }
    const left = Math.min(...xs), top = Math.min(...ys);
    return { left, top, width: Math.max(...xs) - left, height: Math.max(...ys) - top };
  }

  burst() {
    this.burstAge = 0;
  }

  /* -- animation ---------------------------------------------------- */

  frame() {
    const dt = Math.min(this.clock.getDelta(), 0.05), t = this.clock.elapsedTime;
    const k = 1 - Math.pow(0.0015, dt);   // frame-rate independent easing
    for (const rec of this.cards.values()) {
      const m = rec.mesh, e = rec.entry, target = rec.target.clone();
      const hovered = rec === this.hovered;
      if (hovered) target.y += 0.35;
      if (e.zone === "chain") target.y += Math.sin(t * 2 + e.index) * 0.04;
      m.position.lerp(target, k);
      const rotY = e.tapped ? -0.32 : 0;
      m.rotation.y += (rotY - m.rotation.y) * k;
      // tilt a little towards the camera so faces read better
      const tilt = e.zone === "chain" ? 0.55 : (hovered ? 0.3 : 0.1);
      m.rotation.x += (tilt - m.rotation.x) * k;
      const s = hovered ? 1.12 : 1;
      m.scale.setScalar(m.scale.x + (s - m.scale.x) * k);
      if (rec.glow.visible) rec.glow.material.opacity = 0.75 + Math.sin(t * 5) * 0.25;
    }
    for (const f of this.fires) f.update(dt, t);
    for (const f of this.combatFires || []) if (f.points.visible) f.update(dt, t);
    this.lights.forEach((l, i) => { l.intensity = 12 + Math.sin(t * 9 + i * 2) * 2 + Math.sin(t * 23 + i) * 1.5; });
    if (this.sigil) this.sigil.material.opacity = 0.65 + Math.sin(t * 1.2) * 0.25;
    this.burstAge += dt;
    const b = Math.max(0, 1 - this.burstAge / 0.7);
    this.burstSprite.material.opacity = b * 0.8;
    this.burstSprite.scale.setScalar(1 + (1 - b) * 5);
    this.burstLight.intensity = b * 25;
    this.renderer.render(this.scene, this.camera);
  }
}

window.Board3D = Board3D;
