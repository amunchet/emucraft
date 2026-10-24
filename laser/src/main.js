// Laser engraving simulator app: loads a model and a bitmap, aligns the bitmap on the model, runs the
// simulation in a worker and renders the engraved part with PBR materials for customer-facing previews.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { OBJLoader } from 'three/addons/loaders/OBJLoader.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { toCreasedNormals } from 'three/addons/utils/BufferGeometryUtils.js';
import { DEFAULT_LASER, beam_diameter, rayleigh_range, plan_grid } from './sim.js';

const $ = (id) => document.getElementById(id);

// Material presets: base look plus the look of lasered material. mark_full = removed depth (mm) at which the
// mark look is fully developed; preview = overlay colour used while aligning.
const PRESETS = {
  anodized_black: { label: 'Anodized aluminium, black', color: '#1c1d21', metal: 0.55, rough: 0.38, mark: '#d3d6d9', mark_metal: 0.75, mark_rough: 0.5, mark_full: 0.004, preview: '#ff7a3d' },
  anodized_blue: { label: 'Anodized aluminium, blue', color: '#1d4ea6', metal: 0.6, rough: 0.36, mark: '#d6d9dc', mark_metal: 0.75, mark_rough: 0.5, mark_full: 0.004, preview: '#ff9a3d' },
  anodized_red: { label: 'Anodized aluminium, red', color: '#9e1d27', metal: 0.6, rough: 0.36, mark: '#d6d9dc', mark_metal: 0.75, mark_rough: 0.5, mark_full: 0.004, preview: '#3dd2ff' },
  anodized_silver: { label: 'Anodized aluminium, natural', color: '#b8bbbf', metal: 0.85, rough: 0.42, mark: '#eeeeec', mark_metal: 0.2, mark_rough: 0.75, mark_full: 0.004, preview: '#1a4fd8' },
  stainless: { label: 'Brushed stainless', color: '#c9cbce', metal: 1, rough: 0.3, mark: '#24242a', mark_metal: 0.45, mark_rough: 0.42, mark_full: 0.01, tex: 'brushed', preview: '#1a4fd8' },
  brass: { label: 'Polished brass', color: '#cfa548', metal: 1, rough: 0.22, mark: '#7d5f2a', mark_metal: 0.85, mark_rough: 0.62, mark_full: 0.02, preview: '#1a4fd8' },
  acrylic: { label: 'Black acrylic (cast)', color: '#0a0a0c', metal: 0, rough: 0.06, clearcoat: 1, mark: '#d2d2d2', mark_metal: 0, mark_rough: 0.8, mark_full: 0.02, preview: '#ff7a3d' },
  walnut: { label: 'Walnut', color: '#ffffff', metal: 0, rough: 0.62, mark: '#1f1109', mark_metal: 0, mark_rough: 0.92, mark_full: 0.08, tex: 'walnut', preview: '#7fd4ff' },
  maple: { label: 'Maple', color: '#ffffff', metal: 0, rough: 0.58, mark: '#2e1a0c', mark_metal: 0, mark_rough: 0.9, mark_full: 0.08, tex: 'maple', preview: '#1a4fd8' },
  slate: { label: 'Slate', color: '#ffffff', metal: 0, rough: 0.82, mark: '#b9bcb8', mark_metal: 0, mark_rough: 0.95, mark_full: 0.01, tex: 'slate', preview: '#ff7a3d' },
};

const state = {
  placement: { cx: 0, cy: 0, angle: 0, width_mm: 30, height_mm: 20 },
  laser: { ...DEFAULT_LASER },
  supersample: 2,
  mode: 'align',
  material: { preset: 'anodized_black', custom_texture: null },
  model: null, // { name, triangles, size: [x, y, z] }
  image: null, // { name, w, h }
  result: null, // summary of the last simulation
  busy: false,
};
let model = null; // { positions, top, radius }
let image = null; // { grey: Float32Array 0..255, w, h }
let res = null; // raw worker output + the placement/laser it was made with
let dirty = true; // render on demand: set whenever the scene changes

// ---------- renderer, scene, lights ----------
const view = $('view');
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;
view.prepend(renderer.domElement);
const canvas = renderer.domElement;

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 10000);
camera.up.set(0, 0, 1);
const pmrem = new THREE.PMREMGenerator(renderer);
const env_tex = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
scene.environment = env_tex;

const key = new THREE.DirectionalLight(0xffffff, 2.6);
key.castShadow = true;
key.shadow.mapSize.set(4096, 4096);
key.shadow.bias = -0.0002;
key.shadow.normalBias = 0.01;
scene.add(key, key.target);
const fill = new THREE.HemisphereLight(0xdfe8ff, 0x2a2420, 0.35);
fill.position.set(0, 0, 1);
scene.add(fill);

const ground = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.ShadowMaterial({ opacity: 0.35 }));
ground.receiveShadow = true;
scene.add(ground);

// Shared uniforms for the alignment preview, the footprint discard and the mark look.
const U = {
  u_place: { value: new THREE.Vector4() }, // cx, cy, angle, preview opacity
  u_size: { value: new THREE.Vector2(1, 1) },
  u_img: { value: null },
  u_img_tone: { value: new THREE.Vector2(0, 1) }, // invert, gamma
  u_preview: { value: new THREE.Color() },
  u_rplace: { value: new THREE.Vector4() }, // result placement cx, cy, angle, discard on/off
  u_rsize: { value: new THREE.Vector4(1, 1, 1, 1) }, // width, height, grid_w, grid_h
  u_surf: { value: null },
  u_mark: { value: new THREE.Color() },
  u_mark_pbr: { value: new THREE.Vector3() }, // roughness, metalness, full depth
  u_max_depth: { value: 1 },
  u_heat: { value: 0 },
};

const GLSL_VERT_HEAD = `
varying vec3 v_wpos;
varying vec3 v_wnormal;
#ifdef IS_RESULT
attribute float depth;
varying float v_depth;
#endif
`;
const GLSL_VERT_MAIN = `
v_wpos = (modelMatrix * vec4(transformed, 1.0)).xyz;
v_wnormal = normalize(mat3(modelMatrix) * normal);
#ifdef IS_RESULT
v_depth = depth;
#endif
`;
const GLSL_FRAG_HEAD = `
varying vec3 v_wpos;
varying vec3 v_wnormal;
#ifdef IS_RESULT
varying float v_depth;
#endif
uniform vec4 u_place; uniform vec2 u_size; uniform sampler2D u_img; uniform vec2 u_img_tone; uniform vec3 u_preview;
uniform vec4 u_rplace; uniform vec4 u_rsize; uniform sampler2D u_surf;
uniform vec3 u_mark; uniform vec3 u_mark_pbr; uniform float u_max_depth; uniform float u_heat;
// world XY -> image UV (0..1, v down from the top row) for a placement centre/angle/size
vec2 place_uv(vec2 p, vec3 pl, vec2 size) {
  vec2 d = p - pl.xy;
  float c = cos(pl.z), s = sin(pl.z);
  vec2 l = vec2(c * d.x + s * d.y, -s * d.x + c * d.y);
  return vec2(l.x / size.x + 0.5, 0.5 - l.y / size.y);
}
vec3 turbo(float x) {
  x = clamp(x, 0.0, 1.0);
  vec4 v4 = vec4(1.0, x, x * x, x * x * x); vec2 v2 = v4.zw * v4.z;
  vec3 c = vec3(dot(v4, vec4(0.13572138, 4.61539260, -42.66032258, 132.13108234)) + dot(v2, vec2(-152.94239396, 59.28637943)),
    dot(v4, vec4(0.09140261, 2.19418839, 4.84296658, -14.18503333)) + dot(v2, vec2(4.27729857, 2.82956604)),
    dot(v4, vec4(0.10667330, 12.64194608, -60.58204836, 110.36276771)) + dot(v2, vec2(-89.90310912, 27.34824973)));
  return pow(clamp(c, 0.0, 1.0), vec3(2.2));
}
`;
// Hide the original top surface inside the simulated footprint (the heightfield replaces it).
// The kept band reaches the heightfield border (the outer cell centres, where its skirt wall stands) plus ~1.5
// screen pixels, because with MSAA a discarded fragment drops all its samples and would open a hairline crack.
const GLSL_DISCARD = `
vec2 ruv = place_uv(v_wpos.xy, u_rplace.xyz, u_rsize.xy);
vec2 inset = 0.5 / u_rsize.zw + fwidth(ruv) * 1.5;
if (u_rplace.w > 0.5 && v_wnormal.z > 0.2) {
  if (all(greaterThan(ruv, inset)) && all(lessThan(ruv, 1.0 - inset))) {
    float sz = texture2D(u_surf, ruv).r;
    float pitch = max(u_rsize.x / u_rsize.z, u_rsize.y / u_rsize.w);
    float tol = 0.02 + length(v_wnormal.xy) / max(v_wnormal.z, 0.05) * pitch;
    if (sz > -1e8 && abs(v_wpos.z - sz) < tol) discard;
  }
}
`;

