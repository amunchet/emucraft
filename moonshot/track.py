"""Step 4 (optional): tracking, sliding the contact point along the drum to spread its wear.

Each point keeps the orientation step 3 chose; only where along the drum it touches
changes. Moving the contact by s along the axis a moves the drum by -s*a, so it spans
[-L/2 - s, L/2 - s] around the contact instead of [-L/2, L/2]. An offset is safe when the
drum then touches exactly the same part material as at its middle: material within `gap`
of the drum surface stays on the drum if it was on it, and stays past the drum's ends if it
was past them. The holder only has to stay clear. Nothing new is touched or hit and the
patch the drum sands is the same, so the contact itself is unchanged.

By default the contact moves only between passes. The surface is cut into passes, strips
`spacing` apart along Y (square to the drum axis, the way the part feeds past the drum).
The contact stays put for a whole pass and moves a step along the drum between one pass and
the next, back and forth across the middle `band` of the drum. A pass keeps one offset, so it
must be safe for every point on it.

The alternative sweep moves the contact during passes too. Its phase is the distance along
the surface from each region's lowest point along `along` (the grain), ranked by area so
every inch of the band takes the same share of the surface. Where a point's safe range is
narrower than the band, the sweep is clipped to it, the range limits are eased in over the
surface so the motion stays smooth, and the sweep is re-spread so the band stays evenly used.
"""
import numpy as np
from numba import njit, prange
from scipy.sparse import csr_matrix, diags, identity
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.sparse.linalg import splu

from .holder import fixture_probes, front_view, table
from .reach import _index, _skip_box, probe_points


@njit(parallel=True, cache=True)
def _reach_along(pts, nrm, axis, order, probes, origin, cell, dims, cstart, cend, clo, chi, fstart, fend, flo, fhi,
                 radius, half_len, reach, near, out):
    """Per point, over probes within `near` of the drum axis: the lowest and highest axial
    coordinate on the drum (|t| <= L/2) and the nearest past each end (t > L/2, t < -L/2).
    t is measured from the contact along the axis and limited to |t| <= reach.
    """
    rn2 = near * near
    rb2 = near * near + reach * reach
    for jj in prange(pts.shape[0]):
        i = order[jj]
        n0, n1, n2 = nrm[i, 0], nrm[i, 1], nrm[i, 2]
        a0, a1, a2 = axis[i, 0], axis[i, 1], axis[i, 2]
        b0, b1, b2 = n1 * a2 - n2 * a1, n2 * a0 - n0 * a2, n0 * a1 - n1 * a0
        cx, cy, cz = pts[i, 0] + radius * n0, pts[i, 1] + radius * n1, pts[i, 2] + radius * n2
        lo_on, hi_on, past_hi, past_lo = 0.0, 0.0, np.inf, -np.inf  # the contact itself is on the drum
        g0, g1 = np.empty(3, np.int64), np.empty(3, np.int64)  # cells around the drum's reach along its axis
        for d in range(3):
            cd = pts[i, d] + radius * nrm[i, d]
            ext = reach * abs(axis[i, d]) + near
            g0[d] = max(int(np.floor((cd - ext - origin[d]) / cell)), 0)
            g1[d] = min(int(np.floor((cd + ext - origin[d]) / cell)), dims[d] - 1)
        for gx in range(g0[0], g1[0] + 1):
            for gy in range(g0[1], g1[1] + 1):
                for gz in range(g0[2], g1[2] + 1):
                    cid = (gx * dims[1] + gy) * dims[2] + gz
                    if cstart[cid] == cend[cid] or _skip_box(clo, chi, cid, cx, cy, cz, n0, n1, n2, rb2, near) \
                            or _skip_box(clo, chi, cid, cx, cy, cz, b0, b1, b2, rb2, near):
                        continue
                    for f in range(cstart[cid], cend[cid]):
                        if _skip_box(flo, fhi, f, cx, cy, cz, n0, n1, n2, rb2, near) \
                                or _skip_box(flo, fhi, f, cx, cy, cz, b0, b1, b2, rb2, near):
                            continue
                        mid = ((flo[f, 0] + fhi[f, 0]) / 2 - cx) * a0 + ((flo[f, 1] + fhi[f, 1]) / 2 - cy) * a1 \
                            + ((flo[f, 2] + fhi[f, 2]) / 2 - cz) * a2
                        half = ((fhi[f, 0] - flo[f, 0]) * abs(a0) + (fhi[f, 1] - flo[f, 1]) * abs(a1)
                                + (fhi[f, 2] - flo[f, 2]) * abs(a2)) / 2
                        t0, t1 = mid - half, mid + half
                        # Skip cells that cannot move any of the four bounds.
                        if t0 > reach or t1 < -reach or (t0 >= lo_on and t1 <= hi_on) \
                                or (t0 > half_len and t0 >= past_hi) or (t1 < -half_len and t1 <= past_lo):
                            continue
                        for q in range(fstart[f], fend[f]):
                            dx, dy, dz = probes[q, 0] - cx, probes[q, 1] - cy, probes[q, 2] - cz
                            h = dx * n0 + dy * n1 + dz * n2
                            y = dx * b0 + dy * b1 + dz * b2
                            if h * h + y * y >= rn2:
                                continue
                            t = dx * a0 + dy * a1 + dz * a2
                            if t > half_len:
                                past_hi = min(past_hi, t)
                            elif t < -half_len:
                                past_lo = max(past_lo, t)
                            else:
                                lo_on = min(lo_on, t)
                                hi_on = max(hi_on, t)
        out[i, 0], out[i, 1], out[i, 2], out[i, 3] = lo_on, hi_on, past_lo, past_hi


