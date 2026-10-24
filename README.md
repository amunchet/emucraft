# Emucraft

## TODO: Need to have a block diagram of the pipeline (Gcode parser -> Arc helper -> C Kernel -> Blocks output -> Python renderer web page results)
Emucraft is the child of Emu.  

The goal is to determine whether or not a collision occurs during a G-code program.

This is done through modelling of the block and resulting 3D approxmiation of the machining path, compared with the cutting tool information.

Components:
    - G-code parser to XYZ file (Python)
    - Kernel in C to do actual collision detection and to return an array of block state
    - `Open3D` (Python) to render final block state or any collision states for visualization.

Key improvements over Emu:

- Separation of Visual and Backend
- Full tests and coverage
- Easy API

## Laser engraving simulator (`laser/`)
A separate, browser-based section of Emucraft that simulates laser engraving a bitmap into a solid and renders the result with three.js for customer-facing previews.

Run it: `cd laser && npm install && npm run dev` (tests: `npm test`, production bundle: `npm run build`).

Inputs:
- A model: STL, OBJ, GLB/GLTF. It is centred in XY with its lowest point at Z = 0. The laser fires along -Z.
- A bitmap: any browser-readable image, converted to greyscale. By default black removes the most material and white removes none (`invert` flips this). Transparent pixels are not burned.
- A built-in demo plate and bitmap load on startup.

Workflow:
1. **Align**: the image is projected straight down onto the model. Drag to move it, Shift+drag to rotate (hold Ctrl as well to snap to 15°), Alt/Ctrl+wheel to scale, or type values. By default one pixel is one spot at focus; "Fit to model" scales the image to the part instead.
2. **Laser**: set spot size at focus, lens focal length, beam diameter at the lens, focus offset, power and rated power, depth per pass, passes, ablation threshold and gamma.
3. **Simulate**: runs in a Web Worker (`src/worker.js` → `src/sim.js`).
4. **Result**: choose a material preset (anodized aluminium, stainless, brass, acrylic, walnut, maple, slate) or upload a custom texture. Adjust lighting and exposure. Export a GLB for the website or a PNG screenshot.

Model (`laser/src/sim.js`):
- The top surface under the image is rasterized into a height grid. The grid uses the image pixels, optionally supersampled, and is capped at 4M cells.
- The spot diameter at a distance dz from the focal plane is `d = sqrt(spot² + (dz · D / f)²)`. Here D is the beam diameter at the lens and f is the focal length, so the Rayleigh range is `spot · f / D`.
- The focal plane sits at the highest point under the footprint, plus the focus offset.
- Passes are simulated in up to 24 steps. In each step every cell removes `max(0, grey · power/max · (spot/d)² − threshold) / (1 − threshold) · depth_per_pass`. That removal is spread by a Gaussian with σ = d/4, using the cell's own current defocus. Deeper cuts therefore grow wider, shallower and fuzzier.

Limitations and next steps: STEP/IGES import (would need occt-import-js), lasers that don't fire straight down or rotary axes, heightmap export, and splitting the 720 kB bundle into smaller chunks.

## KNOWN ISSUES
- Helical interpolation is lazy - we need to ensure it checks at the lowest Z value (i.e., the destination) and not anywhere else.  Right now, the helical interpolation is only being applied in X and Y.  This shouldn't matter for normal 3 axis verifications, but it's worth noting.

## Roadmap
1.  [COMPLETE] Get the kernel working.  Be able to simulate cuts and block state.
2.  Translate G-code to `XYZ format` and simulate physical part being machined
    a.  [COMPLETE] Arc helper for helical interpolation
3.  Check performance
4.  UI frontend
5.  Integration into production process (CI/CD)

## Performance
So, using `numpy` turned out to be too slow even still.

At roughly the 5_000 x 5_000 size, it took ~.4 seconds to remove a sample section.

In C, it took .4 seconds to load the entire array, and then remove the sample section.  There wasn't a noticeable amount of time to change the sample section.

I think we're going to create a Python extension to leverage the `Open3d` easy visualization.  

## Resources
- https://pythonspeed.com/articles/python-extension-performance/

- http://www.open3d.org/docs/latest/python_api/open3d.visualization.draw_geometries.html

- http://www.open3d.org/docs/release/tutorial/geometry/pointcloud.html

- http://www.open3d.org/docs/0.9.0/tutorial/Basic/working_with_numpy.html

- http://www.open3d.org/docs/0.14.1/python_api/open3d.visualization.RenderOption.html?highlight=renderoption

- http://www.open3d.org/docs/0.14.1/tutorial/visualization/customized_visualization.html?highlight=renderoption

- https://github.com/isl-org/Open3D/issues/3307