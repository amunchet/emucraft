"""Step 1 grid and the shared geometry helpers."""
import numpy as np
import trimesh

from moonshot.geometry import angles, corner_normals, load_mesh, part_poses, tangent_frames, tool_rotations
from moonshot.grid import neighbor_graph, surface_grid
from moonshot.holder import fixture_probes, front_view, hidden_faces, table


def test_box_grid_is_exact():
    """An axis-aligned box gets exactly one lattice point per grid cell on every face, none twice."""
    box = trimesh.creation.box(extents=(4, 3, 2), transform=trimesh.transformations.translation_matrix((2, 1.5, 1)))
    pts, nrm, _, fam = surface_grid(box, 0.1)
    assert len(pts) == 2 * (40 * 30 + 40 * 20 + 30 * 20)
    assert len(np.unique(np.round(pts, 9), axis=0)) == len(pts)
    assert np.allclose(np.abs(nrm).max(axis=1), 1.0)
    # Points sit on lattice lines (i + 0.5) * h in the two coordinates across each face.
    top = pts[nrm[:, 2] > 0.5]
    lattice = top[:, :2] / 0.1 - 0.5
    assert np.allclose(top[:, 2], 2.0) and np.abs(lattice - np.round(lattice)).max() < 1e-9


def test_table_faces_are_skipped():
    box = trimesh.creation.box(extents=(1, 1, 1), transform=trimesh.transformations.translation_matrix((0.5, 0.5, 0.5)))
    plane, resting = table(box, (0, 0, -1))
    pts, nrm, _, _ = surface_grid(box, 0.1, resting)
    assert len(pts) == 5 * 100 and (nrm[:, 2] > -0.5).all()
    assert np.allclose(plane[0], (0, 0, -1)) and plane[1] == 0.0


def test_fixture_side_is_hidden():
    """A plate on a smaller pad, held from below: only the plate's top and outer walls are worked."""
    plate = trimesh.creation.box(extents=(2, 2, 0.5), transform=trimesh.transformations.translation_matrix((0, 0, 0.45)))
    pad = trimesh.creation.box(extents=(1, 1, 0.2), transform=trimesh.transformations.translation_matrix((0, 0, 0.1)))
    part = trimesh.util.concatenate([plate, pad])
    pts, nrm, _, _ = surface_grid(part, 0.05, hidden_faces(part, front_view(part, (0, 0, -1), 0.01)))
    walls = np.abs(nrm[:, 2]) < 0.5
    assert len(pts) == 40 * 40 + 4 * 40 * 10
    assert (pts[:, 2] > 0.2).all() and np.allclose(np.abs(pts[walls, :2]).max(axis=1), 1.0)


def test_sphere_points_and_normals():
    sphere = trimesh.creation.icosphere(subdivisions=5, radius=1.0)
    pts, nrm, _, _ = surface_grid(sphere, 0.05)
    assert np.abs(np.linalg.norm(pts, axis=1) - 1).max() < 2e-3  # on the tessellated sphere
    assert np.degrees(np.arccos(np.clip(np.sum(nrm * pts / np.linalg.norm(pts, axis=1)[:, None], 1), -1, 1))).max() < 1.0
    expected = 4 * np.pi / 0.05 ** 2 * np.mean(np.abs(nrm).max(axis=1))  # tri-dexel density
    assert abs(len(pts) / expected - 1) < 0.02


def test_neighbor_graph_skips_thin_wall_opposite_faces():
    wall = trimesh.creation.box(extents=(2, 0.012, 1))  # thinner than the neighbor reach
    pts, nrm, _, _ = surface_grid(wall, 0.01)
    e = neighbor_graph(pts, nrm, 0.01)
    assert (np.einsum("ij,ij->i", nrm[e[:, 0]], nrm[e[:, 1]]) > -0.5).all()


def test_corner_normals_keep_creases_and_smooth_curves():
    box = trimesh.creation.box()
    cn = corner_normals(box)
    assert np.allclose(cn, box.face_normals[:, None, :])
    sphere = trimesh.creation.icosphere(subdivisions=3)
    cn = corner_normals(sphere)
    radial = sphere.vertices[sphere.faces] / np.linalg.norm(sphere.vertices[sphere.faces], axis=2, keepdims=True)
    assert np.einsum("fcd,fcd->fc", cn, radial).min() > 0.999


def test_tool_rotations_home_pose_and_round_trip():
    euler, quat = angles(tool_rotations(np.array([[1.0, 0, 0]]), np.array([[0, 0, 1.0]])))
    assert np.allclose(euler, 0) and np.allclose(quat, [[1, 0, 0, 0]])
    rng = np.random.default_rng(0)
    nrm = rng.normal(size=(200, 3))
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True)
    t1, t2 = tangent_frames(nrm)
    ang = rng.uniform(0, 2 * np.pi, 200)
    axis = np.cos(ang)[:, None] * t1 + np.sin(ang)[:, None] * t2
    euler, _ = angles(tool_rotations(axis, nrm))
    rx, ry, rz = np.radians(euler).T
    c, s = np.cos, np.sin
    # R = Rz @ Ry @ Rx; column 0 is the axis, column 2 the normal.
    col0 = np.column_stack([c(rz) * c(ry), s(rz) * c(ry), -s(ry)])
    col2 = np.column_stack([c(rz) * s(ry) * c(rx) + s(rz) * s(rx), s(rz) * s(ry) * c(rx) - c(rz) * s(rx), c(ry) * c(rx)])
    assert np.allclose(col0, axis, atol=1e-9) and np.allclose(col2, nrm, atol=1e-9)
    assert np.allclose(np.abs(euler[:, 1]), np.degrees(np.arcsin(np.abs(axis[:, 2]))))  # |ry| = tilt of the axis


def test_part_pose_inverts_the_tool_pose():
    """Moving the part instead of the cylinder: the contact lands on the origin, normal on +Z, axis on +X."""
    rng = np.random.default_rng(1)
    pts = rng.uniform(-2, 2, size=(50, 3))
    nrm = rng.normal(size=(50, 3))
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True)
    t1, t2 = tangent_frames(nrm)
    ang = rng.uniform(0, np.pi, 50)
    axis = np.cos(ang)[:, None] * t1 + np.sin(ang)[:, None] * t2
    pos, rot = part_poses(pts, tool_rotations(axis, nrm))
    assert np.allclose(rot.apply(pts) + pos, 0, atol=1e-12)
    assert np.allclose(rot.apply(nrm), [0, 0, 1]) and np.allclose(rot.apply(axis), [1, 0, 0])


def test_load_rotates_the_part(tmp_path):
    box = trimesh.creation.box(extents=(1, 2, 3))
    box.export(tmp_path / "box.stl")
    turned = load_mesh(tmp_path / "box.stl", rotate=(-90, 0, 0))  # Y -> -Z, Z -> Y
    assert np.allclose(turned.extents, (1, 3, 2))
