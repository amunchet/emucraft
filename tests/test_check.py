"""End-to-end collision checks."""

import json
import time

import pytest

from emucraft.check import check_file, check_text, choose_cell, parse_program, run_check
from emucraft.gcode import NOCUT
from emucraft.model import Config, StockSpec, ToolSpec
from helpers import EXAMPLES

STOCK = "( MIN X: 0.)\n( MIN Y: 0.)\n( MIN Z: -1.)\n( MAX X: 4.)\n( MAX Y: 3.)\n( MAX Z: 0.)\n"
FLAT = "(T1 D=0.5 CR=0. - flat end mill)\n"


def coarse(**check):
    config = Config()
    config.check.resolution = check.pop("resolution", 0.01)
    for key, value in check.items():
        setattr(config.check, key, value)
    return config


def issues(result):
    return [(i["kind"], i["severity"], i["lines"][0], i["lines"][1]) for i in result.to_dict()["issues"]]


@pytest.fixture(scope="module")
def crash():
    return check_file(EXAMPLES / "crash_demo.nc")


@pytest.fixture(scope="module")
def makino():
    t0 = time.perf_counter()
    result = check_file(EXAMPLES / "makino_roughing.nc")
    result.elapsed = time.perf_counter() - t0
    return result


def test_crash_demo_finds_every_planted_crash(crash):
    assert crash.status == "fail"
    assert issues(crash) == [
        ("rapid-cut", "error", 29, 29),
        ("spindle-off-cut", "error", 34, 34),
        ("holder", "error", 44, 46),
        ("shank", "error", 45, 45),
        ("table", "error", 52, 52),
    ]
    by_kind = {i.kind: i for i in crash.issues}
    assert by_kind["rapid-cut"].depth == pytest.approx(0.1, abs=1e-3)
    assert by_kind["spindle-off-cut"].depth == pytest.approx(0.1, abs=1e-3)
    assert by_kind["holder"].depth == pytest.approx(0.15, abs=1e-3)  # plunge to -0.9, holder 0.75 up
    assert by_kind["shank"].depth == pytest.approx(0.4, abs=1e-3)  # 0.9 deep side cut, 0.5 flutes
    assert by_kind["table"].depth == pytest.approx(0.2)  # drilled to Z-1.2 through a 1 in block
    assert {i.tool for i in crash.issues} == {1, 2, 3}
    assert crash.summary() == "5 collisions"


def test_crash_demo_is_stable_at_coarse_resolution():
    result = check_file(EXAMPLES / "crash_demo.nc", cell=0.02)
    assert [(k, a, b) for k, _, a, b in issues(result)] == [
        ("rapid-cut", 29, 29), ("spindle-off-cut", 34, 34), ("holder", 44, 46), ("shank", 45, 45), ("table", 52, 52),
    ]


def test_crash_demo_tools(crash):
    t1, t2, t3 = crash.tools[1], crash.tools[2], crash.tools[3]
    assert (t1.shape, t1.diameter, t1.flute_length, t1.stickout, t1.holder_diameter) == ("flat", 0.5, 1.0, 1.25, 1.5)
    assert (t2.shape, t2.diameter, t2.corner_radius) == ("ball", 0.25, 0.125)
    assert (t3.shape, t3.tip_angle) == ("cone", 118.0)
    assert t3.holder_diameter == 1.5  # examples/emucraft.toml [default_tool]
    assert t3.source["holder_diameter"] == "config default_tool"
    assert t2.bodies() == [(0.5, 0.75, "holder")]


def test_makino_sample_is_clean_and_fast(makino):
    assert makino.status == "warn"  # program warnings only (G65 probes, H1 with T20, M#150)
    assert makino.issues == []
    assert makino.summary() == "0 collisions, 5 program warnings"
    st = makino.stats
    assert st["volume_removed"] == pytest.approx(1.9086, abs=0.005)
    assert st["volume_by_tool"] == {"20": pytest.approx(st["volume_removed"])}
    assert st["remaining_volume"] == pytest.approx(st["stock_volume"] - st["volume_removed"], abs=0.01)
    assert st["moves"] == {"rapid": 66, "feed": 7382, "arc": 4838, "cycle": 0}
    assert st["dwell_seconds"] == pytest.approx(310.0)
    assert st["cutting_seconds"] == pytest.approx(125.47, abs=0.1)
    assert st["simulated_segments"] < makino.program.n_moves  # collinear moves were merged
    assert makino.grid.cell == pytest.approx(0.0019)
    assert makino.elapsed < 15  # typically well under a second


def test_config_discovery(makino):
    # examples/emucraft.toml sits next to the program
    t20 = makino.tools[20]
    assert t20.holder_diameter == 1.3 and t20.source["holder_diameter"] == "config tools.20"
    assert t20.flute_length == 1.0
    assert t20.stickout == 1.4 and t20.source["stickout"].startswith("program line")
    assert sum(1 for f in makino.flags if f & NOCUT) == 7  # the F750 links (link_feed = 700)
    assert makino.stats["rapid_rate"] == 1200.0
    plain = check_file(EXAMPLES / "makino_roughing.nc", config=Config(), cell=0.01)
    assert plain.tools[20].holder_diameter == 10.0
    assert not any(f & NOCUT for f in plain.flags)
    assert plain.stats["rapid_rate"] == 1000.0


