"""Workholding: cutting loads from the simulated cut against vise and clamp friction."""

import json
import math

import pytest

from emucraft.check import check_text
from emucraft.cli import main
from emucraft.config import ConfigError, config_from_dict
from emucraft.workholding import MATERIALS, N_PER_LBF, NM_PER_FTLBF, tooth_peak

BLOCK = "( MIN X: 0.)\n( MIN Y: 0.)\n( MIN Z: -1.)\n( MAX X: 4.)\n( MAX Y: 2.)\n( MAX Z: 0.)\n"
MILL = "( Tool Number: 1)\n( Tool:   End Mill)\n( DIAMETER: 0.5)\n( Number of Flutes: 4)\n"
# a 0.25 deep full-width slot along X through the middle of the block, line 17
SLOT = BLOCK + MILL + """G20 G17 G90
T1 M6
S3000 M3
G0 X-0.5 Y1.0
G0 Z0.1
G1 Z-0.25 F20.
G1 X4.5 F12.
G0 Z0.1
M30
"""
SLOT_LINE = 17
VISE = {"material": "steel", "fixture": "vise", "grip": 0.5, "force_per_torque": 100, "max_torque": 80}
CORNERS = [[0.25, 0.25], [3.75, 0.25], [0.25, 1.75], [3.75, 1.75]]


def hold(text=SLOT, **options):
    config = config_from_dict({"workholding": options})
    config.check.resolution = 0.005
    return check_text(text, "part.nc", config)


def slot_tangential(material="steel", wear=1.5):
    """Peak tangential force (N) of the slot by hand: Kienzle per tooth, two of
    the four straight teeth in the cut at 45 and 135 degrees."""
    kc1, mc = MATERIALS[material]
    ap, fz = 0.25 * 25.4, 12.0 / 3000 / 4 * 25.4
    return kc1 * ap * fz ** (1 - mc) * 2 * math.sin(math.pi / 4) ** (1 - mc) * wear


def test_tooth_peak():
    assert tooth_peak(2, 1.0, 0.25) == pytest.approx(1.0)  # one tooth at a time, peak at 90 degrees
    assert tooth_peak(3, 1.0, 0.25) == pytest.approx(2 * 0.5 ** 0.75, rel=1e-3)  # two teeth at 30 and 150
    # a thin cut: one tooth, thickest where it leaves the cut
    assert tooth_peak(4, 0.05, 0.25) == pytest.approx(math.sin(math.acos(0.9)) ** 0.75)


def test_slot_load_follows_kienzle():
    w = hold(**VISE).workholding
    worst = w["worst"]
    tangential = slot_tangential() / N_PER_LBF
    assert worst["side_force"] == pytest.approx(tangential * math.sqrt(1.25), rel=0.01)
    assert worst["lift"] == pytest.approx(tangential * 0.5, rel=0.01)
    assert worst["cutting_torque"] == pytest.approx(tangential * 0.25 / 12, rel=0.01)  # ft-lbf
    assert worst["axial_depth"] == pytest.approx(0.25, abs=1e-3)
    assert (worst["feed"], worst["rpm"], worst["tool"]) == (12.0, 3000.0, 1)
    assert w["units"] == {"force": "lbf", "torque": "ft-lbf"}


def test_vise_torque_passes_above_the_requirement_and_fails_below():
    need = hold(**VISE).workholding["required_torque"]
    assert 5 < need < 80
    weak = hold(**VISE, torque=need * 0.95)
    assert weak.status == "fail"
    assert [(i.kind, i.first_line + 1) for i in weak.issues] == [("workholding", SLOT_LINE)]
    assert "rocks in the jaws" in weak.issues[0].message
    assert weak.summary() == "0 collisions, 1 workholding failure"
    assert weak.workholding["margin"] == pytest.approx(2.0 * 0.95, rel=1e-6)
    strong = hold(**VISE, torque=need * 1.05)
    assert strong.status == "pass" and not strong.issues
    assert strong.workholding["margin"] == pytest.approx(2.0 * 1.05, rel=1e-6)
    assert strong.workholding["force"] == pytest.approx(need * 1.05 * 100)