def _along(points, normals, axes, probes, radius, half_len, reach, near):
    """(lo_on, hi_on, past_lo, past_hi) per point from `_reach_along`, for one probe set."""
    out = np.zeros((len(points), 4))
    out[:, 2:] = -np.inf, np.inf
    if len(points) and len(probes):
        cell = 1.28  # 16 fine cells of 0.08 in: the query is a long thin prism, so fine boxes do the culling
        index = _index(probes, cell, 0.08)
        sorted_probes, origin, dims = index[:3]
        pc = np.clip(((points - origin) / cell).astype(np.int64), 0, dims - 1)
        order = np.argsort((pc[:, 0] * dims[1] + pc[:, 1]) * dims[2] + pc[:, 2], kind="stable")
        _reach_along(points, normals, axes, order, sorted_probes, origin, cell, dims, *index[3:], radius, half_len,
                     reach, near, out)
    return out.T


def limits(points, normals, axes, probes, radius, length, band, gap, holder=None, plane=None, margin=0.0, tol=0.001):
    """Safe contact offsets along the drum: per point (lo, hi) with lo <= 0 <= hi, in inches.

    The drum keeps each point's axis; at offset s the contact sits s along the drum from its
    middle. Within [lo, hi] the drum covers the same part `probes` within `gap` of its surface
    as at s = 0 (see the module docstring) and the contact stays within the middle `band`. The
    holder only has to stay clear: `holder` probes may not come within R - tol of the axis
    along the drum, and a table `plane` = (m, d) may not be hit. `margin` shrinks each side,
    for material between probes. Also returns the extent (lo_on, hi_on) along the axis of the
    part material the drum touches at its middle.
    """
    half, reach = length / 2, length / 2 + band / 2
    lo_on, hi_on, past_lo, past_hi = _along(points, normals, axes, probes, radius, half, reach, radius + gap)
    lo = np.maximum.reduce([np.full(len(points), -band / 2), -half - lo_on, half - past_hi])
    hi = np.minimum.reduce([np.full(len(points), band / 2), half - hi_on, -half - past_lo])
    if holder is not None:  # holder material on the drum at its middle would be a collision, which step 2 rules out
        _, _, past_lo, past_hi = _along(points, normals, axes, holder, radius, half, reach, radius - tol)
        lo, hi = np.maximum(lo, half - past_hi), np.minimum(hi, -half - past_lo)
    if plane is not None:
        # The drum reaches c.m + (L/2)|u| + R sqrt(1 - u^2) along m (u = a.m); the offset moves c by -s a.
        m, d = np.asarray(plane[0], float), float(plane[1])
        u = axes @ m
        slack = d + tol - ((points + radius * normals) @ m + half * np.abs(u) + radius * np.sqrt(np.maximum(1 - u * u, 0)))
        bound = np.maximum(slack, 0.0) / np.maximum(np.abs(u), 1e-12)
        lo = np.where(u > 0, np.maximum(lo, -bound), lo)
        hi = np.where(u < 0, np.minimum(hi, bound), hi)
    return np.minimum(lo + margin, 0.0), np.maximum(hi - margin, 0.0), lo_on, hi_on


