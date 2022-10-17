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