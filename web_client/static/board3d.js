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
const SLEEVES = {
  classic: ["#1d2c55", "#101a3a", "#c9a24c"], blood: ["#6d2a8f", "#2a0d3b", "#c88cf0"],
  ruby: ["#b83224", "#3d0b06", "#ffb38a"], sapphire: ["#2b6fbf", "#0a1d3b", "#9ad1ff"],
  wild: ["#3c8f3a", "#0d2a0c", "#b8f08c"], diamond: ["#f6f0d4", "#9a8a55", "#fffbe9"],
  cosmic: ["#3a1670", "#050a2b", "#e6c8ff"],
};

const GLOW = { playable: "#7cf0ff", selectable: "#ffe066", selected: "#ffb020", "chosen-attacker": "#ff5a3c",
  attacking: "#ff5a3c", blocking: "#7ad3ff", top: "#ffd27a" };

// Arc colours: a green attack, a red block, a gold target (chain targets and picked targets).
const ARC = { attack: "#42ff6e", block: "#ff3d3d", target: "#ffb020" };
const TAP_ATTACK = Math.PI / 2;   // an attacker turns 90 degrees anticlockwise, seen from above

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
// Painted hand and preview faces as image URLs (Board3D.cardImage), kept to a bound.
const faceImages = new Map();
const FACE_IMAGE_LIMIT = 400;
let chromeAsked = false;   // the card frames' readiness callback is registered once
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

// Pixels decoded from the client install as a texture. Cached per image, so
// rebuilding the arena reuses the upload.
const rgbaTextures = new WeakMap();
function rgbaTexture(pixels) {
  if (!rgbaTextures.has(pixels)) {
    // Textures restored from storage arrive as bitmaps; decoded ones as RGBA.
    let source = pixels.bitmap;
    if (!source) {
      const [c, ctx] = canvas(pixels.width, pixels.height);
      ctx.putImageData(new ImageData(pixels.rgba, pixels.width, pixels.height), 0, 0);
      source = c;
    }
    const texture = new THREE.CanvasTexture(source);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = 4;
    rgbaTextures.set(pixels, texture);
  }
  return rgbaTextures.get(pixels);
}

// The glow of a Hex _ESRA mask: its red channel is the emission (the rune lines)
// and its blue channel the sky-reflection area. Each is given its own colour, as
// the authored shader does, instead of glowing in the mask's raw red and blue.
const esraTextures = new WeakMap();
function esraGlowTexture(pixels, line, sky) {
  const key = `${line}|${sky}`;
  let byLook = esraTextures.get(pixels);
  if (!byLook) esraTextures.set(pixels, (byLook = new Map()));
  if (!byLook.has(key)) {
    const [c, ctx] = canvas(pixels.width, pixels.height);
    // Read the raw RGBA when there is one: a canvas premultiplies, which blanks the
    // colour where the mask's alpha is 0 (the centre circle).
    let src = pixels.rgba;
    if (!src) {
      ctx.drawImage(pixels.bitmap, 0, 0);
      src = ctx.getImageData(0, 0, pixels.width, pixels.height).data;
    }
    const image = ctx.createImageData(pixels.width, pixels.height), px = image.data;
    for (let i = 0; i < px.length; i += 4) {
      const e = src[i] / 255, r = src[i + 2] / 255;
      for (let k = 0; k < 3; k++) px[i + k] = Math.min(255, 255 * (e * line[k] + r * sky[k]));
      px[i + 3] = 255;
    }
    ctx.putImageData(image, 0, 0);
    const texture = new THREE.CanvasTexture(c);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = 4;
    byLook.set(key, texture);
  }
  return byLook.get(key);
}

// Unity particle systems from the battleboard, simulated on the CPU as point
// sprites. Emission, lifetime, speed, size, colour over life and the shape
// (sphere, cone, box, circle, edge) follow the authored values; curves are
// approximated by their constants and the noise and force modules are ignored.
const PARTICLE_VERT = `
  attribute float aSize;
  attribute vec4 aColor;
  uniform float uPixelScale;
  varying vec4 vColor;
  void main() {
    vColor = aColor;
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = aSize * uPixelScale / max(-mv.z, 0.01);
    gl_Position = projectionMatrix * mv;
  }`;
