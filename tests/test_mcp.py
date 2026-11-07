"""MCP server tools, called through an in-process MCP client."""

import asyncio
import base64
import json
import struct
import threading
import urllib.request

import pytest

pytest.importorskip("mcp")

from mcp import Client  # noqa: E402

from emucraft import mcp_server  # noqa: E402
from emucraft.fixture import _box  # noqa: E402
from emucraft.server import make_server  # noqa: E402
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


@pytest.fixture
def served(monkeypatch):
    """The module state `emucraft mcp --http` sets; restored after the test."""
    def serve(folder=None):
        monkeypatch.setattr(mcp_server, "remote", True)
        monkeypatch.setattr(mcp_server, "programs", folder and folder.resolve())
    return serve


def test_http_without_a_programs_folder_takes_only_gcode(served):
    served()
    with pytest.raises(RuntimeError, match="pass the program as `gcode`"):
        call("program_info", path=str(EXAMPLES / "crash_demo.nc"))
    with pytest.raises(RuntimeError, match="no programs folder"):
        call("list_programs")
    assert call("program_info", gcode=HEADER)["stock"] is not None


def test_programs_folder_keeps_paths_inside(served):
    served(EXAMPLES)
    assert "crash_demo.nc" in call("list_programs")["programs"]
    assert call("program_info", path="crash_demo.nc")["name"] == "crash_demo.nc"
    for outside in ("../README.md", "/etc/passwd"):
        with pytest.raises(RuntimeError, match="outside the programs folder"):
            call("program_info", path=outside)
    with pytest.raises(RuntimeError, match="outside the programs folder"):
        call("program_info", gcode=HEADER, config_path="../pyproject.toml")


def test_fixture_models_stay_inside_the_programs_folder(served, tmp_path, settings_file):
    (tmp_path / "plate.stl").write_text("solid p\nendsolid p\n")
    settings_file.write_text(json.dumps({"fixtures": [{"model": str(tmp_path / "plate.stl")}]}))
    served(EXAMPLES)
    with pytest.raises(RuntimeError, match="fixture model .* is outside the programs folder"):
        call("check_program", gcode=HEADER)
    report = call("check_program", path="vise/vise_demo.nc")  # its own config, model next to it
    assert [i["kind"] for i in report["issues"]] == ["fixture", "fixture", "holder"]


def test_http_app_needs_the_token(monkeypatch):
    from starlette.testclient import TestClient

    monkeypatch.setenv("EMUCRAFT_TOKEN", "secret")
    app = mcp_server.guarded(mcp_server.server.streamable_http_app())
    with TestClient(app, base_url="http://127.0.0.1:8000") as http:
        assert http.get("/health").json() == {"status": "ok"}
        assert http.post("/mcp", json={}).status_code == 401
        hello = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
        got = http.post("/mcp", json=hello, headers={
            "Authorization": "Bearer secret", "Accept": "application/json, text/event-stream"})
        assert got.status_code == 200


def test_fixtures_for_one_call(served):
    served(EXAMPLES)
    box = [{"name": "toe clamp", "min": [-1, -1, -0.5], "max": [5, 4, -0.1]}]
    assert not call("check_program", gcode=HEADER)["fixtures"]
    report = call("check_program", gcode=HEADER, fixtures=box)
    assert [f["name"] for f in report["fixtures"]] == ["toe clamp"]
    with pytest.raises(RuntimeError, match="needs either model"):
        call("check_program", gcode=HEADER, fixtures=[{"name": "nothing"}])
    assert call("check_program", path="vise/vise_demo.nc", fixtures=[])["fixtures"] == []


def test_saved_fixture_models_are_relative_to_the_programs_folder(served, tmp_path, settings_file):
    served(EXAMPLES)
    saved = call("update_settings", changes={"fixtures": [{"name": "vise", "model": "vise/kurt.stl"}]})
    assert saved["settings"]["fixtures"][0]["model"] == str(EXAMPLES.resolve() / "vise" / "kurt.stl")
    assert [f["name"] for f in call("check_program", gcode=HEADER)["fixtures"]] == ["vise"]


def test_a_model_outside_the_programs_folder_is_not_saved(served, tmp_path, settings_file):
    (tmp_path / "plate.stl").write_text("solid p\nendsolid p\n")
    served(EXAMPLES)
    with pytest.raises(RuntimeError, match="Settings not saved: fixture model .* is outside the programs folder"):
        call("update_settings", changes={"fixtures": [{"model": str(tmp_path / "plate.stl")}]})
    assert not settings_file.exists()


