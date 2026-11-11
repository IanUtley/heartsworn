// A CSS2D renderer for three.js scenes: a CSS2DObject is an Object3D whose element is drawn where its world position
// projects on the screen, centred on that point. It follows the API of three.js's CSS2DRenderer (r160). This is a local
// implementation of that API, written for this board, not a copy of the three.js addon.

import * as THREE from "./vendor/three.module.min.js";

export class CSS2DObject extends THREE.Object3D {
  constructor(element) {
    super();
    this.element = element;
    this.element.style.position = "absolute";
    this.isCSS2DObject = true;
  }
}

export class CSS2DRenderer {
  constructor() {
    this.domElement = document.createElement("div");
    this.domElement.style.position = "absolute";
    this.domElement.style.top = "0";
    this.domElement.style.left = "0";
    this.width = 0;
    this.height = 0;
    this.projected = new THREE.Vector3();
  }

  setSize(width, height) {
    this.width = width;
    this.height = height;
    this.domElement.style.width = `${width}px`;
    this.domElement.style.height = `${height}px`;
  }

  render(scene, camera) {
    scene.updateMatrixWorld();
    if (camera.parent === null) camera.updateMatrixWorld();
    this.place(scene, camera);
  }

  // Each CSS2DObject in the scene: its element is attached to the overlay and moved to its projected point. A point
  // behind the camera hides the element.
  place(object, camera) {
    for (const child of object.children) {
      if (child.isCSS2DObject) {
        const element = child.element;
        if (element.parentNode !== this.domElement) this.domElement.appendChild(element);
        this.projected.setFromMatrixPosition(child.matrixWorld).project(camera);
        const x = (this.projected.x + 1) * this.width / 2;
        const y = (1 - this.projected.y) * this.height / 2;
        const behind = this.projected.z > 1;
        element.style.display = behind ? "none" : "";
        element.style.transform = `translate(-50%, -50%) translate(${x}px, ${y}px)`;
      }
      this.place(child, camera);
    }
  }
}
