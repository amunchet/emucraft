"""A small software renderer for the asset images (no GPU or display needed).

Deferred shading on a supersampled G-buffer: z-buffered triangles, sphere impostors
for points, tube impostors for line glyphs, a key-light shadow map, a hemisphere
ambient term and crease/silhouette outlines. Colors are given in sRGB 0..1.
"""
import numpy as np
from numba import njit, prange
from PIL import Image

from .geometry import corner_normals


def camera(eye, target, up=(0.0, 0.0, 1.0), fov=28.0, ortho=None):
    """Look-at camera. `ortho` is the visible height in world units (orthographic), else perspective."""
    eye, target = np.asarray(eye, float), np.asarray(target, float)
    f = target - eye
    f /= np.linalg.norm(f)
    r = np.cross(f, up)
    r /= np.linalg.norm(r)
    return {"eye": eye, "rot": np.array([r, np.cross(r, f), f]), "fov": fov, "ortho": ortho}


def srgb_to_linear(c):
    c = np.asarray(c, float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def hex_rgb(h):
    """'#rrggbb' -> sRGB triple in 0..1."""
    return np.array([int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)])


@njit(cache=True)
def _project(p, eye, rot, focal, cx, cy, ortho):
    """World point -> (screen x, screen y, view depth)."""
    d0, d1, d2 = p[0] - eye[0], p[1] - eye[1], p[2] - eye[2]
    xv = rot[0, 0] * d0 + rot[0, 1] * d1 + rot[0, 2] * d2
    yv = rot[1, 0] * d0 + rot[1, 1] * d1 + rot[1, 2] * d2
    zv = rot[2, 0] * d0 + rot[2, 1] * d1 + rot[2, 2] * d2
    if ortho:
        return cx + xv * focal, cy - yv * focal, zv
    return cx + xv / zv * focal, cy - yv / zv * focal, zv


@njit(cache=True)
def _triangles(tri, nrm, col, eye, rot, focal, cx, cy, ortho, cull, depth, gn, ga, gid, layer):
    """Rasterize triangles into the G-buffer with perspective-correct normals and colors."""
    hgt, wid = depth.shape
    sx, sy, sz = np.empty(3), np.empty(3), np.empty(3)
    for t in range(tri.shape[0]):
        ok = True
        for k in range(3):
            sx[k], sy[k], sz[k] = _project(tri[t, k], eye, rot, focal, cx, cy, ortho)
            if sz[k] <= 1e-3:
                ok = False
        if not ok:
            continue
        area = (sx[1] - sx[0]) * (sy[2] - sy[0]) - (sx[2] - sx[0]) * (sy[1] - sy[0])
        if abs(area) < 1e-12 or (cull and area > 0):
            continue
        x0, x1 = max(int(min(sx[0], sx[1], sx[2])), 0), min(int(max(sx[0], sx[1], sx[2])) + 1, wid - 1)
        y0, y1 = max(int(min(sy[0], sy[1], sy[2])), 0), min(int(max(sy[0], sy[1], sy[2])) + 1, hgt - 1)
        for y in range(y0, y1 + 1):
            py = y + 0.5
            for x in range(x0, x1 + 1):
                px = x + 0.5
                b0 = ((sx[1] - px) * (sy[2] - py) - (sx[2] - px) * (sy[1] - py)) / area
                b1 = ((sx[2] - px) * (sy[0] - py) - (sx[0] - px) * (sy[2] - py)) / area
                b2 = 1.0 - b0 - b1
                if b0 < 0 or b1 < 0 or b2 < 0:
                    continue
                if ortho:
                    w0, w1, w2 = b0, b1, b2
                    z = b0 * sz[0] + b1 * sz[1] + b2 * sz[2]
                else:
                    inv = b0 / sz[0] + b1 / sz[1] + b2 / sz[2]
                    z = 1.0 / inv
                    w0, w1, w2 = b0 / sz[0] * z, b1 / sz[1] * z, b2 / sz[2] * z
                if z >= depth[y, x]:
                    continue
                depth[y, x] = z
                gid[y, x] = layer
                for d in range(3):
                    gn[y, x, d] = w0 * nrm[t, 0, d] + w1 * nrm[t, 1, d] + w2 * nrm[t, 2, d]
                    ga[y, x, d] = w0 * col[t, 0, d] + w1 * col[t, 1, d] + w2 * col[t, 2, d]


