"""Text / JUnit reports and the command line."""

import json
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from emucraft import __version__
from emucraft.check import check_file, check_text
from emucraft.cli import main, parse_tool_override
from emucraft.model import Config
from emucraft.report import format_text, junit_xml, to_json
from helpers import EXAMPLES, ROOT

CRASH = str(EXAMPLES / "crash_demo.nc")
MAKINO = str(EXAMPLES / "makino_roughing.nc")
POCKET = str(EXAMPLES / "pocket_corners.nc")


@pytest.fixture(scope="module")
def crash():
    return check_file(CRASH, cell=0.005)


@pytest.fixture(scope="module")
def makino():
    return check_file(MAKINO, cell=0.005)


def run_cli(capsys, *args):
    code = main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


# ------------------------------------------------------------------ reports


def test_text_report_for_a_failing_program(crash):
    text = format_text(crash)
    assert text.startswith("crash_demo.nc: inch, 58 lines, 22 moves")
    assert "FAIL  5 collisions" in text
    assert "x line 29          rapid-cut" in text
    assert "x lines 44-46      holder" in text
    assert "Tool holder of T2 hits the stock (0.1500 in interference)" in text
    assert "T3  cone  D0.2500  holder not checked (needs holder_diameter and stickout)" in text
    assert "stock  X0.0000..4.0000  Y0.0000..3.0000  Z-1.0000..0.0000  (program)" in text
    assert "Cycle time" in text and "3 tool changes" in text
    assert "Checked in" in text


def test_text_report_for_a_clean_program(makino):
    text = format_text(makino)
    assert "WARN  0 collisions, 5 program warnings" in text
    assert "T20  flat  D0.3750  flutes 1.0000  stick-out 1.4000  holder D1.3000" in text
    assert "! line 352     M#150 uses an undefined variable; word ignored (x9)" in text
    assert "Cycle time 7:" in text
    assert "start-position" not in text and "machine position" not in text  # info hidden...
    assert "machine position before the first move is unknown" in format_text(makino, verbose=True)


def test_text_report_mm_and_problem_tools():
    result = check_text("G21\nT3 M6\nS100 M3\nG0 X0 Y0 Z10\nG1 Z-1 F100\n", "mm.nc", Config(),
                        stock=None)
    text = format_text(result)
    assert "mm" in text and "D?" in text
    assert "T3 cannot be simulated" in text


def test_json_report(crash):
    data = json.loads(to_json(crash))
    assert data["emucraft"] == __version__
    assert data["status"] == "fail"
    assert [i["kind"] for i in data["issues"]] == ["rapid-cut", "spindle-off-cut", "holder", "shank", "table"]


def test_junit_xml(crash, makino):
    root = ET.fromstring(junit_xml([crash, makino]))
    assert root.tag == "testsuites"
    assert (root.get("tests"), root.get("failures")) == ("6", "1")
    suites = {s.get("name"): s for s in root}
    assert set(suites) == {"crash_demo.nc", "makino_roughing.nc"}
    failing = suites["crash_demo.nc"]
    assert failing.get("failures") == "1"
    failure = failing.find("testcase[@name='collisions']/failure")
    assert failure.get("message") == "5 collisions"
    assert "line 29: Rapid move cuts material" in failure.text
    assert suites["makino_roughing.nc"].get("failures") == "0"
    assert [c.get("name") for c in suites["makino_roughing.nc"]] == ["collisions", "warnings", "program"]


def test_junit_escapes_text():
    result = check_text("T1 M6\nG0 X0 Y0 Z1\nG1 X1\n", 'a<b>&"c".nc', Config())
    root = ET.fromstring(junit_xml([result]))  # program error: feed without F
    assert root.find("testsuite").get("name") == 'a<b>&"c".nc'
    assert root.find(".//testcase[@name='program']/failure") is not None


# ------------------------------------------------------------------ command line


def test_check_exit_codes(capsys):
    assert run_cli(capsys, "check", MAKINO, "-q")[0] == 0  # warnings only
    assert run_cli(capsys, "check", CRASH, "-q", "--resolution", "0.01")[0] == 1
    assert run_cli(capsys, "check", MAKINO, "-q", "--strict")[0] == 1
    code, out, err = run_cli(capsys, "check", str(EXAMPLES / "missing.nc"))
    assert code == 2 and "emucraft:" in err


def test_check_prints_the_report(capsys):
    code, out, _ = run_cli(capsys, "check", CRASH, "--resolution", "0.01")
    assert code == 1
    assert "FAIL  5 collisions" in out and "rapid-cut" in out


