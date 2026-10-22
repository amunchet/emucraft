#!/usr/bin/env python3
"""
Emucraft MCP server

Exposes the G-code parser, comment detection settings and the C kernel so a
generative CAM pipeline can check a program before it reaches the machine.

Run: python3 mcp_server.py   (stdio transport)
"""
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from collections import Counter

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "gcode"))

import gcode_parser  # noqa: E402

SETTINGS_PATH = os.environ.get("EMUCRAFT_DETECTION", os.path.join(ROOT, "detection.json"))
KERNEL_SRC = os.path.join(ROOT, "kernel", "src")
KERNEL_BIN = os.path.join(ROOT, "kernel", "build", "emukernel")

server = MCPServer(
    "emucraft",
    instructions=(
        "Emucraft checks G-code (3 axis, G17, G90) for collisions. Tooling and stock sizes are read "
        "from program comments; use analyze_program to see what was detected and test_detection / "
        "update_detection_settings to tune how comments are recognized. simulate runs the C kernel "
        "and reports material removed by rapid or spindle-off moves."
    ),
)


def load_settings():
    """
    Saved detection settings layered over the defaults (so new default fields still apply)
    """
    defaults = gcode_parser.DEFAULT_DETECTION
    if not os.path.exists(SETTINGS_PATH):
        return json.loads(json.dumps(defaults))

    with open(SETTINGS_PATH) as f:
        saved = json.load(f)
    return {
        "fuzz_threshold": saved.get("fuzz_threshold", defaults["fuzz_threshold"]),
        "units": saved.get("units", defaults["units"]),
        "labels": {**defaults["labels"], **saved.get("labels", {})},
        "patterns": {**defaults["patterns"], **saved.get("patterns", {})},
    }


def save_settings(settings):
    """
    Persists detection settings for future calls
    """
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)


def load_program(path, gcode):
    """
    Parses a G-code program from a file path or inline text with the current detection settings
    """
    if (path is None) == (gcode is None):
        raise ToolError("Pass exactly one of `path` or `gcode`")
    if path is not None:
        with open(path) as f:
            gcode = f.read()

    program = gcode_parser.Program(detection=load_settings())
    try:
        program.parse_line(gcode)
    except gcode_parser.NotImplementedException as e:
        raise ToolError(f"Unsupported G-code ({e}): G91, G18, G19, G28 and G52 are not simulated") from e
    return program


@server.tool()
def analyze_program(path: str | None = None, gcode: str | None = None) -> dict:
    """
    Parse a G-code program and report the tooling, stock block and toolpath detected from it.

    `detections` shows which line set each value and how it matched; `missing` lists
    detectable fields no comment provided (they stay 0). Pass a file `path` or inline `gcode`.
    """
    program = load_program(path, gcode)
    fields = list(gcode_parser.DEFAULT_DETECTION["labels"])
    return {
        "units": "in" if program.inches else "mm",
        "values": {field: getattr(program, field) for field in fields},
        "block": program.block,
        "detections": program.detections,
        "missing": [field for field in fields if field not in program.detections],
        "moves": len(program.lines),
        "points": sum(len(section) for section in program.lines),
    }


@server.tool()
def test_detection(text: str) -> list[dict]:
    """
    Show which tooling/block values the current detection settings find in some G-code or comment text.

    Use this to try out label aliases, patterns and the fuzz threshold before running a full analysis.
    """
    settings = load_settings()
    return [
        {"line": line.strip(), "field": field, "value": value, "how": how}
        for line in text.split("\n")
        for (field, value, how) in gcode_parser.detect_comment(line, settings)
    ]


@server.tool()
def get_detection_settings() -> dict:
    """
    Return the comment detection settings: label aliases and regex patterns per field, fuzz threshold and units.
    """
    return load_settings()


@server.tool()
def update_detection_settings(
    labels: dict[str, list[str]] | None = None,
    patterns: dict[str, list[str]] | None = None,
    fuzz_threshold: float | None = None,
    units: str | None = None,
) -> dict:
    """
    Change how tooling and block values are detected in comments. Settings persist between calls.

    labels   - {field: [aliases]}; replaces the alias list of each given field. Aliases match comments
               like `(SHANK DIA: .5)` case-insensitively, tolerating typos down to `fuzz_threshold`.
    patterns - {field: [regex]}; replaces the regex list of each given field. Regexes run on the whole
               line and must contain `{num}` where the value is, e.g. `#127\\s*=\\s*{num}`.
    fuzz_threshold - 0 to 1 similarity needed for a label typo to match (1 = exact only).
    units    - "in" or "mm"; values with an explicit mm/in suffix are converted to these units.

    Fields: tool_holder_diameter, tool_holder_length, tool_diameter, tool_length, cutting_length,
    shank_diameter, gauge_length, block_{x,y,z}_{min,max}.
    """
    settings = load_settings()
    fields = gcode_parser.DEFAULT_DETECTION["labels"]

    for key, updates in (("labels", labels), ("patterns", patterns)):
        for field, values in (updates or {}).items():
            if field not in fields:
                raise ToolError(f"Unknown field '{field}', expected one of {list(fields)}")
            if key == "patterns":
                for pattern in values:
                    if "{num}" not in pattern:
                        raise ToolError(f"Pattern '{pattern}' must contain {{num}}")
                    try:
                        re.compile(pattern.replace("{num}", gcode_parser.NUMBER))
                    except re.error as e:
                        raise ToolError(f"Pattern '{pattern}' is not a valid regex: {e}") from e
            settings[key][field] = values

    if fuzz_threshold is not None:
        if not 0 <= fuzz_threshold <= 1:
            raise ToolError("fuzz_threshold must be between 0 and 1")
        settings["fuzz_threshold"] = fuzz_threshold

    if units is not None:
        if units not in ("in", "mm"):
            raise ToolError("units must be 'in' or 'mm'")
        settings["units"] = units

    save_settings(settings)
    return settings


