"""Shared helpers for the test suite."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
DATA = Path(__file__).resolve().parent / "data"


def moves(program):
    """[(start, end, flags, feed, 1-based line)] of an interpreted program."""
    return [
        (program.point(i), program.point(i + 1), program.flags[i], program.feed[i], program.line[i] + 1)
        for i in range(program.n_moves)
    ]


def ends(program):
    """End point of every move."""
    return [program.point(i + 1) for i in range(program.n_moves)]


def codes(program_or_messages, level=None):
    messages = getattr(program_or_messages, "messages", program_or_messages)
    return [m.code for m in messages if level is None or m.level == level]


def close(p, q, tol=1e-9):
    return len(p) == len(q) and all(abs(a - b) <= tol for a, b in zip(p, q))
