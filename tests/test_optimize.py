"""Feed optimization: corner slow-downs, load limits, and safe rewriting."""

import json
import re

import pytest

from emucraft.check import parse_program
from emucraft.gcode import ARC, EDIT_LOCKED, RAPID
from emucraft.model import Config
from emucraft.optimize import MATERIALS, OptimizeSpec, optimize, verify_same_path
from helpers import DATA, EXAMPLES

TOL = 3e-4
F_WORD = re.compile(r"F[-+]?(?:\d+\.?\d*|\.\d+)", re.I)


def strip_f(line):
    return F_WORD.sub("", line).strip()


def run(name, config=None, **options):
    text = (EXAMPLES / name).read_text() if (EXAMPLES / name).exists() else (DATA / name).read_text()
    program = parse_program(text, name)
    spec = OptimizeSpec.from_dict(options or {"material": "titanium"})
    return program, optimize(program, config or Config(), spec)


def feeds_along(original, optimized):
    """For every original move, the feeds of the optimized moves lying on it."""
    out = [[] for _ in range(original.n_moves)]
    i = 0
    for j in range(optimized.n_moves):
        out[i].append((optimized.feed[j], optimized.flags[j]))
        q, p1 = optimized.point(j + 1), original.point(i + 1)
        if sum((a - b) ** 2 for a, b in zip(q, p1)) <= TOL * TOL:
            i += 1
    assert i == original.n_moves
    return out


@pytest.fixture(scope="module")
def pocket():
    return run("pocket_corners.nc")


def test_pocket_is_verified_and_slower(pocket):
    program, result = pocket
    assert result.verified and not result.messages
    st = result.stats
    assert st["corners"] >= 40  # 6 rectangles x 4 corners x 2 levels, plus ramps
    assert 0 < st["pieces_slowed"] < st["pieces"]
    assert st["cutting_seconds_after"] > st["cutting_seconds_before"]
    assert st["lines_changed"] > 0 and st["lines_added"] > 0
    assert result.text != program.text
    assert all(c["angle"] >= 40 and 0 < c["factor"] <= 1 for c in result.corners)


def test_pocket_path_is_unchanged(pocket):
    program, result = pocket
    reparsed = parse_program(result.text, "optimized")
    assert verify_same_path(program, reparsed, TOL) is None
    assert reparsed.n_moves > program.n_moves  # G1 moves were split at slow zones
    assert not [m for m in reparsed.messages if m.level == "error"]


def test_pocket_feeds_are_exactly_the_intended_ones(pocket):
    """Every emitted F (and every modal F restored after a slow zone) gives the
    feed the optimizer chose; nothing gets faster with air_factor == 1."""
    program, result = pocket
    reparsed = parse_program(result.text, "optimized")
    spec = result.spec
    sequence = []  # (new feed, programmed feed) of every optimized feed move
    for i, pieces in enumerate(feeds_along(program, reparsed)):
        if program.flags[i] & RAPID:
            assert all(f & RAPID for _, f in pieces)
            continue
        feeds = [feed for feed, _ in pieces]
        assert min(feeds) == pytest.approx(result.move_feed[i])
        assert max(feeds) <= program.feed[i]
        assert min(feeds) >= program.feed[i] * spec.min_factor - 1e-9
        sequence += [(feed, program.feed[i]) for feed in feeds]
    restored = sum(1 for (a, _), (b, full) in zip(sequence, sequence[1:]) if a < b == full)
    assert restored >= 40  # the full feed comes back after every slow zone


def test_corner_zones_hit_the_corners(pocket):
    program, result = pocket
    reparsed = parse_program(result.text, "optimized")
    # at every rectangle corner of the pocket the feed is the corner feed
    corner_feed = 45.0 * MATERIALS["titanium"]["corner_factor"]
    slow_points = {reparsed.point(j + 1)[:2] for j in range(reparsed.n_moves)
                   if not reparsed.flags[j] & RAPID and reparsed.feed[j] <= corner_feed}
    assert (1.4375, 1.4375) in slow_points  # a corner of the innermost rectangle
    assert (3.3125, 2.3125) in slow_points  # a corner of the outermost rectangle


def test_arcs_only_change_their_feed():
    program, result = run("makino_roughing.nc")
    assert result.verified
    original = program.text.splitlines()
    out = result.text.splitlines()

    def arc_lines(lines):
        return [ln for ln in lines if re.search(r"[IJ][-+]?[\d.]", ln.split("(")[0])]

    before, after = arc_lines(original), arc_lines(out)
    assert len(before) == len(after) == 422
    assert [strip_f(a) for a in before] == [strip_f(b) for b in after]
    assert any(a != b for a, b in zip(before, after))  # some arcs were slowed
    # the rewritten program keeps the compact style of the post
    assert all(" " not in ln for ln in out if ln.startswith("G1X"))


