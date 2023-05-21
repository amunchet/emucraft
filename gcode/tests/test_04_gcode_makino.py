#!/usr/bin/env python
"""
Tests Gcode functions

Output XYZ file format (from `functions.h`)
    [X] [Y] [Z] [Cutter Diameter] [Tool Holder Diameter] [Tool Holder Z (Bottom)] [MOVE TYPE - 0: non-cutting , 1: normal]
    XXXXX XXXXX XXXXX XXXXXX XXXXX XXXXX XXXXX
"""
import pytest
import os


import gcode_parser as gcode

# Test the helper function
def test_codes_parse():
    """
    Tests the coding parses
    """
    line = """
    G90G0X-1
    G91 G17 G40 
    G1 X12. Y13.33 F213M3
    M3 S2312
    M5
    M6T213
    M3S2343333G40G80
    m30
    """
    x = gcode.codes_parse(line)
    assert x[0] == [
        ("G", 90),
        ("G", 0),
        ("X",-1)
    ]

    assert x[1] == [
        ("G", 91),
        ("G", 17),
        ("G", 40)
    ]

    assert x[2] == [
        ("G", 1),
        ("X", 12.0),
        ("Y", 13.33),
        ("F", 213),
        ("M", 3)
    ]

    assert x[3] == [
        ("M", 3),
        ("S", 2312)
    ]
    assert x[4] == [
        ("M", 5)
    ]
    assert x[5] == [
        ("M", 6),
        ("T", 213)
    ]
    assert x[6] == [
        ("M", 3),
        ("S", 2343333),
        ("G", 40),
        ("G", 80)
    ]
    assert x[7] == [
        ("M", 30)
    ]


