"""Fixtures: a second block that the tool, shank and holder must never touch."""

import base64
import gzip
import struct
from array import array
from pathlib import Path

import pytest

from emucraft.check import check_file, check_text
from emucraft.config import ConfigError, config_from_dict, config_to_dict, load_config
from emucraft.fixture import (
    _box,
    clean_name,
    delete_model,
    fixture_specs,
    list_models,
    place_fixtures,
    read_stl,
    save_model,
    save_setup,
)
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


def placed(entry, units="inch", config=None):
    (fx,) = place_fixtures(config or config_from_dict({"fixtures": [entry]}), units)
    return fx


def test_at_puts_the_center_there_however_far_the_models_origin_is():
    entry = {"min": [500, 100, 300], "max": [540, 120, 380], "units": "mm", "at": [2, 3, -1]}
    fx = placed(entry)
    assert [(a + b) / 2 for a, b in zip(*fx.bounds)] == pytest.approx([2, 3, -1], abs=1e-4)
    assert fx.spec.position_units == "inch" and fx.spec.at == (2, 3, -1)
    # a turn keeps the center: a long part just swaps its X and Y sizes
    lo, hi = placed({**entry, "rotate": 90}).bounds
    assert [(a + b) / 2 for a, b in zip(lo, hi)] == pytest.approx([2, 3, -1], abs=1e-4)
    assert hi[0] - lo[0] == pytest.approx(20 / 25.4, abs=1e-4) and hi[1] - lo[1] == pytest.approx(40 / 25.4, abs=1e-4)


def test_turning_about_x_stands_a_y_up_model_up():
    # a 1 x 3 x 2 block: a quarter turn about X takes its Y height to Z
    lo, hi = placed({"min": [0, 0, 0], "max": [1, 3, 2], "rotate": [90, 0, 0], "at": [0, 0, 0]}).bounds
    assert [b - a for a, b in zip(lo, hi)] == pytest.approx([1, 2, 3], abs=1e-5)


def test_rotations_apply_about_x_then_y_then_z():
    # (x, y, z) -> (z, y, -x) about Y, then -> (-y, z, -x) about Z; the other order would keep Z above 0
    fx = placed({"min": [0, 0, 0], "max": [1, 0.1, 0.1], "rotate": [0, 90, 90]})
    assert min(fx.triangles[2::3]) == pytest.approx(-1, abs=1e-5)
    assert min(fx.triangles[0::3]) == pytest.approx(-0.1, abs=1e-5)


def test_position_units_differ_from_the_models():
    fx = placed({"min": [0, 0, 0], "max": [25.4, 25.4, 25.4], "units": "mm", "at": [50.8, 0, 0], "position_units": "mm"})
    assert fx.bounds[1][0] == pytest.approx(2.5, abs=1e-5)


def test_placed_spec_rebuilds_the_same_fixture_in_any_units():
    """The viewer sends a fixture back as the report gave it, and setups keep it for later programs."""
    for units in ("inch", "mm"):
        cfg = load_config(EXAMPLES / "vise" / "emucraft.toml")
        (fx,) = place_fixtures(cfg, units)
        spec = fx.to_dict()["spec"]
        assert "offset" not in spec and spec["position_units"] == units and spec["units"] == "mm"
        (again,) = place_fixtures(cfg, units, fixture_specs([spec]))
        assert again.bounds[0] == pytest.approx(fx.bounds[0], abs=1e-3)
        assert again.bounds[1] == pytest.approx(fx.bounds[1], abs=1e-3)
        (other,) = place_fixtures(cfg, "mm" if units == "inch" else "inch", fixture_specs([spec]))
        k = 25.4 if units == "inch" else 1 / 25.4
        assert [v / k for v in other.bounds[1]] == pytest.approx(fx.bounds[1], abs=1e-3)


