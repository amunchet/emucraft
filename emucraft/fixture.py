"""Fixtures: vises, clamps and plates the tool must never touch.

A fixture is an STL model (or a box) placed in program coordinates. The
checker sees it from +Z as a height field, like the stock (Sim.add_mesh), and
runs every move over it with the same kernel and tools: anything the cutter,
shank or holder would remove there is a collision.
"""

from __future__ import annotations

import math
import re
import struct
import sys
from array import array
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from .model import Config, unit_factor

_meshes: Dict[Tuple[str, int, int], array] = {}
_VERTEX = re.compile(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)")


def read_stl(path) -> array:
    """Triangles of a binary or ASCII STL file: 9 floats each (cached per file version)."""
    path = Path(path)
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    tris = _meshes.get(key)
    if tris is not None:
        return tris
    data = path.read_bytes()
    count = struct.unpack_from("<I", data, 80)[0] if len(data) >= 84 else -1
    if len(data) == 84 + 50 * count:
        tris = array("f")
        for i in range(count):
            tris.frombytes(data[84 + 50 * i + 12:84 + 50 * i + 48])
        if sys.byteorder != "little":  # pragma: no cover
            tris.byteswap()
    else:
        try:
            tris = array("f", (float(v) for m in _VERTEX.finditer(data) for v in m.groups()))
        except ValueError:
            raise ValueError(f"{path}: not an STL file") from None
        if not tris or len(tris) % 9:
            raise ValueError(f"{path}: not an STL file")
    if len(_meshes) > 16:
        _meshes.clear()
    _meshes[key] = tris
    return tris


def _box(lo: Sequence[float], hi: Sequence[float]) -> array:
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    c = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
             (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return array("f", (v for f in faces for k in f for v in c[k]))


@dataclass
class Fixture:
    """A fixture placed in program units."""

    name: str
    source: str  # model file, or "box"
    triangles: array  # array('f'), 9 values per triangle

    @cached_property
    def bounds(self) -> Tuple[List[float], List[float]]:
        t = self.triangles
        return ([min(t[a::3]) for a in range(3)], [max(t[a::3]) for a in range(3)])

    def to_dict(self) -> dict:
        lo, hi = self.bounds
        return {"name": self.name, "source": self.source, "triangles": len(self.triangles) // 9,
                "bounds": [lo, hi]}


def place_fixtures(config: Config, units: str) -> List[Fixture]:
    """The configured fixtures, scaled, turned and moved into program units."""
    cf = unit_factor(config.units or units, units)
    out = []
    for spec in config.fixtures:
        raw = read_stl(spec.model) if spec.model else _box(*spec.box)
        k = spec.scale * unit_factor(spec.units or config.units or units, units)
        a = math.radians(spec.rotate)
        ca, sa = math.cos(a) * k, math.sin(a) * k
        ox, oy, oz = (v * cf for v in spec.offset)
        tris = array("f", bytes(4 * len(raw)))
        for i in range(0, len(raw), 3):
            x, y, z = raw[i], raw[i + 1], raw[i + 2]
            tris[i] = ca * x - sa * y + ox
            tris[i + 1] = sa * x + ca * y + oy
            tris[i + 2] = k * z + oz
        out.append(Fixture(spec.label, spec.model or "box", tris))
    return out

