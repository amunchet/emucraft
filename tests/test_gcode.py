"""G-code interpreter tests.

Scenarios from the legacy parser tests (git show 71f1ae4:gcode/tests/...) are
ported here with correct expectations: the old tests encoded rounding errors
and arcs whose end points were not on their circles.
"""

import math

import pytest

from emucraft.gcode import (
    ARC,
    CYCLE,
    EDIT_FEED,
    EDIT_LOCKED,
    EDIT_NONE,
    EDIT_SPLIT,
    RAPID,
    SPINDLE,
    Interpreter,
    dogleg,
    evaluate_expression,
    parse,
    split_comments,
)
from helpers import EXAMPLES, codes, ends, moves

STOCK = "( MIN X: 0.)\n( MIN Y: 0.)\n( MIN Z: -1.)\n( MAX X: 4.)\n( MAX Y: 3.)\n( MAX Z: 0.)\n"
START = "T1 M6\nS1000 M3\nG0 X0 Y0 Z1\n"  # spindle on, tool at (0, 0, 1)


def message(program, code):
    return next(m for m in program.messages if m.code == code)


# ------------------------------------------------------------------ tokens


def test_split_comments():
    assert split_comments("N260( DIAMETER: 0.37500)") == ("N260 ", [" DIAMETER: 0.37500"])
    assert split_comments("G1 X1 ; to the end") == ("G1 X1  ", [" to the end"])
    code, comments = split_comments("(A)G0(B)X1")
    assert code.split() == ["G0", "X1"] and comments == ["A", "B"]
    assert split_comments("G0 X1 (unclosed")[1] == ["unclosed"]


def test_packed_and_spaced_words_parse_the_same():
    packed = parse("T1M6\nS100M3\nG0X0Y0Z1\nG1X1.Y-.5Z.25F12.5\n")
    spaced = parse("T1 M6\nS100 M3\nG0 X0 Y0 Z1\nG1 X 1. Y -.5 Z .25 F 12.5\n")
    assert moves(packed) == moves(spaced)
    assert moves(packed)[-1][1] == (1.0, -0.5, 0.25)
    assert moves(packed)[-1][3] == 12.5


def test_unreadable_text_is_an_error_not_a_move():
    p = parse(START + "G1 X1 F10\nG1 X@2\n")
    assert codes(p, "error") == ["syntax"]
    assert ends(p) == [(1.0, 0.0, 1.0)]


def test_duplicate_word_warns_and_uses_the_last():
    p = parse(START + "G0 X1 X2\n")
    assert "duplicate-word" in codes(p, "warning")
    assert ends(p)[-1] == (2.0, 0.0, 1.0)


# ------------------------------------------------------------------ CAM header notes


def test_makino_header(makino_text):
    p = parse(makino_text, "makino")
    assert p.units == "inch"
    assert p.stock_note == {
        "xmin": -2.80015, "ymin": -0.34666, "zmin": 0.0,
        "xmax": 1.19985, "ymax": 4.65334, "zmax": 1.75,
    }
    assert p.slot_tools == [20]
    note = p.tool_notes[20].values
    assert note["diameter"] == 0.375
    assert note["holder_diameter"] == 10.0  # the holder comment is not the cutter diameter
    assert note["stickout"] == 1.4  # "#128 = 1.4" and "Recommended length: 1.40000"
    assert note["holder_length"] == 3.9216  # "#127 = 3.9216"
    assert note["flutes"] == 1
    assert note["kind"] == "End Mill"
    assert note["name"] == ".375 OSG Exocarb 1.4 length"
    assert p.tool_point == "tip"
    assert p.tool_changes == 1
    assert p.dwell == pytest.approx(310.0)  # /G04 X300. (block skip off) + G04 X10.
    assert p.end_line == 3124  # M30, 0-based
    assert p.n_moves == 7448


def test_makino_messages_and_motion(makino_text):
    p = parse(makino_text)
    found = {(m.level, m.code, m.line + 1 if m.line is not None else None, m.count) for m in p.messages}
    assert ("warning", "macro-call", 15, 1) in found  # G65 P9624 probe call
    assert ("warning", "macro-flow", 26, 1) in found  # IF [#130 LE #129] ...
    assert ("warning", "h-mismatch", 77, 1) in found  # G43 H01 with T20
    assert ("warning", "vacant", 352, 9) in found  # M#150, nine times
    assert ("warning", "macro-call", 3124, 1) in found
    assert not codes(p, "error")
    # the first move comes down from above the stock (MAX Z 1.75 + 1 in clearance)
    assert p.point(0) == (0.7226, -0.0226, 2.75)
    assert p.point(1) == (0.7226, -0.0226, 1.95)
    assert p.flags[0] == RAPID | SPINDLE
    # G04 X300. is a dwell, not a move to X300; G65 ... Y-.169 is not a move either
    xs, ys, zs = p.points[0::3], p.points[1::3], p.points[2::3]
    assert max(xs) < 1.2 and min(xs) > -2.8
    assert -0.169 not in ys
    assert min(zs) == pytest.approx(1.65) and max(zs) == pytest.approx(2.75)
    assert sum(1 for f in p.flags if f & ARC) == 4838


