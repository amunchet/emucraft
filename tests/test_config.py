"""Configuration files and tool / stock resolution."""

import json
import math

import pytest

from emucraft.check import parse_program
from emucraft.config import ConfigError, config_from_dict, find_config, load_config
from emucraft.model import (
    Config,
    StockSpec,
    ToolSpec,
    apply_tool_values,
    resolve_stock,
    resolve_tools,
    shape_from_kind,
    unit_factor,
)
from helpers import EXAMPLES


def test_example_config_loads():
    cfg = load_config(EXAMPLES / "emucraft.toml")
    assert cfg.units == "inch"
    assert cfg.machine.rapid_rate == 1200 and cfg.machine.tool_change_time == 8
    assert cfg.check.link_feed == 700
    assert cfg.tools == {20: {"flute_length": 1.0, "holder_diameter": 1.3}}
    assert cfg.default_tool == {"holder_diameter": 1.5}
    assert cfg.optimize == {"material": "titanium"}


def test_json_config(tmp_path):
    path = tmp_path / "emucraft.json"
    path.write_text(json.dumps({
        "units": "mm",
        "stock": {"min": [0, 0, -10], "max": [100, 50, 0]},
        "tools": {"T3": {"shape": "ball", "diameter": 6}},
        "comment_rules": [{"pattern": r"HOLDER\s*=\s*([\d.]+)", "field": "tool.holder_diameter"}],
        "variable_rules": {"#140": "tool.stickout"},
        "machine": {"limits": {"X": [-500, 500]}},
    }))
    cfg = load_config(path)
    assert cfg.stock == StockSpec(0, 0, -10, 100, 50, 0, source="config")
    assert cfg.tools[3] == {"shape": "ball", "diameter": 6}
    assert cfg.comment_rules == [(r"HOLDER\s*=\s*([\d.]+)", "tool.holder_diameter")]
    assert cfg.variable_rules == {140: "tool.stickout"}
    assert load_config(None) == Config()


def test_find_config(tmp_path):
    assert find_config(EXAMPLES / "crash_demo.nc") == EXAMPLES / "emucraft.toml"
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    program = deep / "part.nc"
    program.write_text("")
    assert find_config(program) is None or find_config(program).parent not in (deep, deep.parent)
    (tmp_path / "a" / "emucraft.json").write_text("{}")
    assert find_config(program) == tmp_path / "a" / "emucraft.json"
    (deep / "emucraft.toml").write_text("")
    assert find_config(program) == deep / "emucraft.toml"


@pytest.mark.parametrize("data, message", [
    ({"bogus": 1}, "unknown key"),
    ({"units": "furlong"}, "units"),
    ({"machine": {"rapid_mode": "teleport"}}, "rapid_mode"),
    ({"machine": {"rapid_rate": -5}}, "rapid_rate"),
    ({"machine": {"warp": 9}}, "unknown key"),
    ({"machine": {"limits": {"a": [0, 1]}}}, "unknown axis"),
    ({"machine": {"limits": {"x": [5, 1]}}}, "min < max"),
    ({"machine": 5}, "must be a table"),
    ({"check": {"resolution": 0}}, "resolution"),
    ({"check": {"resolution": "fine"}}, "resolution"),
    ({"check": {"max_cells": 1.5}}, "max_cells"),
    ({"check": {"link_severity": "panic"}}, "link_severity"),
    ({"check": {"block_delete": "yes"}}, "block_delete"),
    ({"check": {"tool_point": "side"}}, "tool_point"),
    ({"stock": {"min": [0, 0], "max": [1, 1, 1]}}, "needs min"),
    ({"stock": {"min": [0, 0, 0], "max": [1, -1, 1]}}, "larger than min"),
    ({"tools": {"drill": {"diameter": 1}}}, "not a tool number"),
    ({"tools": {"5": {"diameter": 0}}}, "diameter"),
    ({"tools": {"5": {"shape": "star"}}}, "shape"),
    ({"tools": {"5": {"diameter": "big"}}}, "diameter"),
    ({"tools": {"5": {"colour": "red"}}}, "unknown key"),
    ({"default_tool": {"flutes": 0}}, "flutes"),
    ({"comment_rules": [{"field": "tool.diameter"}]}, "needs a pattern"),
    ({"comment_rules": [{"pattern": "D=([\\d.]+", "field": "tool.diameter"}]}, "bad pattern"),
    ({"comment_rules": [{"pattern": "D=[\\d.]+", "field": "tool.diameter"}]}, "(group)"),
    ({"comment_rules": [{"pattern": "D=([\\d.]+)", "field": "tool.colour"}]}, "cannot store"),
    ({"variable_rules": {"abc": "tool.stickout"}}, "not a macro variable"),
    ({"variable_rules": {"#127": "stock.volume"}}, "cannot store"),
])
def test_config_validation(data, message):
    with pytest.raises(ConfigError, match=message.replace("(", r"\(").replace(")", r"\)")):
        config_from_dict(data)