const PARTICLE_FRAG = `
  uniform sampler2D uMap;
  varying vec4 vColor;
  void main() {
    vec4 t = texture2D(uMap, gl_PointCoord);
    gl_FragColor = vec4(t.rgb * vColor.rgb, t.a * vColor.a);
    if (gl_FragColor.a < 0.01) discard;
  }`;
const MAX_PARTICLES = 300;
// Largest world size of one particle sprite; larger ones saturate to white.
const MAX_PARTICLE_SIZE = 2;
const PARTICLE_INTENSITY = 0.6;
// The phase dial (the PhaseLens part) shows the icon for the turn phase. phase_dial.png
// is the material's sprite sheet, DIAL_COLS x DIAL_ROWS cells, exported from the
// battleboard. The cell for each ETurnPhases value is AnimGameBoard's
// ConvertETurnPhaseToSprite; a value not listed uses cell 0.
const DIAL_COLS = 4, DIAL_ROWS = 4;
const DIAL_CELL = {
  1: 1, 5: 1, 6: 1, 7: 1, 8: 1, 9: 1,          // NotPlaying, StartGame, StartTurn, Ready, Prep, Draw
  12: 7, 13: 7,                                 // DeclareAttack (+ priority window): axe
  14: 3, 15: 3,                                 // DeclareDefense (+ priority window): shield
  16: 5, 17: 5, 18: 5,                          // first-strike and combat damage
  19: 4,                                        // SecondMainPhase
  20: 6, 21: 6, 22: 6, 24: 6,                   // EndPhase, Discard, EndTurn, EndGame
};
// Unity GameObject names of battleboard emitters that are not drawn (see the
// ?emitters console log in extractedDecor).
const SKIPPED_EMITTERS = ["Flames"];
let dialSheet = null;
// The default sprite sheet, used when the battleboard has none of its own. Boards
// draw their dial from their own PhaseLens texture, which keeps their look.
function dialTexture() {
  if (!dialSheet) {
    dialSheet = new THREE.TextureLoader().load(new URL("phase_dial.png", import.meta.url).href);
    dialSheet.colorSpace = THREE.SRGBColorSpace;
    dialSheet.anisotropy = 4;
  }
  return dialSheet;
}
// A dial's sheet wraps, so a shifted cell is read from the sheet's other cells.
function dialMap(pixels) {
  const map = pixels ? rgbaTexture(pixels) : dialTexture();
  map.wrapS = map.wrapT = THREE.RepeatWrapping;
  return map;
}

// Look presets for battleboards whose authored shader is not reproduced: the
// surface tint is scaled (dark boards), and boards with an _ESRA mask glow with a
// line colour (its emission) and a sky colour (its reflection area).
const BOARD_LOOK = {
  default: { tint: 0.18 },
  frostarena: { tint: 0.75, line: [0.5, 0.9, 1.0], sky: [0.12, 0.4, 0.6] },
  // The lava lines are the surface's own emission: its _EmissionMap, lit by the
  // material's _EmissionColor.
  badlands: { tint: 2.5, emission: true },
};
// Height above the playing surface that raised props rest at, so they do not
// z-fight with it.
const SURFACE_LIFT = 0.02;
// How far past the view the board's surface reaches when it covers the screen.
const COVER_MARGIN = 1.08;
// How far the camera moves back when a Unity battleboard is shown.
const BOARD_CAMERA_PULL = 1.1;
// Background behind the battleboard, shown until one is loaded.
const ARENA_BACKGROUND = "#0d1210";

function randomIn([lo, hi]) { return lo + Math.random() * (hi - lo); }

// Colour at t (0..1) along gradient stops; the constant colour when there are none.
function gradientAt(stops, t, fallback) {
  if (!stops || !stops.length) return fallback;
  if (t <= stops[0].t) return stops[0].rgba;
  const last = stops[stops.length - 1];
  if (t >= last.t) return last.rgba;
  for (let i = 1; i < stops.length; i++) {
    if (t <= stops[i].t) {
      const a = stops[i - 1], b = stops[i], k = (t - a.t) / Math.max(b.t - a.t, 1e-6);
      return a.rgba.map((v, j) => v + (b.rgba[j] - v) * k);
    }
  }
  return last.rgba;
}

