"""Step 5: a FANUC robot program (.LS) that runs the passes, for a robot holding the part against a fixed drum.

    python -m moonshot.fanuc results/grip --frame 0 0 0  1 0 0  0 1 0

Every pass becomes a stroke: a path through the middle of its strip, end to end, with the
contact held still on the drum (its tracking offset). Along each stroke the drum axis is chosen
again by dynamic programming over the collision-free angles of step 2, with step 3's turning and
grain terms and a cap on how fast it may turn (`swing`). On the faces that is step 3's level
axis; where a level axis is free at one point and blocked at the next, it no longer swings back
and forth. A turn that would sweep the drum through blocked angles is avoided, or the stroke is
cut there. Over edges the drum rolls in small steps, each settled onto the part. Points are kept
where the robot's straight move between them would stray more than `tol` from the planned
contact or `angle_tol` from its orientation, judged with the controller's own interpolation: the
part frame's origin moves in a straight line (a robot holding the part), or the drum frame's
(Remote TCP, or a robot holding the drum). Between pieces the part lifts off, moves across and
touches down again, each move checked to clear the drum. Last, every move is sampled and checked
against the part's probes, the holder and the drum.

Poses inside are the drum frame in the part's planning coordinates. The drum frame (FANUC
UFRAME for a robot holding the part) has its origin on the drum's surface at the middle of
its length, X along its axis and Z pointing into the drum, toward its axis; Y = Z x X is the
way the part moves across it. The part frame (UTOOL) is given by `--frame` in the model
file's own coordinates.
"""
import argparse
import json
import re
import time
import warnings
from pathlib import Path

import numpy as np
from numba import njit, prange
from scipy.ndimage import gaussian_filter1d
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation

from .geometry import UNITS
from .pipeline import load
from .smooth import _arcs, transport
from .track import across, integrate, limits, surroundings

MM = 25.4


@njit(cache=True)
def _viterbi(arcs, pull_re, pull_im, delta, cap, weight, step):
    """Feasible bin per node along a chain: least turning plus pulls, with turns past `cap` costing `weight` per rad^2.

    `arcs` numbers each node's runs of feasible bins (-1 where infeasible). The turn between nodes i
    and i + 1 is theta_i+1 - theta_i - delta_i (mod pi), as in step 3. A turn that leaves the run
    it starts in, at either node, sweeps the drum through orientations that collide: it costs 1000
    and is flagged, for the stroke to be cut there.
    """
    m, nb = arcs.shape
    cos2, sin2 = np.cos(2 * (np.arange(nb) + 0.5) * step), np.sin(2 * (np.arange(nb) + 0.5) * step)
    cost, new = np.full(nb, np.inf), np.empty(nb)
    back = np.zeros((m, nb), np.int64)
    for c in range(nb):
        if arcs[0, c] >= 0:
            cost[c] = -0.5 * (pull_re[0] * cos2[c] + pull_im[0] * sin2[c])
    for i in range(1, m):
        shift = delta[i - 1] / step
        for c in range(nb):
            new[c] = np.inf
            if arcs[i, c] < 0:
                continue
            best, arg = np.inf, 0
            back_c = int(np.floor(c - shift + 0.5)) % nb  # where c sits seen from node i
            for b in range(nb):
                if cost[b] < best:  # turning costs at least 0
                    d = ((c - b) * step - delta[i - 1] + np.pi / 2) % np.pi - np.pi / 2
                    over = max(abs(d) - cap[i - 1], 0.0)
                    value = cost[b] + np.sin(d) ** 2 + weight * over * over
                    if arcs[i - 1, b] != arcs[i - 1, back_c] or arcs[i, int(np.floor(b + shift + 0.5)) % nb] != arcs[i, c]:
                        value += 1000.0
                    if value < best:
                        best, arg = value, b
            new[c] = best - 0.5 * (pull_re[i] * cos2[c] + pull_im[i] * sin2[c])
            back[i, c] = arg
        cost[:] = new
    out = np.empty(m, np.int64)
    out[m - 1] = np.argmin(cost)
    for i in range(m - 1, 0, -1):
        out[i - 1] = back[i, out[i]]
    crossed = np.zeros(m - 1, np.bool_)
    for i in range(1, m):
        shift = delta[i - 1] / step
        b, c = out[i - 1], out[i]
        crossed[i - 1] = arcs[i - 1, b] != arcs[i - 1, int(np.floor(c - shift + 0.5)) % nb] or \
            arcs[i, int(np.floor(b + shift + 0.5)) % nb] != arcs[i, c]
    return out, crossed


