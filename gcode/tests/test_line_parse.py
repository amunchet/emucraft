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

    yield a

    return "Done"


def test_line_parse_linear_move_cutting(setup):
    """
    Tests the generation of the linearized points from given G-Codes

    Linear move - cutting
    """


    # TODO: Feed in the G-code line - linearization will be created relative to current position

    # TODO: Return the expected lines


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