def test_bad_toml(tmp_path):
    path = tmp_path / "emucraft.toml"
    path.write_text("[check\n")
    with pytest.raises(ConfigError):
        load_config(path)


def test_config_rules_reach_the_interpreter():
    cfg = config_from_dict({
        "comment_rules": [{"pattern": r"HOLDER\s*=\s*([\d.]+)", "field": "tool.holder_diameter"}],
        "variable_rules": {"140": "tool.stickout"},
    })
    program = parse_program("T2 M6\n(HOLDER = 1.25)\n#140 = 2.5\n#128 = 1.1\n", "p", cfg)
    assert program.tool_notes[2].values == {"holder_diameter": 1.25, "stickout": 1.1}
    # #140 was set first, the default #128 rule then wins for the stick-out
    program = parse_program("T2 M6\n#128 = 1.1\n#140 = 2.5\n", "p", cfg)
    assert program.tool_notes[2].values["stickout"] == 2.5


# ------------------------------------------------------------------ model


@pytest.mark.parametrize("kind, shape, angle", [
    ("End Mill", "flat", None),
    ("flat end mill", "flat", None),
    ("Ball Nosed", "ball", None),
    ("ball end mill", "ball", None),
    ("Tip Radiused", "bull", None),
    ("bull nose end mill", "bull", None),
    ("drill", "cone", 118.0),
    ("spot drill", "cone", 90.0),
    ("center drill", "cone", 60.0),
    ("chamfer mill", "cone", 90.0),
    ("engraving tool", "cone", 60.0),
    ("face mill", "flat", None),
    ("thread mill form", None, None),
])
def test_shape_from_kind(kind, shape, angle):
    assert shape_from_kind(kind) == (shape, angle)


def test_unit_factor():
    assert unit_factor("inch", "mm") == 25.4
    assert unit_factor("mm", "inch") == pytest.approx(1 / 25.4)
    assert unit_factor("mm", "mm") == 1.0


def test_tool_spec_geometry():
    t = ToolSpec(1, shape="cone", diameter=0.5, tip_angle=90.0)
    shape, radius, corner, slope, flute = t.kernel_args()
    assert (shape, radius, corner, flute) == (3, 0.25, 0.0, 0.0)
    assert slope == pytest.approx(1.0)
    t = ToolSpec(2, diameter=0.125, shank_diameter=0.25, flute_length=0.4, stickout=1.0, holder_diameter=1.0)
    assert t.bodies() == [(0.125, 0.4, "shank"), (0.5, 1.0, "holder")]
    assert ToolSpec(3, diameter=0.5, shank_diameter=0.5, flute_length=1).bodies() == []  # shank = cutter
    assert ToolSpec(4, diameter=0.5, holder_diameter=1.0).bodies() == []  # no stick-out: unknown height
    assert ToolSpec(5).problems() == ["diameter is unknown"]
    assert "bull nose" in ToolSpec(6, shape="bull", diameter=0.5, corner_radius=0.3).problems()[0]
    assert "smaller than the cutter" in ToolSpec(7, diameter=1, holder_diameter=0.5).problems()[0]
    assert ToolSpec(8, diameter=1).to_dict()["problems"] == []


