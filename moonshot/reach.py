"""Step 2: which cylinder orientations touch each grid point without colliding.

A cylinder of radius R and length L that touches grid point p tangentially, at the
middle of its length, has its axis through c = p + R*n lying in the tangent plane.
Its pose therefore has exactly one free parameter: the axis angle theta in the local
tangent frame, over [0, pi) because the cylinder is symmetric end to end.

The solid's surface is sampled densely with probe points. For a probe q, write
q - c = (x, y, h) in the frame (t1, t2, n). The probe is inside the cylinder at
angle theta exactly when

    h^2 + r^2 sin^2(theta - phi) < (R - tol)^2   and   r^2 cos^2(theta - phi) < (L/2)^2,

with r, phi the polar coordinates of (x, y). That is the band
1 - (L/2)^2/r^2 < sin^2(theta - phi) < ((R - tol)^2 - h^2)/r^2, which is at most two
theta intervals in closed form. The colliding orientations of p are the union of
those intervals over all nearby probes, accumulated per angle bin with a circular
difference array, so no orientation between bins is ever skipped. A table under the
part is a plane x.m = d (m points from the part into the table) and is handled
analytically: the cylinder reaches c.m + (L/2)|a.m| + R*sqrt(1 - (a.m)^2) along m.
"""
import functools
import math

import numpy as np
from numba import njit, prange

from .grid import surface_grid


def probe_points(mesh, spacing, edge_deg=20.0):
    """Dense collision probes on the solid: a surface lattice plus fine samples along sharp edges."""
    lattice = surface_grid(mesh, spacing)[0]
    ends = mesh.vertices[mesh.face_adjacency_edges[mesh.face_adjacency_angles > np.radians(edge_deg)]]
    counts = np.ceil(np.linalg.norm(ends[:, 1] - ends[:, 0], axis=1) / (spacing / 4)).astype(int) + 1
    seg = np.repeat(np.arange(len(ends)), counts)
    t = (np.arange(seg.size) - np.repeat(np.cumsum(counts) - counts, counts)) / np.repeat(counts - 1, counts)
    edge_pts = ends[seg, 0] + t[:, None] * (ends[seg, 1] - ends[seg, 0])
    return np.vstack([lattice, edge_pts])


@njit(cache=True)
def _mark(diff, start, span, step, nb):
    """Add the closed angle interval [start, start + span] (mod pi) to a circular difference array.

    Returns True when the interval covers every bin.
    """
    s = start % np.pi
    k0 = min(int(s / step), nb - 1)
    k1 = int((s + span) / step)
    if k1 - k0 + 1 >= nb:
        return True
    if k1 < nb:
        diff[k0] += 1
        diff[k1 + 1] -= 1
    else:
        diff[k0] += 1
        diff[nb] -= 1
        diff[0] += 1
        diff[k1 - nb + 1] -= 1
    return False


@njit(cache=True)
def _skip_box(lo, hi, b, cx, cy, cz, n0, n1, n2, rb2, rp):
    """True when box b holds no probe the cylinder can reach: beyond its radius or outside its slab."""
    ex = max(lo[b, 0] - cx, 0.0, cx - hi[b, 0])
    ey = max(lo[b, 1] - cy, 0.0, cy - hi[b, 1])
    ez = max(lo[b, 2] - cz, 0.0, cz - hi[b, 2])
    if ex * ex + ey * ey + ez * ez >= rb2:
        return True
    mid = ((lo[b, 0] + hi[b, 0]) / 2 - cx) * n0 + ((lo[b, 1] + hi[b, 1]) / 2 - cy) * n1 \
        + ((lo[b, 2] + hi[b, 2]) / 2 - cz) * n2
    half = ((hi[b, 0] - lo[b, 0]) * abs(n0) + (hi[b, 1] - lo[b, 1]) * abs(n1) + (hi[b, 2] - lo[b, 2]) * abs(n2)) / 2
    return mid + half <= -rp or mid - half >= rp