def test_requirement_scales_with_safety_and_wear():
    base = hold(**VISE).workholding["required_torque"]
    assert hold(**VISE, safety_factor=4).workholding["required_torque"] == pytest.approx(2 * base, rel=1e-6)
    assert hold(**VISE, wear_factor=1).workholding["required_torque"] == pytest.approx(base / 1.5, rel=1e-6)


def test_vise_rocking_is_worst_at_the_ends_of_the_jaws():
    w = hold(**VISE).workholding
    assert w["worst"]["mode"] == "rocks in the jaws"
    assert w["worst"]["position"][0] == pytest.approx(0.0, abs=0.13)  # entering at the end of the jaws
    deeper = hold(**{**VISE, "grip": 0.9}).workholding  # less moment arm for the side force
    assert deeper["required_force"] < w["required_force"]
    short = hold(**VISE, jaw_width=2.0).workholding  # the jaws grip only the middle 2 in
    assert short["required_force"] > w["required_force"]


def test_lift_stop_leaves_only_sliding():
    """With the lift held positively, the jaws only have to stop the side force
    sliding: 2 * friction * force >= safety factor * side force."""
    w = hold(**VISE, lift_stop=True).workholding
    side = slot_tangential() * math.sqrt(1.25)
    assert w["worst"]["mode"] == "slides along the jaws"
    assert w["required_force"] * N_PER_LBF == pytest.approx(2.0 * side / (2 * 0.15), rel=0.01)


def test_jaws_closing_along_x():
    w = hold(**VISE, axis="x").workholding
    # the jaws now run along Y over the 2 in side: the slot's lift sits in their middle
    assert w["worst"]["mode"] in ("rocks in the jaws", "pulls out of the jaws", "slides along the jaws")
    assert w["required_force"] > 0


def test_clamps_hold_the_slot_and_bolt_limit():
    r = hold(material="steel", fixture="clamps", clamps=CORNERS, bolt=0.625, torque=60)
    w = r.workholding
    assert r.status == "pass" and w["margin"] > 2
    # 5/8-11 grade 5 at 75 % of proof (85 ksi on 0.226 in^2), nut factor 0.2
    area = math.pi / 4 * (0.625 - 0.9743 / 11) ** 2
    assert w["max_torque"] == pytest.approx(0.2 * 0.75 * 85000 * area * 0.625 / 12, rel=0.01)
    # each clamp presses with half the bolt force: torque = K * F * d
    assert w["force"] == pytest.approx(0.5 * 60 * 12 / (0.2 * 0.625), rel=1e-6)
    assert w["required_torque"] == pytest.approx(60 * 2 / w["margin"], rel=1e-6)


def test_one_clamp_cannot_stop_the_part_turning():
    r = hold(material="steel", fixture="clamps", clamps=[[2.0, 1.0]], bolt=0.625)
    assert r.status == "fail"
    assert r.workholding["required_force"] is None
    assert r.issues[0].message.startswith("No clamping force holds this cut")


def test_clamps_at_one_end_let_the_part_swing():
    r = hold(material="steel", fixture="clamps", clamps=[[0.25, 0.25], [0.25, 1.75]], bolt=0.625, torque=60)
    w = r.workholding
    assert w["worst"]["mode"] == "slides or turns under the clamps"
    assert w["worst"]["position"][0] > 3.5  # the cut farthest from the clamps
    assert r.status == "fail"
    assert w["required_torque"] > 3 * hold(material="steel", fixture="clamps", clamps=CORNERS,
                                           bolt=0.625).workholding["required_torque"]


def test_a_tall_part_tips_over_an_edge():
    tall = SLOT.replace("( MIN Z: -1.)", "( MIN Z: -6.)").replace("( MAX Y: 2.)", "( MAX Y: 1.)")
    tall = tall.replace("Y1.0", "Y0.5")
    clamps = [[0.25, 0.1], [3.75, 0.1], [0.25, 0.9], [3.75, 0.9]]
    w = hold(tall, material="steel", fixture="clamps", clamps=clamps, bolt=0.625, torque=60).workholding
    assert w["worst"]["mode"] in ("tips over its Y- edge", "tips over its Y+ edge")


