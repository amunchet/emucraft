"""Collision check: interpret a program, simulate it, report what went wrong.

This is the entry point used by the CLI, the web server and the tests::

    result = check_file("part.nc")
    print(result.status, len(result.issues))
"""

from __future__ import annotations

import math
import os
import time
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import __version__
from .config import find_config, load_config
from .gcode import CYCLE, NOCUT, RAPID, SPINDLE, Interpreter, Message, Program
from .kernel import EV_BODY, EV_FLUTE, EV_NOCUT_CUT, EV_RAPID_CUT, EV_SPINDLE_OFF_CUT, Sim, simplify
from .model import (
    Config,
    StockSpec,
    ToolSpec,
    default_rapid_rate,
    default_tolerance,
    resolve_stock,
    resolve_tools,
    unit_factor,
)

MAX_ISSUES = 500


@dataclass
class Grid:
    nx: int
    ny: int
    x0: float
    y0: float
    cell: float

    def to_dict(self) -> dict:
        return {"nx": self.nx, "ny": self.ny, "x0": self.x0, "y0": self.y0, "cell": self.cell}


@dataclass
class Issue:
    """One problem, possibly spanning a run of consecutive moves."""

    kind: str  # rapid-cut, spindle-off-cut, link-cut, shank, holder, table, travel, tool
    severity: str  # "error" | "warning"
    message: str
    first_move: int
    last_move: int
    first_line: int  # 0-based
    last_line: int
    position: Tuple[float, float, float]  # worst point (material, or tip for table/travel)
    depth: float = 0.0
    volume: float = 0.0
    tool: Optional[int] = None
    moves: int = 1

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "lines": [self.first_line + 1, self.last_line + 1],
            "moves": [self.first_move, self.last_move],
            "move_count": self.moves,
            "position": list(self.position),
            "depth": self.depth,
            "volume": self.volume,
            "tool": self.tool,
        }


