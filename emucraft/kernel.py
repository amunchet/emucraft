"""ctypes binding for the C kernel (kernel/src/emucraft.c).

The shared library is looked up in this order:

1. ``$EMUCRAFT_KERNEL`` (explicit path),
2. a library built by ``pip install`` next to this file,
3. ``kernel/build/libemucraft.*`` in a source checkout (``make -C kernel``),
4. a private build cache, compiled on first use with ``$CC`` / ``cc``.
"""

from __future__ import annotations

import ctypes
import hashlib
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from array import array
from concurrent.futures import ThreadPoolExecutor
from ctypes import POINTER, c_double, c_float, c_int32, c_uint32, c_void_p
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

EV_RAPID_CUT = 1
EV_SPINDLE_OFF_CUT = 2
EV_NOCUT_CUT = 3
EV_FLUTE = 4
EV_BODY = 5

_PKG = Path(__file__).resolve().parent
_SOURCES = [_PKG / "_kernel" / "emucraft.c", _PKG.parent / "kernel" / "src" / "emucraft.c"]
_CFLAGS = ["-O3", "-std=c99", "-fno-math-errno", "-ffp-contract=off", "-fPIC"]


class KernelError(RuntimeError):
    pass


class _Event(ctypes.Structure):
    _fields_ = [
        ("kind", c_int32),
        ("move", c_int32),
        ("body", c_int32),
        ("cells", c_int32),
        ("x", c_double),
        ("y", c_double),
        ("z", c_double),
        ("depth", c_double),
        ("volume", c_double),
    ]


def _lib_filename() -> str:
    if sys.platform == "darwin":
        return "libemucraft.dylib"
    if sys.platform == "win32":
        return "emucraft.dll"
    return "libemucraft.so"


def _source() -> Optional[Path]:
    for src in _SOURCES:
        if src.is_file():
            return src
    return None


