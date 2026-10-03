"""G-code interpreter.

Turns a program into one continuous path of straight moves (arcs are split
into chords) plus what the checker needs to know about every move: rapid or
feed, spindle state, tool, feed rate, spindle speed and the source line. The
target dialect is Fanuc-style ISO code as posted by PowerMill, Fusion 360,
Mastercam and friends for Fanuc, Makino, Haas and similar controls.

Anything that cannot be simulated faithfully is reported as a message rather
than guessed silently.
"""

from __future__ import annotations

import math
import re
from array import array
from bisect import bisect_left
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

MM_PER_INCH = 25.4

# Move flags. The low three bits are read by the kernel (see emucraft.h).
RAPID = 1
SPINDLE = 2
NOCUT = 4  # set by the checker for high-feed link moves
ARC = 8
CYCLE = 16

UNITS = ("inch", "mm")

# How the optimizer may edit a line that has feed moves (Program.line_edit).
EDIT_NONE = 0  # no feed moves
EDIT_SPLIT = 1  # plain absolute G1 X/Y/Z/F: may be split and re-fed
EDIT_FEED = 2  # arcs, cycles, lines with other words: F may change
EDIT_LOCKED = 3  # inverse time, feed per rev, macro feeds: leave alone

# ------------------------------------------------------------------ results


@dataclass
class Message:
    """Something the interpreter could not do (or wants to point out)."""

    level: str  # "error" | "warning" | "info"
    code: str
    text: str
    line: Optional[int] = None  # 0-based index of the first line concerned
    count: int = 1

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "code": self.code,
            "text": self.text,
            "line": None if self.line is None else self.line + 1,
            "count": self.count,
        }


@dataclass
class ToolNote:
    """Tool data found in the program (comments, macro variables)."""

    number: int
    values: Dict[str, object] = field(default_factory=dict)
    lines: Dict[str, int] = field(default_factory=dict)


class Program:
    """An interpreted program: the path and everything known about it.

    Move ``i`` goes from point ``i`` to point ``i + 1``; ``points`` holds
    ``3 * (n + 1)`` coordinates in program units.
    """

    def __init__(self, text: str, name: str = "program") -> None:
        self.name = name
        self.text = text
        self.lines: List[str] = text.splitlines()
        self.units = "inch"
        self.points = array("d")
        self.flags = array("i")
        self.tool_slot = array("i")  # index into slot_tools, -1 = no tool
        self.feed = array("d")  # units/min, 0 for rapids
        self.speed = array("d")  # spindle rpm while the move runs
        self.line = array("i")  # 0-based source line
        self.slot_tools: List[int] = []  # slot -> tool number
        self.tool_notes: Dict[int, ToolNote] = {}
        self.stock_note: Dict[str, float] = {}
        self.stock_lines: Dict[str, int] = {}
        self.tool_point = "tip"
        self.messages: List[Message] = []
        self.dwell = 0.0  # seconds of G4 dwell
        self.tool_changes = 0
        self.cycle_rapid = 0.0  # extra rapid travel inside peck cycles
        self.end_line: Optional[int] = None
        self.line_edit = bytearray(len(self.lines))
        self.home_z: Optional[float] = None  # assumed Z of the start point and of G28/G53

    @property
    def n_moves(self) -> int:
        return len(self.flags)

    def point(self, i: int) -> Tuple[float, float, float]:
        p = self.points
        return (p[3 * i], p[3 * i + 1], p[3 * i + 2])

    def move_length(self, i: int) -> float:
        p = self.points
        j = 3 * i
        return math.sqrt(
            (p[j + 3] - p[j]) ** 2 + (p[j + 4] - p[j + 1]) ** 2 + (p[j + 5] - p[j + 2]) ** 2
        )

    def first_move_of_line(self, line: int) -> Optional[int]:
        """First move produced by a 0-based source line, if any."""
        i = bisect_left(self.line, line)
        if i < len(self.line) and self.line[i] == line:
            return i
        return None

    def tool_number(self, move: int) -> Optional[int]:
        slot = self.tool_slot[move]
        return self.slot_tools[slot] if slot >= 0 else None

    def errors(self) -> List[Message]:
        return [m for m in self.messages if m.level == "error"]


# ---------------------------------------------------------- comment notes

NUM = r"([-+]?(?:\d+\.?\d*|\.\d+))"

# (regex, target). Targets: tool.<field>, stock.<field>, program.<field>.
# Applied to comment text, case-insensitively. PowerMill header style first.
DEFAULT_COMMENT_RULES: List[Tuple[str, str]] = [
    (r"\bTool\s+Number\s*:\s*(\d+)", "tool.number"),
    (r"^\s*(?:TOOL\s+)?DIA(?:METER|\.)?\s*[:=]\s*" + NUM, "tool.diameter"),
    (r"\b(?:TIP|CORNER)\s+RAD(?:IUS)?\s*[:=]\s*" + NUM, "tool.corner_radius"),
    (r"\bTool\s*:\s*([A-Za-z][A-Za-z \-]*[A-Za-z])\s*$", "tool.kind"),
    (r"\bTool\s+Id\s*:\s*(.+?)\s*$", "tool.name"),
    (r"\bNumber\s+of\s+Flutes\s*:\s*(\d+)", "tool.flutes"),
    (r"\bRecommended\s+length\s*:\s*" + NUM, "tool.stickout"),
    (r"\b(?:STICK\s*OUT|OVERHANG|GAUGE\s+LENGTH\s+FROM\s+HOLDER)\s*[:=]\s*" + NUM, "tool.stickout"),
    (r"\bTool\s+Holder\s+Diameter\s*[:=]\s*" + NUM, "tool.holder_diameter"),
    (r"\bHOLDER\s+DIA(?:METER|\.)?\s*[:=]\s*" + NUM, "tool.holder_diameter"),
    (r"\bHOLDER\s+LENGTH\s*[:=]\s*" + NUM, "tool.holder_length"),
    (r"\b(?:FLUTE|CUTTING)\s+LENGTH\s*[:=]\s*" + NUM, "tool.flute_length"),
    (r"\bSHANK\s+DIA(?:METER|\.)?\s*[:=]\s*" + NUM, "tool.shank_diameter"),
    (r"\bTIP\s+ANGLE\s*[:=]\s*" + NUM, "tool.tip_angle"),
    (r"\bMIN\s*X\s*:\s*" + NUM, "stock.xmin"),
    (r"\bMIN\s*Y\s*:\s*" + NUM, "stock.ymin"),
    (r"\bMIN\s*Z\s*:\s*" + NUM, "stock.zmin"),
    (r"\bMAX\s*X\s*:\s*" + NUM, "stock.xmax"),
    (r"\bMAX\s*Y\s*:\s*" + NUM, "stock.ymax"),
    (r"\bMAX\s*Z\s*:\s*" + NUM, "stock.zmax"),
    (r"\bUnits\s*:\s*(INCH(?:ES)?|MM|MILLIMET(?:RE|ER)S?)\b", "program.units"),
    (r"\bTool\s+Coordinates\s*:\s*(Tip|Centre|Center)\b", "program.tool_point"),
]

