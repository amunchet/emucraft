#!/usr/bin/env python
"""
Tests Gcode Line Parse

Output XYZ file format (from `functions.h`)
    [X] [Y] [Z] [Cutter Diameter] [Tool Holder Diameter] [Tool Holder Z (Bottom)] [MOVE TYPE - 0: non-cutting , 1: normal]
    XXXXX XXXXX XXXXX XXXXXX XXXXX XXXXX XXXXX
"""
import pytest
import os


import gcode_parser as gcode

# Test the Class itself
@pytest.fixture
def setup():
    """
    Sets up the Gcode system
    """
    a = gcode.Program()

    # TODO: Set up the current positions in the class

    a.current = {
        "G" : [90, 0],
        "M" : [],
        "X": 0,
        "Y" : 0,
        "Z" : 10,
        "H" : 1,
        "I" : None,
        "J" : None,
        "K" : None,
        "F" : 100,
        "S": 10000,
        "D" : None,
        "T" : 6
    }
    yield a

    return "Done"


def test_line_parse_linear_move_cutting(setup):
    """
    Tests the generation of the linearized points from given G-Codes

    Linear move - cutting
    """

    lines = """
    G90 G1 Z4. F30.
    X10. Y10. F40.
    """
    
    minimum_step = 0.1


    # TODO: Feed in the G-code line - linearization will be created relative to current position
    setup.parse_line(lines, minimum_step=0.1)

    # TODO: Return the expected lines
    assert len(setup.lines) == 2
    assert setup.lines == [

    ]


    # TODO: Test each axis independently, then all together
    assert False

def test_line_parse_linear_move_noncutting(setup):
    assert False

def test_line_parse_circular_move_ccw_cutting(setup):
    assert False

def test_line_parse_circular_move_ccw_noncutting(setup):
    assert False

def test_line_parse_circular_move_cw_cutting(setup):
    assert False

def test_line_parse_circular_move_cw_noncutting(setup):
    assert False

def test_line_parse_canned_cycles(setup):
    assert False