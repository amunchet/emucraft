"""MCP server: collision checks and comment detection for a CAM pipeline.

    emucraft mcp                                   # stdio transport; needs `pip install emucraft[mcp]`
    emucraft mcp --http --port 8000 --programs DIR  # streamable HTTP at /mcp, plus GET /health

Programs are passed as a file ``path`` or inline ``gcode``. With a programs folder, ``path`` is relative
to it and cannot leave it, and list_programs shows what is there; served over HTTP without one, only
``gcode`` is accepted, so callers cannot read the server's files. With $EMUCRAFT_TOKEN set, every HTTP
request to /mcp needs ``Authorization: Bearer <token>``. The configuration
for a call is, first found: ``config_path``, an emucraft.toml / .json next to
the program, then the server's settings file (edited with update_settings,
and an ordinary emucraft JSON config, so ``emucraft check -c`` can use it).

Fixture models and setups live in the data folder ($EMUCRAFT_DATA), shared with the web viewer: PUT
/fixtures/<name> takes a raw STL too big for a tool call. check_program saves each check there as well;
$EMUCRAFT_URL, the viewer's public address, turns that into a link for the user.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .check import check_text, parse_program
from .config import ConfigError, config_from_dict, find_config, fixture_dict, load_config
from .fixture import (
    choose_fixtures,
    delete_model,
    delete_setup,
    list_models,
    list_setups,
    read_setup,
    save_model,
    save_setup,
)
from .gcode import DEFAULT_COMMENT_RULES, DEFAULT_FUZZ_THRESHOLD, DEFAULT_LABEL_ALIASES, DEFAULT_VARIABLE_RULES
from .kernel import KernelError
from .model import Config, FixtureSpec, StockSpec, fixture_dir, resolve_stock, resolve_tools
from .payload import save_simulation
from .server import MAX_BODY, PROGRAM_SUFFIXES

# Where `path` and `config_path` may point: anywhere (None), or inside this folder. Set by serve().
programs: Optional[Path] = None
remote = False  # served over HTTP: with no programs folder, `path` is refused

server = MCPServer(
    "emucraft",
    instructions=(
        "Emucraft simulates 3-axis G-code against the stock, fixtures, tool, shank and holder. Use check_program "
        "to find collisions (rapid or spindle-off cuts, holder, shank, fixture, table) and whether the holder "
        "would hit the stock a broken tool leaves uncut (broken-tool warnings) before a program reaches "
        "the machine. Tool and stock sizes come from CAM comments; program_info shows what was read "
        "and from which line. When a value is missing or misread, tune detection with update_settings "
        "(comment_aliases, comment_rules, variable_rules, check.fuzz_threshold) or pass tools / stock "
        "to check_program. Pass workholding (vise or toe clamps, part material, torque) to check_program "
        "to learn whether the cutting forces can move the part and what torque holds it. For fixtures, "
        "upload_fixture keeps an STL in the library (list_fixtures shows it); place it with check_program's "
        "fixtures, and keep the placement with save_fixture_setup. G-code does not say what holds the part: "
        "a check uses the fixtures you pass, then the saved setup named by a (FIXTURE: name) comment, then "
        "the saved setup named after the program, then the config's. Each check_program result has "
        "simulation.url, a page where the user can watch that check in 3D with its fixtures: give it to them."
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


def _file(path: Optional[str]) -> Optional[str]:
    """A `path` or `config_path` argument, kept inside the programs folder when there is one."""
    if path is None or (programs is None and not remote):
        return path
    if programs is None:
        raise ToolError("This server has no programs folder: pass the program as `gcode`")
    file = (programs / path).resolve()
    if not file.is_relative_to(programs):
        raise ToolError(f"{path} is outside the programs folder")
    return str(file)


def _guard_models(specs: List[FixtureSpec]) -> None:
    """Fixture models are files too: keep them inside the library or the programs folder when there is one."""
    if programs is None and not remote:
        return
    roots = [fixture_dir()] + ([programs] if programs else [])
    for fx in specs:
        if fx.model and not any(Path(fx.model).is_relative_to(root) for root in roots):
            raise ToolError(f"fixture model {fx.model} is outside the programs folder")


def _specs(entries: List[Dict[str, Any]]) -> List[FixtureSpec]:
    """Fixtures named in a tool call: relative models are in the programs folder, then the library."""
    try:
        specs = config_from_dict({"fixtures": entries}, programs).fixtures
    except ConfigError as exc:
        raise ToolError(str(exc)) from None
    _guard_models(specs)
    return specs


def _load(path: Optional[str], gcode: Optional[str], config_path: Optional[str],
          name: Optional[str] = None) -> Tuple[str, str, Config, Optional[str]]:
    """(text, name, config, where the config came from) for a tool call."""
    if (path is None) == (gcode is None):
        raise ToolError("Pass exactly one of `path` or `gcode`")
    path, config_path = _file(path), _file(config_path)
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace") if path else gcode
        source = config_path or (find_config(path) if path else None)
        if source is None and settings_path().is_file():
            source = settings_path()
        config = load_config(source) if source else Config()
    except (OSError, ConfigError) as exc:
        raise ToolError(str(exc)) from None
    _guard_models(config.fixtures)
    return text, name or (Path(path).name if path else "program"), config, None if source is None else str(source)


@server.tool()
def list_programs() -> dict:
    """The programs in the server's programs folder, as `path` values for the other tools."""
    if programs is None:
        raise ToolError("This server has no programs folder")
    return {
        "folder": str(programs),
        "programs": sorted(str(f.relative_to(programs)) for f in programs.rglob("*")
                           if f.is_file() and f.suffix.lower() in PROGRAM_SUFFIXES),
    }