def write_stl(path, lo, hi):
    tris = _box(lo, hi)
    with open(path, "wb") as f:
        f.write(b" " * 80 + struct.pack("<I", len(tris) // 9))
        for i in range(0, len(tris), 9):
            f.write(b"\0" * 12 + struct.pack("<9f", *tris[i:i + 9]) + b"\0\0")
    return path.read_bytes()


def test_library_keeps_models_under_clean_names(tmp_path):
    data = write_stl(tmp_path / "x.stl", (0, 0, 0), (4, 2, 1))
    saved = save_model("Toe Clamp (rev B).STL", data)
    assert saved["name"] == "toe-clamp-rev-b.stl"
    assert list(saved["bounds"]) == [[0, 0, 0], [4, 2, 1]] and saved["triangles"] == 12
    assert [m["name"] for m in list_models()] == ["toe-clamp-rev-b.stl"]
    with pytest.raises(FileExistsError):
        save_model("toe-clamp-rev-b", data)
    assert save_model("toe-clamp-rev-b", write_stl(tmp_path / "y.stl", (0, 0, 0), (1, 1, 1)), overwrite=True)["bounds"][1] == [1, 1, 1]
    delete_model("toe-clamp-rev-b.stl")
    assert list_models() == []


@pytest.mark.parametrize("data", [b"", b"not a mesh", b"solid x\nvertex 1 2 3\nendsolid",
                                  b" " * 80 + struct.pack("<I", 0),
                                  b" " * 84 + struct.pack("<I", 1)[:0] + b"\0" * 12 + struct.pack("<9f", *[float("nan")] * 9) + b"\0\0"])
def test_library_refuses_what_is_not_a_mesh(data):
    if len(data) == 134:  # one triangle
        data = data[:80] + struct.pack("<I", 1) + data[84:]
    with pytest.raises(ValueError, match="not an STL"):
        save_model("junk", data)
    assert list_models() == []


@pytest.mark.parametrize("name", ["", "...", "---", "()"])
def test_a_name_with_nothing_left_is_refused(name):
    with pytest.raises(ValueError, match="cannot name"):
        save_model(name, b"")
    with pytest.raises(ValueError, match="cannot name"):
        save_setup(name, [])


def test_names_cannot_leave_their_folder():
    assert "/" not in clean_name("../../etc/passwd") and ".." != clean_name("..")


def test_a_config_finds_a_library_model_by_name(tmp_path):
    save_model("plate", write_stl(tmp_path / "x.stl", (0, 0, 0), (1, 1, 1)))
    (tmp_path / "emucraft.toml").write_text('[[fixtures]]\nmodel = "plate.stl"\n')
    assert Path(load_config(tmp_path / "emucraft.toml").fixtures[0].model).parent.name == "fixtures"


CLAMP = {"name": "clamp", "min": [0, 0, 0], "max": [1, 1, 1], "units": "inch", "position_units": "inch", "at": [9, 9, 0]}
PLAIN = "G20\nG0 X0 Y0 Z1\nM30\n"
NAMED = "(FIXTURE: Kurt 6in)\nG20\nG0 X0 Y0 Z1\nM30\n"


def names(result):
    return [f.name for f in result.fixtures]


def test_a_comment_picks_a_saved_setup_written_any_way():
    save_setup("kurt-6in", fixture_specs([CLAMP]))
    result = check_text(NAMED, "part.nc", config())
    assert names(result) == ["clamp"]
    assert result.fixture_source == "setup kurt-6in (program line 1)"
    assert result.to_dict()["program"]["fixture"] == "Kurt 6in"
    # posts often force comments to upper case
    assert names(check_text(NAMED.replace("Kurt 6in", "KURT 6IN"), "part.nc")) == ["clamp"]


def test_a_setup_named_after_the_program_applies_by_itself():
    save_setup("bracket", fixture_specs([CLAMP]))
    assert check_text(PLAIN, "Bracket.NC", config()).fixture_source == "setup bracket (named after the program)"
    assert names(check_text(PLAIN, "other.nc")) == []


def test_the_comment_outranks_the_program_name_and_the_config():
    save_setup("kurt-6in", fixture_specs([CLAMP]))
    save_setup("part", fixture_specs([{**CLAMP, "name": "by name"}]))
    assert names(check_text(NAMED, "part.nc", config())) == ["clamp"]
    assert names(check_text(PLAIN, "part.nc", config())) == ["by name"]
    assert check_text(PLAIN, "other.nc", config()).fixture_source == "config"


def test_fixtures_passed_for_a_check_outrank_everything():
    save_setup("kurt-6in", fixture_specs([CLAMP]))
    result = check_text(NAMED, "part.nc", config(), fixtures=fixture_specs([{**CLAMP, "name": "here"}]))
    assert names(result) == ["here"] and result.fixture_source == "this check"
    none = check_text(NAMED, "part.nc", config(), fixtures=[])
    assert names(none) == [] and none.fixture_source is None


def test_a_named_setup_that_is_missing_is_a_warning_not_a_pass():
    stock = config_from_dict({"stock": {"min": [0, 0, 0], "max": [1, 1, 1]}})
    result = check_text(NAMED, "part.nc", stock)
    assert result.status == "warn" and result.fixtures == []
    (message,) = [m for m in result.messages if m.code == "fixtures"]
    assert "kurt-6in is not saved; no fixtures were checked" in message.text and message.line == 0
    # the config's fixtures still stand in for it
    fallback = check_text(NAMED, "part.nc", config())
    assert len(fallback.fixtures) == 3 and "using the config's fixtures" in fallback.messages[-1].text


def test_a_setup_whose_model_is_gone_fails_the_check_loudly(tmp_path):
    save_model("vise", write_stl(tmp_path / "x.stl", (0, 0, 0), (1, 1, 1)))
    save_setup("part", fixture_specs([{"model": "vise.stl"}]))
    delete_model("vise")
    result = check_text(PLAIN, "part.nc", config())
    assert result.status == "fail" and result.fixtures == []
    assert any(m.code == "fixtures" and "cannot be used" in m.text for m in result.messages)


def test_your_own_comment_can_name_the_setup():
    save_setup("kurt", fixture_specs([CLAMP]))
    cfg = config_from_dict({"comment_rules": [{"pattern": r"^VISE\s*:\s*(.+)$", "field": "program.fixture"}]})
    assert names(check_text("(VISE: KURT)\nG20\nG0 X0 Y0 Z1\nM30\n", "part.nc", cfg)) == ["clamp"]


def test_ordinary_comments_do_not_name_a_fixture():
    for comment in ("(CLEAR OF THE FIXTURE)", "(FIXTURE)", "(CHECK FIXTURE: BOLTS TIGHT BEFORE RUNNING)"):
        assert check_text(f"{comment}\nG20\nG0 X0 Y0 Z1\nM30\n", "part.nc").program.fixture_note is None


def test_config_survives_the_trip_through_a_saved_simulation():
    for source in (EXAMPLES / "emucraft.toml", EXAMPLES / "vise" / "emucraft.toml", EXAMPLES / "workholding" / "emucraft.toml"):
        cfg = load_config(source)
        assert config_from_dict(config_to_dict(cfg), source.parent) == cfg
    cfg = config_from_dict({
        "units": "mm", "stock": {"min": [0, 0, 0], "max": [1, 1, 1]}, "machine": {"limits": {"x": [0, 9]}},
        "tools": {"3": {"diameter": 6}}, "default_tool": {"stickout": 9},
        "comment_rules": [{"pattern": "HD=(\\d+)", "field": "tool.holder_diameter"}],
        "variable_rules": {"500": "tool.stickout"}, "comment_aliases": {"tool.stickout": ["reach"]},
        "fixtures": [{"min": [0, 0, 0], "max": [1, 1, 1], "rotate": [1, 2, 3], "at": [4, 5, 6], "position_units": "inch"}]})
    assert config_from_dict(config_to_dict(cfg)) == cfg
