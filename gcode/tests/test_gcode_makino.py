#!/usr/bin/env python
"""
Tests Gcode functions

Output XYZ file format (from `functions.h`)
    [X] [Y] [Z] [Cutter Diameter] [Tool Holder Diameter] [Tool Holder Z (Bottom)] [MOVE TYPE - 0: rapid, 1: normal]
    XXXXX XXXXX XXXXX XXXXXX XXXXX XXXXX XXXXX
"""
import pytest
import os


import gcode.gcode_parser as gcode

@pytest.fixture
def setup():
    """
    Sets up the Gcode system
    """
    a = gcode.Program()

    yield a

    return "Done"

def test_parse_comments(setup):
    """
    Need to determine the Cutter radius and geometry from the comments
    """

    input = """

    (THIS ISNT IN POST YET)
    (Tool Holder Diameter: 10.0)
    
    (This is to be subtracted from the guage length to find the tool holder length)
    N130( Gauge Length: 4.558)

    N290( DIAMETER: 0.12500)
    
    N110( Block:)
    N120( MIN X: -2.8)
    N130( MIN Y: -0.35)
    N140( MIN Z: 0.00000)
    N150( MAX X: 1.195)
    N160( MAX Y: 4.65)
    N170( MAX Z: 1.75000)


    (GUAGE LENGTH)
    #128 = 0.75

    #127 = 3.9216 (REGOFIX TOOL HOLDER LENGTH)
    #129 = #127 + #128 (THEORETICAL SAFE HEIGHT)
    #130 = #11001 (CURRENT H VALUE)
    IF [ #130 LE #129 ] THEN #3000 = 140
    (SPINDLE START)
    """
    assert setup.parse_line(input)

    assert setup.tool_holder_diameter == 10
    assert setup.tool_holder_length == 3.9216
    assert setup.tool_diameter == 0.125
    assert setup.tool_length == 0.75

    # Block
    # We need to offset so that the block start is at (0,0)
    assert setup.block == {
        "x" : (-2.8, 1.195),
        "y" : (-0.35, 4.65),
        "z" : (0, 1.75)
    }

    assert setup.block_offsets == {
        "x" : 2.8,
        "y": 0.35,
        "z" : 0
    }

    # Starting point
    # I think I'll default to something safe like (0,0,10)

    assert setup.starting_point == (0,0,10)

def test_g0_g1(setup):
    """
    Tests G0 (Rapid)
        - Want optimized for above block height
    """
    # Unoptimized - rapid below 1.0

    input = """
    N10 G90 G00 X10. Y10. Z0.99
    N20 G91 G01 X1. Y1. Z0
    N25 G90 G01 X13. Y13. Z1.0
    N30 
    """
    assert setup.parse_line(input)

    # Must remeber the offset
    # REMEMBER THE OFFSETS (to get block to start at (0,0,0))
    assert setup.lines[:4] == [    
        "0 0 10000 1250 10000 10750 0",
        f"12800 10350 990 1250 10000 {990 + 750} 0",
        f"13800 11350 990 1250 10000 {990 + 750} 1",
        f"14800 13350 1000 1250 10000 {1000 + 750} 1"
    ]

def test_g2_g3(setup):
    """
    G2/G3 - Helical interpolation
    
    G02 - Clockwise
    G03 - Counter clockwise

    IJK - INCREMENTAL from start point to center of circle
        - IJK takes precedence over the ending point.  If the ending point is beyond the radius of the circle, it'll try to get there, but respect the circle's radius above all.

    R - Radius value of circle (have to figure out center point on own)

    So this is annoying - I'll need to convert each arc move into linear moves.

    To do this, I'll need to figure out the angle to move that will be .001", then do the little line segments connecting them.

    https://ncviewer.com/ is your friend
    """

def test_g17_g18_g19(setup):
    """
    G17/18/19 - plane select
    """

    # G2/G3 G17

    # G2/G3 G18

    # G2/G3 G19

def test_g20_21(setup):
    """
    Inches/MM
    - Doesn't make a difference to me
    """

def test_g28(setup):
    """
    Reference move - mainly to avoid the G28 G90 disaster.
    """

    # TODO FUTURE: Throw a fit if incorrect

def test_g40_g41_g42_g43(setup):
    """
    Cutter Comp
        - I guess we can ask for D values?
    """

def test_g52(setup):
    """
    Temporary workplane offset
    """

def test_g80_g81_g82_g83_g88(setup):
    """
    Canned Cycles
        - Basically treat as max depth cut
    """

def test_g90_g91(setup):
    """
    Absolute or relative
    """

def test_m6(setup):
    """
    Tool change
    """

def test_m3(setup):
    """
    Spindle On
    """

def test_m5(setup):
    """
    Spindle Off
        - If we have moves after this below the block Z plane, throw a fit
    """

def test_m30(setup):
    """
    Program end
    """

# Tests writing out the arrays
def test_output_xyz(setup):
    """
    Tests outputting the XYZ file
    """