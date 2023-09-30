# Emucraft Fixturing
The goal here it to allow fixture models to be imported.  

Similar to how the tool holder (and presumably spindle) are calculated as "second" (non-cutting) tools to determine collision, so the fixture will be calculated as a "second" block - which, if cut, produces an error.

Steps:
- [COMPLETE] Need to bring in STL
- Align the fixture STL with the block for cutting
- [COMPLETE] Simulate the height map of the fixture STL - save to block file
- Run the kernel with cutting on both the normal block and the fixture block.  If the fixture block is cut at all, then a collision occurs
- Simulation will just draw the second block (the fixture) and pause if there's a collision

How to use the toys:
- `toy.py` - this renders the `kurt.stl` to the height map (toy.block) file 
- `toy_visualize.py` - renders the block with the visualization system
- `toy_sphere_stl.py` - makes a sphere STL that can be used with `toy.py`