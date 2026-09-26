"""The web server's JSON API and static files, over real HTTP."""

import base64
import gzip
import json
import threading
import urllib.error
import urllib.request
from array import array
from pathlib import Path

import pytest

from emucraft.model import Config
from emucraft.server import make_server

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
