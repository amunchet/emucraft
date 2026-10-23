"""Feed-rate optimization.

Two rules, combined by taking the slower feed:

* **corners** -- slow down within a zone around sharp direction changes of
  the cutting path (the classic hand edit for titanium, Inconel and friends);
* **load** -- where the simulation shows the tool removing material faster
  than the nominal cut (inside corners, full-width slots, rest material),
  scale the feed down so the material removal rate (MRR) stays at the limit.
  At a given spindle speed the cutting force follows the MRR, so a ramp or
  plunge that is already programmed slowly is only slowed further if it still
  removes material faster than the limit.

Optionally, feed moves that cut nothing ("air cuts") can be sped up.

Only F words change. Plain G1 lines are split where a slow zone starts or
ends; arcs and canned cycles take the slowest feed of their extent. The new
program is interpreted again and its path is checked against the original
before anything is returned.
"""

from __future__ import annotations

import math
import os
import re
from array import array
from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass, field, fields
from typing import Dict, List, Optional, Tuple

from .check import choose_cell, interpreter_for
from .gcode import EDIT_FEED, EDIT_LOCKED, EDIT_NONE, RAPID, SPINDLE, Message, Program, split_comments
from .kernel import Sim
from .model import Config, resolve_stock, resolve_tools, unit_factor

# Starting points per material; every value can be overridden.
MATERIALS: Dict[str, Dict[str, float]] = {
    "aluminum": dict(corner_angle=60, corner_factor=0.8, corner_zone=0.5, load_limit=2.0, min_factor=0.5),
    "steel": dict(corner_angle=50, corner_factor=0.7, corner_zone=0.75, load_limit=1.5, min_factor=0.4),
    "stainless": dict(corner_angle=45, corner_factor=0.6, corner_zone=1.0, load_limit=1.3, min_factor=0.35),
    "titanium": dict(corner_angle=40, corner_factor=0.5, corner_zone=1.0, load_limit=1.2, min_factor=0.3),
    "inconel": dict(corner_angle=35, corner_factor=0.4, corner_zone=1.5, load_limit=1.1, min_factor=0.25),
}


@dataclass
class OptimizeSpec:
    material: Optional[str] = None
    corner_angle: float = 45.0  # direction change (degrees) that counts as a corner
    corner_factor: float = 0.6  # feed factor at corners of 90 degrees and sharper
    corner_zone: float = 1.0  # slow zone before and after a corner, in tool diameters
    corner_window: float = 0.25  # path length the direction is measured over, in diameters
    load: bool = True  # slow down where the tool removes more than the nominal cut
    load_limit: float = 1.3  # allowed removal rate relative to the nominal cut
    nominal_mrr: Optional[float] = None  # units^3/min; default: median of the cutting moves
    min_factor: float = 0.3  # never slower than this fraction of the programmed feed
    air_factor: float = 1.0  # factor for feed moves that remove nothing (> 1 speeds up)
    max_feed: Optional[float] = None  # cap for any new feed (program units/min)
    feed_step: Optional[float] = None  # rounding of new feeds (default 1 in/min, 10 mm/min)
    resolution: Optional[float] = None  # simulation cell size (default from the stock)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "OptimizeSpec":
        data = dict(data or {})
        names = {f.name for f in fields(cls)}
        unknown = set(data) - names
        if unknown:
            raise ValueError(f"unknown optimize option(s): {', '.join(sorted(unknown))}")
        material = data.get("material")
        base: Dict[str, object] = {}
        if material:
            key = str(material).lower()
            if key not in MATERIALS:
                raise ValueError(f"unknown material '{material}' (known: {', '.join(MATERIALS)})")
            base.update(MATERIALS[key])
        base.update({k: v for k, v in data.items() if v is not None})
        spec = cls(**base)
        spec.validate()
        return spec

    def validate(self) -> None:
        if not 0 < self.corner_factor <= 1 or not 0 < self.min_factor <= 1:
            raise ValueError("corner_factor and min_factor must be in (0, 1]")
        if not 0 < self.corner_angle < 180:
            raise ValueError("corner_angle must be between 0 and 180 degrees")
        if self.corner_zone < 0 or self.corner_window <= 0 or self.load_limit <= 0:
            raise ValueError("corner_zone, corner_window and load_limit must be positive")
        if self.air_factor <= 0:
            raise ValueError("air_factor must be positive")


