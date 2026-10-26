"""Step 2: collision masks against brute force and closed-form cases."""
import numpy as np
import pytest
import trimesh

from moonshot.geometry import load_mesh, tangent_frames
from moonshot.grid import surface_grid
from moonshot.holder import fixture_probes, front_view, table
from moonshot.reach import collision_masks, erode, probe_points
from moonshot.samples import mesh_field, round_intersect, slab

R, L, TOL, K = 0.5, 1.0, 0.001, 180


def brute_force(point, normal, t1, t2, probes, radius, length, tol, bins, sub=8):
    """Per-bin collision flags from dense sub-angle point-in-cylinder tests."""
    d = probes - (point + radius * normal)
    d = d[(np.einsum("ij,ij->i", d, d) < (radius - tol) ** 2 + (length / 2) ** 2) & (np.abs(d @ normal) < radius - tol)]
    h, x, y = d @ normal, d @ t1, d @ t2
    angles = (np.arange(bins)[:, None] + (np.arange(sub)[None] + 0.5) / sub).ravel() * np.pi / bins
    hit = np.zeros(angles.size, bool)
    for chunk in np.array_split(np.arange(angles.size), max(1, d.shape[0] * angles.size // 4_000_000)):
        s = np.outer(np.cos(angles[chunk]), x) + np.outer(np.sin(angles[chunk]), y)
        rho2 = h[None] ** 2 + x[None] ** 2 + y[None] ** 2 - s ** 2
        hit[chunk] = ((rho2 < (radius - tol) ** 2) & (np.abs(s) < length / 2)).any(axis=1)
    return hit.reshape(bins, sub).any(axis=1)


@pytest.mark.parametrize("name, length", [("s_channel", L), ("pocket_block", L), ("wave_plate", 10.0)])
def test_matches_brute_force(name, length):
    mesh = load_mesh(f"samples/{name}.stl")
    plane, resting = table(mesh, (0, 0, -1))
    pts, nrm, _, _ = surface_grid(mesh, 0.05, resting)
    t1, t2 = tangent_frames(nrm)
    probes = probe_points(mesh, 0.02)
    hit, flo = collision_masks(pts, nrm, t1, t2, probes, R, length, TOL, K, plane)
    missed = extra = 0
    count = 120 if length < 2 else 40  # a long cylinder sees most of the part from every point
    for i in np.random.default_rng(0).choice(len(pts), count, replace=False):
        truth = brute_force(pts[i], nrm[i], t1[i], t2[i], probes, R, length, TOL, K)
        free = ~flo[i]
        missed += np.sum(truth & ~hit[i] & free)
        extra += np.sum(~truth & hit[i] & free)
    assert missed == 0  # never call a colliding orientation free
    assert extra < 0.002 * count * K  # only edge bins of each interval are conservative


def test_floor_reach_on_a_wall():
    """On a vertical wall the cylinder needs R of clearance lying flat, L/2 standing up, more in between."""
    box = trimesh.creation.box(extents=(4, 4, 3), transform=trimesh.transformations.translation_matrix((0, 0, 1.5)))
    length = 1.5
    heights = np.array([0.45, 0.55, 0.70, 0.80, 0.90])
    pts = np.column_stack([np.full(5, 2.0), np.zeros(5), heights])
    nrm = np.tile([1.0, 0.0, 0.0], (5, 1))
    t1, t2 = tangent_frames(nrm)  # t1 = +Y (horizontal), t2 = +Z (vertical)
    probes = probe_points(box, 0.02)
    _, flo = collision_masks(pts, nrm, t1, t2, probes, R, length, TOL, K, ((0, 0, -1), 0.0))
    flat, standing, diagonal = flo[:, 0], flo[:, K // 2], flo[:, K // 4]
    # Lying flat needs z >= R = 0.5; standing needs z >= L/2 = 0.75; 45 deg needs ~0.884.
    assert flat.tolist() == [True, False, False, False, False]
    assert standing.tolist() == [True, True, True, False, False]
    assert diagonal.tolist() == [True, True, True, True, False]


def test_fixture_blocks_only_what_reaches_behind_the_part():
    """A fixture inside the outline of a box held from below: a cylinder under the box hits it, one standing beside it does not."""
    box = trimesh.creation.box(extents=(2, 2, 1), transform=trimesh.transformations.translation_matrix((0, 0, 0.5)))
    fixture = fixture_probes(front_view(box, (0, 0, -1), 0.005), 0.05, 0.02, 5.1)
    assert np.abs(fixture[:, :2]).max() < 0.96 and fixture[:, 2].max() <= 0 and fixture[:, 2].min() < -5
    pts = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.5]])
    nrm = np.array([[0.0, 0.0, -1.0], [1.0, 0.0, 0.0]])
    t1, t2 = tangent_frames(nrm)  # beside the box, t2 = +Z: the cylinder standing upright
    hit, _ = collision_masks(pts, nrm, t1, t2, fixture, R, 10.0, TOL, K)
    assert hit[0].all() and not hit[1].any()


