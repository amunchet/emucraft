"""Fixtures: a second block that the tool, shank and holder must never touch."""

import base64
import gzip
import struct
from array import array

import pytest

from emucraft.check import check_file, check_text
from emucraft.config import ConfigError, config_from_dict, load_config
from emucraft.fixture import _box, place_fixtures, read_stl
from emucraft.kernel import Sim
from emucraft.payload import analysis_payload
from helpers import EXAMPLES

PROGRAM = """\
G20 G90 G17
T1 M6
S5000 M3
G0 X0.5 Y1 Z1.1
G1 Z0.8 F20
G1 X3.5 F40
G1 X3.9 (CUTTER 0.15 INTO THE JAW)
G0 Z2
G0 X5 Y1
G1 Z0.5 F20
G1 X5.9 F40 (CLEAR: ABOVE THE CLAMP)
G1 Z0.2 (INTO THE CLAMP)
G0 Z3
G0 X3 Y3.5
G1 Z1.1 F20 (HOLDER OVER THE BLOCK)
G0 Z3
M30
"""


def config(**fixture_extra):
    return config_from_dict({
        "units": "inch",
        "stock": {"min": [0, 0, 0], "max": [4, 2, 1]},
        "tools": {"1": {"diameter": 0.5, "flute_length": 1, "stickout": 1.0, "holder_diameter": 2}},
        "machine": {"table_z": -1},
        "fixtures": [
            {"name": "jaw", "min": [4, 0, 0], "max": [4.75, 2, 0.9]},
            {"name": "clamp", "min": [5.5, 0.5, 0], "max": [6.5, 1.5, 0.3]},
            {"name": "block", "min": [2, 4.2, 0], "max": [4, 5, 2.3], **fixture_extra},
        ],
    })


def fixture_issues(result):
    return [(i.kind, i.first_line + 1, i.message) for i in result.issues if i.kind == "fixture"]


def test_cutter_and_holder_hits_name_the_fixture_and_line():
    result = check_text(PROGRAM, "jaws.nc", config())
    assert fixture_issues(result) == [
        ("fixture", 7, "T1 cuts into fixture jaw (0.1000 in deep)"),
        ("fixture", 12, "T1 cuts into fixture clamp (0.1000 in deep)"),
        # tip at 1.1, holder bottom at 2.1, block top 2.3: the holder (radius 1) reaches Y4.5
        ("fixture", 15, "Holder of T1 hits fixture block (0.2000 in interference)"),
    ]
    assert result.status == "fail"
    assert [f["name"] for f in result.to_dict()["fixtures"]] == ["jaw", "clamp", "block"]


def test_no_fixtures_no_fixture_issues():
    plain = config()
    plain.fixtures = []
    assert fixture_issues(check_text(PROGRAM, "jaws.nc", plain)) == []


def test_placement_rotates_scales_and_offsets():
    # a 1 x 2 x 1 box in mm, scaled 10x, turned 90 degrees, moved in inches
    cfg = config_from_dict({"units": "inch", "fixtures": [
        {"min": [0, 0, 0], "max": [1, 2, 1], "units": "mm", "scale": 10, "rotate": 90, "offset": [1, 2, 3]}]})
    (fx,) = place_fixtures(cfg, "inch")
    lo, hi = fx.bounds
    k = 10 / 25.4
    assert lo == pytest.approx([1 - 2 * k, 2, 3], abs=1e-6)
    assert hi == pytest.approx([1, 2 + k, 3 + k], abs=1e-6)
    # a program in mm sees the same fixture in mm
    (mm,) = place_fixtures(cfg, "mm")
    assert mm.bounds[1] == pytest.approx([25.4, 50.8 + 10, 76.2 + 10], abs=1e-4)