@njit(parallel=True, cache=True)
def _collisions(pts, nrm, t1, t2, order, probes, origin, cell, dims, offsets, cstart, cend, clo, chi,
                fstart, fend, flo, fhi, radius, half_len, tol, nb, pm, pd, out_self, out_plane):
    """Fill per-point, per-bin collision flags for the plane x.pm = pd and, where it leaves room, the probes.

    Self flags are only resolved for bins the plane does not already block; the scan
    stops once every bin is blocked one way or the other.
    """
    step = np.pi / nb
    rp = radius - tol
    rp2 = rp * rp
    hl2 = half_len * half_len
    rb2 = rp2 + hl2
    # Along m the cylinder reaches furthest past its center when |a.m| = ustar (its rim corner).
    ustar = half_len / np.sqrt(hl2 + radius * radius)
    for jj in prange(pts.shape[0]):
        i = order[jj]
        n0, n1, n2 = nrm[i, 0], nrm[i, 1], nrm[i, 2]
        cx, cy, cz = pts[i, 0] + radius * n0, pts[i, 1] + radius * n1, pts[i, 2] + radius * n2
        free_bins = nb
        if pd < np.inf:
            cm = cx * pm[0] + cy * pm[1] + cz * pm[2]
            am1 = t1[i, 0] * pm[0] + t1[i, 1] * pm[1] + t1[i, 2] * pm[2]
            am2 = t2[i, 0] * pm[0] + t2[i, 1] * pm[1] + t2[i, 2] * pm[2]
            amp = np.sqrt(am1 * am1 + am2 * am2)
            peak = np.arctan2(am2, am1) % np.pi  # |a.m| is largest here and zero a quarter turn away
            zero = (peak + np.pi / 2) % np.pi
            for k in range(nb):
                th0, th1 = k * step, (k + 1) * step
                u0 = abs(am1 * np.cos(th0) + am2 * np.sin(th0))
                u1 = abs(am1 * np.cos(th1) + am2 * np.sin(th1))
                umin, umax = min(u0, u1), max(u0, u1)
                if th0 <= peak <= th1:
                    umax = amp
                if th0 <= zero <= th1:
                    umin = 0.0
                u = min(max(ustar, umin), umax)
                blocked = cm + half_len * u + radius * np.sqrt(max(0.0, 1.0 - u * u)) > pd + tol
                out_plane[i, k] = blocked
                free_bins -= blocked
        if free_bins == 0:
            continue
        diff = np.zeros(nb + 1, np.int32)
        full = False
        hx, hy, hz = int((cx - origin[0]) / cell), int((cy - origin[1]) / cell), int((cz - origin[2]) / cell)
        for o in range(offsets.shape[0]):  # nearest cells first, so deep collisions end the scan early
            gx, gy, gz = hx + offsets[o, 0], hy + offsets[o, 1], hz + offsets[o, 2]
            if gx < 0 or gy < 0 or gz < 0 or gx >= dims[0] or gy >= dims[1] or gz >= dims[2]:
                continue
            cid = (gx * dims[1] + gy) * dims[2] + gz
            if _skip_box(clo, chi, cid, cx, cy, cz, n0, n1, n2, rb2, rp):
                continue
            marked = False
            for f in range(cstart[cid], cend[cid]):  # fine cells: tight boxes cull most of a long slab
                if _skip_box(flo, fhi, f, cx, cy, cz, n0, n1, n2, rb2, rp):
                    continue
                for q in range(fstart[f], fend[f]):
                    dx, dy, dz = probes[q, 0] - cx, probes[q, 1] - cy, probes[q, 2] - cz
                    h = dx * n0 + dy * n1 + dz * n2
                    if h <= -rp or h >= rp:
                        continue
                    d2 = dx * dx + dy * dy + dz * dz
                    if d2 >= rb2:
                        continue
                    x = dx * t1[i, 0] + dy * t1[i, 1] + dz * t1[i, 2]
                    y = dx * t2[i, 0] + dy * t2[i, 1] + dz * t2[i, 2]
                    r2 = x * x + y * y
                    if r2 < 1e-20:  # on the normal line through p: inside for every angle
                        full = True
                        break
                    lo = max(0.0, 1.0 - hl2 / r2)
                    hi = min(1.0, (rp2 - h * h) / r2)
                    if lo >= hi:
                        continue
                    phi = np.arctan2(y, x)
                    b_lo, b_hi = np.arcsin(np.sqrt(lo)), np.arcsin(np.sqrt(hi))
                    if hi >= 1.0 and lo <= 0.0:
                        full = True
                    elif hi >= 1.0:
                        full = _mark(diff, phi + b_lo, np.pi - 2 * b_lo, step, nb)
                    elif lo <= 0.0:
                        full = _mark(diff, phi - b_hi, 2 * b_hi, step, nb)
                    else:
                        full = (_mark(diff, phi + b_lo, b_hi - b_lo, step, nb)
                                or _mark(diff, phi + np.pi - b_hi, b_hi - b_lo, step, nb))
                    if full:
                        break
                    marked = True
                if full:
                    break
            if full:
                break
            if marked:  # stop once probes and plane together block every bin
                run = 0
                full = True
                for k in range(nb):
                    run += diff[k]
                    if run == 0 and not out_plane[i, k]:
                        full = False
                        break
                if full:
                    break
        run = 0
        for k in range(nb):
            run += diff[k]
            out_self[i, k] = full or run > 0