def test_apply_tool_values():
    t = ToolSpec(1)
    apply_tool_values(t, {"diameter": 12.7, "kind": "ball end mill"}, "config", 1 / 25.4)
    assert t.diameter == pytest.approx(0.5) and t.shape == "ball" and t.name == "ball end mill"
    assert t.source == {"diameter": "config", "shape": "config"}
    with pytest.raises(ValueError):
        apply_tool_values(t, {"colour": "red"}, "config", 1.0)
    with pytest.raises(ValueError):
        apply_tool_values(t, {"shape": "star"}, "config", 1.0)


PROGRAM = """(T1 D=0.5 CR=0.06 - bull nose end mill)
(T2 D=0.25 - ball end mill)
T1 M6
(HOLDER DIAMETER: 1.0)
G0 X0 Y0 Z1
T2 M6
G0 X1
T3 M6
G0 X2
"""


def test_resolve_tools_precedence():
    program = parse_program(PROGRAM, "p")
    cfg = Config(units="mm")
    cfg.default_tool = {"holder_diameter": 50.8, "stickout": 25.4, "kind": "drill"}
    cfg.tools = {2: {"flute_length": 12.7}}
    tools = resolve_tools(program, cfg, {"*": {"flutes": 3}, 3: {"diameter": 0.1}})
    t1, t2, t3 = tools[1], tools[2], tools[3]
    assert (t1.shape, t1.diameter, t1.corner_radius) == ("bull", 0.5, 0.06)
    assert t1.holder_diameter == 1.0 and t1.source["holder_diameter"] == "program line 4"
    assert t1.stickout == pytest.approx(1.0) and t1.source["stickout"] == "config default_tool"
    assert (t2.shape, t2.corner_radius) == ("ball", 0.125)  # a ball's corner is its radius
    assert t2.flute_length == pytest.approx(0.5) and t2.source["flute_length"] == "config tools.2"
    assert t2.holder_diameter == pytest.approx(2.0)
    assert t3.shape == "cone" and t3.tip_angle == 118.0  # default_tool kind fills an unknown tool
    assert t3.diameter == 0.1 and t3.source["diameter"] == "override"
    assert all(t.flutes == 3 for t in tools.values())


def test_bull_without_corner_is_flat():
    program = parse_program("T1 M6\nG0 X0 Y0 Z1\n", "p")
    cfg = Config()
    cfg.tools = {1: {"shape": "bull", "diameter": 0.5}}
    assert resolve_tools(program, cfg)[1].shape == "flat"


def test_resolve_stock_precedence():
    header = "( MIN X: 0.)\n( MIN Y: 0.)\n( MIN Z: -1.)\n( MAX X: 4.)\n( MAX Y: 3.)\n( MAX Z: 0.)\nT1 M6\n"
    program = parse_program(header, "p")
    stock, msgs = resolve_stock(program, Config())
    assert (stock.source, stock.xmax, msgs) == ("program", 4.0, [])
    cfg = Config(units="mm")
    cfg.stock = StockSpec(0, 0, -25.4, 50.8, 25.4, 0)
    stock, _ = resolve_stock(program, cfg)
    assert stock.source == "config" and stock.xmax == pytest.approx(2.0)
    bare = parse_program("T1 M6\n", "p")
    stock, msgs = resolve_stock(bare, Config(), ((0, 0, -1), (2, 1, 0.5), 0.25))
    assert (stock.xmin, stock.xmax, stock.zmax, stock.source) == (-0.25, 2.25, 0.5, "toolpath")
    assert msgs[0].level == "warning"
    stock, msgs = resolve_stock(bare, Config())
    assert stock is None and msgs[0].level == "error"
    cfg = Config()
    cfg.stock = StockSpec(0, 0, 0, 1, 1, 0)
    stock, msgs = resolve_stock(bare, cfg)
    assert stock is None and "empty" in msgs[0].text


def test_config_copy_is_deep():
    cfg = Config()
    cfg.tools = {1: {"diameter": 0.5}}
    copy = cfg.copy()
    copy.tools[1]["diameter"] = 1.0
    assert cfg.tools[1]["diameter"] == 0.5
    assert math.isclose(Config().copy().machine.tool_change_time, 6.0)
