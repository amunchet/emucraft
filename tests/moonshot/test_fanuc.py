"""Step 5: the FANUC program reads back as written, puts every contact on the drum, and never cuts into the part."""
import re

import numpy as np
import pytest
import trimesh
from scipy.spatial.transform import Rotation, Slerp

from moonshot.fanuc import _viterbi, build, frame, interpolate, write
from moonshot.pipeline import run
from moonshot.reach import probe_points

QUIET = {"log": lambda *_: None}
PART = [1.5, 0.5, 0.25, 1.5, 2.5, 0.25, 0.0, 0.5, 1.0]  # three points: a frame turned 90 degrees, off the part's origin


def turn(axis, deg):
    """Rotation about a coordinate axis (0, 1, 2), degrees."""
    c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
    i, j = [k for k in range(3) if k != axis]
    out = np.eye(3)
    out[i, i] = out[j, j] = c
    out[i, j], out[j, i] = -s, s
    if axis == 1:
        out[i, j], out[j, i] = s, -s
    return out


def wpr(w, p, r):
    """FANUC W, P, R in degrees: turns about the fixed X, then Y, then Z axes."""
    return turn(2, r) @ turn(1, p) @ turn(0, w)


def read_ls(path):
    """Positions (mm, degrees) and /MN lines of a written program, checking the LS layout on the way."""
    head, rest = path.read_text().split("/MN\n")
    body, pos = rest.split("/POS\n")
    lines = body.splitlines()
    assert head.startswith("/PROG  ") and "/ATTR\n" in head and f"LINE_COUNT\t= {len(lines)};" in head
    assert pos.endswith("/END\n") and [int(line[:4]) for line in lines] == list(range(1, len(lines) + 1))
    entries = re.findall(r"P\[(\d+)\]\{\n   GP1:\n\tUF : \d+, UT : \d+,\t\tCONFIG : '[^']*',\n\tX = (.+)  mm,\tY = (.+)  mm,"
                         r"\tZ = (.+)  mm,\n\tW = (.+) deg,\tP = (.+) deg,\tR = (.+) deg\n\};", pos)
    motions = [line for line in lines if line[5] in "JL"]
    assert all(re.fullmatch(r" *\d+:(J P\[\d+\] 20% FINE|L P\[\d+\] \d+msec (FINE|CNT100( RTCP)?))    ;", m) for m in motions)
    assert all(re.fullmatch(r" *\d+:  (!.*|UFRAME_NUM=\d+|UTOOL_NUM=\d+) ;", line) for line in lines if line not in motions)
    assert [int(e[0]) for e in entries] == list(range(1, len(entries) + 1))
    assert [int(re.search(r"P\[(\d+)\]", m)[1]) for m in motions] == list(range(1, len(entries) + 1))
    return np.array([[float(v) for v in e[1:]] for e in entries]), lines


def read_csv(path):
    """program.csv as named float columns, plus each point's kind."""
    header, *rows = path.read_text().splitlines()
    names, cells = header.split(","), [row.split(",") for row in rows]
    values = np.array([[float(v) if v else np.nan for v in row[:-1]] for row in cells])
    return {k: values[:, i] for i, k in enumerate(names[:-1])}, np.array([row[-1] for row in cells])


@pytest.fixture(scope="module")
def planned():
    return run("samples/pocket_block.stl", spacing=0.03, track=True, **QUIET)


@pytest.fixture(scope="module")
def program(planned):
    return build(planned, frame(PART), **QUIET)


def test_frame_follows_fanuc():
    """Three points give FANUC's frame (X toward the second, the third on the +Y side); six are X Y Z W P R."""
    o, px, py = np.array([1.0, 2.0, 3.0]), np.array([2.0, 3.0, 3.0]), np.array([0.0, 4.0, 3.5])
    out = frame(np.concatenate([o, px, py]))
    rot = out[:3, :3]
    assert np.allclose(out[:3, 3], o) and np.allclose(rot[:, 0], (px - o) / np.linalg.norm(px - o))
    assert (py - o) @ rot[:, 1] > 0 and abs((py - o) @ rot[:, 2]) < 1e-12
    assert np.allclose(rot.T @ rot, np.eye(3)) and np.isclose(np.linalg.det(rot), 1.0)
    assert np.allclose(frame([1, 2, 3, 30, -20, 45])[:3, :3], wpr(30, -20, 45))
    # In the model file's units, on a model the planner turned with --rotate 90 0 0.
    out = frame([25.4, 0, 0, 0, 0, 90], "mm", (90, 0, 0))
    assert np.allclose(out[:3, 3], turn(0, 90) @ [1, 0, 0]) and np.allclose(out[:3, :3], turn(0, 90) @ turn(2, 90))
    with pytest.raises(ValueError):
        frame([0, 0, 0, 1, 0, 0, 2, 0, 0])  # on one line
    with pytest.raises(ValueError):
        frame([0, 0])