def test_bad_config_is_a_usage_error(capsys, tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("[check]\nbogus = 1\n")
    code, _, err = run_cli(capsys, "check", MAKINO, "-c", str(bad))
    assert code == 2 and "unknown key" in err
    broken = tmp_path / "broken.toml"
    broken.write_text("[check\n")
    assert run_cli(capsys, "check", MAKINO, "-c", str(broken))[0] == 2


def test_json_to_stdout_and_files(capsys, tmp_path):
    code, out, _ = run_cli(capsys, "check", CRASH, "-q", "--resolution", "0.01", "--json", "-")
    data = json.loads(out)
    assert code == 1 and data["status"] == "fail" and len(data["issues"]) == 5
    report = tmp_path / "report.json"
    junit = tmp_path / "junit.xml"
    code, out, _ = run_cli(capsys, "check", CRASH, POCKET, "-q", "--resolution", "0.01",
                           "--json", str(report), "--junit", str(junit))
    assert code == 1 and out == ""
    both = json.loads(report.read_text())
    assert [d["status"] for d in both] == ["fail", "pass"]
    assert ET.parse(junit).getroot().get("failures") == "1"


def test_tool_overrides(capsys):
    code, out, _ = run_cli(capsys, "check", MAKINO, "-q", "--resolution", "0.01", "--json", "-",
                           "--tool", "*:holder_diameter=2,stickout=0.05")
    data = json.loads(out)
    assert code == 1
    assert {i["kind"] for i in data["issues"]} == {"holder"}
    assert data["tools"]["20"]["stickout"] == 0.05
    assert data["tools"]["20"]["source"]["stickout"] == "override"


def test_tool_override_parsing():
    assert parse_tool_override("20:holder_diameter=1.5,stickout=1.4") == (20, {"holder_diameter": 1.5, "stickout": 1.4})
    assert parse_tool_override("T7:shape=ball,diameter=0.25") == (7, {"shape": "ball", "diameter": 0.25})
    assert parse_tool_override("*:holder_diameter=2") == ("*", {"holder_diameter": 2.0})
    for bad in ("20", "x:diameter=1", "20:diameter", "20:diameter=big"):
        with pytest.raises(Exception):
            parse_tool_override(bad)
    with pytest.raises(SystemExit) as exc:
        main(["check", MAKINO, "--tool", "20"])
    assert exc.value.code == 2


def test_setup_options(capsys):
    def issues(*extra):
        code, out, _ = run_cli(capsys, "check", CRASH, "-q", "--resolution", "0.01", "--json", "-", *extra)
        return code, [i["kind"] for i in json.loads(out)["issues"]]

    # a deeper stock (and so a lower table) makes the drill legal
    assert "table" not in issues("--stock", "0,0,-2,4,3,0")[1]
    assert "table" not in issues("--table-z", "-1.5")[1]
    assert issues("--rapid-mode", "dogleg")[0] == 1
    code, out, _ = run_cli(capsys, "check", MAKINO, "-q", "--resolution", "0.01", "--json", "-",
                           "--link-feed", "100")
    data = json.loads(out)
    assert code == 0 and data["grid"]["cell"] == 0.01


def test_optimize_command(capsys, tmp_path):
    out_file = tmp_path / "pocket.opt.nc"
    details = tmp_path / "details.json"
    code, out, err = run_cli(capsys, "optimize", POCKET, "-o", str(out_file), "--material", "titanium",
                             "--json", str(details))
    assert code == 0, err
    assert "corners" in out and "cutting time" in out
    optimized = out_file.read_text()
    assert optimized != (EXAMPLES / "pocket_corners.nc").read_text()
    assert json.loads(details.read_text())["verified"] is True
    # default output name next to the input
    copy = tmp_path / "part.nc"
    copy.write_text((EXAMPLES / "pocket_corners.nc").read_text())
    assert run_cli(capsys, "optimize", str(copy), "--corner-factor", "0.8", "--no-load")[0] == 0
    assert (tmp_path / "part.opt.nc").exists()


def test_optimize_command_errors(capsys, tmp_path):
    code, _, err = run_cli(capsys, "optimize", POCKET, "-o", str(tmp_path / "x.nc"), "--corner-factor", "2")
    assert code == 2 and "corner_factor" in err
    assert run_cli(capsys, "optimize", str(tmp_path / "missing.nc"))[0] == 2
    no_tool = tmp_path / "t9.nc"
    no_tool.write_text("T9 M6\nS100 M3\nG0 X0 Y0 Z1\nG1 X1 F10\n")
    code, _, err = run_cli(capsys, "optimize", str(no_tool), "-o", str(tmp_path / "t9.opt.nc"))
    assert code == 1 and "nothing written" in err
    assert not (tmp_path / "t9.opt.nc").exists()


def test_info_command(capsys):
    code, out, _ = run_cli(capsys, "info", MAKINO)
    data = json.loads(out)
    assert code == 0
    assert data["units"] == "inch" and data["moves"] == 7448
    assert data["tools"]["20"]["holder_diameter"] == 1.3  # examples/emucraft.toml
    assert data["stock"]["source"] == "program"
    code, out, _ = run_cli(capsys, "info", MAKINO, "--stock", "0,0,0,1,1,1", "--tool", "20:diameter=0.5")
    data = json.loads(out)
    assert data["stock"]["source"] == "command line" and data["tools"]["20"]["diameter"] == 0.5


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_module_entry_point():
    proc = subprocess.run([sys.executable, "-m", "emucraft", "check", CRASH, "-q", "--resolution", "0.02"],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 1, proc.stderr


def test_json_to_stdout_keeps_stdout_machine_readable(capsys):
    """Without -q the human report still shows, but on stderr."""
    code, out, err = run_cli(capsys, "check", CRASH, "--resolution", "0.02", "--json", "-")
    assert code == 1
    assert json.loads(out)["status"] == "fail"
    assert "FAIL  5 collisions" in err