class ParticleEmitter {
  constructor(data, scale, texture) {
    this.data = data;
    this.scale = scale;
    this.count = Math.min(MAX_PARTICLES, Math.max(8, Math.ceil(data.rate[1] * data.lifetime[1] + 16)));
    this.position = new Float32Array(this.count * 3);
    this.velocity = new Float32Array(this.count * 3);
    this.age = new Float32Array(this.count);
    this.life = new Float32Array(this.count);
    this.size = new Float32Array(this.count);
    this.color = new Float32Array(this.count * 4);
    this.next = 0;
    this.time = 0;
    this.accumulated = 0;
    this.burstDone = new Set();
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(this.position, 3).setUsage(THREE.DynamicDrawUsage));
    geometry.setAttribute("aSize", new THREE.BufferAttribute(this.size, 1).setUsage(THREE.DynamicDrawUsage));
    geometry.setAttribute("aColor", new THREE.BufferAttribute(this.color, 4).setUsage(THREE.DynamicDrawUsage));
    this.uniforms = { uMap: { value: texture }, uPixelScale: { value: 1 } };
    this.material = new THREE.ShaderMaterial({ uniforms: this.uniforms, vertexShader: PARTICLE_VERT,
      fragmentShader: PARTICLE_FRAG, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending });
    this.points = new THREE.Points(geometry, this.material);
    this.points.frustumCulled = false;
    this.points.position.fromArray(data.position);
  }

  spawn() {
    const d = this.data, i = this.next;
    this.next = (this.next + 1) % this.count;
    const dir = [0, 1, 0], at = [0, 0, 0], shape = d.shape;
    const theta = Math.random() * Math.PI * 2;
    switch (shape.type) {
      case 0: case 1: case 2: case 3: {         // sphere, hemisphere
        const v = [Math.random() * 2 - 1, Math.random() * 2 - 1, Math.random() * 2 - 1];
        const len = Math.hypot(...v) || 1;
        for (let k = 0; k < 3; k++) dir[k] = v[k] / len;
        if (shape.type === 2 || shape.type === 3) dir[1] = Math.abs(dir[1]);
        const r = shape.radius * (shape.type === 1 || shape.type === 3 ? 1 : Math.cbrt(Math.random()));
        at[0] = dir[0] * r; at[1] = dir[1] * r; at[2] = dir[2] * r;
        break;
      }
      case 4: case 7: case 8: case 9: {         // cone
        const a = (shape.angle * Math.PI / 180) * Math.random();
        dir[0] = Math.sin(a) * Math.cos(theta); dir[1] = Math.cos(a); dir[2] = Math.sin(a) * Math.sin(theta);
        const r = shape.radius * Math.sqrt(Math.random());
        at[0] = Math.cos(theta) * r; at[2] = Math.sin(theta) * r;
        break;
      }
      case 5: case 14:                          // box
        for (let k = 0; k < 3; k++) at[k] = (Math.random() - 0.5) * shape.box[k];
        break;
      case 10: {                                // circle
        const r = shape.radius * Math.sqrt(Math.random());
        at[0] = Math.cos(theta) * r; at[2] = Math.sin(theta) * r;
        break;
      }
      case 11:                                  // edge
        at[0] = (Math.random() - 0.5) * 2 * shape.radius;
        break;
      default:
        break;
    }
    const speed = randomIn(d.speed);
    const life = Math.max(randomIn(d.lifetime), 0.01);
    for (let k = 0; k < 3; k++) {
      this.position[i * 3 + k] = at[k];
      this.velocity[i * 3 + k] = dir[k] * speed;
    }
    this.age[i] = 0;
    this.life[i] = life;
    this.size[i] = Math.min(randomIn(d.size) * this.scale, MAX_PARTICLE_SIZE);
    const rgba = gradientAt(d.gradient, 0, d.startColor);
    this.color.set(rgba.map((v, k) => (k < 3 ? v * PARTICLE_INTENSITY : v)), i * 4);
  }

  // dt in seconds; pixelScale converts world size to pixels at distance 1.
  update(dt, pixelScale) {
    const d = this.data;
    this.time += dt;
    if (d.looping && this.time >= d.duration) {
      this.time %= d.duration;
      this.burstDone.clear();
    }
    if (this.time >= (d.delay || 0) && (d.looping || this.time < d.duration) && d.duration >= 0) {
      this.accumulated += randomIn(d.rate) * dt;
      while (this.accumulated >= 1) { this.accumulated -= 1; this.spawn(); }
      d.bursts.forEach((burst, index) => {
        if (this.time >= burst.time && !this.burstDone.has(index)) {
          this.burstDone.add(index);
          const n = Math.round(randomIn(burst.count));
          for (let k = 0; k < n; k++) this.spawn();
        }
      });
    }
    for (let i = 0; i < this.count; i++) {
      if (this.life[i] <= 0) continue;
      this.age[i] += dt;
      if (this.age[i] >= this.life[i]) {
        this.life[i] = 0;
        this.size[i] = 0;
        continue;
      }
      for (let k = 0; k < 3; k++) this.position[i * 3 + k] += this.velocity[i * 3 + k] * dt;
      const rgba = gradientAt(d.gradient, this.age[i] / this.life[i], d.startColor);
      this.color.set(rgba.map((v, k) => (k < 3 ? v * PARTICLE_INTENSITY : v)), i * 4);
    }
    this.points.geometry.attributes.position.needsUpdate = true;
    this.points.geometry.attributes.aSize.needsUpdate = true;
    this.points.geometry.attributes.aColor.needsUpdate = true;
    this.uniforms.uPixelScale.value = pixelScale;
  }
}

