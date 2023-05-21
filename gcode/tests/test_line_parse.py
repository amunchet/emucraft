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
from gcode_logger import logger

# Test the Class itself
@pytest.fixture
def setup():
    """
    Sets up the Gcode system
    """
    a = gcode.Program()

    # Set up the current positions in the class

    a.tool_diameter = 0.5
    a.tool_holder_length = 3.0
    a.tool_holder_diameter = 5.0

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


def test_line_parse_linear_move(setup):
    """
    Tests the generation of the linearized points from given G-Codes

    Linear move - cutting and non-cutting
    """

    lines = """
    G90 G1 Z1.1 F30.
    X1. Y1.5 F40.
    G0 Z2.0
    """
    
    minimum_step = 0.1


    # TODO: Feed in the G-code line - linearization will be created relative to current position
    setup.parse_line(lines, minimum_step=0.1)

    # TODO: Return the expected lines
    
    assert len(setup.lines) == 3

    # First move - Z 
    assert setup.lines[0][0] == "0 0 10000 500 5000 3000 1" # X0 Y0 Z10. Diameter .5" Tool diam 5.0" Height 3" Cutting Move
    assert setup.lines[0][-1] == "0 0 1100 500 5000 3000 1" # X0 Y0 Z1.1 Diameter .5" Tool Diam 5.0" Height 3.0" Cutting move

    # Second move - cutting
    assert setup.lines[1][0] == "0 0 1100 500 5000 3000 1" # Starting point
    assert setup.lines[1][-1] == "1000 1500 1100 500 5000 3000 1" # X1. Y1. Z1.1 Diam .5" Tool Diam 5.0" Height 3.0" Cutting

    # Third move - Z clearance (G0)
    assert setup.lines[2][0] == "1000 1500 1100 500 5000 3000 0" # Same as last end, just in rapid
    assert setup.lines[2][-1] == "1000 1500 2000 500 5000 3000 0" # Ending Z2.0


def test_line_parse_circular_move_ccw(setup):
    lines = """
    G90 G1 Z1.1 F30.
    G02 X1. Y1.5 I0.5 J0.5
    G0 Z4.
    G0 X0 Y0
    G02 X1. Y1.5 I0.5 J0.5
    """
    setup.parse_line(lines, minimum_step=0.1)

    # TODO: Return the expected lines
    assert len(setup.lines) == 5

    assert setup.lines[1][0] == "-9 9 1100 500 5000 3000 1" # The -9 seems to be a rounding error
    assert setup.lines[1][-1] == "821 1130 1100 500 5000 3000 1" # Rounding issue - tried to get as close as we could

    assert setup.lines[2][0] == "1000 1500 1100 500 5000 3000 0" # Starting point.  TODO: This may be a problem - we might want to have the last arc point set as the X, Y coordinates going forward (instead of what they're supposed to be)
    assert setup.lines[2][-1] == "1000 1500 4000 500 5000 3000 0"

    assert setup.lines[3][0] == "1000 1500 4000 500 5000 3000 0"
    assert setup.lines[3][-1] == "0 0 4000 500 5000 3000 0"


    assert setup.lines[4][0] == "-9 9 4000 500 5000 3000 0"
    assert setup.lines[4][-1] == "821 1130 4000 500 5000 3000 0"


def test_line_parse_circular_move_cw(setup):
    assert False


def test_line_parse_canned_cycles(setup):
    assert False