def test_interpolate_moves_like_a_linear_move():
    """Rotation about one fixed axis; the drum's origin or the part frame's moves in a straight line."""
    rng = np.random.default_rng(1)
    r0, r1 = Rotation.random(2, random_state=rng).as_matrix()
    o0, o1, origin = rng.normal(size=(3, 3))
    t = np.linspace(0, 1, 11)
    slerp = Slerp([0, 1], Rotation.from_matrix([r0, r1]))(t).as_matrix()
    rot, org = interpolate(r0, o0, r1, o1, t)
    assert np.allclose(rot, slerp, atol=1e-9) and np.allclose(org, o0 + t[:, None] * (o1 - o0))
    rot, org = interpolate(r0, o0, r1, o1, t, origin)
    held = np.einsum("tji,tj->ti", rot, origin - org)  # the part frame's origin, seen from the drum
    assert np.allclose(rot, slerp, atol=1e-9) and np.allclose(held, held[0] + t[:, None] * (held[-1] - held[0]))
    assert np.allclose(org[[0, -1]], [o0, o1])


def test_axis_neither_swings_nor_sweeps_through_collisions():
    """A turn cap stops the axis following pulls that alternate; it never turns through blocked angles unless forced."""
    m, nb, step = 40, 180, np.pi / 180
    delta = np.zeros(m - 1)

    def toward(deg):
        """Step 3's grain pull, as hard as at its default, toward an angle per node."""
        return 100 * np.cos(2 * np.radians(deg)), 100 * np.sin(2 * np.radians(deg))

    # Every angle free, pulled to 0 and 40 degrees in turn: uncapped the axis follows, capped it holds.
    pull = toward(np.where(np.arange(m) % 2, 40.0, 0.0))
    arcs = np.ones((m, nb), np.int64)
    swung = _viterbi(arcs, *pull, delta, np.full(m - 1, 10.0), 1e4, step)[0]
    held = _viterbi(arcs, *pull, delta, np.full(m - 1, np.radians(2.0)), 1e4, step)[0]
    assert np.abs(np.diff(swung)).min() >= 35 and np.abs(np.diff(held)).max() <= 2
    # Every other point allows only 40-60 degrees and all are pulled to 0: going level in between would sweep the
    # drum through the blocked angles, so the axis stays at 40.
    arcs[1::2] = -1
    arcs[1::2, 40:60] = 1
    bins, crossed = _viterbi(arcs, *toward(np.zeros(m)), delta, np.full(m - 1, 10.0), 1e4, step)
    assert (bins == 40).all() and not crossed.any()
    # Two points whose free angles do not meet: the turn is flagged, for the stroke to be cut there.
    arcs = -np.ones((2, nb), np.int64)
    arcs[0, :10], arcs[1, 90:100] = 1, 1
    assert _viterbi(arcs, *toward(np.zeros(2)), np.zeros(1), np.full(1, 10.0), 1e4, step)[1].all()