def build_library(dest: Path, source: Optional[Path] = None) -> Path:
    """Compile the kernel into ``dest`` with the system C compiler."""
    source = source or _source()
    if source is None:
        raise KernelError("kernel source emucraft.c not found")
    cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        raise KernelError("no C compiler found (set CC, or run `make -C kernel`)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shared = ["-dynamiclib"] if sys.platform == "darwin" else ["-shared"]
    fd, tmp = tempfile.mkstemp(suffix=dest.suffix, dir=str(dest.parent))
    os.close(fd)
    cmd = [cc, *_CFLAGS, *shared, "-o", tmp, str(source), "-lm"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        os.unlink(tmp)
        raise KernelError(f"compiling the kernel failed:\n{' '.join(cmd)}\n{proc.stderr}")
    os.replace(tmp, dest)
    return dest


def _cache_dir() -> Path:
    base = os.environ.get("EMUCRAFT_CACHE") or os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "emucraft"


def find_library() -> Path:
    env = os.environ.get("EMUCRAFT_KERNEL")
    if env:
        return Path(env)
    for pattern in ("_emucraft_kernel*.so", "_emucraft_kernel*.pyd", "_emucraft_kernel*.dylib"):
        for hit in sorted(_PKG.glob(pattern)):
            return hit
    source = _source()
    local = _PKG.parent / "kernel" / "build" / _lib_filename()
    if local.is_file() and (source is None or local.stat().st_mtime >= source.stat().st_mtime):
        return local
    if source is None:
        raise KernelError("no kernel library and no kernel source to build it from")
    digest = hashlib.sha256(source.read_bytes() + " ".join(_CFLAGS).encode()).hexdigest()[:16]
    cached = _cache_dir() / digest / _lib_filename()
    if not cached.is_file():
        build_library(cached, source)
    return cached


_lib = None
_lib_lock = threading.Lock()


def _declare(lib) -> None:
    sim, ctx = c_void_p, c_void_p
    sigs = {
        "ec_version": (c_int32, []),
        "ec_tile_size": (c_int32, []),
        "ec_event_size": (c_int32, []),
        "ec_sim_new": (sim, [c_int32, c_int32, c_double, c_double, c_double, c_double, c_double]),
        "ec_sim_free": (None, [sim]),
        "ec_sim_reset": (None, [sim]),
        "ec_sim_heights": (POINTER(c_float), [sim]),
        "ec_sim_marks": (POINTER(c_uint32), [sim]),
        "ec_sim_invalidate": (None, [sim]),
        "ec_sim_set_tolerance": (None, [sim, c_double]),
        "ec_sim_set_tool": (c_int32, [sim, c_int32, c_int32, c_double, c_double, c_double, c_double]),
        "ec_sim_set_body": (c_int32, [sim, c_int32, c_int32, c_double, c_double]),
        "ec_sim_clear_tool": (None, [sim, c_int32]),
        "ec_sim_volume": (c_double, [sim]),
        "ec_sim_sample": (None, [sim, c_int32, c_int32, c_double, c_double, c_double, c_double, c_double,
                                 POINTER(c_float)]),
        "ec_sim_min_height": (c_double, [sim]),
        "ec_ctx_new": (ctx, [c_int32]),
        "ec_ctx_free": (None, [ctx]),
        "ec_ctx_reset": (None, [ctx]),
        "ec_ctx_set_move_outputs": (None, [ctx, POINTER(c_double), POINTER(c_float), c_int32]),
        "ec_ctx_event_count": (c_int32, [ctx]),
        "ec_ctx_events_dropped": (c_int32, [ctx]),
        "ec_ctx_events": (POINTER(_Event), [ctx]),
        "ec_ctx_volume": (c_double, [ctx]),
        "ec_ctx_cells_cut": (c_double, [ctx]),
        "ec_ctx_dirty": (None, [ctx, POINTER(c_int32)]),
        "ec_sim_run": (c_int32, [sim, ctx, POINTER(c_double), POINTER(c_int32), POINTER(c_int32),
                                 c_int32, c_int32, c_int32, c_int32]),
        "ec_simplify": (c_int32, [POINTER(c_double), POINTER(c_int32), POINTER(c_int32), c_int32,
                                 c_double, c_int32, POINTER(c_double), POINTER(c_int32),
                                 POINTER(c_int32), POINTER(c_int32)]),
        "ec_merge_move_outputs": (None, [POINTER(c_double), POINTER(c_float), POINTER(c_double),
                                          POINTER(c_float), c_int32, c_int32]),
        "ec_sim_segment": (c_int32, [sim, ctx, c_double, c_double, c_double, c_double, c_double,
                                     c_double, c_int32, c_int32, c_int32, c_int32, c_int32]),
    }
    for name, (res, args) in sigs.items():
        fn = getattr(lib, name)
        fn.restype = res
        fn.argtypes = args


def load():
    """Load (building if needed) the kernel library."""
    global _lib
    with _lib_lock:
        if _lib is None:
            path = find_library()
            lib = ctypes.CDLL(str(path))
            _declare(lib)
            if lib.ec_version() != 1:
                raise KernelError(f"{path}: unexpected kernel version {lib.ec_version()}")
            if lib.ec_event_size() != ctypes.sizeof(_Event):
                raise KernelError(f"{path}: event layout mismatch")
            _lib = lib
        return _lib


@dataclass
class Event:
    kind: int
    move: int
    body: int
    cells: int
    x: float
    y: float
    z: float
    depth: float
    volume: float
    last: int = -1  # last move the event may belong to (runs of simplified moves)


@dataclass
class RunResult:
    events: List[Event] = field(default_factory=list)
    volume: float = 0.0
    cells: float = 0.0
    dropped: int = 0
    move_volume: Optional[array] = None  # array('d') per move
    move_depth: Optional[array] = None  # array('f') per move
    dirty: Tuple[int, int, int, int] = (0, 0, 0, 0)


def simplify(points: array, flags: array, tools: array, eps: float,
             max_run: int = 64) -> Tuple[array, array, array, array]:
    """Merge nearly collinear runs of moves (see ec_simplify).

    Returns (points, flags, tools, first) where first[k] is the first
    original move of merged move k.
    """
    lib = load()
    n = len(flags)
    out_p = array("d", bytes(8 * 3 * (n + 1)))
    out_f = array("i", bytes(4 * n))
    out_t = array("i", bytes(4 * n))
    out_first = array("i", bytes(4 * n))
    if n == 0:
        return array("d", points), out_f, out_t, out_first
    k = lib.ec_simplify(_buf(points, c_double), _buf(flags, c_int32), _buf(tools, c_int32), n,
                        float(eps), int(max_run), _buf(out_p, c_double), _buf(out_f, c_int32),
                        _buf(out_t, c_int32), _buf(out_first, c_int32))
    del out_p[3 * (k + 1):]
    del out_f[k:]
    del out_t[k:]
    del out_first[k:]
    return out_p, out_f, out_t, out_first


def _buf(arr: array, ctype):
    return (ctype * len(arr)).from_buffer(arr) if len(arr) else None


class Sim:
    """A block of stock on a regular grid, plus tool slots."""

    def __init__(self, nx: int, ny: int, x0: float, y0: float, cell: float, z_top: float,
                 z_bottom: float, tolerance: Optional[float] = None) -> None:
        self.lib = load()
        self.nx, self.ny = int(nx), int(ny)
        self.x0, self.y0, self.cell = float(x0), float(y0), float(cell)
        self.z_top, self.z_bottom = float(z_top), float(z_bottom)
        self._p = self.lib.ec_sim_new(self.nx, self.ny, self.x0, self.y0, self.cell, self.z_top,
                                      self.z_bottom)
        if not self._p:
            raise KernelError(f"cannot allocate a {self.nx} x {self.ny} stock grid")
        if tolerance is not None:
            self.lib.ec_sim_set_tolerance(self._p, float(tolerance))
        self.tile = self.lib.ec_tile_size()

    def close(self) -> None:
        if getattr(self, "_p", None):
            self.lib.ec_sim_free(self._p)
            self._p = None

    def __del__(self) -> None:  # pragma: no cover - depends on GC timing
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self) -> "Sim":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------- state

    def heights(self):
        """Live float32 view of the height field (row-major, iy * nx + ix)."""
        ptr = self.lib.ec_sim_heights(self._p)
        return memoryview((c_float * (self.nx * self.ny)).from_address(ctypes.addressof(ptr.contents))).cast("B").cast("f")

    def marks(self):
        ptr = self.lib.ec_sim_marks(self._p)
        return memoryview((c_uint32 * (self.nx * self.ny)).from_address(ctypes.addressof(ptr.contents))).cast("B").cast("I")

    def reset(self) -> None:
        self.lib.ec_sim_reset(self._p)

    def invalidate(self) -> None:
        self.lib.ec_sim_invalidate(self._p)

    def volume(self) -> float:
        return self.lib.ec_sim_volume(self._p)

    def set_tolerance(self, tolerance: float) -> None:
        self.lib.ec_sim_set_tolerance(self._p, float(tolerance))

    def load_heights(self, heights) -> None:
        """Replace the height field (e.g. with stock saved from another run)."""
        view = self.heights()
        if len(heights) != len(view):
            raise ValueError("height field size does not match the grid")
        view[:] = memoryview(array("f", heights)).cast("B").cast("f")
        self.invalidate()

    def sample(self, nx: int, ny: int, x0: float, y0: float, cell: float,
               x_max: float = math.inf, y_max: float = math.inf) -> array:
        """Heights at the cell centers of another grid (nearest cell), never
        reading cells whose centers lie past (x_max, y_max)."""
        out = array("f", bytes(4 * nx * ny))
        self.lib.ec_sim_sample(self._p, nx, ny, x0, y0, cell, float(x_max), float(y_max), _buf(out, c_float))
        return out

    def min_height(self) -> float:
        return self.lib.ec_sim_min_height(self._p)

    def set_tool(self, slot: int, shape: int, radius: float, corner: float = 0.0,
                 slope: float = 0.0, flute: float = 0.0,
                 bodies: Sequence[Tuple[float, float]] = ()) -> None:
        if not self.lib.ec_sim_set_tool(self._p, slot, shape, radius, corner, slope, flute):
            raise KernelError(f"invalid tool for slot {slot}")
        for j in range(4):
            if j < len(bodies):
                r, bottom = bodies[j]
                self.lib.ec_sim_set_body(self._p, slot, j, float(r), float(bottom))
            else:
                self.lib.ec_sim_set_body(self._p, slot, j, 0.0, 0.0)

    def clear_tool(self, slot: int) -> None:
        self.lib.ec_sim_clear_tool(self._p, slot)

    # ---------------------------------------------------------------- runs

    def bands(self, count: int) -> List[Tuple[int, int]]:
        """Split the rows into at most ``count`` tile-aligned bands."""
        count = max(1, min(count, math.ceil(self.ny / self.tile)))
        rows = math.ceil(math.ceil(self.ny / count) / self.tile) * self.tile
        return [(r, min(self.ny, r + rows)) for r in range(0, self.ny, rows)]

    def run(self, points: array, flags: array, tools: array, begin: int = 0,
            end: Optional[int] = None, threads: int = 1, move_outputs: bool = False,
            events_capacity: int = 50_000) -> RunResult:
        """Simulate moves [begin, end) of a path (see ec_sim_run)."""
        n = len(flags)
        end = n if end is None else end
        if len(points) != 3 * (n + 1) or len(tools) != n:
            raise ValueError("points must hold 3 * (n + 1) values and tools n values")
        if end <= begin:
            return RunResult(move_volume=array("d", bytes(8 * n)) if move_outputs else None,
                             move_depth=array("f", bytes(4 * n)) if move_outputs else None)
        pts = _buf(points, c_double)
        flg = _buf(flags, c_int32)
        tls = _buf(tools, c_int32)
        threads = max(1, int(threads))
        bands = self.bands(threads if move_outputs else threads * 4) if threads > 1 else [(0, self.ny)]
        lib, sim = self.lib, self._p

        def work(band: Tuple[int, int]):
            ctx = lib.ec_ctx_new(events_capacity)
            if not ctx:
                raise KernelError("cannot allocate a kernel context")
            try:
                mv = md = None
                if move_outputs:
                    mv = (c_double * n)()
                    md = (c_float * n)()
                    lib.ec_ctx_set_move_outputs(ctx, mv, md, n)
                lib.ec_sim_run(sim, ctx, pts, flg, tls, begin, end, band[0], band[1])
                count = lib.ec_ctx_event_count(ctx)
                evp = lib.ec_ctx_events(ctx)
                events = [(e.kind, e.move, e.body, e.cells, e.x, e.y, e.z, e.depth, e.volume)
                          for e in (evp[i] for i in range(count))]
                dirty = (c_int32 * 4)()
                lib.ec_ctx_dirty(ctx, dirty)
                return (events, lib.ec_ctx_volume(ctx), lib.ec_ctx_cells_cut(ctx),
                        lib.ec_ctx_events_dropped(ctx), mv, md, tuple(dirty))
            finally:
                lib.ec_ctx_free(ctx)

        if len(bands) == 1:
            parts = [work(bands[0])]
        else:
            with ThreadPoolExecutor(max_workers=threads) as pool:
                parts = list(pool.map(work, bands))

        result = RunResult()
        merged: Dict[Tuple[int, int, int], Event] = {}
        x0 = y0 = 1 << 30
        x1 = y1 = -(1 << 30)
        for events, vol, cells, dropped, mv, md, dirty in parts:
            result.volume += vol
            result.cells += cells
            result.dropped += dropped
            if dirty[0] < dirty[2]:
                x0, y0 = min(x0, dirty[0]), min(y0, dirty[1])
                x1, y1 = max(x1, dirty[2]), max(y1, dirty[3])
            for kind, move, body, ncell, x, y, z, depth, volume in events:
                key = (kind, move, body)
                ev = merged.get(key)
                if ev is None:
                    merged[key] = Event(kind, move, body, ncell, x, y, z, depth, volume)
                else:
                    ev.cells += ncell
                    ev.volume += volume
                    if depth > ev.depth:
                        ev.depth, ev.x, ev.y, ev.z = depth, x, y, z
        result.events = sorted(merged.values(), key=lambda e: (e.move, e.kind, e.body))
        result.dirty = (x0, y0, x1, y1) if x1 > x0 else (0, 0, 0, 0)
        if move_outputs:
            vol_out = array("d", bytes(8 * n))
            dep_out = array("f", bytes(4 * n))
            vbuf, dbuf = _buf(vol_out, c_double), _buf(dep_out, c_float)
            for part in parts:
                lib.ec_merge_move_outputs(vbuf, dbuf, part[4], part[5], begin, end)
            result.move_volume, result.move_depth = vol_out, dep_out
        return result
