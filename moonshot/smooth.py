"""Step 3: pick one feasible orientation per point with the least angular change between neighbors.

Each reachable point chooses an axis angle theta_i from its feasible bins. The
cylinder is symmetric end to end, so an orientation is a line (theta ~ theta + pi).
The energy is

    E = sum over neighbor pairs (i, j) of sin^2(theta_i - theta_j - delta_ij)

where delta_ij carries j's tangent frame into i's by the minimal rotation of n_j onto
n_i. E therefore counts how much the cylinder turns relative to the surface between
neighbors; for line fields this is the same optimum as minimizing the rotation of the
full tool frames. A grain term adds w * area_i * (a_i . g)^2 per point: it keeps the axis
a_i square to the wood grain g (with g = Z, |a.g| = sin|ry|, the rotation about Y), and by
default strongly enough to win wherever the part allows. With u_i = exp(2i theta_i) the smoothness energy is
|u_i - exp(2i delta_ij) u_j|^2 / 4 summed over pairs: a connection Laplacian, and the
per-point terms are linear in u_i, so the global shape of the field comes from sparse
linear solves. The solver:

1. Global relaxation: solve the connection Laplacian with the per-point pulls (grain, plus a
   weak tie-break toward a preferred direction), and penalties that pull points whose relaxed
   angle is infeasible toward their nearest feasible angle, raising the penalty each round.
2. Polish: snap to feasible bins, then graph-colored coordinate descent (ICM). Every
   update moves one point to its locally best feasible bin, so E only goes down and
   the result never leaves the feasible set. Each color class updates in parallel.
3. Refine: the same descent with continuous angles, each point confined to the
   collision-free arc around its bin, so the output is not quantized to bins.
"""
import numpy as np
from numba import njit, prange
from scipy.sparse import csr_matrix, diags
from scipy.sparse.csgraph import breadth_first_order, connected_components
from scipy.sparse.linalg import LinearOperator, cg, splu


def transport(normals, t1, t2, edges):
    """Angle that carries tangent frame j into frame i, for each pair (i, j)."""
    ni, nj = normals[edges[:, 0]], normals[edges[:, 1]]
    k = np.cross(nj, ni)
    v = t1[edges[:, 1]]
    qv = v + np.cross(k, v) + np.cross(k, np.cross(k, v)) / (1 + np.sum(nj * ni, axis=1, keepdims=True))
    return np.arctan2(np.sum(qv * t2[edges[:, 0]], axis=1), np.sum(qv * t1[edges[:, 0]], axis=1))


def line_change(theta, delta, edges):
    """Turn of the cylinder relative to the surface across each pair, in degrees (0..90)."""
    d = theta[edges[:, 0]] - theta[edges[:, 1]] - delta
    return np.degrees(np.arccos(np.clip(np.abs(np.cos(d)), 0.0, 1.0)))


