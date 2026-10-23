"""Human-readable, JSON and JUnit renderings of a check result."""

from __future__ import annotations

import json
from typing import Iterable, List
from xml.sax.saxutils import escape, quoteattr

from .check import CheckResult

_MARK = {"error": "x", "warning": "!", "info": "-"}


def _len(value: float, units: str) -> str:
    return f"{value:.4f}" if units == "inch" else f"{value:.3f}"


def _clock(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _lines(first: int, last: int) -> str:
    return f"line {first}" if first == last else f"lines {first}-{last}"


def format_text(result: CheckResult, verbose: bool = False) -> str:
    p = result.program
    u = p.units
    unit = "in" if u == "inch" else "mm"
    out: List[str] = []
    out.append(f"{p.name}: {u}, {len(p.lines)} lines, {p.n_moves} moves")
    if result.stock:
        s = result.stock
        out.append(
            f"  stock  X{_len(s.xmin, u)}..{_len(s.xmax, u)}  Y{_len(s.ymin, u)}..{_len(s.ymax, u)}  "
            f"Z{_len(s.zmin, u)}..{_len(s.zmax, u)}  ({s.source})")
    if result.grid:
        g = result.grid
        out.append(f"  grid   {g.nx} x {g.ny} cells of {g.cell:g} {unit}")
    for number, t in result.tools.items():
        parts = [f"T{number}", t.shape, f"D{_len(t.diameter, u)}" if t.diameter else "D?"]
        if t.shape == "bull":
            parts.append(f"R{_len(t.corner_radius, u)}")
        if t.flute_length:
            parts.append(f"flutes {_len(t.flute_length, u)}")
        if t.stickout:
            parts.append(f"stick-out {_len(t.stickout, u)}")
        if t.holder_diameter and t.stickout:
            parts.append(f"holder D{_len(t.holder_diameter, u)}")
        else:
            parts.append("holder not checked (needs holder_diameter and stickout)")
        out.append("  " + "  ".join(parts))
    out.append("")
    out.append(f"{result.status.upper()}  {result.summary()}")
    for issue in result.issues:
        x, y, z = issue.position
        out.append(
            f"  {_MARK[issue.severity]} {_lines(issue.first_line + 1, issue.last_line + 1):<16} "
            f"{issue.kind:<16} {issue.message}  at X{_len(x, u)} Y{_len(y, u)} Z{_len(z, u)}")
    shown = [m for m in result.messages if verbose or m.level != "info"]
    if shown:
        out.append("")
        out.append("Program messages:")
        for m in shown:
            where = f"line {m.line + 1}" if m.line is not None else "program"
            times = f" (x{m.count})" if m.count > 1 else ""
            out.append(f"  {_MARK.get(m.level, '-')} {where:<12} {m.text}{times}")
    st = result.stats
    out.append("")
    out.append(
        f"Cycle time {_clock(st['cycle_seconds'])} (cutting {_clock(st['cutting_seconds'])}, "
        f"rapids {_clock(st['rapid_seconds'])}, dwell {_clock(st['dwell_seconds'])}, "
        f"{st['tool_changes']} tool change{'s' if st['tool_changes'] != 1 else ''})")
    if "stock_volume" in st:
        out.append(f"Removed {st['volume_removed']:.4f} of {st['stock_volume']:.4f} {unit}^3 of stock")
    t = result.timing
    parts = [f"{k} {v:.2f}s" for k, v in t.items() if k != "total"]
    out.append(f"Checked in {t.get('total', t.get('check', 0)):.2f}s ({', '.join(parts)})")
    return "\n".join(out)


def to_json(result: CheckResult) -> str:
    return json.dumps(result.to_dict(), indent=2)


def junit_xml(results: Iterable[CheckResult]) -> str:
    """One test suite per program, one test case per check category."""
    suites = []
    total_tests = total_failures = 0
    for r in results:
        p = r.program
        cases = []
        categories = [
            ("collisions", [i for i in r.issues if i.severity == "error"]),
            ("warnings", [i for i in r.issues if i.severity == "warning"]),
            ("program", [m for m in r.messages if m.level == "error"]),
        ]
        failures = 0
        for name, items in categories:
            body = ""
            if items:
                failures += 1
                lines = []
                for item in items:
                    if hasattr(item, "kind"):
                        lines.append(f"{_lines(item.first_line + 1, item.last_line + 1)}: {item.message}")
                    else:
                        where = f"line {item.line + 1}: " if item.line is not None else ""
                        lines.append(f"{where}{item.text}")
                text = "\n".join(lines)
                body = (f"<failure message={quoteattr(f'{len(items)} {name}')} type={quoteattr(name)}>"
                        f"{escape(text)}</failure>")
            cases.append(f'    <testcase classname={quoteattr(p.name)} name={quoteattr(name)} '
                         f'time="0">{body}</testcase>')
        total_tests += len(categories)
        total_failures += failures
        time_s = r.timing.get("total", r.timing.get("check", 0.0))
        suites.append(
            f'  <testsuite name={quoteattr(p.name)} tests="{len(categories)}" failures="{failures}" '
            f'time="{time_s:.3f}">\n' + "\n".join(cases) + "\n  </testsuite>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<testsuites name="emucraft" tests="{total_tests}" failures="{total_failures}">\n'
            + "\n".join(suites) + "\n</testsuites>\n")