def sweep(points, normals, edges, along, sweeps):
    """Sweep phase in [0, 1] per point: `sweeps` passes across the band, even by area.

    The phase grows with the distance along the surface (over the neighbor graph) from the
    lowest point of each connected region along `along`. Ranked by area it is uniform, and
    folding it `sweeps` times gives a triangle wave that crosses the band back and forth.
    """
    count = len(points)
    length = np.linalg.norm(points[edges[:, 0]] - points[edges[:, 1]], axis=1)
    graph = csr_matrix((length + 1e-12, (edges[:, 0], edges[:, 1])), shape=(count, count))  # positive: zeros drop edges
    _, label = connected_components(graph, directed=False)
    by_height = np.lexsort((points @ (np.asarray(along, float) / np.linalg.norm(along)), label))
    lowest = by_height[np.searchsorted(label[by_height], np.arange(label.max() + 1))]
    dist = dijkstra(graph, directed=False, indices=lowest, min_only=True)
    area = 1 / np.abs(normals).max(axis=1)  # tri-dexel points stand for h^2 / |n|max of surface each
    by_dist = np.argsort(dist, kind="stable")
    share = np.empty(count)
    share[by_dist] = (np.cumsum(area[by_dist]) - area[by_dist] / 2) / area.sum()
    phase = (sweeps * share) % 2
    return np.minimum(phase, 2 - phase)


def integrate(points, directions, edges):
    """Least-squares integral of a direction field over the neighbor graph: u with u_j - u_i ~ d.(p_j - p_i).

    `d` is the pair's mean direction; pairs whose directions disagree in sign (a seam of the line
    field) are left out. Each connected region's level is arbitrary. Returns u and the graph of
    the pairs used.
    """
    count = len(points)
    keep = edges[np.einsum("ij,ij->i", directions[edges[:, 0]], directions[edges[:, 1]]) > 0]
    mean = directions[keep[:, 0]] + directions[keep[:, 1]]
    mean /= np.linalg.norm(mean, axis=1, keepdims=True)
    rise = np.einsum("ij,ij->i", mean, points[keep[:, 1]] - points[keep[:, 0]])
    rows, cols = np.concatenate([keep[:, 0], keep[:, 1]]), np.concatenate([keep[:, 1], keep[:, 0]])
    link = csr_matrix((np.ones(rows.size), (rows, cols)), shape=(count, count))
    lap = diags(np.asarray(link.sum(axis=1)).ravel() + 1e-8) - link  # the tiny diagonal fixes each region's level
    return splu(lap.tocsc()).solve(np.bincount(keep[:, 1], rise, count) - np.bincount(keep[:, 0], rise, count)), link


def across(points, axes, edges):
    """Coordinate across the passes, along the drum axis: what `passes` cuts into strips.

    It integrates the drum axis over the surface (`integrate`). Each connected region's coordinate
    is then shifted to match, on average, the coordinate along the part's main axis direction, so
    passes line up across regions. Where the axis turns all the way round a feature (a ring, a loop
    of walls) it cannot be integrated, and passes there bend and widen.
    """
    out, link = integrate(points, axes, edges)
    main = np.linalg.eigh(axes.T @ axes)[1][:, -1]
    main *= np.sign(np.sum(axes @ main)) or 1.0
    _, label = connected_components(link, directed=False)
    return out + (np.bincount(label, points @ main - out) / np.bincount(label))[label]


def passes(points, axes, edges, spacing):
    """Pass number per point: strips `spacing` wide along Y (square to the drum axis), numbered across the part."""
    u = across(points, axes, edges)
    return np.floor((u - u.min()) / spacing).astype(np.int64)


def stepped(lo, hi, index, band, step, score=None, tries=16):
    """Contact offset per point: constant along each pass, and `step` further along the drum each pass.

    Passes are taken in order, and after each one the contact moves `step` along the drum, back
    and forth across the band (a triangle wave). A pass whose safe range (the overlap of its
    points' ranges) does not reach that position takes the nearest one it can, and the next
    passes carry on with the wave. Given `score` (per-pass offsets -> a cost), the wave starts at
    whichever of `tries` points along it costs least; otherwise it starts at one end. Returns the
    per-point offsets and, per pass number, its offset (NaN for numbers no point has).
    """
    count = int(index.max()) + 1 if index.size else 0
    low, high = np.full(count, -np.inf), np.full(count, np.inf)
    np.maximum.at(low, index, lo)
    np.minimum.at(high, index, hi)
    used = np.flatnonzero(np.isfinite(low))
    best = (np.inf, np.zeros(count))
    for start in np.arange(tries if score else 1) * 2 * band / tries:
        travel = (start + np.arange(used.size) * step) % (2 * band)
        offset = np.full(count, np.nan)
        offset[used] = np.clip(np.minimum(travel, 2 * band - travel) - band / 2, low[used], high[used])
        value = score(offset) if score else 0.0
        if value < best[0] - 1e-12:
            best = (value, offset)
    return (best[1][index] if count else np.zeros(0)), best[1]


