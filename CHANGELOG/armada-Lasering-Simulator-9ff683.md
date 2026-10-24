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