def test_drilling_torque_follows_the_power():
    """Mc = kc * f * D^2 / 8 for a drill, kc at the chip f / 2 * sin(59)."""
    text = BLOCK + """( Tool Number: 2)
( Tool:   Drill)
( DIAMETER: 0.5)
G20 G17 G90
T2 M6
S1000 M3
G0 X2. Y1. Z0.1
G81 Z-0.75 R0.1 F5.
G80
M30
"""
    w = hold(text, material="steel", fixture="clamps", clamps=CORNERS, bolt=0.625, torque=60).workholding
    kc1, mc = MATERIALS["steel"]
    f, d = 5.0 / 1000 * 25.4, 0.5 * 25.4
    kc = kc1 * (f / 2 * math.sin(math.radians(59))) ** -mc
    torque = kc * f * d ** 2 / 8 / 1000 * 1.5 / NM_PER_FTLBF  # N*mm -> ft-lbf, worn
    assert w["worst"]["cutting_torque"] == pytest.approx(torque, rel=0.03)
    assert w["worst"]["side_force"] == 0 and w["worst"]["lift"] == 0


def test_unknown_flutes_assume_one():
    text = SLOT.replace("( Number of Flutes: 4)\n", "")
    r = hold(text, **VISE)
    assert "workholding-flutes" in [m.code for m in r.messages]
    four = hold(**VISE).workholding["required_force"]
    # 0.025 mm chips become 0.1 mm: 4 ** 0.75 per tooth, against two of four teeth in the cut
    assert r.workholding["required_force"] == pytest.approx(four * 4 ** 0.75 / (2 * 0.5 ** 0.375), rel=0.02)
    config = config_from_dict({"workholding": VISE, "tools": {"1": {"flutes": 4}}})
    config.check.resolution = 0.005
    assert check_text(text, "part.nc", config).workholding["required_force"] == pytest.approx(four)


def test_cutting_without_a_spindle_speed_is_an_error():
    r = hold(SLOT.replace("S3000 M3", "M3"), **VISE)
    assert r.status == "fail"
    assert "workholding-speed" in [m.code for m in r.messages if m.level == "error"]


def test_metric_config_matches_inch():
    """The same vise in mm, N and N-m gives the same answer in program units."""
    inch = hold(**VISE, torque=40).workholding
    config = config_from_dict({"units": "mm", "workholding": {
        "material": "steel", "fixture": "vise", "grip": 12.7, "torque": 40 * NM_PER_FTLBF,
        "force_per_torque": 100 * N_PER_LBF / NM_PER_FTLBF, "max_torque": 80 * NM_PER_FTLBF}})
    config.check.resolution = 0.005
    mm = check_text(SLOT, "part.nc", config).workholding
    for key in ("margin", "required_force", "required_torque", "max_torque", "force"):
        assert mm[key] == pytest.approx(inch[key], rel=1e-6)


def test_material_from_the_optimizer_preset():
    config = config_from_dict({"optimize": {"material": "titanium"},
                               "workholding": {k: v for k, v in VISE.items() if k != "material"}})
    config.check.resolution = 0.005
    w = check_text(SLOT, "part.nc", config).workholding
    assert (w["material"], w["kc1"]) == ("titanium", MATERIALS["titanium"][0])


