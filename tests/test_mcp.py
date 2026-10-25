"""MCP server tools, called through an in-process MCP client."""

import asyncio
import json

import pytest

pytest.importorskip("mcp")

from mcp import Client  # noqa: E402

from emucraft import mcp_server  # noqa: E402
from helpers import EXAMPLES  # noqa: E402


@pytest.fixture(autouse=True)
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "mcp.json"
    monkeypatch.setenv("EMUCRAFT_MCP_CONFIG", str(path))
    return path


def call(tool, **args):
    async def run():
        async with Client(mcp_server.server) as client:
            return await client.call_tool(tool, args)

    result = asyncio.run(run())
    if result.is_error:
        raise RuntimeError(result.content[0].text)
    return json.loads(result.content[0].text)


HEADER = """\
T1 M6
(MIN X: 0)
(MAX X: 2)
(MIN Y: 0)
(MAX Y: 2)
(MIN Z: 0)
(MAX Z: 1)
(DIAMETER: 0.25)
M3 S10000
G0 X-0.5 Y1 Z2
"""


def test_check_program_finds_the_demo_crashes():
    report = call("check_program", path=str(EXAMPLES / "crash_demo.nc"))
    assert report["status"] == "fail"
    assert [i["kind"] for i in report["issues"]] == ["rapid-cut", "spindle-off-cut", "holder", "shank", "table"]
    assert report["config"] == str(EXAMPLES / "emucraft.toml")  # found next to the program


def test_check_program_inline_with_overrides():
    rapid = HEADER + "Z0.5\nG0 X1\nM30\n"
    report = call("check_program", gcode=rapid)
    assert [(i["kind"], i["lines"]) for i in report["issues"]] == [("rapid-cut", [12, 12])]
    assert report["config"] is None

    # a holder only 0.3 above the tip, 0.5 into the stock, rubs on a feed move
    feed = HEADER + "G1 Z0.5 F20\nX1\nG0 Z2\nM30\n"
    assert call("check_program", gcode=feed)["status"] == "pass"
    report = call("check_program", gcode=feed, tools={"T1": {"holder_diameter": 1.0, "stickout": 0.3}})
    assert [i["kind"] for i in report["issues"]] == ["holder"]
    assert report["tools"]["1"]["source"]["holder_diameter"] == "override"

    # a stock override that the toolpath stays clear of
    assert call("check_program", gcode=rapid, stock=[5, 5, 0, 6, 6, 1])["issues"] == []


def test_check_program_bad_input():
    with pytest.raises(RuntimeError, match="exactly one"):
        call("check_program")
    with pytest.raises(RuntimeError, match="6 numbers"):
        call("check_program", gcode=HEADER, stock=[0, 0, 0])
    with pytest.raises(RuntimeError, match="unknown tool field"):
        call("check_program", gcode=HEADER, tools={"1": {"colour": 3}})


def test_program_info_shows_where_values_came_from():
    info = call("program_info", path=str(EXAMPLES / "makino_roughing.nc"))
    found = {d["target"]: d for d in info["detections"]}
    assert found["tool.holder_length"]["text"] == "#127 = 3.9216 (REGOFIX TOOL HOLDER LENGTH)"
    assert found["tool.diameter"]["value"] == 0.375
    assert info["stock"]["source"] == "program"

    info = call("program_info", gcode="T3 M6\n(HOLDER DIAMTER: 2)\n(LOCK: 1)\n")
    assert [(d["target"], d["line"]) for d in info["detections"]] == [("tool.holder_diameter", 2)]
    assert [m["code"] for m in info["messages"] if m["line"] == 2] == ["comment-label"]


def test_settings_tune_detection(settings_file):
    text = "T3 M6\n(GAUGE FROM HOLDER: 1.4)\n(HOLDER DIAMTER: 2)\n"
    assert call("program_info", gcode=text)["detections"][0]["target"] == "tool.holder_diameter"

    saved = call("update_settings", changes={
        "comment_aliases": {"tool.stickout": ["gauge from holder"]},
        "check": {"fuzz_threshold": 1.0},
    })
    assert saved["settings_file"] == str(settings_file)
    info = call("program_info", gcode=text)
    assert [(d["target"], d["value"]) for d in info["detections"]] == [("tool.stickout", 1.4)]
    assert info["config"] == str(settings_file)

    # tables merge, null removes a key; the file stays a valid emucraft config
    call("update_settings", changes={"check": {"link_feed": 700, "fuzz_threshold": None}})
    assert call("get_settings")["settings"] == {
        "comment_aliases": {"tool.stickout": ["gauge from holder"]}, "check": {"link_feed": 700},
    }

    with pytest.raises(RuntimeError, match="Settings not saved.*cannot store"):
        call("update_settings", changes={"comment_aliases": {"tool.colour": ["colour"]}})
    assert call("reset_settings")["settings"] == {}
    assert not settings_file.exists()
    assert call("get_settings")["defaults"]["fuzz_threshold"] == 0.85