def test_locked_and_incremental_lines():
    program, result = run("feed_modes.nc")
    assert result.verified
    original = program.text.splitlines()
    out = result.text.splitlines()

    def section(lines, start, end):
        a = next(i for i, ln in enumerate(lines) if ln.startswith(start))
        b = next(i for i, ln in enumerate(lines) if ln.startswith(end))
        return lines[a:b]

    # G93 / G95 lines are copied as they are
    assert section(out, "G93", "G94 G1") == section(original, "G93", "G94 G1")
    # G91 lines may get a new F but are never split
    inc_before, inc_after = section(original, "G91", "G90"), section(out, "G91", "G90")
    assert [strip_f(a) for a in inc_before] == [strip_f(b) for b in inc_after]
    assert inc_before != inc_after  # the incremental square does slow at its corners
    # the absolute G94 square was split at its slow zones
    assert len(section(out, "G1 X", "G93")) > len(section(original, "G1 X", "G93"))
    assert verify_same_path(program, parse_program(result.text, "out"), TOL) is None


def test_slow_programmed_feeds_keep_their_precision():
    text = (DATA / "feed_modes.nc").read_text().replace("F40.", "F0.05").replace("F10.", "F0.05")
    program = parse_program(text, "slow")
    result = optimize(program, Config(), OptimizeSpec.from_dict({"material": "titanium"}))
    assert result.verified
    reparsed = parse_program(result.text, "out")
    feeds = {f for f, flags in zip(reparsed.feed, reparsed.flags) if not flags & RAPID}
    assert 0.05 in feeds
    # slowed, never rounded up to a whole in/min, never zero
    assert all(0 < f for f in feeds)
    g94 = [f for f, flags, line in zip(reparsed.feed, reparsed.flags, reparsed.line)
           if not flags & RAPID and reparsed.line_edit[line] != EDIT_LOCKED]
    assert max(g94) <= 0.05 and min(g94) >= 0.05 * result.spec.min_factor - 1e-12


def test_air_moves_can_be_sped_up():
    stock = "( MIN X: 0.)\n( MIN Y: 0.)\n( MIN Z: -1.)\n( MAX X: 4.)\n( MAX Y: 3.)\n( MAX Z: 0.)\n"
    text = stock + ("(T1 D=0.5 CR=0. - flat end mill)\nT1 M6\nS2000 M3\nG0 X0.5 Y0.5 Z0.1\n"
                    "G1 Z-0.1 F10.\nX3.5 F40.\nG1 Z0.5 F40.\nX0.5 Y2.5\nZ-0.1\nX3.5\nG0 Z1.\n")
    program = parse_program(text, "air")
    same = optimize(program, Config(), OptimizeSpec.from_dict({"load": False, "corner_factor": 1.0}))
    assert same.verified and same.text == program.text
    fast = optimize(program, Config(), OptimizeSpec.from_dict(
        {"load": False, "corner_factor": 1.0, "air_factor": 2.0, "max_feed": 60.0}))
    assert fast.verified and fast.stats["pieces_air"] > 0
    reparsed = parse_program(fast.text, "fast")
    assert max(reparsed.feed) == 60.0  # 2 x 40 capped by max_feed
    assert fast.stats["cutting_seconds_after"] < fast.stats["cutting_seconds_before"]


def test_spec_from_dict_and_presets():
    assert OptimizeSpec.from_dict(None) == OptimizeSpec()
    ti = OptimizeSpec.from_dict({"material": "Titanium"})
    for key, value in MATERIALS["titanium"].items():
        assert getattr(ti, key) == value
    assert OptimizeSpec.from_dict({"material": "titanium", "corner_factor": 0.9}).corner_factor == 0.9
    assert set(MATERIALS) == {"aluminum", "steel", "stainless", "titanium", "inconel"}
    factors = [MATERIALS[m]["corner_factor"] for m in ("aluminum", "steel", "stainless", "titanium", "inconel")]
    assert factors == sorted(factors, reverse=True)  # harder materials slow down more


@pytest.mark.parametrize("options", [
    {"bogus": 1},
    {"material": "unobtainium"},
    {"corner_factor": 0},
    {"corner_factor": 1.5},
    {"min_factor": 0},
    {"corner_angle": 0},
    {"corner_angle": 180},
    {"corner_zone": -1},
    {"corner_window": 0},
    {"load_limit": 0},
    {"air_factor": 0},
])
def test_spec_validation(options):
    with pytest.raises(ValueError):
        OptimizeSpec.from_dict(options)


