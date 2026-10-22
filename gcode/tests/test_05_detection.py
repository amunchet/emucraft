#!/usr/bin/env python
"""
Tests comment detection of tooling and block values
"""
import copy

import gcode_parser as gcode


def detect(line, settings=gcode.DEFAULT_DETECTION):
    return {field: value for (field, value, how) in gcode.detect_comment(line, settings)}


def test_detect_common_formats():
    """
    Labels are matched regardless of case, separator, abbreviation dots and leading `.`
    """
    assert detect("N260( DIAMETER: 0.37500)") == {"tool_diameter": 0.375}
    assert detect("(Tool Holder Diameter: 10.0)") == {"tool_holder_diameter": 10.0}
    assert detect("(TOOL HOLDER DIA. 1.25)") == {"tool_holder_diameter": 1.25}
    assert detect("(flute length = .75)") == {"cutting_length": 0.75}
    assert detect("(LOC .5\")") == {"cutting_length": 0.5}
    assert detect("(Shank Diameter: 5)") == {"shank_diameter": 5.0}
    assert detect("N120( MIN X: -2.80015)") == {"block_x_min": -2.80015}
    assert detect("#127 = 3.9216 (REGOFIX TOOL HOLDER LENGTH)") == {"tool_holder_length": 3.9216}


def test_detect_does_not_cross_match():
    """
    Similar labels must not land on the wrong field
    """
    assert detect("(MAX X: 1.0)") == {"block_x_max": 1.0}
    assert detect("(SHANK DIA: .5)") == {"shank_diameter": 0.5}
    assert detect("(STEPOVER: 0.125)") == {}
    assert detect("(TOLERANCE:0.00030)") == {}
    assert detect("(Tool Id: .375 OSG Exocarb 1.4 length)") == {}
    assert detect("#129 = #127 + #128 (THEORETICAL SAFE HEIGHT)") == {}


def test_detect_fuzzy_threshold():
    """
    Typos match at the default threshold, but not when the threshold demands exact labels
    """
    assert detect("(GUAGE LENGTH: 5.2)") == {"gauge_length": 5.2}

    strict = copy.deepcopy(gcode.DEFAULT_DETECTION)
    strict["fuzz_threshold"] = 1.0
    assert detect("(GUAGE LENGTH: 5.2)", strict) == {}


def test_detect_units():
    """
    Explicit unit suffixes are converted into the configured units
    """
    assert detect("(SHANK DIA: 12.7mm)") == {"shank_diameter": 0.5}

    metric = copy.deepcopy(gcode.DEFAULT_DETECTION)
    metric["units"] = "mm"
    assert detect("(SHANK DIA: 0.5in)", metric) == {"shank_diameter": 12.7}
    assert detect("(SHANK DIA: 6)", metric) == {"shank_diameter": 6.0}


def test_custom_settings():
    """
    New aliases and patterns are picked up by the Program
    """
    settings = copy.deepcopy(gcode.DEFAULT_DETECTION)
    settings["labels"]["tool_holder_length"].append("holder gauge")
    settings["patterns"]["cutting_length"] = [r"#140\s*=\s*{num}"]

    program = gcode.Program(detection=settings)
    program.parse_line("(HOLDER GAUGE: 2.5)\n#140 = 1.1\nM30")

    assert program.tool_holder_length == 2.5
    assert program.cutting_length == 1.1
    assert program.detections["cutting_length"]["line"] == "#140 = 1.1"


def test_block_set_when_program_ends_with_m30():
    """
    M30 stops parsing but the block helpers must still run
    """
    program = gcode.Program()
    program.parse_line("(MIN X: -1)\n(MAX X: 1)\n(MIN Y: 0)\n(MAX Y: 2)\n(MIN Z: 0)\n(MAX Z: 1)\nM30\nG0 X5.")

    assert program.block == {"x": (-1, 1), "y": (0, 2), "z": (0, 1)}
    assert program.current["X"] == 0  # Nothing after M30 is run
