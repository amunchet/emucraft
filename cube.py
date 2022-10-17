#!/usr/bin/env python3
"""
Cube class and functions
    - Cubes are the atomic element of emucraft simulation.  A single cube of .001" x .001" x .001" represents the smallest amount of material defined
"""
from __future__ import annotations
from typing import List
class Cube:
    def __init__(self):
        maximum = [0,0,0] # X,Y,Z - maximum values - sorted for later
        minimum = [0,0,0] # X,Y,Z - minimum values

    
    def size(self):
        """Returns the size for the cube"""
        return [
            self.maximum[0] - self.minimum[0],
            self.maximum[1] - self.minimum[1],
            self.maximum[2] - self.minimum[2]
        ]
    
    def export(self):
        """Exports the cube to trimesh"""
    
    def cut(self, cutter: Cube) -> List(Cube):
        """
        Cuts the cube with another defined cube
        Returns array of resulting cubes (can be empty)
        """

        # First it must collide, if it doesn't, do nothing

        # Check if it fully annihilates this cube

        # Hole in the middle (No outer wall cut)

        # Partial wall cut

        # Cutting all the way through

    def collides_with(self, second: Cube) -> bool:
        """
        Checks if this cube collides with the provided cube
        ```
        return (
            a.minX <= b.maxX &&
            a.maxX >= b.minX &&
            a.minY <= b.maxY &&
            a.maxY >= b.minY &&
            a.minZ <= b.maxZ &&
            a.maxZ >= b.minZ
        );
        ```
        """
    



