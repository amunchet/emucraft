# Emucraft Fixturing
The goal here it to allow fixture models to be imported.  

Similar to how the tool holder (and presumably spindle) are calculated as "second" (non-cutting) tools to determine collision, so the fixture will be calculated as a "second" block - which, if cut, produces an error.

Steps:
- Need to bring in STL
- Align the fixture STL with the block for cutting
- Simulate the height map of the fixture STL - save to block file
- Run the kernel with cutting on both the normal block and the fixture block.  If the fixture block is cut at all, then a collision occurs
- Simulation will just draw the second block (the fixture) and pause if there's a collision