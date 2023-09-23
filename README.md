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

## KNOWN ISSUES
- Helical interpolation is lazy - we need to ensure it checks at the lowest Z value (i.e., the destination) and not anywhere else.  Right now, the helical interpolation is only being applied in X and Y.  This shouldn't matter for normal 3 axis verifications, but it's worth noting.

## Roadmap
1.  [COMPLETE] Get the kernel working.  Be able to simulate cuts and block state.
2.  [COMPLETE] Translate G-code to `XYZ format` and simulate physical part being machined
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