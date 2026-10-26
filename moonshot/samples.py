"""Procedural sample solids, meshed from implicit fields with marching cubes.

Every sample is in inches, Z-up, resting on z = 0. Fields are negative inside.
Fillets and edge rounds come from the round-blend operators, so the parts look
like machined stock rather than voxel art.
"""
import numpy as np
import trimesh
from scipy.spatial import cKDTree


def round_union(a, b, r):
    """Union of two fields with a concave fillet of radius ~r."""
    u, v = np.maximum(r - a, 0), np.maximum(r - b, 0)
    return np.maximum(r, np.minimum(a, b)) - np.sqrt(u * u + v * v)


def round_intersect(a, b, r):
    """Intersection of two fields with a convex round of radius ~r."""
    u, v = np.maximum(r + a, 0), np.maximum(r + b, 0)
    return np.minimum(-r, np.maximum(a, b)) + np.sqrt(u * u + v * v)


def rounded_rect(x, y, x0, x1, y0, y1, corner):
    """Signed distance to a Z-extruded rectangle with rounded vertical corners."""
    qx = np.abs(x - (x0 + x1) / 2) - ((x1 - x0) / 2 - corner)
    qy = np.abs(y - (y0 + y1) / 2) - ((y1 - y0) / 2 - corner)
    return np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - corner


def slab(z, z0, z1):
    """Signed distance to the slab z0 <= z <= z1."""
    return np.maximum(z0 - z, z - z1)


def wave_plate(x, y, z):
    """Plate whose top is an egg-crate of convex peaks, concave valleys and saddles."""
    top = 1.0 + 0.12 * np.sin(2 * np.pi * x / 2.0) * np.cos(2 * np.pi * y / 1.5)
    body = round_intersect(rounded_rect(x, y, 0, 4, 0, 3, 0.25), z - top, 0.05)
    return round_intersect(body, -z, 0.02)


def dome_fillet(x, y, z):
    """Square base with a spherical dome blended in by a 0.75 in concave fillet."""
    base = round_intersect(rounded_rect(x, y, -2.5, 2.5, -2.5, 2.5, 0.3), slab(z, 0, 0.75), 0.06)
    dome = np.sqrt(x * x + y * y + (z - 0.25) ** 2) - 1.4
    return np.maximum(round_union(base, dome, 0.75), -z)


def s_channel(x, y, z):
    """Block with an S-shaped full-round groove (0.7 in radius) running along X."""
    xs = np.linspace(-1.0, 6.0, 7001)
    curve = np.column_stack([xs, 1.5 + 0.35 * np.sin(2 * np.pi * (xs - 0.5) / 4.0)])
    gx, gy = np.broadcast_arrays(x[:, :, 0], y[:, :, 0])
    d_xy = cKDTree(curve).query(np.column_stack([gx.ravel(), gy.ravel()]))[0].reshape(gx.shape + (1,))
    groove = np.sqrt(d_xy.astype(np.float32) ** 2 + (z - 1.35) ** 2) - 0.7
    block = round_intersect(rounded_rect(x, y, 0, 5, 0, 3, 0.3), slab(z, 0, 1.25), 0.06)
    return round_intersect(block, -groove, 0.08)


def pocket_block(x, y, z):
    """Block with a filleted rectangular pocket and a through hole: tight concave features."""
    block = round_intersect(rounded_rect(x, y, 0, 4, 0, 3, 0.25), slab(z, 0, 1.5), 0.06)
    pocket = round_intersect(rounded_rect(x, y, 0.5, 2.6, 0.5, 2.5, 0.3), slab(z, 0.9, 2.0), 0.2)
    hole = np.hypot(x - 3.3, y - 1.5) - 0.3
    return round_intersect(block, -np.minimum(pocket, hole), 0.05)


SAMPLES = {
    "wave_plate": (wave_plate, (0, 0, 0), (4, 3, 1.15)),
    "dome_fillet": (dome_fillet, (-2.5, -2.5, 0), (2.5, 2.5, 1.65)),
    "s_channel": (s_channel, (0, 0, 0), (5, 3, 1.25)),
    "pocket_block": (pocket_block, (0, 0, 0), (4, 3, 1.5)),
}


def make_sample(name, voxel=0.015, faces=120_000):
    """Mesh a named sample (see SAMPLES)."""
    return mesh_field(*SAMPLES[name], voxel=voxel, faces=faces)


def mesh_field(field, lo, hi, voxel=0.015, faces=120_000):
    """Mesh an implicit field over the box [lo, hi] with marching cubes, then decimate to ~`faces` triangles."""
    from skimage.measure import marching_cubes

    # The odd sub-voxel offset keeps lattice nodes off planar faces; exact zeros make MC non-manifold.
    lo, hi = np.asarray(lo) - 3.37 * voxel, np.asarray(hi) + 3 * voxel
    axes = [np.arange(a, b + voxel, voxel, dtype=np.float32) for a, b in zip(lo, hi)]
    x, y, z = np.meshgrid(*axes, indexing="ij", sparse=True)
    values = np.broadcast_to(field(x, y, z), (axes[0].size, axes[1].size, axes[2].size)).astype(np.float32)
    verts, tris, _, _ = marching_cubes(values, 0.0, spacing=(voxel,) * 3)
    mesh = trimesh.Trimesh(verts + lo, tris, process=True)
    if len(mesh.faces) > faces:
        mesh = mesh.simplify_quadric_decimation(face_count=faces, aggression=3)  # gentler = far more accurate
        # Low aggression can leave a few zero-area slivers folded onto an edge; they are not geometry.
        mesh.update_faces(mesh.nondegenerate_faces())
        mesh.remove_unreferenced_vertices()
    mesh.fix_normals()
    if not mesh.is_watertight:
        raise RuntimeError(f"{field.__name__}: generated mesh is not watertight")
    return mesh


if __name__ == "__main__":
    import sys
    from pathlib import Path

    out = Path(sys.argv[1] if len(sys.argv) > 1 else "samples")
    out.mkdir(parents=True, exist_ok=True)
    for name in SAMPLES:
        mesh = make_sample(name)
        mesh.export(out / f"{name}.stl")
        print(f"{name}: {len(mesh.faces)} faces, bounds {np.round(mesh.bounds, 3).tolist()}")