@dataclass
class OptimizeResult:
    text: str
    program: Program
    optimized: Optional[Program]
    spec: OptimizeSpec
    corners: List[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    messages: List[Message] = field(default_factory=list)
    move_feed: array = field(default_factory=lambda: array("d"))
    move_load: array = field(default_factory=lambda: array("d"))
    verified: bool = False

    def to_dict(self, include_text: bool = False) -> dict:
        d = {
            "spec": asdict(self.spec),
            "corners": self.corners,
            "stats": self.stats,
            "messages": [m.to_dict() for m in self.messages],
            "verified": self.verified,
        }
        if include_text:
            d["text"] = self.text
        return d


# ------------------------------------------------------------------ helpers

_F_WORD = re.compile(r"F\s*([-+]?(?:\d+\.?\d*|\.\d+))", re.I)


def _num(value: float, decimals: int) -> str:
    text = f"{value:.{decimals}f}".rstrip("0")
    return "0." if text in ("-0.", "-.") else text


def _feed_text(value: float) -> str:
    """F value text: full precision, so programmed feeds survive unchanged."""
    return _num(value, 6)


def _quantize(value: float, step: float) -> float:
    """Round a new feed down to a multiple of ``step`` (two significant digits
    for feeds below ten steps). Never rounds up and never returns zero."""
    if value <= 0:
        return value
    if value >= 10 * step:
        return math.floor(value / step + 1e-9) * step
    mag = 10.0 ** math.floor(math.log10(value))
    return math.floor(value / mag * 10 + 1e-9) * mag / 10


def _line_feed_word(raw: str) -> Tuple[Optional[float], bool]:
    """(F value, has an F word) of a line's code, ignoring comments."""
    code, _ = split_comments(raw)
    m = _F_WORD.search(code)
    if not m:
        return None, "F" in code.upper()
    try:
        return float(m.group(1)), True
    except ValueError:
        return None, True


def _with_feed(raw: str, feed_text: str, sep: Optional[str] = None) -> str:
    """Set (or add) the F word of a line, keeping everything else.

    ``sep`` separates an appended F from the previous word; by default it
    follows the line's own spacing."""
    code_end = len(raw)
    for opener in ("(", ";"):
        k = raw.find(opener)
        if 0 <= k < code_end:
            code_end = k
    code, rest = raw[:code_end], raw[code_end:]
    m = _F_WORD.search(code)
    if m:
        code = code[:m.start()] + "F" + feed_text + code[m.end():]
    else:
        stripped = code.rstrip()
        if sep is None:
            sep = " " if " " in stripped.strip() else ""
        code = stripped + sep + "F" + feed_text + code[len(stripped):]
    return code + rest


class _Chain:
    """A run of consecutive cutting moves (by fine index) with arc lengths."""

    def __init__(self, first: int) -> None:
        self.first = first
        self.last = first
        self.s: List[float] = [0.0]  # arc length at fine points first..last+1


def _point(pts: array, k: int) -> Tuple[float, float, float]:
    return pts[3 * k], pts[3 * k + 1], pts[3 * k + 2]


def _lerp_at(pts: array, chain: _Chain, s: float) -> Tuple[float, float, float]:
    """Point at arc length s along the chain (clamped)."""
    S = chain.s
    if s <= 0:
        return _point(pts, chain.first)
    if s >= S[-1]:
        return _point(pts, chain.last + 1)
    i = bisect_right(S, s) - 1
    seg = S[i + 1] - S[i]
    t = (s - S[i]) / seg if seg > 0 else 0.0
    a = _point(pts, chain.first + i)
    b = _point(pts, chain.first + i + 1)
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t


def _angle(u, v) -> Optional[float]:
    nu = math.sqrt(u[0] ** 2 + u[1] ** 2 + u[2] ** 2)
    nv = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if nu < 1e-12 or nv < 1e-12:
        return None
    c = (u[0] * v[0] + u[1] * v[1] + u[2] * v[2]) / (nu * nv)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


# ------------------------------------------------------------------ main


def optimize(program: Program, config: Optional[Config] = None, spec: Optional[OptimizeSpec] = None,
             *, overrides: Optional[Dict[object, Dict[str, object]]] = None,
             stock=None, threads: Optional[int] = None) -> OptimizeResult:
    """Rewrite the feeds of ``program``. ``stock`` (program units) overrides
    the configuration, ``overrides`` works as in :func:`resolve_tools`."""
    config = config or Config()
    spec = spec or OptimizeSpec.from_dict(config.optimize)
    units = program.units
    cf = unit_factor(config.units or units, units)
    result = OptimizeResult(program.text, program, None, spec)
    msgs = result.messages

    if not program.n_moves:
        msgs.append(Message("error", "optimize", "The program has no moves", None))
        return result
    tools = resolve_tools(program, config, overrides)
    slot_specs = {}
    for slot, number in enumerate(program.slot_tools):
        t = tools[number]
        if t.problems():
            msgs.append(Message("warning", "optimize",
                                f"T{number} has no usable geometry; its feeds are left alone", None))
        else:
            slot_specs[slot] = t
    if not slot_specs:
        msgs.append(Message("error", "optimize", "No tool geometry: cannot optimize", None))
        return result

    # ---- fine path: cutting moves split into short pieces
    min_d = min(t.diameter for t in slot_specs.values())
    step = max(min_d / 4.0, 1e-6)
    P = program.points
    fpts = array("d", P[0:3])
    fflags, ftools, fparent = array("i"), array("i"), array("i")
    for i in range(program.n_moves):
        f = program.flags[i]
        j = 3 * i
        x0, y0, z0, x1, y1, z1 = P[j], P[j + 1], P[j + 2], P[j + 3], P[j + 4], P[j + 5]
        length = math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2)
        k = 1 if (f & RAPID or length <= step) else int(math.ceil(length / step))
        for m in range(1, k + 1):
            t = m / k
            if m == k:
                fpts.extend((x1, y1, z1))
            else:
                fpts.extend((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, z0 + (z1 - z0) * t))
            fflags.append(f)
            ftools.append(program.tool_slot[i] if program.tool_slot[i] in slot_specs else -1)
            fparent.append(i)
    nf = len(fflags)

    # ---- simulate to measure the load on every piece
    stock_msgs: List[Message] = []
    if stock is None:
        stock, stock_msgs = resolve_stock(program, config, None)
    if stock is None:
        # fall back to the extent of the cutting moves
        lo = [math.inf] * 3
        hi = [-math.inf] * 3
        for i in range(program.n_moves):
            if program.flags[i] & RAPID:
                continue
            for k in (i, i + 1):
                p = program.point(k)
                for a in range(3):
                    lo[a] = min(lo[a], p[a])
                    hi[a] = max(hi[a], p[a])
        r = max(t.radius for t in slot_specs.values())
        stock, stock_msgs = resolve_stock(program, config, (lo, hi, r))
    msgs.extend(m for m in stock_msgs if m.level != "info")
    if stock is None:
        return result
    cfg = config.copy()
    if spec.resolution:
        cfg.check.resolution = spec.resolution
    else:
        cfg.check.max_cells = min(cfg.check.max_cells, 4_000_000)
    cell = choose_cell(stock, cfg, units, tools)
    nx = max(1, math.ceil((stock.xmax - stock.xmin) / cell - 1e-9))
    ny = max(1, math.ceil((stock.ymax - stock.ymin) / cell - 1e-9))
    with Sim(nx, ny, stock.xmin, stock.ymin, cell, stock.zmax, stock.zmin) as sim:
        for slot, t in slot_specs.items():
            shape, radius, corner, slope, flute = t.kernel_args()
            sim.set_tool(slot, shape, radius, corner, slope, 0.0)
        run = sim.run(fpts, fflags, ftools, threads=threads or min(8, os.cpu_count() or 1),
                      move_outputs=True)
    fvol = run.move_volume

    flen = array("d", bytes(8 * nf))
    fload = array("d", bytes(8 * nf))
    for k in range(nf):
        j = 3 * k
        length = math.sqrt((fpts[j + 3] - fpts[j]) ** 2 + (fpts[j + 4] - fpts[j + 1]) ** 2
                           + (fpts[j + 5] - fpts[j + 2]) ** 2)
        flen[k] = length
        fload[k] = fvol[k] / length if length > 0 else 0.0

    cutting = [k for k in range(nf) if not fflags[k] & RAPID and fflags[k] & SPINDLE and ftools[k] >= 0]

    # ---- nominal removal rate per tool: median of the pieces that actually cut
    mrr = array("d", bytes(8 * nf))
    for k in cutting:
        mrr[k] = fload[k] * program.feed[fparent[k]]
    nominal: Dict[int, float] = {}
    typical_load: Dict[int, float] = {}
    for slot in slot_specs:
        rates = sorted(mrr[k] for k in cutting if ftools[k] == slot and mrr[k] > 0)
        loads = sorted(fload[k] for k in cutting if ftools[k] == slot and fload[k] > 0)
        if loads:
            typical_load[slot] = loads[len(loads) // 2]
        if spec.nominal_mrr:
            nominal[slot] = spec.nominal_mrr * cf ** 3
        elif rates:
            # ignore the lightest skims when finding the typical cut
            heavy = rates[len(rates) // 5:]
            nominal[slot] = heavy[len(heavy) // 2]

    factor = array("d", [1.0]) * nf
    air = bytearray(nf)
    for k in cutting:
        slot = ftools[k]
        nom = nominal.get(slot)
        if fload[k] <= typical_load.get(slot, 0.0) * 1e-3:
            air[k] = 1
        elif spec.load and nom and mrr[k] > spec.load_limit * nom:
            factor[k] = min(factor[k], spec.load_limit * nom / mrr[k])

    # ---- corners
    chains: List[_Chain] = []
    cur: Optional[_Chain] = None
    for k in cutting:
        if cur is not None and k == cur.last + 1 and ftools[k] == ftools[cur.first]:
            cur.last = k
        else:
            cur = _Chain(k)
            chains.append(cur)
    for ch in chains:
        acc = 0.0
        for k in range(ch.first, ch.last + 1):
            acc += flen[k]
            ch.s.append(acc)
    corners = []
    for ch in chains:
        d = slot_specs[ftools[ch.first]].diameter
        w = spec.corner_window * d
        zone = spec.corner_zone * d
        cand = []
        for idx in range(1, ch.last - ch.first + 1):  # interior fine points
            k = ch.first + idx
            if fparent[k] == fparent[k - 1]:
                continue  # split point on a straight move
            s = ch.s[idx]
            p = _point(fpts, k)
            a = _lerp_at(fpts, ch, s - w)
            b = _lerp_at(fpts, ch, s + w)
            ang = _angle((p[0] - a[0], p[1] - a[1], 0.0), (b[0] - p[0], b[1] - p[1], 0.0))
            if ang is not None and ang >= spec.corner_angle:
                cand.append((s, ang, k))
        for s, ang, k in cand:  # keep local maxima only
            if any(abs(s2 - s) <= w and (a2 > ang or (a2 == ang and s2 < s)) for s2, a2, _ in cand):
                continue
            span = max(1e-9, 90.0 - spec.corner_angle)
            fc = 1.0 - (1.0 - spec.corner_factor) * min(1.0, (ang - spec.corner_angle) / span)
            lo = bisect_left(ch.s, s - zone)
            hi = bisect_right(ch.s, s + zone)
            for idx in range(max(0, lo - 1), min(len(ch.s) - 1, hi)):
                kk = ch.first + idx
                if not air[kk]:
                    factor[kk] = min(factor[kk], fc)
            p = _point(fpts, k)
            corners.append({
                "position": list(p), "angle": ang, "factor": fc,
                "line": program.line[fparent[k]] + 1, "move": fparent[k],
            })
    result.corners = corners

    # ---- desired feed per piece
    step_f = spec.feed_step * cf if spec.feed_step else (1.0 if units == "inch" else 10.0)
    max_feed = spec.max_feed * cf if spec.max_feed else None
    want = array("d", bytes(8 * nf))
    for k in range(nf):
        if fflags[k] & RAPID:
            continue
        programmed = program.feed[fparent[k]]
        f = factor[k]
        if air[k] and spec.air_factor != 1.0:
            f = spec.air_factor
        if f < 1.0:
            f = max(f, spec.min_factor)
        if f == 1.0 or programmed <= 0 or ftools[k] < 0:
            want[k] = programmed
            continue
        new = programmed * f
        if max_feed:
            new = min(new, max(max_feed, programmed))
        # round down, but never below the min_factor floor
        new = max(_quantize(new, step_f), min(new, programmed * spec.min_factor))
        want[k] = new if abs(new - programmed) > 1e-9 else programmed

    text, emit_stats = _emit(program, fpts, fflags, fparent, want)
    result.text = text

    # per original move: slowest piece
    move_feed = array("d", program.feed)
    move_load = array("d", bytes(8 * program.n_moves))
    for k in range(nf):
        i = fparent[k]
        if not fflags[k] & RAPID and want[k] < move_feed[i]:
            move_feed[i] = want[k]
        if fload[k] > move_load[i]:
            move_load[i] = fload[k]
    for i in range(program.n_moves):
        line_code = program.line_edit[program.line[i]]
        if line_code in (EDIT_NONE, EDIT_LOCKED):
            move_feed[i] = program.feed[i]
    result.move_feed, result.move_load = move_feed, move_load

    # ---- verify: same path, feeds only
    interp = interpreter_for(config)
    new_prog = interp.run(text, program.name)
    result.optimized = new_prog
    tol = 3e-4 if units == "inch" else 3e-3
    problem = verify_same_path(program, new_prog, tol)
    if not problem:
        problem = _verify_feeds(program, new_prog, tol, max(1.0, spec.air_factor))
    if problem:
        msgs.append(Message("error", "optimize-verify", f"Optimized path differs: {problem}", None))
        result.text = program.text
        result.verified = False
    else:
        result.verified = True

    before = _feed_seconds(program, program.feed)
    after = _feed_seconds(new_prog, new_prog.feed) if result.verified else before
    slowed = sum(1 for k in cutting if want[k] < program.feed[fparent[k]])
    result.stats = {
        "cutting_seconds_before": before,
        "cutting_seconds_after": after,
        "corners": len(corners),
        "pieces": nf,
        "pieces_slowed": slowed,
        "pieces_air": sum(air),
        "nominal_mrr": {str(program.slot_tools[s]): v for s, v in nominal.items()},
        "resolution": cell,
        **emit_stats,
    }
    return result


def _feed_seconds(program: Program, feeds) -> float:
    total = 0.0
    for i in range(program.n_moves):
        if program.flags[i] & RAPID or feeds[i] <= 0:
            continue
        total += program.move_length(i) / feeds[i]
    return total * 60.0


def _word_spacing(lines: List[str]) -> str:
    """'' if the program writes words without spaces ("G1X1.Y2."), else ' '."""
    spaced = packed = 0
    for raw in lines[:500]:
        code, _ = split_comments(raw)
        spaced += len(_SPACED.findall(code))
        packed += len(_PACKED.findall(code))
    return "" if packed > spaced else " "


_SPACED = re.compile(r"[\d.]\s+[A-Z]", re.I)
_PACKED = re.compile(r"[\d.][A-Z]", re.I)


def _emit(program: Program, fpts: array, fflags: array, fparent: array, want: array) -> Tuple[str, dict]:
    units = program.units
    dec = 4 if units == "inch" else 3
    sep = _word_spacing(program.lines)

    # feed pieces per source line, in order
    per_line: Dict[int, List[int]] = {}
    for k in range(len(fflags)):
        if fflags[k] & RAPID:
            continue
        li = program.line[fparent[k]]
        per_line.setdefault(li, []).append(k)

    out: List[str] = []
    out_f: Optional[float] = None  # modal F of the rewritten program (F word value)
    in_f: Optional[float] = None  # modal F of the original program
    changed = added = 0
    eol = "\r\n" if "\r\n" in program.text else "\n"
    for li, raw in enumerate(program.lines):
        code = program.line_edit[li]
        pieces = per_line.get(li)
        value, has_f = _line_feed_word(raw)
        if code in (EDIT_NONE, EDIT_LOCKED) or not pieces:
            if pieces and not has_f and in_f is not None and out_f != in_f:
                # a block we may not touch runs at the modal feed: restore the
                # original one first, in the block's own terms
                out.append(f"F{_feed_text(in_f)}")
                added += 1
                out_f = in_f
            out.append(raw)
            if has_f:
                in_f = out_f = value
            continue
        if has_f:
            in_f = value
        groups: List[Tuple[float, int]] = []  # (feed, last piece)
        for k in pieces:
            if groups and abs(groups[-1][0] - want[k]) < 1e-9:
                groups[-1] = (groups[-1][0], k)
            else:
                groups.append((want[k], k))
        if code == EDIT_FEED or len(groups) == 1:
            feed = min(g[0] for g in groups)
            if (has_f and value is not None and abs(value - feed) > 1e-9) or (not has_f and out_f != feed):
                out.append(_with_feed(raw, _feed_text(feed), sep))
                changed += 1
            else:
                out.append(raw)
            out_f = feed
            continue
        for feed, last in groups[:-1]:
            x, y, z = _point(fpts, last + 1)
            out.append(f"G1{sep}X{_num(x, dec)}{sep}Y{_num(y, dec)}{sep}Z{_num(z, dec)}{sep}F{_feed_text(feed)}")
            added += 1
            out_f = feed
        feed = groups[-1][0]
        out.append(_with_feed(raw, _feed_text(feed), sep))
        changed += 1
        out_f = feed
    text = eol.join(out)
    if program.text.endswith(("\n", "\r")):
        text += eol
    return text, {"lines_changed": changed, "lines_added": added}


def _verify_feeds(a: Program, b: Program, tol: float, max_factor: float) -> Optional[str]:
    """Every feed move of b (following a's path) must keep a positive feed no
    faster than ``max_factor`` times the feed of the move of a it lies on."""
    i = 0
    tol2 = tol * tol
    for j in range(b.n_moves):
        if i >= a.n_moves:
            break
        if not b.flags[j] & RAPID and a.feed[i] > 0:
            if not 0 < b.feed[j] <= a.feed[i] * max_factor * (1 + 1e-9) + 1e-12:
                return (f"feed F{b.feed[j]:g} at line {b.line[j] + 1} does not match the "
                        f"programmed F{a.feed[i]:g}")
        q, p1 = b.point(j + 1), a.point(i + 1)
        if sum((q[k] - p1[k]) ** 2 for k in range(3)) <= tol2:
            i += 1
    return None


def verify_same_path(a: Program, b: Program, tol: float) -> Optional[str]:
    """None if b follows exactly the path of a (b may split a's moves)."""
    if a.n_moves == 0:
        return None if b.n_moves == 0 else "extra moves"
    i = 0
    tol2 = tol * tol
    if any(abs(p - q) > tol for p, q in zip(a.point(0), b.point(0))):
        return "different start point"
    for j in range(b.n_moves):
        if i >= a.n_moves:
            return f"extra move at line {b.line[j] + 1}"
        if (a.flags[i] & RAPID) != (b.flags[j] & RAPID):
            return f"move type changed at line {b.line[j] + 1}"
        p0, p1 = a.point(i), a.point(i + 1)
        q = b.point(j + 1)
        ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
        uu = ux * ux + uy * uy + uz * uz
        t = ((q[0] - p0[0]) * ux + (q[1] - p0[1]) * uy + (q[2] - p0[2]) * uz) / uu if uu > 0 else 1.0
        t = max(0.0, min(1.0, t))
        d2 = sum((q[k] - (p0[k] + t * (p1[k] - p0[k]))) ** 2 for k in range(3))
        if d2 > tol2:
            return f"point off the original path at line {b.line[j] + 1}"
        if sum((q[k] - p1[k]) ** 2 for k in range(3)) <= tol2:
            i += 1
    if i != a.n_moves:
        return f"path ends early (move {i} of {a.n_moves})"
    return None