@server.tool()
def check_program(
    path: Optional[str] = None,
    gcode: Optional[str] = None,
    config_path: Optional[str] = None,
    stock: Optional[List[float]] = None,
    tools: Optional[Dict[str, Dict[str, Any]]] = None,
    resolution: Optional[float] = None,
    workholding: Optional[Dict[str, Any]] = None,
    fixtures: Optional[List[Dict[str, Any]]] = None,
    fixture_setup: Optional[str] = None,
    name: Optional[str] = None,
) -> dict:
    """Simulate a G-code program and report collisions, and whether the vise or clamps hold the part.

    status is "pass", "warn" or "fail". Each issue has a kind (rapid-cut, spindle-off-cut, holder,
    broken-tool, shank, fixture, table, link-cut, travel, tool, workholding), the 1-based source lines
    and the worst position/depth. A broken-tool issue is a warning: had the tool snapped as its run
    started, the holder would hit the stock it left uncut (needs holder_diameter and stickout; turn it
    off with check.broken_tool in update_settings). messages lists what the interpreter could not
    simulate. tools shows the geometry used and where every value came from. fixtures shows each one
    as placed (bounds, and spec: an entry to pass back with `at` changed); fixture_source says why it
    was used. simulation.url opens this check, fixtures included, in the 3D viewer: give it to the
    user. It is null when the server does not know the viewer's address ($EMUCRAFT_URL).

    stock       - [xmin, ymin, zmin, xmax, ymax, zmax] in program units, instead of the CAM header.
    tools       - per-tool overrides in program units, e.g. {"20": {"holder_diameter": 1.3,
                  "stickout": 1.4}} or {"*": {...}} for every tool. Fields: diameter, shape
                  (flat/ball/bull/cone), corner_radius, tip_angle, flute_length, shank_diameter,
                  stickout, holder_diameter, holder_length, flutes.
    resolution  - simulation cell size in program units (default picks ~6 million cells).
    workholding - fixture and cutting-force options laid over the config's [workholding], in
                  program units, with forces and torques in lbf and ft-lbf for inch programs (N and
                  N-m for mm). The cutting load of every cut, times wear_factor, must be held
                  safety_factor times over. The result's "workholding" gives the margin at the given
                  torque, the least torque that holds every cut (required_torque), and the weakest
                  cut. A Kurt vise: {"fixture": "vise", "vise": "d688", "material": "steel", "grip": 0.5,
                  "torque": 40} (d688, dx6, 3600v, hd690, hdl6, scd640 self-centering); another
                  vise: force_per_torque and max_torque instead of vise. Toe clamps: {"fixture": "clamps",
                  "material": "aluminum", "clamps": [[0.5, 0.5], [3.5, 0.5]], "bolt": 0.625,
                  "torque": 60}. Other options: safety_factor (default 2), wear_factor (1.5),
                  friction (0.15), jaw_width, axis ("y": the jaws close along Y), center, lift_stop
                  (a dovetail or step holds the part down), force (instead of torque), nut_factor,
                  bolt_grade, lever, kc1 and mc (a material of your own), radial_ratio, axial_ratio.
    fixtures    - solids the tool must not touch, for this call, instead of any saved or configured
                  ones ([] for none). Each is a box {"name": "toe clamp", "min": [x, y, z], "max": [x,
                  y, z]} or {"name": "vise", "model": "kurt.stl"} (from list_fixtures, or a file in the
                  programs folder), placed with "at": [x, y, z], where the center of its bounding box
                  goes. Optional: rotate (degrees about Z, or [x, y, z], about its center), scale,
                  units ("inch" or "mm", the model's lengths: an STL does not say) and position_units
                  (those of at). Lengths default to the config's units, or program units when it
                  sets none. Without at, offset [x, y, z] moves the model's own origin instead.
    fixture_setup - the saved setup to use (list_fixtures), instead of fixtures.
    name        - a file name for inline gcode: it titles the simulation, and a setup saved under
                  its stem applies (name "bracket.nc" uses setup "bracket").
    """
    if fixtures is not None and fixture_setup is not None:
        raise ToolError("Pass fixtures or fixture_setup, not both")
    text, name, config, source = _load(path, gcode, config_path, name)
    if stock is not None and len(stock) != 6:
        raise ToolError("stock needs 6 numbers: xmin, ymin, zmin, xmax, ymax, zmax")
    overrides: Dict[object, Dict[str, Any]] = {}
    for key, values in (tools or {}).items():
        try:
            overrides["*" if key == "*" else int(str(key).lstrip("Tt"))] = values
        except ValueError:
            raise ToolError(f"'{key}' is not a tool number (use \"20\", \"T20\" or \"*\")") from None
    if fixture_setup is not None:
        fixtures = read_setup(fixture_setup)
        if fixtures is None:
            raise ToolError(f"No fixture setup named {fixture_setup}: see list_fixtures")
    specs = None if fixtures is None else _specs(fixtures)
    try:
        result = check_text(
            text, name, config,
            overrides=overrides,
            stock=StockSpec(*map(float, stock), source="override") if stock else None,
            cell=resolution,
            workholding=workholding,
            fixtures=specs,
        )
    except (KernelError, ValueError) as exc:
        raise ToolError(str(exc)) from None
    # what the viewer needs to re-check it: the same keys as its own settings
    settings = {"stock": stock, "tools": tools, "resolution": resolution, "workholding": workholding,
                "fixtures": None if specs is None else [fixture_dict(s) for s in specs]}
    ident = save_simulation(result, {k: v for k, v in settings.items() if v is not None})
    base = os.environ.get("EMUCRAFT_URL") or (None if remote else "http://127.0.0.1:8765")
    return {**result.to_dict(), "config": source,
            "simulation": {"id": ident, "url": f"{base.rstrip('/')}/?sim={ident}" if base else None}}


