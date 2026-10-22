#!/usr/bin/env python
"""
Tests the Emucraft MCP server tools
"""
import asyncio
import json
import os
import shlex
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import mcp_server  # noqa: E402
from mcp import Client  # noqa: E402

BLOCK = """
(MIN X: 0)
(MAX X: 1)
(MIN Y: 0)
(MAX Y: 1)
(MIN Z: 0)
(MAX Z: 1)
(DIAMETER: 0.25)
(Tool Holder Diameter: 2.0)
#127 = 3.0
M3 S1000
G0 X-0.5 Y0.5 Z2.
G1 Z0.5 F10.
"""


@pytest.fixture(autouse=True)
def settings_path(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "SETTINGS_PATH", str(tmp_path / "detection.json"))


def call(tool, **args):
    """
    Calls a tool in-process and returns its decoded result
    """
    async def run():
        async with Client(mcp_server.server) as client:
            return await client.call_tool(tool, args)

    result = asyncio.run(run())
    if result.is_error:
        raise RuntimeError(result.content[0].text)
    if result.structured_content and "result" in result.structured_content:
        return result.structured_content["result"]
    return json.loads(result.content[0].text)


def test_analyze_program():
    result = call("analyze_program", gcode=BLOCK + "G1 X0.5\nM30")

    assert result["values"]["tool_diameter"] == 0.25
    assert result["values"]["tool_holder_length"] == 3.0
    assert result["block"] == {"x": [0, 1], "y": [0, 1], "z": [0, 1]}
    assert result["detections"]["tool_holder_diameter"]["line"] == "(Tool Holder Diameter: 2.0)"
    assert "shank_diameter" in result["missing"]
    assert "tool_diameter" not in result["missing"]


def test_analyze_program_rejects_unsupported_codes():
    with pytest.raises(RuntimeError, match="Invalid G-code"):
        call("analyze_program", gcode="G91 G0 X1.")


def test_update_detection_settings_persists():
    assert call("analyze_program", gcode="(ENDMILL SIZE: 0.5)")["values"]["tool_diameter"] == 0

    call("update_detection_settings", labels={"tool_diameter": ["endmill size"]}, fuzz_threshold=0.9)
    assert call("get_detection_settings")["fuzz_threshold"] == 0.9
    assert call("analyze_program", gcode="(ENDMILL SIZE: 0.5)")["values"]["tool_diameter"] == 0.5

    # Only the given field's aliases are replaced
    assert call("test_detection", text="(SHANK DIA: .5)")[0]["field"] == "shank_diameter"

    call("reset_detection_settings")
    assert call("get_detection_settings")["fuzz_threshold"] == 0.85


def test_update_detection_settings_validates():
    with pytest.raises(RuntimeError, match="Unknown field"):
        call("update_detection_settings", labels={"spindle_nose": ["nose"]})
    with pytest.raises(RuntimeError, match="must contain"):
        call("update_detection_settings", patterns={"tool_length": [r"#128\s*="]})
    with pytest.raises(RuntimeError, match="not a valid regex"):
        call("update_detection_settings", patterns={"tool_length": [r"#128 = ({num}"]})
    with pytest.raises(RuntimeError, match="units"):
        call("update_detection_settings", units="cm")


def test_generate_xyz(tmp_path):
    output = tmp_path / "out.xyz"
    result = call("generate_xyz", output_path=str(output), gcode=BLOCK + "G1 X0.5")

    lines = output.read_text().strip().split("\n")
    assert len(lines) == result["points"]
    assert lines[-1].split(" ")[3:] == ["250", "2000", "3000", "1"]
    assert min(int(line.split(" ")[0]) for line in lines) == 0


compiler = shlex.split(os.environ.get("EMUCRAFT_CC", "cc"))[0]
needs_compiler = pytest.mark.skipif(shutil.which(compiler) is None, reason="No C compiler for the kernel")


@needs_compiler
def test_simulate_clean_program():
    result = call("simulate", gcode=BLOCK + "G1 X0.5\nG0 Z2.\nM30")

    assert result["collision"] is False
    assert result["cells_cut"] > 0
    assert result["grid"] == {"resolution": 0.01, "x": 100, "y": 100, "height": 100}


@needs_compiler
def test_simulate_rapid_into_stock():
    result = call("simulate", gcode=BLOCK + "G0 X0.5\nG0 Z2.\nM30")

    assert result["collision"] is True
    assert result["collisions"][0]["gcode"] == "G0 X0.5"
    assert result["collision_lines"] == 1


@needs_compiler
def test_simulate_spindle_off_cut():
    result = call("simulate", gcode=BLOCK + "M5\nG1 X0.5\nM30")

    assert result["collision"] is True
    assert result["collisions"][0]["gcode"] == "G1 X0.5"


def test_simulate_needs_block():
    with pytest.raises(RuntimeError, match="Block size"):
        call("simulate", gcode="(DIAMETER: 0.25)\nG1 X1.")
