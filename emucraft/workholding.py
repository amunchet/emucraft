"""Workholding check: can the cutting forces move the part in its vise or
under its clamps?

The simulation measures every short piece of the cutting path: the axial
depth and the cross-section of the cut, and so its radial width. Kienzle's
law (kc = kc1 * h^-mc) turns them, with the feed per tooth, into the peak
force of a straight-fluted cutter, which bounds a helical cutter's from
above; a right-hand helix adds a pull that lifts the part. Drilling and
plunging load the part with their torque, from the cutting power. A
worn-tool factor scales every load.

The fixture holds by friction:

* vise: the two jaw faces grip the part, 2 * friction * force between them,
  against the force in their plane: along the jaws and upwards. A moment
  about the clamping axis rocks the part over the floor (parallels) at the
  far end of the jaws, so it adds to the upward force the jaws must hold.
  The fixed jaw, the screw and the floor carry everything else.
* clamps: toe clamps press the part onto the table over its supports.
  Friction under the part and under the clamp tips resists sliding and
  turning (combined linearly, F/Fmax + M/Mmax <= 1, which stays inside the
  true friction limit), and the clamp forces resist lifting and tipping over
  an edge of the part.

The side force is taken in its worst direction. Holding grows in proportion
to the clamping force, so the smallest margin over the program gives the
least torque that holds every cut with the required safety factor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from .check import Grid, Issue, fine_path, trim_to_stock
from .gcode import CYCLE, MM_PER_INCH, RAPID, SPINDLE, Message, Program
from .kernel import Sim
from .model import Config, StockSpec, ToolSpec, unit_factor

N_PER_LBF = 4.4482216152605
NM_PER_FTLBF = 1.3558179483314004

# Kienzle specific cutting force kc1 (N/mm^2 at a 1 mm chip) and exponent mc
MATERIALS: Dict[str, Tuple[float, float]] = {
    "aluminum": (700.0, 0.25),
    "brass": (780.0, 0.18),
    "cast_iron": (1100.0, 0.28),
    "steel": (1700.0, 0.25),
    "alloy_steel": (2000.0, 0.25),
    "stainless": (2000.0, 0.21),
    "titanium": (1400.0, 0.23),
    "inconel": (3300.0, 0.24),
    "hardened_steel": (4800.0, 0.25),
}

# Coarse thread pitch by nominal size: UNC threads per inch, ISO metric mm
UNC_TPI = {0.25: 20, 0.3125: 18, 0.375: 16, 0.4375: 14, 0.5: 13, 0.5625: 12, 0.625: 11, 0.75: 10, 0.875: 9,
           1.0: 8, 1.125: 7, 1.25: 7}
ISO_PITCH = {6: 1.0, 8: 1.25, 10: 1.5, 12: 1.75, 14: 2.0, 16: 2.0, 18: 2.5, 20: 2.5, 22: 2.5, 24: 3.0, 27: 3.0,
             30: 3.5}
# Proof strength in MPa: SAE J429 grades (1/4 to 1 in) and ISO 898-1 classes
PROOF_MPA = {"5": 586.0, "8": 827.0, "8.8": 580.0, "10.9": 830.0, "12.9": 970.0}

FIXTURES = ("vise", "clamps")

# Kurt 6 in vises: clamping force (lbf) at handle torque (ft-lbf), from the
# "Manual Vise Clamping Force" table of each model's operating manual. The
# tables end at the most torque Kurt lists; Kurt warns against handle
# extensions, impact wrenches and hammers.
VISES: Dict[str, List[Tuple[float, float]]] = {
    "d688": [(10, 1129), (20, 2242), (30, 3337), (40, 4148), (50, 5379), (60, 6310), (70, 6977), (80, 7968)],
    "dx6": [(10, 512), (20, 1487), (30, 2464), (40, 3440), (50, 4415), (60, 5391), (70, 6367), (80, 7342)],
    "3600v": [(10, 653), (20, 1743), (30, 2234), (40, 3015), (50, 3833), (60, 4438), (70, 5528), (80, 6356)],
    "hd690": [(10, 975), (20, 1625), (30, 2400), (40, 3250), (50, 3700), (60, 4275), (70, 4700), (80, 5250)],
    "hdl6": [(10, 1540), (20, 2520), (30, 3350), (40, 4310), (50, 5750), (60, 6860), (70, 7460)],
    "scd640": [(10, 975), (20, 1750), (30, 2575), (40, 3750), (50, 4375), (60, 5750)],  # self-centering
}
VISE_JAW = 6.0  # in


@dataclass
class HoldSpec:
    """The fixture and the cutting-force model. Lengths in program units,
    forces in N, torques in N*m."""

    fixture: str = "vise"
    material: Optional[str] = None
    kc1: Optional[float] = None  # N/mm^2; overrides the material
    mc: Optional[float] = None
    safety_factor: float = 2.0  # required margin on the worn-tool load
    wear_factor: float = 1.5  # cutting force of a worn tool over a sharp one
    radial_ratio: float = 0.5  # radial / tangential cutting force
    axial_ratio: float = 0.5  # lift of a right-hand helix / tangential force
    friction: float = 0.15  # jaw or clamp friction coefficient
    lift_stop: bool = False  # lift is held positively (dovetail, step jaws, bolts)
    torque: Optional[float] = None  # vise handle, or each clamp's bolt
    force: Optional[float] = None  # instead of a torque: vise force, or each clamp's force on the part
    max_torque: Optional[float] = None
    axis: str = "y"  # vise: the direction the jaws close
    force_per_torque: Optional[float] = None  # vise: N of clamping force per N*m on the handle
    vise: Optional[str] = None  # a vise model of VISES: its force-torque table replaces force_per_torque
    jaw_width: Optional[float] = None  # vise: jaw length (default: the whole part is gripped)
    grip: Optional[float] = None  # vise: height of the part held in the jaws, from its bottom
    center: Optional[float] = None  # vise: the jaws' middle along the jaws (default: the part's)
    clamps: List[Tuple[float, float]] = field(default_factory=list)  # (x, y) where each clamp presses
    bolt: Optional[float] = None  # clamps: bolt diameter, mm
    bolt_area: Optional[float] = None  # clamps: tensile stress area, mm^2 (standard coarse threads)
    bolt_grade: Optional[str] = None
    nut_factor: float = 0.2  # torque = nut_factor * bolt force * bolt diameter
    lever: float = 0.5  # share of the bolt force that presses the part

    def cutting(self) -> Tuple[float, float]:
        if self.kc1 is not None:
            return self.kc1, self.mc if self.mc is not None else 0.25
        return MATERIALS[self.material]

    def curve(self) -> Optional[List[Tuple[float, float]]]:
        """(N*m, N) points from no torque up, when the fixture says how torque
        becomes clamping force."""
        if self.vise and self.fixture == "vise":
            return [(0.0, 0.0)] + [(t * NM_PER_FTLBF, f * N_PER_LBF) for t, f in VISES[self.vise]]
        k = self.force_per_torque if self.fixture == "vise" else (
            self.lever / (self.nut_factor * self.bolt / 1000.0) if self.bolt else None)
        return None if k is None else [(0.0, 0.0), (1.0, k)]

    def force_at(self, torque: float) -> float:
        return _interpolate(self.curve(), torque, 0, 1)

    def torque_for(self, force: float) -> float:
        return _interpolate(self.curve(), force, 1, 0)

    def limit(self) -> Optional[float]:
        """Most torque the fixture takes: max_torque, else the top of the vise
        maker's table, else what preloads the bolt to 75 % of its proof load,
        the usual ceiling for a joint that is undone again."""
        if self.max_torque is None and self.vise and self.fixture == "vise":
            return VISES[self.vise][-1][0] * NM_PER_FTLBF
        if self.max_torque is not None or not self.bolt_area:
            return self.max_torque
        proof = PROOF_MPA[self.bolt_grade or "5"]
        if self.bolt_grade == "8.8" and self.bolt > 16.5:
            proof = 600.0
        return self.nut_factor * 0.75 * proof * self.bolt_area * self.bolt / 1000.0


def _interpolate(points: List[Tuple[float, float]], x: float, a: int, b: int) -> float:
    """Piecewise-linear lookup of coordinate b at coordinate a = x, the end
    segments extended."""
    for p, q in zip(points, points[1:]):
        if x <= q[a] or q is points[-1]:
            return p[b] + (x - p[a]) * (q[b] - p[b]) / (q[a] - p[a])
    raise ValueError("empty curve")


_TEXT = ("fixture", "material", "axis", "bolt_grade", "vise")
_LENGTHS = ("jaw_width", "grip", "center")
_NUMBERS = ("kc1", "mc", "safety_factor", "wear_factor", "radial_ratio", "axial_ratio", "friction", "nut_factor",
            "lever")
# forces and torques given in lbf and ft*lbf (inch) are stored in N and N*m
_IMPERIAL = {"torque": NM_PER_FTLBF, "max_torque": NM_PER_FTLBF, "force": N_PER_LBF,
             "force_per_torque": N_PER_LBF / NM_PER_FTLBF}
OPTIONS = set(_TEXT) | set(_LENGTHS) | set(_NUMBERS) | set(_IMPERIAL) | {"lift_stop", "clamps", "bolt"}


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def apply_values(spec: HoldSpec, values: Dict[str, object], units: str, factor: float) -> None:
    """Set fields from config values: lengths times ``factor``; forces and
    torques in lbf and ft*lbf when ``units`` is "inch", else in N and N*m."""
    inch = units == "inch"
    for key, value in values.items():
        if key not in OPTIONS:
            raise ValueError(f"unknown workholding option '{key}'")
        if value is None:
            continue
        if key in _TEXT:
            value = str(value).lower()
            allowed = {"fixture": FIXTURES, "material": tuple(MATERIALS), "axis": ("x", "y"),
                       "bolt_grade": tuple(PROOF_MPA), "vise": tuple(VISES)}[key]
            if value not in allowed:
                raise ValueError(f"workholding {key} must be one of {', '.join(allowed)}, not '{value}'")
            setattr(spec, key, value)
        elif key == "lift_stop":
            if not isinstance(value, bool):
                raise ValueError("workholding lift_stop must be true or false")
            spec.lift_stop = value
        elif key == "clamps":
            if not (isinstance(value, list) and value and all(
                    isinstance(p, (list, tuple)) and len(p) == 2 and all(_number(v) for v in p) for p in value)):
                raise ValueError("workholding clamps must be a list of [x, y] positions")
            spec.clamps = [(float(x) * factor, float(y) * factor) for x, y in value]
        elif key == "bolt":
            if not (_number(value) and value > 0):
                raise ValueError("workholding bolt must be a positive diameter")
            size = float(value)
            if inch:
                tpi = UNC_TPI.get(round(size, 4))
                spec.bolt = size * MM_PER_INCH
                spec.bolt_area = tpi and math.pi / 4 * (size - 0.9743 / tpi) ** 2 * MM_PER_INCH ** 2
            else:
                pitch = ISO_PITCH.get(size)
                spec.bolt = size
                spec.bolt_area = pitch and math.pi / 4 * (size - 0.9382 * pitch) ** 2
            spec.bolt_grade = spec.bolt_grade or ("5" if inch else "8.8")
        elif not _number(value):
            raise ValueError(f"workholding {key} must be a number")
        elif key in _IMPERIAL:
            if not value > 0:
                raise ValueError(f"workholding {key} must be positive")
            setattr(spec, key, float(value) * (_IMPERIAL[key] if inch else 1.0))
        else:
            setattr(spec, key, float(value) * (factor if key in _LENGTHS else 1.0))


def hold_spec(config: Config, units: str, overrides: Optional[Dict[str, object]] = None) -> HoldSpec:
    """The workholding setup of ``config`` for a program in ``units``, with
    ``overrides`` in program units. Raises ValueError when it is incomplete."""
    spec = HoldSpec()
    apply_values(spec, config.workholding, config.units or units, unit_factor(config.units or units, units))
    if overrides:
        apply_values(spec, overrides, units, 1.0)
    if spec.material is None and spec.kc1 is None and config.optimize.get("material"):
        spec.material = str(config.optimize["material"]).lower()
        if spec.material not in MATERIALS:
            raise ValueError(f"unknown material '{spec.material}' (known: {', '.join(MATERIALS)})")
    if spec.material is None and spec.kc1 is None:
        raise ValueError("workholding needs the part material (material, or kc1 and mc)")
    if (spec.kc1 is not None and not spec.kc1 > 0) or (spec.mc is not None and not 0 <= spec.mc < 1):
        raise ValueError("workholding kc1 must be positive and mc in [0, 1)")
    if spec.safety_factor < 1 or spec.wear_factor < 1:
        raise ValueError("workholding safety_factor and wear_factor must be at least 1")
    if spec.radial_ratio < 0 or spec.axial_ratio < 0 or not 0 < spec.friction <= 1.5:
        raise ValueError("workholding radial_ratio and axial_ratio must be >= 0 and friction in (0, 1.5]")
    if not 0 < spec.nut_factor < 1 or not 0 < spec.lever <= 1:
        raise ValueError("workholding nut_factor must be in (0, 1) and lever in (0, 1]")
    if spec.torque is not None and spec.force is not None:
        raise ValueError("give the workholding torque or force, not both")
    if spec.fixture == "vise":
        if spec.grip is None or not spec.grip > 0:
            raise ValueError("a vise needs grip: the height of the part held in the jaws")
        if spec.jaw_width is not None and not spec.jaw_width > 0:
            raise ValueError("workholding jaw_width must be positive")
        if spec.torque is not None and spec.curve() is None:
            raise ValueError("a vise torque needs the vise model (vise) or force_per_torque, its clamping "
                             "force per unit of handle torque (or give the clamping force instead)")
        if spec.vise and spec.jaw_width is None:
            spec.jaw_width = VISE_JAW * unit_factor("inch", units)
    elif not spec.clamps:
        raise ValueError("clamps need their positions: clamps = [[x, y], ...]")
    elif spec.torque is not None and spec.bolt is None:
        raise ValueError("a clamp torque needs the bolt diameter (bolt)")
    return spec


@lru_cache(maxsize=None)
def tooth_peak(teeth: int, ratio: float, mc: float) -> float:
    """Peak over a turn of sum(sin(phi) ** (1 - mc)) over the teeth in a cut
    ``ratio`` diameters wide: the peak tangential force of straight flutes, in
    units of kc1 * ap * fz ** (1 - mc)."""
    exit_ = math.acos(1.0 - 2.0 * ratio)
    pitch = 2.0 * math.pi / teeth
    p = 1.0 - mc
    best = 0.0
    for theta in [pitch * k / 64 for k in range(64)] + [(math.pi / 2) % pitch, exit_ % pitch]:
        total = 0.0
        for j in range(teeth):
            phi = theta + j * pitch
            if phi <= exit_:
                total += math.sin(phi) ** p
        best = max(best, total)
    return best


@dataclass
class _Load:
    """Worn-tool peak load of one piece of the path: N, and N * program units."""

    piece: int
    side: float  # force in the table plane
    lift: float  # upward pull
    torque: float  # about the tool axis
    height: float  # Z where the side force acts
    depth: float  # axial depth of cut
    width: float  # radial width of cut


def analyze(program: Program, tools: Dict[int, ToolSpec], stock: StockSpec, grid: Grid, spec: HoldSpec,
            heights=None, threads: int = 1) -> Tuple[List[Issue], List[Message], dict]:
    """Every cut's peak load against the fixture: (issues, messages, report).

    ``tools`` maps the slots with usable geometry to their specs; ``heights``
    is the starting stock of a chained check."""
    units = program.units
    u = MM_PER_INCH if units == "inch" else 1.0  # mm per program unit
    f_unit, t_unit = ("lbf", "ft-lbf") if units == "inch" else ("N", "N-m")
    f_out, t_out = (1 / N_PER_LBF, 1 / NM_PER_FTLBF) if units == "inch" else (1.0, 1.0)
    kc1, mc = spec.cutting()
    issues: List[Issue] = []
    messages: List[Message] = []

    # ---- the cut of every short piece of the path, and its peak load
    teeth: Dict[int, int] = {}
    for slot, t in tools.items():
        teeth[slot] = 2 if t.shape == "cone" else t.flutes or 1
        if not t.flutes and t.shape != "cone":
            messages.append(Message("warning", "workholding-flutes",
                                    f"T{t.number}: number of flutes unknown; its cutting force is worked out "
                                    f"for 1 flute, the worst case (set flutes for this tool)", None))
    fpts, fflags, ftools, fparent = fine_path(program, {s: t.diameter / 4.0 for s, t in tools.items()})
    with Sim(grid.nx, grid.ny, grid.x0, grid.y0, grid.cell, stock.zmax, stock.zmin) as sim:
        if heights is not None:
            sim.load_heights(heights)
        else:
            trim_to_stock(sim, stock)
        for slot, t in tools.items():
            shape, radius, corner, slope, _ = t.kernel_args()
            sim.set_tool(slot, shape, radius, corner, slope, 0.0)
        run = sim.run(fpts, fflags, ftools, threads=threads, move_outputs=True)
    loads: List[_Load] = []
    no_speed = set()
    root = math.sqrt(1.0 + spec.radial_ratio ** 2)
    w = spec.wear_factor
    for k in range(len(fflags)):
        slot, f, vol = ftools[k], fflags[k], run.move_volume[k]
        if slot < 0 or f & RAPID or not f & SPINDLE or not vol > 0:
            continue  # rapids and spindle-off cuts are collisions of their own
        i = fparent[k]
        feed, rpm = program.feed[i], program.speed[i]
        if not (feed > 0 and rpm > 0):
            no_speed.add(slot)
            continue
        j = 3 * k
        dx, dy, dz = fpts[j + 3] - fpts[j], fpts[j + 4] - fpts[j + 1], fpts[j + 5] - fpts[j + 2]
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        if length <= 0:
            continue
        t = tools[slot]
        z = teeth[slot]
        f_rev = feed / rpm * u  # mm per turn
        area = vol / length * u * u  # mm^2 across the feed direction
        depth = float(run.move_depth[k])
        if f & CYCLE or math.hypot(dx, dy) <= 0.1 * abs(dz):
            # drilling or plunging: the torque follows from the cutting power;
            # a drill's two lips cancel sideways, an end mill's may not
            sink = math.sin(math.radians(t.tip_angle / 2.0)) if t.shape == "cone" else 1.0
            kc = kc1 * (f_rev / z * sink) ** -mc
            torque = kc * area * f_rev / (2.0 * math.pi) / u
            side = 0.0 if t.shape == "cone" else 2.0 * torque / t.diameter * root
            loads.append(_Load(k, side * w, 0.0, torque * w, fpts[j + 5], depth, t.diameter))
            continue
        ap = max(depth * u, 1e-9)
        ratio = min(1.0, area / ap / (t.diameter * u))
        tangential = kc1 * ap * (f_rev / z) ** (1.0 - mc) * tooth_peak(z, round(ratio, 3), mc) * w
        loads.append(_Load(k, tangential * root, 0.0 if t.shape == "cone" else tangential * spec.axial_ratio,
                           tangential * t.diameter / 2.0, (fpts[j + 2] + fpts[j + 5]) / 2.0 + depth / 2.0,
                           depth, ratio * t.diameter))
    for slot in sorted(no_speed):
        messages.append(Message("error", "workholding-speed",
                                f"T{program.slot_tools[slot]} cuts without a spindle speed or feed: its cutting "
                                f"forces cannot be worked out", None))
    if spec.lift_stop:
        for load in loads:
            load.lift = 0.0

    # ---- holding margin of every load, per newton of clamping force
    mu = spec.friction
    if spec.fixture == "vise":
        along = 0 if spec.axis == "y" else 1  # coordinate along the jaws
        lo, hi = (stock.xmin, stock.xmax) if along == 0 else (stock.ymin, stock.ymax)
        center = spec.center if spec.center is not None else (lo + hi) / 2.0
        if spec.jaw_width:
            lo, hi = max(lo, center - spec.jaw_width / 2.0), min(hi, center + spec.jaw_width / 2.0)
        if hi <= lo:
            raise ValueError("the vise jaws do not reach the part")
        if spec.grip > stock.zmax - stock.zmin + 1e-9:
            raise ValueError("the vise grip is taller than the part")
        span, mid, z_mid = hi - lo, (lo + hi) / 2.0, stock.zmin + spec.grip / 2.0

        def holding(load: _Load) -> Tuple[float, str]:
            if spec.lift_stop:
                return 2.0 * mu / load.side if load.side > 0 else math.inf, "slides along the jaws"
            p = fpts[3 * load.piece + 3 + along]
            r = tools[ftools[load.piece]].diameter / 2.0
            # rocking about the clamping axis: the floor under the far end of the
            # jaws takes the moment, and the jaws must hold the near end down
            rock = 2.0 * (load.side * abs(load.height - z_mid) + load.lift * (abs(p - mid) + r)) / span
            up = load.lift + rock
            mode = ("slides along the jaws" if load.side >= up else
                    "rocks in the jaws" if rock > load.lift else "pulls out of the jaws")
            force = math.hypot(load.side, up)  # drilling: the jaws and floor take it all
            return 2.0 * mu / force if force > 0 else math.inf, mode
    else:
        pts = spec.clamps
        tol = 1e-6 * max(1.0, abs(stock.xmax), abs(stock.ymax))
        for x, y in pts:
            if not (stock.xmin - tol <= x <= stock.xmax + tol and stock.ymin - tol <= y <= stock.ymax + tol):
                raise ValueError(f"the clamp at X{x:g} Y{y:g} is not on the part")
        n = len(pts)
        gx, gy = sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n
        # smallest sum of the clamps' distances to one point (their geometric
        # median, Weiszfeld): a pure twist meets a friction moment mu * F * spread
        cx, cy = gx, gy
        for _ in range(200):
            wsum = sx = sy = 0.0
            for x, y in pts:
                dist = max(math.hypot(x - cx, y - cy), 1e-12)
                wsum += 1.0 / dist
                sx += x / dist
                sy += y / dist
            cx, cy = sx / wsum, sy / wsum
        spread = min(sum(math.hypot(x - qx, y - qy) for x, y in pts) for qx, qy in [(cx, cy), *pts])
        # the part's footprint edges it could tip over: (axis, coordinate, inward sign)
        edges = [(0, stock.xmin, 1.0, "X-"), (0, stock.xmax, -1.0, "X+"),
                 (1, stock.ymin, 1.0, "Y-"), (1, stock.ymax, -1.0, "Y+")]
        resist = [sum((p[a] - c) * s for p in pts) for a, c, s, _ in edges]

        def holding(load: _Load) -> Tuple[float, str]:
            j = 3 * load.piece + 3
            p = (fpts[j], fpts[j + 1])
            r = tools[ftools[load.piece]].diameter / 2.0
            # sliding and turning against friction under the part and under the
            # clamp tips; the lift takes its share off the table's
            twist = load.torque + load.side * math.hypot(p[0] - gx, p[1] - gy)
            share = load.side / (2 * mu * n)
            if twist > 0:
                share += twist / (2 * mu * spread) if spread > 0 else math.inf
            den = 2.0 * share + load.lift / n
            best, mode = 2.0 / den if den > 0 else math.inf, "slides or turns under the clamps"
            for (a, c, s, name), m in zip(edges if not spec.lift_stop else [], resist):
                over = load.lift * ((p[a] - c) * s + r) + load.side * (load.height - stock.zmin)
                if over > 0 and m / over < best:
                    best, mode = m / over, f"tips over its {name} edge"
            return best, mode

    # ---- the clamping force applied and the least that holds every cut
    report: dict = {
        "fixture": spec.fixture,
        "material": spec.material if spec.kc1 is None else None,
        "kc1": kc1,
        "mc": mc,
        "safety_factor": spec.safety_factor,
        "wear_factor": spec.wear_factor,
        "friction": mu,
        "lift_stop": spec.lift_stop,
        "units": {"force": f_unit, "torque": t_unit},
        "torque": None if spec.torque is None else spec.torque * t_out,
        "force": None,
        "max_torque": None if spec.limit() is None else spec.limit() * t_out,
        "cuts": len(loads),
        "margin": None,
        "required_force": None,
        "required_torque": None,
        "worst": None,
        # the rest of the setup, as the options take it (program units)
        "radial_ratio": spec.radial_ratio,
        "axial_ratio": spec.axial_ratio,
        "axis": spec.axis,
        "vise": spec.vise,
        "force_per_torque": None if spec.force_per_torque is None else float(
            f"{spec.force_per_torque * f_out / t_out:.9g}"),
        "grip": spec.grip,
        "jaw_width": spec.jaw_width,
        "center": spec.center,
        "clamps": [list(p) for p in spec.clamps],
        "bolt": None if spec.bolt is None else spec.bolt / u,
        "bolt_grade": spec.bolt_grade,
        "nut_factor": spec.nut_factor,
        "lever": spec.lever,
    }
    per_torque = spec.curve() is not None
    applied = spec.force if spec.force is not None else (
        spec.force_at(spec.torque) if spec.torque is not None else None)
    limit = spec.limit()
    if applied is not None:
        report["force"] = applied * f_out
    if spec.torque is not None and limit is not None and spec.torque > limit * (1 + 1e-9):
        messages.append(Message("error", "workholding-torque",
                                f"The clamping torque {spec.torque * t_out:.0f} {t_unit} is more than the "
                                f"{limit * t_out:.0f} {t_unit} the fixture takes", None))
    if not loads:
        return issues, messages, report
    held = [holding(load) for load in loads]
    weakest = min(range(len(loads)), key=lambda q: held[q][0])
    s_min, mode = held[weakest]
    load = loads[weakest]
    i = fparent[load.piece]
    need = spec.safety_factor / s_min if s_min > 0 else math.inf  # newtons of clamping force
    if s_min > 0:
        report["required_force"] = need * f_out
        if per_torque:
            report["required_torque"] = spec.torque_for(need) * t_out
    report["worst"] = {
        "line": program.line[i] + 1,
        "move": i,
        "tool": program.slot_tools[ftools[load.piece]],
        "mode": mode,
        "position": list(fpts[3 * load.piece + 3:3 * load.piece + 6]),
        "side_force": load.side * f_out,
        "lift": load.lift * f_out,
        "cutting_torque": load.torque * u / 1000.0 * t_out,
        "axial_depth": load.depth,
        "radial_width": load.width,
        "feed": program.feed[i],
        "rpm": program.speed[i],
    }

    def issue(group: List[Tuple[_Load, float, str]], text: Optional[str] = None) -> Issue:
        weak, margin, why = min(group, key=lambda g: g[1])
        first, last = fparent[group[0][0].piece], fparent[group[-1][0].piece]
        tool = program.slot_tools[ftools[weak.piece]]
        if text is None:
            text = (f"Cutting load can move the part: it {why} (holding margin {margin:.2f}x, needs "
                    f"{spec.safety_factor:g}x)")
        text += (f"; T{tool} pushes {weak.side * f_out:.0f} {f_unit} sideways"
                 + (f" and lifts {weak.lift * f_out:.0f} {f_unit}" if weak.lift > 0 else ""))
        j = 3 * weak.piece + 3
        return Issue("workholding", "error", text, first, last, program.line[first], program.line[last],
                     (fpts[j], fpts[j + 1], fpts[j + 2]), depth=weak.depth, tool=tool, moves=last - first + 1)

    if applied is not None:
        report["margin"] = s_min * applied if math.isfinite(s_min) else None
        group: List[Tuple[_Load, float, str]] = []
        for load, (s, why) in zip(loads, held):
            if s * applied >= spec.safety_factor:
                continue
            k = load.piece
            if group and (fparent[k] - fparent[group[-1][0].piece] > 1 or ftools[k] != ftools[group[-1][0].piece]):
                issues.append(issue(group))
                group = []
            group.append((load, s * applied, why))
        if group:
            issues.append(issue(group))
    elif not s_min > 0:
        issues.append(issue([(load, 0.0, mode)], f"No clamping force holds this cut: the part {mode}"))
    elif limit is not None and per_torque and spec.torque_for(need) > limit * (1 + 1e-9):
        issues.append(issue([(load, 0.0, mode)],
                            f"Holding this cut needs {spec.torque_for(need) * t_out:.0f} {t_unit}, more than the "
                            f"{limit * t_out:.0f} {t_unit} the fixture takes (the part {mode})"))
    return issues, messages, report