@njit(parallel=True, cache=True)
def _clearance(rot, origin, probes, radius, half_len, out):
    """Least signed distance from the probes to the drum, per pose (negative inside it).

    A pose is the drum frame (rotation columns X, Y, Z and origin) in the probes' coordinates;
    the drum is the solid cylinder along X through (0, 0, R).
    """
    for k in prange(rot.shape[0]):
        best = np.inf
        for q in range(probes.shape[0]):
            dx, dy, dz = probes[q, 0] - origin[k, 0], probes[q, 1] - origin[k, 1], probes[q, 2] - origin[k, 2]
            ex = abs(rot[k, 0, 0] * dx + rot[k, 1, 0] * dy + rot[k, 2, 0] * dz) - half_len
            if ex >= best:  # no nearer than its distance past the drum's end
                continue
            y = rot[k, 0, 1] * dx + rot[k, 1, 1] * dy + rot[k, 2, 1] * dz
            z = rot[k, 0, 2] * dx + rot[k, 1, 2] * dy + rot[k, 2, 2] * dz - radius
            if best + radius > 0 and y * y + z * z >= (best + radius) ** 2:  # nor than its distance off the side
                continue
            er = np.sqrt(y * y + z * z) - radius
            best = min(best, max(er, ex) if er <= 0 and ex <= 0 else np.sqrt(max(er, 0.0) ** 2 + max(ex, 0.0) ** 2))
        out[k] = best


def interpolate(r0, o0, r1, o1, t, origin=None):
    """Poses a straight move from pose 0 to pose 1 passes through, at fractions `t`.

    The rotation turns about one fixed axis, as FANUC's linear moves do. Without `origin` the
    drum frame's origin moves in a straight line across the part (Remote TCP, or a robot holding
    the drum); with it the part frame's origin (`origin`, in part coordinates) moves in a straight
    line past the drum (a robot holding the part).
    """
    t = np.atleast_1d(np.asarray(t, float))
    rel = r0.T @ r1
    turn = np.arccos(np.clip((np.trace(rel) - 1) / 2, -1.0, 1.0))
    axis = np.array([rel[2, 1] - rel[1, 2], rel[0, 2] - rel[2, 0], rel[1, 0] - rel[0, 1]])
    if np.sin(turn) > 1e-6:
        axis /= 2 * np.sin(turn)
    else:  # no turn, or half a turn: then rel + I = 2 k k^T
        col = np.argmax(np.diag(rel))
        axis = (rel + np.eye(3))[:, col] / np.sqrt(max(2 * (rel[col, col] + 1), 1e-300))
    k = np.array([[0.0, -axis[2], axis[1]], [axis[2], 0.0, -axis[0]], [-axis[1], axis[0], 0.0]])
    rot = r0 @ (np.eye(3) + np.sin(t * turn)[:, None, None] * k + (1 - np.cos(t * turn))[:, None, None] * (k @ k))
    if origin is None:
        return rot, o0 + t[:, None] * (o1 - o0)
    d0, d1 = r0.T @ (origin - o0), r1.T @ (origin - o1)
    return rot, origin - np.einsum("tij,tj->ti", rot, d0 + t[:, None] * (d1 - d0))


def frame(values, units="in", rotate=(0.0, 0.0, 0.0)):
    """Part frame (4x4, inches) in planning coordinates from `--frame` numbers in the model file's coordinates.

    3 numbers are an origin (axes as the file's); 6 are an origin and FANUC W, P, R in degrees;
    9 are FANUC's three-point method: the origin, a point along +X, and a point on the +Y side of
    the XY plane.
    """
    values = np.asarray(values, float)
    out = np.eye(4)
    if values.size == 6:
        out[:3, :3] = Rotation.from_euler("xyz", values[3:], degrees=True).as_matrix()
    elif values.size == 9:
        x = values[3:6] - values[:3]
        z = np.cross(x, values[6:] - values[:3])
        if np.linalg.norm(x) < 1e-9 or np.linalg.norm(z) < 1e-9 * np.linalg.norm(x):
            raise ValueError("--frame points must not coincide or lie on one line")
        x, z = x / np.linalg.norm(x), z / np.linalg.norm(z)
        out[:3, :3] = np.column_stack([x, np.cross(z, x), z])
    elif values.size != 3:
        raise ValueError(f"--frame takes 3, 6 or 9 numbers, not {values.size}")
    out[:3, 3] = values[:3] * UNITS[units]
    turn = np.eye(4)
    turn[:3, :3] = Rotation.from_euler("xyz", rotate, degrees=True).as_matrix()
    return turn @ out