# Macro variables that carry tool data (this shop's post: #127 holder length,
# #128 tool stick-out below the holder).
DEFAULT_VARIABLE_RULES: Dict[int, str] = {127: "tool.holder_length", 128: "tool.stickout"}

# Fusion 360: (T1 D=0.25 CR=0.03 TAPER=118deg - ZMIN=-0.5 - bull nose end mill)
_FUSION_TOOL = re.compile(
    r"^\s*T(\d+)\s+D=" + NUM + r"(?:\s+CR=" + NUM + r")?(?:\s+TAPER=" + NUM + r"deg)?"
    r"(?:.*-\s*([A-Za-z][A-Za-z \-]*?))?\s*$",
    re.I,
)
# Mastercam: (TOOL - 1 DIA. OFF. - 1 LEN. - 1 DIA. - .5)
_MASTERCAM_TOOL = re.compile(
    r"\bTOOL\s*-\s*(\d+)\s+DIA\.\s*OFF\.\s*-\s*\d+\s+LEN\.\s*-\s*\d+\s+DIA\.\s*-\s*" + NUM, re.I
)

_TEXT_FIELDS = {"kind", "name", "units", "tool_point"}
_INT_FIELDS = {"number", "flutes"}


def _note_value(target: str, raw: str):
    fld = target.split(".", 1)[1]
    if fld in _TEXT_FIELDS:
        return raw.strip()
    if fld in _INT_FIELDS:
        return int(float(raw))
    return float(raw)


# ------------------------------------------------------------- tokenizing

_WORD = re.compile(r"([A-Z])\s*([+-]?(?:\d+\.?\d*|\.\d+))|(\S)")
# The common CAM block: [N..] [G0|G1] [X..] [Y..] [Z..] [F..] in this order.
_FAST = re.compile(
    r"\s*(?:N\d+\s*)?(?:G0*([01])\s*)?(?:X\s*([-+]?[\d.]+)\s*)?(?:Y\s*([-+]?[\d.]+)\s*)?"
    r"(?:Z\s*([-+]?[\d.]+)\s*)?(?:F\s*([-+]?[\d.]+)\s*)?$"
)
_COMMENT = re.compile(r"\(([^)]*)\)?|;(.*)$")
_ASSIGN = re.compile(r"^#\s*(\d+|\[.*\])\s*=\s*(.+)$")
_MACRO_STATEMENT = re.compile(r"^(IF|WHILE|GOTO|END\d*|DO\d*)\b")


class _ExprError(Exception):
    pass


_FUNCS = {
    "SIN": lambda v: math.sin(math.radians(v)),
    "COS": lambda v: math.cos(math.radians(v)),
    "TAN": lambda v: math.tan(math.radians(v)),
    "ASIN": lambda v: math.degrees(math.asin(v)),
    "ACOS": lambda v: math.degrees(math.acos(v)),
    "ATAN": lambda v: math.degrees(math.atan(v)),
    "SQRT": math.sqrt,
    "SQR": math.sqrt,
    "ABS": abs,
    "ROUND": lambda v: float(math.floor(v + 0.5)) if v >= 0 else -float(math.floor(-v + 0.5)),
    "FIX": lambda v: float(math.floor(v)) if v >= 0 else -float(math.floor(-v)),
    "FUP": lambda v: float(math.ceil(v)) if v >= 0 else -float(math.ceil(-v)),
    "LN": math.log,
    "EXP": math.exp,
}


class _Expr:
    """Recursive-descent evaluator for Fanuc macro expressions.

    Returns None when an undefined (vacant) variable is involved.
    """

    _TOKEN = re.compile(r"\s*(?:(\d+\.?\d*|\.\d+)|([A-Z]+)|(.))")

    def __init__(self, text: str, variables: Dict[int, float]) -> None:
        self.toks: List[Tuple[str, str]] = []
        for num, word, ch in self._TOKEN.findall(text):
            if num:
                self.toks.append(("num", num))
            elif word:
                self.toks.append(("word", word))
            elif ch.strip():
                self.toks.append(("op", ch))
        self.i = 0
        self.vars = variables
        self.vacant = False

    def peek(self) -> Optional[Tuple[str, str]]:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self) -> Tuple[str, str]:
        tok = self.peek()
        if tok is None:
            raise _ExprError("unexpected end of expression")
        self.i += 1
        return tok

    def expect(self, op: str) -> None:
        tok = self.take()
        if tok != ("op", op):
            raise _ExprError(f"expected '{op}'")

    def evaluate(self) -> Optional[float]:
        value = self.sum()
        if self.peek() is not None:
            raise _ExprError("unexpected text after expression")
        return None if self.vacant else value

    def sum(self) -> float:
        value = self.product()
        while True:
            tok = self.peek()
            if tok == ("op", "+"):
                self.i += 1
                value += self.product()
            elif tok == ("op", "-"):
                self.i += 1
                value -= self.product()
            elif tok == ("word", "OR") or tok == ("word", "XOR"):
                raise _ExprError("logical operators are not supported")
            else:
                return value

    def product(self) -> float:
        value = self.unary()
        while True:
            tok = self.peek()
            if tok == ("op", "*"):
                self.i += 1
                value *= self.unary()
            elif tok == ("op", "/"):
                self.i += 1
                d = self.unary()
                if d == 0:
                    if self.vacant:
                        return 0.0
                    raise _ExprError("division by zero")
                value /= d
            elif tok == ("word", "MOD"):
                self.i += 1
                d = self.unary()
                value = math.fmod(value, d) if d else 0.0
            else:
                return value

    def unary(self) -> float:
        tok = self.peek()
        if tok == ("op", "-"):
            self.i += 1
            return -self.unary()
        if tok == ("op", "+"):
            self.i += 1
            return self.unary()
        return self.atom()

    def atom(self) -> float:
        kind, text = self.take()
        if kind == "num":
            return float(text)
        if kind == "op" and text == "[":
            value = self.sum()
            self.expect("]")
            return value
        if kind == "op" and text == "#":
            tok = self.peek()
            if tok is not None and tok[0] == "num":
                self.i += 1
                index = int(float(tok[1]))
            else:
                index = int(self.atom())
            if index == 0 or index not in self.vars:
                self.vacant = True
                return 0.0
            return self.vars[index]
        if kind == "word" and text in _FUNCS:
            self.expect("[")
            value = self.sum()
            self.expect("]")
            if text == "ATAN" and self.peek() == ("op", "/"):
                self.i += 1
                self.expect("[")
                x = self.sum()
                self.expect("]")
                return math.degrees(math.atan2(value, x)) % 360.0
            try:
                return float(_FUNCS[text](value))
            except (ValueError, OverflowError) as exc:
                raise _ExprError(str(exc)) from None
        raise _ExprError(f"unexpected '{text}'")