def pass_wear(index, lo_on, hi_on, area, length, stations=401):
    """Busiest inch of the drum for per-pass offsets: a function for `stepped` to score with.

    Each pass's work, spread over its points' contact patches, is tabulated once at offset 0 as a
    cumulative curve along the drum; an offset just shifts the curve.
    """
    count = int(index.max()) + 1 if index.size else 0
    along = np.linspace(-length / 2, length / 2, stations)
    start, span = lo_on, hi_on - lo_on + 1e-9
    done = np.array([np.bincount(index, area * np.clip((x - start) / span, 0.0, 1.0), count) for x in along]).T
    window = int(round(1.0 / (along[1] - along[0])))

    def busiest_inch(offset):
        total = np.zeros(stations)
        for k in np.flatnonzero(np.isfinite(offset)):
            total += np.interp(along - offset[k], along, done[k])
        return (total[window:] - total[:-window]).max() / max(total[-1], 1e-300)

    return busiest_inch


def _below(values, rows, cols, length):
    """min over j of (values_j + length_ij), with length the slope-scaled distance over the neighbor graph.

    That is the largest function under `values` that changes by at most the slope per inch:
    one shortest-path search from a virtual source tied to every point.
    """
    count = len(values)
    if count == 0:
        return values
    base = values.min()
    weights = np.concatenate([length, values - base + 1e-9]) + 1e-12  # kept positive: zero weights drop edges
    graph = csr_matrix((weights, (np.append(rows, np.full(count, count)), np.append(cols, np.arange(count)))),
                       shape=(count + 1, count + 1))
    return dijkstra(graph, indices=count)[:count] - 1e-9 + base


def offsets(lo, hi, phase, band, edges, points, area, slope, rounds=8):
    """Contact offset per point: the sweep across the band, within the safe range, smooth and even.

    The safe range is first eased: its limits are tightened so they change by at most `slope`
    per inch along the surface. The sweep is clipped to it, which piles contact toward the
    middle near restricted points, so the sweep is then re-spread for a few rounds: each point's
    sweep value moves to its rank, by area, among the clipped offsets, and is smoothed to the
    same slope limit. Free points take up the slack, so the band stays evenly used, and the
    result never changes by more than `slope` per inch between neighbors.
    """
    rows, cols = np.concatenate([edges[:, 0], edges[:, 1]]), np.concatenate([edges[:, 1], edges[:, 0]])
    length = slope * np.linalg.norm(points[rows] - points[cols], axis=1)
    lo_e = np.clip(-_below(-lo, rows, cols, length), lo, 0.0)
    hi_e = np.clip(_below(hi, rows, cols, length), 0.0, hi)
    target = band * (phase - 0.5)
    for _ in range(rounds):
        s = np.clip(target, lo_e, hi_e)
        order = np.argsort(s, kind="stable")
        target = band * (np.interp(target, s[order], np.cumsum(area[order]) / area.sum()) - 0.5)
        target = (_below(target, rows, cols, length) - _below(-target, rows, cols, length)) / 2
    return np.clip(target, lo_e, hi_e)


def wear(track, lo_on, hi_on, weights, length, bin_in=1.0):
    """Share of the drum's work done by each `bin_in` stretch of it.

    Each point's weight (its area) is spread evenly over the stretch of drum it touches,
    [track + lo_on, track + hi_on]. Returns the bin edges along the drum (0 = its middle) and
    the share per bin.
    """
    edges = np.arange(-length / 2, length / 2 + 1e-9, bin_in)
    start, span = track + lo_on, hi_on - lo_on + 1e-9
    done = np.array([weights @ np.clip((x - start) / span, 0.0, 1.0) for x in edges])
    return edges, np.diff(done) / max(weights.sum(), 1e-300)


def busiest(track, lo_on, hi_on, weights, length, window=1.0, step=0.05):
    """Largest share of the drum's work done by any `window` inches of it."""
    _, share = wear(track, lo_on, hi_on, weights, length, step)
    return float(np.convolve(share, np.ones(int(round(window / step))), "valid").max())