@functools.cache
def _cuda_kernel(nb):
    """GPU version of `_collisions` for `nb` bins: one thread block per grid point.

    The block's threads split the probes of each fine cell and add their intervals to a
    shared-memory difference array with atomics; the plane bins are split across threads.
    Built on first use so CPU-only installs never touch CUDA.
    """
    # Kernels must see `cuda` as a module global: numba's CUDA simulator swaps it in there.
    global cuda
    from numba import cuda, int32

    @cuda.jit(device=True)
    def mark(diff, start, span, step):
        s = start % math.pi
        k0 = min(int(s / step), nb - 1)
        k1 = int((s + span) / step)
        if k1 - k0 + 1 >= nb:
            return True
        cuda.atomic.add(diff, k0, 1)
        if k1 < nb:
            cuda.atomic.add(diff, k1 + 1, -1)
        else:
            cuda.atomic.add(diff, nb, -1)
            cuda.atomic.add(diff, 0, 1)
            cuda.atomic.add(diff, k1 - nb + 1, -1)
        return False

    @cuda.jit(device=True)
    def skip_box(lo, hi, b, cx, cy, cz, n0, n1, n2, rb2, rp):
        ex = max(lo[b, 0] - cx, 0.0, cx - hi[b, 0])
        ey = max(lo[b, 1] - cy, 0.0, cy - hi[b, 1])
        ez = max(lo[b, 2] - cz, 0.0, cz - hi[b, 2])
        if ex * ex + ey * ey + ez * ez >= rb2:
            return True
        mid = ((lo[b, 0] + hi[b, 0]) / 2 - cx) * n0 + ((lo[b, 1] + hi[b, 1]) / 2 - cy) * n1 \
            + ((lo[b, 2] + hi[b, 2]) / 2 - cz) * n2
        half = ((hi[b, 0] - lo[b, 0]) * abs(n0) + (hi[b, 1] - lo[b, 1]) * abs(n1) + (hi[b, 2] - lo[b, 2]) * abs(n2)) / 2
        return mid + half <= -rp or mid - half >= rp

    @cuda.jit
    def kernel(pts, nrm, t1, t2, probes, origin, cell, dims, offsets, cstart, cend, clo, chi, fstart, fend, flo, fhi,
               radius, half_len, tol, pm, pd, out_self, out_plane):
        i, tid, nthreads = cuda.blockIdx.x, cuda.threadIdx.x, cuda.blockDim.x
        diff = cuda.shared.array(nb + 1, int32)
        flags = cuda.shared.array(2, int32)  # [every bin blocked by one probe, bins blocked by the plane]
        for k in range(tid, nb + 1, nthreads):
            diff[k] = 0
        if tid == 0:
            flags[0], flags[1] = 0, 0
        cuda.syncthreads()
        step = math.pi / nb
        rp = radius - tol
        rp2, hl2 = rp * rp, half_len * half_len
        rb2 = rp2 + hl2
        n0, n1, n2 = nrm[i, 0], nrm[i, 1], nrm[i, 2]
        cx, cy, cz = pts[i, 0] + radius * n0, pts[i, 1] + radius * n1, pts[i, 2] + radius * n2
        if pd < math.inf:
            ustar = half_len / math.sqrt(hl2 + radius * radius)
            cm = cx * pm[0] + cy * pm[1] + cz * pm[2]
            am1 = t1[i, 0] * pm[0] + t1[i, 1] * pm[1] + t1[i, 2] * pm[2]
            am2 = t2[i, 0] * pm[0] + t2[i, 1] * pm[1] + t2[i, 2] * pm[2]
            amp = math.sqrt(am1 * am1 + am2 * am2)
            peak = math.atan2(am2, am1) % math.pi
            zero = (peak + math.pi / 2) % math.pi
            for k in range(tid, nb, nthreads):
                th0, th1 = k * step, (k + 1) * step
                u0, u1 = abs(am1 * math.cos(th0) + am2 * math.sin(th0)), abs(am1 * math.cos(th1) + am2 * math.sin(th1))
                umin, umax = min(u0, u1), max(u0, u1)
                if th0 <= peak <= th1:
                    umax = amp
                if th0 <= zero <= th1:
                    umin = 0.0
                u = min(max(ustar, umin), umax)
                blocked = cm + half_len * u + radius * math.sqrt(max(0.0, 1.0 - u * u)) > pd + tol
                out_plane[i, k] = blocked
                if blocked:
                    cuda.atomic.add(flags, 1, 1)
        cuda.syncthreads()
        if flags[1] < nb:
            hx, hy, hz = int((cx - origin[0]) / cell), int((cy - origin[1]) / cell), int((cz - origin[2]) / cell)
            for o in range(offsets.shape[0]):
                if flags[0]:
                    break
                gx, gy, gz = hx + offsets[o, 0], hy + offsets[o, 1], hz + offsets[o, 2]
                if gx < 0 or gy < 0 or gz < 0 or gx >= dims[0] or gy >= dims[1] or gz >= dims[2]:
                    continue
                cid = (gx * dims[1] + gy) * dims[2] + gz
                if skip_box(clo, chi, cid, cx, cy, cz, n0, n1, n2, rb2, rp):
                    continue
                for f in range(cstart[cid], cend[cid]):
                    if skip_box(flo, fhi, f, cx, cy, cz, n0, n1, n2, rb2, rp):
                        continue
                    for q in range(fstart[f] + tid, fend[f], nthreads):
                        dx, dy, dz = probes[q, 0] - cx, probes[q, 1] - cy, probes[q, 2] - cz
                        h = dx * n0 + dy * n1 + dz * n2
                        if h <= -rp or h >= rp or dx * dx + dy * dy + dz * dz >= rb2:
                            continue
                        x = dx * t1[i, 0] + dy * t1[i, 1] + dz * t1[i, 2]
                        y = dx * t2[i, 0] + dy * t2[i, 1] + dz * t2[i, 2]
                        r2 = x * x + y * y
                        full = r2 < 1e-20
                        if not full:
                            lo = max(0.0, 1.0 - hl2 / r2)
                            hi = min(1.0, (rp2 - h * h) / r2)
                            if lo >= hi:
                                continue
                            phi = math.atan2(y, x)
                            b_lo, b_hi = math.asin(math.sqrt(lo)), math.asin(math.sqrt(hi))
                            if hi >= 1.0 and lo <= 0.0:
                                full = True
                            elif hi >= 1.0:
                                full = mark(diff, phi + b_lo, math.pi - 2 * b_lo, step)
                            elif lo <= 0.0:
                                full = mark(diff, phi - b_hi, 2 * b_hi, step)
                            else:
                                full = mark(diff, phi + b_lo, b_hi - b_lo, step)
                                full = mark(diff, phi + math.pi - b_hi, b_hi - b_lo, step) or full
                        if full:
                            flags[0] = 1
        cuda.syncthreads()
        if tid == 0:
            run = 0
            for k in range(nb):
                run += diff[k]
                out_self[i, k] = flags[0] == 1 or run > 0

    return kernel


