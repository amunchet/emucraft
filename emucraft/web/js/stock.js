// Stock rendering: the kernel's height field lives in a GPU texture and a
// grid mesh is displaced in the vertex shader. Only the rectangle the kernel
// touched is re-uploaded each frame, straight from WebAssembly memory.
import * as THREE from 'three';

const MAX_MESH_VERTICES = 1_600_000;
const COLOR_WIDTH = 2048;

export const STOCK_MODES = { material: 0, tool: 1, depth: 2, recent: 3 };

const surfaceVertex = /* glsl */`
  uniform sampler2D uHeight;
  uniform highp usampler2D uMark;
  uniform ivec2 uGrid;
  uniform ivec2 uMesh;
  out vec3 vWorld;
  out float vHeight;
  flat out uint vMark;
  void main() {
    int i = gl_VertexID % uMesh.x;
    int j = gl_VertexID / uMesh.x;
    ivec2 t = ivec2((i * (uGrid.x - 1)) / max(uMesh.x - 1, 1), (j * (uGrid.y - 1)) / max(uMesh.y - 1, 1));
    float h = texelFetch(uHeight, t, 0).r;
    vMark = texelFetch(uMark, t, 0).r;
    vHeight = h;
    vec4 world = modelMatrix * vec4(position.xy, h, 1.0);
    vWorld = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }`;

const skirtVertex = /* glsl */`
  uniform sampler2D uHeight;
  uniform highp usampler2D uMark;
  uniform float uZBottom;
  in vec3 aTexel;            // x, y texel and 1 for the bottom edge
  out vec3 vWorld;
  out float vHeight;
  flat out uint vMark;
  void main() {
    ivec2 t = ivec2(aTexel.xy);
    float h = texelFetch(uHeight, t, 0).r;
    vMark = texelFetch(uMark, t, 0).r;
    float z = aTexel.z > 0.5 ? uZBottom : h;
    vHeight = h;
    vec4 world = modelMatrix * vec4(position.xy, z, 1.0);
    vWorld = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }`;

const fragment = /* glsl */`
  uniform sampler2D uColors;
  uniform int uColorsW;
  uniform int uMode;
  uniform uint uCurrent;
  uniform vec3 uStock;
  uniform vec3 uCut;
  uniform vec3 uHighlight;
  uniform vec3 uPrevious;
  uniform float uZTop;
  uniform float uZBottom;
  uniform vec3 uCameraDir;
  in vec3 vWorld;
  in float vHeight;
  flat in uint vMark;
  layout(location = 0) out highp vec4 fragColor;

  vec3 ramp(float t) {
    t = clamp(t, 0.0, 1.0) * 5.0;
    vec3 c0 = vec3(0.18, 0.20, 0.55), c1 = vec3(0.13, 0.42, 0.72), c2 = vec3(0.10, 0.62, 0.70);
    vec3 c3 = vec3(0.36, 0.78, 0.45), c4 = vec3(0.86, 0.85, 0.25), c5 = vec3(0.99, 0.62, 0.20);
    if (t < 1.0) return mix(c0, c1, t);
    if (t < 2.0) return mix(c1, c2, t - 1.0);
    if (t < 3.0) return mix(c2, c3, t - 2.0);
    if (t < 4.0) return mix(c3, c4, t - 3.0);
    return mix(c4, c5, t - 4.0);
  }
  vec3 toLinear(vec3 c) { return pow(c, vec3(2.2)); }   // inputs below are sRGB

  void main() {
    float span = max(uZTop - uZBottom, 1e-6);
    if (vHeight <= uZBottom + span * 1e-5) discard;   // cut through: show the hole
    vec3 n = normalize(cross(dFdx(vWorld), dFdy(vWorld)));
    if (dot(n, cameraPosition - vWorld) < 0.0) n = -n;   // face the viewer
    vec3 base = uStock;
    if (vMark == 0xffffffffu) {
      base = uPrevious;                          // cut by an earlier program
    } else if (vMark > 0u) {
      int m = int(vMark) - 1;
      if (uMode == 1) base = toLinear(texelFetch(uColors, ivec2(m % uColorsW, m / uColorsW), 0).rgb);
      else if (uMode == 3 && vMark == uCurrent) base = uHighlight;
      else base = uCut;
    }
    if (uMode == 2) base = toLinear(ramp((vHeight - uZBottom) / span));
    vec3 key = normalize(vec3(-0.35, -0.55, 0.76));
    float diffuse = 0.55 * max(dot(n, key), 0.0) + 0.45 * max(dot(n, -uCameraDir), 0.0);
    float sky = 0.5 + 0.5 * n.z;
    vec3 color = base * (0.22 + 0.18 * sky + 0.75 * diffuse);
    vec3 h = normalize(key - uCameraDir);
    color += vec3(0.06) * pow(max(dot(n, h), 0.0), 40.0) * (vMark > 0u ? 1.0 : 0.3);
    fragColor = linearToOutputTexel(vec4(color, 1.0));
  }`;