@njit(cache=True)
def _spheres(centers, radius, col, eye, rot, focal, cx, cy, ortho, depth, gn, ga, gid):
    """Sphere impostors: a shaded disc per point with true sphere depth and normals."""
    hgt, wid = depth.shape
    for i in range(centers.shape[0]):
        sx, sy, sz = _project(centers[i], eye, rot, focal, cx, cy, ortho)
        if sz <= 1e-3:
            continue
        rp = radius * focal if ortho else radius * focal / sz
        x0, x1 = max(int(sx - rp), 0), min(int(sx + rp) + 1, wid - 1)
        y0, y1 = max(int(sy - rp), 0), min(int(sy + rp) + 1, hgt - 1)
        for y in range(y0, y1 + 1):
            b = (y + 0.5 - sy) / rp
            for x in range(x0, x1 + 1):
                a = (x + 0.5 - sx) / rp
                q = 1.0 - a * a - b * b
                if q <= 0:
                    continue
                nz = np.sqrt(q)
                z = sz - radius * nz
                if z >= depth[y, x]:
                    continue
                depth[y, x] = z
                gid[y, x] = 2
                for d in range(3):
                    gn[y, x, d] = a * rot[0, d] - b * rot[1, d] - nz * rot[2, d]
                    ga[y, x, d] = col[i, d]


@njit(cache=True)
def _tubes(seg, radius, col, eye, rot, focal, cx, cy, ortho, depth, gn, ga, gid):
    """Tube impostors with round caps for line glyphs."""
    hgt, wid = depth.shape
    for i in range(seg.shape[0]):
        ax, ay, az = _project(seg[i, 0], eye, rot, focal, cx, cy, ortho)
        bx, by, bz = _project(seg[i, 1], eye, rot, focal, cx, cy, ortho)
        if az <= 1e-3 or bz <= 1e-3:
            continue
        ra = radius * focal if ortho else radius * focal / az
        rb = radius * focal if ortho else radius * focal / bz
        rmax = max(ra, rb)
        x0, x1 = max(int(min(ax, bx) - rmax), 0), min(int(max(ax, bx) + rmax) + 1, wid - 1)
        y0, y1 = max(int(min(ay, by) - rmax), 0), min(int(max(ay, by) + rmax) + 1, hgt - 1)
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy
        for y in range(y0, y1 + 1):
            py = y + 0.5
            for x in range(x0, x1 + 1):
                px = x + 0.5
                t = 0.0 if ll < 1e-12 else min(max(((px - ax) * dx + (py - ay) * dy) / ll, 0.0), 1.0)
                ox, oy = px - (ax + t * dx), py - (ay + t * dy)
                rt = ra + t * (rb - ra)
                dist = np.sqrt(ox * ox + oy * oy)
                if dist >= rt:
                    continue
                s = dist / rt
                nz = np.sqrt(1.0 - s * s)
                z = az + t * (bz - az) - radius * nz
                if z >= depth[y, x]:
                    continue
                depth[y, x] = z
                gid[y, x] = 2
                ux, uy = (ox / dist, oy / dist) if dist > 1e-9 else (0.0, 0.0)
                for d in range(3):
                    gn[y, x, d] = s * (ux * rot[0, d] - uy * rot[1, d]) - nz * rot[2, d]
                    ga[y, x, d] = col[i, d]