def test_overrides(makino_text):
    program = parse_program(makino_text, "makino")
    result = run_check(program, Config(), overrides={"*": {"holder_diameter": 2.0}, 20: {"stickout": 0.05}},
                       cell=0.01)
    t20 = result.tools[20]
    assert (t20.holder_diameter, t20.stickout) == (2.0, 0.05)
    assert t20.source["stickout"] == "override"
    # a 0.05 in stick-out puts the holder into the second (0.1 deep) level
    assert result.status == "fail"
    assert {i.kind for i in result.issues} == {"holder"}


LINK = STOCK + FLAT + """T1 M6
S1000 M3
G0 X0.5 Y0.5 Z0.5
G1 Z-0.1 F20.
X3.5 F40.
G0 Z0.5
X0.5 Y2.5
G1 Z-0.05 F20.
X3.5 F750.
G0 Z1.
"""


def test_link_feed():
    assert check_text(LINK, "link", coarse()).issues == []
    for result in (check_text(LINK, "link", coarse(), link_feed=700),
                   check_text(LINK, "link", coarse(link_feed=700))):
        assert issues(result) == [("link-cut", "warning", 16, 16)]
        assert result.issues[0].message == "High-feed link move (F750) cuts material (0.0500 in deep)"
        assert result.status == "warn"
    strict = check_text(LINK, "link", coarse(link_feed=700, link_severity="error"))
    assert issues(strict) == [("link-cut", "error", 16, 16)]
    assert strict.status == "fail"
    # moves below the link feed may cut
    assert check_text(LINK, "link", coarse(link_feed=800)).issues == []


def test_tool_without_geometry():
    text = STOCK + "T9 M6\nS1000 M3\nG0 X1 Y1 Z0.5\nG1 Z-0.2 F10.\nX2.\nG0 Z1.\n"
    result = check_text(text, "t9", coarse())
    assert issues(result) == [("tool", "error", 10, 11)]
    assert "T9 cannot be simulated (diameter is unknown)" in result.issues[0].message
    assert result.tools[9].problems() == ["diameter is unknown"]
    fixed = check_text(text, "t9", coarse(), overrides={9: {"diameter": 0.25}})
    assert fixed.issues == [] and fixed.stats["volume_removed"] > 0


def test_stock_is_estimated_when_missing():
    text = FLAT + "T1 M6\nS1000 M3\nG0 X1 Y1 Z0.5\nG1 Z-0.2 F10.\nX2.\nG0 Z1.\n"
    result = check_text(text, "auto", coarse())
    assert result.stock.source == "toolpath"
    assert (result.stock.xmin, result.stock.xmax, result.stock.zmin) == (0.75, 2.25, -0.2)
    assert any(m.code == "stock" and m.level == "warning" for m in result.messages)
    assert result.status == "warn"


def test_no_stock_and_no_cutting_moves():
    result = check_text("T1 M6\nG0 X0 Y0 Z1\nG0 X1\n", "air", Config())
    assert result.stock is None and result.grid is None
    assert any(m.code == "stock" and m.level == "error" for m in result.messages)
    assert result.status == "fail"


TABLE = STOCK + FLAT + "T1 M6\nS1000 M3\nG0 X1 Y1 Z0.5\nG1 Z-1.3 F10.\nG0 Z1.\nG0 X10.\n"


def test_table_plane():
    result = check_text(TABLE, "table", coarse())
    table = [i for i in result.issues if i.kind == "table"]
    assert [(i.first_line + 1, i.last_line + 1) for i in table] == [(11, 12)]
    assert table[0].depth == pytest.approx(0.3)
    assert table[0].position == (1.0, 1.0, -1.3)
    assert not [i for i in check_text(TABLE, "table", coarse(), table_z=-2.0).issues if i.kind == "table"]
    config = coarse()
    config.machine.table_z = -1.2
    [i] = [i for i in check_text(TABLE, "table", config).issues if i.kind == "table"]
    assert i.depth == pytest.approx(0.1)


def test_machine_travel_limits():
    config = coarse()
    config.machine.limits = {"x": [-1, 5]}
    travel = [i for i in check_text(TABLE, "travel", config).issues if i.kind == "travel"]
    assert [(i.first_line + 1, i.message) for i in travel] == [(13, "X leaves the machine travel [-1, 5] (X10)")]


