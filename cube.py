#!/usr/bin/env python3
"""
Cube class and functions
    - Cubes are the atomic element of emucraft simulation.  A single cube of .001" x .001" x .001" represents the smallest amount of material defined
"""
from __future__ import annotations
from typing import List
class Cube:
    def __init__(self):
        verticies = [
            (0,0), # Corner 1
            (0,0), # Corner 2
            (0,0), # 3
            (0,0), # 4
            (0,0), # Corner 5
            (0,0), # Corner 6
            (0,0), # 7
            (0,0), # 8
        ]
    
    def maxZ(self):
        """Returns the Maximum Z value for the cube"""
    
    def export(self):
        """Exports the cube to trimesh"""
    
    def cut(self, cutter: Cube) -> List(Cube):
        """
        Cuts the cube with another defined cube
        Returns array of resulting cubes (can be empty)
        """
    def collides_with(self, second: Cube) -> bool:
        """
        Checks if this cube collides with the provided cube
        """
    



