#!/usr/bin/env python
"""
Tests Gcode functions
"""
import pytest

import gcode

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
    N120( MIN X: -2.80015)
    N130( MIN Y: -0.34666)
    N140( MIN Z: 0.00000)
    N150( MAX X: 1.19985)
    N160( MAX Y: 4.65334)
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

    # TODO: Block

    # TODO: Starting point

def test_g0(setup):
    """
    Tests G0 (Rapid)
        - Want optimized for above block height
    """
    # Unoptimized - rapid below 1.0

    input = """
    N10 G01 X10. Y10. Z0.99
    N20 G00 X11. Y11. Z0.99
    N30 
    """
    assert setup.parse_line(input)

    assert setup.lines[:3] == [    
    ]

    # Optimized - rapid above 1.0
    #   - Check with a XYZ component as well


def test_g1(setup):
    """
    Tests G1 (Normal)
    """

def test_g2_g3(setup):
    """
    G2/G3 - Helical interpolation
    """

def test_g17_g18_g19(setup):
    """
    G17/18/19 - plane select
    """

def test_g20_21(setup):
    """
    Inches/MM
    """

def test_g28(setup):
    """
    Reference move - mainly to avoid the G28 G90 disaster.
    """

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