@njit(cache=True)
def _nearest_bin(row, target, step):
    """Feasible bin whose center is closest to `target` (radians, mod pi), or -1 if none."""
    nb = row.size
    kt = int(target / step) % nb
    best, best_d = -1, 1e9
    for d in range(nb // 2 + 2):
        for k in (kt + d, kt - d):
            kk = k % nb
            if row[kk]:
                dist = abs((target - (kk + 0.5) * step + np.pi / 2) % np.pi - np.pi / 2)
                if dist < best_d:
                    best, best_d = kk, dist
        if best >= 0 and d * step > best_d + step:
            break
    return best


@njit(parallel=True, cache=True)
def _nearest_bins(feasible, targets, step):
    """Vectorized `_nearest_bin` over points."""
    out = np.empty(feasible.shape[0], np.int64)
    for i in prange(feasible.shape[0]):
        out[i] = _nearest_bin(feasible[i], targets[i], step)
    return out


@njit(parallel=True, cache=True)
def _energy(theta, e, delta, pull_re, pull_im):
    """Total energy: sin^2 of the transported angle difference over pairs, plus the per-point pulls."""
    total = 0.0
    for k in prange(e.shape[0]):
        total += np.sin(theta[e[k, 0]] - theta[e[k, 1]] - delta[k]) ** 2
    for i in prange(theta.size):
        total += 0.5 * (np.hypot(pull_re[i], pull_im[i]) - pull_re[i] * np.cos(2 * theta[i]) - pull_im[i] * np.sin(2 * theta[i]))
    return total


@njit(parallel=True, cache=True)
def _arcs(bins, feasible, step):
    """Unwrapped angle bounds of the run of feasible bins containing each point's bin."""
    count, nb = feasible.shape
    lo, hi = np.empty(count), np.empty(count)
    for i in prange(count):
        b = bins[i]
        left = 0
        while left < nb and feasible[i, (b - left - 1) % nb]:
            left += 1
        right = 0
        while right < nb and feasible[i, (b + right + 1) % nb]:
            right += 1
        if left >= nb - 1:  # every angle is free
            lo[i], hi[i] = -np.inf, np.inf
        else:
            lo[i], hi[i] = (b - left) * step + 1e-9, (b + right + 1) * step - 1e-9
    return lo, hi


@njit(parallel=True, cache=True)
def _refine_sweep(theta, lo, hi, indptr, indices, phase, prior_re, prior_im, order, color_ptr):
    """Continuous coordinate descent: each point moves to its best angle inside its own arc."""
    moved = 0.0
    for c in range(color_ptr.size - 1):
        for t in prange(color_ptr[c], color_ptr[c + 1]):
            i = order[t]
            mr, mi = prior_re[i], prior_im[i]
            for e in range(indptr[i], indptr[i + 1]):
                ang = 2.0 * (theta[indices[e]] + phase[e])
                mr += np.cos(ang)
                mi += np.sin(ang)
            if mr * mr + mi * mi < 1e-24:
                continue
            target = np.arctan2(mi, mr) / 2
            target += np.pi * np.round((0.5 * (lo[i] + hi[i]) - target) / np.pi) if np.isfinite(lo[i]) else \
                np.pi * np.round((theta[i] - target) / np.pi)
            if target < lo[i] or target > hi[i]:
                # Outside the arc: the better end is the one circularly closer to the target.
                dlo = abs((target - lo[i] + np.pi / 2) % np.pi - np.pi / 2)
                dhi = abs((target - hi[i] + np.pi / 2) % np.pi - np.pi / 2)
                target = lo[i] if dlo < dhi else hi[i]
            moved += abs(target - theta[i])
            theta[i] = target
    return moved


@njit(cache=True)
def _greedy_colors(indptr, indices):
    """Greedy graph coloring; nodes of one color share no edge, so they can update together."""
    n = indptr.size - 1
    color = -np.ones(n, np.int64)
    taken = -np.ones(np.max(indptr[1:] - indptr[:-1]) + 2, np.int64)
    for i in range(n):
        for e in range(indptr[i], indptr[i + 1]):
            c = color[indices[e]]
            if c >= 0:
                taken[c] = i
        c = 0
        while taken[c] == i:
            c += 1
        color[i] = c
    return color


@njit(parallel=True, cache=True)
def _icm_sweep(bins, feasible, indptr, indices, phase, prior_re, prior_im, order, color_ptr, step):
    """One coordinate-descent sweep: each point takes its locally optimal feasible bin."""
    changed = 0
    for c in range(color_ptr.size - 1):
        for t in prange(color_ptr[c], color_ptr[c + 1]):
            i = order[t]
            mr, mi = prior_re[i], prior_im[i]
            for e in range(indptr[i], indptr[i + 1]):
                ang = 2.0 * ((bins[indices[e]] + 0.5) * step + phase[e])
                mr += np.cos(ang)
                mi += np.sin(ang)
            if mr * mr + mi * mi < 1e-24:
                continue
            target = np.arctan2(mi, mr)
            k = _nearest_bin(feasible[i], (target / 2) % np.pi, step)
            # Move only on a strict improvement, so ties cannot flip back and forth.
            if k != bins[i] and np.cos(2 * (k + 0.5) * step - target) > np.cos(2 * (bins[i] + 0.5) * step - target) + 1e-12:
                bins[i] = k
                changed += 1
    return changed


def optimize(points, normals, t1, t2, feasible, edges, prefer=(1.0, 0.0, 0.0), prior=0.01, grain=(0.0, 0.0, 1.0),
             grain_weight=1e6, penalties=(0.1, 0.4, 1.6, 6.4, 25.6), sweeps=100, settle=1e-4, refine=200,
             cluster_steps=4, rtol=1e-3):
    """Choose a smooth, feasible, sign-consistent axis angle for every reachable point.

    `grain_weight` is the cost of (a . g)^2 per square inch (g = `grain` normalized, None for no
    grain). At the default, tilting the axis 1 deg toward the grain costs as much as the field turning
    7 rad per inch, so the tilt stays within about a degree of the lowest the collisions allow; smaller
    weights trade tilt for smoother motion. `prior` is the pull toward `prefer` per square inch; it only breaks
    ties where nothing else decides. Scaling both by the grid step keeps results independent of
    spacing. Returns a dict with `theta` (radians, axis = cos t1 + sin t2, NaN where unreachable),
    `naive` (each point's own best choice, ignoring its neighbors, for comparison), `delta` and
    `edges` restricted to reachable points, and the energy `history`.
    """
    nb = feasible.shape[1]
    step = np.pi / nb
    alive = feasible.any(axis=1)
    idx = np.flatnonzero(alive)
    remap = np.full(len(feasible), -1)
    remap[idx] = np.arange(idx.size)
    e = remap[edges[alive[edges[:, 0]] & alive[edges[:, 1]]]]
    n, a1, a2, feas = normals[idx], t1[idx], t2[idx], np.ascontiguousarray(feasible[idx])
    count = idx.size
    delta = transport(n, a1, a2, e)
    rows, cols = np.concatenate([e[:, 0], e[:, 1]]), np.concatenate([e[:, 1], e[:, 0]])
    phase = np.concatenate([delta, -delta])

    # Per-point pulls, as doubled-angle phasors: toward the preferred direction projected into the
    # tangent plane (weak), and away from the grain's projection, which is (a.g)^2 = (|g2| + Re(u g2*)) / 2.
    d = np.asarray(prefer, float)
    pts = points[idx]
    grid_step = np.median(np.linalg.norm(pts[e[:, 0]] - pts[e[:, 1]], axis=1))
    p2 = prior * grid_step ** 2 * (a1 @ d + 1j * (a2 @ d)) ** 2
    if grain is not None:
        g = np.asarray(grain, float) / np.linalg.norm(grain)
        p2 = p2 - grain_weight * grid_step ** 2 * (a1 @ g + 1j * (a2 @ g)) ** 2
    naive = _nearest_bins(feas, (np.angle(p2) / 2) % np.pi, step)

    lap = csr_matrix((-np.exp(2j * phase), (rows, cols)), shape=(count, count))
    lap = lap + diags(np.bincount(rows, minlength=count).astype(float))

    # Two-level preconditioner: Jacobi for fine detail plus an exact solve on clusters of about
    # cluster_steps x cluster_steps grid cells (split by facing), each in its first point's frame.
    size = cluster_steps * grid_step
    major = np.argmax(np.abs(n), axis=1)
    facing = 2 * major + (n[np.arange(count), major] > 0)
    _, first, cluster = np.unique(np.column_stack([np.floor(pts / size).astype(np.int64), facing]), axis=0,
                                  return_index=True, return_inverse=True)
    cluster = cluster.ravel()
    to_rep = transport(n, a1, a2, np.column_stack([np.arange(count), first[cluster]]))
    prolong = csr_matrix((np.exp(2j * to_rep), (np.arange(count), cluster)), shape=(count, first.size))
    restrict = prolong.conj().T.tocsr()
    constrained = ~feas.all(axis=1)
    active = np.zeros(count, bool)
    bins = naive.copy()
    u = np.exp(2j * (bins + 0.5) * step)
    history = [("naive", _energy((naive + 0.5) * step, e, delta, p2.real, p2.imag))]
    for mu in penalties:
        relaxed = (np.angle(u) / 2) % np.pi
        active |= constrained & ~feas[np.arange(count), (relaxed / step).astype(int) % nb]
        guide = np.exp(2j * (_nearest_bins(feas, relaxed, step) + 0.5) * step)
        # The 1e-6 proximal term keeps components with no prior or penalty well posed.
        weight = np.abs(p2) + mu * active + 1e-6
        a = (lap + diags(weight)).tocsr()
        coarse = splu((restrict @ a @ prolong).tocsc())
        dinv = 1.0 / a.diagonal()
        precond = LinearOperator(a.shape, matvec=lambda r: dinv * r + prolong @ coarse.solve(restrict @ r),
                                 dtype=complex)
        u, _ = cg(a, p2 + mu * active * guide + 1e-6 * u, x0=u, rtol=rtol, maxiter=300, M=precond)
        bins = _nearest_bins(feas, (np.angle(u) / 2) % np.pi, step)
        history.append((f"relax mu={mu:g}", _energy((bins + 0.5) * step, e, delta, p2.real, p2.imag)))

    by_row = np.lexsort((cols, rows))
    indptr = np.searchsorted(rows[by_row], np.arange(count + 1))
    indices, row_phase = cols[by_row], phase[by_row]
    color = _greedy_colors(indptr, indices)
    order = np.argsort(color, kind="stable")
    color_ptr = np.searchsorted(color[order], np.arange(color.max() + 2))
    for s in range(sweeps):
        changed = _icm_sweep(bins, feas, indptr, indices, row_phase, p2.real, p2.imag, order, color_ptr, step)
        history.append((f"polish {s + 1}", _energy((bins + 0.5) * step, e, delta, p2.real, p2.imag)))
        if changed <= settle * count:
            break

    theta = (bins + 0.5) * step
    lo, hi = _arcs(bins, feas, step)
    for s in range(refine):
        moved = _refine_sweep(theta, lo, hi, indptr, indices, row_phase, p2.real, p2.imag, order, color_ptr)
        history.append((f"refine {s + 1}", _energy(theta, e, delta, p2.real, p2.imag)))
        if moved < 1e-6 * count:
            break

    # Choose axis signs so neighbors point the same way (continuous Euler angles). One BFS from a
    # virtual node tied to each component's root (its axis most aligned with `prefer`) covers all.
    graph = csr_matrix((np.ones(rows.size), (rows, cols)), shape=(count, count))
    ncomp, label = connected_components(graph, directed=False)
    pref = a1 @ d + 1j * (a2 @ d)
    along = np.abs(pref) * np.cos(theta - np.angle(pref))
    by_label = np.lexsort((-np.abs(along), label))
    roots = by_label[np.searchsorted(label[by_label], np.arange(ncomp))]
    links = np.concatenate([rows, roots, np.full(ncomp, count)]), np.concatenate([cols, np.full(ncomp, count), roots])
    seq, pred = breadth_first_order(csr_matrix((np.ones(links[0].size), links), shape=(count + 1, count + 1)),
                                    count, directed=False)
    sign = np.ones(count)
    sign[roots] = np.where(along[roots] >= 0, 1.0, -1.0)
    child = seq[1:][pred[seq[1:]] != count]
    flip = np.cos(theta[pred[child]] - theta[child] - transport(n, a1, a2, np.column_stack([pred[child], child]))) < 0
    for node, f in zip(child.tolist(), flip.tolist()):
        sign[node] = -sign[pred[node]] if f else sign[pred[node]]
    full_theta = np.full(len(feasible), np.nan)
    full_theta[idx] = theta + np.pi * (sign < 0)
    full_naive = np.full(len(feasible), np.nan)
    full_naive[idx] = (naive + 0.5) * step
    return {"theta": full_theta, "naive": full_naive, "edges": idx[e], "delta": delta, "history": history}