def test_legacy_comment_block():
    """Port of test_04_gcode_makino.test_parse_comments."""
    text = """
    (THIS ISNT IN POST YET)
    (Tool Holder Diameter: 10.0)
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
    interp = Interpreter()
    p = interp.run(text)
    # no tool is selected yet, so the notes go to T0
    assert p.tool_notes[0].values == {
        "holder_diameter": 10.0, "diameter": 0.125, "stickout": 0.75, "holder_length": 3.9216,
    }
    assert p.stock_note == {"xmin": -2.8, "ymin": -0.35, "zmin": 0.0, "xmax": 1.195, "ymax": 4.65, "zmax": 1.75}
    assert interp.vars[129] == pytest.approx(4.6716)
    assert 130 not in interp.vars  # #11001 is a system variable we do not know
    assert codes(p, "warning") == ["macro-flow", "no-moves"]
    assert p.n_moves == 0


def test_holder_comment_does_not_set_cutter_diameter():
    p = parse("T3 M6\n(Tool Holder Diameter: 2.5)\n(HOLDER DIA: 2.5)\n")
    assert p.tool_notes[3].values == {"holder_diameter": 2.5}


def test_fusion_and_mastercam_tool_comments():
    p = parse(
        "(T1 D=0.25 CR=0.03 TAPER=118deg - ZMIN=-0.5 - bull nose end mill)\n"
        "(T3 D=0.5 CR=0. - ZMIN=-1. - flat end mill)\n"
        "(T4 D=6. - drill)\n"
        "(TOOL - 7 DIA. OFF. - 7 LEN. - 7 DIA. - .5)\n"
    )
    notes = {k: v.values for k, v in p.tool_notes.items()}
    assert notes[1] == {"diameter": 0.25, "corner_radius": 0.03, "tip_angle": 118.0, "kind": "bull nose end mill"}
    assert notes[3] == {"diameter": 0.5, "corner_radius": 0.0, "kind": "flat end mill"}
    assert notes[4] == {"diameter": 6.0, "kind": "drill"}
    assert notes[7] == {"diameter": 0.5}
    assert p.tool_notes[1].lines["diameter"] == 0


def test_generic_tool_comments_follow_the_selected_tool():
    p = parse(
        "T9 M6\n(TOOL DIA: 0.625)\n(STICKOUT = 1.75)\n(FLUTE LENGTH: 0.8)\n"
        "(SHANK DIAMETER: 0.5)\n(TIP ANGLE: 90)\n(CORNER RADIUS: 0.06)\n"
        "(Tool Number: 12)\n( DIAMETER: 0.25)\n"
    )
    assert p.tool_notes[9].values == {
        "diameter": 0.625, "stickout": 1.75, "flute_length": 0.8,
        "shank_diameter": 0.5, "tip_angle": 90.0, "corner_radius": 0.06,
    }
    # "Tool Number: 12" redirects the following notes
    assert p.tool_notes[12].values == {"diameter": 0.25}


def test_program_notes_units_and_tool_point():
    p = parse("( Units: MM)\n( Tool Coordinates: Centre)\n")
    assert p.units == "mm"
    assert p.tool_point == "center"


def test_custom_comment_rules():
    interp = Interpreter(comment_rules=[(r"HOLDER DIA\s*=\s*([\d.]+)", "tool.holder_diameter")])
    p = interp.run("T2 M6\n(HOLDER DIA = 1.25)\n")
    assert p.tool_notes[2].values == {"holder_diameter": 1.25}


# ------------------------------------------------------------------ macros


@pytest.mark.parametrize("text, value", [
    ("1+2*3", 7.0),
    ("[1+2]*3", 9.0),
    ("SIN[30]", 0.5),
    ("COS[60]", 0.5),
    ("ATAN[1]/[1]", 45.0),
    ("ATAN[1]/[-1]", 135.0),
    ("SQRT[16]", 4.0),
    ("ABS[-3]", 3.0),
    ("ROUND[2.5]", 3.0),
    ("ROUND[-2.5]", -3.0),
    ("FIX[-2.7]", -2.0),
    ("FUP[2.1]", 3.0),
    ("10 MOD 3", 1.0),
    ("-#1", -2.0),
    ("2*-#1", -4.0),
    ("#[#1+125]", 3.9216),
    ("#127+#128", 5.3216),
])
def test_expressions(text, value):
    variables = {1: 2.0, 127: 3.9216, 128: 1.4}
    assert evaluate_expression(text, variables) == pytest.approx(value)


@pytest.mark.parametrize("text", ["#5", "#0", "#1/#5", "#5+1"])
def test_vacant_variables(text):
    assert evaluate_expression(text, {1: 2.0}) is None


@pytest.mark.parametrize("text", ["2/0", "1+", "SIN 30", "1 OR 2", "[1+2", "1 2"])
def test_bad_expressions(text):
    with pytest.raises(ValueError):
        evaluate_expression(text, {})


def test_macro_variables_in_words():
    p = parse("#100 = 2.\nN10 #101 = [#100 * 1.5]\nT1 M6\nG0 X#100 Y#101 Z[1+SQRT[4]]\nG0 X-#100\n")
    assert ends(p) == [(-2.0, 3.0, 3.0)]
    # macro lines are never edited by the optimizer
    p = parse(START + "#1 = 20.\nG1 X1 F#1\n")
    assert p.feed[-1] == 20.0
    assert p.line_edit[4] == EDIT_LOCKED


def test_macro_statements_and_alarms():
    p = parse(START + "IF [#1 EQ 0] GOTO 10\nWHILE [#1 LT 5] DO1\nG1 X1 F10\nM#150\n#3000 = 1 (TOOL BROKEN)\n")
    flow = message(p, "macro-flow")
    assert flow.level == "warning" and flow.count == 2 and flow.line == 3
    assert message(p, "vacant").text.startswith("M#150")
    alarm = message(p, "alarm")
    assert alarm.level == "error" and alarm.line == 7
    assert ends(p) == [(1.0, 0.0, 1.0)]


def test_indirect_assignment_and_vacant_values():
    interp = Interpreter()
    interp.run("#1 = 2\n#[#1+1] = 7\n#4 = #99\n")
    assert interp.vars == {1: 2.0, 3: 7.0}


def test_g65_blocks_are_ignored_entirely():
    p = parse("T5 M6\nS100 M3\nG0 X0 Y0 Z1\nG65 P9810 X5. Y5. T9\nG65P9624D1H1T1Y-.169\nG1 X1 F10\n")
    assert p.slot_tools == [5]
    assert ends(p) == [(1.0, 0.0, 1.0)]
    call = message(p, "macro-call")
    assert call.text == "Macro call G65 P9810 is not simulated"


# ------------------------------------------------------------------ dwell, units, positioning


def test_dwell_is_not_motion():
    p = parse("T1 M6\nG0 X0 Y0 Z1\nG4 X2.5\nG04 P500\nG0 X1\n")
    assert p.dwell == pytest.approx(3.0)
    assert ends(p) == [(1.0, 0.0, 1.0)]


def test_units_and_mid_program_switch():
    p = parse("G21\nT1 M6\nG0 X10 Y10 Z10\nG1 X20 F100\nG20\nG1 X1 F10\n")
    assert p.units == "mm"
    assert ends(p) == [(20.0, 10.0, 10.0), (25.4, 10.0, 10.0)]
    assert list(p.feed) == [100.0, pytest.approx(254.0)]
    # lines in the other unit system are locked for the optimizer
    assert p.line_edit[3] == EDIT_SPLIT and p.line_edit[5] == EDIT_LOCKED


def test_g20_g21_legacy():
    """Port of test_g20_21: G20 is inch, G21 millimetres; units follow the first one."""
    assert parse("G20").units == "inch"
    assert parse("G21").units == "mm"
    assert parse("").units == "inch"  # default
    assert parse("", default_units="mm").units == "mm"
    assert parse("G21", units="inch").units == "inch"  # forced


def test_absolute_and_incremental():
    p = parse("T1 M6\nG0 X0 Y0 Z1\nG91 G0 X1 Y1\nX1\nG90 X0\n")
    assert ends(p) == [(1.0, 1.0, 1.0), (2.0, 1.0, 1.0), (0.0, 1.0, 1.0)]


def test_g52_local_offset():
    p = parse("T1 M6\nG0 X0 Y0 Z1\nG52 X10 Y5\nG0 X1 Y1\nG52 X0 Y0\nG0 X1 Y1\n")
    assert ends(p) == [(11.0, 6.0, 1.0), (1.0, 1.0, 1.0)]


def test_g28_incremental_retracts_straight_up():
    p = parse(STOCK + "T1 M6\nG0 X1 Y1 Z0.5\nG91 G28 Z0.\n")
    # home is the stock top (0) + 1 in
    assert ends(p) == [(1.0, 1.0, 0.5), (1.0, 1.0, 1.0)]


def test_g28_absolute_plunges_to_the_intermediate_point():
    """The 'G28 G90 disaster': Z0 is taken as a work coordinate first."""
    p = parse(STOCK + "T1 M6\nG0 X1 Y1 Z0.5\nG90 G28 Z0.\n")
    assert ends(p) == [(1.0, 1.0, 0.5), (1.0, 1.0, 0.0), (1.0, 1.0, 1.0)]
    assert all(f & RAPID for f in p.flags)


def test_g53_retract_and_unsupported_xy():
    p = parse(STOCK + "T1 M6\nG0 X1 Y1 Z0.5\nG53 G0 Z0.\nG53 X0 Y0\n")
    assert ends(p) == [(1.0, 1.0, 0.5), (1.0, 1.0, 1.0)]
    assert "machine-xy" in codes(p, "info")


def test_home_z_option():
    p = parse("T1 M6\nG0 X1 Y1 Z0.5\n", home_z=5.0)
    assert p.point(0) == (1.0, 1.0, 5.0)


def test_unknown_start_position():
    # an XY move first: the tool appears there, Z comes down from home later
    p = parse(STOCK + "T1 M6\nG0 X1 Y2\nG0 Z0.1\n")
    assert moves(p)[0][:2] == ((1.0, 2.0, 1.0), (1.0, 2.0, 0.1))
    assert "start-position" in codes(p, "info")


# ------------------------------------------------------------------ arcs


def radius_error(program, center, radius):
    return max(abs(math.hypot(x - center[0], y - center[1]) - radius) for x, y, _ in ends(program))


@pytest.mark.parametrize("body, center, sweep_deg", [
    ("G3 X0 Y1 I-1 J0", (0, 0), 90),
    ("G2 X0 Y1 I-1 J0", (0, 0), 270),
    ("G3 X0 Y1 R1", (0, 0), 90),
    ("G3 X0 Y1 R-1", (1, 1), 270),
    ("G2 X0 Y1 R1", (1, 1), 90),
    ("G2 X0 Y1 R-1", (0, 0), 270),
    ("G2 X1 Y0 I-1 J0", (0, 0), 360),
])
def test_arc_centers_and_sweeps(body, center, sweep_deg):
    p = parse("T1 M6\nS1000 M3\nG0 X1 Y0 Z0\n" + body + " F10\n")
    assert radius_error(p, center, 1.0) < 1e-9
    pts = [p.point(i) for i in range(p.n_moves + 1)]
    sweep = sum(
        abs(math.atan2(b[1] - center[1], b[0] - center[0]) - math.atan2(a[1] - center[1], a[0] - center[0])
            + math.pi) % (2 * math.pi) - math.pi
        for a, b in zip(pts, pts[1:])
    )
    assert math.degrees(abs(sweep)) == pytest.approx(sweep_deg, abs=1e-6)
    assert ends(p)[-1] == ((1.0, 0.0, 0.0) if sweep_deg == 360 else (0.0, 1.0, 0.0))
    assert all(f == ARC | SPINDLE for f in p.flags)
    assert not codes(p, "error")


def test_arc_direction():
    ccw = parse("T1 M6\nG0 X1 Y0 Z0\nG3 X0 Y1 I-1 J0 F10\n")
    cw = parse("T1 M6\nG0 X1 Y0 Z0\nG2 X0 Y1 I-1 J0 F10\n")
    assert ends(ccw)[0][1] > 0  # counter-clockwise from +X goes up
    assert ends(cw)[0][1] < 0  # clockwise goes down


def test_helical_arc():
    p = parse("T1 M6\nG0 X1 Y0 Z0\nG3 X0 Y1 Z-1 I-1 J0 F10\n")
    zs = [q[2] for q in ends(p)]
    assert zs == sorted(zs, reverse=True) and zs[-1] == -1.0
    # Z advances in proportion to the angle
    x, y, z = ends(p)[len(zs) // 2 - 1]
    angle = math.atan2(y, x)
    assert z == pytest.approx(-angle / (math.pi / 2), abs=1e-9)


def test_g18_and_g19_arcs():
    """Ports test_g17_g18_g19: other planes are simulated instead of rejected."""
    xz = parse("T1 M6\nG0 X1 Y0 Z0\nG18 G2 X0 Z1 I-1 K0 F10\n")
    assert not codes(xz, "error")
    assert all(abs(math.hypot(x, z) - 1) < 1e-9 and y == 0 for x, y, z in ends(xz))
    mid = ends(xz)[len(ends(xz)) // 2]
    assert mid[0] > 0 and mid[2] > 0  # G2 seen from +Y: the short way round
    yz = parse("T1 M6\nG0 X0 Y1 Z0\nG19 G2 Y0 Z1 J-1 K0 F10\n")
    assert all(abs(math.hypot(y, z) - 1) < 1e-9 and x == 0 for x, y, z in ends(yz))
    mid = ends(yz)[len(ends(yz)) // 2]
    assert mid[1] < 0 and mid[2] < 0  # G2 seen from +X: the long way round


def test_arc_end_point_mismatch_is_an_error():
    """The legacy tests used arcs like this one; a control would alarm."""
    p = parse("T1 M6\nG0 X0 Y0 Z1.1\nG02 X1. Y1.5 I0.5 J0.5 F30.\n")
    err = message(p, "arc")
    assert err.level == "error" and "off the circle" in err.text
    assert ends(p)[-1] == (1.0, 1.5, 1.1)  # still ends where programmed


def test_arc_without_center_moves_linearly():
    p = parse("T1 M6\nG0 X1 Y0 Z0\nG2 X0 Y1 F10\n")
    assert message(p, "arc").level == "error"
    assert moves(p) == [((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), 0, 10.0, 3)]


def test_r_arc_too_small_is_an_error():
    p = parse("T1 M6\nG0 X0 Y0 Z0\nG2 X2 Y0 R0.5 F10\n")
    assert "smaller than half the chord" in message(p, "arc").text


def test_chord_tolerance():
    for tol in (0.001, 0.0002):
        p = parse("T1 M6\nG0 X1 Y0 Z0\nG3 X0 Y1 I-1 J0 F10\n", chord_tolerance=tol)
        pts = [p.point(i) for i in range(p.n_moves + 1)]
        sagitta = max(1 - math.hypot((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(pts, pts[1:]))
        assert tol / 2 < sagitta <= tol
    assert parse("T1 M6\nG0 X1 Y0 Z0\nG3 X0 Y1 I-1 J0 F10\n", chord_tolerance=0.001).n_moves < p.n_moves


def test_arc_absolute_centers():
    p = parse("T1 M6\nG0 X3 Y2 Z0\nG90.1 G3 X2 Y3 I2 J2 F10\n")
    assert radius_error(p, (2, 2), 1.0) < 1e-9


def test_legacy_circular_moves():
    """Port of test_line_parse_circular_move_cw with a valid arc: from (0, 0)
    around (1, 0) to (2, 0) counter-clockwise passes below the X axis."""
    p = parse(START + "G90 G1 Z0 F30.\nG03 X2.0 Y0.0 I1.0 J0.0\nG0 Z1.5\n")
    arc = [q for q, f in zip(ends(p), p.flags) if f & ARC]
    assert arc[-1] == (2.0, 0.0, 0.0)
    assert min(y for _, y, _ in arc) == pytest.approx(-1.0, abs=1e-3)
    assert p.feed[1] == 30.0  # modal F carries into the arc


# ------------------------------------------------------------------ canned cycles


def test_drilling_cycle_g98_returns_to_the_initial_level():
    p = parse(START + "G81 G98 X1 Y1 Z-0.5 R0.1 F10\nX2\nG80\nG0 Z2\n")
    assert moves(p) == [
        ((0.0, 0.0, 1.0), (1.0, 1.0, 1.0), RAPID | SPINDLE, 0.0, 4),
        ((1.0, 1.0, 1.0), (1.0, 1.0, 0.1), RAPID | SPINDLE, 0.0, 4),
        ((1.0, 1.0, 0.1), (1.0, 1.0, -0.5), CYCLE | SPINDLE, 10.0, 4),
        ((1.0, 1.0, -0.5), (1.0, 1.0, 1.0), RAPID | CYCLE | SPINDLE, 0.0, 4),
        ((1.0, 1.0, 1.0), (2.0, 1.0, 1.0), RAPID | SPINDLE, 0.0, 5),
        ((2.0, 1.0, 1.0), (2.0, 1.0, 0.1), RAPID | SPINDLE, 0.0, 5),
        ((2.0, 1.0, 0.1), (2.0, 1.0, -0.5), CYCLE | SPINDLE, 10.0, 5),
        ((2.0, 1.0, -0.5), (2.0, 1.0, 1.0), RAPID | CYCLE | SPINDLE, 0.0, 5),
        ((2.0, 1.0, 1.0), (2.0, 1.0, 2.0), RAPID | SPINDLE, 0.0, 7),
    ]
    assert p.line_edit[3] == EDIT_FEED


def test_drilling_cycle_g99_returns_to_r():
    p = parse(START + "G81 G99 X1 Y1 Z-0.5 R0.1 F10\nX2\nG80\n")
    assert [q for q in ends(p)] == [
        (1.0, 1.0, 1.0), (1.0, 1.0, 0.1), (1.0, 1.0, -0.5), (1.0, 1.0, 0.1),
        (2.0, 1.0, 0.1), (2.0, 1.0, -0.5), (2.0, 1.0, 0.1),
    ]


def test_incremental_cycle_with_repeats():
    p = parse(START + "G91 G81 X1 Y0 Z-0.6 R-0.9 K3 F10\nG80 G90\n")
    bottoms = [q for q, f in zip(ends(p), p.flags) if f & CYCLE and not f & RAPID]
    assert bottoms == [(1.0, 0.0, -0.5), (2.0, 0.0, -0.5), (3.0, 0.0, -0.5)]


def test_peck_and_bore_cycles():
    peck = parse(START + "G83 G98 X1 Y1 Z-1 R0.1 Q0.25 F10\nG80\n")
    assert peck.cycle_rapid == pytest.approx(2 * 4 * 1.1 / 2)  # 4 extra pecks back to R
    bore = parse(START + "G85 G99 X1 Y1 Z-1 R0.1 F10 P200\nG80\n")
    assert bore.dwell == pytest.approx(0.2)
    out = moves(bore)[-1]
    assert out[1] == (1.0, 1.0, 0.1) and not out[2] & RAPID and out[3] == 10.0  # bores out at feed


def test_legacy_canned_cycle_without_r():
    """Port of test_line_parse_canned_cycles: without R the initial level is used."""
    p = parse("T1 M6\nS1 M3\nG0 X0 Y0 Z10\nG90 G0 X1. Y1.\nG81 G98 Z2. F200.\nX2.\nY2.\n")
    drills = [(a, b) for a, b, f, _, _ in moves(p) if f & CYCLE and not f & RAPID]
    assert drills == [
        ((1.0, 1.0, 10.0), (1.0, 1.0, 2.0)),
        ((2.0, 1.0, 10.0), (2.0, 1.0, 2.0)),
        ((2.0, 2.0, 10.0), (2.0, 2.0, 2.0)),
    ]
    assert message(p, "cycle").level == "warning"


def test_legacy_cycle_in_a_g1_block():
    """Port of test_g80_g81_g82_g83_g88: G1 and G81 in one block drill."""
    p = parse("T1 M6\nS1 M3\nG0 X0 Y0 Z10\nG90G1G81G99X10.Y10.Z0.1R5.0F30.\nG90G1G83G99X10.Y10.Z0.2R5.0F30.\n")
    # G1 cancels the running cycle, the G8x after it in the same block starts a new one
    drills = [b for _, b, f, _, _ in moves(p) if f & CYCLE and not f & RAPID]
    assert drills == [(10.0, 10.0, 0.1), (10.0, 10.0, 0.2)]
    assert ends(p)[-1] == (10.0, 10.0, 5.0)  # G99: back to R


def test_cycle_without_depth_is_an_error():
    p = parse(START + "G81 X1 Y1 R0.1 F10\n")
    assert message(p, "cycle").level == "error"


# ------------------------------------------------------------------ rapids, spindle, tools


def test_dogleg_legs():
    assert dogleg((0, 0, 1), (2, 1, 0)) == [(1.0, 1.0, 0.0), (2, 1, 0)]
    assert dogleg((0, 0, 0), (1, 1, 1)) == [(1, 1, 1)]
    assert dogleg((0, 0, 0), (0, 0, 0)) == []
    p = parse("T1 M6\nG0 X0 Y0 Z1\nG0 X2 Y1 Z0\n", rapid_mode="dogleg")
    assert ends(p) == [(1.0, 1.0, 0.0), (2.0, 1.0, 0.0)]
    assert all(f & RAPID for f in p.flags)


def test_spindle_and_tool_changes():
    p = parse("T1 M6\nS1000 M3\nG0 X0 Y0 Z1\nG1 X1 F10\nM5\nG1 X2\nM3\nG1 X3\nT2 M6\nG1 X4\nS0 M3\n")
    assert [f & SPINDLE for f in p.flags] == [SPINDLE, 0, SPINDLE, 0]  # M6 stops the spindle
    assert list(p.speed) == [1000.0, 0.0, 1000.0, 0.0]
    assert p.slot_tools == [1, 2] and list(p.tool_slot) == [0, 0, 0, 1]
    assert p.tool_changes == 2
    assert message(p, "no-speed").line == 10


def test_legacy_m6_and_m3_m5():
    """Ports test_m6 / test_m3_m5 with today's semantics: rapids never cut,
    feed moves carry the spindle state."""
    p = parse("M3S100000\nM6T17\nG90G0X7Y7Z1\nG90G1X10Y12F10\nM3S100\nG1X11Y12\nM5\nG1X12\n")
    assert p.slot_tools == [17]
    assert [f for f in p.flags] == [0, SPINDLE, 0]


def test_tool_is_assumed_when_missing():
    p = parse("T4\nG0 X0 Y0 Z1\nG1 X1 F10\n")
    assert p.slot_tools == [4]
    assert "assumed-tool" in codes(p, "info")
    p = parse("G0 X0 Y0 Z1\nG1 X1 F10\n")
    assert p.slot_tools == [0]


def test_feed_modes():
    p = parse(START + "G93 G1 X10 F2\nG94 G95 G1 X11 F0.01\nG94 G1 X12 F5\nG1 X13\n")
    assert list(p.feed) == [20.0, 10.0, 5.0, 5.0]  # inverse time: 10 long at 2/min
    assert list(p.line_edit[3:7]) == [EDIT_LOCKED, EDIT_LOCKED, EDIT_FEED, EDIT_SPLIT]


def test_missing_feed_is_an_error():
    p = parse("T1 M6\nG0 X0 Y0 Z1\nG1 X1\nG1 X2\n")
    err = message(p, "no-feed")
    assert err.level == "error" and err.count == 2
    assert list(p.feed) == [0.0, 0.0]


def test_block_delete():
    text = "T1 M6\nG0 X0 Y0 Z1\n/G0 X5\nG0 X1\n"
    assert ends(parse(text)) == [(5.0, 0.0, 1.0), (1.0, 0.0, 1.0)]
    assert ends(parse(text, block_delete=True)) == [(1.0, 0.0, 1.0)]


def test_program_end():
    """Port of test_m30: nothing after M30 (or a closing %) runs."""
    p = parse("T1 M6\nG0 X0 Y0 Z1\nG0 X1\nM30\nG90G0X0Y10Z5\nG1X10.Y15.\n")
    assert ends(p) == [(1.0, 0.0, 1.0)] and p.end_line == 3
    p = parse("%\nT1 M6\nG0 X0 Y0 Z1\nG0 X1\n%\nG0 X9\n")
    assert ends(p) == [(1.0, 0.0, 1.0)] and p.end_line == 4
    assert parse("T1 M6\nG0 X0 Y0 Z1\nM2\nG0 X4\n").n_moves == 0


def test_unsupported_and_ignored_codes():
    p = parse(START + "G68 X0 Y0 R45\nG41 D1 G1 X1 F10\nG5.1 Q1\nG187\nG54 G55\nG999\n")
    assert message(p, "unsupported").level == "error"
    assert message(p, "cutter-comp").level == "warning"
    assert message(p, "unknown-g").text == "G999 is not recognized; ignored"
    assert "work-offsets" in codes(p, "warning")


def test_legacy_linear_moves():
    """Port of test_line_parse_linear_move."""
    p = parse(START + "G90 G1 Z1.1 F30.\nX1. Y1.5 F40.\nG0 Z2.0\n")
    assert moves(p) == [
        ((0.0, 0.0, 1.0), (0.0, 0.0, 1.1), SPINDLE, 30.0, 4),
        ((0.0, 0.0, 1.1), (1.0, 1.5, 1.1), SPINDLE, 40.0, 5),
        ((1.0, 1.5, 1.1), (1.0, 1.5, 2.0), RAPID | SPINDLE, 0.0, 6),
    ]


def test_first_move_of_line_and_tool_number():
    p = parse(START + "G1 X1 F10\nG2 X2 Y0 I0.5 J0\n")
    assert p.first_move_of_line(3) == 0
    arc = p.first_move_of_line(4)
    assert arc == 1 and p.line[arc] == 4 and p.line[-1] == 4
    assert p.first_move_of_line(0) is None
    assert p.tool_number(0) == 1


# ------------------------------------------------------------------ optimizer edit classes


def test_line_edit_classes():
    p = parse(START + "G1 X1 Y1 F10\nG2 X2 Y0 I.5 J-.5\nG1 X3 M8\nG93 G1 X4 F1\nG94 G0 X5\n"
              "G1 X6 F10\nG91 G1 X1\nG90\n")
    assert list(p.line_edit) == [
        EDIT_NONE, EDIT_NONE, EDIT_NONE,  # set-up and rapids
        EDIT_SPLIT,  # plain G1
        EDIT_FEED,  # arc
        EDIT_FEED,  # G1 with an M code
        EDIT_LOCKED,  # inverse time
        EDIT_NONE,  # rapid
        EDIT_SPLIT,  # plain G1 again
        EDIT_FEED,  # incremental: may change F but must not be split
        EDIT_NONE,
    ]


# ------------------------------------------------------------------ fast path


FAST_FIELDS = ("points", "flags", "feed", "line", "tool_slot", "speed", "line_edit")

MIXED = """T1 M6
S1000 M3
G0 X0 Y0 Z1
G1 Z-0.1 F20.
X1. Y1. F40.
G2 X2. Y0. I.5 J-.5
G1 X3.
G91 X1.
G90 G0 Z1.
N100 G1 X1 Y2 Z0 F10
N110 X2 F12
M5
X3
G0 X0
M3
G1 X1
G52 X1
G1 X2
G52 X0
G1 X3 Y3
G81 G98 X1 Y1 Z-0.5 R0.1 F5
X2
G80
G1 X0 Y0
"""


@pytest.mark.parametrize("source", ["makino_roughing.nc", "pocket_corners.nc", "crash_demo.nc", None])
@pytest.mark.parametrize("rapid_mode", ["linear", "dogleg"])
def test_fast_path_matches_the_full_interpreter(source, rapid_mode):
    """Lower-case text cannot use the fast path, so it exercises the general one."""
    text = (EXAMPLES / source).read_text() if source else MIXED
    a = parse(text, rapid_mode=rapid_mode)
    b = parse(text.lower(), rapid_mode=rapid_mode)
    assert a.n_moves > 0
    for name in FAST_FIELDS:
        assert list(getattr(a, name)) == list(getattr(b, name)), name
    assert [(m.code, m.line, m.count) for m in a.messages] == [(m.code, m.line, m.count) for m in b.messages]


# ------------------------------------------------------------------ tool center programming


def test_center_programming_declared_after_m6():
    """PowerMill writes T/M6 first and the tool header after it."""
    text = ("N0T5M6\nN10( Tool Coordinates: Centre)\nN20( DIAMETER: 0.25000)\nN30( Tool:   Ball Nosed)\n"
            "S1000M3\nG0X0Y0Z1.\nG1Z.125F10.\nX1.\nX2.\n")
    p = parse(text)
    assert p.tool_point == "center"
    assert [q[2] for q in ends(p)] == [0.0, 0.0, 0.0]  # centre 0.125 - radius 0.125
    assert not codes(p, "error")
    # the general path agrees
    lower = parse(text.lower())
    assert list(lower.points) == list(p.points)


def test_center_programming_across_tool_changes():
    text = ("(T1 D=0.25 - ball end mill)\n(T2 D=0.5 - ball end mill)\n( Tool Coordinates: Center)\n"
            "T1 M6\nS100 M3\nG0 X0 Y0 Z1\nG1 Z0 F10\nX1\nT2 M6\nS100 M3\nG1 X2 F10\nG0 Z1\n")
    p = parse(text)
    assert moves(p) == [
        ((0.0, 0.0, 0.875), (0.0, 0.0, -0.125), SPINDLE, 10.0, 7),
        ((0.0, 0.0, -0.125), (1.0, 0.0, -0.125), SPINDLE, 10.0, 8),
        ((1.0, 0.0, -0.125), (1.0, 0.0, -0.25), RAPID | SPINDLE, 0.0, 11),  # the bigger ball reaches lower
        ((1.0, 0.0, -0.25), (2.0, 0.0, -0.25), SPINDLE, 10.0, 11),
        ((2.0, 0.0, -0.25), (2.0, 0.0, 0.75), RAPID | SPINDLE, 0.0, 12),
    ]


def test_center_programming_needs_a_radius():
    p = parse("( Tool Coordinates: Center)\nT1 M6\nG0 X0 Y0 Z1\nG1 Z0 F10\n")
    assert message(p, "center-no-radius").level == "error"
    p = parse("T1 M6\nG0 X0 Y0 Z1\nG1 Z0 F10\n", tool_point="center", tool_radius=lambda t: 0.1)
    assert ends(p) == [(0.0, 0.0, -0.1)]


def test_byte_order_mark_is_ignored():
    for text in ("﻿%\nT1 M6\nG0 X0 Y0 Z1\nG1 X1 F10\n%\n", "﻿T1 M6\nG0 X0 Y0 Z1\nG1 X1 F10\n"):
        p = parse(text)
        assert not codes(p, "error")
        assert ends(p) == [(1.0, 0.0, 1.0)]
        assert p.lines[0].startswith("﻿")  # the text itself is kept as it was


def test_cycle_k0_stores_the_cycle_without_drilling():
    p = parse(START + "G81 G98 X5 Y5 Z-1 R0.1 F10 K0\nX1 Y1\nG80\n")
    drills = [b for _, b, f, _, _ in moves(p) if f & CYCLE and not f & RAPID]
    assert drills == [(1.0, 1.0, -1.0)]  # no hole at X5 Y5
    assert moves(p)[0][0] == (0.0, 0.0, 1.0)


def test_full_circle_from_center_words_only():
    p = parse("T1 M6\nS1 M3\nG0 X1 Y0 Z0\nG3 I-1 F10\n")
    assert radius_error(p, (0, 0), 1.0) < 1e-9
    assert ends(p)[-1] == (1.0, 0.0, 0.0) and p.n_moves > 100