// The battleboard as three.js meshes: its playing surface and props, scaled so
// the surface spans the arena ring and centred on it.
// Board-space height of the view's top and bottom edges, along the centre
// line of the board, at ground level. Used to frame the board by its daises.
function groundZAt(camera, ndcY) {
  camera.updateMatrixWorld();
  const v = new THREE.Vector3();
  let lo = -200, hi = 200;          // projected height falls as z grows (towards the camera)
  for (let i = 0; i < 60; i++) {
    const mid = (lo + hi) / 2;
    if (v.set(0, 0, mid).project(camera).y > ndcY) lo = mid; else hi = mid;
  }
  return (lo + hi) / 2;
}

// The width and depth of the ground the view shows: the four corners of the screen
// are cast from the camera onto the y = 0 plane. Null when no corner meets the ground.
function groundFootprint(camera) {
  camera.updateMatrixWorld();
  const origin = camera.position;
  const xs = [], zs = [];
  for (const [x, y] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
    const dir = new THREE.Vector3(x, y, 0.5).unproject(camera).sub(origin).normalize();
    if (dir.y >= 0) continue;
    const t = -origin.y / dir.y;
    xs.push(origin.x + dir.x * t);
    zs.push(origin.z + dir.z * t);
  }
  if (!xs.length) return null;
  return { x: Math.max(...xs) - Math.min(...xs), z: Math.max(...zs) - Math.min(...zs) };
}

