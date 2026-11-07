// HUD panels inside the three.js battle scene.
//
// The champion panels, the piles and the phase dial are ordinary DOM (app.js
// fills them in and handles their clicks). This module hands those elements to
// CSS3DRenderer and places each one in the board's scene, so they are drawn by
// three.js and follow the board's camera. Each panel keeps the screen corner
// it had in the flat layout: the placement is recomputed every frame from the
// panel's current size, so a resize or a changed panel stays where it belongs.

import * as THREE from "./vendor/three.module.min.js";
import { CSS3DRenderer, CSS3DObject } from "./vendor/CSS3DRenderer.js";

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
  // `host` is the element that holds the board canvas.
  constructor(host) {
    this.host = host;
    this.renderer = new CSS3DRenderer();
    this.renderer.domElement.classList.add("hud3d");
    host.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    // One scene unit is one CSS pixel here, so the camera sits at the distance
    // where that scale holds: the panels land exactly where the flat layout put them.
    this.camera = new THREE.PerspectiveCamera(36, 1, 1, 10000);
    this.size = [0, 0];
    this.panels = [];
    for (const p of PANELS) {
      const el = document.querySelector(p.sel);
      if (!el) continue;
      el.classList.add("hud3d-panel");
      const object = new CSS3DObject(el);
      this.scene.add(object);
      this.panels.push({ p, el, object });
    }
  }

  // Place every panel at its pixel corner, then draw the layer.
  update() {
    const W = this.host.clientWidth || 1, H = this.host.clientHeight || 1;
    if (W !== this.size[0] || H !== this.size[1]) {
      this.size = [W, H];
      this.renderer.setSize(W, H);
      this.camera.aspect = W / H;
      this.camera.position.set(0, 0, H / (2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2))));
      this.camera.updateProjectionMatrix();
    }
    for (const { p, el, object } of this.panels) {
      const [cx, cy] = centreOf(p, el.offsetWidth, el.offsetHeight, W, H);
      object.position.set(cx - W / 2, H / 2 - cy, 0);
    }
    this.renderer.render(this.scene, this.camera);
  }
}
