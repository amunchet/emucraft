# Emucraft
Emucraft is the child of Emu.  

The goal is to determine whether or not a collision occurs during a G-code program.

This is done through modelling of the block and resulting 3D approxmiation of the machining path, compared with the cutting tool information.

It will rely on `trimesh` for visualization.  Eventually, it may use `three.js` to visualize in a separate component.