def test_program_puts_every_contact_on_the_drum(planned, program, tmp_path):
    """Read back from the .LS, every point touches where it should, one offset per pass, and the check passes."""
    stats, tolerance = program["stats"], planned["params"]["tolerance"]
    assert stats["deepest_in"] <= tolerance + 0.001 + 1e-9 and stats["holder_clearance_in"] >= -tolerance
    assert stats["off_part_clearance_in"] >= 0.1 and stats["fit_gap_in"] <= 0.001 and stats["fit_angle_deg"] <= 0.5
    part = frame(PART)
    pos, lines = read_ls(write(program, part, tmp_path))
    csv, kind = read_csv(tmp_path / "program.csv")
    assert kind[0] == "approach" and kind[1] == "touch" and kind[-1] == "retract" and "UFRAME_NUM=1" in lines[3]
    count = np.unique(csv["pass"]).size
    assert [line[7:-2] for line in lines if "!Pass" in line] == [f"!Pass {k} of {count}" for k in range(1, count + 1)]
    off = np.flatnonzero(kind == "lift")
    assert (kind[off + 1] == "transit").all() and (kind[off + 2] == "touch").all()
    # The part frame in the drum frame, from X Y Z W P R: each contact (in the part frame) lands on the drum's
    # contact line at its offset, its normal along +Z (into the drum) and its drum axis along +X.
    rot = np.array([wpr(*v) for v in pos[:, 3:]])
    where = np.einsum("kij,kj->ki", rot, np.column_stack([csv["contact_x"], csv["contact_y"], csv["contact_z"]]))
    where += pos[:, :3] / 25.4
    on = np.isfinite(csv["track"])
    assert np.isin(kind[on], ["touch", "sand", "bridge"]).all() and on.sum() > 0.9 * len(kind)
    assert np.allclose(where[on], np.column_stack([csv["track"][on], np.zeros((on.sum(), 2))]), atol=2e-4)
    for name, unit in (("normal", [0, 0, 1]), ("axis", [1, 0, 0])):
        v = np.einsum("kij,kj->ki", rot, np.column_stack([csv[f"{name}_{c}"] for c in "xyz"]))
        assert np.linalg.norm(np.cross(v, unit), axis=1).max() < 1e-4 and (v @ unit > 0).all()
    for k in np.unique(csv["pass"][on]):
        assert np.ptp(csv["track"][on & (csv["pass"] == k)]) < 1e-9
    # Off the part, the pose keeps the orientation of the contact it leaves.
    assert np.allclose(pos[off, 3:], pos[off - 1, 3:], atol=2e-3)
    # The contact points are on the part's surface (in the part frame).
    mesh = planned["mesh"].copy()
    mesh.apply_transform(np.linalg.inv(part))
    gap = trimesh.proximity.closest_point(mesh, np.column_stack([csv[f"contact_{c}"] for c in "xyz"])[on])[1]
    assert gap.max() < 0.003


def test_robot_moves_never_cut_into_the_part(planned, program, tmp_path):
    """Simulating the controller's straight moves between the written positions, the drum never digs in."""
    part = frame(PART)
    pos, _ = read_ls(write(program, part, tmp_path))
    rots = Rotation.from_matrix([wpr(*v) for v in pos[:, 3:]])
    probes = (probe_points(planned["mesh"], planned["params"]["probe_spacing"]) - part[:3, 3]) @ part[:3, :3]
    radius, half = planned["params"]["radius"], planned["params"]["length"] / 2
    deepest = -np.inf
    for i in range(1, len(pos)):
        # A robot holding the part: the part frame's origin moves straight through the drum frame.
        t = np.linspace(0, 1, 5)[1:]
        rot = Slerp([0, 1], rots[[i - 1, i]])(t).as_matrix()
        shift = (pos[i - 1, :3] + t[:, None] * (pos[i, :3] - pos[i - 1, :3])) / 25.4
        local = np.einsum("tij,qj->tqi", rot, probes) + shift[:, None]
        along = np.abs(local[..., 0]) <= half
        depth = radius - np.hypot(local[..., 1], local[..., 2] - radius)
        deepest = max(deepest, depth[along].max(initial=-np.inf))
    assert deepest <= planned["params"]["tolerance"] + 0.001 + 1e-4  # plus the rounding of 0.001 mm and deg


def test_holding_the_drum_inverts_the_program(planned, tmp_path):
    """The same moves written for a robot holding the drum are the inverse poses; Remote TCP marks the sanding moves."""
    part = frame(PART)
    program = build(planned, part, model="rtcp", **QUIET)
    held_part, lines = read_ls(write(program, part, tmp_path / "part", setup="part", rtcp=True))
    held_drum, _ = read_ls(write(program, part, tmp_path / "drum", setup="drum"))
    for a, b in zip(held_part, held_drum):
        pose_a, pose_b = np.eye(4), np.eye(4)
        pose_a[:3, :3], pose_a[:3, 3] = wpr(*a[3:]), a[:3]
        pose_b[:3, :3], pose_b[:3, 3] = wpr(*b[3:]), b[:3]
        assert np.allclose(pose_a @ pose_b, np.eye(4), atol=5e-3)
    _, kind = read_csv(tmp_path / "part" / "program.csv")
    marked = [line.endswith("RTCP    ;") for line in lines if line[5] in "JL"]
    assert np.array_equal(marked, np.isin(kind, ["sand", "bridge"]))
    with pytest.raises(ValueError):
        write(program, part, tmp_path, name="sand")
