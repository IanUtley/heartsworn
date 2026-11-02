// The layered Settings backgrounds, drawn full-screen behind the main page. Each textured layer sits where the prefab
// places it, drawn back to front; each particle system is approximated by sprites that rise and fade. The prefab's
// animators and scripts are not run, so animated layers stay where they were authored.
import * as THREE from "./vendor/three.module.min.js";

const FOV = 40;   // vertical field of view, in degrees

const mid = (range) => (Array.isArray(range) ? (range[0] + range[1]) / 2 : Number(range) || 0);

// A decoded texture as a three.js texture. Decoded rows run from the top of the image, and V runs up from the bottom,
// so the rows are turned over.
function textureOf(pixels) {
  const { width, height, rgba } = pixels;
  const flipped = new Uint8Array(rgba.length);
  const row = width * 4;
  for (let y = 0; y < height; y++) flipped.set(rgba.subarray((height - 1 - y) * row, (height - y) * row), y * row);
  const texture = new THREE.DataTexture(flipped, width, height, THREE.RGBAFormat);
  texture.magFilter = THREE.LinearFilter;
  texture.minFilter = THREE.LinearFilter;
  texture.generateMipmaps = false;
  texture.needsUpdate = true;
  return texture;
}

export class BackgroundScene {
  // ``layers`` is the result of backgroundLayers(): { parts, emitters, bounds } in three.js axes.
  constructor(canvas, layers) {
    this.layers = layers;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.setClearColor(0x07060c, 1);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(FOV, 1, 0.1, 100000);
    this.textures = new Map();
    this.particles = [];
    this.build();
    this.resize = () => this.fit();
    window.addEventListener("resize", this.resize);
    this.fit();
    this.last = performance.now();
    this.frame = requestAnimationFrame((now) => this.tick(now));
  }

  texture(pixels) {
    if (!this.textures.has(pixels)) this.textures.set(pixels, textureOf(pixels));
    return this.textures.get(pixels);
  }

  build() {
    // The camera looks down -Z, so the layers with the smaller Z are further away: draw those first.
    const zOf = (part) => {
      let sum = 0;
      for (let i = 2; i < part.positions.length; i += 3) sum += part.positions[i];
      return sum / (part.positions.length / 3);
    };
    const parts = [...this.layers.parts].sort((a, b) => zOf(a) - zOf(b));
    // The backdrop is the layer with the largest area in the view, measured from its own bounds.
    const extent = (part) => {
      const lo = [Infinity, Infinity], hi = [-Infinity, -Infinity];
      for (let i = 0; i < part.positions.length; i += 3) for (let k = 0; k < 2; k++) {
        lo[k] = Math.min(lo[k], part.positions[i + k]);
        hi[k] = Math.max(hi[k], part.positions[i + k]);
      }
      return { lo, hi, area: (hi[0] - lo[0]) * (hi[1] - lo[1]) };
    };
    const widest = parts.length ? parts.reduce((best, part) => (extent(part).area > extent(best).area ? part : best)) : null;
    this.backdrop = widest
      ? [[extent(widest).lo[0], extent(widest).lo[1], 0], [extent(widest).hi[0], extent(widest).hi[1], 0]]
      : this.layers.bounds;
    // Centre the frame on the backdrop's depth too, so the camera sits in front of it.
    if (widest) {
      const z = zOf(widest);
      this.backdrop[0][2] = z;
      this.backdrop[1][2] = z;
    }
    parts.forEach((part, order) => {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(part.positions, 3));
      geometry.setAttribute("uv", new THREE.BufferAttribute(part.uvs, 2));
      geometry.setIndex(new THREE.BufferAttribute(part.indices, 1));
      const material = new THREE.MeshBasicMaterial({
        map: this.texture(part.texture), color: new THREE.Color(...part.tint),
        transparent: true, depthTest: false, depthWrite: false, side: THREE.DoubleSide,
      });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.renderOrder = order;
      this.scene.add(mesh);
    });

    // Particles: a few sprites per emitter, sized and coloured from its authored values, spread through its lifetime.
    const emitterOrder = parts.length;
    this.layers.emitters.forEach((emitter, index) => {
      const life = Math.max(0.2, mid(emitter.lifetime) || 1);
      const count = Math.max(4, Math.min(24, Math.round(mid(emitter.rate) * life) || 6));
      const map = emitter.texture ? this.texture(emitter.texture) : null;
      const [r, g, b, a] = emitter.startColor || [1, 1, 1, 1];
      for (let n = 0; n < count; n++) {
        const material = new THREE.SpriteMaterial({
          map, color: new THREE.Color(r, g, b), transparent: true, opacity: 0, depthTest: false, depthWrite: false,
        });
        const sprite = new THREE.Sprite(material);
        sprite.renderOrder = emitterOrder + index;
        sprite.scale.setScalar(Math.max(0.05, mid(emitter.size) || 0.2));
        this.scene.add(sprite);
        this.particles.push({
          sprite, emitter, life, alpha: a ?? 1,
          age: Math.random() * life,
          spread: [(Math.random() - 0.5) * 0.4, (Math.random() - 0.5) * 0.2],
          rise: Math.max(0.05, mid(emitter.speed) * 0.25),
        });
      }
    });
  }

  // The camera covers the view with the backdrop: the largest layer, so the scene fills the screen and the smaller layers
  // are framed inside it.
  fit() {
    const width = Math.max(1, window.innerWidth), height = Math.max(1, window.innerHeight);
    this.renderer.setSize(width, height, false);
    const [lo, hi] = this.backdrop;
    const cx = (lo[0] + hi[0]) / 2, cy = (lo[1] + hi[1]) / 2, cz = (lo[2] + hi[2]) / 2;
    const w = Math.max(hi[0] - lo[0], 1), h = Math.max(hi[1] - lo[1], 1);
    const aspect = width / height;
    // The view must sit inside the backdrop, so the visible height is the smaller of its height and its width over the aspect.
    const visibleHeight = Math.min(h, w / aspect);
    const distance = visibleHeight / (2 * Math.tan(THREE.MathUtils.degToRad(FOV / 2)));
    this.camera.aspect = aspect;
    this.camera.position.set(cx, cy, cz + distance);
    this.camera.lookAt(cx, cy, cz);
    this.camera.updateProjectionMatrix();
  }

  step(dt) {
    for (const p of this.particles) {
      p.age += dt;
      if (p.age >= p.life) p.age -= p.life;
      const t = p.age / p.life;
      const [x, y, z] = p.emitter.position;
      p.sprite.position.set(x + p.spread[0] * t, y + p.rise * p.age, z);
      p.sprite.material.opacity = p.alpha * Math.sin(Math.PI * t);
    }
  }

  tick(now) {
    const dt = Math.min(0.1, (now - this.last) / 1000);
    this.last = now;
    this.step(dt);
    this.renderer.render(this.scene, this.camera);
    this.frame = requestAnimationFrame((next) => this.tick(next));
  }

  dispose() {
    cancelAnimationFrame(this.frame);
    window.removeEventListener("resize", this.resize);
    this.renderer.dispose();
    this.scene.clear();
  }
}