function makeTexture(gl, internal, width, height) {
  const tex = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.texStorage2D(gl.TEXTURE_2D, 1, internal, width, height);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  return tex;
}

export class StockView {
  constructor(renderer, grid, moveCount) {
    this.renderer = renderer;
    this.gl = renderer.getContext();
    if (!(this.gl instanceof WebGL2RenderingContext)) throw new Error('WebGL 2 is required');
    this.grid = grid;
    const gl = this.gl;
    const { nx, ny } = grid;
    const maxSize = gl.getParameter(gl.MAX_TEXTURE_SIZE);
    if (nx > maxSize || ny > maxSize) throw new Error(`Stock grid ${nx} x ${ny} exceeds the GPU texture limit ${maxSize}`);

    renderer.state.reset();
    this.heightTex = makeTexture(gl, gl.R32F, nx, ny);
    this.markTex = makeTexture(gl, gl.R32UI, nx, ny);
    this.colorRows = Math.max(1, Math.ceil(Math.max(1, moveCount) / COLOR_WIDTH));
    this.colorTex = makeTexture(gl, gl.RGBA8, COLOR_WIDTH, this.colorRows);
    gl.bindTexture(gl.TEXTURE_2D, null);
    renderer.state.reset();

    this.uniforms = {
      uHeight: { value: new THREE.ExternalTexture(this.heightTex) },
      uMark: { value: new THREE.ExternalTexture(this.markTex) },
      uColors: { value: new THREE.ExternalTexture(this.colorTex) },
      uColorsW: { value: COLOR_WIDTH },
      uGrid: { value: new THREE.Vector2(nx, ny) },
      uMesh: { value: new THREE.Vector2(1, 1) },
      uMode: { value: 0 },
      uCurrent: { value: 0 },
      uStock: { value: new THREE.Color(0x6f7b89) },
      uCut: { value: new THREE.Color(0xc4ced9) },
      uHighlight: { value: new THREE.Color(0xff9d2e) },
      uPrevious: { value: new THREE.Color(0x9aa6b4) },
      uZTop: { value: grid.z_top },
      uZBottom: { value: grid.z_bottom },
      uCameraDir: { value: new THREE.Vector3(0, 0, -1) },
    };
    // ivec2 uniforms: three uploads Vector2 to ivec2 through uniform2iv
    this.group = new THREE.Group();
    this.group.name = 'stock';
    this._buildSurface();
    this._buildSkirt();
    this._buildOutline();
  }

  _material(vertexShader) {
    return new THREE.ShaderMaterial({
      glslVersion: THREE.GLSL3,
      uniforms: this.uniforms,
      vertexShader,
      fragmentShader: fragment,
      side: THREE.DoubleSide,
    });
  }

  _buildSurface() {
    const { nx, ny, x0, y0, cell } = this.grid;
    let gx = nx, gy = ny;
    if (gx * gy > MAX_MESH_VERTICES) {
      const s = Math.sqrt(MAX_MESH_VERTICES / (gx * gy));
      gx = Math.max(2, Math.floor(gx * s));
      gy = Math.max(2, Math.floor(gy * s));
    }
    gx = Math.max(2, gx);
    gy = Math.max(2, gy);
    this.uniforms.uMesh.value.set(gx, gy);
    const pos = new Float32Array(gx * gy * 3);
    for (let j = 0; j < gy; j++) {
      const ty = Math.floor((j * (ny - 1)) / Math.max(gy - 1, 1));
      const y = y0 + (ty + 0.5) * cell;
      for (let i = 0; i < gx; i++) {
        const tx = Math.floor((i * (nx - 1)) / Math.max(gx - 1, 1));
        const k = 3 * (j * gx + i);
        pos[k] = x0 + (tx + 0.5) * cell;
        pos[k + 1] = y;
      }
    }
    const quads = (gx - 1) * (gy - 1);
    const index = new Uint32Array(quads * 6);
    let k = 0;
    for (let j = 0; j < gy - 1; j++) {
      for (let i = 0; i < gx - 1; i++) {
        const a = j * gx + i, b = a + 1, c = a + gx, d = c + 1;
        index[k++] = a; index[k++] = b; index[k++] = d;
        index[k++] = a; index[k++] = d; index[k++] = c;
      }
    }
    const geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geom.setIndex(new THREE.BufferAttribute(index, 1));
    this._bounds(geom);
    this.surface = new THREE.Mesh(geom, this._material(surfaceVertex));
    this.surface.frustumCulled = false;
    this.group.add(this.surface);
  }