def test_trough_forces_alignment():
    """A straight full-round groove a little wider than the cylinder only admits axes along it."""

    def trough(x, y, z):
        block = round_intersect(np.maximum(np.abs(x) - 1.5, np.abs(y) - 1.5), slab(z, 0, 1.2), 0.03)
        return np.maximum(block, 0.75 - np.sqrt(y * y + (z - 1.6) ** 2))

    mesh = mesh_field(trough, (-1.5, -1.5, 0), (1.5, 1.5, 1.2), voxel=0.01)
    bottom = np.array([[0.0, 0.0, 0.85]])
    _, dist, tri = trimesh.proximity.closest_point(mesh, bottom)
    nrm = mesh.face_normals[tri]
    t1, t2 = tangent_frames(nrm)  # t1 ~ +X, the groove direction
    hit, flo = collision_masks(bottom, nrm, t1, t2, probe_points(mesh, 0.01), R, L, TOL, K, ((0, 0, -1), 0.0))
    free = np.flatnonzero(~(hit | flo)[0])
    angles = np.degrees((free + 0.5) * np.pi / K)
    off_axis = np.minimum(angles, 180 - angles)
    assert free.size > 0 and off_axis.max() < 12  # only orientations close to the groove survive
    # Flat ground well away from the groove admits every orientation.
    top = np.array([[0.0, -1.2, 1.2]])
    up = np.array([[0.0, 0.0, 1.0]])
    ut1, ut2 = tangent_frames(up)
    hit, flo = collision_masks(top, up, ut1, ut2, probe_points(mesh, 0.01), R, L, TOL, K, ((0, 0, -1), 0.0))
    assert not (hit | flo)[0, :].any()


def test_erode_trims_arcs_circularly():
    mask = np.zeros((1, 12), bool)
    mask[0, [0, 1, 2, 3, 10, 11]] = True  # one arc wrapping through bin 0
    assert np.flatnonzero(erode(mask, 1)[0]).tolist() == [0, 1, 2, 11]


def test_cuda_kernel_matches_cpu():
    """The GPU kernel gives the same masks as the CPU kernel (run in numba's CUDA simulator)."""
    import os
    import subprocess
    import sys

    script = """
import numpy as np
from moonshot.geometry import load_mesh, tangent_frames
from moonshot.grid import surface_grid
from moonshot.reach import collision_masks, probe_points
mesh = load_mesh("samples/pocket_block.stl")
from moonshot.holder import fixture_probes, front_view, table
plane, resting = table(mesh, (0, 0, -1))
pts, nrm, _, _ = surface_grid(mesh, 0.1, resting)
idx = np.random.default_rng(3).choice(len(pts), 6, replace=False)
pts, nrm = pts[idx], nrm[idx]
t1, t2 = tangent_frames(nrm)
probes = probe_points(mesh, 0.06)
for length in (1.0, 10.0):
    cpu = collision_masks(pts, nrm, t1, t2, probes, 0.5, length, 0.001, 36, plane)
    gpu = collision_masks(pts, nrm, t1, t2, probes, 0.5, length, 0.001, 36, plane, device="cuda")
    ok_cpu, ok_gpu = ~(cpu[0] | cpu[1]), ~(gpu[0] | gpu[1])
    assert (cpu[1] == gpu[1]).all() and (ok_cpu == ok_gpu).all(), (length, ok_cpu.sum(), ok_gpu.sum())
print("match", int(ok_cpu.sum()))
"""
    env = dict(os.environ, NUMBA_ENABLE_CUDASIM="1")
    out = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=1800)
    assert out.returncode == 0 and "match" in out.stdout, out.stderr[-2000:]