def test_config_optimize_section_is_the_default():
    config = Config()
    config.optimize = {"material": "aluminum"}
    program = parse_program((EXAMPLES / "pocket_corners.nc").read_text(), "pocket")
    result = optimize(program, config)
    assert result.spec.corner_factor == MATERIALS["aluminum"]["corner_factor"]
    assert result.verified


def test_no_tool_geometry():
    program = parse_program("T9 M6\nS100 M3\nG0 X0 Y0 Z1\nG1 X1 F10\n", "t9")
    result = optimize(program, Config())
    assert not result.verified
    assert result.text == program.text
    assert [m.level for m in result.messages] == ["warning", "error"]


def test_verify_same_path_detects_changes():
    text = "T1 M6\nG0 X0 Y0 Z1\nG1 X1 F10\nG1 X2 Y1\nG0 Z2\n"
    a = parse_program(text)
    assert verify_same_path(a, a, TOL) is None
    split = parse_program(text.replace("G1 X1 F10", "G1 X0.5 F10\nG1 X1"))
    assert verify_same_path(a, split, TOL) is None
    moved = parse_program(text.replace("X2 Y1", "X2 Y1.1"))
    assert "off the original path" in verify_same_path(a, moved, TOL)
    rapid = parse_program(text.replace("G1 X2 Y1", "G0 X2 Y1"))
    assert "move type changed" in verify_same_path(a, rapid, TOL)
    short = parse_program(text.replace("G0 Z2\n", ""))
    assert "ends early" in verify_same_path(a, short, TOL)
    longer = parse_program(text + "G0 X5\n")
    assert "extra move" in verify_same_path(a, longer, TOL)


def test_result_to_dict(pocket):
    _, result = pocket
    data = json.loads(json.dumps(result.to_dict()))
    assert data["verified"] is True and data["spec"]["material"] == "titanium"
    assert "text" not in data
    assert result.to_dict(include_text=True)["text"] == result.text
    assert len(result.move_feed) == len(result.move_load) == result.program.n_moves
    assert any(v > 0 for v in result.move_load)


def test_arc_flags_survive(pocket):
    program, result = run("makino_roughing.nc", material="aluminum")
    reparsed = parse_program(result.text, "out")
    assert sum(1 for f in reparsed.flags if f & ARC) == sum(1 for f in program.flags if f & ARC)


def test_block_delete_lines_are_locked_and_keep_their_feed():
    """A '/' line may be skipped at the machine: nothing new may run in its
    place, and it must still run at its own (modal) feed."""
    text = (EXAMPLES / "pocket_corners.nc").read_text().replace("N240 X2.5625", "/N240 X2.5625")
    program = parse_program(text, "slash")
    result = optimize(program, Config(), OptimizeSpec.from_dict({"material": "titanium"}))
    assert result.verified
    out = result.text.splitlines()
    k = next(i for i, line in enumerate(out) if line.startswith("/N240"))
    assert out[k] == "/N240 X2.5625"  # untouched, not split
    assert out[k - 1] == "F45."  # the original modal feed is restored before it
    reparsed = parse_program(result.text, "out")
    assert {reparsed.feed[i] for i in range(reparsed.n_moves)
            if reparsed.lines[reparsed.line[i]].startswith("/N240")} == {45.0}
    assert out[k + 1].startswith("N250") and out[k + 1].endswith("F22.")  # slow again after it


def test_air_speed_up_does_not_leak_into_locked_lines():
    stock = "( MIN X: 0.)\n( MIN Y: 0.)\n( MIN Z: -1.)\n( MAX X: 4.)\n( MAX Y: 3.)\n( MAX Z: 0.)\n"
    text = stock + ("(T1 D=0.5 CR=0. - flat end mill)\nT1 M6\nS2000 M3\nG0 X0.5 Y0.5 Z1.\n"
                    "G1 Z0.5 F40.\nX3.5\n/G1 Z-0.1\n/X0.5\nG0 Z1.\n")
    program = parse_program(text, "leak")
    result = optimize(program, Config(), OptimizeSpec.from_dict(
        {"load": False, "corner_factor": 1.0, "air_factor": 2.0}))
    assert result.verified
    reparsed = parse_program(result.text, "out")
    by_line = {}
    for i in range(reparsed.n_moves):
        by_line.setdefault(reparsed.lines[reparsed.line[i]], set()).add(reparsed.feed[i])
    assert by_line["X3.5"] == {80.0}  # an air move, sped up
    assert by_line["/G1 Z-0.1"] == {40.0} and by_line["/X0.5"] == {40.0}  # cutting at the programmed feed