// Physical material with the shared shader injections; is_result adds the depth-driven mark look.
function make_material(is_result) {
  const m = new THREE.MeshPhysicalMaterial();
  if (is_result) m.defines = { IS_RESULT: '' };
  m.onBeforeCompile = (sh) => {
    Object.assign(sh.uniforms, U);
    sh.vertexShader = GLSL_VERT_HEAD + sh.vertexShader.replace('#include <clipping_planes_vertex>', '#include <clipping_planes_vertex>\n' + GLSL_VERT_MAIN);
    sh.fragmentShader = GLSL_FRAG_HEAD + sh.fragmentShader
      .replace('#include <clipping_planes_fragment>', `#include <clipping_planes_fragment>
        float lm = 0.0; float preview_t = 0.0;
        #ifndef IS_RESULT
        ${GLSL_DISCARD}
        #endif`)
      .replace('#include <color_fragment>', `#include <color_fragment>
        #ifdef IS_RESULT
          lm = smoothstep(0.0, u_mark_pbr.z, v_depth) * 0.96;
          diffuseColor.rgb = mix(diffuseColor.rgb, u_mark, lm);
          if (u_heat > 0.5) diffuseColor.rgb = v_depth > 1e-5 ? turbo(v_depth / u_max_depth) : vec3(0.3);
        #else
          if (u_heat > 0.5) diffuseColor.rgb = vec3(0.3);
          if (u_place.w > 0.0 && v_wnormal.z > 0.25) {
            vec2 iuv = place_uv(v_wpos.xy, u_place.xyz, u_size);
            if (all(greaterThan(iuv, vec2(0.0))) && all(lessThan(iuv, vec2(1.0)))) {
              float g = texture2D(u_img, iuv).r;
              preview_t = pow(u_img_tone.x > 0.5 ? g : 1.0 - g, u_img_tone.y) * u_place.w;
              diffuseColor.rgb = mix(diffuseColor.rgb, u_preview, preview_t);
            }
          }
        #endif`)
      .replace('#include <roughnessmap_fragment>', `#include <roughnessmap_fragment>
        roughnessFactor = u_heat > 0.5 ? 0.8 : mix(roughnessFactor, u_mark_pbr.x, lm);`)
      .replace('#include <metalnessmap_fragment>', `#include <metalnessmap_fragment>
        metalnessFactor = u_heat > 0.5 ? 0.0 : mix(mix(metalnessFactor, u_mark_pbr.y, lm), 0.0, preview_t);`)
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>
        totalEmissiveRadiance += u_preview * preview_t * 0.35;
        #ifdef IS_RESULT
        if (u_heat > 0.5 && v_depth > 1e-5) totalEmissiveRadiance += diffuseColor.rgb * 0.4;
        #endif`)
      .replace('#include <lights_physical_fragment>', `#include <lights_physical_fragment>
        #ifdef USE_CLEARCOAT
        material.clearcoat *= 1.0 - lm;
        #endif`);
  };
  m.customProgramCacheKey = () => (is_result ? 'laser_result' : 'laser_base');
  return m;
}
const base_mat = make_material(false);
const res_mat = make_material(true);
// Shadow depth material for the base mesh, so the hidden top surface does not shadow the engraving.
const base_depth_mat = new THREE.MeshDepthMaterial({ depthPacking: THREE.RGBADepthPacking });
base_depth_mat.onBeforeCompile = (sh) => {
  Object.assign(sh.uniforms, U);
  sh.vertexShader = GLSL_VERT_HEAD + sh.vertexShader.replace('#include <clipping_planes_vertex>', '#include <clipping_planes_vertex>\n' + GLSL_VERT_MAIN);
  sh.fragmentShader = GLSL_FRAG_HEAD + sh.fragmentShader.replace('#include <clipping_planes_fragment>', '#include <clipping_planes_fragment>\n' + GLSL_DISCARD);
};

const base_mesh = new THREE.Mesh(new THREE.BufferGeometry(), base_mat);
base_mesh.castShadow = base_mesh.receiveShadow = true;
base_mesh.customDepthMaterial = base_depth_mat;
const res_mesh = new THREE.Mesh(new THREE.BufferGeometry(), res_mat);
res_mesh.castShadow = res_mesh.receiveShadow = true;
res_mesh.visible = false;
const outline = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: 0xff7a3d, depthTest: false, transparent: true, opacity: 0.9 }));
outline.renderOrder = 10;
scene.add(base_mesh, res_mesh, outline);

const controls = new OrbitControls(camera); // connected after the alignment listeners below
controls.enableDamping = true;
controls.dampingFactor = 0.12;
controls.screenSpacePanning = true;

// ---------- procedural textures ----------
// Tiling value-noise fBm: lattice periods (px, py) must be powers of two so every octave wraps seamlessly.
const noise_vals = (() => {
  let s = 1234567;
  const v = new Float32Array(256 * 256);
  for (let k = 0; k < v.length; k++) { s = (s * 1103515245 + 12345) >>> 0; v[k] = s / 4294967296; }
  return v;
})();
function fbm(x, y, px, py, octaves) {
  let sum = 0, amp = 0.5, norm = 0;
  for (let o = 0; o < octaves; o++) {
    const ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy;
    const sx = fx * fx * (3 - 2 * fx), sy = fy * fy * (3 - 2 * fy);
    const x0 = ix & (px - 1), x1 = (ix + 1) & (px - 1), y0 = (iy & (py - 1)) * 256, y1 = ((iy + 1) & (py - 1)) * 256;
    const a = noise_vals[y0 + x0], b = noise_vals[y0 + x1], c = noise_vals[y1 + x0], d = noise_vals[y1 + x1];
    sum += amp * (a + (b - a) * sx + (c - a) * sy + (a - b - c + d) * sx * sy);
    norm += amp;
    amp *= 0.5; x *= 2; y *= 2; px = Math.min(256, px * 2); py = Math.min(256, py * 2);
  }
  return sum / norm;
}

