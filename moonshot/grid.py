"""Step 1: lay a grid of points over the entire surface.

The grid is a tri-dexel lattice: rays on a square lattice of pitch `spacing` are cast
along X, Y and Z, and each triangle is sampled only by the family of rays most
perpendicular to it. Every surface region therefore gets a true square grid (in
its dominant projection) and nothing is sampled twice, including vertical walls and
undercuts. Points carry smooth, crease-aware normals.
"""
import numpy as np
from numba import njit, prange
from scipy.spatial import cKDTree

from .geometry import corner_normals


@njit(cache=True)
def _face_lattice(tri, cn, k, h, fill, out_p, out_n, out_f, f, base):
    """Count (or write, when `fill`) the lattice points of axis family k inside one triangle."""
    u, v = (k + 1) % 3, (k + 2) % 3
    i0, i1, i2 = 0, 1, 2
    area = (tri[1, u] - tri[0, u]) * (tri[2, v] - tri[0, v]) - (tri[2, u] - tri[0, u]) * (tri[1, v] - tri[0, v])
    if abs(area) < 1e-18:
        return 0
    if area < 0:  # orient counter-clockwise in the (u, v) projection
        i1, i2 = 2, 1
        area = -area
    xs = (tri[i0, u], tri[i1, u], tri[i2, u])
    ys = (tri[i0, v], tri[i1, v], tri[i2, v])
    lo_i = int(np.ceil(min(xs) / h - 0.5))
    hi_i = int(np.floor(max(xs) / h - 0.5))
    lo_j = int(np.ceil(min(ys) / h - 0.5))
    hi_j = int(np.floor(max(ys) / h - 0.5))
    n = 0
    w = np.empty(3)
    for i in range(lo_i, hi_i + 1):
        x = (i + 0.5) * h
        for j in range(lo_j, hi_j + 1):
            y = (j + 0.5) * h
            inside = True
            for e in range(3):  # edge e is opposite corner e
                ax, ay = xs[(e + 1) % 3], ys[(e + 1) % 3]
                dx, dy = xs[(e + 2) % 3] - ax, ys[(e + 2) % 3] - ay
                w[e] = dx * (y - ay) - dy * (x - ax)
                # Top-left rule: a point exactly on a shared edge belongs to one triangle only.
                if w[e] < 0 or (w[e] == 0 and not (dy < 0 or (dy == 0 and dx > 0))):
                    inside = False
                    break
            if not inside:
                continue
            if fill:
                idx = (i0, i1, i2)
                p = base + n
                nx, ny, nz = 0.0, 0.0, 0.0
                for c in range(3):
                    lam = w[c] / area
                    for d in range(3):
                        out_p[p, d] += lam * tri[idx[c], d]
                    nx += lam * cn[idx[c], 0]
                    ny += lam * cn[idx[c], 1]
                    nz += lam * cn[idx[c], 2]
                norm = np.sqrt(nx * nx + ny * ny + nz * nz)
                out_n[p, 0], out_n[p, 1], out_n[p, 2] = nx / norm, ny / norm, nz / norm
                out_f[p] = f
            n += 1
    return n


@njit(parallel=True, cache=True)
def _rasterize(tris, cns, family, h):
    """Two-pass parallel rasterization of every face into its dominant tri-dexel family."""
    nf = tris.shape[0]
    counts = np.zeros(nf, np.int64)
    dummy_p, dummy_n, dummy_f = np.zeros((1, 3)), np.zeros((1, 3)), np.zeros(1, np.int64)
    for f in prange(nf):
        if family[f] >= 0:
            counts[f] = _face_lattice(tris[f], cns[f], family[f], h, False, dummy_p, dummy_n, dummy_f, f, 0)
    offsets = np.cumsum(counts) - counts
    total = counts.sum()
    out_p, out_n, out_f = np.zeros((total, 3)), np.zeros((total, 3)), np.zeros(total, np.int64)
    for f in prange(nf):
        if counts[f] > 0:
            _face_lattice(tris[f], cns[f], family[f], h, True, out_p, out_n, out_f, f, offsets[f])
    return out_p, out_n, out_f


def surface_grid(mesh, spacing, skip=None, crease_deg=30.0):
    """Grid points, unit normals, source faces and ray family (0=X, 1=Y, 2=Z) over the surface.

    Faces flagged in `skip` (those the part is held by, see `holder`) get no points.
    """
    family = np.argmax(np.abs(mesh.face_normals), axis=1)
    if skip is not None:
        family[skip] = -1
    points, normals, faces = _rasterize(mesh.triangles, corner_normals(mesh, crease_deg), family, spacing)
    return points, normals, faces, family[faces]


def neighbor_graph(points, normals, spacing, reach=1.75, max_turn_deg=120.0):
    """Undirected neighbor pairs: points within `reach` grid steps whose normals turn < max_turn_deg.

    The normal test keeps the two faces of a thin wall from being treated as neighbors.
    """
    pairs = cKDTree(points).query_pairs(reach * spacing, output_type="ndarray")
    turn = np.einsum("ij,ij->i", normals[pairs[:, 0]], normals[pairs[:, 1]])
    return pairs[turn > np.cos(np.radians(max_turn_deg))]