def build(result, frame=np.eye(4), model="part", lift=0.25, approach=2.0, clear=0.1, feed=2.0, rapid=4.0, turn=90.0,
          tol=0.001, angle_tol=0.5, max_step=0.25, min_len=0.25, swing=480.0, spacing=0.1, log=print):
    """The robot program for a planned result: each pass as a stroke, and the moves between them.

    `frame` is the part frame (4x4) in planning coordinates. `model` is how the controller moves
    between points: "part" (a robot holding the part: the part frame's origin moves straight),
    "rtcp" (the same, but with Remote TCP while sanding: the drum frame's origin moves straight
    across the part) or "drum" (a robot holding the drum: always so). Lengths are inches,
    `feed` and `rapid` inches per second (across the part, and off it), `turn` the fastest the
    part may turn in degrees per second, and `swing` the fastest the drum axis may turn along a
    stroke, relative to the surface, in degrees per inch. Without tracking passes the passes are
    cut `spacing` apart. Returns the moves (the drum frame's pose in part coordinates, kind,
    pass, grid point, offset and duration) and stats.
    """
    clock = time.perf_counter()
    params, ts = result["params"], result.get("track_stats") or {}
    radius, length, tolerance = params["radius"], params["length"], params["tolerance"]
    idx = np.flatnonzero(np.isfinite(result["axis"][:, 0]))
    remap = np.full(len(result["points"]), -1)
    remap[idx] = np.arange(idx.size)
    e = remap[result["pair_edges"]]
    p, n, a = result["points"][idx], result["normals"][idx], result["axis"][idx]
    t1, t2, feasible = result["t1"][idx], result["t2"][idx], np.ascontiguousarray(result["feasible"][idx])
    step = np.pi / feasible.shape[1]
    u = across(p, a, e)
    if ts.get("mode") == "passes":
        spacing = ts["spacing"]
        number = result["pass"][idx]
        if not np.array_equal(number, np.floor((u - u.min()) / spacing).astype(np.int64)):
            raise ValueError("the saved passes differ from this version's; plan the part again")
    else:
        number = np.floor((u - u.min()) / spacing).astype(np.int64)
    track = np.nan_to_num(result["track"][idx]) if ts else np.zeros(idx.size)
    feed_v = integrate(p, np.cross(n, a), e)[0]
    sanding = None if model in ("rtcp", "drum") else frame[:3, 3]  # what moves straight while sanding
    moving = None if model == "drum" else frame[:3, 3]  # and off the part
    allowed = tolerance + tol

    # Step 3's per-point pull away from the grain (its tie-break toward `prefer` is left out: the stroke decides).
    h = np.median(np.linalg.norm(p[e[:, 0]] - p[e[:, 1]], axis=1))
    pull = np.zeros(idx.size, complex)
    if params["grain"] is not None:
        g = np.asarray(params["grain"], float) / np.linalg.norm(params["grain"])
        pull -= params["grain_weight"] * h ** 2 * (t1 @ g + 1j * (t2 @ g)) ** 2
    seg = np.linalg.norm(p[e[:, 0]] - p[e[:, 1]], axis=1)
    rows, cols = np.concatenate([e[:, 0], e[:, 1]]), np.concatenate([e[:, 1], e[:, 0]])
    graph = csr_matrix((np.concatenate([seg, seg]), (rows, cols)), shape=(idx.size, idx.size))
    probes, holder, plane = surroundings(result, ts.get("band", 0.9 * length))
    sparse, tree = probes[::max(1, probes.shape[0] // 4000)], cKDTree(probes)

    def clearance(rot, org):
        """Least distance from the drum to the part and to its holder, per pose (negative: inside)."""
        rot, org = np.ascontiguousarray(rot), np.ascontiguousarray(org)
        part, held = np.empty(len(rot)), np.full(len(rot), np.inf)
        _clearance(rot, org, probes, radius, length / 2, part)
        if holder is not None:
            _clearance(rot, org, holder, radius, length / 2, held)
        if plane is not None:  # the drum reaches c.m + (L/2)|a.m| + R sqrt(1 - (a.m)^2) along m
            m, d = np.asarray(plane[0], float), float(plane[1])
            am = rot[:, :, 0] @ m
            reach = (org + radius * rot[:, :, 2]) @ m + length / 2 * np.abs(am) + radius * np.sqrt(np.maximum(1 - am ** 2, 0))
            held = np.minimum(held, d - reach)
        return part, held

    def angle(r0, r1):
        """Rotation between frames, degrees."""
        return np.degrees(np.arccos(np.clip((np.trace(np.swapaxes(r0, -1, -2) @ r1, axis1=-2, axis2=-1) - 1) / 2, -1, 1)))

    # Strokes: per pass, a path through the middle of each piece of its strip, with its drum axis and offset.
    strokes = []
    for k in np.unique(number):
        members = np.flatnonzero(number == k)
        sub = graph[members][:, members].tocsr()
        off = ((u[members] - u.min() - (k + 0.5) * spacing) / (spacing / 2)) ** 2
        coo = sub.tocoo()
        weighted = csr_matrix((coo.data * (1 + 2 * (off[coo.row] + off[coo.col])), (coo.row, coo.col)), shape=sub.shape)
        _, piece = connected_components(sub, directed=False)
        paths = []
        for c in np.unique(piece):
            nodes = np.flatnonzero(piece == c)
            if nodes.size < 3:
                continue
            start = nodes[np.argmax(dijkstra(sub, indices=nodes[0])[nodes])]
            far = dijkstra(sub, indices=start)[nodes]
            if far.max() < min_len:
                continue
            pred = dijkstra(weighted, indices=start, return_predecessors=True)[1]
            path = [nodes[np.argmax(far)]]
            while path[-1] != start:
                path.append(pred[path[-1]])
            path = members[np.array(path[::-1])]
            paths.append(path if feed_v[path[0]] <= feed_v[path[-1]] else path[::-1])
        if not paths:
            continue
        paths.sort(key=lambda q: feed_v[q[0]])
        axes, jumps = [], []
        for path in paths:
            delta = transport(n, t1, t2, np.column_stack([path[1:], path[:-1]]))
            cap = np.radians(swing) * np.linalg.norm(np.diff(p[path], axis=0), axis=1)
            mask = feasible[path]
            arcs = np.where(mask, np.cumsum(np.diff(mask.astype(np.int64), axis=1, prepend=0) == 1, axis=1), -1)
            wrap = mask[:, 0] & mask[:, -1]  # a run through 180 degrees is one run
            arcs = np.where(wrap[:, None] & (arcs == arcs[:, -1:]), arcs[:, :1], arcs)
            bins, crossed = _viterbi(arcs, pull[path].real, pull[path].imag, delta, cap, 1e4, step)
            # Blur away the 1 degree steps: the turn relative to the surface, summed along the path, is
            # smoothed, and each angle kept inside the collision-free arc around its bin.
            theta = (bins + 0.5) * step
            turned = np.concatenate([[0.0], np.cumsum((np.diff(theta) - delta + np.pi / 2) % np.pi - np.pi / 2)])
            smooth = theta[0] + np.concatenate([[0.0], np.cumsum(delta)]) + gaussian_filter1d(turned, 2, mode="nearest")
            lo, hi = _arcs(bins, feasible[path], step)
            theta = np.clip(smooth + np.pi * np.round((theta - smooth) / np.pi), lo, hi)
            axis = np.cos(theta)[:, None] * t1[path] + np.sin(theta)[:, None] * t2[path]
            axis *= np.cumprod(np.concatenate([[1.0], np.sign(np.einsum("ij,ij->i", axis[1:], axis[:-1]))]))[:, None]
            axis *= np.sign(np.sum(axis * a[path])) or 1.0  # the planner's sign, which its offsets are measured along
            axes.append(axis)
            jumps.append(crossed)
        # The offset is along the axis as signed here. A pass's planned offset is along the planner's signs,
        # which these follow on the whole; a swept point's is along its own.
        nodes, axis = np.concatenate(paths), np.concatenate(axes)
        sweep = ts.get("mode") == "sweep"
        target = track[nodes] * (np.sign(np.einsum("ij,ij->i", axis, a[nodes])) if sweep else 1.0)
        offset = np.zeros(nodes.size)
        if ts:
            lo, hi = limits(p[nodes], n[nodes], axis, probes, radius, length, ts["band"], ts["gap"], holder, plane,
                            params["probe_spacing"], tolerance)[:2]
            offset = np.clip(target, lo, hi) if sweep else np.clip(target, lo.max(), hi.min())
        pieces = []
        for path, ax, jump, s in zip(paths, axes, jumps, np.split(offset, np.cumsum([q.size for q in paths])[:-1])):
            rot, c = np.stack([ax, np.cross(n[path], ax), n[path]], axis=2), p[path]
            # Roll over sharp turns (creases) in steps of at most 2 degrees, each settled onto the part's
            # probes so that the drum just touches it. Where the drum cannot turn that way without hitting
            # something, the piece is cut and the part lifts off there instead.
            counts = np.ceil(angle(rot[:-1], rot[1:]) / 2.0).astype(int)
            fill = {}
            for i in np.flatnonzero(counts > 1):
                t = np.arange(1, counts[i]) / counts[i]
                rr, cc = interpolate(rot[i], c[i], rot[i + 1], c[i + 1], t)
                for r_, c_ in zip(rr, cc):
                    local = (probes[tree.query_ball_point(c_, 0.3)] - c_) @ r_
                    c_ -= (np.sqrt(local[:, 1] ** 2 + (local[:, 2] - radius) ** 2) - radius).min() * r_[:, 2]
                fill[i] = (np.where(t < 0.5, path[i], path[i + 1]), rr, cc, s[i] + t * (s[i + 1] - s[i]))
            blocked = set(np.flatnonzero(jump).tolist())
            if fill:
                part, held = clearance(np.concatenate([v[1] for v in fill.values()]),
                                       np.concatenate([v[2] - v[3][:, None] * v[1][:, :, 0] for v in fill.values()]))
                cuts = np.cumsum([v[0].size for v in fill.values()])[:-1]
                blocked |= {i for i, pt, hd in zip(fill, np.split(part, cuts), np.split(held, cuts))
                            if pt.min() < -allowed or hd.min() < -tolerance}
            runs = [[[path[:1]], [rot[:1]], [c[:1]], [s[:1]]]]
            for i in range(len(path) - 1):
                if i in blocked:
                    runs.append([[], [], [], []])
                elif i in fill:
                    for run, v in zip(runs[-1], fill[i]):
                        run.append(v)
                for run, v in zip(runs[-1], (path[i + 1:i + 2], rot[i + 1:i + 2], c[i + 1:i + 2], s[i + 1:i + 2])):
                    run.append(v)
            for run in runs:
                nodes_, rot_, c_, s_ = (np.concatenate(v) for v in run)
                if len(nodes_) > 1:
                    pieces.append({"pass": int(k), "nodes": nodes_, "R": rot_, "c": c_, "o": c_ - s_[:, None] * rot_[:, :, 0],
                                   "s": s_})
        if pieces:
            strokes.append({"pass": int(k), "pieces": pieces, "planned": float(target[0]), "used": float(offset[0])})

    # Each stroke runs whichever way starts nearer where the last one ended.
    sequence = []
    for stroke in strokes:
        pieces = stroke["pieces"]
        if sequence and np.linalg.norm(pieces[-1]["c"][-1] - sequence[-1]["c"][-1]) < \
                np.linalg.norm(pieces[0]["c"][0] - sequence[-1]["c"][-1]):
            pieces = [{key: (v[::-1] if key != "pass" else v) for key, v in q.items()} for q in pieces[::-1]]
        sequence += pieces

    # Keep the fewest points whose straight moves stay within `tol` of the planned contact and `angle_tol`
    # of its orientation at every point in between, as the controller interpolates them.
    worst = [0.0, 0.0]
    for q in sequence:
        q["arc"] = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(q["c"], axis=0), axis=1))])
        keep = [0]
        while keep[-1] < len(q["nodes"]) - 1:
            i = j = keep[-1]
            found = (0.0, 0.0)
            while j + 1 < len(q["nodes"]) and (j == i or q["arc"][j + 1] - q["arc"][i] <= max_step):
                mid = np.arange(i + 1, j + 1)
                if mid.size:
                    rot, org = interpolate(q["R"][i], q["o"][i], q["R"][j + 1], q["o"][j + 1],
                                           (q["arc"][mid] - q["arc"][i]) / (q["arc"][j + 1] - q["arc"][i]), sanding)
                    at = np.einsum("tji,tj->ti", rot, q["c"][mid] - org)
                    normal = np.einsum("tji,tj->ti", rot, q["R"][mid][:, :, 2])
                    gap = np.abs((radius - at[:, 2]) * normal[:, 2] - at[:, 1] * normal[:, 1] - radius).max()
                    turned = angle(rot, q["R"][mid]).max()
                    if gap > tol or turned > angle_tol:
                        break
                    found = (gap, turned)
                j += 1
            keep.append(j)
            worst = [max(worst[0], found[0]), max(worst[1], found[1])]
        q["keep"] = keep

    # Every move between kept points is sampled and checked against all the probes; where the drum digs
    # in further than the planner allows plus `tol`, or meets the holder, the move is split in the middle.
    checked, splits = set(), 0
    t_in = np.array([0.25, 0.5, 0.75])
    while True:
        todo = [(qi, i, j) for qi, q in enumerate(sequence) for i, j in zip(q["keep"][:-1], q["keep"][1:])
                if j - i > 1 and (qi, i, j) not in checked]
        if not todo:
            break
        poses = [interpolate(sequence[qi]["R"][i], sequence[qi]["o"][i], sequence[qi]["R"][j], sequence[qi]["o"][j],
                             t_in, sanding) for qi, i, j in todo]
        part, held = clearance(np.concatenate([r for r, _ in poses]), np.concatenate([o for _, o in poses]))
        bad = (part.reshape(len(todo), -1).min(axis=1) < -allowed) | (held.reshape(len(todo), -1).min(axis=1) < -tolerance)
        checked.update(todo)
        for (qi, i, j), b in zip(todo, bad):
            if b:
                sequence[qi]["keep"] = sorted(sequence[qi]["keep"] + [(i + j) // 2])
                splits += 1

    # The moves: approach, then each piece in contact, bridging gaps in contact where the drum stays clear,
    # otherwise lifting off, crossing over and touching down; then away.
    moves = {key: [] for key in ("kind", "pass", "R", "o", "node", "s", "time")}

    def add(kind, k, rot, org, node=-1, s=np.nan, dt=0.0):
        """Append one move: the pose it goes to and how long it takes."""
        for key, v in zip(moves, (kind, k, rot, org, node, s, dt)):
            moves[key].append(v)

    # Ways off the part: straight away from the drum, or tilted 45 degrees along or across it, at heights
    # doubling from `lift`. A way counts when the move along it never digs in and ends `clear` of everything.
    side = np.sqrt(0.5)
    dirs = np.array([[0, 0, 1], [0, side, side], [0, -side, side], [side, 0, side], [-side, 0, side]])

    def exits(rot, org, heights):
        """Clear ways off the part from contact pose (rot, org): (height, direction, lifted origin), lowest first."""
        ways = [(h, k, org + h * rot @ d) for k, d in enumerate(dirs) for h in heights]
        t = np.arange(1, 9) / 8
        part, held = clearance(np.repeat(rot[None], 8 * len(ways), axis=0),
                               np.concatenate([org + t[:, None] * (up - org) for _, _, up in ways]))
        part, held = part.reshape(len(ways), 8), held.reshape(len(ways), 8)
        good = (part.min(axis=1) >= -allowed) & (part[:, -1] >= clear) & (held.min(axis=1) >= -tolerance) & (held[:, -1] >= clear)
        return sorted((w for w, g_ in zip(ways, good) if g_), key=lambda w: w[:2])

    lifted = []
    rises = lift * 2.0 ** np.arange(6)
    for qi, q in enumerate(sequence):
        r0, o0, node0 = q["R"][0], q["o"][0], idx[q["nodes"][0]]
        if qi == 0:
            ways = exits(r0, o0, approach / 2.0 ** np.arange(4))
            if not ways:
                raise ValueError("no straight way onto the part at the first point clears the drum")
            way = max(ways, key=lambda w: (w[0], -w[1]))
            add("approach", q["pass"], r0, way[2])
            add("touch", q["pass"], r0, o0, node0, q["s"][0], way[0] / feed)
        else:
            prev = sequence[qi - 1]
            r1, o1 = prev["R"][-1], prev["o"][-1]
            gap = np.linalg.norm(q["c"][0] - prev["c"][-1])
            bridged = False
            if prev["pass"] == q["pass"]:
                rot, org = interpolate(r1, o1, r0, o0, np.linspace(0, 1, int(np.ceil(gap / 0.02)) + 2)[1:-1], sanding)
                part, held = clearance(rot, org)
                bridged = part.min() >= -allowed and held.min() >= -tolerance
            if bridged:
                add("bridge", q["pass"], r0, o0, node0, q["s"][0], max(gap / feed, angle(r1, r0) / turn))
            else:
                off, on = exits(r1, o1, rises), exits(r0, o0, rises)
                pairs = sorted(((w1, w0) for w1 in off for w0 in on),
                               key=lambda pair: (max(pair[0][0], pair[1][0]), pair[0][1] + pair[1][1]))
                for w1, w0 in pairs:
                    part, held = clearance(*interpolate(r1, w1[2], r0, w0[2], np.linspace(0, 1, 41), moving))
                    if part.min() >= clear and held.min() >= clear:
                        break
                else:
                    raise ValueError(f"no way off and back on the part up to {rises[-1]:g} in clears the drum between "
                                     f"passes {prev['pass']} and {q['pass']}; is the part frame far from the part?")
                lifted.append(max(w1[0], w0[0]))
                shift = np.linalg.norm(np.einsum("ji,qj->qi", r0, sparse - w0[2]) - np.einsum("ji,qj->qi", r1, sparse - w1[2]),
                                       axis=1).max()
                add("lift", prev["pass"], r1, w1[2], dt=w1[0] / feed)
                add("transit", q["pass"], r0, w0[2], dt=max(shift / rapid, angle(r1, r0) / turn))
                add("touch", q["pass"], r0, o0, node0, q["s"][0], w0[0] / feed)
        for i, j in zip(q["keep"][:-1], q["keep"][1:]):
            add("sand", q["pass"], q["R"][j], q["o"][j], idx[q["nodes"][j]], q["s"][j],
                max((q["arc"][j] - q["arc"][i]) / feed, angle(q["R"][i], q["R"][j]) / turn))
    ways = exits(sequence[-1]["R"][-1], sequence[-1]["o"][-1], approach / 2.0 ** np.arange(4))
    if not ways:
        raise ValueError("no straight way off the part at the last point clears the drum")
    way = max(ways, key=lambda w: (w[0], -w[1]))
    add("retract", sequence[-1]["pass"], sequence[-1]["R"][-1], way[2], dt=way[0] / rapid)
    moves = {key: np.array(v) for key, v in moves.items()}

    # Last, every move is sampled as the controller runs it and checked against the part, the holder and the drum.
    kind = moves["kind"]
    contact = np.isin(kind, ("sand", "bridge"))
    samples = np.where(kind == "transit", 40, 4)[1:]
    poses = [interpolate(moves["R"][i - 1], moves["o"][i - 1], moves["R"][i], moves["o"][i],
                         np.arange(1, c + 1) / c, sanding if contact[i] else moving) for i, c in enumerate(samples, 1)]
    part, held = clearance(np.concatenate([r for r, _ in poses]), np.concatenate([o for _, o in poses]))
    which = np.repeat(np.arange(1, len(kind)), samples)
    touching = np.isin(kind[which], ("sand", "bridge", "touch", "lift"))
    turned = np.array([0.0] + [angle(moves["R"][i - 1], moves["R"][i]) for i in range(1, len(kind))])
    sand = kind == "sand"
    # Tilt toward the grain along the strokes, weighted by length, for these axes and for step 3's at the same points.
    if params["grain"] is not None:
        weight = np.concatenate([np.gradient(q["arc"]) for q in sequence])
        here = np.concatenate([q["R"][:, :, 0] for q in sequence]) @ g
        planned = a[np.concatenate([q["nodes"] for q in sequence])] @ g
        tilt = [float(np.degrees(np.arcsin(np.minimum(np.abs(v), 1))) @ weight / weight.sum()) for v in (here, planned)]
    else:
        tilt = [0.0, 0.0]
    stats = {"model": model, "passes": len(strokes), "pieces": len(sequence), "moves": int(len(kind)),
             "contact_points": int(contact.sum() + (kind == "touch").sum()), "bridges": int((kind == "bridge").sum()),
             "lifts": int((kind == "lift").sum()), "highest_lift_in": float(max(lifted, default=0.0)),
             "contact_length_in": float(sum(q["arc"][-1] for q in sequence)), "cycle_s": float(moves["time"].sum()),
             "sanding_s": float(moves["time"][contact].sum()),
             "turn_limited_s": float(moves["time"][sand & (turned / turn >= moves["time"] - 1e-12)].sum()),
             "fit_gap_in": worst[0], "fit_angle_deg": worst[1], "splits": splits,
             "deepest_in": float(-part[touching].min()) if touching.any() else 0.0,
             "off_part_clearance_in": float(part[~touching].min()) if (~touching).any() else np.inf,
             "holder_clearance_in": float(held.min()),
             "grain_tilt_mean_deg": tilt[0], "grain_tilt_plan_deg": tilt[1],
             "offsets_changed": int(sum(abs(s["planned"] - s["used"]) > 1e-9 for s in strokes)),
             "offset_change_max_in": float(max((abs(s["planned"] - s["used"]) for s in strokes), default=0.0))}
    if stats["deepest_in"] > allowed + 1e-6 or stats["holder_clearance_in"] < -tolerance or \
            stats["off_part_clearance_in"] < clear - 1e-6:
        log(f"WARNING: the check found the drum {stats['deepest_in']:.4f} in into the part (allowed {allowed:g}), "
            f"{stats['holder_clearance_in']:.3f} in from the holder and {stats['off_part_clearance_in']:.3f} in from the part "
            f"between passes")
    log(f"program: {stats['passes']} passes in {stats['pieces']} pieces, {stats['moves']:,} points "
        f"({stats['contact_points']:,} in contact, {stats['bridges']} gaps bridged, {stats['lifts']} lifts); "
        f"{stats['contact_length_in']:.0f} in of contact, cycle about {stats['cycle_s']:.0f} s "
        f"({time.perf_counter() - clock:.1f} s)")
    return {"moves": moves, "strokes": [{k: v for k, v in s.items() if k != "pieces"} for s in strokes], "stats": stats}


def write(program, frame, out, name="SAND", setup="part", rtcp=False, uframe=1, utool=1, config="N U T, 0, 0, 0",
          extra=None):
    """Write the FANUC program <name>.ls, program.csv (every point) and program.json (frames and stats) to `out`.

    With `setup` "part" the robot holds the part: positions are the part frame (UTOOL `utool`) in
    the drum frame (UFRAME `uframe`). With "drum" the robot holds the drum: positions are the drum
    frame (UTOOL) in the part frame (UFRAME). Positions are mm and FANUC W, P, R degrees.
    """
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,35}", name):
        raise ValueError(f"FANUC program names are a capital letter then capitals, digits or _, at most 36: {name!r}")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    moves = program["moves"]
    kind, rot, org, f = moves["kind"], moves["R"], moves["o"], frame[:3, 3]
    if setup == "part":
        r_out, t_out = np.swapaxes(rot, 1, 2) @ frame[:3, :3], np.einsum("kji,kj->ki", rot, f - org)
    else:
        r_out, t_out = frame[:3, :3].T @ rot, (org - f) @ frame[:3, :3]
    with warnings.catch_warnings():  # P = +-90 is a gimbal lock; scipy then sets R to 0, which is still exact
        warnings.simplefilter("ignore", UserWarning)
        wpr = Rotation.from_matrix(r_out).as_euler("xyz", degrees=True)
    xyz = t_out * MM
    msec = np.maximum(np.round(moves["time"] * 1000), 1).astype(int)

    # /MN: frames, then one motion line per point, with a remark where each pass starts.
    fixed, held = ("drum", "part") if setup == "part" else ("part", "drum")
    body = ["!Moonshot drum sanding", f"!UFRAME {uframe}: {fixed} frame", f"!UTOOL {utool}: {held} frame",
            f"UFRAME_NUM={uframe}", f"UTOOL_NUM={utool}"]
    starts = np.flatnonzero(np.concatenate([[True], moves["pass"][1:] != moves["pass"][:-1]]))  # the move to each pass
    line_of = np.zeros(len(kind), int)
    for i, k in enumerate(kind):
        if i in starts:
            body.append(f"!Pass {np.searchsorted(starts, i) + 1} of {starts.size}")
        if k == "approach":
            motion = f"J P[{i + 1}] 20% FINE"
        else:
            end = "FINE" if k == "retract" else "CNT100" + (" RTCP" if rtcp and k in ("sand", "bridge") else "")
            motion = f"L P[{i + 1}] {msec[i]}msec {end}"
        body.append(motion)
        line_of[i] = len(body)
    stamp = time.strftime("DATE %y-%m-%d  TIME %H:%M:%S")
    text = [f"/PROG  {name}", "/ATTR", "OWNER\t\t= MNEDITOR;", 'COMMENT\t\t= "Moonshot sand";', "PROG_SIZE\t= 0;",
            f"CREATE\t\t= {stamp};", f"MODIFIED\t= {stamp};", "FILE_NAME\t= ;", "VERSION\t\t= 0;",
            f"LINE_COUNT\t= {len(body)};", "MEMORY_SIZE\t= 0;", "PROTECT\t\t= READ_WRITE;",
            "TCD:  STACK_SIZE\t= 0,", "      TASK_PRIORITY\t= 50,", "      TIME_SLICE\t= 0,", "      BUSY_LAMP_OFF\t= 0,",
            "      ABORT_REQUEST\t= 0,", "      PAUSE_REQUEST\t= 0;", "DEFAULT_GROUP\t= 1,*,*,*,*;",
            "CONTROL_CODE\t= 00000000 00000000;", "/MN"]
    text += [f"{i:4d}:{b}    ;" if b[:2] in ("J ", "L ") else f"{i:4d}:  {b} ;" for i, b in enumerate(body, 1)]
    text.append("/POS")
    for i, ((x, y, z), (w, pp, r)) in enumerate(zip(xyz, wpr), 1):
        text += [f"P[{i}]{{", "   GP1:", f"\tUF : {uframe}, UT : {utool},\t\tCONFIG : '{config}',",
                 f"\tX = {x:9.3f}  mm,\tY = {y:9.3f}  mm,\tZ = {z:9.3f}  mm,",
                 f"\tW = {w:9.3f} deg,\tP = {pp:9.3f} deg,\tR = {r:9.3f} deg", "};"]
    text.append("/END")
    (out / f"{name.lower()}.ls").write_text("\n".join(text) + "\n")

    # Every point, with what it touches in the part frame.
    rf = frame[:3, :3]
    contact = np.where(np.isfinite(moves["s"])[:, None], org + np.nan_to_num(moves["s"])[:, None] * rot[:, :, 0], np.nan)
    rows = np.column_stack([line_of, np.arange(1, len(kind) + 1), xyz, wpr, msec, moves["pass"], (contact - f) @ rf,
                            rot[:, :, 2] @ rf, rot[:, :, 0] @ rf, moves["s"]])
    header = ("line,position,x_mm,y_mm,z_mm,w_deg,p_deg,r_deg,time_ms,pass,contact_x,contact_y,contact_z,"
              "normal_x,normal_y,normal_z,axis_x,axis_y,axis_z,track,kind")
    with open(out / "program.csv", "w") as fh:
        fh.write(header + "\n")
        for row, k in zip(rows, kind):
            fh.write(",".join("" if not np.isfinite(v) else f"{v:.6g}" for v in row) + f",{k}\n")
    summary = {"program": name, "setup": setup, "rtcp": rtcp, "uframe": uframe, "utool": utool, "config": config,
               "lines": len(body), **(extra or {}), "stats": program["stats"], "strokes": program["strokes"]}
    (out / "program.json").write_text(json.dumps(summary, indent=2))
    return out / f"{name.lower()}.ls"


def main(argv=None):
    """Command-line entry point."""
    ap = argparse.ArgumentParser(prog="python -m moonshot.fanuc", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("result", help="results directory written by `python -m moonshot` (plan with --track for the passes)")
    ap.add_argument("--frame", type=float, nargs="+", default=[0.0, 0.0, 0.0], metavar="N",
                    help="part frame in the model file's coordinates and units: X Y Z (origin, the file's axes), "
                         "X Y Z W P R, or three points X Y Z  X Y Z  X Y Z (origin, a point on +X, a point on the "
                         "+Y side), FANUC's three-point method (default: the file's own origin and axes)")
    ap.add_argument("--name", default="SAND", help="program name (default SAND)")
    ap.add_argument("--out", help="output directory (default <result>/fanuc)")
    ap.add_argument("--setup", default="part", choices=("part", "drum"),
                    help="part (default): the robot holds the part, UTOOL is the part frame and UFRAME the drum's; "
                         "drum: the robot holds the drum, UTOOL is the drum frame and UFRAME the part's")
    ap.add_argument("--rtcp", action="store_true", help="sand with FANUC Remote TCP (the drum frame's UFRAME as the remote TCP)")
    ap.add_argument("--uframe", type=int, default=1, help="UFRAME number (default 1)")
    ap.add_argument("--utool", type=int, default=1, help="UTOOL number (default 1)")
    ap.add_argument("--config", default="N U T, 0, 0, 0", help="FANUC configuration for every point (default 'N U T, 0, 0, 0')")
    ap.add_argument("--feed", type=float, default=120.0, help="speed across the part, inches per minute (120)")
    ap.add_argument("--rapid", type=float, default=240.0, help="speed off the part, inches per minute (240)")
    ap.add_argument("--max-turn", type=float, default=90.0, help="fastest the part may turn, degrees per second (90)")
    ap.add_argument("--swing", type=float, default=480.0,
                    help="fastest the drum axis may turn along a stroke, relative to the surface, degrees per inch (480)")
    ap.add_argument("--lift", type=float, default=0.25, help="how far the part lifts off between passes, inches (0.25)")
    ap.add_argument("--approach", type=float, default=2.0, help="height of the first and last points, inches (2)")
    ap.add_argument("--tol", type=float, default=0.001,
                    help="how far a move between points may leave the planned contact, inches (0.001)")
    ap.add_argument("--angle-tol", type=float, default=0.5, help="and turn from its orientation, degrees (0.5)")
    ap.add_argument("--max-step", type=float, default=0.25, help="longest move between points, inches (0.25)")
    args = ap.parse_args(argv)
    result = load(args.result)
    params = result["params"]
    part = frame(args.frame, params["units"], params["rotate"])
    model = "drum" if args.setup == "drum" else "rtcp" if args.rtcp else "part"
    if args.rtcp and args.setup == "drum":
        ap.error("--rtcp is for a robot holding the part")
    program = build(result, part, model, args.lift, args.approach, 0.1, args.feed / 60, args.rapid / 60, args.max_turn,
                    args.tol, args.angle_tol, args.max_step, swing=args.swing)
    extra = {"frame": args.frame, "model_units": params["units"], "model_rotate": params["rotate"],
             "drum_frame": "origin on the drum's surface at the middle of its length, X along its axis, Z into the "
                           "drum toward its axis, Y = Z x X",
             "feed_in_min": args.feed, "rapid_in_min": args.rapid, "max_turn_deg_s": args.max_turn, "swing_deg_in": args.swing}
    path = write(program, part, args.out or Path(args.result) / "fanuc", args.name, args.setup, args.rtcp, args.uframe,
                 args.utool, args.config, extra)
    print(f"wrote {path}, program.csv, program.json")


if __name__ == "__main__":
    main()