def test_call_fixtures_take_their_units_like_the_configs_own(settings_file):
    mm = "G21\n" + HEADER
    box = {"name": "b", "min": [0, 0, -2], "max": [1, 1, -1]}
    assert call("check_program", gcode=mm, fixtures=[box])["fixtures"][0]["bounds"][1][0] == pytest.approx(1)
    call("update_settings", changes={"units": "inch"})
    assert call("check_program", gcode=mm, fixtures=[box])["fixtures"][0]["bounds"][1][0] == pytest.approx(25.4)
    explicit = {**box, "units": "mm"}
    assert call("check_program", gcode=mm, fixtures=[explicit])["fixtures"][0]["bounds"][1][0] == pytest.approx(1)


def stl(lo=(0, 0, 0), hi=(1, 1, 1)):
    tris = _box(lo, hi)
    out = b" " * 80 + struct.pack("<I", len(tris) // 9)
    for i in range(0, len(tris), 9):
        out += b"\0" * 12 + struct.pack("<9f", *tris[i:i + 9]) + b"\0\0"
    return out


CUT = HEADER + "G0 X1\nG1 Z0.5 F20\nG0 Z2\nM30\n"
BLOCK = {"name": "block", "model": "block.stl", "at": [1, 1, 0.5], "units": "inch", "position_units": "inch"}


def upload(name="block.stl", **sizes):
    return call("upload_fixture", name=name, data=base64.b64encode(stl(**sizes)).decode())


def test_upload_place_and_move_a_fixture(served):
    served()  # a remote server with no programs folder: the library is the only way in for a model
    model = upload(hi=(1, 1, 1))
    assert model["name"] == "block.stl" and model["bounds"] == [[0, 0, 0], [1, 1, 1]]
    assert call("list_fixtures")["models"][0]["name"] == "block.stl"
    report = call("check_program", gcode=CUT, fixtures=[BLOCK])
    assert [i["kind"] for i in report["issues"]] == ["fixture"]
    (fx,) = report["fixtures"]
    assert fx["bounds"] == [[0.5, 0.5, 0], [1.5, 1.5, 1]]
    moved = {**fx["spec"], "at": [8, 8, 0.5], "rotate": 45}
    assert call("check_program", gcode=CUT, fixtures=[moved])["issues"] == []


def test_upload_fixture_refuses_bad_files(served):
    served()
    with pytest.raises(RuntimeError, match="not an STL"):
        call("upload_fixture", name="x", data=base64.b64encode(b"nope").decode())
    with pytest.raises(RuntimeError):
        call("upload_fixture", name="x", data="!!! not base64 !!!")
    upload()
    with pytest.raises(RuntimeError, match="already in the library"):
        upload()
    assert call("upload_fixture", name="block", data=base64.b64encode(stl(hi=(2, 2, 2))).decode(),
                replace=True)["bounds"][1] == [2, 2, 2]
    assert call("delete_fixture", name="block.stl") == {"deleted": "block.stl"}
    with pytest.raises(RuntimeError, match="No fixture model"):
        call("delete_fixture", name="block.stl")


def test_a_library_model_is_not_a_way_out_of_the_guard(served, tmp_path):
    served()
    (tmp_path / "secret.stl").write_bytes(stl())
    with pytest.raises(RuntimeError, match="outside the programs folder"):
        call("check_program", gcode=CUT, fixtures=[{"model": str(tmp_path / "secret.stl")}])
    with pytest.raises(RuntimeError, match="outside the programs folder"):
        call("save_fixture_setup", name="x", fixtures=[{"model": str(tmp_path / "secret.stl")}])


def test_saved_setups_from_the_call_the_name_and_the_comment(served):
    served()
    upload()
    saved = call("save_fixture_setup", name="Kurt 6in", fixtures=[BLOCK])
    assert saved["name"] == "kurt-6in"
    assert call("list_fixtures")["setups"][0]["fixtures"][0]["model"] == "block.stl"
    by_call = call("check_program", gcode=CUT, fixture_setup="kurt-6in")
    assert [i["kind"] for i in by_call["issues"]] == ["fixture"] and by_call["fixture_source"] == "this check"
    by_comment = call("check_program", gcode="(FIXTURE: KURT 6IN)\n" + CUT)
    assert by_comment["fixture_source"] == "setup kurt-6in (program line 1)"
    call("save_fixture_setup", name="bracket", fixtures=[BLOCK])
    assert call("check_program", gcode=CUT, name="bracket.nc")["fixture_source"].startswith("setup bracket")
    assert call("check_program", gcode=CUT)["fixtures"] == []
    with pytest.raises(RuntimeError, match="No fixture setup"):
        call("check_program", gcode=CUT, fixture_setup="nope")
    with pytest.raises(RuntimeError, match="not both"):
        call("check_program", gcode=CUT, fixture_setup="kurt-6in", fixtures=[])
    assert call("delete_fixture_setup", name="kurt-6in") == {"deleted": "kurt-6in"}


def test_program_info_says_what_a_check_would_use(served):
    served()
    upload()
    call("save_fixture_setup", name="kurt", fixtures=[BLOCK])
    info = call("program_info", gcode="(FIXTURE: kurt)\n" + HEADER + "M30\n")
    assert info["fixtures"] == ["block"] and info["fixture_source"] == "setup kurt (program line 1)"
    assert {"target": "program.fixture", "value": "kurt", "line": 1, "text": "(FIXTURE: kurt)"} in info["detections"]
    missing = call("program_info", gcode="(FIXTURE: other)\n" + HEADER + "M30\n")
    assert missing["fixtures"] == [] and "other is not saved" in missing["messages"][-1]["text"]


def test_saved_settings_take_library_models_too(served, settings_file):
    served(EXAMPLES)
    upload()
    call("update_settings", changes={"fixtures": [BLOCK]})
    assert call("check_program", gcode=CUT)["fixtures"][0]["name"] == "block"


def test_every_check_links_to_its_simulation(monkeypatch):
    report = call("check_program", gcode=CUT, name="bracket.nc")
    assert report["simulation"]["url"] == f"http://127.0.0.1:8765/?sim={report['simulation']['id']}"
    monkeypatch.setenv("EMUCRAFT_URL", "https://emu.example.com/emucraft/")
    report = call("check_program", gcode=CUT)
    assert report["simulation"]["url"] == f"https://emu.example.com/emucraft/?sim={report['simulation']['id']}"


def test_a_remote_server_never_guesses_the_viewers_address(served):
    served()
    assert call("check_program", gcode=CUT)["simulation"]["url"] is None


def test_the_link_opens_the_check_with_its_fixtures_in_the_viewer(served):
    """The whole trip: MCP checks with an uploaded fixture, the web viewer shows it from the shared folder."""
    served()
    upload()
    report = call("check_program", gcode=CUT, name="bracket.nc", fixtures=[BLOCK], stock=[0, 0, 0, 2, 2, 1],
                  tools={"T1": {"holder_diameter": 1, "stickout": 1}})
    web = make_server("127.0.0.1", 0)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{web.server_address[1]}"
        with urllib.request.urlopen(f"{url}/api/simulations/{report['simulation']['id']}") as resp:
            sim = json.loads(resp.read())
        assert sim["name"] == "bracket.nc" and sim["text"] == CUT
        shown = sim["payload"]["report"]
        assert shown["issues"] == report["issues"] and shown["fixtures"] == report["fixtures"]
        assert sim["payload"]["fixtures"][0]["name"] == "block" and sim["payload"]["path"]["n"]
        body = json.dumps({"name": sim["name"], "text": sim["text"], "simulation": report["simulation"]["id"],
                           "settings": sim["settings"]}).encode()
        req = urllib.request.Request(f"{url}/api/analyze", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            again = json.loads(resp.read())["report"]
        assert again["issues"] == shown["issues"] and again["tools"]["1"]["holder_diameter"] == 1
        assert again["stock"]["xmax"] == 2 and again["fixtures"][0]["bounds"] == shown["fixtures"][0]["bounds"]
    finally:
        web.shutdown()
        web.server_close()


def http_client(token=None, monkeypatch=None):
    from starlette.testclient import TestClient

    if token:
        monkeypatch.setenv("EMUCRAFT_TOKEN", token)
    return TestClient(mcp_server.guarded(mcp_server.server.streamable_http_app(host="testserver")))


def test_big_models_go_up_as_raw_files(monkeypatch):
    client = http_client("secret", monkeypatch)
    assert client.put("/fixtures/big.stl", content=stl()).status_code == 401
    bearer = {"Authorization": "Bearer secret"}
    response = client.put("/fixtures/big.stl", content=stl(), headers=bearer)
    assert response.status_code == 200 and response.json()["name"] == "big.stl"
    assert client.put("/fixtures/big.stl", content=stl(), headers=bearer).status_code == 409
    assert client.put("/fixtures/big.stl?replace=1", content=stl(), headers=bearer).status_code == 200
    assert client.put("/fixtures/junk.stl", content=b"nope", headers=bearer).status_code == 400
    assert client.get("/fixtures/big.stl", headers=bearer).status_code == 405  # up only
    assert client.get("/health").status_code == 200