const smooth = (a, b, x) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
const tex_cache = {};
// Build (once) the maps for a procedural texture kind: wood grain runs along X, brushing along X.
function procedural_maps(kind) {
  if (tex_cache[kind]) return tex_cache[kind];
  const N = 1024;
  const cv = document.createElement('canvas');
  cv.width = cv.height = N;
  const ctx = cv.getContext('2d');
  const img = ctx.createImageData(N, N);
  const px = img.data;
  const wood = { walnut: [[128, 86, 54], [58, 35, 20], 24], maple: [[236, 211, 166], [196, 154, 100], 30] }[kind];
  for (let y = 0; y < N; y++) {
    const v = y / N;
    for (let x = 0; x < N; x++) {
      const u = x / N, k = (y * N + x) * 4;
      let r, g, b;
      if (wood) {
        const [light, dark, rings] = wood;
        const t_ring = v * rings + fbm(u * 2, v * 2, 2, 2, 3) * 1.6 + fbm(u * 8, v * 16, 8, 16, 2) * 0.15;
        const f = t_ring - Math.floor(t_ring), ring = f * 0.25 + 0.45 * smooth(0.6, 0.88, f) * (1 - smooth(0.9, 1, f));
        const pores = fbm(u * 32, v * 128, 32, 128, 2);
        const t = Math.min(1, Math.max(0, ring + (pores - 0.5) * 0.4 + (fbm(u * 4, v * 8, 4, 8, 2) - 0.5) * 0.25));
        r = light[0] + (dark[0] - light[0]) * t; g = light[1] + (dark[1] - light[1]) * t; b = light[2] + (dark[2] - light[2]) * t;
      } else if (kind === 'brushed') {
        const t = 0.82 + (fbm(u * 8, v * 256, 8, 256, 2) - 0.5) * 0.35 + (fbm(u * 2, v * 64, 2, 64, 2) - 0.5) * 0.15;
        r = g = b = 255 * t;
      } else if (kind === 'slate') {
        const layer = fbm(u * 2, v * 24, 2, 32, 4);
        const t = 0.55 * fbm(u * 8, v * 8, 8, 8, 5) + 0.45 * layer;
        r = 58 + 50 * t; g = 63 + 50 * t; b = 70 + 52 * t;
      } else throw new Error(`unknown texture ${kind}`);
      px[k] = r; px[k + 1] = g; px[k + 2] = b; px[k + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  const map = new THREE.CanvasTexture(cv);
  map.colorSpace = THREE.SRGBColorSpace;
  const maps = { map };
  if (kind === 'brushed') maps.roughnessMap = new THREE.CanvasTexture(cv);
  if (kind === 'slate' || wood) maps.bumpMap = new THREE.CanvasTexture(cv);
  for (const t of Object.values(maps)) { t.wrapS = t.wrapT = THREE.RepeatWrapping; t.anisotropy = 8; }
  return (tex_cache[kind] = maps);
}

// ---------- material / lighting application ----------
// Push the material panel values into both materials and the mark uniforms.
function apply_material() {
  dirty = true;
  const p = PRESETS[state.material.preset];
  const maps = state.material.custom_texture ? { map: state.material.custom_texture } : p.tex ? procedural_maps(p.tex) : {};
  const scale = 1 / Number($('m_tex_scale').value);
  for (const t of Object.values(maps)) t.repeat.set(scale, scale);
  for (const m of [base_mat, res_mat]) {
    m.color.set($('m_color').value);
    m.roughness = Number($('m_rough').value);
    m.metalness = Number($('m_metal').value);
    m.clearcoat = p.clearcoat || 0;
    m.clearcoatRoughness = 0.03;
    m.map = maps.map || null;
    m.roughnessMap = maps.roughnessMap || null;
    m.bumpMap = maps.bumpMap || null;
    m.bumpScale = p.tex === 'slate' ? 0.6 : 0.08;
    m.wireframe = $('v_wire').checked;
    m.needsUpdate = true;
  }
  U.u_mark.value.set($('m_mark').value);
  U.u_mark_pbr.value.set(Number($('m_mark_rough').value), Number($('m_mark_metal').value), p.mark_full);
  U.u_preview.value.set(p.preview);
  U.u_heat.value = $('v_heat').checked ? 1 : 0;
}

// Load a preset's values into the material panel and apply them.
function set_preset(name) {
  const p = PRESETS[name];
  if (!p) throw new Error(`unknown preset ${name}`);
  state.material.preset = name;
  state.material.custom_texture?.dispose();
  state.material.custom_texture = null;
  $('m_preset').value = name;
  $('m_color').value = p.color;
  $('m_mark').value = p.mark;
  $('m_rough').value = p.rough;
  $('m_metal').value = p.metal;
  $('m_mark_rough').value = p.mark_rough;
  $('m_mark_metal').value = p.mark_metal;
  apply_material();
  sync_outputs();
}

// Key light direction, intensities, exposure, background and the shadow camera fitted to the model.
function apply_lighting() {
  dirty = true;
  const az = THREE.MathUtils.degToRad(Number($('v_az').value)), el = THREE.MathUtils.degToRad(Number($('v_el').value));
  const r = model ? model.radius : 50;
  const c = new THREE.Vector3(0, 0, model ? model.top / 2 : 0);
  const dir = new THREE.Vector3(Math.cos(el) * Math.cos(az), Math.cos(el) * Math.sin(az), Math.sin(el));
  key.position.copy(c).addScaledVector(dir, r * 3);
  key.target.position.copy(c);
  const cam = key.shadow.camera;
  cam.left = cam.bottom = -r * 1.15;
  cam.right = cam.top = r * 1.15;
  cam.near = r * 0.5;
  cam.far = r * 6;
  cam.updateProjectionMatrix();
  key.shadow.radius = Number($('v_soft').value);
  key.shadow.normalBias = r * 0.0003;
  key.intensity = Number($('v_key').value);
  fill.intensity = Number($('v_fill').value);
  renderer.toneMappingExposure = Number($('v_exposure').value);
  scene.environmentIntensity = Number($('v_env').value);
  ground.scale.set(r * 30, r * 30, 1);
  ground.position.z = -0.001;
  ground.visible = $('v_ground').checked;

  const bg = $('v_bg').value, col = new THREE.Color($('v_bg_color').value);
  if (scene.background?.isCanvasTexture) scene.background.dispose();
  scene.backgroundBlurriness = 0;
  scene.backgroundIntensity = 1;
  ground.material.opacity = bg === 'studio' ? 0.28 : 0.4;
  if (bg === 'solid') scene.background = col;
  else if (bg === 'studio') scene.background = new THREE.Color('#e9ebee');
  else if (bg === 'env') {
    scene.background = env_tex;
    scene.backgroundBlurriness = 0.4;
    scene.backgroundIntensity = Number($('v_env').value);
  } else {
    const cv = document.createElement('canvas');
    cv.width = cv.height = 512;
    const g = cv.getContext('2d');
    const top = col.clone().lerp(new THREE.Color('#ffffff'), 0.18), bottom = col.clone().multiplyScalar(0.35);
    const grad = g.createRadialGradient(256, 170, 20, 256, 300, 420);
    grad.addColorStop(0, '#' + top.getHexString());
    grad.addColorStop(1, '#' + bottom.getHexString());
    g.fillStyle = grad;
    g.fillRect(0, 0, 512, 512);
    const t = new THREE.CanvasTexture(cv);
    t.colorSpace = THREE.SRGBColorSpace;
    scene.background = t;
  }
}

// ---------- placement / alignment ----------
// Write placement into the inputs, the preview uniforms and the footprint outline.
function sync_placement() {
  dirty = true;
  const P = state.placement;
  $('p_cx').value = +P.cx.toFixed(3);
  $('p_cy').value = +P.cy.toFixed(3);
  $('p_w').value = +P.width_mm.toFixed(3);
  $('p_h').value = +P.height_mm.toFixed(3);
  $('p_angle').value = +THREE.MathUtils.radToDeg(P.angle).toFixed(2);
  if ($('status').classList.contains('err')) set_status('');
  U.u_place.value.set(P.cx, P.cy, P.angle, state.mode === 'align' ? Number($('p_opacity').value) : 0);
  U.u_size.value.set(P.width_mm, P.height_mm);

  const c = Math.cos(P.angle), s = Math.sin(P.angle), w = P.width_mm / 2, h = P.height_mm / 2;
  const z = model ? model.top + 0.02 : 0;
  const pts = [[-w, -h], [w, -h], [w, -h], [w, h], [w, h], [-w, h], [-w, h], [-w, -h],
    [-h * 0.15, h], [0, h * 1.12], [0, h * 1.12], [h * 0.15, h], [-w * 0.04, 0], [w * 0.04, 0], [0, -w * 0.04], [0, w * 0.04]];
  outline.geometry.setAttribute('position', new THREE.Float32BufferAttribute(pts.flatMap(([x, y]) => [P.cx + c * x - s * y, P.cy + s * x + c * y, z]), 3));
  outline.geometry.computeBoundingSphere();
  outline.visible = state.mode === 'align' && !!image;

  if (image) {
    const pitch_x = P.width_mm / image.w, pitch_y = P.height_mm / image.h;
    const { grid_w, grid_h } = plan_grid(image.w, image.h, state.supersample), capped = grid_w < Math.round(image.w * state.supersample);
    $('r_place').innerHTML = `Pixel pitch <b>${(pitch_x * 1000).toFixed(1)}</b> × <b>${(pitch_y * 1000).toFixed(1)}</b> µm` +
      ` (${(25.4 / pitch_x).toFixed(0)} DPI) · spot <b>${(state.laser.spot_mm * 1000).toFixed(0)}</b> µm<br>` +
      `Simulated grid ${grid_w}×${grid_h}, pitch <b>${(P.width_mm / grid_w * 1000).toFixed(1)}</b> µm` +
      (capped ? ' <span class="warn">(capped at 4 M cells, below the image resolution)</span>' : '');
  }
  mark_stale();
}

// Flag the shown result as out of date after a placement or laser change.
function mark_stale() {
  if (!res) return;
  const same = JSON.stringify(res.placement) === JSON.stringify(state.placement) && JSON.stringify(res.laser) === JSON.stringify(state.laser) &&
    res.supersample === state.supersample;
  state.result.stale = !same;
  $('badge').style.display = same ? 'none' : 'block';
}

// Size the image at one pixel = one spot.
function reset_size() {
  if (!image) return;
  state.placement.width_mm = image.w * state.laser.spot_mm;
  state.placement.height_mm = image.h * state.laser.spot_mm;
  sync_placement();
}

// Scale the image (aspect kept) to 90% of the model's limiting XY dimension and centre it on the model.
function fit_to_model() {
  if (!image || !state.model) return;
  const P = state.placement, [mx, my] = state.model.size, f = 0.9 * Math.min(mx / P.width_mm, my / P.height_mm);
  P.width_mm *= f;
  P.height_mm *= f;
  P.cx = P.cy = 0;
  sync_placement();
}

// Switch between aligning the bitmap (preview on the raw model) and viewing the engraved result.
function set_mode(mode) {
  if (mode === 'result' && !res) throw new Error('no result to show yet');
  state.mode = mode;
  res_mesh.visible = mode === 'result';
  U.u_rplace.value.w = mode === 'result' ? 1 : 0;
  $('mode_align').classList.toggle('on', mode === 'align');
  $('mode_result').classList.toggle('on', mode === 'result');
  $('help').textContent = mode === 'align'
    ? 'Drag image to move · Shift+drag rotate (+Ctrl snaps 15°) · Alt/Ctrl+wheel scale · drag elsewhere to orbit'
    : 'Left-drag orbit · right-drag pan · wheel zoom';
  sync_placement();
}

// Place the camera looking at the model from a direction (top view keeps +Y up on screen).
function view_from(dir) {
  dirty = true;
  const r = model ? model.radius : 50;
  const dist = r / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * 1.05;
  const target = new THREE.Vector3(0, 0, model ? model.top * 0.5 : 0);
  controls.enableDamping = false; // drop any leftover orbit inertia, which would otherwise skew the new view
  controls.update();
  controls.target.copy(target);
  camera.position.copy(target).addScaledVector(dir.clone().normalize(), dist);
  camera.near = Math.max(0.05, r * 0.01);
  camera.far = r * 100;
  camera.updateProjectionMatrix();
  controls.update();
  controls.enableDamping = true;
}

const ray = new THREE.Raycaster();
let drag = null;
// Cast the pointer ray and return its intersection with the horizontal plane at height z.
function plane_point(e, z) {
  const r = canvas.getBoundingClientRect();
  ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
  const d = ray.ray.direction.z;
  if (Math.abs(d) < 1e-6) return null;
  const t = (z - ray.ray.origin.z) / d;
  return t > 0 ? ray.ray.at(t, new THREE.Vector3()) : null;
}
// Whether world point p lies inside the image footprint.
function in_footprint(p) {
  const P = state.placement, dx = p.x - P.cx, dy = p.y - P.cy, c = Math.cos(P.angle), s = Math.sin(P.angle);
  return Math.abs(c * dx + s * dy) <= P.width_mm / 2 && Math.abs(-s * dx + c * dy) <= P.height_mm / 2;
}

// These listeners are registered before OrbitControls connects, so an image drag can stop it seeing the event.
canvas.addEventListener('pointerdown', (e) => {
  if (state.mode !== 'align' || e.button !== 0 || !model || !image) return;
  plane_point(e, model.top); // sets the ray for the model hit test
  const hit = ray.intersectObject(base_mesh)[0];
  const p = hit ? hit.point : plane_point(e, model.top);
  if (!p || (!e.shiftKey && !in_footprint(p))) return;
  e.stopImmediatePropagation();
  canvas.setPointerCapture(e.pointerId);
  const P = state.placement;
  drag = { z: p.z, rotate: e.shiftKey, ox: P.cx - p.x, oy: P.cy - p.y, a0: P.angle, b0: Math.atan2(p.y - P.cy, p.x - P.cx) };
  canvas.style.cursor = e.shiftKey ? 'alias' : 'grabbing';
}, { capture: true });
canvas.addEventListener('pointermove', (e) => {
  if (!drag) {
    if (state.mode === 'align' && model && image) {
      const p = plane_point(e, model.top);
      canvas.style.cursor = p && in_footprint(p) ? (e.shiftKey ? 'alias' : 'grab') : '';
    }
    return;
  }
  const p = plane_point(e, drag.z);
  if (!p) return;
  const P = state.placement;
  if (drag.rotate) {
    let a = drag.a0 + Math.atan2(p.y - P.cy, p.x - P.cx) - drag.b0;
    a = Math.atan2(Math.sin(a), Math.cos(a));
    P.angle = e.ctrlKey ? THREE.MathUtils.degToRad(Math.round(THREE.MathUtils.radToDeg(a) / 15) * 15) : a;
  } else {
    P.cx = p.x + drag.ox;
    P.cy = p.y + drag.oy;
  }
  sync_placement();
});
const end_drag = (e) => {
  if (!drag) return;
  drag = null;
  canvas.releasePointerCapture(e.pointerId);
  canvas.style.cursor = '';
};
canvas.addEventListener('pointerup', end_drag);
canvas.addEventListener('pointercancel', end_drag);
canvas.addEventListener('wheel', (e) => {
  if (state.mode !== 'align' || !image || !(e.altKey || e.ctrlKey)) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  const f = Math.exp(-e.deltaY * 0.0015);
  state.placement.width_mm = Math.max(0.01, state.placement.width_mm * f);
  state.placement.height_mm = Math.max(0.01, state.placement.height_mm * f);
  sync_placement();
}, { capture: true, passive: false });
controls.connect(canvas);

// ---------- inputs ----------
// Take a triangle soup, centre it (XY centred, min Z = 0) and show it as the base part.
function set_model(positions, name) {
  if (!positions.length || positions.length % 9) throw new Error(`model ${name} has no usable triangles`);
  const box = new THREE.Box3().setFromArray(positions);
  const ox = (box.min.x + box.max.x) / 2, oy = (box.min.y + box.max.y) / 2, oz = box.min.z;
  for (let k = 0; k < positions.length; k += 3) { positions[k] -= ox; positions[k + 1] -= oy; positions[k + 2] -= oz; }
  const size = box.getSize(new THREE.Vector3());
  model = { positions, top: size.z, radius: size.length() / 2 };
  state.model = { name, triangles: positions.length / 9, size: [size.x, size.y, size.z].map((v) => +v.toFixed(3)) };

  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(positions.slice(), 3));
  const shown = toCreasedNormals(g, THREE.MathUtils.degToRad(30));
  // box-projected UVs in mm (texture repeat sets the physical tile size)
  const p = shown.getAttribute('position').array, uv = new Float32Array((p.length / 3) * 2);
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  for (let t = 0; t < p.length; t += 9) {
    a.fromArray(p, t); b.fromArray(p, t + 3); c.fromArray(p, t + 6);
    const n = b.sub(a).cross(c.sub(a));
    const ax = Math.abs(n.x), ay = Math.abs(n.y), az = Math.abs(n.z);
    for (let v = 0; v < 3; v++) {
      const x = p[t + v * 3], y = p[t + v * 3 + 1], z = p[t + v * 3 + 2], o = (t / 3 + v) * 2;
      if (az >= ax && az >= ay) { uv[o] = x; uv[o + 1] = y; } else if (ax >= ay) { uv[o] = y; uv[o + 1] = z; } else { uv[o] = x; uv[o + 1] = z; }
    }
  }
  shown.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  base_mesh.geometry.dispose();
  base_mesh.geometry = shown;
  clear_result();
  state.placement.cx = state.placement.cy = 0;
  sync_placement();
  apply_lighting();
  view_from(new THREE.Vector3(-0.45, -1, 0.95));
  show_inputs();
}

async function load_model_file(file) {
  const ext = file.name.split('.').pop().toLowerCase();
  if (!['stl', 'obj', 'glb', 'gltf'].includes(ext)) throw new Error(`unsupported model format .${ext}`);
  let root;
  try {
    if (ext === 'stl') root = new THREE.Mesh(new STLLoader().parse(await file.arrayBuffer()));
    else if (ext === 'obj') root = new OBJLoader().parse(await file.text());
    else {
      root = (await new GLTFLoader().parseAsync(await file.arrayBuffer(), '')).scene;
      root.rotation.x = Math.PI / 2; // glTF is Y-up; the simulator is Z-up
    }
  } catch (err) {
    throw new Error(`Could not read ${file.name}: ${err.message || err}`);
  }
  root.updateMatrixWorld(true);
  const out = [], v = new THREE.Vector3();
  root.traverse((o) => {
    if (!o.isMesh) return;
    const pos = o.geometry.getAttribute('position'), idx = o.geometry.index;
    const count = idx ? idx.count : pos.count;
    for (let k = 0; k < count; k++) {
      v.fromBufferAttribute(pos, idx ? idx.getX(k) : k).applyMatrix4(o.matrixWorld);
      out.push(v.x, v.y, v.z);
    }
  });
  set_model(new Float32Array(out), file.name);
  set_status(`Loaded model ${file.name}`);
}

// Take a grey image (0..255, row 0 = top) as the bitmap to burn.
function set_image(grey, w, h, name) {
  image = { grey, w, h };
  state.image = { name, w, h };
  const tex = new THREE.DataTexture(Uint8Array.from(grey), w, h, THREE.RedFormat, THREE.UnsignedByteType);
  tex.unpackAlignment = 1;
  tex.magFilter = THREE.LinearFilter;
  tex.minFilter = THREE.LinearFilter;
  tex.needsUpdate = true;
  U.u_img.value?.dispose();
  U.u_img.value = tex;
  clear_result();
  reset_size();
  const [mx, my] = state.model ? state.model.size : [Infinity, Infinity];
  const fit = state.placement.width_mm > mx || state.placement.height_mm > my;
  if (fit) fit_to_model();
  show_inputs();
  return fit;
}

// Draw an image source onto a canvas and convert to luminance; transparent pixels count as white (no burn).
function image_to_grey(src, w, h) {
  const cv = document.createElement('canvas');
  cv.width = w;
  cv.height = h;
  const ctx = cv.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(src, 0, 0);
  const d = ctx.getImageData(0, 0, w, h).data, grey = new Float32Array(w * h);
  for (let k = 0; k < grey.length; k++) grey[k] = d[k * 4 + 3] < 128 ? 255 : 0.299 * d[k * 4] + 0.587 * d[k * 4 + 1] + 0.114 * d[k * 4 + 2];
  return grey;
}

async function load_image_file(file) {
  const bmp = await createImageBitmap(file);
  const fit = set_image(image_to_grey(bmp, bmp.width, bmp.height), bmp.width, bmp.height, file.name);
  bmp.close();
  set_status(`Loaded image ${file.name} (${image.w}×${image.h})` + (fit ? '. At 1 px = 1 spot it was larger than the model, so it was scaled to fit.' : ''));
}

// Built-in demo: a 60×40×5 mm plate with a gentle crown (so defocus matters) and a drawn test bitmap.
function load_demo() {
  const geo = new THREE.BoxGeometry(60, 40, 5, 240, 160, 1).toNonIndexed();
  const p = geo.getAttribute('position').array;
  for (let k = 0; k < p.length; k += 3) {
    if (p[k + 2] > 0) p[k + 2] += 0.6 * (1 - (p[k] / 30) ** 2);
    p[k + 2] += 2.5;
  }
  set_model(new Float32Array(p), 'demo plate 60×40×5 mm');

  const W = 600, H = 400, cv = document.createElement('canvas');
  cv.width = W;
  cv.height = H;
  const g = cv.getContext('2d');
  g.fillStyle = '#fff';
  g.fillRect(0, 0, W, H);
  g.strokeStyle = '#000';
  g.lineWidth = 6;
  g.strokeRect(14, 14, W - 28, H - 28);
  g.fillStyle = '#000';
  g.font = 'bold 104px Georgia, "Times New Roman", serif';
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  g.fillText('EMUCRAFT', W / 2, 108);
  g.font = '600 26px system-ui, sans-serif';
  g.fillText('LASER  ·  0.05 mm SPOT', W / 2, 186);
  const ramp = g.createLinearGradient(50, 0, W - 50, 0);
  ramp.addColorStop(0, '#fff');
  ramp.addColorStop(1, '#000');
  g.fillStyle = ramp;
  g.fillRect(50, 222, W - 100, 46);
  g.strokeStyle = '#000';
  g.lineWidth = 2;
  g.strokeRect(50, 222, W - 100, 46);
  [0.75, 0.5, 0.25, 0].forEach((level, k) => {
    const v = Math.round(level * 255);
    g.fillStyle = `rgb(${v},${v},${v})`;
    g.beginPath();
    g.arc(110 + k * 70, 324, 28, 0, Math.PI * 2);
    g.fill();
  });
  for (let k = 0; k < 9; k++) g.fillRect(400 + k * 14, 296, 1 + (k % 3), 56);
  g.beginPath();
  for (let k = 0; k < 10; k++) {
    const r = k % 2 ? 12 : 28, a = -Math.PI / 2 + (k * Math.PI) / 5;
    g.lineTo(560 - 22 + r * Math.cos(a) - 10, 324 + r * Math.sin(a));
  }
  g.fillStyle = '#000';
  g.fill();
  set_image(image_to_grey(cv, W, H), W, H, 'demo bitmap 600×400');
  set_status('Demo loaded. Drag the image to align it, then Simulate.');
}

function show_inputs() {
  const m = state.model, i = state.image;
  $('r_inputs').innerHTML = (m ? `Model <b>${m.name}</b><br>${m.triangles.toLocaleString()} triangles · ${m.size.join(' × ')} mm` : 'No model') +
    '<br>' + (i ? `Image <b>${i.name}</b> · ${i.w}×${i.h} px` : 'No image');
}

// ---------- simulation ----------
const new_worker = () => new Worker(new URL('./worker.js', import.meta.url), { type: 'module' });
let worker = new_worker();
let cancel_job = null; // set while a job runs: stops the worker and resolves the pending simulate() with null

function simulate() {
  if (state.busy) return Promise.reject(new Error('a simulation is already running'));
  if (!model || !image) return Promise.reject(new Error('load a model and an image first'));
  state.busy = true;
  $('b_sim').disabled = true;
  set_progress(0);
  set_status('Simulating...');
  const placement = { ...state.placement }, laser = { ...state.laser }, t0 = performance.now(), job_model = model, job_image = image;
  const job = { positions: model.positions.slice(), grey: image.grey.slice(), img_w: image.w, img_h: image.h, placement, laser, supersample: state.supersample };
  return new Promise((resolve, reject) => {
    const finish = () => {
      state.busy = false;
      cancel_job = null;
      $('b_sim').disabled = false;
    };
    const fail = (msg) => {
      finish();
      set_progress(0);
      reject(new Error(msg));
    };
    cancel_job = () => {
      worker.terminate();
      worker = new_worker();
      finish();
      resolve(null);
    };
    worker.onerror = (e) => { e.preventDefault(); fail(`Simulation worker crashed: ${e.message || 'failed to load'}`); };
    worker.onmessage = (e) => {
      const d = e.data;
      if (d.type === 'progress') return set_progress(d.fraction);
      if (d.type === 'error') return fail(`Simulation failed: ${d.message}`);
      if (model !== job_model || image !== job_image) return fail('The model or image changed during the simulation; result discarded. Simulate again.');
      finish();
      try {
        res = { ...d, placement, laser, supersample: job.supersample };
        show_result(performance.now() - t0);
        resolve(state.result);
      } catch (err) {
        clear_result();
        reject(new Error(`Showing the result failed: ${err.message || err}`));
      }
    };
    worker.postMessage(job, [job.positions.buffer, job.grey.buffer]);
  });
}

// Upload the result surface for the discard test, build the heightfield and switch to result view.
function show_result(ms) {
  const { grid_w: gw, grid_h: gh, surface, placement: P } = res;
  const max_tex = renderer.capabilities.maxTextureSize;
  if (gw > max_tex || gh > max_tex) throw new Error(`grid ${gw}×${gh} exceeds the GPU texture limit ${max_tex}; lower supersample`);
  const s = new Float32Array(gw * gh);
  for (let k = 0; k < s.length; k++) s[k] = Number.isNaN(surface[k]) ? -1e9 : surface[k];
  U.u_surf.value?.dispose();
  const tex = new THREE.DataTexture(s, gw, gh, THREE.RedFormat, THREE.FloatType);
  tex.magFilter = tex.minFilter = THREE.NearestFilter;
  tex.needsUpdate = true;
  U.u_surf.value = tex;
  U.u_rplace.value.set(P.cx, P.cy, P.angle, 1);
  U.u_rsize.value.set(P.width_mm, P.height_mm, gw, gh);
  U.u_max_depth.value = Math.max(res.max_depth, 1e-6);
  refresh_result();
  state.result = {
    grid_w: gw, grid_h: gh, max_depth_mm: res.max_depth, min_spot_mm: res.stats.min_spot_mm, max_spot_mm: res.stats.max_spot_mm,
    focal_z: res.stats.focal_z, ms: Math.round(ms), stale: false,
  };
  $('mode_result').disabled = false;
  set_mode('result');
  set_progress(1);
  $('r_result').innerHTML = `Grid <b>${gw}×${gh}</b> · ${Math.round(ms)} ms<br>Max depth <b>${(res.max_depth * 1000).toFixed(1)} µm</b>` +
    ` · spot <b>${(res.stats.min_spot_mm * 1000).toFixed(1)}</b>–<b>${(res.stats.max_spot_mm * 1000).toFixed(1)}</b> µm`;
  set_status('Simulation complete.');
}

// Heightfield mesh over the grid at depth exaggeration k_exag: one vertex per cell centre, plus a skirt wall
// along the mesh border from the engraved floor up to the original surface, so the part stays closed and the
// engraving reaches the border. `uv` is in mm (base texture), `uv_grid` spans the grid (baked GLB textures).
function build_result(k_exag) {
  const { grid_w: gw, grid_h: gh, surface, depth, placement: P } = res;
  const n = gw * gh, z = new Float32Array(n);
  for (let k = 0; k < n; k++) z[k] = surface[k] - k_exag * depth[k]; // NaN cells stay NaN
  const pw = P.width_mm / gw, ph = P.height_mm / gh, c = Math.cos(P.angle), s = Math.sin(P.angle);
  const pos = new Float32Array(n * 3), nor = new Float32Array(n * 3), uv = new Float32Array(n * 2), uvg = new Float32Array(n * 2);
  for (let j = 0; j < gh; j++) {
    const ly = (0.5 - (j + 0.5) / gh) * P.height_mm;
    for (let i = 0; i < gw; i++) {
      const idx = j * gw + i, lx = ((i + 0.5) / gw - 0.5) * P.width_mm;
      const x = P.cx + c * lx - s * ly, y = P.cy + s * lx + c * ly;
      pos[idx * 3] = x; pos[idx * 3 + 1] = y; pos[idx * 3 + 2] = Number.isNaN(z[idx]) ? 0 : z[idx]; // NaN cells are unreferenced
      uv[idx * 2] = x; uv[idx * 2 + 1] = y;
      uvg[idx * 2] = (i + 0.5) / gw; uvg[idx * 2 + 1] = (j + 0.5) / gh;
      if (Number.isNaN(z[idx])) { nor[idx * 3 + 2] = 1; continue; } // keep unit normals so GLB export stays valid
      const il = i > 0 && !Number.isNaN(z[idx - 1]) ? idx - 1 : idx, ir = i < gw - 1 && !Number.isNaN(z[idx + 1]) ? idx + 1 : idx;
      const ju = j > 0 && !Number.isNaN(z[idx - gw]) ? idx - gw : idx, jd = j < gh - 1 && !Number.isNaN(z[idx + gw]) ? idx + gw : idx;
      const gx = ir === il ? 0 : (z[ir] - z[il]) / ((ir - il) * pw);
      const gy = jd === ju ? 0 : (z[ju] - z[jd]) / (((jd - ju) / gw) * ph);
      const len = Math.hypot(gx, gy, 1);
      nor[idx * 3] = (-c * gx + s * gy) / len; nor[idx * 3 + 1] = (-s * gx - c * gy) / len; nor[idx * 3 + 2] = 1 / len;
    }
  }
  // quad (i, j) holds t1 = (a, c, b) and t2 = (b, c, d) with a = (i, j), b = a + 1, c = a + gw, d = c + 1
  const ok = (i, j) => !Number.isNaN(z[j * gw + i]);
  const t1 = (i, j) => i >= 0 && j >= 0 && i < gw - 1 && j < gh - 1 && ok(i, j) && ok(i + 1, j) && ok(i, j + 1);
  const t2 = (i, j) => i >= 0 && j >= 0 && i < gw - 1 && j < gh - 1 && ok(i + 1, j) && ok(i, j + 1) && ok(i + 1, j + 1);
  const index = new Uint32Array(Math.max(0, (gw - 1) * (gh - 1) * 6));
  let m = 0;
  for (let j = 0; j < gh - 1; j++) {
    for (let i = 0; i < gw - 1; i++) {
      const a = j * gw + i, b = a + 1, cc = a + gw, d = cc + 1;
      if (t1(i, j)) { index[m++] = a; index[m++] = cc; index[m++] = b; }
      if (t2(i, j)) { index[m++] = b; index[m++] = cc; index[m++] = d; }
    }
  }
  // skirt: an edge with a triangle on one side only is on the border; its wall faces that triangle's third vertex
  const sk = { pos: [], nor: [], uv: [], uvg: [], dep: [], index: [] };
  const wall = (p, q, o) => {
    if (k_exag * Math.max(depth[p], depth[q]) < 1e-6) return;
    const ex = pos[q * 3] - pos[p * 3], ey = pos[q * 3 + 1] - pos[p * 3 + 1];
    if (ey * (pos[o * 3] - pos[p * 3]) - ex * (pos[o * 3 + 1] - pos[p * 3 + 1]) < 0) [p, q] = [q, p];
    const nx = pos[q * 3 + 1] - pos[p * 3 + 1], ny = pos[p * 3] - pos[q * 3], len = Math.hypot(nx, ny), v0 = n + sk.dep.length;
    for (const [k, top] of [[p, false], [q, false], [q, true], [p, true]]) {
      sk.pos.push(pos[k * 3], pos[k * 3 + 1], top ? surface[k] : z[k]);
      sk.nor.push(nx / len, ny / len, 0);
      sk.uv.push(uv[k * 2], uv[k * 2 + 1]);
      sk.uvg.push(uvg[k * 2], uvg[k * 2 + 1]);
      sk.dep.push(depth[k]);
    }
    sk.index.push(v0, v0 + 1, v0 + 2, v0, v0 + 2, v0 + 3);
  };
  for (let j = 0; j < gh; j++) {
    for (let i = 0; i < gw; i++) {
      const a = j * gw + i;
      if (i < gw - 1 && t1(i, j) !== t2(i, j - 1)) wall(a, a + 1, t1(i, j) ? a + gw : a + 1 - gw);
      if (j < gh - 1 && t1(i, j) !== t2(i - 1, j)) wall(a, a + gw, t1(i, j) ? a + 1 : a + gw - 1);
      if (i < gw - 1 && j < gh - 1 && t1(i, j) !== t2(i, j)) wall(a + 1, a + gw, t1(i, j) ? a : a + gw + 1);
    }
  }
  const join = (grid, extra, size) => {
    const out = new Float32Array(grid.length + extra.length);
    out.set(grid);
    out.set(extra, grid.length);
    return new THREE.BufferAttribute(out, size);
  };
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', join(pos, sk.pos, 3));
  g.setAttribute('normal', join(nor, sk.nor, 3));
  g.setAttribute('uv', join(uv, sk.uv, 2));
  g.setAttribute('uv_grid', join(uvg, sk.uvg, 2));
  g.setAttribute('depth', join(depth, sk.dep, 1));
  const all = new Uint32Array(m + sk.index.length);
  all.set(index.subarray(0, m));
  all.set(sk.index, m);
  g.setIndex(new THREE.BufferAttribute(all, 1));
  g.computeBoundingSphere();
  return g;
}

// Show the heightfield at the current depth exaggeration.
function refresh_result() {
  dirty = true;
  res_mesh.geometry.dispose();
  res_mesh.geometry = build_result(Number($('v_exag').value));
}

// Drop the shown result (and cancel a running job, whose inputs are being replaced).
function clear_result() {
  cancel_job?.();
  res = null;
  state.result = null;
  res_mesh.geometry.dispose();
  res_mesh.geometry = new THREE.BufferGeometry();
  $('mode_result').disabled = true;
  $('badge').style.display = 'none';
  $('r_result').innerHTML = '';
  set_progress(0);
  set_mode('align');
}

function set_progress(f) {
  $('progress').firstElementChild.style.width = `${Math.round(f * 100)}%`;
}

function set_status(msg, err = false) {
  $('status').textContent = msg;
  $('status').classList.toggle('err', err);
  if (err) console.error(msg);
}

// Derived beam read-outs for the current laser settings.
function show_laser() {
  const L = state.laser, zr = rayleigh_range(L);
  $('r_laser').innerHTML = `Rayleigh range <b>${zr.toFixed(3)}</b> mm · depth of focus <b>${(2 * zr).toFixed(3)}</b> mm<br>` +
    `Spot at ±0.5 mm: <b>${(beam_diameter(L, 0.5) * 1000).toFixed(1)}</b> µm · ±1 mm: <b>${(beam_diameter(L, 1) * 1000).toFixed(1)}</b> µm<br>` +
    `Max removal ≈ <b>${(L.depth_per_pass_mm * L.passes * L.power_w / L.max_power_w * 1000).toFixed(0)}</b> µm in focus`;
}

// ---------- export ----------
function download(blob, name) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

function save_png(scale) {
  const ratio = renderer.getPixelRatio();
  renderer.setPixelRatio(ratio * scale);
  renderer.render(scene, camera);
  canvas.toBlob((b) => download(b, 'laser-render.png'), 'image/png');
  renderer.setPixelRatio(ratio);
}

// GLB of the engraved part at true depth: the base mesh with its top surface cut out of the footprint (triangles
// clipped against the footprint rectangle) plus the heightfield, whose mark look is baked into grid textures.
async function export_glb() {
  const group = new THREE.Group();
  group.rotation.x = -Math.PI / 2; // Z-up mm -> glTF Y-up metres
  group.scale.setScalar(0.001);
  const base_out_mat = new THREE.MeshPhysicalMaterial({
    color: base_mat.color, roughness: base_mat.roughness, metalness: base_mat.metalness, map: base_mat.map, roughnessMap: base_mat.roughnessMap,
    clearcoat: base_mat.clearcoat, clearcoatRoughness: base_mat.clearcoatRoughness,
  });
  const src = base_mesh.geometry.getAttribute('position').array, src_uv = base_mesh.geometry.getAttribute('uv').array;
  let out = src, out_uv = src_uv;
  if (res) {
    const { grid_w: gw, grid_h: gh, surface, placement: P } = res;
    const c = Math.cos(P.angle), s = Math.sin(P.angle), pw = P.width_mm / gw, ph = P.height_mm / gh;
    const x0 = -P.width_mm / 2 + pw / 2, x1 = -x0, y0 = -P.height_mm / 2 + ph / 2, y1 = -y0;
    // split a polygon of [lx, ly, z, u, v] at coord[axis] = value into [below, above]
    const split = (poly, axis, value) => {
      const lo = [], hi = [];
      for (let k = 0; k < poly.length; k++) {
        const a = poly[k], b = poly[(k + 1) % poly.length], da = a[axis] - value, db = b[axis] - value;
        (da < 0 ? lo : hi).push(a);
        if ((da < 0) !== (db < 0)) {
          const t = da / (da - db), q = a.map((v, i) => v + (b[i] - v) * t);
          lo.push(q);
          hi.push(q);
        }
      }
      return [lo, hi];
    };
    const tris = [], uvs = [];
    const emit = (poly) => {
      for (let k = 1; k + 1 < poly.length; k++) {
        // clipping leaves zero-area slivers; they would get zero normals and an invalid GLB
        const [a, b, d] = [poly[0], poly[k], poly[k + 1]], e = [b[0] - a[0], b[1] - a[1], b[2] - a[2]], f = [d[0] - a[0], d[1] - a[1], d[2] - a[2]];
        if (Math.hypot(e[1] * f[2] - e[2] * f[1], e[2] * f[0] - e[0] * f[2], e[0] * f[1] - e[1] * f[0]) < 1e-9) continue;
        for (const q of [a, b, d]) { tris.push(P.cx + c * q[0] - s * q[1], P.cy + s * q[0] + c * q[1], q[2]); uvs.push(q[3], q[4]); }
      }
    };
    for (let t = 0; t < src.length; t += 9) {
      const poly = [0, 1, 2].map((v) => {
        const dx = src[t + v * 3] - P.cx, dy = src[t + v * 3 + 1] - P.cy;
        return [c * dx + s * dy, -s * dx + c * dy, src[t + v * 3 + 2], src_uv[(t / 3 + v) * 2], src_uv[(t / 3 + v) * 2 + 1]];
      });
      const ex = [poly[1][0] - poly[0][0], poly[1][1] - poly[0][1], poly[1][2] - poly[0][2]], ey = [poly[2][0] - poly[0][0], poly[2][1] - poly[0][1], poly[2][2] - poly[0][2]];
      const nx = ex[1] * ey[2] - ex[2] * ey[1], ny = ex[2] * ey[0] - ex[0] * ey[2], nz = ex[0] * ey[1] - ex[1] * ey[0];
      const nzn = nz / Math.hypot(nx, ny, nz);
      const xs = poly.map((q) => q[0]), ys = poly.map((q) => q[1]);
      if (!(nzn > 0.2) || Math.max(...xs) <= x0 || Math.min(...xs) >= x1 || Math.max(...ys) <= y0 || Math.min(...ys) >= y1) { emit(poly); continue; }
      let rest = poly, piece;
      [piece, rest] = split(rest, 0, x0); emit(piece);
      [rest, piece] = split(rest, 0, x1); emit(piece);
      [piece, rest] = split(rest, 1, y0); emit(piece);
      [rest, piece] = split(rest, 1, y1); emit(piece);
      if (rest.length < 3) continue;
      // keep the inside piece unless it is the surface the heightfield replaces (same test as the shader)
      const mx = rest.reduce((a, q) => a + q[0], 0) / rest.length, my = rest.reduce((a, q) => a + q[1], 0) / rest.length, mz = rest.reduce((a, q) => a + q[2], 0) / rest.length;
      const gi = Math.min(gw - 1, Math.max(0, Math.floor(mx / pw + gw / 2))), gj = Math.min(gh - 1, Math.max(0, Math.floor(gh / 2 - my / ph)));
      const sz = surface[gj * gw + gi], tol = 0.02 + (Math.sqrt(Math.max(0, 1 - nzn * nzn)) / nzn) * Math.max(pw, ph);
      if (Number.isNaN(sz) || Math.abs(mz - sz) >= tol) emit(rest);
    }
    out = new Float32Array(tris);
    out_uv = new Float32Array(uvs);
  }
  const bg = new THREE.BufferGeometry();
  bg.setAttribute('position', new THREE.BufferAttribute(out, 3));
  bg.setAttribute('uv', new THREE.BufferAttribute(out_uv, 2));
  const base_out = toCreasedNormals(bg, THREE.MathUtils.degToRad(30));
  group.add(new THREE.Mesh(base_out, base_out_mat));
  if (res) {
    // Bake what the app's shader shows, per grid cell: colour = mix(base colour * base map, mark, lm), and a
    // packed texture with R = clearcoat, G = roughness, B = metalness (glTF clearcoat + metallicRoughness).
    const { grid_w: gw, grid_h: gh, depth } = res;
    const g = build_result(1), wpos = g.getAttribute('position').array;
    const pixels = (t) => {
      if (!t) return null;
      const cv = document.createElement('canvas');
      cv.width = t.image.width;
      cv.height = t.image.height;
      const ctx = cv.getContext('2d', { willReadFrequently: true });
      ctx.drawImage(t.image, 0, 0);
      return { d: ctx.getImageData(0, 0, cv.width, cv.height).data, w: cv.width, h: cv.height, t };
    };
    // nearest texel of a repeating texture at mm uv (x, y), channel ch, 0..1
    const sample = (m, x, y, ch) => {
      const u = x * m.t.repeat.x, v = y * m.t.repeat.y, fu = u - Math.floor(u), fv = v - Math.floor(v);
      const px = Math.min(m.w - 1, Math.floor(fu * m.w)), py = Math.min(m.h - 1, Math.floor((m.t.flipY ? 1 - fv : fv) * m.h));
      return m.d[(py * m.w + px) * 4 + ch] / 255;
    };
    const map = pixels(base_mat.map), rmap = pixels(base_mat.roughnessMap);
    const [mark_rough, mark_metal, full] = U.u_mark_pbr.value.toArray();
    const cv_col = document.createElement('canvas'), cv_mr = document.createElement('canvas');
    cv_col.width = cv_mr.width = gw;
    cv_col.height = cv_mr.height = gh;
    const img_col = new ImageData(gw, gh), img_mr = new ImageData(gw, gh), col = new THREE.Color(), srgb = {};
    for (let k = 0; k < gw * gh; k++) {
      const x = wpos[k * 3], y = wpos[k * 3 + 1], t = Math.min(1, Math.max(0, depth[k] / full)), lm = t * t * (3 - 2 * t) * 0.96;
      if (map) col.setRGB(sample(map, x, y, 0), sample(map, x, y, 1), sample(map, x, y, 2), THREE.SRGBColorSpace).multiply(base_mat.color);
      else col.copy(base_mat.color);
      col.lerp(U.u_mark.value, lm).getRGB(srgb, THREE.SRGBColorSpace);
      img_col.data.set([srgb.r * 255, srgb.g * 255, srgb.b * 255, 255], k * 4);
      const rough = base_mat.roughness * (rmap ? sample(rmap, x, y, 1) : 1);
      img_mr.data.set([(1 - lm) * 255, (rough + (mark_rough - rough) * lm) * 255, (base_mat.metalness + (mark_metal - base_mat.metalness) * lm) * 255, 255], k * 4);
    }
    cv_col.getContext('2d').putImageData(img_col, 0, 0);
    cv_mr.getContext('2d').putImageData(img_mr, 0, 0);
    const tex_col = new THREE.CanvasTexture(cv_col), tex_mr = new THREE.CanvasTexture(cv_mr);
    tex_col.colorSpace = THREE.SRGBColorSpace;
    tex_col.flipY = tex_mr.flipY = false; // grid row 0 is the top of the image, as glTF expects at v = 0
    g.setAttribute('uv', g.getAttribute('uv_grid'));
    g.deleteAttribute('uv_grid');
    g.deleteAttribute('depth');
    group.add(new THREE.Mesh(g, new THREE.MeshPhysicalMaterial({
      map: tex_col, roughnessMap: tex_mr, metalnessMap: tex_mr, roughness: 1, metalness: 1,
      clearcoat: base_mat.clearcoat, clearcoatMap: base_mat.clearcoat ? tex_mr : null, clearcoatRoughness: base_mat.clearcoatRoughness,
    })));
  }
  const glb = await new GLTFExporter().parseAsync(group, { binary: true });
  download(new Blob([glb], { type: 'model/gltf-binary' }), 'laser-part.glb');
  set_status('Exported laser-part.glb');
}

// ---------- UI wiring ----------
function sync_outputs() {
  for (const id of ['p_opacity', 'm_rough', 'm_metal', 'm_mark_rough', 'm_mark_metal', 'v_exag', 'v_exposure', 'v_env', 'v_key', 'v_az', 'v_el', 'v_soft', 'v_fill']) {
    $('o_' + id.split('_').slice(1).join('_')).textContent = $(id).value;
  }
}

// Run an async UI action and report failures in the status line.
const guard = (fn) => async (...args) => {
  try { await fn(...args); } catch (err) { set_status(`${err.message || err}`, true); }
};

for (const [name, p] of Object.entries(PRESETS)) $('m_preset').add(new Option(p.label, name));
for (const key_name of Object.keys(DEFAULT_LASER)) {
  const el = $('l_' + key_name);
  if (!el) throw new Error(`laser parameter ${key_name} has no input`);
  if (el.type === 'checkbox') el.checked = state.laser[key_name];
  else el.value = state.laser[key_name];
  el.addEventListener('change', guard(() => {
    if (el.type === 'checkbox') state.laser[key_name] = el.checked;
    else {
      const v = el.value === '' ? NaN : Number(el.value);
      if (!Number.isFinite(v) || (el.min !== '' && v < Number(el.min)) || (el.max !== '' && v > Number(el.max))) {
        el.value = state.laser[key_name];
        throw new Error(`${el.closest('.row').querySelector('label').textContent} must be between ${el.min} and ${el.max}`);
      }
      state.laser[key_name] = key_name === 'passes' ? Math.round(v) : v;
    }
    U.u_img_tone.value.set(state.laser.invert ? 1 : 0, state.laser.gamma);
    show_laser();
    sync_placement();
  }));
}
U.u_img_tone.value.set(state.laser.invert ? 1 : 0, state.laser.gamma);

// Numeric placement input; an invalid entry is reported and the field restored from the state.
const place_input = (id, apply) => $(id).addEventListener('change', guard(() => {
  const v = $(id).value === '' ? NaN : Number($(id).value);
  try {
    if (!Number.isFinite(v)) throw new Error(`${$(id).closest('.row').querySelector('label').textContent} must be a number`);
    apply(v);
  } catch (err) {
    sync_placement();
    throw err;
  }
  if (state.mode === 'result') set_mode('align');
  sync_placement();
}));
place_input('p_cx', (v) => { state.placement.cx = v; });
place_input('p_cy', (v) => { state.placement.cy = v; });
place_input('p_angle', (v) => { state.placement.angle = THREE.MathUtils.degToRad(v); });
place_input('p_w', (v) => {
  if (!(v > 0)) throw new Error('Width must be positive');
  if ($('p_lock').checked) state.placement.height_mm *= v / state.placement.width_mm;
  state.placement.width_mm = v;
});
place_input('p_h', (v) => {
  if (!(v > 0)) throw new Error('Height must be positive');
  if ($('p_lock').checked) state.placement.width_mm *= v / state.placement.height_mm;
  state.placement.height_mm = v;
});
$('p_opacity').addEventListener('input', () => { sync_outputs(); sync_placement(); });
$('b_reset_size').onclick = reset_size;
$('b_fit').onclick = fit_to_model;
$('b_centre').onclick = () => { state.placement.cx = state.placement.cy = 0; sync_placement(); };
$('b_top').onclick = () => view_from(new THREE.Vector3(0, -0.0001, 1));
$('b_persp').onclick = () => view_from(new THREE.Vector3(-0.45, -1, 0.95));
$('mode_align').onclick = () => set_mode('align');
$('mode_result').onclick = () => set_mode('result');
$('s_super').value = String(state.supersample);
$('s_super').onchange = () => { state.supersample = Number($('s_super').value); sync_placement(); };
$('b_sim').onclick = guard(simulate);
$('b_demo').onclick = guard(load_demo);
$('f_model').onchange = guard(async (e) => { if (e.target.files[0]) await load_model_file(e.target.files[0]); e.target.value = ''; });
$('f_image').onchange = guard(async (e) => { if (e.target.files[0]) await load_image_file(e.target.files[0]); e.target.value = ''; });
$('f_texture').onchange = guard(async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const url = URL.createObjectURL(f);
  const t = await new THREE.TextureLoader().loadAsync(url).finally(() => URL.revokeObjectURL(url));
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.anisotropy = 8;
  state.material.custom_texture?.dispose();
  state.material.custom_texture = t;
  $('m_color').value = '#ffffff';
  apply_material();
  e.target.value = '';
});
$('m_preset').onchange = () => set_preset($('m_preset').value);
for (const id of ['m_color', 'm_mark', 'm_rough', 'm_metal', 'm_mark_rough', 'm_mark_metal', 'm_tex_scale', 'v_heat', 'v_wire']) {
  $(id).addEventListener('input', () => { apply_material(); sync_outputs(); });
}
for (const id of ['v_exposure', 'v_env', 'v_key', 'v_az', 'v_el', 'v_soft', 'v_fill', 'v_bg', 'v_bg_color', 'v_ground']) {
  $(id).addEventListener('input', () => { apply_lighting(); sync_outputs(); });
}
$('v_exag').addEventListener('input', sync_outputs);
$('v_exag').addEventListener('change', () => { if (res) refresh_result(); });
$('b_png').onclick = () => save_png(1);
$('b_png2').onclick = () => save_png(2);
$('b_glb').onclick = guard(export_glb);

// drag-and-drop files anywhere: models by extension, images by MIME type
let drag_depth = 0;
window.addEventListener('dragenter', (e) => { e.preventDefault(); drag_depth++; $('drop').classList.add('on'); });
window.addEventListener('dragleave', () => { if (--drag_depth <= 0) { drag_depth = 0; $('drop').classList.remove('on'); } });
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('drop', guard(async (e) => {
  e.preventDefault();
  drag_depth = 0;
  $('drop').classList.remove('on');
  for (const f of e.dataTransfer.files) {
    if (/\.(stl|obj|glb|gltf)$/i.test(f.name)) await load_model_file(f);
    else if (f.type.startsWith('image/')) await load_image_file(f);
    else throw new Error(`don't know what to do with ${f.name}`);
  }
}));

new ResizeObserver(() => {
  const w = view.clientWidth, h = view.clientHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / Math.max(1, h);
  camera.updateProjectionMatrix();
  dirty = true;
}).observe(view);

set_preset(state.material.preset);
sync_outputs();
show_laser();
load_demo();
controls.addEventListener('change', () => { dirty = true; });
renderer.setAnimationLoop(() => {
  controls.update();
  if (!dirty) return;
  dirty = false;
  renderer.render(scene, camera);
});

window.__laser = {
  simulate, state, set_preset, set_mode, view_from: (x, y, z) => view_from(new THREE.Vector3(x, y, z)),
  export_glb, load_model_file, load_image_file, load_demo, presets: Object.keys(PRESETS), camera, controls, renderer, scene,
};