def evaluate_expression(text: str, variables: Dict[int, float]) -> Optional[float]:
    """Evaluate a macro expression; None if a vacant variable is used.

    Raises ValueError for malformed or unsupported expressions.
    """
    try:
        return _Expr(text.upper(), variables).evaluate()
    except _ExprError as exc:
        raise ValueError(str(exc)) from None


def split_comments(text: str) -> Tuple[str, List[str]]:
    """Split a line into its code and its comments."""
    comments: List[str] = []

    def keep(m: "re.Match[str]") -> str:
        comments.append(m.group(1) if m.group(1) is not None else (m.group(2) or ""))
        return " "

    return _COMMENT.sub(keep, text), comments


# ---------------------------------------------------------------- G codes

# G codes are keyed as round(value * 10): G1 -> 10, G43.4 -> 434.
_MOTION_G = {0: 0, 10: 1, 20: 2, 30: 3}
_CYCLE_G = {730, 740, 760, 810, 820, 830, 840, 850, 860, 870, 880, 890}
_WORK_G = {540, 550, 560, 570, 580, 590, 541, 1540}
_IGNORED_G = {
    90,  # G9 exact stop (one shot)
    50, 51, 80, 81,  # G5 / G5.1 high speed, G8 look-ahead
    150, 500, 690, 400, 490, 670,  # cancels: polar, scaling, rotation, comp, macro
    610, 611, 620, 630, 640,  # exact stop / tapping / cutting modes
    430, 440,  # tool length compensation (tip programming assumed)
    1030, 1870,  # Haas block buffering / smoothness
    131,  # polar interpolation cancel
}
_UNSUPPORTED_G = {
    120: "G12 circular pocket milling",
    130: "G13 circular pocket milling",
    160: "G16 polar coordinates",
    510: "G51 scaling",
    680: "G68 coordinate rotation",
    681: "G68.1 coordinate rotation",
    682: "G68.2 tilted work plane",
    434: "G43.4 tool center point control",
    435: "G43.5 tool center point control",
    330: "G33 threading",
    71: "G7.1 cylindrical interpolation",
    121: "G12.1 polar interpolation",
    920: "G92 coordinate system setting",
    1500: "G150 pocket milling",
}

# M codes applied before / after the motion of their block.
_M_BEFORE = {3, 4, 6, 7, 8, 19}
_M_AFTER = {0, 1, 2, 5, 9, 30, 98, 99, 97}


