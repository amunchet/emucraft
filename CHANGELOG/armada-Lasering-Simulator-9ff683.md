# Changelog: Lasering Simulator

- Armada session: `Lasering Simulator`
- Branch: `armada/Lasering-Simulator-9ff683`
- Base branch: `master`
- Started: 2026-10-24 17:28 UTC

Entries are appended newest last, each stamped with the UTC date and time.

## 2026-10-24 12:46 CDT
Started the laser engraving simulator as a new self-contained browser app in `laser/` (Vite + three.js). Scaffolded the package only; simulation core and viewer follow.

## 2026-10-24 14:03 CDT
Added the laser engraving simulator in `laser/`. `sim.js` holds the pure simulation core: a top-down surface rasterizer and a multi-pass Gaussian-beam ablation model whose spot grows with defocus and depth. It runs in a Web Worker. `main.js` and `index.html` are the three.js app: image alignment by drag/rotate/scale projected onto the model, laser parameters, an engraved heightfield that replaces the original top surface, PBR material presets with distinct mark looks, custom textures, lighting controls, and GLB/PNG export. There are 20 vitest tests on the sim core. The README has a new section. After the first build, the code was reviewed through physics, rendering and UX lenses, and the confirmed findings were fixed: GLB mark fidelity, image downsampling aliasing, blur bleeding onto lower surfaces, panel overflow at 1280x720, and fit-to-model for large photos.

## 2026-10-24 14:06 CDT
Merged master, which had been restructured into the packaged `emucraft`. Resolved the README conflict by keeping master's text and moving the laser section in after "Web Deployment". Added the laser vitest suite to CI and bumped CI Node from 20 to 22, which Vite 8 needs. Excluded `node_modules` from the Docker build context. All 275 Python tests and all 20 laser tests pass.

## 2026-10-24 14:35 CDT
Added a laser test sample in `laser/samples/`. It is a generated, watertight 1911 grip panel STL with a crowned face, 3.2 mm edge round-over, screw holes and the mag-release notch. With it comes a 940x2240 px design that bleeds 2 mm past the panel edges: checkering, double diamonds, a cartouche with a grey ramp, and text on the bottom round-over. Simulating it shows how the defocus model behaves on the edges. With focus on the crown, the round-over fades out. With focus 2.5 mm lower, the edges cut deepest and the crown is shallow.

## 2026-10-24 19:23 CDT
Added Altamont Super Wood (Dymondwood) material presets: Super Rosewood (red), Super Blackwood (black) and Super Walnut (brown). Each is a procedural dyed-birch laminate with veneers running along the grip and dark glue lines between them. The palettes were sampled from Altamont 1911 product photos on the staging site and darkened to take out the studio lighting. The finish is a satin lacquer. Laser marks are a dark matte char that removes the lacquer. Presets can now set their own clearcoat roughness and bump strength.

## 2026-10-24 20:15 CDT
Fixed the Dymondwood presets: the veneers are now stacked in Z, as the real laminate is, instead of running side by side across X. Laminate materials use (x + y, z) texture coordinates on the model, the engraved heightfield, its skirt walls and the GLB bake. A flat face therefore shows a single veneer, curvature in XZ or YZ shows contour bands, and engraving reveals the layers below. Smoothed the layer boundaries so gentle slopes give clean bands, and darkened Super Rosewood. Gave the 1911 sample grip a much stronger crown (7.6 mm at the centre, ~3.3 mm at the long edges, ends arched 1.4 mm) so the laminate banding and defocus have real curvature to work with.

## 2026-10-26 13:50 CDT
Customer GLB API for the laser simulator: `laser/server.mjs` (plain node:http, one worker thread per job) takes a model (STL/OBJ), a bitmap (PNG/JPEG), placement, laser and material settings as JSON and returns one `.glb` for a three.js frontend that does not carry the solid: an open, unit-less front shell (top relief on a coarse 0.5 mm grid from the unengraved surface plus outline walls, no back), with the engraving and fine detail baked into colour, normal, roughness/metal/clearcoat and alpha-mask textures (`laser/src/relief.js`). About 5–7 MB for the sample grip instead of ~78 MB, and it passes the Khronos glTF validator. Strict input validation (unknown fields are 400s). Added a reference customer viewer (`laser/viewer.html`), a Docker image and compose service, API tests, and moved presets/procedural textures/model loading into `laser/src/part.js` so the app and the API render materials identically.

## 2026-10-26 14:14 CDT
Fixes from a multi-agent review of the laser API.
- **Service hardening:**
  - The job slot is now held from before the body is read; previously the `LASER_JOBS` cap could be bypassed.
  - The body size is checked against Content-Length, with a configurable `LASER_MAX_MB` cap.
  - PNG/JPEG dimensions are capped at 25 MP before decoding.
  - The mesh grid is bounded: `mesh_mm` is 0.1–2 mm and never finer than 2 texels or 1/1024 of the part.
  - Placements so small that the spot covers hundreds of pixels are rejected.
  - Jobs are stopped when the client disconnects.
  - An optional `LASER_TOKEN` bearer auth was added.
  - Internal errors are no longer echoed to the client.
  - The compose service has memory and CPU limits.
- **Protection:**
  - `X-Laser-Summary` no longer carries scale or texel size.
  - The material tile size is varied ±10 % per render, so wall texture coordinates can't be used as a ruler for the real size.
- **Quality:**
  - Texel pitch now follows the part and `max_texture`, not the engraving grid.
  - Depth is box-filtered when texels are coarser than the engraving (no moiré on hatching or dither).
  - Outline simplification respects the wall-top height.
  - Folded triangles from boundary snapping are dropped.
  - The normal-map frame uses the snapped triangle.
- **Viewer:** fixed the orbit limit and height-only resizes.

## 2026-10-26 18:23 CDT
Customer GLB no longer gives away the part's real size.
- **The leak:** the relief vertex pitch was exactly `mesh_mm` (0.5 mm by default, published by `/api/laser/info`). Since the file is scaled so the longest side is 1, the real size was 0.5 mm ÷ pitch, recoverable to about 0.1 %.
- **The fix:** `customer_glb` takes a `jitter` factor and applies it to every length it builds from in mm: the mesh pitch, the material tile and the 0.005 mm texel floor. Using one factor for all three means they can't be cross-checked against each other. The API picks the factor in [0.9, 1.1] from an HMAC of the part's geometry, so a re-render of the same part can't be used to average it out. It replaces the per-render `Math.random()` tile jitter. The new `LASER_SECRET` variable keeps the factor stable across restarts.
- **Also:** the bearer token is now compared in constant time.
- **Tests:** a unit test for the jitter (mesh pitch and wall UVs), and an API test that the factor is stable per part, stays within ±10 % and depends on the secret.
