"""MCP server: collision checks and comment detection for a CAM pipeline.

    emucraft mcp          # stdio transport; needs `pip install emucraft[mcp]`

Programs are passed as a file ``path`` or inline ``gcode``. The configuration
for a call is, first found: ``config_path``, an emucraft.toml / .json next to
the program, then the server's settings file (edited with update_settings,
and an ordinary emucraft JSON config, so ``emucraft check -c`` can use it).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .check import check_text, parse_program
from .config import ConfigError, config_from_dict, find_config, load_config
from .gcode import DEFAULT_COMMENT_RULES, DEFAULT_FUZZ_THRESHOLD, DEFAULT_LABEL_ALIASES, DEFAULT_VARIABLE_RULES
from .kernel import KernelError
from .model import Config, StockSpec, resolve_stock, resolve_tools

server = MCPServer(
    "emucraft",
    instructions=(
        "Emucraft simulates 3-axis G-code against the stock, tool, shank and holder. Use check_program "
        "to find collisions (rapid or spindle-off cuts, holder, shank, table) before a program reaches "
        "the machine. Tool and stock sizes come from CAM comments; program_info shows what was read "
        "and from which line. When a value is missing or misread, tune detection with update_settings "
        "(comment_aliases, comment_rules, variable_rules, check.fuzz_threshold) or pass tools / stock "
        "to check_program."
    ),
)


def settings_path() -> Path:
    """The JSON config file the settings tools edit."""
    env = os.environ.get("EMUCRAFT_MCP_CONFIG")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "emucraft" / "mcp.json"


def _saved_settings() -> Dict[str, Any]:
    path = settings_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ToolError(f"{path} is not valid JSON ({exc}); fix it or call reset_settings") from None


def _load(path: Optional[str], gcode: Optional[str], config_path: Optional[str]) -> Tuple[str, str, Config, Optional[str]]:
    """(text, name, config, where the config came from) for a tool call."""
    if (path is None) == (gcode is None):
        raise ToolError("Pass exactly one of `path` or `gcode`")
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace") if path else gcode
        source = config_path or (find_config(path) if path else None)
        if source is None and settings_path().is_file():
            source = settings_path()
        config = load_config(source) if source else Config()
    except (OSError, ConfigError) as exc:
        raise ToolError(str(exc)) from None
    return text, Path(path).name if path else "program", config, None if source is None else str(source)


@server.tool()
def check_program(
    path: Optional[str] = None,
    gcode: Optional[str] = None,
    config_path: Optional[str] = None,
    stock: Optional[List[float]] = None,
    tools: Optional[Dict[str, Dict[str, Any]]] = None,
    resolution: Optional[float] = None,
) -> dict:
    """Simulate a G-code program and report collisions.

    status is "pass", "warn" or "fail". Each issue has a kind (rapid-cut, spindle-off-cut, holder,
    shank, table, link-cut, travel, tool), the 1-based source lines and the worst position/depth.
    messages lists what the interpreter could not simulate. tools shows the geometry used and
    where every value came from.

    stock      - [xmin, ymin, zmin, xmax, ymax, zmax] in program units, instead of the CAM header.
    tools      - per-tool overrides in program units, e.g. {"20": {"holder_diameter": 1.3,
                 "stickout": 1.4}} or {"*": {...}} for every tool. Fields: diameter, shape
                 (flat/ball/bull/cone), corner_radius, tip_angle, flute_length, shank_diameter,
                 stickout, holder_diameter, holder_length.
    resolution - simulation cell size in program units (default picks ~6 million cells).
    """
    text, name, config, source = _load(path, gcode, config_path)
    if stock is not None and len(stock) != 6:
        raise ToolError("stock needs 6 numbers: xmin, ymin, zmin, xmax, ymax, zmax")
    overrides: Dict[object, Dict[str, Any]] = {}
    for key, values in (tools or {}).items():
        try:
            overrides["*" if key == "*" else int(str(key).lstrip("Tt"))] = values
        except ValueError:
            raise ToolError(f"'{key}' is not a tool number (use \"20\", \"T20\" or \"*\")") from None
    try:
        result = check_text(
            text, name, config,
            overrides=overrides,
            stock=StockSpec(*map(float, stock), source="override") if stock else None,
            cell=resolution,
        )
    except (KernelError, ValueError) as exc:
        raise ToolError(str(exc)) from None
    return {**result.to_dict(), "config": source}


@server.tool()
def program_info(path: Optional[str] = None, gcode: Optional[str] = None, config_path: Optional[str] = None) -> dict:
    """Show the tool and stock data read from a program's comments and macro variables, without simulating.

    detections lists every value found with its 1-based line and that line's text; messages with
    code "comment-label" mark labels matched loosely (typos, variants). Pass a few comment lines as
    `gcode` to try out detection settings.
    """
    text, name, config, source = _load(path, gcode, config_path)
    program = parse_program(text, name, config)
    detections = [
        {"target": f"tool.{fld}", "tool": number, "value": value, "line": note.lines[fld] + 1,
         "text": program.lines[note.lines[fld]].strip()}
        for number, note in program.tool_notes.items()
        for fld, value in note.values.items()
    ] + [
        {"target": f"stock.{fld}", "value": value, "line": program.stock_lines[fld] + 1,
         "text": program.lines[program.stock_lines[fld]].strip()}
        for fld, value in program.stock_note.items()
    ]
    stock = resolve_stock(program, config)[0]
    return {
        "name": program.name,
        "units": program.units,
        "moves": program.n_moves,
        "tools_used": list(program.slot_tools),
        "detections": sorted(detections, key=lambda d: d["line"]),
        "stock": stock.to_dict() if stock else None,
        "tools": {str(k): v.to_dict() for k, v in resolve_tools(program, config).items()},
        "messages": [m.to_dict() for m in program.messages],
        "config": source,
    }


@server.tool()
def get_settings() -> dict:
    """Show the saved settings and the built-in detection defaults they adjust.

    comment_aliases - {target: [labels]}: "(label: value)" comments whose label is close to one of
                      these (case, punctuation and typos forgiven) set the target. Replaces the
                      default list for each target given.
    comment_rules   - [{pattern, field}] regexes on comment text; group 1 is the value. Run before
                      the defaults; aliases are only tried when no rule matched.
    variable_rules  - {"#127": "tool.holder_length"} macro variables that carry tool data.
    check.fuzz_threshold - 0-1 label similarity needed (default 0.85, 1 = exact labels only).
    """
    return {
        "settings_file": str(settings_path()),
        "settings": _saved_settings(),
        "defaults": {
            "comment_aliases": DEFAULT_LABEL_ALIASES,
            "comment_rules": [{"pattern": p, "field": f} for p, f in DEFAULT_COMMENT_RULES],
            "variable_rules": {f"#{k}": v for k, v in DEFAULT_VARIABLE_RULES.items()},
            "fuzz_threshold": DEFAULT_FUZZ_THRESHOLD,
        },
    }


@server.tool()
def update_settings(changes: Dict[str, Any]) -> dict:
    """Change the saved settings (an emucraft JSON config) used when no other config applies.

    Top-level keys: comment_aliases, comment_rules, variable_rules, check, machine, stock, tools,
    default_tool, units. Tables (comment_aliases, variable_rules, check, machine, tools,
    default_tool) are merged key by key; anything else is replaced. A null value removes a key.
    Lengths are in `units` ("inch" or "mm"; default: the program's units). Example:
    {"comment_aliases": {"tool.holder_length": ["holder length", "gauge from holder"]},
     "check": {"fuzz_threshold": 0.9}, "default_tool": {"holder_diameter": 1.5}}
    The result is validated before it is saved; see get_settings for the detection defaults.
    """
    settings = _saved_settings()
    for key, value in changes.items():
        if value is None:
            settings.pop(key, None)
        elif isinstance(value, dict) and key != "stock":
            merged = {**settings.get(key, {}), **value}
            settings[key] = {k: v for k, v in merged.items() if v is not None}
        else:
            settings[key] = value
    try:
        config_from_dict(settings)
    except ConfigError as exc:
        raise ToolError(f"Settings not saved: {exc}") from None
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return {"settings_file": str(path), "settings": settings}


@server.tool()
def reset_settings() -> dict:
    """Delete the saved settings, going back to the built-in defaults."""
    path = settings_path()
    if path.is_file():
        path.unlink()
    return {"settings_file": str(path), "settings": {}}