@njit(parallel=True, cache=True)
def _shade(depth, gn, ga, eye, rot, focal, cx, cy, ortho, key, fill, smap, lrot, leye, lscale, cap, out):
    """Hemisphere ambient + shadowed key light + fill + specular, in linear color.

    Back faces (only visible on open, unculled meshes such as a section) get the flat `cap` color,
    so a cut reads as solid material.
    """
    hgt, wid = depth.shape
    sres = smap.shape[0]
    for y in prange(hgt):
        for x in range(wid):
            z = depth[y, x]
            if z == np.inf:
                continue
            n0, n1, n2 = gn[y, x, 0], gn[y, x, 1], gn[y, x, 2]
            nl = np.sqrt(n0 * n0 + n1 * n1 + n2 * n2)
            n0, n1, n2 = n0 / nl, n1 / nl, n2 / nl
            if ortho:
                xv, yv = (x + 0.5 - cx) / focal, -(y + 0.5 - cy) / focal
            else:
                xv, yv = (x + 0.5 - cx) * z / focal, -(y + 0.5 - cy) * z / focal
            p = np.empty(3)
            v = np.empty(3)
            for d in range(3):
                p[d] = eye[d] + xv * rot[0, d] + yv * rot[1, d] + z * rot[2, d]
                v[d] = eye[d] - p[d]
            vl = np.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
            if n0 * v[0] + n1 * v[1] + n2 * v[2] < 0:
                for d in range(3):
                    out[y, x, d] = cap[d]
                continue
            lit = 1.0
            if sres > 1:
                ix, iy, lz = _project(p, leye, lrot, lscale, sres / 2, sres / 2, True)
                hits = 0
                for oy in range(-1, 2):
                    for ox in range(-1, 2):
                        jx, jy = int(ix) + ox, int(iy) + oy
                        if 0 <= jx < sres and 0 <= jy < sres and smap[jy, jx] < lz - 3.0 / lscale:
                            hits += 1
                lit = 1.0 - hits / 9.0
            ndk = max(n0 * key[0] + n1 * key[1] + n2 * key[2], 0.0)
            ndf = max(n0 * fill[0] + n1 * fill[1] + n2 * fill[2], 0.0)
            sky = 0.5 + 0.5 * n2
            amb = 0.10 + 0.14 * sky
            hx, hy, hz = key[0] + v[0] / vl, key[1] + v[1] / vl, key[2] + v[2] / vl
            hl = np.sqrt(hx * hx + hy * hy + hz * hz)
            spec = max((n0 * hx + n1 * hy + n2 * hz) / hl, 0.0) ** 40 * 0.12 * lit
            for d in range(3):
                c = ga[y, x, d] * (amb + 0.85 * ndk * lit + 0.18 * ndf) + spec
                out[y, x, d] = c / (1.0 + 0.35 * c)  # gentle highlight roll-off


@njit(parallel=True, cache=True)
def _outline(depth, gn, gid, out, strength):
    """Darken mesh silhouettes and creases (depth jumps or normal turns over 60 deg)."""
    hgt, wid = depth.shape
    for y in prange(1, hgt - 1):
        for x in range(1, wid - 1):
            if gid[y, x] != 1:
                continue
            z = depth[y, x]
            edge = False
            for oy, ox in ((0, 1), (1, 0), (0, -1), (-1, 0)):
                g = gid[y + oy, x + ox]
                if g == 0:
                    edge = True
                elif g == 1:
                    zz = depth[y + oy, x + ox]
                    if abs(zz - z) > 0.012 * z:
                        edge = zz > z
                    else:
                        c = na = nb = 0.0
                        for d in range(3):
                            c += gn[y, x, d] * gn[y + oy, x + ox, d]
                            na += gn[y, x, d] ** 2
                            nb += gn[y + oy, x + ox, d] ** 2
                        edge = c < 0.5 * np.sqrt(na * nb)
                if edge:
                    break
            if edge:
                for d in range(3):
                    out[y, x, d] *= strength


def mesh_layer(mesh, color, crease_deg=30.0):
    """Triangle layer for a trimesh: positions, crease-aware normals and per-vertex sRGB colors."""
    col = np.asarray(color, float)
    if col.ndim == 1:
        col = np.broadcast_to(col, (len(mesh.faces), 3, 3))
    elif col.shape == (len(mesh.vertices), 3):
        col = col[mesh.faces]
    return {"tri": mesh.triangles, "nrm": corner_normals(mesh, crease_deg), "col": col}