def test_mm_config_for_an_inch_program():
    config = Config(units="mm")
    config.stock = StockSpec(0, 0, -25.4, 101.6, 76.2, 0.0)
    config.tools = {1: {"diameter": 12.7, "holder_diameter": 25.4, "stickout": 12.7}}
    config.check.resolution = 0.254
    config.machine.rapid_rate = 25400
    result = check_text("T1 M6\nS1000 M3\nG20\nG0 X1 Y1 Z0.5\nG1 Z-0.3 F10.\nX2.\nG0 Z1.\n", "mm", config)
    assert result.program.units == "inch"
    s = result.stock
    assert (s.xmax, s.ymax, s.zmin) == (pytest.approx(4.0), pytest.approx(3.0), pytest.approx(-1.0))
    t1 = result.tools[1]
    assert (t1.diameter, t1.holder_diameter, t1.stickout) == (pytest.approx(0.5), pytest.approx(1.0), pytest.approx(0.5))
    assert result.grid.cell == pytest.approx(0.01)
    assert result.stats["rapid_rate"] == pytest.approx(1000.0)
    assert result.issues == []  # 0.3 deep, holder 0.5 above the tip


def test_rapid_crash_is_reported_on_the_crashing_line():
    """Collinear moves are merged for speed; a rapid must still name its own line."""
    text = STOCK + FLAT + "T1 M6\nS1000 M3\nG0 X2 Y1.5 Z1.\nG0 Z0.5\nG0 Z-0.1\nG0 Z1.\n"
    for simplify in (None, 0.0):
        result = check_text(text, "rapid", coarse(simplify=simplify))
        assert issues(result) == [("rapid-cut", "error", 12, 12)]


def test_threads_do_not_change_the_result():
    one = check_file(EXAMPLES / "crash_demo.nc", cell=0.005, threads=1)
    many = check_file(EXAMPLES / "crash_demo.nc", cell=0.005, threads=4)
    assert issues(one) == issues(many)
    assert one.stats["volume_removed"] == pytest.approx(many.stats["volume_removed"], rel=1e-12)


def test_keep_sim_and_progress():
    seen = []
    result = check_file(EXAMPLES / "pocket_corners.nc", cell=0.01, keep_sim=True, progress=seen.append)
    assert result.sim is not None and seen[-1] == 1.0
    assert result.sim.volume() == pytest.approx(result.stats["remaining_volume"])
    result.sim.close()


def test_to_dict_is_json(crash, makino):
    for result in (crash, makino):
        data = json.loads(json.dumps(result.to_dict()))
        assert data["status"] == result.status
        assert data["program"]["units"] == "inch"
        assert set(data) >= {"issues", "messages", "stock", "tools", "grid", "stats", "timing", "machine"}
    assert json.loads(json.dumps(crash.to_dict()))["issues"][0]["lines"] == [29, 29]


def test_choose_cell():
    stock = StockSpec(0, 0, -1, 4, 5, 0)
    assert choose_cell(stock, Config(), "inch", {}) == pytest.approx(0.0019)  # ~6 M cells
    config = Config(units="mm")
    config.check.resolution = 0.0508
    assert choose_cell(stock, config, "inch", {}) == pytest.approx(0.002)
    # small tools get finer cells, big tools do not change anything
    assert choose_cell(stock, Config(), "inch", {1: ToolSpec(1, diameter=0.004)}) < 0.0019
    assert choose_cell(stock, Config(), "inch", {1: ToolSpec(1, diameter=2.0)}) == pytest.approx(0.0019)
    # ...but never beyond four times the cell budget
    big = StockSpec(0, 0, -1, 40, 20, 0)
    cell = choose_cell(big, Config(), "inch", {1: ToolSpec(1, diameter=1 / 64)})
    assert (40 / cell) * (20 / cell) <= 4 * Config().check.max_cells * 1.05


def test_coarse_resolution_warning():
    result = check_file(EXAMPLES / "crash_demo.nc", cell=0.1)
    assert any(m.code == "resolution" for m in result.messages)


def test_grid_overhang_holds_no_material():
    """The grid is a whole number of cells, so it overhangs the stock. A rapid
    that plunges tangent to the stock side must not hit that overhang."""
    tangent = STOCK + FLAT + "T1 M6\nS1000 M3\nG0 X4.25 Y1.5 Z0.5\nG0 Z-0.5\nG0 Z0.5\n"
    result = check_text(tangent, "tangent", coarse(resolution=0.03))
    assert result.grid.nx * 0.03 > 4.0  # the grid does overhang
    assert result.issues == []
    overlap = tangent.replace("X4.25", "X4.1")
    assert [i.kind for i in check_text(overlap, "overlap", coarse(resolution=0.03)).issues] == ["rapid-cut"]
    stats = check_file(EXAMPLES / "makino_roughing.nc", config=Config(), cell=0.01).stats
    assert stats["remaining_volume"] == pytest.approx(stats["stock_volume"] - stats["volume_removed"], abs=0.01)


def test_travel_limits_ignore_the_assumed_home():
    """The start height and G28/G53 retracts are guesses, not programmed moves."""
    config = coarse(resolution=0.02)
    config.machine.limits = {"z": [-5, 0.8]}
    text = STOCK + FLAT + "T1 M6\nS1 M3\nG0 X1 Y1 Z0.5\nG91 G28 Z0\nG90 G0 Z0.9\n"
    result = check_text(text, "home", config)
    assert result.program.home_z == 1.0  # stock top + 1 in
    assert [(i.kind, i.first_line + 1) for i in result.issues] == [("travel", 12)]  # only the programmed Z0.9
