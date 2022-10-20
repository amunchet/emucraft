# Emucraft
Emucraft is the child of Emu.  

The goal is to determine whether or not a collision occurs during a G-code program.

This is done through modelling of the block and resulting 3D approxmiation of the machining path, compared with the cutting tool information.

It will rely on `trimesh` for visualization.  Eventually, it may use `three.js` to visualize in a separate component.

Key improvements over Emu:

- Separation of Visual and Backend
- Full tests and coverage
- Easy API

## Roadmap
1.  Get the kernel of `cubes` working.  Be able to simulate cube interactions
2.  Translate G-code to `cubes` and simulate physical part being machined
3.  Scale up and determine collisions


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