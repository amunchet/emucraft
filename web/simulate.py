#!/usr/bin/env python3
"""
Simulation pipeline for the web service

G-code -> gcode_parser (XYZ points) -> C kernel `cut` (block heightmap) -> JSON result

All coordinates coming out of the parser are in thousandths of a program unit
(MULTIPLIER = 1000).  The XY plane is down-sampled to `resolution` thousandths
per grid cell so the block fits in memory; Z stays at full precision.
"""
import math
import multiprocessing
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GCODE_DIR = os.path.join(ROOT, "gcode")
KERNEL_DIR = os.getenv("EMUCRAFT_KERNEL_DIR", os.path.join(ROOT, "kernel", "src"))

for path in (GCODE_DIR, KERNEL_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

MULTIPLIER = 1000
MAX_RAPID_CUTS_REPORTED = 100
MAX_TOOLPATH_POINTS = 20_000


class SimulationError(Exception):
    """Raised for problems with the submitted program (shown to the user)"""


def _bounds(program, points):
    """
    Block bounds in thousandths.  Uses the MIN/MAX comments from the program
    header when present, otherwise falls back to the toolpath extents with the
    stock top at Z0.
    """
    xs, ys, zs = points[:, 0], points[:, 1], points[:, 2]

    if program.block_x_max > program.block_x_min and program.block_y_max > program.block_y_min:
        x = (program.block_x_min * MULTIPLIER, program.block_x_max * MULTIPLIER)
        y = (program.block_y_min * MULTIPLIER, program.block_y_max * MULTIPLIER)
        from_header = True
    else:
        x = (float(xs.min()), float(xs.max()))
        y = (float(ys.min()), float(ys.max()))
        from_header = False

    if program.block_z_max > program.block_z_min:
        z = (program.block_z_min * MULTIPLIER, program.block_z_max * MULTIPLIER)
    else:
        z = (min(float(zs.min()), -1.0), 0.0)

    return x, y, z, from_header


def _min_pool(arr, size):
    """Down-samples a 2D array to at most size x size, keeping the lowest value per cell"""
    nx, ny = arr.shape
    fx = max(1, math.ceil(nx / size))
    fy = max(1, math.ceil(ny / size))
    px = math.ceil(nx / fx) * fx - nx
    py = math.ceil(ny / fy) * fy - ny
    padded = np.pad(arr, ((0, px), (0, py)), mode="edge")
    return padded.reshape(padded.shape[0] // fx, fx, padded.shape[1] // fy, fy).min(axis=(1, 3)), fx, fy


def _toolpath(points, cutting):
    """Thins the toolpath for display, always keeping changes of move type"""
    step = max(1, len(points) // MAX_TOOLPATH_POINTS)
    keep = np.zeros(len(points), dtype=bool)
    keep[::step] = True
    keep[-1] = True
    keep[1:] |= cutting[1:] != cutting[:-1]
    keep[:-1] |= cutting[1:] != cutting[:-1]
    idx = np.nonzero(keep)[0]
    return [
        [round(float(x) / MULTIPLIER, 4), round(float(y) / MULTIPLIER, 4), round(float(z) / MULTIPLIER, 4), int(c)]
        for (x, y, z), c in zip(points[idx], cutting[idx])
    ]


def run(gcode, resolution=5, max_cells=2000, preview_size=256):
    """
    Runs the full simulation in-process.  Use `run_isolated` from the web layer.

    resolution - XY grid cell size in thousandths of a program unit
    max_cells  - upper bound on grid cells per side (resolution is coarsened to fit)
    """
    import gcode_parser
    from _emukernel import ffi, lib

    program = gcode_parser.Program()
    try:
        program.parse_line(gcode)
    except gcode_parser.NotImplementedException as e:
        raise SimulationError(f"Unsupported G-code: {e}")
    except (ValueError, IndexError) as e:
        raise SimulationError(f"Could not parse G-code: {e}")

    rows = [line.split(" ") for section in program.lines for line in section]
    if not rows:
        raise SimulationError("No motion found in program")
    data = np.array(rows, dtype=np.int64)
    points = data[:, 0:3]
    diameters = data[:, 3]
    cutting = data[:, 6]

    (x_min, x_max), (y_min, y_max), (z_min, z_max), from_header = _bounds(program, points)

    resolution = max(1, int(resolution))
    span = max(x_max - x_min, y_max - y_min)
    if span / resolution > max_cells:
        resolution = math.ceil(span / max_cells)

    nx = max(1, math.ceil((x_max - x_min) / resolution))
    ny = max(1, math.ceil((y_max - y_min) / resolution))

    # NOTE: The kernel indexes BLOCK[x * BLOCK_X + y], which is only correct for square blocks
    n = max(nx, ny)
    top = int(round(z_max - z_min))
    block = np.full(n * n, top, dtype=np.intc)
    block_ptr = ffi.cast("int *", ffi.from_buffer(block))

    gx = np.rint((points[:, 0] - x_min) / resolution).astype(np.int64)
    gy = np.rint((points[:, 1] - y_min) / resolution).astype(np.int64)
    gz = np.rint(points[:, 2] - z_min).astype(np.int64)
    gd = np.maximum(np.rint(diameters / resolution).astype(np.int64), 2)

    # Consecutive points that land on the same cell do the same cut
    moved = np.ones(len(points), dtype=bool)
    moved[1:] = (gx[1:] != gx[:-1]) | (gy[1:] != gy[:-1]) | (gz[1:] != gz[:-1]) | (gd[1:] != gd[:-1]) | (cutting[1:] != cutting[:-1])

    removed_total = 0
    rapid_cuts = []
    rapid_cut_count = 0
    for i in np.nonzero(moved)[0]:
        removed = lib.cut(block_ptr, int(gx[i]), int(gy[i]), int(gd[i]), int(gz[i]), n, n, ffi.NULL, 0)
        removed_total += removed
        if removed > 0 and not cutting[i]:
            rapid_cut_count += 1
            if len(rapid_cuts) < MAX_RAPID_CUTS_REPORTED:
                rapid_cuts.append({
                    "point": int(i),
                    "x": round(float(points[i, 0]) / MULTIPLIER, 4),
                    "y": round(float(points[i, 1]) / MULTIPLIER, 4),
                    "z": round(float(points[i, 2]) / MULTIPLIER, 4),
                })

    heights = block.reshape(n, n)[:nx, :ny]
    preview, fx, fy = _min_pool(heights, preview_size)
    cell = resolution / MULTIPLIER

    return {
        "block": {
            "x": [x_min / MULTIPLIER, x_max / MULTIPLIER],
            "y": [y_min / MULTIPLIER, y_max / MULTIPLIER],
            "z": [z_min / MULTIPLIER, z_max / MULTIPLIER],
            "from_header": from_header,
        },
        "tool": {
            "diameter": program.tool_diameter,
            "holder_diameter": program.tool_holder_diameter,
            "holder_length": program.tool_holder_length,
        },
        "grid": {"nx": nx, "ny": ny, "cell_size": cell},
        "heightmap": {
            "nx": int(preview.shape[0]),
            "ny": int(preview.shape[1]),
            "cell_x": cell * fx,
            "cell_y": cell * fy,
            # Absolute Z in program units, row-major [x][y]
            "z": np.round((preview.astype(np.float64) + z_min) / MULTIPLIER, 4).ravel().tolist(),
        },
        "toolpath": _toolpath(points, cutting),
        "stats": {
            "points": int(len(points)),
            "cuts": int(moved.sum()),
            "removed_volume": removed_total * cell * cell / MULTIPLIER,
            "rapid_cut_count": rapid_cut_count,
        },
        "rapid_cuts": rapid_cuts,
    }


def _child(conn, gcode, kwargs):
    # The kernel printf()s on every cut; keep that out of the container logs
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    try:
        conn.send(("ok", run(gcode, **kwargs)))
    except SimulationError as e:
        conn.send(("user_error", str(e)))
    except Exception as e:  # noqa: BLE001 - reported back to the parent
        conn.send(("error", f"{type(e).__name__}: {e}"))
    finally:
        conn.close()


def run_isolated(gcode, timeout=300, **kwargs):
    """
    Runs the simulation in a forked child so a kernel crash, leak or runaway job
    can't take the web worker down with it.
    """
    ctx = multiprocessing.get_context("fork")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(child, gcode, kwargs), daemon=True)
    proc.start()
    child.close()
    try:
        if not parent.poll(timeout):
            raise TimeoutError(f"Simulation exceeded {timeout}s")
        status, payload = parent.recv()
    except EOFError:
        proc.join(1)
        raise RuntimeError(f"Simulation process died (exit code {proc.exitcode})")
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join()
        parent.close()

    if status == "user_error":
        raise SimulationError(payload)
    if status == "error":
        raise RuntimeError(payload)
    return payload