def stl_files(tmp_path):
    tris = _box((0, 0, 0), (1, 1, 0.5))
    binary = tmp_path / "box.stl"
    with open(binary, "wb") as f:
        f.write(b"solid but really binary".ljust(80, b" ") + struct.pack("<I", len(tris) // 9))
        for i in range(0, len(tris), 9):
            f.write(struct.pack("<3f", 0, 0, 0) + struct.pack("<9f", *tris[i:i + 9]) + b"\0\0")
    ascii_ = tmp_path / "box_ascii.stl"
    facets = "".join("facet normal 0 0 0\n outer loop\n" + "".join(
        f"  vertex {tris[i + 3 * k]:e} {tris[i + 3 * k + 1]:e} {tris[i + 3 * k + 2]:e}\n" for k in range(3))
        + " endloop\nendfacet\n" for i in range(0, len(tris), 9))
    ascii_.write_text(f"solid box\n{facets}endsolid box\n")
    return tris, binary, ascii_


def test_reads_binary_and_ascii_stl(tmp_path):
    tris, binary, ascii_ = stl_files(tmp_path)
    assert list(read_stl(binary)) == list(tris)
    assert list(read_stl(ascii_)) == list(tris)
    (tmp_path / "junk.stl").write_text("not a mesh")
    with pytest.raises(ValueError, match="not an STL"):
        read_stl(tmp_path / "junk.stl")


def test_model_paths_are_relative_to_the_config_file(tmp_path):
    stl_files(tmp_path)
    (tmp_path / "emucraft.toml").write_text('[[fixtures]]\nmodel = "box.stl"\noffset = [4.2, 0.5, 0]\n')
    cfg = load_config(tmp_path / "emucraft.toml")
    assert cfg.fixtures[0].model == str((tmp_path / "box.stl").resolve())
    assert cfg.fixtures[0].label == "box"
    program = "(MIN X: 0)\n(MAX X: 4)\n(MIN Y: 0)\n(MAX Y: 2)\n(MIN Z: 0)\n(MAX Z: 1)\n" \
              "G20\nT1 M6\n(DIAMETER: 0.5)\nM3 S1000\nG0 X1 Y1 Z1.5\nG1 Z0.4 F10\nG1 X4.1 F20\nG0 Z2\nM30\n"
    result = check_text(program, "p.nc", cfg)
    assert fixture_issues(result) == [("fixture", 13, "T1 cuts into fixture box (0.1000 in deep)")]


@pytest.mark.parametrize("fixture, message", [
    ({"model": "missing.stl"}, "not found"),
    ({"model": "x.stl", "min": [0, 0, 0], "max": [1, 1, 1]}, "either model"),
    ({}, "either model"),
    ({"min": [0, 0, 0], "max": [1, 0, 1]}, "min = "),
    ({"min": [0, 0, 0], "max": [1, 1, 1], "units": "cm"}, "units"),
    ({"min": [0, 0, 0], "max": [1, 1, 1], "scale": 0}, "scale"),
    ({"min": [0, 0, 0], "max": [1, 1, 1], "offset": [1, 2]}, "offset"),
    ({"min": [0, 0, 0], "max": [1, 1, 1], "colour": "red"}, "unknown key"),
])
def test_bad_fixture_config(tmp_path, fixture, message):
    with pytest.raises(ConfigError, match=message):
        config_from_dict({"fixtures": [fixture]}, tmp_path)


def test_add_mesh_takes_the_top_of_the_mesh():
    # a ramp rising 0.5 per unit in X over a box: the top is the higher of the two
    ramp = array("f", [0, 0, 0, 2, 0, 1, 2, 2, 1, 0, 0, 0, 2, 2, 1, 0, 2, 0])
    owner = array("b", [-1]) * 16
    with Sim(4, 4, 0, 0, 0.5, -1, -1) as sim:
        sim.add_mesh(_box((1, 0, 0), (2, 1, 0.6)), 0, owner)
        sim.add_mesh(ramp, 1, owner)
        h = list(sim.heights())
    for iy in range(4):
        for ix in range(4):
            x, y = (ix + 0.5) * 0.5, (iy + 0.5) * 0.5
            box = 0.6 if x > 1 and y < 1 else -1
            assert h[iy * 4 + ix] == pytest.approx(max(box, x / 2), abs=1e-6)
            assert owner[iy * 4 + ix] == (0 if box > x / 2 else 1)


@pytest.fixture(scope="module")
def vise():
    return check_file(EXAMPLES / "vise" / "vise_demo.nc")


def test_vise_demo(vise):
    assert [(i.kind, i.first_line + 1, i.last_line + 1) for i in vise.issues] == [
        ("fixture", 47, 51),  # outside profile through both jaws
        ("fixture", 62, 64),  # T2's short holder over the fixed jaw
        ("holder", 62, 64),  # ... and over the stock
    ]
    assert "T1 cuts into fixture Kurt vise" in vise.issues[0].message
    assert vise.issues[1].message.startswith("Holder of T2 hits fixture Kurt vise")


def test_payload_carries_the_fixture_mesh(vise):
    (fx,) = analysis_payload(vise)["fixtures"]
    tris = array("f", gzip.decompress(base64.b64decode(fx["triangles_gz"])))
    assert fx["name"] == "Kurt vise" and tris == vise.fixtures[0].triangles
