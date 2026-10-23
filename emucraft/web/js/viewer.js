// Three.js scene: Z-up camera, orbit controls, lights, markers. Renders on
// demand so an idle viewer costs nothing.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const VIEWS = {
  iso: new THREE.Vector3(1, -1.25, 0.95),
  top: new THREE.Vector3(0, -0.0001, 1),
  front: new THREE.Vector3(0, -1, 0.0001),
  right: new THREE.Vector3(1, 0, 0.0001),
};

export class Viewer {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
    if (!this.renderer.capabilities.isWebGL2) throw new Error('This viewer needs WebGL 2');
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(35, 1, 0.01, 1e5);
    this.camera.up.set(0, 0, 1);
    this.camera.position.set(4, -5, 4);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;
    this.controls.screenSpacePanning = true;
    this.controls.addEventListener('change', () => { this.dirty = true; });

    this.scene.add(new THREE.HemisphereLight(0xdfe8f5, 0x2a2f36, 1.4));
    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(-3, -5, 8);
    this.scene.add(key);
    const fill = new THREE.DirectionalLight(0xb8d4ff, 0.6);
    fill.position.set(5, 4, 3);
    this.scene.add(fill);

    this.world = new THREE.Group();
    this.scene.add(this.world);
    this.markers = new THREE.Group();
    this.markers.name = 'markers';
    this.scene.add(this.markers);
    this.helpers = new THREE.Group();
    this.scene.add(this.helpers);

    this.dirty = true;
    this.size = 1;
    this.applyTheme();
    matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => this.applyTheme());
    new ResizeObserver(() => this.resize()).observe(canvas.parentElement);
    this.resize();
  }

  applyTheme() {
    const css = getComputedStyle(document.documentElement);
    this.scene.background = new THREE.Color(css.getPropertyValue('--viewport').trim() || '#10141a');
    this.dirty = true;
  }

  resize() {
    const parent = this.canvas.parentElement;
    const w = parent.clientWidth, h = parent.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.dirty = true;
  }

  // Table grid and origin axes sized to the job.
  setHelpers(bounds, tableZ) {
    for (const child of [...this.helpers.children]) {
      this.helpers.remove(child);
      child.traverse(o => { o.geometry?.dispose(); o.material?.dispose(); });
    }
    const size = bounds.getSize(new THREE.Vector3());
    const span = Math.max(size.x, size.y, 1e-3);
    const step = Math.pow(10, Math.floor(Math.log10(span / 4)));
    const extent = Math.ceil((span * 1.6) / step) * step;
    const grid = new THREE.GridHelper(extent, Math.round(extent / step), 0x3a4452, 0x252c36);
    grid.rotation.x = Math.PI / 2;
    const center = bounds.getCenter(new THREE.Vector3());
    grid.position.set(center.x, center.y, Number.isFinite(tableZ) ? tableZ - span * 1e-4 : bounds.min.z);
    grid.material.transparent = true;
    grid.material.opacity = 0.5;
    this.helpers.add(grid);
    const axisLen = span * 0.12;
    const axes = new THREE.AxesHelper(axisLen);
    axes.material.depthTest = false;
    axes.renderOrder = 10;
    this.helpers.add(axes);
    this.size = Math.max(size.x, size.y, size.z, 1e-3);
    this.camera.near = this.size / 1000;
    this.camera.far = this.size * 200;
    this.camera.updateProjectionMatrix();
    this.dirty = true;
  }

  frame(bounds, view = 'iso') {
    this.bounds = bounds.clone();
    const dir = (VIEWS[view] || VIEWS.iso).clone().normalize();
    const sphere = bounds.getBoundingSphere(new THREE.Sphere());
    const fov = (this.camera.fov * Math.PI) / 180;
    const fit = Math.max(sphere.radius, 1e-3) / Math.sin(fov / 2) * (this.camera.aspect < 1 ? 1.35 / this.camera.aspect : 1.08);
    this.controls.target.copy(sphere.center);
    this.camera.position.copy(sphere.center).addScaledVector(dir, fit);
    this.camera.updateProjectionMatrix();
    this.controls.update();
    this.dirty = true;
  }

  setView(view) {
    if (!this.bounds) return;
    if (view === 'fit') {
      const dir = this.camera.position.clone().sub(this.controls.target).normalize();
      this.frame(this.bounds, 'iso');
      const dist = this.camera.position.distanceTo(this.controls.target);
      this.camera.position.copy(this.controls.target).addScaledVector(dir, dist);
      this.controls.update();
    } else {
      this.frame(this.bounds, view);
    }
  }

  focus(point, radius) {
    const offset = this.camera.position.clone().sub(this.controls.target);
    const dist = Math.max(radius * 10, this.size * 0.9);
    offset.setLength(dist);
    this.controls.target.copy(point);
    this.camera.position.copy(point).add(offset);
    this.controls.update();
    this.dirty = true;
  }

  // Ray through a canvas pixel.
  ray(clientX, clientY) {
    const r = this.canvas.getBoundingClientRect();
    const ndc = new THREE.Vector2(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
    const caster = new THREE.Raycaster();
    caster.setFromCamera(ndc, this.camera);
    return caster;
  }

  render() {
    this.dirty = false;
    // update() fires 'change' (and returns true) while damping still moves
    // the camera, which keeps the next frame coming
    if (this.controls.update()) this.dirty = true;
    this.renderer.render(this.scene, this.camera);
  }
}