@server.tool()
def program_info(path: Optional[str] = None, gcode: Optional[str] = None, config_path: Optional[str] = None,
                 name: Optional[str] = None) -> dict:
    """Show the tool, stock and fixture data read from a program's comments and macro variables, without simulating.

    detections lists every value found with its 1-based line and that line's text; messages with
    code "comment-label" mark labels matched loosely (typos, variants). fixtures names what a check
    of it would use and fixture_source why. Pass a few comment lines as `gcode` to try out detection
    settings; `name` is a file name for inline gcode, as for check_program.
    """
    text, name, config, source = _load(path, gcode, config_path, name)
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
    if program.fixture_note:
        detections.append({"target": "program.fixture", "value": program.fixture_note,
                           "line": program.fixture_line + 1, "text": program.lines[program.fixture_line].strip()})
    stock = resolve_stock(program, config)[0]
    specs, fixture_source, notes = choose_fixtures(program, config)
    return {
        "name": program.name,
        "units": program.units,
        "moves": program.n_moves,
        "tools_used": list(program.slot_tools),
        "detections": sorted(detections, key=lambda d: d["line"]),
        "stock": stock.to_dict() if stock else None,
        "tools": {str(k): v.to_dict() for k, v in resolve_tools(program, config).items()},
        "fixtures": [f.label for f in specs],
        "fixture_source": fixture_source,
        "messages": [m.to_dict() for m in program.messages + notes],
        "config": source,
    }