def _index(probes, cell, fine):
    """Two-level probe index: coarse cells of size `cell`, each split into fine cells of about `fine`.

    Probes are sorted by (coarse, fine) cell. Every non-empty fine cell keeps its probe range and
    tight bounding box, and every coarse cell its range of fine cells and their joint box.
    """
    k = int(np.clip(np.round(cell / fine), 1, 16))
    origin = probes.min(axis=0) - cell
    dims = np.ceil((probes.max(axis=0) - origin) / cell).astype(np.int64) + 1
    rel = (probes - origin) / cell
    ijk = rel.astype(np.int64)
    sub = np.minimum(((rel - ijk) * k).astype(np.int64), k - 1)
    key = ((ijk[:, 0] * dims[1] + ijk[:, 1]) * dims[2] + ijk[:, 2]) * k ** 3 + (sub[:, 0] * k + sub[:, 1]) * k + sub[:, 2]
    by_key = np.argsort(key, kind="stable")
    key, sorted_probes = key[by_key], np.ascontiguousarray(probes[by_key])
    fkey, fstart = np.unique(key, return_index=True)
    fend = np.append(fstart[1:], len(key))
    flo, fhi = np.minimum.reduceat(sorted_probes, fstart, axis=0), np.maximum.reduceat(sorted_probes, fstart, axis=0)
    coarse = fkey // k ** 3
    ncell = np.arange(int(np.prod(dims)))
    cstart, cend = np.searchsorted(coarse, ncell), np.searchsorted(coarse, ncell, side="right")
    used = np.flatnonzero(cend > cstart)
    clo, chi = np.full((ncell.size, 3), np.inf), np.full((ncell.size, 3), -np.inf)
    clo[used], chi[used] = np.minimum.reduceat(flo, cstart[used], axis=0), np.maximum.reduceat(fhi, cstart[used], axis=0)
    return sorted_probes, origin, dims, cstart, cend, clo, chi, fstart, fend, flo, fhi


