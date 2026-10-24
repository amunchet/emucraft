"""The browser's WebAssembly kernel must match the native kernel bit for bit.

Both are built from kernel/src/emucraft.c; this runs the committed
emucraft.wasm through the viewer's own kernel.js wrapper in Node and compares
the resulting height field and move marks with the native library.
"""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from emucraft.check import check_file, trim_to_stock
from emucraft.kernel import EV_BODY, Sim
from emucraft.model import StockSpec
from emucraft.payload import analysis_payload

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests" / "js" / "wasm_run.mjs"
EXAMPLES = ROOT / "examples"
KINDS = {1: "rapid-cut", 2: "spindle-off-cut", 3: "link-cut", 4: "shank", EV_BODY: "holder"}

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")


def native_digests(payload, threads=1):
    g = payload["display"]
    result_path = payload["_program_arrays"]
    sim = Sim(g["nx"], g["ny"], g["x0"], g["y0"], g["cell"], g["z_top"], g["z_bottom"],
              payload["report"]["stats"]["tolerance"])
    trim_to_stock(sim, StockSpec(g["x0"], g["y0"], g["z_bottom"], g["x_max"], g["y_max"], g["z_top"]))
    for t in payload["kernel_tools"]:
        if t["ok"]:
            sim.set_tool(t["slot"], t["shape"], t["radius"], t["corner"], t["slope"], t["flute"],
                         [(b[0], b[1]) for b in t["bodies"]])
    points, flags, tools = result_path
    run = sim.run(points, flags, tools, threads=threads)
    heights = hashlib.sha256(sim.heights().tobytes()).hexdigest()
    marks = hashlib.sha256(sim.marks().tobytes()).hexdigest()
    sim.close()
    return heights, marks, sorted((KINDS[e.kind], e.move) for e in run.events)


def wasm_digests(payload, tmp_path, chunk=0):
    data = {k: v for k, v in payload.items() if not k.startswith("_")}
    path = tmp_path / "payload.json"
    path.write_text(json.dumps(data))
    out = subprocess.run(["node", str(SCRIPT), str(path), str(chunk)], capture_output=True, text=True,
                         timeout=300)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.parametrize("name", ["crash_demo.nc", "pocket_corners.nc", "makino_roughing.nc"])
def test_wasm_matches_native(name, tmp_path):
    result = check_file(EXAMPLES / name)
    payload = analysis_payload(result)
    payload["_program_arrays"] = (result.program.points, result.flags, result.program.tool_slot)
    heights, marks, events = native_digests(payload)
    wasm = wasm_digests(payload, tmp_path)
    assert wasm["heights"] == heights
    assert wasm["marks"] == marks
    assert sorted(tuple(e) for e in wasm["events"]) == events
    if name == "crash_demo.nc":
        assert {kind for kind, _ in events} >= {"rapid-cut", "spindle-off-cut", "shank", "holder"}


def test_chunked_runs_and_threads_do_not_change_the_result(tmp_path):
    """The viewer simulates in time-budgeted chunks; the CLI in thread bands."""
    result = check_file(EXAMPLES / "makino_roughing.nc")
    payload = analysis_payload(result)
    payload["_program_arrays"] = (result.program.points, result.flags, result.program.tool_slot)
    heights, marks, _ = native_digests(payload, threads=4)
    chunked = wasm_digests(payload, tmp_path, chunk=37)
    assert chunked["heights"] == heights
    assert chunked["marks"] == marks


@pytest.mark.parametrize("name", ["crash_demo.nc", "pocket_corners.nc"])
def test_viewer_playback_matches_a_full_run(name, tmp_path):
    """The viewer animates with partial moves and replays after scrubbing back."""
    result = check_file(EXAMPLES / name)
    path = tmp_path / "payload.json"
    path.write_text(json.dumps(analysis_payload(result)))
    out = subprocess.run(["node", str(ROOT / "tests" / "js" / "player_partial.mjs"), str(path)],
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["move"] == data["n"]
    assert data["differing"] == 0 and data["worst"] == 0
    assert data["times_monotonic"]
