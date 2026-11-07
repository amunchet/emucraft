"""Fixtures: vises, clamps and plates the tool must never touch.

A fixture is an STL model (or a box) placed in program coordinates. The
checker sees it from +Z as a height field, like the stock (Sim.add_mesh), and
runs every move over it with the same kernel and tools: anything the cutter,
shank or holder would remove there is a collision.

G-code has no word for its fixturing (G54 names an origin, not the hardware
around it), so a check chooses: see choose_fixtures. Uploaded models and saved
setups (fixtures placed for a job) live in the shared data folder.
"""

from __future__ import annotations

import json
import math
import re
import struct
import sys
from array import array
from dataclasses import dataclass, replace
from functools import cached_property, lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .config import ConfigError, config_from_dict, fixture_dict
from .gcode import Message, Program
from .model import Config, FixtureSpec, data_dir, fixture_dir, unit_factor, write_file

_meshes: Dict[Tuple[str, int, int, int], array] = {}
_VERTEX = re.compile(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)")


def parse_stl(data: bytes, label: object) -> array:
    """Triangles of a binary or ASCII STL: 9 floats each."""
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
            raise ValueError(f"{label}: not an STL file") from None
    if not tris or len(tris) % 9 or not math.isfinite(sum(tris)):
        raise ValueError(f"{label}: not an STL file")
    return tris


def _version(path: Path) -> Tuple[str, int, int, int]:
    """Tells versions of a file apart; times alone do not, as they only move with the kernel's clock tick."""
    st = path.stat()
    return str(path), st.st_mtime_ns, st.st_size, st.st_ino


def read_stl(path) -> array:
    """Triangles of an STL file (cached per file version)."""
    path = Path(path)
    key = _version(path)
    tris = _meshes.get(key)
    if tris is not None:
        return tris
    tris = parse_stl(path.read_bytes(), path)
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
    spec: Optional[FixtureSpec] = None  # as placed: at and position_units in program units

    @cached_property
    def bounds(self) -> Tuple[List[float], List[float]]:
        return _bounds(self.triangles)

    def to_dict(self) -> dict:
        lo, hi = self.bounds
        return {"name": self.name, "source": self.source, "triangles": len(self.triangles) // 9,
                "bounds": [lo, hi], "spec": fixture_dict(self.spec)}


def _bounds(t: array) -> Tuple[List[float], List[float]]:
    return ([min(t[a::3]) for a in range(3)], [max(t[a::3]) for a in range(3)])


def place_fixtures(config: Config, units: str, specs: Optional[List[FixtureSpec]] = None) -> List[Fixture]:
    """Fixtures (default: the configured ones) scaled, turned and moved into program units."""
    out = []
    for spec in config.fixtures if specs is None else specs:
        raw = read_stl(spec.model) if spec.model else _box(*spec.box)
        model_units = spec.units or config.units or units
        k = spec.scale * unit_factor(model_units, units)
        cx, cy, cz = (math.cos(math.radians(a)) for a in spec.rotate)
        sx, sy, sz = (math.sin(math.radians(a)) for a in spec.rotate)
        m = [k * v for v in (cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx,
                             sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx,
                             -sy, cy * sx, cy * cx)]  # Rz Ry Rx, scaled
        lo, hi = _bounds(raw)
        c = [(a + b) / 2 for a, b in zip(lo, hi)]
        center = [m[j] * c[0] + m[j + 1] * c[1] + m[j + 2] * c[2] for j in (0, 3, 6)]
        f = unit_factor(spec.position_units or config.units or units, units)
        if spec.at is not None:
            at = [v * f for v in spec.at]
            ox, oy, oz = (a - b for a, b in zip(at, center))
        else:
            ox, oy, oz = (v * f for v in spec.offset)
            at = [a + b for a, b in zip((ox, oy, oz), center)]
        tris = array("f", bytes(4 * len(raw)))
        for i in range(0, len(raw), 3):
            x, y, z = raw[i], raw[i + 1], raw[i + 2]
            tris[i] = m[0] * x + m[1] * y + m[2] * z + ox
            tris[i + 1] = m[3] * x + m[4] * y + m[5] * z + oy
            tris[i + 2] = m[6] * x + m[7] * y + m[8] * z + oz
        placed = replace(spec, name=spec.label, units=model_units, at=tuple(at), position_units=units)
        out.append(Fixture(spec.label, spec.model or "box", tris, placed))
    return out