@dataclass
class CheckResult:
    program: Program
    config: Config
    stock: Optional[StockSpec]
    tools: Dict[int, ToolSpec]
    grid: Optional[Grid]
    issues: List[Issue]
    messages: List[Message]
    stats: dict
    timing: dict
    flags: array
    sim: Optional[Sim] = None
    initial_heights: Optional[array] = None  # starting stock of a chained check

    @property
    def errors(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def status(self) -> str:
        if self.errors or any(m.level == "error" for m in self.messages):
            return "fail"
        if self.issues or any(m.level == "warning" for m in self.messages):
            return "warn"
        return "pass"

    def summary(self) -> str:
        collisions = len(self.errors)
        warnings = len(self.issues) - collisions
        msg_err = sum(1 for m in self.messages if m.level == "error")
        msg_warn = sum(1 for m in self.messages if m.level == "warning")
        parts = []
        parts.append(f"{collisions} collision{'s' if collisions != 1 else ''}")
        if warnings:
            parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
        if msg_err:
            parts.append(f"{msg_err} program error{'s' if msg_err != 1 else ''}")
        if msg_warn:
            parts.append(f"{msg_warn} program warning{'s' if msg_warn != 1 else ''}")
        return ", ".join(parts)

    def to_dict(self) -> dict:
        p = self.program
        return {
            "emucraft": __version__,
            "status": self.status,
            "summary": self.summary(),
            "program": {
                "name": p.name,
                "lines": len(p.lines),
                "units": p.units,
                "moves": p.n_moves,
                "tools": list(p.slot_tools),
                "end_line": None if p.end_line is None else p.end_line + 1,
            },
            "issues": [i.to_dict() for i in self.issues],
            "messages": [m.to_dict() for m in self.messages],
            "stock": self.stock.to_dict() if self.stock else None,
            "tools": {str(k): v.to_dict() for k, v in self.tools.items()},
            "machine": {
                "rapid_rate": self.stats.get("rapid_rate"),
                "rapid_mode": self.config.machine.rapid_mode,
                "table_z": self.stats.get("table_z"),
            },
            "grid": self.grid.to_dict() if self.grid else None,
            "stats": self.stats,
            "timing": self.timing,
        }


# ------------------------------------------------------------------ setup


def interpreter_for(config: Config) -> Interpreter:
    """An interpreter configured from ``config`` (lengths in config units)."""
    chk = config.check
    stock_top = config.stock.zmax if config.stock else None

    def tool_radius(number: int) -> Optional[float]:
        values = config.tools.get(number) or {}
        d = values.get("diameter") or config.default_tool.get("diameter")
        return None if d is None else float(d) / 2.0

    rules = None
    if config.comment_rules:
        from .gcode import DEFAULT_COMMENT_RULES

        rules = list(config.comment_rules) + list(DEFAULT_COMMENT_RULES)
    variables = None
    if config.variable_rules:
        from .gcode import DEFAULT_VARIABLE_RULES

        variables = {**DEFAULT_VARIABLE_RULES, **config.variable_rules}
    return Interpreter(
        units=chk.units,
        default_units=chk.default_units,
        rapid_mode=config.machine.rapid_mode,
        chord_tolerance=chk.chord_tolerance,
        arc_radius_tolerance=chk.arc_radius_tolerance,
        home_z=config.machine.home_z,
        block_delete=chk.block_delete,
        tool_point=chk.tool_point,
        tool_radius=tool_radius,
        comment_rules=rules,
        variable_rules=variables,
        stock_top=stock_top,
        option_units=config.units,
    )


def parse_program(text: str, name: str = "program", config: Optional[Config] = None) -> Program:
    return interpreter_for(config or Config()).run(text, name)


def _nice(value: float) -> float:
    """Round up to two significant digits."""
    if value <= 0:
        return value
    exp = math.floor(math.log10(value)) - 1
    step = 10.0 ** exp
    return round(math.ceil(value / step - 1e-9) * step, 12)


def choose_cell(stock: StockSpec, config: Config, units: str, tools: Dict[int, ToolSpec]) -> float:
    if config.check.resolution:
        return float(config.check.resolution) * unit_factor(config.units or units, units)
    area = (stock.xmax - stock.xmin) * (stock.ymax - stock.ymin)
    max_cells = max(1000, int(config.check.max_cells))
    cell = math.sqrt(area / max_cells)
    # small tools need several cells across their radius, within reason: at
    # most four times the cell budget (run_check warns when that is coarse)
    radii = [t.radius for t in tools.values() if t.radius]
    if radii:
        cell = min(cell, max(min(radii) / 4.0, math.sqrt(area / (4.0 * max_cells))))
    return _nice(cell)


def trim_to_stock(sim: Sim, stock: StockSpec) -> None:
    """The grid overhangs the stock by up to a cell; cells whose centers fall
    outside the stock box start without material."""
    nx, ny, cell = sim.nx, sim.ny, sim.cell
    cols = [ix for ix in range(nx - 2, nx) if ix >= 0 and stock.xmin + (ix + 0.5) * cell > stock.xmax]
    rows = [iy for iy in range(ny - 2, ny) if iy >= 0 and stock.ymin + (iy + 0.5) * cell > stock.ymax]
    if not cols and not rows:
        return
    h = sim.heights()
    empty = float(stock.zmin)
    blank_row = array("f", [empty]) * nx
    for iy in range(ny):
        base = iy * nx
        if iy in rows:
            h[base:base + nx] = blank_row
        else:
            for ix in cols:
                h[base + ix] = empty
    sim.invalidate()


def _feed_bounds(program: Program, flags: array):
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    pts = program.points
    found = False
    for i in range(program.n_moves):
        f = flags[i]
        if f & RAPID or not f & SPINDLE:
            continue
        found = True
        for k in (3 * i, 3 * i + 3):
            for a in range(3):
                v = pts[k + a]
                if v < lo[a]:
                    lo[a] = v
                if v > hi[a]:
                    hi[a] = v
    return (lo, hi) if found else None


# ------------------------------------------------------------------ run


def run_check(
    program: Program,
    config: Optional[Config] = None,
    *,
    overrides: Optional[Dict[object, Dict[str, object]]] = None,
    stock: Optional[StockSpec] = None,
    cell: Optional[float] = None,
    table_z: Optional[float] = None,
    link_feed: Optional[float] = None,
    threads: Optional[int] = None,
    keep_sim: bool = False,
    previous: Optional["CheckResult"] = None,
    keep_initial: bool = False,
    progress: Optional[Callable[[float], None]] = None,
) -> CheckResult:
    """Simulate an interpreted program and collect every issue.

    ``stock``, ``cell``, ``table_z`` and ``link_feed`` override the
    configuration and are in program units.

    ``previous`` chains programs: this one starts from the stock the previous
    check (run with ``keep_sim=True``) left behind, on the same grid. Its
    simulation moves to this result. ``keep_initial`` keeps a copy of the
    starting stock so the viewer can show it.
    """
    chain_note = None
    if previous is not None:
        if previous.sim is None and previous.stock is None:
            # nothing was simulated before (no stock): start on a fresh block
            chain_note = Message("warning", "chain",
                                 f"{previous.program.name} had no stock to continue from; "
                                 "this program starts on a fresh block", None)
            previous = None
        elif previous.sim is None:
            raise ValueError("chain from a result checked with keep_sim=True")
        elif previous.program.units != program.units:
            raise ValueError("chained programs must use the same units")
    config = config or Config()
    t0 = time.perf_counter()
    units = program.units
    cf = unit_factor(config.units or units, units)
    messages: List[Message] = list(program.messages)
    if chain_note is not None:
        messages.append(chain_note)
    tools = resolve_tools(program, config, overrides)

    flags = array("i", program.flags)
    if link_feed is None and config.check.link_feed:
        link_feed = float(config.check.link_feed) * cf
    if link_feed:
        limit = float(link_feed)
        for i in range(program.n_moves):
            if not flags[i] & RAPID and program.feed[i] >= limit:
                flags[i] |= NOCUT

    if previous is not None:
        stock = previous.stock
        if program.stock_note and all(k in program.stock_note for k in ("xmin", "xmax", "ymin", "ymax")):
            messages.append(Message("info", "chained-stock",
                                    f"Continuing on the stock left by {previous.program.name}", None))
    if stock is None:
        bounds = None
        fb = _feed_bounds(program, flags)
        if fb:
            r = max((t.radius or 0.0) for t in tools.values()) if tools else 0.0
            bounds = (fb[0], fb[1], r)
        stock, stock_msgs = resolve_stock(program, config, bounds)
        messages.extend(stock_msgs)

    tolerance = config.check.tolerance
    tolerance = tolerance * cf if tolerance is not None else default_tolerance(units)
    rapid_rate = config.machine.rapid_rate
    rapid_rate = rapid_rate * cf if rapid_rate else default_rapid_rate(units)
    if table_z is None:
        table_z = config.machine.table_z
        if table_z is not None:
            table_z *= cf
        elif stock is not None:
            table_z = stock.zmin

    issues: List[Issue] = []
    grid = None
    sim = None
    kernel_time = 0.0
    volume_by_tool: Dict[int, float] = {}
    removed = 0.0
    dropped = 0
    simplified = None

    slot_specs: Dict[int, ToolSpec] = {}
    bad_tools: Dict[int, List[str]] = {}
    for slot, number in enumerate(program.slot_tools):
        spec = tools[number]
        problems = spec.problems()
        if problems:
            bad_tools[slot] = problems
        else:
            slot_specs[slot] = spec

    initial_heights = None
    if stock is not None:
        if previous is not None:
            grid = previous.grid
            cell = grid.cell
        else:
            cell = cell or choose_cell(stock, config, units, tools)
            nx = max(1, math.ceil((stock.xmax - stock.xmin) / cell - 1e-9))
            ny = max(1, math.ceil((stock.ymax - stock.ymin) / cell - 1e-9))
            grid = Grid(nx, ny, stock.xmin, stock.ymin, cell)
        for slot, spec in slot_specs.items():
            r = spec.radius or 0.0
            if cell > r / 2.0:
                messages.append(Message(
                    "warning", "resolution",
                    f"Resolution {cell:g} is coarse for T{spec.number} (diameter {spec.diameter:g}); "
                    f"results near its cuts are approximate", None))
        if previous is not None:
            sim, previous.sim = previous.sim, None
            sim.set_tolerance(tolerance)
            # slots belong to programs: never cut with the previous program's tools
            for slot in range(max(len(previous.program.slot_tools), len(program.slot_tools))):
                sim.clear_tool(slot)
            if keep_initial:
                initial_heights = array("f", sim.heights())
        else:
            sim = Sim(grid.nx, grid.ny, stock.xmin, stock.ymin, cell, stock.zmax, stock.zmin, tolerance)
            trim_to_stock(sim, stock)
        for slot, spec in slot_specs.items():
            shape, radius, corner, slope, flute = spec.kernel_args()
            sim.set_tool(slot, shape, radius, corner, slope, flute,
                         [(b[0], b[1]) for b in spec.bodies()])
        threads = threads or config.check.threads or min(8, os.cpu_count() or 1)
        tk = time.perf_counter()
        eps = config.check.simplify
        eps = eps * cf if eps is not None else min(tolerance / 2.0, cell / 4.0)
        if eps > 0:
            # Moves whose cutting is itself the error (rapids, links, spindle
            # off) keep their own segment so their events name the exact move.
            keys = array("i", program.tool_slot)
            for i in range(program.n_moves):
                f = flags[i]
                if f & (RAPID | NOCUT) or not f & SPINDLE:
                    keys[i] = -2 - i
            points, kflags, _, first = simplify(program.points, flags, keys, eps)
            slots = array("i", (program.tool_slot[i] for i in first))
        else:
            points, kflags, slots = program.points, flags, program.tool_slot
            first = None
        events = []
        start = 0
        n = len(kflags)
        while start < n:
            end = start + 1
            while end < n and slots[end] == slots[start]:
                end += 1
            res = sim.run(points, kflags, slots, start, end, threads=threads)
            events.extend(res.events)
            removed += res.volume
            dropped += res.dropped
            number = program.slot_tools[slots[start]] if slots[start] >= 0 else None
            if number is not None:
                volume_by_tool[number] = volume_by_tool.get(number, 0.0) + res.volume
            if progress:
                progress(end / n)
            start = end
        if first is not None:
            n_merged = len(first)
            for ev in events:  # back to original move numbers
                k = ev.move
                ev.move = first[k]
                ev.last = (first[k + 1] if k + 1 < n_merged else program.n_moves) - 1
        kernel_time = time.perf_counter() - tk
        simplified = n
        issues.extend(_issues_from_events(events, program, slot_specs, units,
                                          link_severity=config.check.link_severity))
        if dropped:
            messages.append(Message("warning", "events-truncated",
                                    f"{dropped} collision events were not recorded (too many)", None))

    issues.extend(_path_issues(program, flags, table_z, config, cf, tolerance, units))
    for slot, problems in bad_tools.items():
        moves = [i for i in range(program.n_moves) if program.tool_slot[i] == slot]
        cutting = [i for i in moves if not flags[i] & RAPID]
        if not cutting:
            continue
        number = program.slot_tools[slot]
        first, last = cutting[0], cutting[-1]
        issues.append(Issue(
            "tool", "error",
            f"T{number} cannot be simulated ({'; '.join(problems)}); its {len(cutting)} feed moves were not checked",
            first, last, program.line[first], program.line[last], program.point(first + 1),
            tool=number, moves=len(cutting)))

    issues.sort(key=lambda i: (i.first_move, i.kind))
    if len(issues) > MAX_ISSUES:
        extra = len(issues) - MAX_ISSUES
        issues = issues[:MAX_ISSUES]
        messages.append(Message("warning", "issues-truncated", f"{extra} more issues not listed", None))

    stats = _stats(program, flags, rapid_rate, config.machine.tool_change_time, tools)
    stats.update({
        "rapid_rate": rapid_rate,
        "table_z": table_z,
        "tolerance": tolerance,
        "link_feed": link_feed or None,
        "volume_removed": removed,
        "volume_by_tool": {str(k): v for k, v in volume_by_tool.items()},
        "simulated_segments": simplified,
    })
    if stock is not None and sim is not None:
        stock_volume = (stock.xmax - stock.xmin) * (stock.ymax - stock.ymin) * (stock.zmax - stock.zmin)
        stats["stock_volume"] = stock_volume
        stats["remaining_volume"] = sim.volume()
    timing = {"simulate": kernel_time, "check": time.perf_counter() - t0}
    if not keep_sim and sim is not None:
        sim.close()
        sim = None
    return CheckResult(program, config, stock, tools, grid, issues, messages, stats, timing, flags, sim,
                       initial_heights)


def _fmt(value: float, units: str) -> str:
    return f"{value:.4f} in" if units == "inch" else f"{value:.3f} mm"


def _issues_from_events(events, program: Program, specs: Dict[int, ToolSpec], units: str,
                        link_severity: str = "warning") -> List[Issue]:
    labels = {EV_RAPID_CUT: "rapid-cut", EV_SPINDLE_OFF_CUT: "spindle-off-cut",
              EV_NOCUT_CUT: "link-cut", EV_FLUTE: "shank"}
    open_: Dict[Tuple[str, int], Issue] = {}
    out: List[Issue] = []
    for ev in sorted(events, key=lambda e: (e.move, e.kind, e.body)):
        slot = program.tool_slot[ev.move]
        spec = specs.get(slot)
        if ev.kind == EV_BODY:
            bodies = spec.bodies() if spec else []
            kind = bodies[ev.body][2] if 0 <= ev.body < len(bodies) else "holder"
        else:
            kind = labels.get(ev.kind, "collision")
        key = (kind, ev.body if ev.kind == EV_BODY else -1)
        line = program.line[ev.move]
        last = max(ev.move, ev.last)  # an event on a merged run covers all of it
        cur = open_.get(key)
        if cur is not None and (ev.move - cur.last_move <= 1 or line <= cur.last_line + 1):
            if last > cur.last_move:
                cur.moves += last - max(cur.last_move + 1, ev.move) + 1
                cur.last_move = last
                cur.last_line = program.line[last]
            cur.volume += ev.volume
            if ev.depth > cur.depth:
                cur.depth = ev.depth
                cur.position = (ev.x, ev.y, ev.z)
            continue
        if cur is not None:
            out.append(cur)
        number = spec.number if spec else program.tool_number(ev.move)
        cur = Issue(kind, "error", "", ev.move, last, line, program.line[last], (ev.x, ev.y, ev.z),
                    depth=ev.depth, volume=ev.volume, tool=number, moves=last - ev.move + 1)
        if ev.kind == EV_NOCUT_CUT:
            cur.severity = link_severity
        open_[key] = cur
    out.extend(open_.values())
    for issue in out:
        issue.message = _event_message(issue, program, specs, units)
    return out


def _event_message(issue: Issue, program: Program, specs: Dict[int, ToolSpec], units: str) -> str:
    d = _fmt(issue.depth, units)
    tool = f"T{issue.tool}" if issue.tool is not None else "The tool"
    if issue.kind == "rapid-cut":
        return f"Rapid move cuts material ({d} deep) with {tool}"
    if issue.kind == "spindle-off-cut":
        return f"{tool} cuts material ({d} deep) with the spindle stopped"
    if issue.kind == "link-cut":
        feed = program.feed[issue.first_move]
        return f"High-feed link move (F{feed:g}) cuts material ({d} deep)"
    if issue.kind == "shank":
        spec = specs.get(program.tool_slot[issue.first_move])
        flute = f" {_fmt(spec.flute_length, units)}" if spec and spec.flute_length else ""
        return f"Material reaches {d} above the flute length{flute} of {tool}: the shank rubs"
    if issue.kind == "holder":
        return f"Tool holder of {tool} hits the stock ({d} interference)"
    return f"Collision ({d})"


def _path_issues(program: Program, flags: array, table_z: Optional[float], config: Config,
                 cf: float, tolerance: float, units: str) -> List[Issue]:
    """Checks that only need the path: table plane and machine travel."""
    out: List[Issue] = []
    pts = program.points
    n = program.n_moves
    if table_z is not None:
        limit = table_z - tolerance
        cur: Optional[Issue] = None
        for i in range(n):
            z = min(pts[3 * i + 2], pts[3 * i + 5])
            if z >= limit:
                continue
            depth = table_z - z
            line = program.line[i]
            if cur is not None and i - cur.last_move <= 1:
                cur.last_move, cur.last_line, cur.moves = i, line, cur.moves + 1
                if depth > cur.depth:
                    cur.depth = depth
                    cur.position = program.point(i if pts[3 * i + 2] < pts[3 * i + 5] else i + 1)
                continue
            if cur is not None:
                out.append(cur)
            k = i if pts[3 * i + 2] < pts[3 * i + 5] else i + 1
            cur = Issue("table", "error", "", i, i, line, line, program.point(k), depth=depth,
                        tool=program.tool_number(i))
        if cur is not None:
            out.append(cur)
        for issue in out:
            issue.message = (f"Tool tip goes {_fmt(issue.depth, units)} below the table / fixture "
                             f"plane Z{table_z:g}")
    limits = config.machine.limits or {}
    for axis, rng in limits.items():
        a = "xyz".index(axis.lower())
        lo, hi = rng[0] * cf, rng[1] * cf
        cur = None
        for i in range(n + 1):
            v = pts[3 * i + a]
            if lo - tolerance <= v <= hi + tolerance:
                continue
            if a == 2 and (i == 0 or v == program.home_z):
                continue  # the assumed start height and home retracts are not programmed
            move = max(0, i - 1)
            line = program.line[move] if n else 0
            if cur is not None and move - cur.last_move <= 1:
                cur.last_move, cur.last_line = move, line
                cur.moves += 1
                continue
            if cur is not None:
                out.append(cur)
            cur = Issue("travel", "error",
                        f"{axis.upper()} leaves the machine travel [{lo:g}, {hi:g}] ({axis.upper()}{v:g})",
                        move, move, line, line, program.point(i), tool=program.tool_number(move) if n else None)
        if cur is not None:
            out.append(cur)
    return out


def _stats(program: Program, flags: array, rapid_rate: float, tool_change_time: float,
           tools: Dict[int, ToolSpec]) -> dict:
    pts = program.points
    feeds = program.feed
    rapid_len = feed_len = cut_minutes = 0.0
    counts = {"rapid": 0, "feed": 0, "arc": 0, "cycle": 0}
    per_tool: Dict[int, dict] = {}
    slots = program.tool_slot
    sqrt = math.sqrt
    for i in range(program.n_moves):
        j = 3 * i
        dx = pts[j + 3] - pts[j]
        dy = pts[j + 4] - pts[j + 1]
        dz = pts[j + 5] - pts[j + 2]
        length = sqrt(dx * dx + dy * dy + dz * dz)
        f = flags[i]
        slot = slots[i]
        entry = per_tool.get(slot)
        if entry is None:
            entry = per_tool[slot] = {"feed_length": 0.0, "rapid_length": 0.0, "minutes": 0.0}
        if f & RAPID:
            rapid_len += length
            counts["rapid"] += 1
            entry["rapid_length"] += length
            entry["minutes"] += length / rapid_rate
        else:
            feed_len += length
            counts["feed"] += 1
            entry["feed_length"] += length
            if feeds[i] > 0:
                cut_minutes += length / feeds[i]
                entry["minutes"] += length / feeds[i]
            if f & 8:
                counts["arc"] += 1
            if f & CYCLE:
                counts["cycle"] += 1
    rapid_minutes = (rapid_len + program.cycle_rapid) / rapid_rate
    seconds = (cut_minutes + rapid_minutes) * 60.0 + program.dwell + program.tool_changes * tool_change_time
    by_tool = {}
    for slot, entry in per_tool.items():
        number = program.slot_tools[slot] if slot >= 0 else None
        by_tool[str(number)] = {
            "feed_length": entry["feed_length"],
            "rapid_length": entry["rapid_length"],
            "seconds": entry["minutes"] * 60.0,
        }
    xs, ys, zs = pts[0::3], pts[1::3], pts[2::3]
    bounds = [[min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]] if len(pts) else None
    return {
        "moves": counts,
        "rapid_length": rapid_len,
        "feed_length": feed_len,
        "cycle_seconds": seconds,
        "cutting_seconds": cut_minutes * 60.0,
        "rapid_seconds": rapid_minutes * 60.0,
        "dwell_seconds": program.dwell,
        "tool_changes": program.tool_changes,
        "by_tool": by_tool,
        "bounds": bounds,
    }


# ------------------------------------------------------------------ helpers


def check_text(text: str, name: str = "program", config: Optional[Config] = None, **kwargs) -> CheckResult:
    config = config or Config()
    t0 = time.perf_counter()
    program = parse_program(text, name, config)
    parse_time = time.perf_counter() - t0
    result = run_check(program, config, **kwargs)
    result.timing["parse"] = parse_time
    result.timing["total"] = time.perf_counter() - t0
    return result


def check_file(path, config: Optional[Config] = None, **kwargs) -> CheckResult:
    path = Path(path)
    if config is None:
        found = find_config(path)
        config = load_config(found) if found else Config()
    text = path.read_text(encoding="utf-8", errors="replace")
    return check_text(text, path.name, config, **kwargs)