@server.tool()
def reset_detection_settings() -> dict:
    """
    Restore the default comment detection settings.
    """
    if os.path.exists(SETTINGS_PATH):
        os.remove(SETTINGS_PATH)
    return load_settings()


@server.tool()
def generate_xyz(output_path: str, path: str | None = None, gcode: str | None = None) -> dict:
    """
    Convert a G-code program to an Emucraft XYZ file (one point per line, units of 0.001).

    Columns: X Y Z cutter_diameter tool_holder_diameter tool_holder_length cutting(0/1).
    X and Y are shifted so every point is positive.
    """
    program = load_program(path, gcode)
    program.adjust_coordinates()
    with open(output_path, "w") as f:
        for section in program.lines:
            f.write("\n".join(section) + "\n")

    return {"output_path": output_path, "points": sum(len(section) for section in program.lines)}


@server.tool()
def simulate(path: str | None = None, gcode: str | None = None, resolution: float = 0.01) -> dict:
    """
    Machine the stock block with the C kernel and report collisions.

    A collision is any material removed by a rapid (G0) or spindle-off move. The block comes from the
    MIN/MAX X/Y/Z comments. `resolution` is the grid size in program units; finer is slower
    (0.01" keeps a typical part to a few seconds). Needs a C compiler (`cc`, or set EMUCRAFT_CC).
    """
    program = load_program(path, gcode)
    block_fields = [f"block_{axis}_{end}" for axis in "xyz" for end in ("min", "max")]
    missing = [field for field in block_fields if field not in program.detections]
    if missing:
        raise ToolError(f"Block size not found in comments: {missing}")

    # Build the kernel when missing or out of date
    sources = [os.path.join(KERNEL_SRC, name) for name in ("main.c", "functions.c", "functions.h")]
    if not os.path.exists(KERNEL_BIN) or os.path.getmtime(KERNEL_BIN) < max(map(os.path.getmtime, sources)):
        os.makedirs(os.path.dirname(KERNEL_BIN), exist_ok=True)
        compiler = shlex.split(os.environ.get("EMUCRAFT_CC", "cc"))
        build = subprocess.run(
            compiler + ["-O2", "-o", KERNEL_BIN, sources[0], sources[1], "-lm"], capture_output=True, text=True
        )
        if build.returncode != 0:
            raise ToolError(f"Kernel build failed:\n{build.stderr}")

    # Kernel input: block-relative grid coordinates, consecutive duplicates dropped.
    # `steps` maps each kernel line back to its G-code line and whether it was a cutting move.
    scale = resolution * 1000
    origin = (program.block_x_min * 1000, program.block_y_min * 1000, program.block_z_min * 1000)
    rows, steps = [], []
    for source, section in zip(program.sources, program.lines):
        for point in section:
            x, y, z, diameter, holder_diameter, holder_length, cutting = map(int, point.split(" "))
            row = (
                f"{round((x - origin[0]) / scale)} {round((y - origin[1]) / scale)} {round((z - origin[2]) / scale)} "
                f"{round(diameter / scale)} {round(holder_diameter / scale)} {round(holder_length / scale)}"
            )
            if rows and rows[-1] == row and steps[-1] == (source, cutting):
                continue
            rows.append(row)
            steps.append((source, cutting))

    dims = [round((program.block_x_max - program.block_x_min) / resolution),
            round((program.block_y_max - program.block_y_min) / resolution),
            round((program.block_z_max - program.block_z_min) / resolution)]

    with tempfile.TemporaryDirectory() as tmp:
        xyz = os.path.join(tmp, "program.xyz")
        with open(xyz, "w") as f:
            f.write("\n".join(rows) + "\n")

        run = subprocess.run([KERNEL_BIN, *map(str, dims), xyz], cwd=tmp, stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE, text=True)
        if run.returncode != 0:
            raise ToolError(f"Kernel failed ({run.returncode}):\n{run.stderr}")

        # .sim lines: [step] [x] [y] [z] for every cell cut
        removed = Counter()
        if os.path.exists(xyz + ".sim"):
            with open(xyz + ".sim") as f:
                for line in f:
                    removed[int(line.split(" ", 1)[0])] += 1

    collisions = Counter()
    for step, cells in removed.items():
        source, cutting = steps[step]
        if not cutting:
            collisions[source] += cells

    return {
        "collision": bool(collisions),
        "collisions": [{"gcode": source, "cells": cells} for source, cells in collisions.most_common(50)],
        "collision_lines": len(collisions),
        "cells_cut": sum(removed.values()),
        "grid": {"resolution": resolution, "x": dims[0], "y": dims[1], "height": dims[2]},
        "kernel_points": len(rows),
    }


if __name__ == "__main__":
    server.run()
