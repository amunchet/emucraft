"""Command line interface.

    emucraft check part.nc               # collision check, exit 1 on collisions
    emucraft check *.nc --junit out.xml  # for CI
    emucraft optimize part.nc -o part.opt.nc --material titanium
    emucraft serve                       # web viewer on http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields
from pathlib import Path
from typing import Dict, List, Optional

from . import __version__
from .config import ConfigError, find_config, load_config
from .model import Config, StockSpec

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2


def _floats(text: str, count: int, what: str) -> List[float]:
    try:
        values = [float(v) for v in text.replace(" ", "").split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError(f"{what}: expected {count} comma-separated numbers") from None
    if len(values) != count:
        raise argparse.ArgumentTypeError(f"{what}: expected {count} comma-separated numbers")
    return values


def parse_tool_override(text: str) -> tuple:
    """'20:holder_diameter=1.5,stickout=1.4' or '*:holder_diameter=2'."""
    if ":" not in text:
        raise argparse.ArgumentTypeError("--tool expects T:field=value[,field=value...]")
    head, body = text.split(":", 1)
    head = head.strip().lstrip("Tt")
    key: object = "*" if head == "*" else None
    if key is None:
        try:
            key = int(head)
        except ValueError:
            raise argparse.ArgumentTypeError(f"--tool: bad tool number '{head}'") from None
    from .model import SHAPES, ToolSpec

    known = {f.name for f in fields(ToolSpec)} - {"number", "source"} | {"kind"}
    values: Dict[str, object] = {}
    for part in body.split(","):
        if not part.strip():
            continue
        if "=" not in part:
            raise argparse.ArgumentTypeError(f"--tool: expected field=value, got '{part}'")
        name, value = (s.strip() for s in part.split("=", 1))
        if name not in known:
            raise argparse.ArgumentTypeError(f"--tool: unknown field '{name}' (one of {', '.join(sorted(known))})")
        if name == "shape" and value not in SHAPES:
            raise argparse.ArgumentTypeError(f"--tool: shape must be one of {', '.join(SHAPES)}")
        if name in ("shape", "kind", "name"):
            values[name] = value
        else:
            try:
                values[name] = float(value)
            except ValueError:
                raise argparse.ArgumentTypeError(f"--tool: {name} needs a number") from None
    return key, values


def _add_setup_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("-c", "--config", help="configuration file (default: emucraft.toml next to the program)")
    p.add_argument("--stock", type=lambda t: _floats(t, 6, "--stock"),
                   metavar="XMIN,YMIN,ZMIN,XMAX,YMAX,ZMAX", help="stock box in program units")
    p.add_argument("--resolution", type=float, help="simulation cell size in program units")
    p.add_argument("--tool", action="append", type=parse_tool_override, default=[],
                   metavar="T:FIELD=VALUE,...", help="override tool data, e.g. 20:holder_diameter=1.5")
    p.add_argument("--rapid-mode", choices=("linear", "dogleg"), help="how rapids move")
    p.add_argument("--threads", type=int, help="worker threads (default: CPU count, max 8)")


def _config_for(path: Path, args) -> Config:
    if args.config:
        config = load_config(args.config)
    else:
        found = find_config(path)
        config = load_config(found) if found else Config()
    config = config.copy()
    if args.rapid_mode:
        config.machine.rapid_mode = args.rapid_mode
    return config


def _overrides(args) -> Dict[object, Dict[str, object]]:
    out: Dict[object, Dict[str, object]] = {}
    for key, values in args.tool:
        out.setdefault(key, {}).update(values)
    return out


def _stock(args) -> Optional[StockSpec]:
    if not args.stock:
        return None
    v = args.stock
    return StockSpec(v[0], v[1], v[2], v[3], v[4], v[5], source="command line")


def cmd_check(args) -> int:
    from .check import check_text
    from .report import format_text, junit_xml

    results = []
    status = EXIT_OK
    # with --json - stdout carries only JSON; the human report goes to stderr
    report_to = sys.stderr if args.json == "-" else sys.stdout
    previous = None
    for name in args.programs:
        path = Path(name)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            config = _config_for(path, args)
        except (OSError, ConfigError) as exc:
            print(f"emucraft: {exc}", file=sys.stderr)
            return EXIT_USAGE
        try:
            result = check_text(text, path.name, config, overrides=_overrides(args), stock=_stock(args),
                                cell=args.resolution, table_z=args.table_z, link_feed=args.link_feed,
                                threads=args.threads, keep_sim=args.chain, previous=previous,
                                keep_initial=args.chain and bool(args.html))
        except ValueError as exc:  # e.g. chained programs in different units
            print(f"emucraft: {path.name}: {exc}", file=sys.stderr)
            return EXIT_USAGE
        if previous is not None:
            previous.initial_heights = None
        if args.chain:
            previous = result
        results.append(result)
        if not args.quiet:
            print(format_text(result, verbose=args.verbose), file=report_to)
            print(file=report_to)
        if result.status == "fail" or (args.strict and result.status == "warn"):
            status = EXIT_FAIL
        if args.html:
            from .html import write_report

            target = Path(args.html)
            if len(args.programs) > 1:
                target = target.with_name(f"{target.stem}-{path.stem}{target.suffix or '.html'}")
            write_report(result, target)
            if not args.quiet:
                print(f"3D report: {target}", file=report_to)
    if previous is not None and previous.sim is not None:
        previous.sim.close()
    if args.json:
        payload = [r.to_dict() for r in results]
        text = json.dumps(payload[0] if len(payload) == 1 else payload, indent=2)
        if args.json == "-":
            print(text)
        else:
            Path(args.json).write_text(text + "\n", encoding="utf-8")
    if args.junit:
        Path(args.junit).write_text(junit_xml(results), encoding="utf-8")
    return status


def cmd_optimize(args) -> int:
    from .check import parse_program
    from .optimize import OptimizeSpec, optimize

    path = Path(args.program)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        config = _config_for(path, args)
    except (OSError, ConfigError) as exc:
        print(f"emucraft: {exc}", file=sys.stderr)
        return EXIT_USAGE
    options = dict(config.optimize)
    for name in ("material", "corner_angle", "corner_factor", "corner_zone", "load_limit",
                 "min_factor", "air_factor", "max_feed"):
        value = getattr(args, name)
        if value is not None:
            options[name] = value
    if args.no_load:
        options["load"] = False
    try:
        spec = OptimizeSpec.from_dict(options)
    except (TypeError, ValueError) as exc:
        print(f"emucraft: {exc}", file=sys.stderr)
        return EXIT_USAGE
    program = parse_program(text, path.name, config)
    if args.resolution:
        spec.resolution = args.resolution
    result = optimize(program, config, spec, overrides=_overrides(args), stock=_stock(args),
                      threads=args.threads)
    for m in result.messages:
        print(f"{m.level}: {m.text}", file=sys.stderr)
    if not result.verified:
        print("emucraft: optimization failed; nothing written", file=sys.stderr)
        return EXIT_FAIL
    out = Path(args.output) if args.output else path.with_name(f"{path.stem}.opt{path.suffix}")
    out.write_text(result.text, encoding="utf-8")
    st = result.stats
    before, after = st["cutting_seconds_before"], st["cutting_seconds_after"]
    change = (after - before) / before * 100 if before else 0.0
    print(f"{out}: {st['corners']} corners, {st['pieces_slowed']} of {st['pieces']} cutting pieces slowed, "
          f"{st['lines_changed']} lines changed, {st['lines_added']} added")
    print(f"cutting time {before / 60:.1f} min -> {after / 60:.1f} min ({change:+.1f}%)")
    if args.json:
        Path(args.json).write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")
    return EXIT_OK


def cmd_info(args) -> int:
    from .check import parse_program
    from .model import resolve_stock, resolve_tools

    path = Path(args.program)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        config = _config_for(path, args)
    except (OSError, ConfigError) as exc:
        print(f"emucraft: {exc}", file=sys.stderr)
        return EXIT_USAGE
    program = parse_program(text, path.name, config)
    tools = resolve_tools(program, config, _overrides(args))
    stock = _stock(args) or resolve_stock(program, config)[0]
    info = {
        "name": program.name,
        "units": program.units,
        "lines": len(program.lines),
        "moves": program.n_moves,
        "stock": stock.to_dict() if stock else None,
        "tools": {str(k): v.to_dict() for k, v in tools.items()},
        "messages": [m.to_dict() for m in program.messages],
    }
    print(json.dumps(info, indent=2))
    return EXIT_OK


def cmd_serve(args) -> int:
    from .server import serve

    config = load_config(args.config) if args.config else None
    return serve(args.host, args.port, programs=args.programs, config=config, open_browser=args.open)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="emucraft", description="G-code simulation, collision checking "
                                     "and feed optimization")
    parser.add_argument("--version", action="version", version=f"emucraft {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="simulate programs and report collisions")
    p.add_argument("programs", nargs="+", help="G-code files")
    _add_setup_options(p)
    p.add_argument("--table-z", type=float, help="lowest Z the tip may reach (default: stock bottom)")
    p.add_argument("--link-feed", type=float, help="feed moves at or above this feed must not cut")
    p.add_argument("--json", metavar="FILE", help="write the JSON report ('-' for stdout)")
    p.add_argument("--junit", metavar="FILE", help="write a JUnit XML report")
    p.add_argument("--html", metavar="FILE", help="write a self-contained 3D report")
    p.add_argument("--chain", action="store_true",
                   help="operations of one job: each program starts from the stock the previous one left")
    p.add_argument("--strict", action="store_true", help="fail on warnings too")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true", help="also list info messages")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("optimize", help="rewrite feeds for corners and tool load")
    p.add_argument("program")
    p.add_argument("-o", "--output", help="output file (default: <name>.opt<ext>)")
    _add_setup_options(p)
    p.add_argument("--material", help="preset: aluminum, steel, stainless, titanium, inconel")
    p.add_argument("--corner-angle", type=float, help="direction change that counts as a corner (deg)")
    p.add_argument("--corner-factor", type=float, help="feed factor at sharp corners (0-1)")
    p.add_argument("--corner-zone", type=float, help="slow zone around corners, in tool diameters")
    p.add_argument("--load-limit", type=float, help="allowed removal rate vs. the nominal cut")
    p.add_argument("--min-factor", type=float, help="lowest feed factor")
    p.add_argument("--air-factor", type=float, help="feed factor for moves that cut nothing")
    p.add_argument("--max-feed", type=float, help="cap for new feeds")
    p.add_argument("--no-load", action="store_true", help="corners only, no load-based slow-down")
    p.add_argument("--json", metavar="FILE", help="write optimization details as JSON")
    p.set_defaults(func=cmd_optimize)

    p = sub.add_parser("info", help="show what the program says about tools and stock")
    p.add_argument("program")
    p.add_argument("-c", "--config")
    p.add_argument("--stock", type=lambda t: _floats(t, 6, "--stock"))
    p.add_argument("--resolution", type=float)
    p.add_argument("--tool", action="append", type=parse_tool_override, default=[])
    p.add_argument("--rapid-mode", choices=("linear", "dogleg"))
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("serve", help="run the web viewer")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--programs", help="folder of programs to list in the viewer")
    p.add_argument("-c", "--config", help="configuration file")
    p.add_argument("--open", action="store_true", help="open a browser")
    p.set_defaults(func=cmd_serve)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    from .kernel import KernelError

    try:
        return args.func(args)
    except (ConfigError, KernelError) as exc:
        print(f"emucraft: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