def render(cam, size=(1600, 1000), ss=2, meshes=(), points=(), tubes=(), shadow=True, outline=0.55,
           background=("#f1f0ec", "#fcfcfb"), cap="#dcdad3"):
    """Render layers to a PIL image. points/tubes are dicts (centers|seg, radius, col)."""
    wid, hgt = size[0] * ss, size[1] * ss
    depth = np.full((hgt, wid), np.inf)
    gn, ga = np.zeros((hgt, wid, 3)), np.zeros((hgt, wid, 3))
    gid = np.zeros((hgt, wid), np.int8)
    focal = hgt / cam["ortho"] if cam["ortho"] else (hgt / 2) / np.tan(np.radians(cam["fov"]) / 2)
    args = (cam["eye"], cam["rot"], focal, wid / 2, hgt / 2, bool(cam["ortho"]))
    for m in meshes:
        _triangles(m["tri"], m["nrm"], srgb_to_linear(m["col"]), *args, m.get("cull", True), depth, gn, ga, gid,
                   m.get("layer", 1))
    for p in points:
        col = np.broadcast_to(srgb_to_linear(p["col"]), (len(p["centers"]), 3)).copy()
        _spheres(np.asarray(p["centers"], float), p["radius"], col, *args, depth, gn, ga, gid)
    for t in tubes:
        col = np.broadcast_to(srgb_to_linear(t["col"]), (len(t["seg"]), 3)).copy()
        _tubes(np.asarray(t["seg"], float), t["radius"], col, *args, depth, gn, ga, gid)

    rot = cam["rot"]
    key = -0.55 * rot[0] + 0.55 * rot[1] - 0.35 * rot[2] + np.array([0, 0, 0.25])
    key /= np.linalg.norm(key)
    fill = 0.70 * rot[0] + 0.10 * rot[1] - 0.70 * rot[2]
    fill /= np.linalg.norm(fill)
    smap, lrot, leye, lscale = np.full((1, 1), np.inf), np.eye(3), np.zeros(3), 1.0
    casters = [m for m in meshes if m.get("shadow", True)]
    if shadow and casters:
        allpts = np.concatenate([m["tri"].reshape(-1, 3) for m in casters])
        mid = (allpts.min(0) + allpts.max(0)) / 2
        extent = np.linalg.norm(allpts.max(0) - allpts.min(0)) / 2 * 1.05
        leye = mid + key * 4 * extent
        lrot = camera(leye, mid, up=(0, 0, 1) if abs(key[2]) < 0.95 else (0, 1, 0))["rot"]
        res = 2048
        lscale = res / (2 * extent)
        smap = np.full((res, res), np.inf)
        scratch_n, scratch_a, scratch_id = np.zeros((res, res, 3)), np.zeros((res, res, 3)), np.zeros((res, res), np.int8)
        for m in casters:
            _triangles(m["tri"], m["nrm"], np.zeros_like(m["tri"]), leye, lrot, lscale, res / 2, res / 2, True, False,
                       smap, scratch_n, scratch_a, scratch_id, 1)
    img = np.zeros((hgt, wid, 3))
    bottom, top = srgb_to_linear(hex_rgb(background[0])), srgb_to_linear(hex_rgb(background[1]))
    img[:] = (bottom + (top - bottom) * np.linspace(1, 0, hgt)[:, None])[:, None, :]
    _shade(depth, gn, ga, *args, key, fill, smap, lrot, leye, lscale, srgb_to_linear(hex_rgb(cap)), img)
    if outline:
        _outline(depth, gn, gid, img, outline)
    img = img.reshape(size[1], ss, size[0], ss, 3).mean(axis=(1, 3))
    img = np.where(img <= 0.0031308, img * 12.92, 1.055 * np.clip(img, 0, 1) ** (1 / 2.4) - 0.055)
    return Image.fromarray(np.clip(img * 255 + 0.5, 0, 255).astype(np.uint8))


def cylinder_mesh(center, axis, normal, radius, length, segments=40, rings=1):
    """Closed triangle mesh of a cylinder (for drawing the tool), as a dict layer without colors.

    `rings` splits the side into bands along the axis, so it can be colored along its length.
    """
    a = np.asarray(axis, float) / np.linalg.norm(axis)
    u = np.asarray(normal, float)
    u = u - a * (u @ a)
    u /= np.linalg.norm(u)
    w = np.cross(a, u)
    ang = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    ring = np.cos(ang)[:, None] * u + np.sin(ang)[:, None] * w
    c = np.asarray(center, float)
    rim = c + np.linspace(-length / 2, length / 2, rings + 1)[:, None, None] * a + radius * ring
    lo, hi = rim[:-1], rim[1:]
    j = (np.arange(segments) + 1) % segments
    i = np.arange(segments)
    side = np.concatenate([np.stack([lo[:, i], lo[:, j], hi[:, j]], 2).reshape(-1, 3, 3),
                           np.stack([lo[:, i], hi[:, j], hi[:, i]], 2).reshape(-1, 3, 3)])
    side_n = np.concatenate([np.tile(np.stack([ring[i], ring[j], ring[j]], 1), (rings, 1, 1)),
                             np.tile(np.stack([ring[i], ring[j], ring[i]], 1), (rings, 1, 1))])
    cap_lo = np.stack([np.broadcast_to(c - a * length / 2, (segments, 3)), rim[0, j], rim[0, i]], 1)
    cap_hi = np.stack([np.broadcast_to(c + a * length / 2, (segments, 3)), rim[-1, i], rim[-1, j]], 1)
    caps_n = np.concatenate([np.broadcast_to(-a, (segments, 3, 3)), np.broadcast_to(a, (segments, 3, 3))])
    return {"tri": np.concatenate([side, cap_lo, cap_hi]), "nrm": np.concatenate([side_n, caps_n])}