def collision_masks(points, normals, t1, t2, probes, radius, length, tol, bins, plane=None, device="cpu"):
    """Per-point boolean masks over `bins` axis angles: (probe collisions, plane collisions).

    Bin k covers theta in [k, k + 1) * pi / bins; the axis is cos(theta) t1 + sin(theta) t2.
    `plane` = (m, d) is a table occupying x.m > d; probe flags are only meaningful where the
    plane flag is False (see `_collisions`). `device="cuda"` runs the same computation on an
    NVIDIA GPU (needs numba-cuda).
    """
    out_self = np.zeros((len(points), bins), np.bool_)
    out_plane = np.zeros((len(points), bins), np.bool_)
    if len(points) == 0:
        return out_self, out_plane
    probes = probes if len(probes) else np.full((1, 3), 1e9)  # a far-away dummy keeps the index well formed
    rb = np.sqrt((radius - tol) ** 2 + (length / 2) ** 2)
    cell = rb / 3
    reach = int(np.ceil(rb / cell)) + 1
    offsets = np.stack(np.meshgrid(*[np.arange(-reach, reach + 1)] * 3, indexing="ij"), -1).reshape(-1, 3)
    offsets = offsets[np.argsort(np.abs(offsets).sum(axis=1) + 1e-3 * (offsets ** 2).sum(axis=1), kind="stable")]
    index = _index(probes, cell, 0.08)
    sorted_probes, origin, dims = index[:3]
    pm, pd = (np.zeros(3), np.inf) if plane is None else (np.asarray(plane[0], float), float(plane[1]))
    if device == "cuda":
        from numba import cuda

        d_self, d_plane = cuda.to_device(out_self.astype(np.uint8)), cuda.to_device(out_plane.astype(np.uint8))
        put = [cuda.to_device(np.ascontiguousarray(a)) for a in (points, normals, t1, t2, sorted_probes, origin)]
        rest = [cuda.to_device(np.ascontiguousarray(a)) for a in (dims, offsets, *index[3:])]
        _cuda_kernel(bins)[len(points), 64](*put, cell, *rest, radius, length / 2, tol, cuda.to_device(pm), pd,
                                            d_self, d_plane)
        return d_self.copy_to_host().astype(bool), d_plane.copy_to_host().astype(bool)
    # Visit grid points in cell order so neighboring work shares cached probes.
    pc = np.clip(((points - origin) / cell).astype(np.int64), 0, dims - 1)
    order = np.argsort((pc[:, 0] * dims[1] + pc[:, 1]) * dims[2] + pc[:, 2], kind="stable")
    _collisions(points, normals, t1, t2, order, sorted_probes, origin, cell, dims, offsets, *index[3:],
                radius, length / 2, tol, bins, pm, pd, out_self, out_plane)
    return out_self, out_plane


def erode(feasible, margin):
    """Shrink every feasible arc by `margin` bins on each side (circularly) as a safety band."""
    out = feasible.copy()
    for d in range(1, margin + 1):
        out &= np.roll(feasible, d, axis=1) & np.roll(feasible, -d, axis=1)
    return out