@server.tool()
def get_settings() -> dict:
    """Show the saved settings and the built-in detection defaults they adjust.

    comment_aliases - {target: [labels]}: "(label: value)" comments whose label is close to one of
                      these (case, punctuation and typos forgiven) set the target. Replaces the
                      default list for each target given.
    comment_rules   - [{pattern, field}] regexes on comment text; group 1 is the value. Run before
                      the defaults; aliases are only tried when no rule matched. The field
                      program.fixture takes the name of a saved fixture setup, so a program can
                      say what holds it in your shop's own words, like (VISE: kurt).
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

    Top-level keys: comment_aliases, comment_rules, variable_rules, check, machine, stock, fixtures,
    tools, default_tool, workholding, units. check.broken_tool false skips the broken-tool warnings.
    fixtures is a list of {name, model (from list_fixtures, or an STL path relative to the programs
    folder if the server has one, else to the settings file) or min/max box, units, scale, rotate,
    at, position_units}, as for check_program; they apply to every later call that finds no saved
    setup: for one program, pass fixtures to check_program or use save_fixture_setup.
    Tables (comment_aliases, variable_rules, check, machine, tools, default_tool, workholding) are
    merged key by key; anything else is replaced. A null value removes a key. workholding takes the
    options of check_program's workholding, in `units`.
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
    if programs is not None:  # the settings file lives elsewhere, as /data does in Docker
        for fx in settings.get("fixtures") or []:
            if isinstance(fx, dict) and isinstance(fx.get("model"), str) and (programs / fx["model"]).is_file():
                fx["model"] = str((programs / fx["model"]).resolve())
    try:
        _guard_models(config_from_dict(settings, settings_path().parent).fixtures)
    except (ConfigError, ToolError) as exc:
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


@server.tool()
def list_fixtures() -> dict:
    """The fixture library, shared with the web viewer's Setup tab.

    models - uploaded STL files: name (use it as a fixture's "model"), bytes, triangles and bounds, in
             the file's own units. An STL does not say what they are, so read them to pick "units".
    setups - saved setups: name and fixtures, entries for check_program's fixtures.
    """
    return {"models": list_models(), "setups": list_setups()}


@server.tool()
def upload_fixture(name: str, data: str, replace: bool = False) -> dict:
    """Keep an STL file in the fixture library, under a cleaned-up `name`; returns it as list_fixtures does.

    data is the file in base64. A file too big for a tool call goes up as the raw bytes instead:
    PUT /fixtures/<name> on this server with the same bearer token (?replace=1 to overwrite). The
    viewer's Setup tab uploads too. replace overwrites a model already there, which also changes
    every setup that uses it.
    """
    try:
        return save_model(name, base64.b64decode(data, validate=True), replace)
    except (binascii.Error, FileExistsError, ValueError) as exc:
        raise ToolError(str(exc)) from None


@server.tool()
def delete_fixture(name: str) -> dict:
    """Remove a model from the fixture library. Setups that use it stop working until it is back."""
    try:
        delete_model(name)
    except (FileNotFoundError, ValueError):
        raise ToolError(f"No fixture model named {name}") from None
    return {"deleted": name}


@server.tool()
def save_fixture_setup(name: str, fixtures: List[Dict[str, Any]]) -> dict:
    """Keep fixtures as placed under a name, so checks find them without passing fixtures again.

    fixtures is a list as for check_program. A program uses the setup when it has a (FIXTURE: name)
    comment or is called name.nc; any other program can ask for it with fixture_setup. Give each
    entry "units" and "position_units" so that it reads the same for inch and mm programs. Saving
    under a name that is taken replaces that setup: the viewer's Setup tab edits the same ones.
    """
    try:
        return save_setup(name, _specs(fixtures))
    except ValueError as exc:
        raise ToolError(str(exc)) from None


@server.tool()
def delete_fixture_setup(name: str) -> dict:
    """Remove a saved fixture setup."""
    try:
        delete_setup(name)
    except FileNotFoundError:
        raise ToolError(f"No fixture setup named {name}") from None
    return {"deleted": name}


@server.custom_route("/health", methods=["GET"])
async def health(request):
    """Liveness, for the container's health check."""
    from starlette.responses import JSONResponse

    return JSONResponse({"status": "ok"})


@server.custom_route("/fixtures/{name}", methods=["PUT"])
async def put_fixture(request):
    """Upload a fixture model as the raw request body."""
    from starlette.responses import JSONResponse

    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        return JSONResponse({"error": "request too large"}, status_code=413)
    try:
        return JSONResponse(save_model(request.path_params["name"], await request.body(),
                                       request.query_params.get("replace") == "1"))
    except FileExistsError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


def guarded(app):
    """The HTTP app behind $EMUCRAFT_TOKEN, when it is set."""
    token = os.environ.get("EMUCRAFT_TOKEN")

    async def wrapper(scope, receive, send):
        if token and scope["type"] == "http" and scope["path"].startswith(("/mcp", "/fixtures")):
            from starlette.responses import JSONResponse

            headers = dict(scope["headers"])
            if not secrets.compare_digest(headers.get(b"authorization", b"").decode(), f"Bearer {token}"):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await app(scope, receive, send)

    return wrapper


def serve(http: bool = False, host: str = "127.0.0.1", port: int = 8000, folder: Optional[str] = None) -> None:
    """Run the server over stdio, or streamable HTTP at /mcp."""
    global programs, remote
    if folder:
        programs = Path(folder).resolve()
        if not programs.is_dir():
            raise SystemExit(f"emucraft: {folder} is not a folder")
    remote = http
    if not http:
        server.run()
        return
    import uvicorn

    # On localhost the SDK guards against DNS rebinding; deployed, Caddy or the token is the guard.
    uvicorn.run(guarded(server.streamable_http_app(host=host)), host=host, port=port)
