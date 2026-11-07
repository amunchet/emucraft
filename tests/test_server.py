"""The web server's JSON API and static files, over real HTTP."""

import base64
import gzip
import http.client
import json
import os
import struct
import threading
import urllib.error
import urllib.request
from array import array
from pathlib import Path

import pytest

from emucraft.check import check_text
from emucraft.config import config_from_dict
from emucraft.fixture import _box, fixture_specs, list_models, list_setups
from emucraft.model import Config
from emucraft.payload import KEEP_SIMULATIONS, save_simulation
from emucraft.server import apply_settings, make_server

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture(scope="module")
def server():
    srv = make_server("127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return resp.status, resp.headers, body


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def decode(b64, code):
    out = array(code)
    out.frombytes(base64.b64decode(b64))
    return out


def test_static_files(server):
    status, headers, body = get(server + "/")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    assert b'type="importmap"' in body
    status, headers, body = get(server + "/wasm/emucraft.wasm")
    assert headers["Content-Type"] == "application/wasm" and body[:4] == b"\0asm"
    status, headers, _ = get(server + "/js/main.js")
    assert headers["Content-Type"].startswith("text/javascript")
    status, headers, body = get(server + "/vendor/three/three.module.min.js", {"Accept-Encoding": "gzip"})
    assert headers["Content-Encoding"] == "gzip" and len(body) > 500_000


@pytest.mark.parametrize("path", ["/../setup.py", "/js/../../server.py", "/nope.js", "/api/nope", "/api/programs/../setup.py"])
def test_not_found_and_no_traversal(server, path):
    with pytest.raises(urllib.error.HTTPError) as err:
        get(server + path)
    assert err.value.code == 404


def test_info_and_programs(server):
    _, _, body = get(server + "/api/info")
    info = json.loads(body)
    assert "titanium" in info["materials"] and "corner_factor" in info["optimize_defaults"]
    _, _, body = get(server + "/api/programs")
    names = {p["name"] for p in json.loads(body)}
    assert {"crash_demo.nc", "makino_roughing.nc", "pocket_corners.nc"} <= names
    _, headers, body = get(server + "/api/programs/crash_demo.nc")
    assert body.startswith(b"%") and headers["Content-Type"].startswith("text/plain")


def test_analyze_payload(server):
    text = (EXAMPLES / "crash_demo.nc").read_text()
    status, data = post(server + "/api/analyze", {"name": "crash_demo.nc", "text": text, "sample": True})
    assert status == 200
    report = data["report"]
    assert report["status"] == "fail"
    assert [i["kind"] for i in report["issues"]] == ["rapid-cut", "spindle-off-cut", "holder", "shank", "table"]
    path = data["path"]
    n = path["n"]
    assert len(decode(path["points"], "d")) == 3 * (n + 1)
    for key, code in (("flags", "i"), ("tools", "i"), ("line", "i"), ("feed", "f"), ("speed", "f")):
        assert len(decode(path[key], code)) == n
    tools = {t["number"]: t for t in data["kernel_tools"]}
    assert tools[2]["shape"] == 1 and tools[2]["bodies"][0][2] == "holder"
    g = data["display"]
    assert g["nx"] * g["ny"] <= 1_600_000 and g["z_top"] == 0.0


def test_analyze_settings_override_the_program(server):
    text = (EXAMPLES / "crash_demo.nc").read_text()
    settings = {"tools": {"2": {"stickout": 1.2, "flute_length": 1.0}}, "table_z": -1.5}
    status, data = post(server + "/api/analyze", {"name": "crash_demo.nc", "text": text, "settings": settings})
    assert status == 200
    kinds = [i["kind"] for i in data["report"]["issues"]]
    assert "holder" not in kinds and "shank" not in kinds and "table" not in kinds
    assert data["report"]["tools"]["2"]["source"]["stickout"] == "override"


def test_analyze_workholding_settings(server):
    """The viewer's workholding form: a vise set up from settings, in program units."""
    _, _, body = get(server + "/api/info")
    assert "steel" in json.loads(body)["workholding_materials"]
    text = (EXAMPLES / "pocket_corners.nc").read_text()
    hold = {"fixture": "vise", "material": "titanium", "grip": 0.5, "force_per_torque": 100, "torque": 1}
    status, data = post(server + "/api/analyze", {"name": "pocket_corners.nc", "text": text,
                                                  "settings": {"workholding": hold, "tools": {"5": {"flutes": 4}}}})
    assert status == 200
    report = data["report"]
    w = report["workholding"]
    assert w["torque"] == 1 and w["force"] == 100 and w["material"] == "titanium"
    assert report["status"] == "fail" and "workholding" in [i["kind"] for i in report["issues"]]
    assert w["margin"] == pytest.approx(w["safety_factor"] / w["required_torque"])


def test_analyze_errors(server):
    status, data = post(server + "/api/analyze", {"name": "x.nc"})
    assert status == 400 and "no program text" in data["error"]
    status, data = post(server + "/api/analyze", {"name": "x.nc", "text": "G0 X1", "settings": {"stock": [0, 0, 0, 1, 1]}})
    assert status == 400
    status, data = post(server + "/api/analyze", {"name": "x.nc", "text": "G0 X1", "settings": {"table_z": "low"}})
    assert status == 400 and "table_z" in data["error"]


def test_optimize_endpoint(server):
    text = (EXAMPLES / "pocket_corners.nc").read_text()
    status, data = post(server + "/api/optimize", {"name": "pocket_corners.nc", "text": text,
                                                  "options": {"material": "titanium"}})
    assert status == 200 and data["verified"]
    assert data["name"] == "pocket_corners.opt.nc"
    assert data["stats"]["corners"] > 0 and data["text"] != text
    feeds = decode(data["move_feed"], "f")
    cutting = [f for f in feeds if f > 0]  # rapids carry 0
    assert cutting and min(cutting) < 45 and max(cutting) <= 45
    status, data = post(server + "/api/optimize", {"name": "p.nc", "text": text, "options": {"material": "unobtainium"}})
    assert status == 400 and "unknown material" in data["error"]


def test_server_config_is_used(tmp_path):
    cfg = Config()
    cfg.check.link_feed = 30.0
    srv = make_server("127.0.0.1", 0, programs=tmp_path, config=cfg)
    (tmp_path / "part.nc").write_text((EXAMPLES / "crash_demo.nc").read_text())
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        _, _, body = get(base + "/api/programs")
        sources = {p["name"]: p["source"] for p in json.loads(body)}
        assert sources["part.nc"] == "programs"
        status, data = post(base + "/api/analyze", {"name": "part.nc", "sample": True})
        assert status == 200
        # the contour at F40 is now a "link" that must not cut
        assert "link-cut" in [i["kind"] for i in data["report"]["issues"]]
    finally:
        srv.shutdown()
        srv.server_close()


def test_display_grid_fits_gpu_textures_for_long_parts():
    from emucraft.check import check_text
    from emucraft.model import StockSpec
    from emucraft.payload import display_grid

    cfg = Config()
    cfg.stock = StockSpec(0, 0, -1, 200, 1, 0)
    result = check_text("T1 M6\nS1000 M3\nG0 X1 Y0.5 Z1\nG1 Z-0.1 F10\nX199", "long.nc", cfg,
                        overrides={1: {"diameter": 0.25}})
    g = display_grid(result)
    assert g["nx"] <= 8192 and g["ny"] <= 8192
    assert g["x_max"] == 200 and g["y_max"] == 1


def test_open_optimized_copy_uses_the_originals_config(server):
    """An optimized example re-checked in the viewer keeps the example's emucraft.toml."""
    text = (EXAMPLES / "makino_roughing.nc").read_text()
    status, alone = post(server + "/api/analyze", {"name": "makino_roughing.opt.nc", "text": text})
    status2, kept = post(server + "/api/analyze", {"name": "makino_roughing.opt.nc", "text": text,
                                                   "configOf": "makino_roughing.nc"})
    assert status == status2 == 200
    assert alone["report"]["tools"]["20"]["holder_diameter"] == 10.0  # the post's placeholder
    assert kept["report"]["tools"]["20"]["holder_diameter"] == 1.3  # examples/emucraft.toml
    assert kept["report"]["stats"]["link_feed"] == 700


def test_viewer_workholding_off_overrides_the_config_file():
    config = config_from_dict({"workholding": {"material": "steel", "fixture": "vise", "grip": 0.5, "force": 3000}})
    off, kwargs = apply_settings(config, {"workholding": {"fixture": "off"}})
    assert not off.workholding and "workholding" not in kwargs
    assert config.workholding  # the loaded config itself stays


def test_viewer_turns_the_broken_tool_check_off():
    assert apply_settings(Config(), {"broken_tool": False})[0].check.broken_tool is False
    assert apply_settings(Config(), {})[0].check.broken_tool is True


def request(method, url, data=b"", content_type="application/octet-stream"):
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def stl(lo=(0, 0, 0), hi=(1, 1, 1)):
    tris = _box(lo, hi)
    out = b" " * 80 + struct.pack("<I", len(tris) // 9)
    for i in range(0, len(tris), 9):
        out += b"\0" * 12 + struct.pack("<9f", *tris[i:i + 9]) + b"\0\0"
    return out


CUT = """G20
(MIN X: 0)
(MAX X: 2)
(MIN Y: 0)
(MAX Y: 2)
(MIN Z: 0)
(MAX Z: 1)
T1 M6
(DIAMETER: 0.25)
M3 S1000
G0 X1 Y1 Z2
G1 Z0.5 F10
G0 Z2
M30
"""


def test_upload_list_and_delete_a_fixture_model(server):
    status, model = request("PUT", server + "/api/fixtures/My%20Vise.STL", stl(hi=(4, 2, 1)))
    assert status == 200 and model["name"] == "my-vise.stl" and model["bounds"] == [[0, 0, 0], [4, 2, 1]]
    assert [m["name"] for m in json.loads(get(server + "/api/fixtures")[2])["models"]] == ["my-vise.stl"]
    # a second upload under the name changes setups that use it, so it has to ask
    assert request("PUT", server + "/api/fixtures/my-vise.stl", stl())[0] == 409
    assert request("PUT", server + "/api/fixtures/my-vise.stl?replace=1", stl())[1]["bounds"][1] == [1, 1, 1]
    assert request("PUT", server + "/api/fixtures/junk.stl", b"not a mesh")[0] == 400
    assert request("DELETE", server + "/api/fixtures/my-vise.stl", None)[0] == 200
    assert request("DELETE", server + "/api/fixtures/my-vise.stl", None)[0] == 404
    assert list_models() == []


@pytest.mark.parametrize("path, status", [("/api/fixtures/junk.stl", 400), ("/api/nope/x", 404)])
def test_a_refused_upload_leaves_the_connection_usable(server, path, status):
    """Its body must not be read as the next request on a keep-alive connection."""
    conn = http.client.HTTPConnection(server.split("//")[1], timeout=30)
    conn.request("PUT", path, b"x" * 5000)
    response = conn.getresponse()
    assert response.read() and response.status == status
    conn.request("GET", "/api/fixtures")
    response = conn.getresponse()
    assert response.read() and response.status == 200
    conn.close()


def test_analyze_with_fixtures_placed_in_the_setup_tab(server):
    request("PUT", server + "/api/fixtures/block.stl", stl(hi=(1, 1, 1)))
    placed = {"name": "block", "model": "block.stl", "units": "inch", "position_units": "inch", "at": [1, 1, 0.5]}
    status, data = post(server + "/api/analyze", {"name": "p.nc", "text": CUT, "settings": {"fixtures": [placed]}})
    assert status == 200 and [i["kind"] for i in data["report"]["issues"]] == ["fixture"]
    (fx,) = data["report"]["fixtures"]
    assert fx["spec"]["model"] == "block.stl" and fx["spec"]["at"] == [1, 1, 0.5]
    assert data["report"]["fixture_source"] == "this check"
    moved = {**fx["spec"], "at": [8, 8, 0.5]}
    status, data = post(server + "/api/analyze", {"name": "p.nc", "text": CUT, "settings": {"fixtures": [moved]}})
    assert status == 200 and data["report"]["issues"] == []


def test_an_emptied_fixture_list_overrides_the_config(server):
    text = (EXAMPLES / "vise" / "vise_demo.nc").read_text()
    source = {"name": "vise/vise_demo.nc", "text": text, "sample": True}
    assert post(server + "/api/analyze", source)[1]["report"]["fixture_source"] == "config"
    _, data = post(server + "/api/analyze", {**source, "settings": {"fixtures": []}})
    assert data["report"]["fixtures"] == [] and data["report"]["fixture_source"] is None


def test_a_config_fixture_can_be_moved_from_the_setup_tab(server):
    """The form sends the example's model back by its path, which is the config's own file."""
    text = (EXAMPLES / "vise" / "vise_demo.nc").read_text()
    source = {"name": "vise/vise_demo.nc", "text": text, "sample": True}
    _, data = post(server + "/api/analyze", source)
    spec = data["report"]["fixtures"][0]["spec"]
    status, moved = post(server + "/api/analyze", {**source, "settings": {"fixtures": [{**spec, "at": [100, 100, -50]}]}})
    assert status == 200 and not [i for i in moved["report"]["issues"] if i["kind"] == "fixture"]


@pytest.mark.parametrize("model", ["/etc/hostname", "../" * 12 + "etc/hostname"])
def test_browsers_cannot_point_a_fixture_at_any_file(server, model):
    settings = {"fixtures": [{"model": model}]}
    status, data = post(server + "/api/analyze", {"name": "p.nc", "text": CUT, "settings": settings})
    assert status == 400 and "not in the library" in data["error"]
    assert request("PUT", server + "/api/setups/x", json.dumps(settings).encode(), "application/json")[0] == 400
    assert list_setups() == []


def test_saved_setup_comes_back_for_its_program(server):
    fixtures = [{"name": "clamp", "min": [0, 0, 0], "max": [1, 1, 1], "units": "inch", "position_units": "inch", "at": [1, 1, 0.5]}]
    status, saved = request("PUT", server + "/api/setups/My%20Part", json.dumps({"fixtures": fixtures}).encode(), "application/json")
    assert status == 200 and saved["name"] == "my-part"
    assert [s["name"] for s in json.loads(get(server + "/api/fixtures")[2])["setups"]] == ["my-part"]
    _, data = post(server + "/api/analyze", {"name": "my-part.nc", "text": CUT})
    assert data["report"]["fixture_source"] == "setup my-part (named after the program)"
    assert [i["kind"] for i in data["report"]["issues"]] == ["fixture"]
    assert request("DELETE", server + "/api/setups/my-part", None)[0] == 200
    assert post(server + "/api/analyze", {"name": "my-part.nc", "text": CUT})[1]["report"]["fixtures"] == []
    assert request("DELETE", server + "/api/setups/my-part", None)[0] == 404


def test_a_saved_simulation_opens_and_rechecks_with_its_own_config(server):
    cfg = config_from_dict({"units": "inch", "tools": {"1": {"holder_diameter": 1, "stickout": 0.3}}})
    plate = [{"name": "plate", "min": [0, 0, 0], "max": [4, 4, 0.25], "units": "inch", "position_units": "inch", "at": [1, 1, -0.2]}]
    specs = fixture_specs(plate)
    ident = save_simulation(check_text(CUT, "part.nc", cfg, fixtures=specs), {"fixtures": plate})
    _, _, body = get(f"{server}/api/simulations/{ident}")
    sim = json.loads(body)
    assert sim["name"] == "part.nc" and sim["text"] == CUT and sim["settings"]["fixtures"] == plate
    report = sim["payload"]["report"]
    assert [f["name"] for f in report["fixtures"]] == ["plate"] and sim["payload"]["fixtures"][0]["triangles_gz"]
    assert "holder" in [i["kind"] for i in report["issues"]]  # needs the saved config's holder
    # the viewer's Re-check runs on the saved config, which this server has never seen
    source = {"name": sim["name"], "text": sim["text"], "simulation": ident}
    status, again = post(server + "/api/analyze", {**source, "settings": sim["settings"]})
    assert status == 200 and [i["kind"] for i in again["report"]["issues"]] == [i["kind"] for i in report["issues"]]
    assert post(server + "/api/analyze", {"name": "part.nc", "text": CUT})[1]["report"]["issues"] == []


@pytest.mark.parametrize("ident", ["0" * 32, "nope", "..%2F..%2Fsetup.py", "%2Fetc%2Fhostname"])
def test_unknown_simulations_are_not_found(server, ident):
    with pytest.raises(urllib.error.HTTPError) as err:
        get(f"{server}/api/simulations/{ident}")
    assert err.value.code == 404 and "gone" in json.loads(err.value.read())["error"]
    status, data = post(server + "/api/analyze", {"name": "p.nc", "text": CUT, "simulation": ident})
    assert status == 404


def test_old_simulations_go_once_the_folder_is_full(data_dir, monkeypatch):
    result = check_text(CUT, "part.nc")
    first = save_simulation(result)
    file = data_dir / "simulations" / f"{first}.json.gz"
    size = file.stat().st_size
    monkeypatch.setattr("emucraft.payload.KEEP_SIMULATIONS", int(size * 2.5))
    os.utime(file, (1000, 1000))
    second = save_simulation(result)
    os.utime(data_dir / "simulations" / f"{second}.json.gz", (2000, 2000))
    third = save_simulation(result)
    left = {f.name.split(".")[0] for f in (data_dir / "simulations").iterdir()}
    assert left == {second, third}
    assert KEEP_SIMULATIONS > 1 << 30