function extractedDecor(board, camera) {
  const group = new THREE.Group();
  const dials = [];   // the phase dial's sprite-sheet maps, moved by Board3D.setPhase
  const [lo, hi] = board.surface;
  const span = Math.max(hi[0] - lo[0], hi[2] - lo[2]) || 1;
  // The board covers the view: its surface is scaled until it spans the ground the
  // screen shows, so no space beyond the board is visible at the screen's edges.
  const footprint = camera ? groundFootprint(camera) : null;
  // A margin keeps the surface's edge out of the corners of the view.
  const scale = footprint ? COVER_MARGIN * Math.max(footprint.x / (hi[0] - lo[0] || 1), footprint.z / (hi[2] - lo[2] || 1)) : (RING_R * 2) / span;
  group.scale.setScalar(scale);
  // The central dais (the phase dial) goes to the centre of the view, so it is
  // centred horizontally even where the surface is not symmetric about it. The
  // surface's middle is the fallback when a board has no dial. Height stays as
  // authored: the playing surface is at Unity y = 0 even where pits and props
  // reach below or above it.
  const dial = board.parts.find((part) => part.name === "PhaseLens");
  const centreX = dial ? (dial.box[0][0] + dial.box[1][0]) / 2 : (lo[0] + hi[0]) / 2;
  group.position.set(-centreX * scale, 0, -(lo[2] + hi[2]) / 2 * scale);
  const emitters = [];
  // Only ambient effects play while the board is idle. One-shot systems are
  // game events (flashes), and fast ones are attack lines that cross the board.
  const ambient = (d) => d.looping && d.speed[1] * d.lifetime[1] <= 2
    && Math.max(...d.shape.box.map(Math.abs)) <= 5 && d.shape.radius <= 10;
  // Emitters near the centre, by Unity GameObject name. Add ?emitters to the page
  // address to log them, then list the names to leave out in SKIPPED_EMITTERS.
  if (location.search.includes("emitters")) {
    console.table((board.emitters || []).filter((d) => Math.hypot(d.position[0], d.position[2]) <= 8)
      .map((d) => ({ name: d.name, x: d.position[0].toFixed(2), y: d.position[1].toFixed(2), z: d.position[2].toFixed(2),
        looping: d.looping, rate: d.rate.join("-"), shape: d.shape.type, ambient: ambient(d) })));
  }
  for (const data of (board.emitters || []).filter((d) => ambient(d) && !SKIPPED_EMITTERS.includes(d.name))) {
    const emitter = new ParticleEmitter(data, scale, data.texture ? rgbaTexture(data.texture) : softTexture("glow"));
    group.add(emitter.points);
    emitters.push(emitter);
  }
  for (const part of board.parts) {
    // Props with no texture in the bundle (their material is Unity's default) are
    // left out rather than drawn as grey blocks; a deck dais is kept, as the client
    // draws it.
    if (!part.texture && !part.name.startsWith("Gameboard") && !part.dais) continue;
    // Daises and portals are authored below the playing surface (Unity y < 0),
    // where the opaque surface would hide them, so they are raised onto it.
    const lift = part.box[0][1] < -0.01 ? -part.box[0][1] + SURFACE_LIFT : 0;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(part.positions, 3));
    if (part.normals) geometry.setAttribute("normal", new THREE.BufferAttribute(part.normals, 3));
    else geometry.computeVertexNormals();
    if (part.uvs) geometry.setAttribute("uv", new THREE.BufferAttribute(part.uvs, 2));
    geometry.setIndex(new THREE.BufferAttribute(part.indices, 1));
    // Unity's material tint and emissive colour; the emissive lifts the surface the
    // way the authored glow does, scaled down because the shader is not reproduced.
    const dial = part.name === "PhaseLens";
    const map = dial ? dialMap(part.texture) : (part.texture ? rgbaTexture(part.texture) : null);
    if (dial && map) dials.push(map);
    const look = BOARD_LOOK[board.key] || {};
    const tint = map && part.tint ? part.tint.map((v) => v * (look.tint ?? 1)) : null;
    const material = new THREE.MeshStandardMaterial({
      map,
      color: tint ? new THREE.Color(...tint) : (map ? "#ffffff" : "#6b6a60"),
      // Only parts with an emission mask (_ESRA) glow; the rest keep their albedo.
      ...(map && look.line && part.emissiveTexture
        ? { emissive: new THREE.Color(1, 1, 1), emissiveMap: esraGlowTexture(part.emissiveTexture, look.line, look.sky) } : {}),
      ...(map && look.emission && part.emissiveTexture && part.emission
        ? { emissive: new THREE.Color(...part.emission), emissiveMap: rgbaTexture(part.emissiveTexture) } : {}),
      roughness: 0.9, alphaTest: 0.5, side: THREE.DoubleSide });
    if (part.glow && map) {
      // Glow materials add their texture to what is behind them and cast no shadow.
      material.color = new THREE.Color(...part.glow);
      material.alphaTest = 0;
      material.transparent = true;
      material.depthWrite = false;
      material.blending = THREE.AdditiveBlending;
      material.emissive = new THREE.Color(0, 0, 0);
      material.emissiveMap = null;
      const glow = new THREE.Mesh(geometry, material);
      glow.renderOrder = 2;
      glow.position.y = lift;
      group.add(glow);
      continue;
    }
    const mesh = new THREE.Mesh(geometry, material);
    mesh.castShadow = mesh.receiveShadow = true;
    mesh.position.y = lift;
    group.add(mesh);
  }
  group.userData.emitters = emitters;
  group.userData.dials = dials;
  return group;
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

