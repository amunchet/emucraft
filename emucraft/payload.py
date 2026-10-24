"""What the browser viewer needs to replay a check: the report plus the
kernel inputs (points, flags, tool slots, tool geometry) as base64 typed
arrays, so its WebAssembly copy of the kernel runs the exact same moves."""

from __future__ import annotations

import base64
import gzip
import math
import sys
from array import array
from pathlib import Path
from typing import Dict, List, Optional

from .check import CheckResult

WEB = Path(__file__).resolve().parent / "web"
DISPLAY_CELLS = 1_500_000
# Cells swept by the browser replay (moves x tool disk area): ~3 s of WebAssembly.
DISPLAY_WORK = 2.5e8
MAX_DISPLAY_SIDE = 8000  # texture size limit of most GPUs is 8192 or more

if sys.byteorder != "little":  # pragma: no cover - typed arrays are sent as-is
    raise ImportError("the viewer payload assumes a little-endian host")


def b64(arr: array) -> str:
    return base64.b64encode(arr.tobytes()).decode("ascii")


def display_grid(result: CheckResult) -> Optional[dict]:
    """Grid for the browser's own simulation: capped so it stays interactive."""
    s = result.stock
    if s is None:
        return None
    w, h = s.xmax - s.xmin, s.ymax - s.ymin
    cell = math.sqrt(w * h / DISPLAY_CELLS)
    if result.grid:
        cell = max(cell, result.grid.cell)
    radii = [t.radius for t in result.tools.values() if t.radius]
    if radii:
        cell = min(cell, max(min(radii) / 3.0, math.sqrt(w * h / (DISPLAY_CELLS * 4))))
    # big programs: every move sweeps its tool disk, so cap the total work
    p = result.program
    counts: Dict[int, int] = {}
    for slot in p.tool_slot:
        counts[slot] = counts.get(slot, 0) + 1
    work = 0.0
    for slot, count in counts.items():
        number = p.slot_tools[slot] if slot >= 0 else None
        spec = result.tools.get(number) if number is not None else None
        if spec is not None and spec.radius:
            work += count * math.pi * spec.radius ** 2
    if work > 0:
        cell = max(cell, math.sqrt(work / DISPLAY_WORK))
    # long, thin parts: every side must fit a GPU texture
    cell = max(cell, w / MAX_DISPLAY_SIDE, h / MAX_DISPLAY_SIDE)
    nx = max(1, math.ceil(w / cell - 1e-9))
    ny = max(1, math.ceil(h / cell - 1e-9))
    return {"nx": nx, "ny": ny, "x0": s.xmin, "y0": s.ymin, "cell": cell, "z_top": s.zmax, "z_bottom": s.zmin,
            "x_max": s.xmax, "y_max": s.ymax}


def kernel_tools(result: CheckResult) -> List[dict]:
    out = []
    for slot, number in enumerate(result.program.slot_tools):
        spec = result.tools[number]
        entry = {"slot": slot, "number": number, "spec": spec.to_dict(), "ok": not spec.problems()}
        if entry["ok"]:
            shape, radius, corner, slope, flute = spec.kernel_args()
            entry.update(shape=shape, radius=radius, corner=corner, slope=slope, flute=flute,
                         bodies=[[r, bottom, label] for r, bottom, label in spec.bodies()])
        out.append(entry)
    return out


def initial_display_heights(result: CheckResult, display: Optional[dict]) -> Optional[str]:
    """Starting stock of a chained check, resampled to the viewer's grid."""
    if result.initial_heights is None or display is None or result.grid is None or result.stock is None:
        return None
    from .kernel import Sim

    g, s = result.grid, result.stock
    with Sim(g.nx, g.ny, g.x0, g.y0, g.cell, s.zmax, s.zmin) as sim:
        sim.load_heights(result.initial_heights)
        heights = sim.sample(display["nx"], display["ny"], display["x0"], display["y0"], display["cell"],
                             s.xmax, s.ymax)
    # mostly flat regions: gzip shrinks this a lot (the viewer inflates it natively)
    return base64.b64encode(gzip.compress(heights.tobytes(), compresslevel=6)).decode("ascii")


def analysis_payload(result: CheckResult) -> dict:
    p = result.program
    display = display_grid(result)
    return {
        "report": result.to_dict(),
        "path": {
            "n": p.n_moves,
            "points": b64(p.points),
            "flags": b64(result.flags),
            "tools": b64(p.tool_slot),
            "feed": b64(array("f", p.feed)),
            "speed": b64(array("f", p.speed)),
            "line": b64(p.line),
        },
        "kernel_tools": kernel_tools(result),
        "display": display,
        "initial_heights_gz": initial_display_heights(result, display),
    }
