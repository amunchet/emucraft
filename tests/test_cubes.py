#!/usr/bin/env python3
"""
Tests cube functions
"""
import trimesh
from pytest import fixture
from cube import Cube

@fixture
def setup():
    """
    Setup function
    """

def test_size():
    """
    Tests Returning cube size
    """

def test_export():
    """
    Tests Export

    ```
    >>> c = trimesh.load_mesh("toy.stl")
    >>> c
    <trimesh.Trimesh(vertices.shape=(16, 3), faces.shape=(24, 3))>
    >>> c.bounding_box
    <trimesh.primitives.Box>
    >>> c.bounding_box()
    Traceback (most recent call last):
    File "<stdin>", line 1, in <module>
    TypeError: 'Box' object is not callable
    >>> c.bounds
    array([[-0.5, -2. , -2.5],
        [ 4.5,  2. ,  2.5]])
        ```

    """


def test_cut():
    """
    Tests cut
    """

def test_collides_with():
    """
    Test collides with
    """