# Test the Class itself
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

    assert setup.tool_holder_diameter == 10.0
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
    
    NOTE: We are not implementing G91 at this time
    """
    # Unoptimized - rapid below 1.0
    test_parse_comments(setup)

    input = """
    N10 G90 G00 X10. Y10. Z0.99
    N20 G90 G01 X11. Y11. Z0
    N25 G90 G01 X13. Y13. Z1.0
    N30 
    """
    assert setup.parse_line(input)

    # Must remeber the offset
    # REMEMBER THE OFFSETS (to get block to start at (0,0,0))
    assert setup.lines[:4] == [    
        "0 0 10000 125 10000 10750 0",
        f"12800 10350 990 125 10000 {990 + 750} 0",
        f"13800 11350 990 125 10000 {990 + 750} 1",
        f"14800 13350 1000 125 10000 {1000 + 750} 1"
    ]

def test_g2(setup):
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

    # Half circle clockwise
    test_parse_comments(setup)
    input = """
    N20 G02 G02 X5. Y2. I3. J3. F20 
    """

    assert setup.block_offsets == {
        "x" : 2.8,
        "y": 0.35,
        "z" : 0
    }

    assert setup.parse_line(input)

    assert len(setup.lines) == 251
    assert setup.lines[0] == f"{2800-52} {350+53} 1000 1250 10000 {990 + 750} 0"
    assert setup.lines[-1] == f"{6813+2800} {1140+350} 1000 1250 10000 {990 + 750} 0"

def test_g3(setup):
    # Half circle counterclockwise
    test_parse_comments(setup)
    input = """
    N20 G02 G03 X5. Y2. I3. J3. F20 
    """
    assert setup.block_offsets == {
        "x" : 2.8,
        "y": 0.35,
        "z" : 0
    }

    assert setup.parse_line(input)

    assert len(setup.lines) == 109
    assert setup.lines[0] == f"2800 350 1000 1250 10000 {990 + 750} 0"
    assert setup.lines[-1] == f"{6780+2800} {1074 + 350} 1000 1250 10000 {990 + 750} 0"

def test_g17_g18_g19(setup):
    """
    G17/18/19 - plane select
    """

    # G2/G3 G17
    ## Do nothing

    input = "G17"
    assert setup.parse_line(input)


    # G2/G3 G18
    ## Throw an unimplemented error

    input = "G18"

    found = False  
    try:
        setup.parse_line(input)
    except gcode.NotImplementedException:
        found = True
    
    assert found

    # G2/G3 G19
    ## Throw an unimplemented error
    input = "G90G19G0"

    found = False  
    try:
        setup.parse_line(input)
    except gcode.NotImplementedException:
        found = True
    
    assert found

def test_g20_21(setup):
    """
    Inches/MM
    - Doesn't honestly make a difference to me
    """

    assert not setup.inches 
    assert not setup.mm

    ## Note it in setup
    lines = "G20"
    assert setup.parse_line(lines)
    assert setup.inches
    assert not setup.mm

    lines = "G21"
    assert setup.parse_line(lines)
    assert setup.mm 
    assert not setup.inches

def test_g28(setup):
    """
    Reference move - mainly to avoid the G28 G90 disaster.
    """

    # TODO FUTURE: Throw a fit if incorrect
    found = False
    lines = "G28"

    try:
        setup.parse_line(lines)
    except gcode.NotImplementedException:
        found = True
    
    assert found

def test_g40_g41_g42_g43(setup):
    """
    Cutter Comp
        - I guess we can ask for D values?
    """

    # Do Nothing for now
    lines = "G40G90G0"
    assert setup.parse_line(lines)

    lines = "G41G90G0"
    assert setup.parse_line(lines)

    lines = "G43G90G0"
    assert setup.parse_line(lines)

    lines = "G42G90G0"
    assert setup.parse_line(lines)

def test_g52(setup):
    """
    Temporary workplane offset
    """

    ## Throw an unimplemented

    found = False
    lines = "G52"

    try:
        setup.parse_line(lines)
    except gcode.NotImplementedException:
        found = True
    
    assert found

def test_g80_g81_g82_g83_g88(setup):
    """
    Canned Cycles
        - Basically treat as max depth cut
    """
    test_parse_comments(setup)

    ## Treat as max Z
    lines = "G90G1G81G99X10.Y10.Z0.1R5.0F30."
    assert setup.parse_line(lines)
    assert setup.lines[-1] == f"{10000 + 2800} {10000 + 350} 100 1250 10000 {990 + 750} 1" 

    lines = "G90G1G83G99X10.Y10.Z0.1R5.0F30."
    assert setup.parse_line(lines)
    assert setup.lines[-1] == f"{10000 + 2800} {10000 + 350} 100 1250 10000 {990 + 750} 1" 

def test_g90_g91(setup):
    """
    Absolute or relative
    """

    ## Test G90
    lines = "G43G91G0X0Y0"
    assert setup.parse_line(lines)

    ## Test G91 Mode
    ## Right now throw an Unimplemented exception
    found = False
    lines = "G43G91G0X0Y0"

    try:
        setup.parse_line(lines)
    except gcode.NotImplementedException:
        found = True
    
    assert found

def test_m6(setup):
    """
    Tool change
        - All following moves without initialization will have cutting move = 0
    """
    test_parse_comments(setup)
    lines = """
    G90G0X10Y10
    """
    assert setup.parse_line(lines)

    assert setup.lines[-1].split(" ")[-1] == "1"

    lines = """M6T17
    G90G0X7Y7
    G90G1X10Y12
    """
    assert setup.parse_line(lines)
    assert setup.lines[-1].split(" ")[-1] == "0"
    ## I guess we need to load in the new tool data information from comments

def test_m3_m5(setup):
    """
    Spindle On
        - Without this, cutting move = 0

    Spindle Off
        - If we have moves after this below the block Z plane, throw a fit
        - After this, all cutting moves = 0, until the spindle is turned back on

    """
    test_parse_comments(setup)
    ## Note that spindle is on in the collision detection
    lines = """
    M3
    G90G0X12Y12
    """
    assert setup.parse_line(lines)
    assert setup.lines[-1].split(" ")[-1] == "0" # Since we're in rapid

    lines = """
    G90G1X17Y17F17.
    """
    assert setup.parse_lines(lines)
    assert setup.lines[-1].split(" ")[-1] == "1" # Since it's a cutting move

    lines = """M5
    G90G1X19Y19F23
    """

    assert setup.parse_lines(lines)
    assert setup.lines[-1].split(" ")[-1] == "0" # Since spindle is off



def test_m30(setup):
    """
    Program end
    """
    lines = """
    M30
    G90G0X0Y10Z5
    G1X10.Y15.
    """

    assert setup.parse_line(lines)
    assert setup.lines == []

    ## Exit if we're not done

# Tests writing out the arrays
def test_output_xyz(setup):
    """
    Tests outputting the XYZ file
    """