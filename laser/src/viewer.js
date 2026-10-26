// Reference customer viewer: shows one customer GLB from the laser API (POST /api/laser/render) with plain three.js
// and the simulator's studio lighting. Load with ?src=<url>, or drop a .glb on the page.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.shadowMap.enabled = true;
document.body.prepend(renderer.domElement);
const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(30, 1, 0.01, 100);
scene.background = new THREE.Color('#2a2f38');
scene.environment = new THREE.PMREMGenerator(renderer).fromScene(new RoomEnvironment(), 0.04).texture;
scene.environmentIntensity = 0.8;
const key = new THREE.DirectionalLight(0xffffff, 2.6);
key.position.set(-1.5, 2, -1.5); // azimuth 225°, elevation 40°, as the simulator's default key light
key.castShadow = true;
key.shadow.mapSize.set(4096, 4096);
key.shadow.bias = -0.0002;
key.shadow.normalBias = 0.01;
Object.assign(key.shadow.camera, { left: -1, right: 1, top: 1, bottom: -1, near: 0.1, far: 10 });
scene.add(key, new THREE.HemisphereLight(0xdfe8ff, 0x2a2420, 0.35));
const ground = new THREE.Mesh(new THREE.PlaneGeometry(10, 10).rotateX(-Math.PI / 2), new THREE.ShadowMaterial({ opacity: 0.35 }));
ground.receiveShadow = true;
scene.add(ground);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.maxPolarAngle = Math.PI * 0.47; // the file has no back: keep the camera above the part
controls.maxDistance = 6;
let part = null;

// Show a GLB (URL or File), scaled so its largest side is 1 and resting on the ground.
async function show(src) {
  const url = src instanceof Blob ? URL.createObjectURL(src) : src;
  const gltf = await new GLTFLoader().loadAsync(url);
  if (src instanceof Blob) URL.revokeObjectURL(url);
  if (part) scene.remove(part);
  part = gltf.scene;
  part.traverse((o) => { if (o.isMesh) o.castShadow = o.receiveShadow = true; });
  const box = new THREE.Box3().setFromObject(part), size = box.getSize(new THREE.Vector3()), k = 1 / Math.max(size.x, size.y, size.z);
  part.scale.setScalar(k);
  part.position.set(-k * (box.min.x + box.max.x) / 2, -k * box.min.y, -k * (box.min.z + box.max.z) / 2);
  scene.add(part);
  view_from(new THREE.Vector3(-0.45, 0.95, 1));
}

// Put the camera on direction dir from the part's centre, framing it.
function view_from(dir) {
  controls.target.set(0, 0.05, 0);
  camera.position.copy(dir).normalize().multiplyScalar(2.2).add(controls.target);
  controls.update();
}

function frame() {
  const w = renderer.domElement.clientWidth, h = renderer.domElement.clientHeight;
  const cv = renderer.domElement, pr = renderer.getPixelRatio();
  if (cv.width !== Math.floor(w * pr) || cv.height !== Math.floor(h * pr)) { renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); }
  controls.update();
  renderer.render(scene, camera);
  requestAnimationFrame(frame);
}
renderer.domElement.style.height = '100vh';
frame();
addEventListener('dragover', (e) => e.preventDefault());
addEventListener('drop', (e) => { e.preventDefault(); if (e.dataTransfer.files[0]) show(e.dataTransfer.files[0]).catch((err) => alert(err.message)); });
const src = new URLSearchParams(location.search).get('src');
if (src) show(src).catch((err) => { document.getElementById('hint').textContent = `Could not load ${src}: ${err.message}`; });
window.__viewer = { show, view_from, camera, controls, scene, renderer, THREE };
