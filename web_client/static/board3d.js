// 3D battle board drawn with three.js.
//
// app.js keeps the game state and the HUD (hands, champions, piles, prompts).
// This module draws the arena, both warzones and the chain, and reports clicks
// and hovers on cards by session card uid. Card faces arrive as plain
// descriptors from app.js and are painted onto canvas textures here.

import * as THREE from "./vendor/three.module.min.js";
import { SLEEVES, canvas, artImage, artImages, ellipsize, paintFace, paintSleeve } from "./card_face.js";

const CARD_W = 1.2, CARD_H = 1.68, CARD_T = 0.03;
const FIELD_R = 4.3, RING_R = 6.5, RING_Y = 0.12;
const ROW_Z = 2.2, CHAIN_Y = 0.6, REST_Y = 0.14;

// Same palette as the CSS gameboards in style.css.

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


// Painted hand and preview faces as image URLs (Board3D.cardImage), kept to a bound.
const faceImages = new Map();
const FACE_IMAGE_LIMIT = 400;
let chromeAsked = false;   // the card frames' readiness callback is registered once

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