# ------------------------------------------------------------------ library


def clean_name(name: str) -> str:
    """A name as a file name, the same however a comment, a post or a caller writes it."""
    return re.sub(r"[^a-z0-9._-]+", "-", name.strip().lower()).strip("-.")


def _model_path(name: str) -> Path:
    stem = clean_name(re.sub(r"\.stl$", "", name.strip(), flags=re.I))
    if not stem:
        raise ValueError(f"'{name}' cannot name a fixture model")
    return fixture_dir() / f"{stem}.stl"


@lru_cache(maxsize=256)
def _model_info(version: Tuple[str, int, int, int]) -> dict:
    """An uploaded model: its name, and its size in the file's own units (an STL does not say which)."""
    path, _, size, _ = version
    tris = read_stl(path)
    return {"name": Path(path).name, "bytes": size, "triangles": len(tris) // 9, "bounds": _bounds(tris)}


def list_models() -> List[dict]:
    return [_model_info(_version(p)) for p in sorted(fixture_dir().glob("*.stl"))]


def save_model(name: str, data: bytes, overwrite: bool = False) -> dict:
    """Keep an uploaded STL in the library."""
    path = _model_path(name)
    parse_stl(data, path.name)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path.name} is already in the library")
    write_file(path, data)
    return _model_info(_version(path))


def delete_model(name: str) -> None:
    _model_path(name).unlink()


def _setup_path(name: str) -> Path:
    return data_dir() / "setups" / f"{clean_name(name)}.json"


def read_setup(name: str) -> Optional[List[dict]]:
    """The fixture entries of a saved setup, if there is one."""
    path = _setup_path(name)
    return json.loads(path.read_text(encoding="utf-8"))["fixtures"] if path.is_file() else None


def list_setups() -> List[dict]:
    return [{"name": p.stem, "fixtures": read_setup(p.stem)} for p in sorted(_setup_path("x").parent.glob("*.json"))]


def save_setup(name: str, specs: List[FixtureSpec]) -> dict:
    """Keep fixtures as placed under a name; the name is how programs find them (choose_fixtures)."""
    if not clean_name(name):
        raise ValueError(f"'{name}' cannot name a fixture setup")
    fixtures = [fixture_dict(s) for s in specs]
    write_file(_setup_path(name), json.dumps({"fixtures": fixtures}, indent=2).encode("utf-8"))
    return {"name": clean_name(name), "fixtures": fixtures}


def delete_setup(name: str) -> None:
    _setup_path(name).unlink()


def fixture_specs(entries: List[dict]) -> List[FixtureSpec]:
    """Fixture entries as a config would give them; bare model names are library models."""
    return config_from_dict({"fixtures": entries}, fixture_dir()).fixtures


def choose_fixtures(program: Program, config: Config) -> Tuple[List[FixtureSpec], Optional[str], List[Message]]:
    """The fixtures a program runs in when the caller did not choose, and where they came from.

    First found: the saved setup its (FIXTURE: name) comment names, the saved
    setup named after the program, the config's. A setup the program counts on
    but the check cannot use is reported: an unseen fixture is not a safe one.
    """
    note, line = program.fixture_note, program.fixture_line
    name = clean_name(note or Path(program.name).stem)
    try:
        entries = read_setup(name)
        if entries is not None:
            where = f"program line {line + 1}" if note else "named after the program"
            return fixture_specs(entries), f"setup {name} ({where})", []
    except (ConfigError, KeyError, ValueError) as exc:
        return [], None, [Message("error", "fixtures", f"Fixture setup {name} cannot be used: {exc}", line)]
    source = "config" if config.fixtures else None
    if not note:
        return config.fixtures, source, []
    instead = "using the config's fixtures" if config.fixtures else "no fixtures were checked"
    return config.fixtures, source, [Message("warning", "fixtures", f"Fixture setup {name} is not saved; {instead}", line)]
