"""Machine, stock and tool descriptions.

Values come from three places, later ones winning:

1. the program itself (CAM header comments, macro variables),
2. a configuration file (``emucraft.toml`` / ``.json``),
3. explicit overrides (command line, web UI).

Every resolved value remembers where it came from so reports can say so.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, fields
from typing import Dict, List, Optional, Tuple

from .gcode import MM_PER_INCH, Message, Program

SHAPES = ("flat", "ball", "bull", "cone")
SHAPE_IDS = {"flat": 0, "ball": 1, "bull": 2, "cone": 3}

TOOL_LENGTH_FIELDS = (
    "diameter",
    "corner_radius",
    "flute_length",
    "shank_diameter",
    "stickout",
    "holder_diameter",
    "holder_length",
)


def unit_factor(src: str, dst: str) -> float:
    """Multiply a length in ``src`` units by this to get ``dst`` units."""
    if src == dst:
        return 1.0
    return MM_PER_INCH if src == "inch" else 1.0 / MM_PER_INCH


def shape_from_kind(kind: str) -> Tuple[Optional[str], Optional[float]]:
    """Map a CAM tool description to a shape and a cone tip angle."""
    k = kind.lower()
    if "ball" in k or "spherical" in k:
        return "ball", None
    if "tip radius" in k or "bull" in k or "corner rad" in k or "radiused" in k or "toroid" in k:
        return "bull", None
    if "spot" in k:
        return "cone", 90.0
    if "center drill" in k or "centre drill" in k:
        return "cone", 60.0
    if "chamfer" in k or "countersink" in k:
        return "cone", 90.0
    if "engrav" in k or "v-bit" in k or "vee" in k:
        return "cone", 60.0
    if "drill" in k:
        return "cone", 118.0
    if any(w in k for w in ("end mill", "endmill", "flat", "slot", "face mill", "reamer", "tap", "bor")):
        return "flat", None
    return None, None


@dataclass
class ToolSpec:
    number: int
    shape: str = "flat"
    diameter: Optional[float] = None
    corner_radius: float = 0.0
    tip_angle: float = 118.0  # included angle of cone tools, degrees
    flute_length: Optional[float] = None
    shank_diameter: Optional[float] = None
    stickout: Optional[float] = None  # tip to holder face
    holder_diameter: Optional[float] = None
    holder_length: Optional[float] = None
    flutes: Optional[int] = None
    name: str = ""
    source: Dict[str, str] = field(default_factory=dict)

    @property
    def radius(self) -> Optional[float]:
        return None if self.diameter is None else self.diameter / 2.0

    def problems(self) -> List[str]:
        out = []
        if self.diameter is None or not self.diameter > 0:
            out.append("diameter is unknown")
        if self.shape not in SHAPES:
            out.append(f"unknown shape '{self.shape}'")
        if self.shape == "bull" and self.diameter and not 0 < self.corner_radius <= self.diameter / 2:
            out.append("bull nose tool needs 0 < corner_radius <= diameter / 2")
        if self.shape == "cone" and not 0 < self.tip_angle < 180:
            out.append("cone tip_angle must be between 0 and 180 degrees")
        if self.holder_diameter and self.diameter and self.holder_diameter < self.diameter:
            out.append("holder diameter is smaller than the cutter")
        return out

    def kernel_args(self) -> Tuple[int, float, float, float, float]:
        """(shape id, radius, corner, slope, flute length) for ec_sim_set_tool."""
        r = self.radius or 0.0
        slope = 0.0
        corner = self.corner_radius or 0.0
        if self.shape == "cone":
            slope = 1.0 / math.tan(math.radians(self.tip_angle / 2.0))
        return SHAPE_IDS[self.shape], r, corner, slope, self.flute_length or 0.0

    def bodies(self) -> List[Tuple[float, float, str]]:
        """Non-cutting cylinders: (radius, bottom above the tip, label)."""
        out = []
        r = self.radius or 0.0
        if self.shank_diameter and self.flute_length and self.shank_diameter / 2 > r:
            out.append((self.shank_diameter / 2, self.flute_length, "shank"))
        if self.holder_diameter and self.stickout:
            out.append((self.holder_diameter / 2, self.stickout, "holder"))
        return out

    def to_dict(self) -> dict:
        d = asdict(self)
        d["problems"] = self.problems()
        return d


@dataclass
class StockSpec:
    xmin: float
    ymin: float
    zmin: float
    xmax: float
    ymax: float
    zmax: float
    source: str = "config"

    def valid(self) -> bool:
        return self.xmax > self.xmin and self.ymax > self.ymin and self.zmax > self.zmin

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MachineSpec:
    rapid_rate: Optional[float] = None  # units/min; default 1000 in/min
    rapid_mode: str = "linear"  # "linear" or "dogleg" (non-interpolated rapids)
    home_z: Optional[float] = None  # Z the tool starts from and G28/G53 retract to
    table_z: Optional[float] = None  # the tip must stay above this (default: stock bottom)
    tool_change_time: float = 6.0  # seconds
    limits: Dict[str, List[float]] = field(default_factory=dict)  # {"x": [min, max], ...}


@dataclass
class CheckSpec:
    resolution: Optional[float] = None  # cell size; default from max_cells
    max_cells: int = 6_000_000
    tolerance: Optional[float] = None  # depth that counts (default 0.0005 in)
    chord_tolerance: Optional[float] = None  # arc chord error (default 0.0002 in)
    arc_radius_tolerance: Optional[float] = None  # arc end point error (default 0.002 in)
    simplify: Optional[float] = None  # merge collinear moves within this (default: tolerance / 2)
    link_feed: Optional[float] = None  # feed moves at/above this must not cut
    link_severity: str = "warning"  # or "error": how a link-cut counts
    block_delete: bool = False  # True: skip blocks starting with '/'
    tool_point: Optional[str] = None  # "tip" or "center" (default: from program)
    units: Optional[str] = None  # force program units
    default_units: str = "inch"
    threads: Optional[int] = None


@dataclass
class Config:
    units: Optional[str] = None  # units of lengths in this config (default: program units)
    machine: MachineSpec = field(default_factory=MachineSpec)
    check: CheckSpec = field(default_factory=CheckSpec)
    stock: Optional[StockSpec] = None
    tools: Dict[int, Dict[str, object]] = field(default_factory=dict)
    default_tool: Dict[str, object] = field(default_factory=dict)
    optimize: Dict[str, object] = field(default_factory=dict)
    comment_rules: List[Tuple[str, str]] = field(default_factory=list)
    variable_rules: Dict[int, str] = field(default_factory=dict)

    def copy(self) -> "Config":
        import copy

        return copy.deepcopy(self)


def _default_len(units: str, inch_value: float) -> float:
    return inch_value * unit_factor("inch", units)


def _tool_fields() -> set:
    return {f.name for f in fields(ToolSpec)} - {"number", "source"}


def apply_tool_values(spec: ToolSpec, values: Dict[str, object], origin: str, factor: float) -> None:
    """Set tool fields from a dict of values (lengths multiplied by factor)."""
    allowed = _tool_fields() | {"kind"}
    for key, value in values.items():
        if key not in allowed:
            raise ValueError(f"unknown tool field '{key}'")
        if value is None:
            continue
        if key == "kind":
            shape, angle = shape_from_kind(str(value))
            if shape:
                spec.shape = shape
                spec.source["shape"] = origin
            if angle and "tip_angle" not in values:
                spec.tip_angle = angle
                spec.source["tip_angle"] = origin
            if not spec.name:
                spec.name = str(value)
            continue
        if key in TOOL_LENGTH_FIELDS:
            value = float(value) * factor
        elif key == "tip_angle":
            value = float(value)
        elif key == "flutes":
            value = int(value)
        elif key == "shape":
            value = str(value).lower()
            if value not in SHAPES:
                raise ValueError(f"tool shape must be one of {SHAPES}, not '{value}'")
        setattr(spec, key, value)
        spec.source[key] = origin


def resolve_tools(
    program: Program, config: Config, overrides: Optional[Dict[object, Dict[str, object]]] = None
) -> Dict[int, ToolSpec]:
    """Tool geometry for every tool the program uses.

    ``overrides`` maps a tool number, or ``"*"`` for every tool, to field
    values in program units.
    """
    overrides = overrides or {}
    units = program.units
    cfg_factor = unit_factor(config.units or units, units)
    out: Dict[int, ToolSpec] = {}
    for number in program.slot_tools:
        spec = ToolSpec(number=number)
        note = program.tool_notes.get(number)
        if note:
            vals = dict(note.values)
            if "kind" in vals:
                apply_tool_values(spec, {"kind": vals.pop("kind")}, "program", 1.0)
            if "corner_radius" in vals and vals["corner_radius"] and spec.shape == "flat":
                spec.shape = "bull"
                spec.source["shape"] = "program"
            for key, value in vals.items():
                line = note.lines.get(key)
                origin = f"program line {line + 1}" if line is not None else "program"
                apply_tool_values(spec, {key: value}, origin, 1.0)
        if config.default_tool:
            unset = {k: v for k, v in config.default_tool.items()
                     if k not in spec.source and not (k == "kind" and "shape" in spec.source)}
            apply_tool_values(spec, unset, "config default_tool", cfg_factor)
        if number in config.tools:
            apply_tool_values(spec, config.tools[number], f"config tools.{number}", cfg_factor)
        if "*" in overrides:
            apply_tool_values(spec, overrides["*"], "override", 1.0)
        if number in overrides:
            apply_tool_values(spec, overrides[number], "override", 1.0)
        if spec.shape == "bull" and not spec.corner_radius:
            spec.shape = "flat"
        if spec.shape == "ball" and spec.diameter:
            spec.corner_radius = spec.diameter / 2
        out[number] = spec
    return out


def resolve_stock(program: Program, config: Config, moves_bounds=None) -> Tuple[Optional[StockSpec], List[Message]]:
    """Stock box: config wins, then the CAM header, then the toolpath extent."""
    msgs: List[Message] = []
    units = program.units
    if config.stock is not None:
        f = unit_factor(config.units or units, units)
        s = config.stock
        stock = StockSpec(s.xmin * f, s.ymin * f, s.zmin * f, s.xmax * f, s.ymax * f, s.zmax * f, s.source)
        if not stock.valid():
            msgs.append(Message("error", "stock", "The configured stock box is empty", None))
            return None, msgs
        return stock, msgs
    note = program.stock_note
    keys = ("xmin", "ymin", "zmin", "xmax", "ymax", "zmax")
    if all(k in note for k in keys):
        stock = StockSpec(*(note[k] for k in keys), source="program")
        if stock.valid():
            return stock, msgs
        msgs.append(Message("warning", "stock", "The stock box in the program header is empty", None))
    if moves_bounds is None:
        msgs.append(Message("error", "stock", "No stock size in the program or configuration", None))
        return None, msgs
    (x0, y0, z0), (x1, y1, z1), r = moves_bounds
    stock = StockSpec(x0 - r, y0 - r, z0, x1 + r, y1 + r, z1, source="toolpath")
    if not stock.valid():
        msgs.append(Message("error", "stock", "Cannot estimate the stock from the toolpath", None))
        return None, msgs
    msgs.append(Message("warning", "stock",
                        "No stock size given; the stock is estimated from the cutting moves", None))
    return stock, msgs


def default_rapid_rate(units: str) -> float:
    return _default_len(units, 1000.0)


def default_tolerance(units: str) -> float:
    return _default_len(units, 0.0005)