class Interpreter:
    """Stateful G-code interpreter. Use :func:`parse` for one-shot parsing."""

    def __init__(
        self,
        *,
        units: Optional[str] = None,
        default_units: str = "inch",
        rapid_mode: str = "linear",
        chord_tolerance: Optional[float] = None,
        arc_radius_tolerance: Optional[float] = None,
        home_z: Optional[float] = None,
        home_clearance: Optional[float] = None,
        block_delete: bool = False,
        tool_point: Optional[str] = None,
        tool_radius: Optional[Callable[[int], Optional[float]]] = None,
        comment_rules: Optional[Sequence[Tuple[str, str]]] = None,
        variable_rules: Optional[Dict[int, str]] = None,
        stock_top: Optional[float] = None,
        option_units: Optional[str] = None,
    ) -> None:
        if units is not None and units not in UNITS:
            raise ValueError(f"units must be one of {UNITS}")
        if rapid_mode not in ("linear", "dogleg"):
            raise ValueError("rapid_mode must be 'linear' or 'dogleg'")
        self.forced_units = units
        self.default_units = default_units
        self.rapid_mode = rapid_mode
        self.chord_tol_opt = chord_tolerance
        self.arc_tol_opt = arc_radius_tolerance
        self.home_z_opt = home_z
        self.home_clearance_opt = home_clearance
        self.block_delete = block_delete
        self.tool_point_opt = tool_point
        self.tool_radius = tool_radius
        rules = list(DEFAULT_COMMENT_RULES if comment_rules is None else comment_rules)
        self.rules = [(re.compile(p, re.I), t) for p, t in rules]
        self.variable_rules = dict(
            DEFAULT_VARIABLE_RULES if variable_rules is None else variable_rules
        )
        self.stock_top = stock_top
        self.option_units = option_units

    def _opt(self, value: Optional[float]) -> Optional[float]:
        """Convert a length option (given in option_units) to program units."""
        if value is None or self.option_units is None:
            return value
        units = self.units or self.default_units
        if units == self.option_units:
            return value
        return value * (MM_PER_INCH if self.option_units == "inch" else 1.0 / MM_PER_INCH)

    # ------------------------------------------------------------ driver

    def run(self, text: str, name: str = "program") -> Program:
        p = Program(text, name)
        self.p = p
        self._reset_state()
        percent = 0
        fast_match = _FAST.match
        pts_extend = p.points.extend
        flags_append = p.flags.append
        slot_append = p.tool_slot.append
        feed_append = p.feed.append
        speed_append = p.speed.append
        line_append = p.line.append
        line_edit = p.line_edit
        fast = False
        # modal state mirrored in locals while the fast path runs
        px = py = pz = 0.0
        motion = f = None
        spindle_flag = slot = 0
        speed = 0.0
        for li, raw in enumerate(p.lines):
            if fast:
                m = fast_match(raw)
                if m is not None:
                    g, xs, ys, zs, fs = m.groups()
                    try:
                        x = float(xs) if xs is not None else px
                        y = float(ys) if ys is not None else py
                        z = float(zs) if zs is not None else pz
                        if fs is not None:
                            fv = float(fs)
                    except ValueError:
                        m = None
                    if m is not None:
                        mode = motion if g is None else (1 if g == "1" else 0)
                        if mode is not None and mode <= 1:
                            if fs is not None:
                                f = fv
                            if mode == 0 or f or (xs is None and ys is None and zs is None):
                                motion = mode
                                if x != px or y != py or z != pz:
                                    pts_extend((x, y, z))
                                    if mode:
                                        flags_append(spindle_flag)
                                        feed_append(f)
                                        line_edit[li] = EDIT_SPLIT
                                    else:
                                        flags_append(RAPID | spindle_flag)
                                        feed_append(0.0)
                                    slot_append(slot)
                                    speed_append(speed)
                                    line_append(li)
                                    px, py, pz = x, y, z
                                continue
                # hand the line to the full interpreter with the state synced
                self.pos = [px, py, pz]
                self.motion = motion
                self.f = f
                fast = False
            s = raw.strip()
            if li == 0 and s.startswith("\ufeff"):  # byte order mark from Windows editors
                s = s[1:].strip()
            if not s:
                continue
            if s[0] == "%":
                percent += 1
                if percent > 1 and self.started:
                    self._end(li)
                    break
                continue
            skippable = s[0] == "/"
            if skippable:
                if self.block_delete:
                    continue
                s = s[1:]
            self.skippable = skippable
            try:
                self._line(li, s)
            except _ProgramEnd:
                self._end(li)
                break
            finally:
                self.skippable = False
            if self._update_fast():
                fast = True
                px, py, pz = self.pos
                motion = self.motion
                f = self.f
                spindle_flag = SPINDLE if self.spindle else 0
                speed = self.speed if self.spindle else 0.0
                slot = self.fast_slot
        if fast:
            self.pos = [px, py, pz]
            self.motion = motion
            self.f = f
        self._finish()
        return p

    def _update_fast(self) -> bool:
        """Enable the fast path while the modal state is plain."""
        self.fast = (
            self.started
            and self.absolute
            and self.cycle is None
            and self.feed_mode == 940
            and self.rapid_mode == "linear"
            and self.tip_offset == 0.0
            and not self.tip_dirty
            and self.tool is not None
            and self.scale == 1.0
            and not (self.local[0] or self.local[1] or self.local[2])
            and None not in self.pos
            and len(self.p.points) > 0
        )
        if self.fast:
            self.fast_slot = self._slot()
        return self.fast

    def _reset_state(self) -> None:
        self.units: Optional[str] = self.forced_units
        self.block_units: Optional[str] = None
        self.comment_units: Optional[str] = None
        self.started = False  # a motion block has been seen
        self.ended = False
        self.pos: List[Optional[float]] = [None, None, None]
        self.home_z: Optional[float] = None
        self.motion: Optional[int] = None
        self.plane = 170
        self.absolute = True
        self.arc_absolute = False
        self.feed_mode = 940
        self.f: Optional[float] = None  # units/min (G94) or units/rev (G95)
        self.f_inverse: Optional[float] = None  # G93
        self.speed = 0.0
        self.spindle = False
        self.tool: Optional[int] = None
        self.pending_tool: Optional[int] = None
        self.comment_tool: Optional[int] = None
        self.slots: Dict[int, int] = {}
        self.local = [0.0, 0.0, 0.0]  # G52
        self.cycle: Optional[int] = None
        self.cycle_initial: Optional[float] = None
        self.cycle_r: Optional[float] = None
        self.cycle_z: Optional[float] = None
        self.cycle_q: Optional[float] = None
        self.cycle_return = 980
        self.work_offsets: Dict[str, int] = {}
        self.vars: Dict[int, float] = {}
        self.msgs: Dict[Tuple[str, str], Message] = {}
        self.tip_offset = 0.0
        self.warned_start = False
        self.fast = False
        self.fast_slot = -1
        self.macro_block = False
        self.edit_code = EDIT_FEED
        self.tip_dirty = False  # recompute tip_offset before the next motion
        self.skippable = False  # the current block starts with '/'

    def _end(self, li: int) -> None:
        self.ended = True
        self.p.end_line = li

    # ------------------------------------------------------------ messages

    def msg(self, level: str, code: str, text: str, line: Optional[int]) -> None:
        key = (code, text)
        m = self.msgs.get(key)
        if m is None:
            self.msgs[key] = Message(level, code, text, line)
        else:
            m.count += 1

    # ------------------------------------------------------------ units

    @property
    def scale(self) -> float:
        if self.block_units is None or self.units is None or self.block_units == self.units:
            return 1.0
        return MM_PER_INCH if self.block_units == "inch" else 1.0 / MM_PER_INCH

    def _fix_units(self) -> None:
        if self.units is None:
            self.units = self.block_units or self.comment_units or self.default_units
            self.p.units = self.units

    def _set_block_units(self, units: str) -> None:
        self.block_units = units
        if self.units is None and not self.started:
            self.units = units
            self.p.units = units

    def _unit_factor(self) -> float:
        """Program units per inch."""
        return 1.0 if (self.units or self.default_units) == "inch" else MM_PER_INCH

    @property
    def chord_tol(self) -> float:
        if self.chord_tol_opt is not None:
            return self._opt(self.chord_tol_opt)
        return 0.0002 * self._unit_factor()

    @property
    def arc_tol(self) -> float:
        if self.arc_tol_opt is not None:
            return self._opt(self.arc_tol_opt)
        return 0.002 * self._unit_factor()

    # ------------------------------------------------------------ lines

    def _line(self, li: int, s: str) -> None:
        if "(" in s or ";" in s:
            code, comments = split_comments(s)
            for c in comments:
                self._comment(c, li)
        else:
            code = s
        code = code.strip().upper()
        if not code:
            return
        if "#" not in code and "[" not in code:
            tokens = []
            for letter, value, junk in _WORD.findall(code):
                if junk:
                    break
                tokens.append((letter, float(value)))
            else:
                if tokens:
                    self._block(tokens, li)
                return
        self._line_macro(code, li)

    def _line_macro(self, code: str, li: int) -> None:
        """Lines with macro variables, expressions or statements."""
        m = re.match(r"N\d+\s*", code)
        if m and m.end() < len(code):
            rest = code[m.end():]
            if rest[0] == "#" or _MACRO_STATEMENT.match(rest):
                code = rest
        if code[0] == "#":
            self._assignment(code, li)
            return
        if _MACRO_STATEMENT.match(code):
            self.msg("warning", "macro-flow",
                     "Macro control flow (IF/WHILE/GOTO) is not evaluated; skipped", li)
            return
        tokens = self._tokens_macro(code, li)
        if tokens:
            self.macro_block = True
            try:
                self._block(tokens, li)
            finally:
                self.macro_block = False

    def _comment(self, text: str, li: int) -> None:
        if not text.strip():
            return
        m = _FUSION_TOOL.match(text)
        if m:
            number = int(m.group(1))
            self._note(number, "diameter", float(m.group(2)), li)
            if m.group(3):
                self._note(number, "corner_radius", float(m.group(3)), li)
            if m.group(4):
                self._note(number, "tip_angle", float(m.group(4)), li)
            if m.group(5):
                self._note(number, "kind", m.group(5).strip(), li)
            return
        m = _MASTERCAM_TOOL.search(text)
        if m:
            self._note(int(m.group(1)), "diameter", float(m.group(2)), li)
            return
        for rx, target in self.rules:
            m = rx.search(text)
            if not m:
                continue
            try:
                value = _note_value(target, m.group(1))
            except ValueError:
                continue
            self._apply_note(target, value, li)

    def _apply_note(self, target: str, value, li: int) -> None:
        scope, fld = target.split(".", 1)
        if scope == "tool":
            self.tip_dirty = True  # a diameter may have arrived after M6
            if fld == "number":
                self.comment_tool = int(value)
                return
            number = self.comment_tool
            if number is None:
                number = self.pending_tool if self.pending_tool is not None else self.tool
            if number is None:
                number = 0
            if isinstance(value, float) and fld not in ("tip_angle",):
                value *= self._note_scale()
            self._note(number, fld, value, li)
        elif scope == "stock":
            self.p.stock_note[fld] = float(value) * self._note_scale()
            self.p.stock_lines[fld] = li
        elif scope == "program":
            if fld == "units":
                self.comment_units = "inch" if value.upper().startswith("INCH") else "mm"
            elif fld == "tool_point":
                self.p.tool_point = "center" if value.lower().startswith("cent") else "tip"
                self.tip_dirty = True

    def _note_scale(self) -> float:
        """Comment values are in the block units in force when they appear."""
        if self.units is None:
            return 1.0
        return self.scale

    def _note(self, number: int, fld: str, value, li: int) -> None:
        note = self.p.tool_notes.setdefault(number, ToolNote(number))
        note.values[fld] = value
        note.lines[fld] = li

    def _assignment(self, code: str, li: int) -> None:
        m = _ASSIGN.match(code)
        if not m:
            self.msg("error", "syntax", f"Cannot read macro statement '{code}'", li)
            return
        target, expr = m.group(1), m.group(2)
        try:
            index = int(target) if target.isdigit() else evaluate_expression(target[1:-1], self.vars)
            value = evaluate_expression(expr, self.vars)
        except ValueError as exc:
            self.msg("warning", "macro-expr", f"Macro expression not evaluated: {exc}", li)
            return
        if index is None:
            return
        index = int(index)
        if index >= 3000 and index < 3100 and value:  # #3000 = n raises an alarm
            self.msg("error", "alarm", f"Program raises alarm #{index} = {value:g}", li)
        if value is None:
            self.vars.pop(index, None)
            return
        self.vars[index] = value
        target_field = self.variable_rules.get(index)
        if target_field:
            self._apply_note(target_field, value, li)

    def _tokens_macro(self, code: str, li: int) -> Optional[List[Tuple[str, float]]]:
        tokens: List[Tuple[str, float]] = []
        i, n = 0, len(code)
        while i < n:
            ch = code[i]
            if ch.isspace():
                i += 1
                continue
            if not ("A" <= ch <= "Z"):
                self.msg("error", "syntax", f"Cannot read '{code}'", li)
                return None
            letter = ch
            i += 1
            while i < n and code[i].isspace():
                i += 1
            start = i
            if i < n and code[i] in "+-":
                i += 1
            while i < n and code[i].isspace():
                i += 1
            if i < n and code[i] == "[":
                depth = 0
                while i < n:
                    if code[i] == "[":
                        depth += 1
                    elif code[i] == "]":
                        depth -= 1
                        if depth == 0:
                            i += 1
                            break
                    i += 1
            elif i < n and code[i] == "#":
                i += 1
                while i < n and (code[i].isdigit() or code[i] == "["):
                    if code[i] == "[":
                        close = code.find("]", i)
                        i = n if close < 0 else close + 1
                    else:
                        i += 1
            else:
                while i < n and (code[i].isdigit() or code[i] == "."):
                    i += 1
            text = code[start:i]
            if not text.strip("+- "):
                self.msg("error", "syntax", f"Word {letter} has no value in '{code}'", li)
                return None
            try:
                value = evaluate_expression(text, self.vars)
            except ValueError as exc:
                self.msg("error", "macro-expr", f"Cannot evaluate {letter}{text}: {exc}", li)
                return None
            if value is None:
                self.msg("warning", "vacant", f"{letter}{text} uses an undefined variable; word ignored", li)
                continue
            tokens.append((letter, value))
        return tokens

    # ------------------------------------------------------------ blocks

    def _block(self, tokens: List[Tuple[str, float]], li: int) -> None:
        gs: List[int] = []
        ms: List[int] = []
        w: Dict[str, float] = {}
        for letter, value in tokens:
            if letter == "G":
                gs.append(int(round(value * 10)))
            elif letter == "M":
                ms.append(int(round(value)))
            elif letter in "NO":
                continue
            else:
                if letter in w and w[letter] != value:
                    self.msg("warning", "duplicate-word",
                             f"Duplicate {letter} word in one block; the last one is used", li)
                w[letter] = value

        if 650 in gs or 660 in gs:  # macro calls: not executed, words are arguments
            p = w.get("P")
            call = f" P{p:g}" if p is not None else ""
            self.msg("warning", "macro-call", f"Macro call G65{call} is not simulated", li)
            return
        if 100 in gs or 110 in gs:  # G10 data setting
            return

        # modal G codes
        nonmodal: Optional[int] = None
        for g in gs:
            if g == 200:
                self._set_block_units("inch")
            elif g == 210:
                self._set_block_units("mm")
        for g in gs:
            if g in _MOTION_G:
                self.motion = _MOTION_G[g]
                self.cycle = None
            elif g in _CYCLE_G:
                if self.cycle is None:
                    self.cycle_initial = self.pos[2]
                self.cycle = g
            elif g == 800:
                self.cycle = None
            elif g in (170, 180, 190):
                self.plane = g
            elif g == 900:
                self.absolute = True
            elif g == 910:
                self.absolute = False
            elif g == 901:
                self.arc_absolute = True
            elif g == 911:
                self.arc_absolute = False
            elif g in (930, 940, 950):
                self.feed_mode = g
            elif g in (410, 420):
                self.msg("warning", "cutter-comp",
                         "Cutter radius compensation (G41/G42) is not simulated; "
                         "the programmed path is treated as the tool center path", li)
            elif g in (980, 990):
                self.cycle_return = g
            elif g in _WORK_G:
                label = f"G{g / 10:g}" + (f" P{w['P']:g}" if g in (541, 1540) and "P" in w else "")
                self.work_offsets.setdefault(label, li)
            elif g in (40, 280, 300, 520, 530):
                nonmodal = g
            elif g in (200, 210) or g in _IGNORED_G:
                pass
            elif g in _UNSUPPORTED_G:
                self.msg("error", "unsupported",
                         f"{_UNSUPPORTED_G[g]} is not supported; the path after it may be wrong", li)
            else:
                self.msg("warning", "unknown-g", f"G{g / 10:g} is not recognized; ignored", li)

        if 430 in gs and "H" in w and self.tool is not None and int(w["H"]) != self.tool:
            self.msg("warning", "h-mismatch",
                     f"Tool length offset H{int(w['H'])} used with tool T{self.tool}", li)

        if "F" in w:
            if self.feed_mode == 930:
                self.f_inverse = w["F"]
            else:
                self.f = w["F"] * self.scale
        if "S" in w:
            self.speed = abs(w["S"])
        if "T" in w:
            self.pending_tool = int(w["T"])
            self.comment_tool = self.pending_tool

        for m in ms:
            if m in (3, 4):
                if not self.speed:
                    self.msg("warning", "no-speed", "Spindle started without a speed (S)", li)
                self.spindle = True
            elif m == 6:
                self._tool_change(li)
            elif m == 19:
                self.spindle = False

        # how the optimizer may edit this block's feed moves ('/' blocks are
        # locked: new lines would run even when the operator skips the block)
        if self.feed_mode != 940 or self.macro_block or self.scale != 1.0 or self.skippable:
            self.edit_code = EDIT_LOCKED
        elif (self.motion == 1 and self.cycle is None and nonmodal is None and not ms
              and all(g == 10 for g in gs) and set(w) <= {"X", "Y", "Z", "F"}
              and self.absolute and self.scale == 1.0 and self.tip_offset == 0.0
              and not (self.local[0] or self.local[1] or self.local[2])
              and self.rapid_mode == "linear"):
            self.edit_code = EDIT_SPLIT
        else:
            self.edit_code = EDIT_FEED

        # motion
        axes = "X" in w or "Y" in w or "Z" in w
        if nonmodal == 40:
            seconds = w.get("X", w.get("U"))
            if seconds is None and "P" in w:
                seconds = w["P"] / 1000.0
            self.p.dwell += max(0.0, seconds or 0.0)
        elif nonmodal in (280, 300):
            self._reference(w, li, nonmodal)
        elif nonmodal == 530:
            self._machine_move(w, li)
        elif nonmodal == 520:
            for i, a in enumerate("XYZ"):
                if a in w:
                    self.local[i] = w[a] * self.scale
        elif self.cycle is not None and (axes or "R" in w):
            self._cycle(w, li)
        elif axes or (self.motion in (2, 3) and any(k in w for k in "IJKR")):
            self._motion(w, li)

        for m in ms:
            if m == 5:
                self.spindle = False
            elif m in (2, 30):
                raise _ProgramEnd()
            elif m == 99:
                raise _ProgramEnd()
            elif m in (97, 98):
                self.msg("warning", "subprogram", f"Subprogram call M{m} is not simulated", li)
            elif m not in _M_BEFORE and m not in _M_AFTER:
                pass

    # ------------------------------------------------------------ tools

    def _tool_change(self, li: int) -> None:
        t = self.pending_tool
        if t is None:
            self.msg("warning", "m6-no-tool", "M6 without a tool number (T)", li)
            return
        self.tool = t
        self.comment_tool = t
        self.spindle = False
        self.p.tool_changes += 1
        # CAM headers often describe the tool after M6: resolve at the next move
        self.tip_dirty = True

    def _slot(self) -> int:
        t = self.tool
        if t is None:
            return -1
        slot = self.slots.get(t)
        if slot is None:
            slot = len(self.p.slot_tools)
            self.slots[t] = slot
            self.p.slot_tools.append(t)
        return slot

    def _update_tip_offset(self, li: int) -> None:
        point = self.tool_point_opt or self.p.tool_point
        if point != "center" or self.tool is None:
            self.tip_offset = 0.0
            return
        radius = self._opt(self.tool_radius(self.tool)) if self.tool_radius else None
        if radius is None:
            note = self.p.tool_notes.get(self.tool)
            if note and isinstance(note.values.get("diameter"), float):
                radius = note.values["diameter"] / 2.0
        if radius is None:
            self.msg("error", "center-no-radius",
                     f"Tool center programming needs the radius of T{self.tool}", li)
            radius = 0.0
        self.tip_offset = radius

    def _ensure_tool(self, li: int) -> None:
        if self.tool is not None:
            return
        if self.pending_tool is not None:
            self.tool = self.pending_tool
            self.msg("info", "assumed-tool",
                     f"No tool change before the first move; assuming T{self.tool} is in the spindle", li)
        else:
            self.tool = 0
            self.msg("info", "assumed-tool",
                     "No tool is selected; moves use the default tool T0", li)
        self.tip_dirty = True

    # ------------------------------------------------------------ geometry

    def _home(self) -> Optional[float]:
        if self.home_z is None:
            if self.home_z_opt is not None:
                self.home_z = self._opt(self.home_z_opt)
            else:
                top = self._opt(self.stock_top)
                if top is None:
                    top = self.p.stock_note.get("zmax")
                if top is not None:
                    clearance = self._opt(self.home_clearance_opt)
                    if clearance is None:
                        clearance = 1.0 * self._unit_factor()
                    self.home_z = top + clearance
        return self.home_z

    def _target(self, w: Dict[str, float], li: int) -> List[Optional[float]]:
        t = list(self.pos)
        sc = self.scale
        for i, a in enumerate("XYZ"):
            if a not in w:
                continue
            v = w[a] * sc
            if self.absolute:
                t[i] = v + self.local[i]
            elif self.pos[i] is None:
                self.msg("error", "incremental-unknown",
                         "Incremental move from an unknown position", li)
                t[i] = v
            else:
                t[i] = self.pos[i] + v
        return t

    def _start_known(self, target: List[Optional[float]], li: int) -> bool:
        """Fill unknown start coordinates; False while the position is unknown."""
        pos = self.pos
        if pos[2] is None and target[2] is not None:
            home = self._home()
            if home is not None and home > target[2]:
                pos[2] = home
        for i in range(3):
            if pos[i] is None and target[i] is not None:
                pos[i] = target[i]
        if None in pos:
            return False
        if not self.warned_start:
            self.warned_start = True
            z = pos[2]
            self.msg("info", "start-position",
                     f"The machine position before the first move is unknown; the tool is assumed "
                     f"to come down from Z{z:.4g} above the first programmed X/Y", li)
        return True

    def _emit(self, target: List[Optional[float]], flags: int, feed: float, li: int) -> None:
        if None in self.pos and not self._start_known(target, li):
            return
        if None in target:
            target = [self.pos[i] if target[i] is None else target[i] for i in range(3)]
        x, y, z = target
        zp = z - self.tip_offset
        p = self.p
        pts = p.points
        if not pts:
            pts.extend((self.pos[0], self.pos[1], self.pos[2] - self.tip_offset))
        else:
            sx, sy, sz = self.pos[0], self.pos[1], self.pos[2] - self.tip_offset
            if pts[-3] != sx or pts[-2] != sy or pts[-1] != sz:
                # tool data changed the tip position: connect with a rapid
                pts.extend((sx, sy, sz))
                self._append(RAPID | (SPINDLE if self.spindle else 0), 0.0, li)
        self.pos = [x, y, z]
        if pts[-3] == x and pts[-2] == y and pts[-1] == zp:
            return
        pts.extend((x, y, zp))
        self._append(flags | (SPINDLE if self.spindle else 0), feed, li)

    def _append(self, flags: int, feed: float, li: int) -> None:
        p = self.p
        if not flags & RAPID:
            p.line_edit[li] = self.edit_code
        p.flags.append(flags)
        p.tool_slot.append(self._slot())
        p.feed.append(feed)
        p.speed.append(self.speed if self.spindle else 0.0)
        p.line.append(li)

    def _begin_motion(self, li: int) -> None:
        if not self.started:
            self.started = True
            self._fix_units()
        self._ensure_tool(li)
        if self.tip_dirty:
            self.tip_dirty = False
            self._update_tip_offset(li)

    def _feed_rate(self, length: float, li: int) -> float:
        if self.feed_mode == 930:
            if not self.f_inverse:
                self.msg("error", "no-feed", "Inverse time feed move without F", li)
                return 0.0
            return length * self.f_inverse
        if not self.f:
            self.msg("error", "no-feed", "Feed move without a feed rate (F)", li)
            return 0.0
        if self.feed_mode == 950:
            if not self.speed:
                self.msg("error", "no-feed", "Feed per revolution (G95) without spindle speed", li)
                return 0.0
            return self.f * self.speed
        return self.f

    def _motion(self, w: Dict[str, float], li: int) -> None:
        self._begin_motion(li)
        mode = self.motion
        if mode is None:
            mode = self.motion = 0
            self.msg("warning", "no-motion-mode", "Axis words before any G0/G1; G0 assumed", li)
        target = self._target(w, li)
        if mode == 0:
            self._rapid(target, li)
        elif mode == 1:
            length = self._distance(target)
            self._emit(target, 0, self._feed_rate(length, li), li)
        else:
            self._arc(mode == 2, w, target, li)

    def _distance(self, target: List[Optional[float]]) -> float:
        d = 0.0
        for i in range(3):
            if target[i] is not None and self.pos[i] is not None:
                d += (target[i] - self.pos[i]) ** 2
        return math.sqrt(d)

    def _rapid(self, target: List[Optional[float]], li: int, extra: int = 0) -> None:
        if self.rapid_mode == "dogleg" and None not in self.pos:
            full = [self.pos[i] if target[i] is None else target[i] for i in range(3)]
            for leg in dogleg(self.pos, full):
                self._emit(list(leg), RAPID | extra, 0.0, li)
        else:
            self._emit(target, RAPID | extra, 0.0, li)

    # ------------------------------------------------------------ arcs

    def _arc(self, cw: bool, w: Dict[str, float], target: List[Optional[float]], li: int) -> None:
        if None in self.pos and not self._start_known(target, li):
            return
        start = list(self.pos)
        end = [start[i] if target[i] is None else target[i] for i in range(3)]
        if self.plane == 170:
            a1, a2, ax, o1, o2 = 0, 1, 2, "I", "J"
        elif self.plane == 180:
            a1, a2, ax, o1, o2 = 2, 0, 1, "K", "I"
        else:
            a1, a2, ax, o1, o2 = 1, 2, 0, "J", "K"
        sc = self.scale
        s1, s2, e1, e2 = start[a1], start[a2], end[a1], end[a2]
        chord = math.hypot(e1 - s1, e2 - s2)
        same = chord <= 1e-9 * max(1.0, abs(s1) + abs(s2))
        if "R" in w and not any(k in w for k in (o1, o2)):
            r = w["R"] * sc
            if same:
                self.msg("error", "arc", "R-format arc with identical start and end point", li)
                self._emit(end, 0, self._feed_rate(0.0, li), li)
                return
            half = chord / 2.0
            if abs(r) < half:
                if half - abs(r) > self.arc_tol:
                    self.msg("error", "arc", f"Arc radius R{abs(r):g} is smaller than half the chord", li)
                r = math.copysign(half, r)
            h = math.sqrt(max(0.0, r * r - half * half))
            m1, m2 = (s1 + e1) / 2.0, (s2 + e2) / 2.0
            n1, n2 = -(e2 - s2) / chord, (e1 - s1) / chord  # left normal of the chord
            # CCW minor arcs have their center on the left; CW and R<0 flip it
            side = 1.0 if (not cw) == (r > 0) else -1.0
            c1, c2 = m1 + side * h * n1, m2 + side * h * n2
        else:
            if o1 not in w and o2 not in w:
                self.msg("error", "arc", "Arc without center (I/J/K) or radius (R); moved in a line", li)
                self._emit(end, 0, self._feed_rate(chord, li), li)
                return
            i1, i2 = w.get(o1, 0.0) * sc, w.get(o2, 0.0) * sc
            if self.arc_absolute:
                c1, c2 = i1 + self.local[a1], i2 + self.local[a2]
            else:
                c1, c2 = s1 + i1, s2 + i2
        rs = math.hypot(s1 - c1, s2 - c2)
        re_ = math.hypot(e1 - c1, e2 - c2)
        if rs <= 1e-12:
            self.msg("error", "arc", "Arc with zero radius; moved in a line", li)
            self._emit(end, 0, self._feed_rate(chord, li), li)
            return
        if abs(rs - re_) > self.arc_tol:
            self.msg("error", "arc",
                     f"Arc end point is off the circle by {abs(rs - re_):.4g}", li)
        th_s = math.atan2(s2 - c2, s1 - c1)
        th_e = math.atan2(e2 - c2, e1 - c1)
        sweep = (th_s - th_e) if cw else (th_e - th_s)
        sweep %= 2.0 * math.pi
        if same:
            sweep = 2.0 * math.pi
        elif sweep < 1e-12:
            sweep = 0.0
        direction = -1.0 if cw else 1.0
        tol = self.chord_tol
        r_max = max(rs, re_)
        if tol < r_max:
            step = 2.0 * math.acos(1.0 - tol / r_max)
            n = max(1, min(20000, int(math.ceil(sweep / step))))
        else:
            n = max(1, int(math.ceil(sweep / (math.pi / 2))))
        pts: List[List[float]] = []
        for k in range(1, n + 1):
            if k == n:
                pts.append(end)
                break
            f = k / n
            th = th_s + direction * sweep * f
            rr = rs + (re_ - rs) * f
            q = [0.0, 0.0, 0.0]
            q[a1] = c1 + rr * math.cos(th)
            q[a2] = c2 + rr * math.sin(th)
            q[ax] = start[ax] + (end[ax] - start[ax]) * f
            pts.append(q)
        length = 0.0
        prev = start
        for q in pts:
            length += math.sqrt(sum((q[i] - prev[i]) ** 2 for i in range(3)))
            prev = q
        feed = self._feed_rate(length, li)
        for q in pts:
            self._emit(q, ARC, feed, li)

    # ------------------------------------------------------------ cycles

    def _cycle(self, w: Dict[str, float], li: int) -> None:
        self._begin_motion(li)
        g = self.cycle
        sc = self.scale
        if None in self.pos:
            target = self._target({k: w[k] for k in "XY" if k in w}, li)
            if not self._start_known(target, li):
                self.msg("error", "cycle", "Canned cycle from an unknown position", li)
                return
        if self.cycle_initial is None:
            self.cycle_initial = self.pos[2]
        if "R" in w:
            self.cycle_r = w["R"] * sc
        if "Z" in w:
            self.cycle_z = w["Z"] * sc
        if "Q" in w:
            self.cycle_q = abs(w["Q"]) * sc
        if self.cycle_z is None:
            self.msg("error", "cycle", "Canned cycle without a depth (Z)", li)
            return
        initial = self.cycle_initial
        if self.absolute:
            r_level = (self.cycle_r + self.local[2]) if self.cycle_r is not None else initial
            bottom = self.cycle_z + self.local[2]
        else:
            r_level = initial + (self.cycle_r or 0.0)
            bottom = r_level + self.cycle_z
        if self.cycle_r is None:
            self.msg("warning", "cycle", "Canned cycle without R; the initial level is used", li)
        repeats = w.get("K", w.get("L"))
        repeats = 1 if repeats is None else int(repeats)
        if repeats <= 0:
            return  # K0 / L0: remember the cycle data, do not position or drill
        for _ in range(repeats):
            xy = {k: w[k] for k in "XY" if k in w}
            target = self._target(xy, li)
            self._rapid([target[0], target[1], self.pos[2]], li)
            self._rapid([target[0], target[1], r_level], li)
            feed = self._feed_rate(abs(r_level - bottom), li)
            self._emit([target[0], target[1], bottom], CYCLE, feed, li)
            if g in (730, 830) and self.cycle_q:
                pecks = max(0, int(math.ceil(abs(r_level - bottom) / self.cycle_q)) - 1)
                depth = abs(r_level - bottom)
                # G83 returns to R between pecks, G73 only backs off a little
                self.p.cycle_rapid += 2 * pecks * (depth / 2 if g == 830 else 0.02 * self._unit_factor())
            if "P" in w:
                self.p.dwell += w["P"] / 1000.0
            back = initial if self.cycle_return == 980 else r_level
            if g in (740, 840, 850, 880, 890):  # tap / bore out at feed
                self._emit([target[0], target[1], back], CYCLE, feed, li)
            else:
                self._rapid([target[0], target[1], back], li, CYCLE)
            if self.absolute:
                break  # repeating an absolute hole drills the same spot

    # ------------------------------------------------------------ references

    def _reference(self, w: Dict[str, float], li: int, g: int) -> None:
        """G28 / G30: rapid to the intermediate point, then Z to home."""
        self._begin_motion(li)
        mid = self._target(w, li)
        if self.absolute and any(a in w for a in "XY"):
            self.msg("info", "g28-absolute",
                     f"G{g // 10} in absolute mode passes through the intermediate point in work coordinates", li)
        self._rapid(mid, li)
        if "Z" in w:
            home = self._home()
            if home is not None and None not in self.pos:
                self._rapid([self.pos[0], self.pos[1], max(home, self.pos[2])], li)
        if "X" in w or "Y" in w:
            self.msg("info", "reference-xy",
                     "X/Y reference return is not simulated (machine home is unknown)", li)

    def _machine_move(self, w: Dict[str, float], li: int) -> None:
        """G53: only the classic 'retract to machine Z home' can be simulated."""
        self._begin_motion(li)
        if "Z" in w:
            home = self._home()
            if home is not None and None not in self.pos:
                self._rapid([self.pos[0], self.pos[1], max(home, self.pos[2])], li)
        if "X" in w or "Y" in w:
            self.msg("info", "machine-xy",
                     "G53 X/Y moves are not simulated (machine coordinates are unknown)", li)

    # ------------------------------------------------------------ finish

    def _finish(self) -> None:
        p = self.p
        if self.units is None:
            self._fix_units()
        p.units = self.units or self.default_units
        p.home_z = self.home_z
        if len(self.work_offsets) > 1:
            names = ", ".join(sorted(self.work_offsets))
            self.msg("warning", "work-offsets",
                     f"Several work offsets are used ({names}); they are simulated as one setup",
                     min(self.work_offsets.values()))
        if not p.n_moves:
            self.msg("warning", "no-moves", "The program has no motion", None)
        p.messages = sorted(self.msgs.values(), key=lambda m: (m.line is None, m.line or 0))


class _ProgramEnd(Exception):
    pass


def dogleg(start: Sequence[float], end: Sequence[float]) -> List[Tuple[float, float, float]]:
    """Legs of a non-interpolated rapid: every axis runs at the same speed
    until it arrives, so the path bends each time one axis finishes."""
    delta = [end[i] - start[i] for i in range(3)]
    order = sorted(range(3), key=lambda i: abs(delta[i]))
    legs = []
    cur = list(start)
    done = 0.0
    for idx in order:
        travel = abs(delta[idx]) - done
        if travel <= 0:
            continue
        for i in range(3):
            if abs(delta[i]) > done:
                cur[i] += math.copysign(travel, delta[i])
        done = abs(delta[idx])
        legs.append((cur[0], cur[1], cur[2]))
    if legs:
        legs[-1] = (end[0], end[1], end[2])
    return legs


def parse(text: str, name: str = "program", **options) -> Program:
    """Interpret a program. Options are passed to :class:`Interpreter`."""
    return Interpreter(**options).run(text, name)


def iter_moves(program: Program) -> Iterable[Tuple[int, Tuple[float, float, float], Tuple[float, float, float]]]:
    for i in range(program.n_moves):
        yield i, program.point(i), program.point(i + 1)
