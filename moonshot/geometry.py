"""Shared geometry: mesh loading, crease-aware normals, tangent frames and tool rotations."""
import warnings

import numpy as np
import trimesh
from numba import njit, prange
from scipy.spatial.transform import Rotation

UNITS = {"in": 1.0, "mm": 1 / 25.4, "cm": 1 / 2.54, "m": 1 / 0.0254}


def load_mesh(path, units="in", rotate=(0.0, 0.0, 0.0)):
    """Load a solid (STL, OBJ, PLY, 3MF, GLB; STEP with the optional cascadio package) in inches.

    `rotate` turns the part about the origin by fixed-axis X, Y, Z angles in degrees (the same
    convention as the output rotations), for parts that were not modeled in their working pose.
    """
    mesh = trimesh.load_mesh(path, process=True)
    mesh.apply_scale(UNITS[units])
    if np.any(rotate):
        turn = np.eye(4)
        turn[:3, :3] = Rotation.from_euler("xyz", rotate, degrees=True).as_matrix()
        mesh.apply_transform(turn)
    if not mesh.is_watertight:
        warnings.warn(f"{path} is not watertight; inside/outside and normals may be unreliable")
    mesh.fix_normals()
    return mesh


@njit(parallel=True, cache=True)
def _corner_normals(faces, fn, area, start, corners, cos_crease):
    """Sum, for every face corner, the area-weighted normals of the faces at its vertex within the crease angle."""
    out = np.empty((faces.shape[0], 3, 3))
    for f in prange(faces.shape[0]):
        for c in range(3):
            v = faces[f, c]
            ax = ay = az = 0.0
            for k in range(start[v], start[v + 1]):
                g = corners[k] // 3
                if fn[f, 0] * fn[g, 0] + fn[f, 1] * fn[g, 1] + fn[f, 2] * fn[g, 2] > cos_crease:
                    ax += area[g] * fn[g, 0]
                    ay += area[g] * fn[g, 1]
                    az += area[g] * fn[g, 2]
            norm = np.sqrt(ax * ax + ay * ay + az * az)
            out[f, c, 0], out[f, c, 1], out[f, c, 2] = ax / norm, ay / norm, az / norm
    return out


def corner_normals(mesh, crease_deg=30.0):
    """Per face-corner normals: area-weighted vertex normals that never smooth across a crease.

    Returns an (F, 3, 3) array; interpolating it barycentrically gives smooth normals on
    curved faces while keeping sharp edges sharp. Area weights (rather than corner angles)
    keep sliver triangles, common in decimated meshes, from tilting their vertices' normals.
    """
    vert = mesh.faces.ravel()
    corners = np.argsort(vert, kind="stable")  # corner ids grouped by vertex
    start = np.concatenate([[0], np.cumsum(np.bincount(vert, minlength=len(mesh.vertices)))])
    return _corner_normals(mesh.faces, mesh.face_normals, mesh.area_faces, start, corners, np.cos(np.radians(crease_deg)))


def tangent_frames(normals):
    """Right-handed tangent bases (t1, t2) with t2 = n x t1; t1 is world X projected (Y near +-X)."""
    ref = np.where(np.abs(normals[:, :1]) < 0.9, [[1.0, 0.0, 0.0]], [[0.0, 1.0, 0.0]])
    t1 = ref - normals * np.sum(ref * normals, axis=1, keepdims=True)
    t1 /= np.linalg.norm(t1, axis=1, keepdims=True)
    return t1, np.cross(normals, t1)


def tool_rotations(axes, normals):
    """Tool frames [X = cylinder axis, Y = normal x axis, Z = contact normal] for each point, as a Rotation.

    The home pose (identity) is a cylinder lying along world X on a horizontal surface.
    """
    return Rotation.from_matrix(np.stack([axes, np.cross(normals, axes), normals], axis=2))


def angles(rot):
    """(euler_deg, quat_wxyz) of a Rotation. Euler angles are fixed-axis X, then Y, then Z
    (R = Rz @ Ry @ Rx, i.e. roll-pitch-yaw; KUKA A,B,C = rz,ry,rx)."""
    with warnings.catch_warnings():
        # A vertical X axis is a gimbal lock for this convention; scipy then zeroes rz.
        warnings.simplefilter("ignore", UserWarning)
        euler = rot.as_euler("xyz", degrees=True)
    return euler, rot.as_quat(scalar_first=True)


def part_poses(points, rot):
    """Pose of the part when it moves and the cylinder stays put: (position, Rotation) in the cylinder frame.

    The cylinder frame has its origin at the contact point in the middle of the cylinder, X along
    the cylinder axis and Z toward its center, so the cylinder occupies |y|^2 + (z - R)^2 < R^2.
    A part point x lands at rotation.apply(x) + position.
    """
    inv = rot.inv()
    return -inv.apply(points), inv