@pytest.mark.parametrize("options, error", [
    ({"fixture": "vise", "material": "steel"}, "grip"),
    ({**VISE, "torque": 40, "force": 4000}, "not both"),
    ({**VISE, "force_per_torque": None, "torque": 40}, "force_per_torque"),
    ({"fixture": "clamps", "material": "steel"}, "positions"),
    ({"fixture": "clamps", "material": "steel", "clamps": CORNERS, "torque": 40}, "bolt"),
    ({**VISE, "material": "unobtainium"}, "material"),
    ({**VISE, "material": None}, "material"),
    ({**VISE, "safety_factor": 0.5}, "at least 1"),
    ({**VISE, "clamp_force": 1}, "unknown workholding option"),
    ({**VISE, "clamps": [[1, 2, 3]]}, "[x, y]"),
])
def test_config_errors(options, error):
    with pytest.raises(ConfigError) as exc:
        config_from_dict({"workholding": {k: v for k, v in options.items() if v is not None}})
    assert error in str(exc.value)


def test_geometry_errors_are_reported():
    r = hold(**{**VISE, "grip": 1.5})
    assert [m.text for m in r.messages if m.code == "workholding"] == ["the vise grip is taller than the part"]
    r = hold(material="steel", fixture="clamps", clamps=[[9.0, 1.0], [0.5, 0.5]], bolt=0.625)
    assert "not on the part" in [m.text for m in r.messages if m.code == "workholding"][0]


def test_chained_finish_starts_from_the_roughed_stock(tmp_path):
    """A finishing pass along the slot wall sees only the stock the slot left."""
    rough = SLOT
    finish = BLOCK + MILL + """G20 G17 G90
T1 M6
S3000 M3
G0 X-0.5 Y1.02
G0 Z0.1
G1 Z-0.25 F20.
G1 X4.5 F12.
G0 Z0.1
M30
"""
    alone = hold(finish, **VISE).workholding
    config = config_from_dict({"workholding": VISE})
    config.check.resolution = 0.005
    from emucraft.check import parse_program, run_check

    first = run_check(parse_program(rough, "rough.nc", config), config, keep_sim=True)
    second = run_check(parse_program(finish, "finish.nc", config), config, previous=first)
    chained = second.workholding
    assert chained["worst"]["radial_width"] == pytest.approx(0.02, abs=0.01)
    assert chained["required_force"] < alone["required_force"] / 2


def test_cli_report(tmp_path, capsys):
    (tmp_path / "part.nc").write_text(SLOT)
    (tmp_path / "emucraft.toml").write_text(
        'units = "inch"\n[check]\nresolution = 0.005\n[workholding]\nmaterial = "steel"\nfixture = "vise"\n'
        'grip = 0.5\ntorque = 20\nforce_per_torque = 100\nmax_torque = 80\n')
    code = main(["check", str(tmp_path / "part.nc"), "--json", str(tmp_path / "out.json")])
    out = capsys.readouterr().out
    assert code == 1
    assert "Workholding: vise, steel" in out
    assert "20 ft-lbf (2000 lbf) is NOT enough" in out
    assert "the fixture takes up to 80 ft-lbf" in out
    data = json.loads((tmp_path / "out.json").read_text())
    assert data["workholding"]["torque"] == 20 and data["workholding"]["max_torque"] == 80
    assert [i["kind"] for i in data["issues"]] == ["workholding"]


def test_kurt_vise_from_its_manual_table():
    """vise = "d688": the manual's force at the torque, its 6 in jaws and 80 ft-lbf top."""
    r = hold(material="steel", vise="D688", grip=0.5, torque=40)
    w = r.workholding
    assert w["force"] == pytest.approx(4148)  # straight off the table
    assert hold(material="steel", vise="d688", grip=0.5, torque=45).workholding["force"] == pytest.approx(
        (4148 + 5379) / 2)
    assert (w["max_torque"], w["jaw_width"], w["vise"]) == (pytest.approx(80), pytest.approx(6.0), "d688")
    # the curve is not linear: the requirement is read back off it, and holds exactly
    need = w["required_torque"]
    assert hold(material="steel", vise="d688", grip=0.5, torque=need).workholding["margin"] == pytest.approx(2.0)


def test_self_centering_vise_stops_at_60_ft_lbf():
    r = hold(material="steel", vise="scd640", grip=0.5, torque=70)
    assert r.workholding["max_torque"] == pytest.approx(60)
    assert "workholding-torque" in [m.code for m in r.messages if m.level == "error"]
