"""What holds the part, which the cylinder must not touch either.

`mount` points from the part toward its holder, in the part's frame:

- fixture (default): the part is held from its mounting side, for example by a robot
  moving it against a stationary cylinder. The fixture is assumed to fit inside the part's
  silhouette seen along `mount`, shrunk by `inset`, and to reach as far behind the part as
  the cylinder could. Faces hidden under the part when seen from the opposite side (the
  mounting side and the recesses in it) are not gridded.
- table: the part rests on an infinite plane at its far end along `mount`; only the faces
  resting on it are skipped. The plane itself is checked analytically in step 2.
- none: nothing but the part.
"""
import numpy as np
from numba import njit
from scipy import ndimage


def view_basis(mount):
    """Orthonormal rows (u, v, w) with w = -mount: view coordinates looking at the part from the front."""
    w = -np.asarray(mount, float) / np.linalg.norm(mount)
    u = np.cross([1.0, 0.0, 0.0] if abs(w[0]) < 0.9 else [0.0, 1.0, 0.0], w)
    u /= np.linalg.norm(u)
    return np.array([u, np.cross(w, u), w])


@njit(cache=True)
def _height_map(tri, ox, oy, pix, heights):
    """Highest surface above each pixel center; triangles are in view coordinates (u, v, height)."""
    ny, nx = heights.shape
    for t in range(tri.shape[0]):
        x0, y0, x1, y1, x2, y2 = tri[t, 0, 0], tri[t, 0, 1], tri[t, 1, 0], tri[t, 1, 1], tri[t, 2, 0], tri[t, 2, 1]
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if abs(area) < 1e-18:
            continue
        i0 = max(int(np.ceil((min(x0, x1, x2) - ox) / pix - 0.5)), 0)
        i1 = min(int(np.floor((max(x0, x1, x2) - ox) / pix - 0.5)), nx - 1)
        j0 = max(int(np.ceil((min(y0, y1, y2) - oy) / pix - 0.5)), 0)
        j1 = min(int(np.floor((max(y0, y1, y2) - oy) / pix - 0.5)), ny - 1)
        for j in range(j0, j1 + 1):
            py = oy + (j + 0.5) * pix
            for i in range(i0, i1 + 1):
                px = ox + (i + 0.5) * pix
                b0 = ((x1 - px) * (y2 - py) - (x2 - px) * (y1 - py)) / area
                b1 = ((x2 - px) * (y0 - py) - (x0 - px) * (y2 - py)) / area
                b2 = 1.0 - b0 - b1
                if b0 < -1e-9 or b1 < -1e-9 or b2 < -1e-9:
                    continue
                z = b0 * tri[t, 0, 2] + b1 * tri[t, 1, 2] + b2 * tri[t, 2, 2]
                if z > heights[j, i]:
                    heights[j, i] = z


def front_view(mesh, mount, pix):
    """Height map of the part seen from the side opposite its holder (-inf where there is no part)."""
    basis = view_basis(mount)
    tri = mesh.triangles @ basis.T
    lo = tri[:, :, :2].reshape(-1, 2).min(axis=0) - 3 * pix
    shape = np.ceil((tri[:, :, :2].reshape(-1, 2).max(axis=0) + 3 * pix - lo) / pix).astype(int)
    heights = np.full((shape[1], shape[0]), -np.inf)
    _height_map(tri, lo[0], lo[1], pix, heights)
    return {"basis": basis, "origin": lo, "pix": pix, "heights": heights, "base": float(tri[:, :, 2].min())}


def hidden_faces(mesh, view):
    """Faces with part material in front of them: a point just off the face, looking away from the holder, is covered."""
    q = (mesh.triangles_center + 2 * view["pix"] * mesh.face_normals) @ view["basis"].T
    ij = np.floor((q[:, :2] - view["origin"]) / view["pix"]).astype(int)
    ny, nx = view["heights"].shape
    inside = (ij[:, 0] >= 0) & (ij[:, 0] < nx) & (ij[:, 1] >= 0) & (ij[:, 1] < ny)
    out = np.zeros(len(q), bool)
    out[inside] = view["heights"][ij[inside, 1], ij[inside, 0]] > q[inside, 2] + view["pix"]
    return out


def fixture_probes(view, inset, spacing, depth):
    """Probe points on the fixture: its outline (the silhouette shrunk by `inset`) swept `depth` behind the part, and its face."""
    pix = view["pix"]
    solid = ndimage.binary_fill_holes(view["heights"] > -np.inf)
    body = ndimage.distance_transform_edt(solid) * pix > inset
    rim = body & ~ndimage.binary_erosion(body)
    k = max(1, int(round(spacing / pix)))

    def centers(mask):
        j, i = np.nonzero(mask)
        _, first = np.unique(np.column_stack([j // k, i // k]), axis=0, return_index=True)
        return view["origin"] + (np.column_stack([i[first], j[first]]) + 0.5) * pix

    rim_uv, face_uv = centers(rim), centers(body)
    hs = view["base"] - np.arange(0.0, depth + spacing, spacing)
    skirt = np.column_stack([np.repeat(rim_uv, len(hs), axis=0), np.tile(hs, len(rim_uv))])
    face = np.column_stack([face_uv, np.full(len(face_uv), view["base"])])
    return np.vstack([skirt, face]) @ view["basis"]


def table(mesh, mount):
    """The plane (m, d) under the part, with x.m > d occupied, and the mask of faces resting on it."""
    m = np.asarray(mount, float) / np.linalg.norm(mount)
    d = float((mesh.vertices @ m).max())
    resting = (mesh.face_normals @ m > 0.99) & ((mesh.triangles @ m).min(axis=1) > d - 1e-4)
    return (m, d), resting
