"""Moonshot's web API end to end, over real HTTP through `emucraft serve`: upload, plan, the 3D scene, the robot
program in the run's coordinates, and file downloads."""
import base64
import gzip
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial import cKDTree

from emucraft.server import make_server

SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "wave_plate.stl"


def request(url, method="GET", body=None, raw=None):
    """(status, decoded body) of one request; JSON bodies are parsed."""
    data = raw if raw is not None else None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            status, headers, out = resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as exc:
        status, headers, out = exc.code, exc.headers, exc.read()
    if headers.get("Content-Encoding") == "gzip":
        out = gzip.decompress(out)
    return status, json.loads(out) if headers.get("Content-Type", "").startswith("application/json") else out


def array(b64, dtype, width=1):
    values = np.frombuffer(base64.b64decode(b64), dtype)
    return values.reshape(-1, width) if width > 1 else values


def wait(api, run_id, count):
    """The run once its first `count` tasks have finished."""
    deadline = time.time() + 600
    while True:
        _, run = request(f"{api}/runs/{run_id}")
        tasks = run["tasks"][:count]
        if len(tasks) == count and all(t["status"] in ("done", "failed") for t in tasks):
            return run
        assert time.time() < deadline
        time.sleep(0.5)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    srv = make_server("127.0.0.1", 0, moonshot_data=tmp_path_factory.mktemp("moonshot"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture(scope="module")
def planned(site):
    """The wave plate uploaded as raw bytes, planned coarsely with tracking, and its robot program written in a
    part frame that is moved and turned, so reading the program back has to undo it."""
    api = f"{site}/api/moonshot"
    status, model = request(f"{api}/models/plate.stl", "PUT", raw=SAMPLE.read_bytes())
    assert status == 200, model
    _, queued = request(f"{api}/runs", "POST", {"model_id": model["model_id"], "spacing": 0.05})
    run = wait(api, queued["run_id"], 1)
    assert run["tasks"][0]["status"] == "done", run["tasks"][0].get("traceback")
    request(f"{api}/runs/{queued['run_id']}/program", "POST", {"frame": [1.0, 2.0, 0.5, 0.0, 0.0, 30.0]})
    run = wait(api, queued["run_id"], 2)
    assert run["tasks"][1]["status"] == "done", run["tasks"][1].get("traceback")
    return api, model, run


def test_page_is_served(site):
    status, page = request(f"{site}/moonshot/")
    assert status == 200 and b"/js/moonshot.js" in page
    status, script = request(f"{site}/js/moonshot.js")
    assert status == 200 and b"/api/moonshot" in script


def test_models_and_samples(planned):
    api, model, _ = planned
    assert model["size"] == SAMPLE.stat().st_size and model["triangles"] > 0
    _, samples = request(f"{api}/samples")
    assert "wave_plate" in samples and "pocket_block" in samples
    status, added = request(f"{api}/samples/pocket_block", "POST")
    assert status == 200 and added["filename"] == "pocket_block.stl"
    _, models = request(f"{api}/models")
    assert {model["model_id"], added["model_id"]} <= {m["model_id"] for m in models}
    assert request(f"{api}/samples/..%2Fpyproject", "POST")[0] in (400, 404)
    assert request(f"{api}/samples/nope", "POST")[0] == 400
    assert request(f"{api}/models/evil.sh", "PUT", raw=b"echo")[0] == 400
    status, refused = request(f"{api}/models/junk.stl", "PUT", raw=b"not a mesh at all")
    assert status == 400 and "does not load as a mesh" in refused["error"]


def test_scene_matches_the_results(planned):
    api, _, run = planned
    _, scene = request(f"{api}/runs/{run['run_id']}/scene")
    _, stats = request(f"{api}/runs/{run['run_id']}/stats")
    count = scene["points"]
    assert count == stats["points"]
    reach = array(scene["reach"], "u1")
    assert len(reach) == count and (reach == 0).sum() == stats["reachable"]
    for key in ("point", "normal", "axis", "center"):
        assert array(scene[key], "<f4", 3).shape == (count, 3), key
    faces = array(scene["faces"], "<u4", 3)
    assert faces.max() < len(array(scene["vertices"], "<f4", 3))
    assert len(array(scene["holder"], "<f4", 3)) % 3 == 0 and len(array(scene["holder"], "<f4", 3)) > 0
    point, normal, center = (array(scene[k], "<f4", 3)[reach == 0] for k in ("point", "normal", "center"))
    axis, track = array(scene["axis"], "<f4", 3)[reach == 0], array(scene["track"], "<f4")[reach == 0]
    # The drum's center sits a radius off each point, shifted back along its axis by the tracking offset.
    expected = point + scene["params"]["radius"] * normal - track[:, None] * axis
    assert np.abs(center - expected).max() < 1e-4
    assert scene["band"] > 0 and scene["passes"] > 1


def test_program_contacts_land_on_the_part(planned):
    api, _, run = planned
    _, scene = request(f"{api}/runs/{run['run_id']}/scene")
    status, program = request(f"{api}/runs/{run['run_id']}/program")
    assert status == 200 and program["stats"]["passes"] > 1
    contact = array(program["contact"], "<f4", 3)
    on = np.isfinite(contact[:, 0])
    assert len(contact) == program["count"] and on.sum() > 10
    gap, _ = cKDTree(array(scene["point"], "<f4", 3)).query(contact[on])
    assert gap.max() < 2 * scene["params"]["spacing"]  # the part frame's shift and turn are undone
    kinds = [program["kinds"][k] for k in array(program["kind"], "u1")]
    assert "sand" in kinds and kinds[0] == "approach"


def test_files_download_and_stay_inside_the_run(planned):
    api, _, run = planned
    site = api[:-len("/api/moonshot")]
    _, files = request(f"{api}/runs/{run['run_id']}/files")
    paths = {f["path"] for f in files}
    assert {"summary.json", "orientations.csv", "fanuc/sand.ls"} <= paths
    url = next(f["url"] for f in files if f["path"] == "fanuc/sand.ls")
    status, body = request(site + url)
    assert status == 200 and body.startswith(b"/PROG")
    assert request(f"{api}/files/{run['run_id']}/..%2F..%2Fmodels")[0] == 400
    assert request(f"{api}/runs/../models")[0] in (400, 404)


def test_bad_requests_are_refused(planned):
    api, model, run = planned
    status, bad = request(f"{api}/runs", "POST", {"model_id": model["model_id"], "holder": "clamp"})
    assert status == 400 and "holder" in bad["error"]
    assert request(f"{api}/runs", "POST", {"model_id": model["model_id"], "colour": 1})[0] == 400
    assert request(f"{api}/runs/{run['run_id']}/program", "POST", {"setup": "drum", "rtcp": True})[0] == 400
    assert request(f"{api}/runs/0123456789ab")[0] == 400