def surroundings(result, band, probes=None):
    """What the drum must keep to: the part's probes, and the holder's probes or table plane (else None).

    The fixture is as in step 2, but as deep as the drum can reach with its contact anywhere in `band`.
    """
    mesh, params = result["mesh"], result["params"]
    probes = probe_points(mesh, params["probe_spacing"]) if probes is None else probes
    holder = plane = None
    if params["holder"] == "fixture":
        spacing = max(params["probe_spacing"], 0.05)
        holder = fixture_probes(front_view(mesh, params["mount"], min(params["spacing"], 0.01) / 2), params["inset"],
                                spacing, np.hypot(params["length"] / 2 + band / 2, 2 * params["radius"]) + spacing)
    elif params["holder"] == "table":
        plane = table(mesh, params["mount"])[0]
    return probes, holder, plane


def plan(result, band=None, mode="passes", step=None, spacing=0.1, sweeps=1, gap=0.01, slope=8.0, probes=None):
    """Step 4 on a step-3 result: the safe range and the tracking offset of every reachable point.

    `band` is the middle stretch of drum the contact may use (default 90% of its length) and `gap`
    how close part material must be to count as touching. With `mode` "passes" the contact holds
    still along each pass, strips `spacing` apart along Y, and moves `step` along the drum between
    passes (default: the band over the number of passes, so one part uses all of it). With "sweep"
    it crosses the band `sweeps` times over the part, changing by at most `slope` per inch along
    the surface. Returns per-point arrays over the whole grid, NaN where unreachable: `track` (the
    contact's offset from the drum's middle along its axis, inches), `track_lo` and `track_hi` (its
    safe range), `patch_lo` and `patch_hi` (the stretch of drum touching the part, relative to the
    contact), `pass` (the pass number, -1 where there is none) and `stats`.
    """
    params = result["params"]
    radius, length, tol = params["radius"], params["length"], params["tolerance"]
    band = 0.9 * length if band is None else float(band)
    idx = np.flatnonzero(np.isfinite(result["axis"][:, 0]))
    points, normals, axes = result["points"][idx], result["normals"][idx], result["axis"][idx]
    probes, holder, plane = surroundings(result, band, probes)
    lo, hi, lo_on, hi_on = limits(points, normals, axes, probes, radius, length, band, gap, holder, plane,
                                  params["probe_spacing"], tol)
    remap = np.full(len(result["points"]), -1)
    remap[idx] = np.arange(idx.size)
    edges = remap[result["pair_edges"]]
    area = 1 / np.abs(normals).max(axis=1)  # tri-dexel points stand for h^2 / |n|max of surface each
    track, number = np.zeros(idx.size), np.full(idx.size, -1)
    stats = {"band": band, "mode": mode, "gap": gap}
    if idx.size and mode == "sweep":
        track = offsets(lo, hi, sweep(points, normals, edges, params["grain"] or (0.0, 0.0, 1.0), sweeps), band, edges,
                        points, area, slope)
        step_len = np.linalg.norm(points[edges[:, 0]] - points[edges[:, 1]], axis=1)
        rate = np.abs(track[edges[:, 0]] - track[edges[:, 1]]) / np.maximum(step_len, 1e-12)
        stats.update(sweeps=sweeps, slope=slope, rate_p50=float(np.median(rate)) if rate.size else 0.0,
                     rate_max=float(rate.max()) if rate.size else 0.0)
    elif idx.size:
        number = passes(points, axes, edges, spacing)
        used = np.unique(number).size
        step = band / max(used - 1, 1) if step is None else float(step)
        track, per_pass = stepped(lo, hi, number, band, step, pass_wear(number, lo_on, hi_on, area, length))
        moves = np.abs(np.diff(per_pass[np.isfinite(per_pass)]))
        stats.update(passes=used, spacing=spacing, step=step,
                     moves_one_step_pct=float(100 * np.mean(np.abs(moves - step) < 1e-9)) if moves.size else 100.0,
                     move_max=float(moves.max()) if moves.size else 0.0)
    out = {}
    for key, values, empty in (("track", track, np.nan), ("track_lo", lo, np.nan), ("track_hi", hi, np.nan),
                               ("patch_lo", lo_on, np.nan), ("patch_hi", hi_on, np.nan), ("pass", number, -1)):
        out[key] = np.full(len(result["points"]), empty, dtype=np.asarray(values).dtype if key == "pass" else float)
        out[key][idx] = values
    full = hi - lo >= 0.99 * band  # the probe margin alone takes about 0.5%
    stats.update(full_band_pct=float(100 * full.mean()) if idx.size else 0.0,
                 busiest_inch_pct=100 * busiest(track, lo_on, hi_on, area, length) if idx.size else 0.0,
                 busiest_inch_pct_untracked=100 * busiest(0 * track, lo_on, hi_on, area, length) if idx.size else 0.0,
                 even_inch_pct=100 / band if band else 0.0)
    out["stats"] = stats
    return out
