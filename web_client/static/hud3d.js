// HUD panels anchored in the three.js battle scene.
//
// The champion panels, the piles and the phase dial are ordinary DOM (app.js fills them in and handles their clicks).
// This module hands those elements to CSS2DRenderer as CSS2DObjects, each under an anchor in the board's own scene, and
// draws them with the board's camera. An anchor sits on the table where the panel's corner in the flat layout meets
// the table plane, so the panels keep the corners they had and move with the camera like the rest of the board.
// Anchors are recomputed every frame from the panel's current size and the camera, so a resize or a changed panel
// stays where it belongs.

import * as THREE from "./vendor/three.module.min.js";
import { CSS2DRenderer, CSS2DObject } from "./css2d.js";

// Each panel's corner, in CSS pixels, copied from the former layout in style.css.
// `left`/`right` and `top`/`bottom` give the gap from that edge to the panel.
const PANELS = [
  { sel: ".champion-slot.opp", x: "left", y: "top", gapX: 12, gapY: 10 },
  { sel: ".champion-slot.me", x: "left", y: "bottom", gapX: 12, gapY: 10 },
  { sel: "#opp-piles", x: "right", y: "top", gapX: 14, gapY: 54 },
  { sel: "#my-piles", x: "right", y: "bottom", gapX: 14, gapY: 14 },
  { sel: "#phase-dial", x: "left", y: "middle", gapX: 0, gapY: 0 },
];

// The pixel centre of a panel of size w x h inside a W x H view.
function centreOf(p, w, h, W, H) {
  const cx = p.x === "left" ? p.gapX + w / 2 : W - p.gapX - w / 2;
  const cy = p.y === "top" ? p.gapY + h / 2
    : p.y === "bottom" ? H - p.gapY - h / 2 : H / 2;
  return [cx, cy];
}

export class HudLayer {
  // `host` holds the board canvas; `scene` and `camera` are the board's own.
  constructor(host, scene, camera) {
    this.host = host;
    this.scene = scene;
    this.camera = camera;
    this.renderer = new CSS2DRenderer();
    this.renderer.domElement.classList.add("hud3d");
    host.appendChild(this.renderer.domElement);
    this.table = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
    this.ray = new THREE.Raycaster();
    this.point = new THREE.Vector3();
    this.pointer = new THREE.Vector2();
    this.size = [0, 0];
    this.panels = [];
    for (const p of PANELS) {
      const el = document.querySelector(p.sel);
      if (!el) continue;
      el.classList.add("hud3d-panel");
      const anchor = new THREE.Object3D();
      anchor.add(new CSS2DObject(el));
      scene.add(anchor);
      this.panels.push({ p, el, anchor });
    }
  }

  // The table point a screen point lies over, or null when the ray misses the table.
  tableAt(x, y, W, H) {
    this.pointer.set((x / W) * 2 - 1, -(y / H) * 2 + 1);
    this.ray.setFromCamera(this.pointer, this.camera);
    return this.ray.ray.intersectPlane(this.table, this.point) ? this.point : null;
  }

  // Anchor every panel over its corner, then draw the layer with the board's camera.
  update() {
    const W = this.host.clientWidth || 1, H = this.host.clientHeight || 1;
    if (W !== this.size[0] || H !== this.size[1]) {
      this.size = [W, H];
      this.renderer.setSize(W, H);
    }
    this.camera.updateMatrixWorld();
    for (const { p, el, anchor } of this.panels) {
      const [cx, cy] = centreOf(p, el.offsetWidth, el.offsetHeight, W, H);
      const point = this.tableAt(cx, cy, W, H);
      if (point) anchor.position.copy(point);
    }
    this.renderer.render(this.scene, this.camera);
  }
}