function paintFace(face, onArt) {
  const W = 256, H = 358, [c, ctx] = canvas(W * 2, H * 2);
  ctx.scale(2, 2);   // painted at double resolution so text stays sharp up close
  ctx.beginPath(); ctx.roundRect(0, 0, W, H, face.hidden ? 16 : 8); ctx.clip();
  if (face.hidden) { paintSleeve(ctx, W, H, face.sleeve); return c; }
  // The client's own card atlases when the install has been read; otherwise
  // the drawn face below.
  const chrome = window.HexAssets && window.HexAssets.cardChrome ? window.HexAssets.cardChrome(onArt) : null;
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
    // Arcs are drawn on a 2D layer above the table, so they can reach the HTML portraits.
    this.arcCanvas = document.createElement("canvas");
    this.arcCanvas.className = "arc-layer";
    container.appendChild(this.arcCanvas);
    this.arcs = [];

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(36, 1, 0.1, 80);
    this.raycaster = new THREE.Raycaster();
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
      map: this.glowTex, color: "#ffe9b0", transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, opacity: 0 }));
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
    this.buildArena();
    renderer.setAnimationLoop(() => this.frame());
  }

  resize() {
    const w = this.container.clientWidth || 1, h = this.container.clientHeight || 1;
    this.renderer.setSize(w, h, false);
    this.arcRatio = Math.min(window.devicePixelRatio || 1, 2);
    this.arcCanvas.width = Math.round(w * this.arcRatio);
    this.arcCanvas.height = Math.round(h * this.arcRatio);
    this.camera.aspect = w / h;
    // Keep the whole field in view on narrow screens: pull back as the aspect narrows.
    const back = Math.max(1, 1.55 / this.camera.aspect) * (this.cameraPull || 1);
    this.camera.position.set(0, 11.6 * back, 7.4 * back);
    this.camera.lookAt(0, 0, 0.55);
    this.camera.updateProjectionMatrix();
  }

  /* -- arena -------------------------------------------------------- */

  // The arena is the client's battleboard (see hex_assets.js). Until one is
  // loaded, or when none can be read, the table is empty: there is no painted
  // stand-in.
  setGameboard(board) {
    this.board = board || null;
    this.buildArena();
  }

  buildArena() {
    for (const child of [...this.arena.children]) {
      this.arena.remove(child);
      child.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) [].concat(o.material).forEach((m) => { if (m.map) m.map.dispose(); m.dispose(); }); });
    }
    this.emitters = [];
    this.dials = [];
    this.scene.background = new THREE.Color(ARENA_BACKGROUND);
    this.scene.fog = null;
    if (!this.board) {
      if (this.cameraPull !== 1) { this.cameraPull = 1; this.resize(); }
      return;
    }
    // The board is framed from the normal camera distance, then the camera
    // pulls back so the central circle and both offset daises fit in view.
    this.cameraPull = 1;
    this.resize();
    const decor = extractedDecor(this.board, this.camera);
    this.arena.add(decor);
    this.emitters = decor.userData.emitters;
    this.dials = decor.userData.dials;
    this.setPhase(this.phase);
    this.cameraPull = BOARD_CAMERA_PULL;
    this.resize();
  }

  setCombat(combat) {
    if (combat === this.combat) return;
    this.combat = combat;
  }

  // The dial shows the current turn phase's icon. The mesh's UVs already cover the
  // sheet's first cell, so a cell is reached by shifting them (the material's
  // _UVOffset in AnimGameBoard); Unity's UV origin is bottom left, so row 0 is the top.
  setPhase(phase) {
    this.phase = phase;
    const cell = DIAL_CELL[phase] || 0;
    for (const map of this.dials || []) {
      map.offset.set((cell % DIAL_COLS) / DIAL_COLS, -Math.floor(cell / DIAL_COLS) / DIAL_ROWS);
    }
  }

  // A card face for the hand and the preview, painted as the board paints it
  // (paintFace) and returned as an image URL. Painted faces are cached by their
  // descriptor; the art is only part of the key once its image has loaded.
  static cardImage(face, onArt) {
    if (face.art && !artImages.has(face.art)) artImage(face.art, onArt);
    const artReady = !!face.art && artImages.get(face.art).ready;
    // The client's card frames load from the install. A face painted before they arrive
    // is painted again once they do, so whether they are ready is part of the key.
    const chrome = window.HexAssets && HexAssets.cardChrome ? HexAssets.cardChrome(chromeAsked ? null : onArt) : null;
    chromeAsked = true;
    const key = JSON.stringify({ ...face, art: artReady ? face.art : null, chrome: !!chrome });
    if (!faceImages.has(key)) {
      if (faceImages.size >= FACE_IMAGE_LIMIT) faceImages.clear();
      faceImages.set(key, paintFace(face, onArt).toDataURL("image/png"));
    }
    return faceImages.get(key);
  }

  /* -- cards -------------------------------------------------------- */

  slot(entry) {
    // World position for a card: warzone rows on either side, chain across the middle.
    const n = entry.count, spacing = Math.min(1.42, (entry.zone === "chain" ? 7 : 8.2) / Math.max(n, 1));
    const x = (entry.index - (n - 1) / 2) * spacing;
    if (entry.zone === "chain") return new THREE.Vector3(x, CHAIN_Y + (entry.top ? 0.12 : 0), 0);
    const side = entry.zone === "mine" ? 1 : -1;
    // Attackers step towards the centre; a blocker steps in a little less. A tapped
    // troop (an attacker after its attack) rests a little back from the centre line.
    const lunge = entry.attacking ? -side * 0.55 : entry.blocking ? -side * 0.3 : entry.tapped ? side * 0.25 : 0;
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

  /* -- arcs --------------------------------------------------------- */

  // The arcs to draw: { from, to, kind }. A card end is a card uid; a portrait end is
  // the portrait element. kind is "attack", "block" or "target".
  setArcs(arcs) {
    this.arcs = arcs;
  }

  // Where an arc end lands, in the arc layer's pixels: a card's top surface, or a
  // portrait's centre. Null when the card is not on the table (e.g. in a pile).
  arcAnchor(end) {
    const box = this.arcCanvas.getBoundingClientRect();
    if (end instanceof Element) {
      const r = end.getBoundingClientRect();
      if (!r.width) return null;
      return { x: r.left + r.width / 2 - box.left, y: r.top + r.height / 2 - box.top };
    }
    const rec = [...this.cards.values()].find((c) => c.entry.uid === end && c.mesh.visible);
    if (!rec) return null;
    const p = this.project(rec.mesh.position.clone().setY(rec.mesh.position.y + CARD_T + 0.2));
    return { x: p.x - box.left, y: p.y - box.top };
  }

  drawArcs(t) {
    const ctx = this.arcCanvas.getContext("2d");
    const ratio = this.arcRatio || 1;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, this.arcCanvas.width / ratio, this.arcCanvas.height / ratio);
    for (const arc of this.arcs) {
      const a = this.arcAnchor(arc.from), b = this.arcAnchor(arc.to);
      if (!a || !b) continue;
      const color = ARC[arc.kind];
      // Bow the arc up the screen, so it reads as an arc rather than a straight line.
      const len = Math.hypot(b.x - a.x, b.y - a.y) || 1;
      const lift = Math.min(170, len * 0.3) + 28;
      const cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2 - lift;
      ctx.save();
      ctx.strokeStyle = color; ctx.fillStyle = color;
      ctx.lineWidth = 4; ctx.lineCap = "round";
      ctx.shadowColor = color; ctx.shadowBlur = 14;
      ctx.setLineDash([12, 8]); ctx.lineDashOffset = -t * 40;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.quadraticCurveTo(cx, cy, b.x, b.y); ctx.stroke();
      // The head points along the curve where it lands.
      const ang = Math.atan2(b.y - cy, b.x - cx), head = 15, spread = 0.42;
      ctx.setLineDash([]);
      ctx.beginPath(); ctx.moveTo(b.x, b.y);
      ctx.lineTo(b.x - head * Math.cos(ang - spread), b.y - head * Math.sin(ang - spread));
      ctx.lineTo(b.x - head * Math.cos(ang + spread), b.y - head * Math.sin(ang + spread));
      ctx.closePath(); ctx.fill();
      ctx.restore();
    }
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
      // Tapped troops (attackers stay tapped until they ready) lie 90 degrees anticlockwise.
      const rotY = e.attacking || e.tapped ? TAP_ATTACK : 0;
      m.rotation.y += (rotY - m.rotation.y) * k;
      // tilt a little towards the camera so faces read better
      const tilt = e.zone === "chain" ? 0.55 : (hovered ? 0.3 : 0.1);
      m.rotation.x += (tilt - m.rotation.x) * k;
      const s = hovered ? 1.12 : 1;
      m.scale.setScalar(m.scale.x + (s - m.scale.x) * k);
      if (rec.glow.visible) rec.glow.material.opacity = 0.75 + Math.sin(t * 5) * 0.25;
    }
    const pixelScale = this.renderer.domElement.height / (2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)));
    for (const e of this.emitters || []) e.update(dt, pixelScale);
    this.burstAge += dt;
    const b = Math.max(0, 1 - this.burstAge / 0.7);
    this.burstSprite.material.opacity = b * 0.8;
    this.burstSprite.scale.setScalar(1 + (1 - b) * 5);
    this.burstLight.intensity = b * 25;
    this.renderer.render(this.scene, this.camera);
    this.drawArcs(t);
  }
}

window.Board3D = Board3D;
