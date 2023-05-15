#!/usr/bin/env python3
"""
Linear interpolation code
"""
import numpy as np
"""
ChatGPT: generate a python function that accepts the starting point (x,y,z), the ending point (x,y,z) and the step amount and generates a list of points going from starting to ending with step value of the step amount
"""

def generate_points(start, end, step_size):
    distance = np.linalg.norm(np.array(end) - np.array(start))
    num_steps = int(np.ceil(distance / step_size))

    if num_steps <= 1:
        return [start, end]

    points = []
    for i in range(num_steps):
        t = i / (num_steps - 1)
        point = tuple((1 - t) * np.array(start) + t * np.array(end))
        points.append(point)

    return points