  _buildSkirt() {
    const { nx, ny, x0, y0, cell } = this.grid;
    const xs = x => x0 + (x + 0.5) * cell, ys = y => y0 + (y + 0.5) * cell;
    const edges = [];
    for (let x = 0; x < nx; x++) edges.push([x, 0]);
    for (let y = 0; y < ny; y++) edges.push([nx - 1, y]);
    for (let x = nx - 1; x >= 0; x--) edges.push([x, ny - 1]);
    for (let y = ny - 1; y >= 0; y--) edges.push([0, y]);
    const pos = new Float32Array(edges.length * 2 * 3);
    const tex = new Float32Array(edges.length * 2 * 3);
    edges.forEach(([x, y], i) => {
      for (let b = 0; b < 2; b++) {
        const k = 3 * (2 * i + b);
        pos[k] = xs(x); pos[k + 1] = ys(y);
        tex[k] = x; tex[k + 1] = y; tex[k + 2] = b;
      }
    });
    const index = [];
    for (let i = 0; i < edges.length - 1; i++) {
      const a = 2 * i, b = a + 1, c = a + 2, d = a + 3;
      index.push(a, b, d, a, d, c);
    }
    const geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geom.setAttribute('aTexel', new THREE.BufferAttribute(tex, 3));
    geom.setIndex(index);
    this._bounds(geom);
    this.skirt = new THREE.Mesh(geom, this._material(skirtVertex));
    this.skirt.frustumCulled = false;
    this.group.add(this.skirt);
  }

  _buildOutline() {
    const { nx, ny, x0, y0, cell, z_top, z_bottom } = this.grid;
    const box = new THREE.Box3(new THREE.Vector3(x0, y0, z_bottom), new THREE.Vector3(x0 + nx * cell, y0 + ny * cell, z_top));
    this.outline = new THREE.Box3Helper(box, 0x55606d);
    this.outline.material.transparent = true;
    this.outline.material.opacity = 0.5;
    this.group.add(this.outline);
  }

  _bounds(geom) {
    const { nx, ny, x0, y0, cell, z_top, z_bottom } = this.grid;
    geom.boundingBox = new THREE.Box3(new THREE.Vector3(x0, y0, z_bottom), new THREE.Vector3(x0 + nx * cell, y0 + ny * cell, z_top));
    geom.boundingSphere = geom.boundingBox.getBoundingSphere(new THREE.Sphere());
  }

  // Copy [x0, x1) x [y0, y1) of the kernel arrays into the textures.
  upload(heights, marks, rect) {
    const gl = this.gl, state = this.renderer.state;
    const { nx, ny } = this.grid;
    const [x0, y0, x1, y1] = rect || [0, 0, nx, ny];
    if (x1 <= x0 || y1 <= y0) return;
    // three leaves these on after uploading image textures (e.g. sprites)
    state.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    state.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    state.pixelStorei(gl.UNPACK_ALIGNMENT, 4);
    state.pixelStorei(gl.UNPACK_ROW_LENGTH, nx);
    state.pixelStorei(gl.UNPACK_SKIP_PIXELS, x0);
    state.pixelStorei(gl.UNPACK_SKIP_ROWS, y0);
    state.bindTexture(gl.TEXTURE_2D, this.heightTex);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, x0, y0, x1 - x0, y1 - y0, gl.RED, gl.FLOAT, heights);
    state.bindTexture(gl.TEXTURE_2D, this.markTex);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, x0, y0, x1 - x0, y1 - y0, gl.RED_INTEGER, gl.UNSIGNED_INT, marks);
    state.pixelStorei(gl.UNPACK_ROW_LENGTH, 0);
    state.pixelStorei(gl.UNPACK_SKIP_PIXELS, 0);
    state.pixelStorei(gl.UNPACK_SKIP_ROWS, 0);
  }

  // colors: Uint8Array RGBA per move
  setMoveColors(colors) {
    const gl = this.gl, state = this.renderer.state;
    const data = new Uint8Array(COLOR_WIDTH * this.colorRows * 4);
    data.set(colors.subarray(0, Math.min(colors.length, data.length)));
    state.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    state.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    state.pixelStorei(gl.UNPACK_ALIGNMENT, 4);
    state.bindTexture(gl.TEXTURE_2D, this.colorTex);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, COLOR_WIDTH, this.colorRows, gl.RGBA, gl.UNSIGNED_BYTE, data);
  }

  setMode(mode) { this.uniforms.uMode.value = STOCK_MODES[mode] ?? 0; }
  setCurrent(move) { this.uniforms.uCurrent.value = Math.max(0, move + 1); }
  setCamera(camera) { camera.getWorldDirection(this.uniforms.uCameraDir.value); }

  dispose() {
    const gl = this.gl;
    for (const obj of [this.surface, this.skirt]) { obj.geometry.dispose(); obj.material.dispose(); }
    this.outline.geometry.dispose();
    this.outline.material.dispose();
    gl.deleteTexture(this.heightTex);
    gl.deleteTexture(this.markTex);
    gl.deleteTexture(this.colorTex);
    this.renderer.state.reset();
  }
}
