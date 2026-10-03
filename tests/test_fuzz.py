"""Seeded random programs: the interpreter, checker and optimizer must never
crash, the fast path must agree with the full interpreter, and optimized
programs must follow the original path."""

import math
import random

import pytest

from emucraft.check import parse_program, run_check
from emucraft.gcode import RAPID
from emucraft.model import Config
from emucraft.optimize import OptimizeSpec, optimize, verify_same_path

HEADER = """%
O1234 (FUZZ)
( MIN X: 0.)
( MIN Y: 0.)
( MIN Z: -1.)
( MAX X: 4.)
( MAX Y: 4.)
( MAX Z: 0.)
(T1 D=0.25 CR=0. - flat end mill)
(T2 D=0.25 CR=0.125 - ball end mill)
(T3 D=0.375 CR=0.05 - bull nose end mill)
(T4 D=0.2 TAPER=118deg - drill)
G20 G17 G40 G80 G90
"""


def num(rng, lo, hi):
    return f"{rng.uniform(lo, hi):.4f}"


def random_program(seed, lines=160):
    rng = random.Random(seed)
    out = [HEADER]
    x = y = 2.0
    z = 0.5
    tool = None
    for _ in range(lines):
        r = rng.random()
        if tool is None or r < 0.03:
            tool = rng.choice([1, 2, 3, 4])
            out.append(f"T{tool} M6\nS{rng.randint(2000, 12000)} M3\nG0 X{x:.4f} Y{y:.4f}\nG43 H{tool} Z0.5")
            z = 0.5
            continue
        if r < 0.20:
            x, y = float(num(rng, 0.2, 3.8)), float(num(rng, 0.2, 3.8))
            out.append(f"G0 X{x:.4f} Y{y:.4f}")
        elif r < 0.30:
            z = float(num(rng, -0.4, 0.5))
            out.append(f"G1 Z{z:.4f} F{rng.choice([10, 20, 30])}.")
        elif r < 0.55:
            x, y = float(num(rng, 0.2, 3.8)), float(num(rng, 0.2, 3.8))
            words = f"X{x:.4f} Y{y:.4f}"
            if rng.random() < 0.3:
                z = float(num(rng, -0.4, 0.2))
                words += f" Z{z:.4f}"
            prefix = rng.choice(["G1 ", "", "N10 G01 "])
            feed = f" F{rng.choice([20, 40, 60, 750])}." if rng.random() < 0.3 else ""
            out.append(prefix + words + feed)
        elif r < 0.68:
            # an arc ending on its circle: pick a centre offset and a sweep
            i, j = float(num(rng, -0.6, 0.6)), float(num(rng, -0.6, 0.6))
            cx, cy = x + i, y + j
            rad = math.hypot(i, j)
            a0 = math.atan2(y - cy, x - cx)
            a1 = a0 + rng.uniform(-3, 3)
            ex, ey = cx + rad * math.cos(a1), cy + rad * math.sin(a1)
            if not (0 < ex < 4 and 0 < ey < 4) or rad < 0.05:
                continue
            g = rng.choice(["G2", "G3"])
            zword = f" Z{float(num(rng, -0.3, 0.2)):.4f}" if rng.random() < 0.2 else ""
            if rng.random() < 0.3:
                out.append(f"{g} X{ex:.4f} Y{ey:.4f}{zword} R{rad:.4f} F25.")
            else:
                out.append(f"{g} X{ex:.4f} Y{ey:.4f}{zword} I{i:.4f} J{j:.4f} F25.")
            x, y = round(ex, 4), round(ey, 4)
        elif r < 0.73:
            out.append(f"G81 G{rng.choice([98, 99])} X{num(rng, 0.3, 3.7)} Y{num(rng, 0.3, 3.7)} "
                       f"Z{num(rng, -0.8, -0.1)} R0.1 F8.\nX{num(rng, 0.3, 3.7)}\nG80\nG0 Z0.5")
            z = 0.5
        elif r < 0.78:
            out.append(f"G91 G1 X{num(rng, -0.3, 0.3)} Y{num(rng, -0.3, 0.3)} F30.\nG90")
            out.append(f"G0 X{x:.4f} Y{y:.4f}")
        elif r < 0.82:
            out.append(rng.choice(["M5", "M3", "M8", "M9", "(A COMMENT)", "G4 P100", "#101 = 0.5",
                                   "G0 Z[#101 + 0.2]", "/G1 X1.5 Y1.5 F20.", "G93 G1 X2. F4.\nG94"]))
            out.append(f"G0 X{x:.4f} Y{y:.4f} Z{max(z, 0.2):.4f}")
        else:
            z = 0.5
            out.append("G0 Z0.5")
    out.append("G0 Z1.\nM30\n%\n")
    return "\n".join(out)


@pytest.mark.parametrize("seed", range(6))
def test_random_programs(seed):
    text = random_program(seed)
    program = parse_program(text, f"fuzz{seed}")
    assert program.n_moves > 50
    lower = parse_program(text.lower(), "lower")
    for name in ("points", "flags", "feed", "line", "tool_slot", "speed", "line_edit"):
        assert list(getattr(program, name)) == list(getattr(lower, name)), name

    config = Config()
    config.check.resolution = 0.02
    config.check.link_feed = 700
    result = run_check(program, config)
    assert result.grid is not None
    for issue in result.issues:
        assert 0 <= issue.first_line <= issue.last_line < len(program.lines)
        assert issue.first_move <= issue.last_move < program.n_moves

    spec = OptimizeSpec.from_dict({"material": "inconel", "resolution": 0.02})
    optimized = optimize(program, config, spec)
    assert optimized.verified, [m.text for m in optimized.messages]
    again = parse_program(optimized.text, "again")
    assert verify_same_path(program, again, 3e-4) is None
    feed_moves = [i for i in range(again.n_moves) if not again.flags[i] & RAPID]
    assert all(again.feed[i] >= 0 for i in feed_moves)
