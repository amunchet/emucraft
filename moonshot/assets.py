"""Visual assets for every step of the pipeline.

    python -m moonshot.assets [part ...]

For each part this writes assets/<part>/: 0_model, 1_grid, 2_reach, 2_probes, 3_field,
3_grain, 3_angles and 3_stats (PNG), and 4_rollout.gif, which plays one pass both ways:
the cylinder moving over the fixed part, and the part moving against the fixed cylinder.
5_track.png and 5_track.gif show tracking (step 4): where along the drum each pass
touches, and neighboring passes with the contact stepping along the drum between them;
the hero part also gets 5_track_full.gif, every pass of the run.
assets/overview.png summarizes the hero part. Results are computed once into
results/<part>/ and reused; tracking is planned from them with its defaults.
"""
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import contourpy
import matplotlib.pyplot as plt
import numpy as np
import trimesh
from matplotlib import font_manager
from matplotlib.colors import LinearSegmentedColormap, Normalize, to_rgb
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.path import Path as Outline
from matplotlib.tri import Triangulation
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage
from scipy.sparse.csgraph import connected_components

from .geometry import angles, tool_rotations
from .holder import front_view, view_basis
from .pipeline import load, run, save
from .reach import erode
from .render import camera, cylinder_mesh, hex_rgb, mesh_layer, render
from .smooth import line_change, optimize
from .track import busiest, integrate, plan, wear

# Dataviz reference palette: ink, chrome, categorical slots 1-3, status, one-hue sequential, diverging.
INK, INK2, MUTED, HAIR, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#fcfcfb"
FAMILY = ["#2a78d6", "#eb6834", "#1baf7a"]
GOOD, CRITICAL = "#0ca30c", "#d03b3b"
PART, FLOOR, HOLDER, TOOL = "#b8b5ac", "#e6e4de", "#77746d", "#2a78d6"
SEQ = LinearSegmentedColormap.from_list("seq", ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"])
GLYPH = LinearSegmentedColormap.from_list("glyph", ["#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"])  # visible on gray
DIV = LinearSegmentedColormap.from_list("div", ["#2a78d6", "#f0efec", "#e34948"])

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 11, "text.color": INK, "axes.labelcolor": INK2,
    "axes.edgecolor": "#c3c2b7", "axes.linewidth": 0.8, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": HAIR,
    "grid.linewidth": 0.8, "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "legend.frameon": False,
})

ROOT = Path(__file__).resolve().parent.parent
# Each part: model file and run() options. The grip is a customer scan kept out of git (ingest/); it was
# scanned lying on its inner face, so it is stood on its butt (grain along Z) and held from that inner face.
PARTS = {name: (ROOT / "samples" / f"{name}.stl", {}) for name in ("wave_plate", "dome_fillet", "s_channel", "pocket_block")}
PARTS["grip"] = (ROOT / "ingest" / "COLT ANACONDA FG MOLD MASTER  RIGHT.stl", {"rotate": (-90, 0, 0), "mount": (0, -1, 0)})

# Per-part framing. `front` is the direction the main face looks and `up` is up in the straight-on
# front view, whose horizontal axis is up x front; probe sites and rollout passes are given in those
# view coordinates (s, t), or as 3D points. `eye` offsets the perspective camera from the part center,
# `slice` is (cut axis, axis along the cut, position, span) for the grid cutaway, and `passes` lists the
# fixed coordinates of the rollout passes and the view axis they sweep.
SHOTS = {
    "wave_plate": {"eye": (-2.6, -5.4, 4.4), "probes": [(0.5, 1.5, "peak"), (1.0, 1.125, "saddle"), (1.5, 1.5, "valley")],
                   "slice": (1, 0, 1.5, (0.35, 1.65)), "passes": ("t", (1.0, 2.0, 3.0))},
    "dome_fillet": {"eye": (-3.9, -6.6, 5.6), "probes": [(0.0, 0.0, "dome top"), (1.55, 0.0, "fillet ring"), (-1.9, -1.4, "base")],
                    "slice": (1, 0, 0.0, (0.6, 2.3)), "passes": ("t", (-0.6, 0.0, 0.6))},
    "s_channel": {"eye": (-3.0, -6.0, 5.2), "probes": [(1.5, 1.85, "groove bottom in a bend"), (1.5, 1.40, "inner wall of the bend"),
                                                       (2.6, 0.5, "flat top")],
                  "slice": (0, 1, 1.5, (0.95, 2.75)), "passes": ("t", (1.0, 2.0, 3.0))},
    "pocket_block": {"eye": (-2.6, -5.4, 5.4), "probes": [(1.55, 1.5, "pocket floor"), (0.75, 0.9, "pocket corner"), (3.3, 2.2, "near the hole")],
                     "slice": (1, 0, 1.5, (-0.1, 1.3)), "passes": ("t", (1.0, 2.0, 3.0))},
    "grip": {"front": (0, 1, 0), "up": (0, 0, 1), "eye": (-5.0, 9.6, 3.4), "probe_eye": (0.0, 0.35, 0.9),
             "probes": [((-0.45, 0.6, -1.0), "outer face"), ((-0.92, 0.15, 0.35), "finger groove (front strap)"),
                        ((0.79, 0.1, -0.3), "back strap")],
             "slice": (2, 0, -0.7, (-1.7, 0.9)), "passes": ("t", (-0.2, 0.4, 1.0)), "drum_eye": (1.0, -1.2, 1.3)},
}
HERO = "grip"


def shot_of(res, name):
    """A part's framing: hand-tuned in SHOTS, or derived from its holder and size for any other part."""
    if name in SHOTS:
        return SHOTS[name]
    mesh, p, n = res["mesh"], res["points"], res["normals"]
    front = -np.asarray(res["params"]["mount"], float)
    front /= np.linalg.norm(front)
    up = np.array([0.0, 1.0, 0.0]) if abs(front[2]) > 0.7 else np.array([0.0, 0.0, 1.0])
    right = np.cross(up, front)
    lo, hi = mesh.bounds
    mid, ext = (lo + hi) / 2, hi - lo
    if abs(front[2]) > 0.7:  # looked at from above, like the samples
        look = np.array([-0.33, -0.69, 0.0]) + 0.56 * front
    else:  # standing up, like the grip: from the front, a little to the side and above
        look = 0.84 * front + 0.44 * right + np.array([0.0, 0.0, 0.3])
    eye = 1.8 * np.linalg.norm(ext) * look / np.linalg.norm(look)
    frac = res["feasible"].mean(axis=1)
    facing = np.flatnonzero((n @ front > 0.3) & (frac > 0))
    if facing.size == 0:
        facing = np.flatnonzero(frac > 0)
    off = np.hypot((p[facing] - mid) @ right, (p[facing] - mid) @ up)
    middle = facing[off <= np.quantile(off, 0.2)]
    center = middle[np.argmax(frac[middle])]  # the freest point in the middle of the front
    probes = [(p[center], "front face"), (p[facing[np.argmin(frac[facing])]], "tightest reachable spot")]
    blocked = np.flatnonzero(frac == 0)
    probes.append((p[blocked[len(blocked) // 2]], "unreachable") if blocked.size else
                  (p[facing[np.argsort(frac[facing])[facing.size // 2]]], "typical point"))
    cut, along = int(np.argmax(np.abs(up))), int(np.argmax(np.abs(right)))
    half = min(0.3 * ext[along], 1.3)
    t = p[facing] @ up
    return {"front": front, "up": up, "eye": eye, "probes": probes,
            "slice": (cut, along, mid[cut], (mid[along] - half, mid[along] + half)),
            "passes": ("t", tuple(np.quantile(t, (0.3, 0.5, 0.7))))}


def view_axes(shot):
    """(front, up, right) of a part's straight-on front view."""
    front, up = np.asarray(shot.get("front", (0, 0, 1)), float), np.asarray(shot.get("up", (0, 1, 0)), float)
    return front, up, np.cross(up, front)


def holder_layers(res):
    """The holder as drawn: a floor quad under a table-held part, a short stub of a fixture, or nothing."""
    mesh, p = res["mesh"], res["params"]
    if p["holder"] == "none":
        return []
    view = front_view(mesh, p["mount"], 0.01)
    basis, pix = view["basis"], view["pix"]
    if p["holder"] == "table":
        lo, hi = view["origin"], view["origin"] + pix * np.array(view["heights"].shape[::-1])
        pad = 1.5 * max(hi - lo)
        quad = np.array([[lo[0] - pad, lo[1] - pad], [hi[0] + pad, lo[1] - pad], [hi[0] + pad, hi[1] + pad], [lo[0] - pad, hi[1] + pad]])
        verts = np.column_stack([quad, np.full(4, view["base"] - 1e-4)]) @ basis
        return [dict(mesh_layer(trimesh.Trimesh(verts, [[0, 1, 2], [0, 2, 3]]), hex_rgb(FLOOR)), layer=3)]
    # Fixture: the inset silhouette swept a short way behind the part (the model sweeps it as far as the cylinder reaches).
    depth_in = ndimage.distance_transform_edt(ndimage.binary_fill_holes(view["heights"] > -np.inf)) * pix
    ring = max(contourpy.contour_generator(z=depth_in).lines(p["inset"] + pix / 2), key=len)[:-1]
    for _ in range(3):  # soften the pixel staircase so the walls shade smoothly
        ring = (np.roll(ring, 1, axis=0) + 2 * ring + np.roll(ring, -1, axis=0)) / 4
    uv = view["origin"] + (ring + 0.5) * pix
    nxt = np.roll(uv, -1, axis=0)
    if np.sum(uv[:, 0] * nxt[:, 1] - nxt[:, 0] * uv[:, 1]) < 0:
        uv = uv[::-1]  # counter-clockwise seen from the part
    count, depth = len(uv), 0.15 * max(mesh.extents)
    verts = np.vstack([np.column_stack([uv, np.full(count, view["base"])]),
                       np.column_stack([uv, np.full(count, view["base"] - depth)])]) @ basis
    i, j = np.arange(count), (np.arange(count) + 1) % count
    walls = np.concatenate([np.column_stack([i, i + count, j + count]), np.column_stack([i, j + count, j])])
    tri = Triangulation(uv[:, 0], uv[:, 1])
    inner = tri.triangles[Outline(uv).contains_points(uv[tri.triangles].mean(axis=1))]
    caps = np.concatenate([inner, inner[:, ::-1] + count])
    stub = trimesh.Trimesh(verts, np.concatenate([walls, caps]), process=False)
    return [dict(mesh_layer(stub, hex_rgb(HOLDER)), layer=3)]


def overview_camera(res, name, fov=30):
    lo, hi = res["mesh"].bounds
    mid = (lo + hi) / 2
    return camera(mid + np.array(shot_of(res, name)["eye"]), mid - np.array([0, 0, 0.25]), fov=fov)


def colors(cmap, values, lo, hi):
    """Map values to sRGB triples."""
    return np.asarray(cmap(Normalize(lo, hi, clip=True)(values)))[:, :3]


def dots(points, normals, cols, radius):
    """Sphere layer lifted a little off the surface."""
    return {"centers": points + 0.6 * radius * normals, "radius": radius, "col": np.asarray(cols, float).reshape(-1, 3)}


def picture(ax, img, title=None):
    ax.imshow(img)
    ax.set_axis_off()
    if title:
        ax.set_title(title, loc="left", fontsize=12, color=INK, pad=6)


def pick(res, site, shot, mask=None):
    """Grid point for a probe site: nearest to a 3D point, or front-facing nearest to view coordinates (s, t)."""
    p, n = res["points"], res["normals"]
    allowed = np.ones(len(p), bool) if mask is None else mask
    if len(site) == 3:
        cand = np.flatnonzero(allowed)
        return cand[np.argmin(np.linalg.norm(p[cand] - site, axis=1))]
    front, up, right = view_axes(shot)
    cand = np.flatnonzero(allowed & (n @ front > 0.3))
    d = np.hypot(p[cand] @ right - site[0], p[cand] @ up - site[1])
    near = cand[d < d.min() + 0.02]
    return near[np.argmax(p[near] @ front)]


def thin(points, mask, size):
    """One masked point per voxel of `size` (the one nearest the voxel center), for legible glyphs."""
    idx = np.flatnonzero(mask)
    vox = np.floor(points[idx] / size)
    off = np.linalg.norm(points[idx] / size - vox - 0.5, axis=1)
    order = np.lexsort((off, vox[:, 2], vox[:, 1], vox[:, 0]))
    v = vox[order]
    first = np.ones(len(order), bool)
    first[1:] = np.any(v[1:] != v[:-1], axis=1)
    return idx[order[first]]


def wire_cylinder(center, axis, normal, radius, length, ring=48, lines=10):
    """Rim circles and generator lines of a cylinder, as tube segments."""
    u = normal - axis * (normal @ axis)
    u /= np.linalg.norm(u)
    w = np.cross(axis, u)
    ang = np.linspace(0, 2 * np.pi, ring + 1)
    segs = []
    for s in (-length / 2, length / 2):
        pts = center + s * axis + radius * (np.cos(ang)[:, None] * u + np.sin(ang)[:, None] * w)
        segs.append(np.stack([pts[:-1], pts[1:]], axis=1))
    g = np.linspace(0, 2 * np.pi, lines, endpoint=False)
    rim = center + radius * (np.cos(g)[:, None] * u + np.sin(g)[:, None] * w)
    segs.append(np.stack([rim - axis * length / 2, rim + axis * length / 2], axis=1))
    return np.concatenate(segs)


def moved(layer, rot, pos):
    """A triangle layer carried by a rigid motion (x -> rot x + pos)."""
    tri = rot.apply(layer["tri"].reshape(-1, 3)).reshape(layer["tri"].shape) + pos
    return dict(layer, tri=tri, nrm=rot.apply(layer["nrm"].reshape(-1, 3)).reshape(layer["nrm"].shape))


def caption(img, text):
    """Write a caption on a small backing into the top-left corner of a rendered frame."""
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_manager.findfont("DejaVu Sans"), 17)
    box = draw.textbbox((14, 10), text, font=font)
    draw.rounded_rectangle((box[0] - 7, box[1] - 5, box[2] + 7, box[3] + 6), radius=4, fill=SURFACE)
    draw.text((14, 10), text, fill=INK, font=font)
    return img


def unreachable_by_holder(res):
    """Unreachable points that the part alone would leave room for (the holder takes the rest)."""
    ok = res["feasible"].any(axis=1)
    if res["params"]["holder"] == "table":  # part flags are only resolved where the table leaves room
        return ~ok & res["holder_hit"].all(axis=1)
    return ~ok & erode(~res["self_hit"], res["params"]["margin"]).any(axis=1)


def passes(res, shot):
    """Grid points along the rollout passes: front-facing reachable points, swept in a serpentine."""
    p, n, ok = res["points"], res["normals"], res["feasible"].any(axis=1)
    front, up, right = view_axes(shot)
    sweep, fixed = shot["passes"]
    cand = np.flatnonzero(ok & (n @ front > 0.2))
    s, t = p[cand] @ right, p[cand] @ up
    along, across = (s, t) if sweep == "s" else (t, s)
    steps = np.linspace(along.min() + 0.12, along.max() - 0.12, 34)
    trail = []
    for r, value in enumerate(fixed):
        for a in (steps if r % 2 == 0 else steps[::-1]):
            d = np.hypot(along - a, across - value)
            if d.min() > 0.03:
                continue
            near = cand[d < d.min() + 0.01]
            i = near[np.argmax(p[near] @ front)]
            if not trail or trail[-1] != i:
                trail.append(i)
    return trail


def asset_images(res, name, out):
    """Render and save every per-step asset for one part; returns key images for the overview."""
    mesh, p, n = res["mesh"], res["points"], res["normals"]
    params = res["params"]
    h, radius, length = params["spacing"], params["radius"], params["length"]
    feas, ok = res["feasible"], res["feasible"].any(axis=1)
    shot = shot_of(res, name)
    front, up, right = view_axes(shot)
    part, holder = mesh_layer(mesh, hex_rgb(PART)), holder_layers(res)
    scene = [part, *holder]
    holder_name = {"table": "the table", "fixture": "the fixture"}.get(params["holder"], "the holder")
    cam = overview_camera(res, name)
    size = (1500, 950)
    shots = {}

    # 0 - the solid as given (as held).
    shots["model"] = render(cam, size, meshes=scene)
    shots["model"].save(out / "0_model.png")

    # 1 - grid points by ray family, plus a close-up with normals.
    full = render(cam, size, meshes=scene, points=[dots(p, n, [to_rgb(FAMILY[f]) for f in res["family"]], 0.4 * h)])
    # Close-up: a cutaway section through the part, with one thin slab of grid points and their normals.
    cut, along, at, (s0, s1) = shot["slice"]
    column = (np.round(at / h - 0.5) + 0.5) * h  # one lattice column: other families sit exactly on it
    near = np.flatnonzero((np.abs(p[:, cut] - column) < 0.3 * h) & (p[:, along] > s0) & (p[:, along] < s1))
    look = np.eye(3)[cut]
    section = dict(mesh_layer(mesh.slice_plane((column - 0.4 * h) * look, look), hex_rgb(PART)), cull=False)
    lo_s, hi_s = p[near].min(axis=0), p[near].max(axis=0)
    zc = (lo_s + hi_s) / 2
    span = max(hi_s - lo_s)
    zcam = camera(zc - 1.7 * span * look + 0.45 * span * front, zc, up=front, fov=34)
    tips = np.stack([p[near] + 0.3 * h * n[near], p[near] + 6 * h * n[near]], axis=1)
    zoom = render(zcam, (950, 950), meshes=[section], outline=0.7,
                  points=[dots(p[near], n[near], [to_rgb(FAMILY[f]) for f in res["family"][near]], 0.42 * h)],
                  tubes=[{"seg": tips, "radius": 0.07 * h, "col": hex_rgb(INK2)}])
    fig, axes = plt.subplots(1, 2, figsize=(16, 6.4), gridspec_kw={"width_ratios": [1.58, 1]})
    picture(axes[0], full, f"Step 1 · {len(p):,} grid points at {h:g} in spacing, covering every face")
    picture(axes[1], zoom, "Cutaway: one row of grid points and their smooth normals")
    handles = [Patch(color=c, label=f"sampled by rays along {a}") for c, a in zip(FAMILY, "XYZ")]
    handles.append(Line2D([], [], color=INK2, lw=2, label="surface normal"))
    fig.legend(handles=handles, loc="lower center", ncol=4, fontsize=11, bbox_to_anchor=(0.5, 0.0))
    fig.subplots_adjust(left=0.01, right=0.99, top=0.93, bottom=0.08, wspace=0.03)
    fig.savefig(out / "1_grid.png", dpi=100)
    plt.close(fig)
    shots["grid"] = zoom

    # 2 - fraction of collision-free orientations; unreachable points by cause.
    frac = feas.mean(axis=1)
    by_holder = unreachable_by_holder(res)
    cols = colors(SEQ, frac, 0, 1)
    cols[~ok] = to_rgb(CRITICAL)
    cols[by_holder] = to_rgb(MUTED)
    shots["reach"] = render(cam, size, meshes=scene, points=[dots(p, n, cols, 0.75 * h)])
    fig, ax = plt.subplots(figsize=(15, 10))
    picture(ax, shots["reach"], f"Step 2 · collision-free cylinder orientations (R {radius:g} in, L {length:g} in) — "
                                f"{ok.mean():.0%} of points reachable")
    cax = fig.add_axes([0.08, 0.075, 0.32, 0.018])
    bar = fig.colorbar(plt.cm.ScalarMappable(Normalize(0, 100), SEQ), cax=cax, orientation="horizontal")
    bar.set_label("share of axis angles that are collision-free (%)", color=INK2)
    bar.outline.set_visible(False)
    fig.legend(handles=[Patch(color=CRITICAL, label="unreachable: the part is in the way"),
                        Patch(color=MUTED, label=f"unreachable: {holder_name} is in the way")],
               loc="lower right", bbox_to_anchor=(0.93, 0.045), fontsize=11)
    fig.subplots_adjust(left=0.01, right=0.99, top=0.95, bottom=0.11)
    fig.savefig(out / "2_reach.png", dpi=100)
    plt.close(fig)

    # 2b - three probe sites: the fan of axis angles and the C-space as a polar plot.
    nb = feas.shape[1]
    theta_bins = (np.arange(nb) + 0.5) * np.pi / nb
    fan_len = min(length, 1.6)
    fig = plt.figure(figsize=(16, 9.6))
    for j, (*site, label) in enumerate(shot["probes"]):
        i = pick(res, site[0] if len(site) == 1 else site, shot)
        c = res["center"][i]
        ks = np.arange(0, nb, 3)
        axes_k = np.cos(theta_bins[ks])[:, None] * res["t1"][i] + np.sin(theta_bins[ks])[:, None] * res["t2"][i]
        fan = np.stack([c - axes_k * fan_len / 2, c + axes_k * fan_len / 2], axis=1)
        fan_col = np.where(feas[i, ks][:, None], to_rgb(GOOD), to_rgb(CRITICAL))
        tubes = [{"seg": fan, "radius": 0.0045, "col": fan_col}]
        if ok[i]:
            tubes.append({"seg": wire_cylinder(c, res["axis"][i], n[i], radius, length), "radius": 0.009, "col": hex_rgb(TOOL)})
        view = n[i] + np.array(shot.get("probe_eye", (-0.45, -0.8, 0.55)))
        pcam = camera(p[i] + 3.6 * view / np.linalg.norm(view), p[i] + 0.3 * n[i], fov=30)
        img = render(pcam, (760, 600), meshes=scene, tubes=tubes, points=[dots(p[i:i + 1], n[i:i + 1], [to_rgb(INK)], 0.02)])
        ax = fig.add_subplot(2, 3, j + 1)
        picture(ax, img, f"{'ABC'[j]} · {label}")
        pax = fig.add_subplot(2, 3, j + 4, projection="polar")
        state = np.where(feas[i], 0, np.where(res["holder_hit"][i] & ~res["self_hit"][i], 2, 1))
        ang = np.concatenate([theta_bins, theta_bins + np.pi])
        bar_cols = np.array([GOOD, CRITICAL, MUTED])[np.concatenate([state, state])]
        pax.bar(ang, np.ones(2 * nb), width=np.pi / nb, bottom=0.35, color=bar_cols, linewidth=0)
        if ok[i]:
            th = res["theta"][i] % np.pi
            pax.plot([th, th], [0, 1.35], color=INK, lw=2)
            pax.plot([th + np.pi, th + np.pi], [0, 1.35], color=INK, lw=2)
        pax.set_ylim(0, 1.4)
        pax.set_yticks([])
        pax.set_xticks(np.radians(np.arange(0, 360, 45)))
        pax.tick_params(colors=MUTED, labelsize=9)
        pax.grid(color=HAIR)
        pax.set_title(f"{feas[i].mean():.0%} of axis angles free", fontsize=11, color=INK2, pad=14)
    fig.legend(handles=[Patch(color=GOOD, label="collision-free"), Patch(color=CRITICAL, label="hits the part"),
                        Patch(color=MUTED, label=f"hits {holder_name}"), Line2D([], [], color=INK, lw=2, label="chosen axis (step 3)"),
                        Line2D([], [], color=TOOL, lw=2, label=f"cylinder at the chosen axis ({length:g} in)")],
               loc="lower center", ncol=5, fontsize=11, bbox_to_anchor=(0.5, 0.0))
    fig.suptitle("Step 2 · at each point the cylinder axis can only turn in the tangent plane; "
                 "colors show which angles collide", x=0.01, ha="left", fontsize=13)
    fig.subplots_adjust(left=0.01, right=0.99, top=0.92, bottom=0.07, wspace=0.05, hspace=0.2)
    fig.savefig(out / "2_probes.png", dpi=100)
    plt.close(fig)

    # The smoothest field without the grain term, to show what keeping the axis square to the grain costs.
    e, delta = res["pair_edges"], res["pair_delta"]
    free = optimize(p, n, res["t1"], res["t2"], feas, res["edges"], tuple(params["prefer"]), grain=None)["theta"]
    fields = {"naive": res["naive"], "free": free, "optimized": res["theta"]}
    turns = {k: line_change(v, delta, e) for k, v in fields.items()}
    g = np.asarray(params["grain"] or (0.0, 0.0, 1.0), float)
    g /= np.linalg.norm(g)
    axes_of = {k: np.cos(v)[:, None] * res["t1"] + np.sin(v)[:, None] * res["t2"] for k, v in fields.items()}
    tilts = {k: np.degrees(np.arcsin(np.minimum(np.abs(a[ok] @ g), 1.0))) for k, a in axes_of.items()}
    tilt_name = "rotation about Y" if np.allclose(g, (0, 0, 1)) else "tilt toward the grain"

    # 3 - naive per-point choice vs optimized field, seen straight on, glyphs colored by local turn.
    show = thin(p, ok & (n @ front > 0.2), 0.1)
    bad = thin(p, ~ok & (n @ front > 0.2), 0.1)
    w_s, w_t = np.ptp(mesh.vertices @ right), np.ptp(mesh.vertices @ up)
    mid = (mesh.bounds[0] + mesh.bounds[1]) / 2
    flat = camera(mid + 10.0 * front, mid, up=up, ortho=1.06 * w_t)
    flat_size = (1000, int(1000 * w_t / w_s)) if w_s >= w_t else (int(1000 * w_s / w_t), 1000)
    imgs = {}
    for key in ("naive", "optimized"):
        turn = res["change_naive"] if key == "naive" else res["change"]
        base = p[show] + 0.012 * n[show]
        seg = np.stack([base - axes_of[key][show] * 0.04, base + axes_of[key][show] * 0.04], axis=1)
        imgs[key] = render(flat, flat_size, meshes=scene,
                           tubes=[{"seg": seg, "radius": 0.008, "col": colors(GLYPH, turn[show], 0, 8)}],
                           points=[dots(p[bad], n[bad], [to_rgb(CRITICAL)] * len(bad), 0.011)])
    tall = 1.5 + 8.5 * flat_size[1] / flat_size[0]
    fig, axes = plt.subplots(1, 2, figsize=(17, tall))
    picture(axes[0], imgs["naive"], f"Naive: each point's own best axis — mean turn "
                                    f"{turns['naive'].mean():.2f}°, {np.mean(turns['naive'] > 10):.1%} of pairs > 10°")
    picture(axes[1], imgs["optimized"], f"Step 3 · optimized for least angular change — mean turn "
                                        f"{turns['optimized'].mean():.2f}°, {np.mean(turns['optimized'] > 10):.1%} of pairs > 10°")
    cax = fig.add_axes([0.06, 0.62 / tall, 0.26, 0.14 / tall])
    bar = fig.colorbar(plt.cm.ScalarMappable(Normalize(0, 8), GLYPH), cax=cax, orientation="horizontal", extend="max")
    bar.set_label("mean turn to neighboring points (°)", color=INK2)
    bar.outline.set_visible(False)
    fig.legend(handles=[Line2D([], [], color=GLYPH(0.5), lw=3, label="cylinder axis, seen straight on (every 0.1 in)"),
                       Patch(color=CRITICAL, label="unreachable point")], loc="lower right", bbox_to_anchor=(0.97, 0.25 / tall), fontsize=11)
    fig.subplots_adjust(left=0.005, right=0.995, top=1 - 0.45 / tall, bottom=1.05 / tall, wspace=0.02)
    fig.savefig(out / "3_field.png", dpi=100)
    plt.close(fig)
    shots["field"] = imgs["optimized"]

    # 3b - the grain: how far the axis tilts out of level, without and with the grain term.
    fig, axes = plt.subplots(1, 2, figsize=(17, 6.6))
    for ax, key, title in zip(axes, ("free", "optimized"), ("Smoothest field, no grain term", "Output: grain term + smoothness")):
        cols = np.tile(to_rgb(HAIR), (len(p), 1))
        cols[ok] = colors(SEQ, tilts[key], 0, 30)
        img = render(cam, (1100, 700), meshes=scene, points=[dots(p, n, cols, 0.75 * h)])
        picture(ax, img, f"{title} — |{tilt_name}| mean {tilts[key].mean():.2f}°, "
                         f"{np.mean(tilts[key] > 5):.1%} of points > 5°")
    cax = fig.add_axes([0.06, 0.08, 0.26, 0.025])
    bar = fig.colorbar(plt.cm.ScalarMappable(Normalize(0, 30), SEQ), cax=cax, orientation="horizontal", extend="max")
    bar.set_label(f"|{tilt_name}|: cylinder axis out of level (°)", color=INK2)
    bar.outline.set_visible(False)
    fig.legend(handles=[Patch(color=HAIR, label="unreachable point")], loc="lower right", bbox_to_anchor=(0.97, 0.05), fontsize=11)
    fig.subplots_adjust(left=0.005, right=0.995, top=0.93, bottom=0.14, wspace=0.02)
    fig.savefig(out / "3_grain.png", dpi=100)
    plt.close(fig)

    # 3c - the output rotations about X, Y and Z.
    fig, axes = plt.subplots(1, 3, figsize=(18, 5.6))
    for ax, k, lim, label in zip(axes, range(3), (180, 90, 180), ("X (rx)", "Y (ry)", "Z (rz)")):
        cols = np.tile(to_rgb(HAIR), (len(p), 1))
        cols[ok] = colors(DIV, res["euler"][ok, k], -lim, lim)
        img = render(cam, (1000, 680), meshes=scene, points=[dots(p, n, cols, 0.75 * h)])
        picture(ax, img, f"Rotation about {label}")
        cax = ax.inset_axes([0.05, -0.06, 0.5, 0.035])
        bar = fig.colorbar(plt.cm.ScalarMappable(Normalize(-lim, lim), DIV), cax=cax, orientation="horizontal")
        bar.set_ticks([-lim, 0, lim])
        bar.outline.set_visible(False)
    fig.suptitle("Output · tool rotation per point in degrees (fixed axes X, then Y, then Z; zero = cylinder along X "
                 "on a level surface; light gray = unreachable)", x=0.01, ha="left", fontsize=12)
    fig.subplots_adjust(left=0.005, right=0.995, top=0.88, bottom=0.1, wspace=0.02)
    fig.savefig(out / "3_angles.png", dpi=100)
    plt.close(fig)

    # 3d - how often neighbors turn by more than x, how far axes tilt toward the grain, and the energy.
    fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(20, 5.4))
    series = (("naive", "naive (each point's best)", FAMILY[1]), ("free", "smoothest, no grain term", FAMILY[2]),
              ("optimized", "output", FAMILY[0]))
    for ax, data, xmax, mark, unit in ((a1, turns, 90, 10, "turn between neighboring points"),
                                       (a2, tilts, 90, 5, f"|{tilt_name}|")):
        grid = np.linspace(0, xmax, 361)
        for key, label, col in series:
            srt = np.sort(data[key])
            share = 100 * (1 - np.searchsorted(srt, grid, side="right") / len(srt))
            ax.plot(grid, np.maximum(share, 1e-4), color=col, lw=2, label=f"{label}: {np.mean(data[key] > mark):.2%} > {mark}°",
                    solid_capstyle="round")
        ax.set_yscale("log")
        ax.set(xlim=(0, xmax), ylim=(1e-3, 100), xlabel=f"{unit}, x (°)", ylabel="share above x (%)")
        ax.legend(loc="upper right", fontsize=10)
    a1.set_title("Smoothness: turns between neighbors", loc="left", fontsize=12)
    a2.set_title("Grain: axis out of level", loc="left", fontsize=12)
    labels, values = zip(*res["history"])
    it = np.arange(1, len(values) + 1)
    lo_e, span = min(values), max(values) - min(values)  # forced grain tilt adds a constant floor, so zoom to the change
    a3.plot(it, values, color=FAMILY[0], lw=2, solid_capstyle="round")
    a3.set_xscale("log")
    edges = np.cumsum([1] + [sum(lbl.startswith(k) for lbl in labels) for k in ("naive", "relax", "polish", "refine")])
    for (start, stop), text, shade in zip(zip(edges[1:-1], edges[2:]), ("global\nrelaxation", "discrete\npolish",
                                                                         "continuous\nrefine"), (True, False, True)):
        if shade:
            a3.axvspan(start - 0.5, stop - 0.5, color=HAIR, alpha=0.6, lw=0)
        a3.annotate(text, (np.sqrt(start * max(stop - 1, start)), lo_e - 0.2 * span), ha="center", va="bottom",
                    color=INK2, fontsize=10)
    a3.annotate(f"naive {values[0]:,.0f}", (1, values[0]), xytext=(8, -14), textcoords="offset points", color=INK2, fontsize=10)
    a3.annotate(f"final {values[-1]:,.0f}", (it[-1], values[-1]), xytext=(-6, 8), textcoords="offset points",
                ha="right", color=INK2, fontsize=10)
    a3.set(xlim=(1, it[-1]), ylim=(lo_e - 0.25 * span, max(values) + 0.1 * span), xlabel="iteration (log scale)",
           ylabel="energy: Σ sin²(turn) + grain term")
    a3.set_title("Optimizer energy", loc="left", fontsize=12)
    fig.subplots_adjust(left=0.05, right=0.985, top=0.9, bottom=0.14, wspace=0.24)
    fig.savefig(out / "3_stats.png", dpi=100)
    plt.close(fig)

    # 4 - one pass both ways: the cylinder moving over the fixed part, and the part (with its holder)
    # moving against the cylinder fixed in its own frame (X along its axis, contact at the origin).
    trail = passes(res, shot)
    rot = tool_rotations(res["axis"][trail], n[trail])
    inv = rot.inv()
    pos = -inv.apply(p[trail])
    reach_d = max(mesh.extents)
    drum_cam = camera(np.array(shot.get("drum_eye", (1.0, -1.2, 1.3))) * reach_d, np.array([0, 0, -0.36]) * reach_d, fov=34)
    if len(mesh.faces) > 300_000:  # frames are small: a lighter copy of a dense scan renders the same
        scene = [mesh_layer(mesh.simplify_quadric_decimation(face_count=250_000), hex_rgb(PART)), *holder]
    drum = cylinder_mesh((0, 0, radius), (1, 0, 0), (0, 0, -1), radius, length)
    drum["col"] = np.broadcast_to(hex_rgb(TOOL), (len(drum["tri"]), 3, 3))
    trail_col = [to_rgb("#0d366b")]
    frames = []
    for k, i in enumerate(trail):
        tool = cylinder_mesh(res["center"][i], res["axis"][i], n[i], radius, length)
        tool["col"] = np.broadcast_to(hex_rgb(TOOL), (len(tool["tri"]), 3, 3))
        done = trail[:k + 1]
        left = render(cam, (720, 456), meshes=[*scene, tool], outline=0.6,
                      points=[dots(p[done], n[done], trail_col * len(done), 0.018)])
        r, t = inv[k], pos[k]
        right_img = render(drum_cam, (720, 456), meshes=[*(moved(m, r, t) for m in scene), drum], outline=0.6,
                           points=[dots(r.apply(p[done]) + t, r.apply(n[done]), trail_col * len(done), 0.018)])
        frame = Image.new("RGB", (1444, 456), SURFACE)
        frame.paste(caption(left, "Cylinder moves, part fixed"), (0, 0))
        frame.paste(caption(right_img, "Part moves, cylinder fixed"), (724, 0))
        frames.append(frame)
    sample = frames[::max(1, len(frames) // 10)]
    mosaic = Image.new("RGB", (frames[0].width, frames[0].height * len(sample)))
    for k, f in enumerate(sample):
        mosaic.paste(f, (0, k * f.height))
    palette = mosaic.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    gif = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]  # one palette: frames store as diffs
    gif[0].save(out / "4_rollout.gif", save_all=True, append_images=gif[1:], duration=90, loop=0, optimize=True)
    return shots


def drum_work(track, lo_on, hi_on, weights, along):
    """Work done so far at each axial position `along` the drum: each point's weight spread over its patch."""
    start, span = track + lo_on, hi_on - lo_on + 1e-9
    return np.array([weights @ np.clip((x - start) / span, 0.0, 1.0) for x in along])


def worn_drum(center, axis, normal, radius, length, work, scale, blur=0.25):
    """The drum, shaded along its length by the work it has done (it spins, so wear goes all the way round).

    `work` is the cumulative work at evenly spaced stations along the drum; its density is blurred
    by `blur` inches, about a contact patch, so single points do not show as stripes.
    """
    drum = cylinder_mesh(center, axis, normal, radius, length, rings=200)
    x = (drum["tri"] - center) @ axis
    along = np.linspace(-length / 2, length / 2, len(work))
    kernel = np.exp(-0.5 * (np.arange(-60, 61) * (along[1] - along[0]) / blur) ** 2)
    density = np.convolve(np.gradient(work, along), kernel / kernel.sum(), "same")
    drum["col"] = np.asarray(SEQ(np.clip(np.interp(x, along, density) / scale, 0, 1)))[..., :3]
    return drum


def middle_ring(center, axis, normal, radius):
    """Thin ring around the drum's middle, so its sliding shows."""
    u = normal - axis * (normal @ axis)
    u /= np.linalg.norm(u)
    ang = np.linspace(0, 2 * np.pi, 61)
    pts = center + 1.01 * radius * (np.cos(ang)[:, None] * u + np.sin(ang)[:, None] * np.cross(axis, u))
    return {"seg": np.stack([pts[:-1], pts[1:]], axis=1), "radius": 0.012, "col": hex_rgb(INK2)}


def wear_strip(width, rows, length, band, step=0.25):
    """Bar charts of the drum's work per `step` inches along it, drawn under the rollout frames.

    `rows` is [(label, shares per bin, color)]. Bars share one scale, up to four times an even share
    (the dashed line); taller bars are cut off, so each label carries the busiest inch in words.
    """
    font = ImageFont.truetype(font_manager.findfont("DejaVu Sans"), 15)
    small = ImageFont.truetype(font_manager.findfont("DejaVu Sans"), 13)
    row_h, left, right = 58, 390, 24
    img = Image.new("RGB", (width, 18 + row_h * len(rows) + 22), SURFACE)
    draw = ImageDraw.Draw(img)
    even = step / band
    top = 4 * even
    span = width - left - right
    for r, (label, share, color) in enumerate(rows):
        y0, y1 = 14 + r * row_h, 14 + r * row_h + row_h - 12
        draw.text((14, y0 + 6), label, fill=INK, font=font)
        ye = y1 - (y1 - y0) * even / top
        for xx in range(left, left + span, 12):  # dashed even-share line
            draw.line((xx, ye, min(xx + 6, left + span), ye), fill=MUTED, width=1)
        bin_w = span / len(share)
        for k, v in enumerate(share):
            if v <= 0:
                continue
            h = (y1 - y0) * min(v / top, 1.0)
            draw.rectangle((left + k * bin_w + 1, y1 - h, left + (k + 1) * bin_w - 1, y1), fill=color)
        draw.line((left, y1, left + span, y1), fill="#c3c2b7", width=1)
    y = 14 + row_h * len(rows) - 8
    for inch in range(-int(length // 2), int(length // 2) + 1):
        x = left + span * (inch + length / 2) / length
        draw.line((x, y, x, y + 4), fill=MUTED, width=1)
        draw.text((x, y + 5), f"{inch:+d}" if inch else "0 in", fill=MUTED, font=small, anchor="mt")
    draw.text((left - 10, y + 5), "along the drum:", fill=MUTED, font=small, anchor="rt")
    return img


def pass_strokes(res, shot, count=None, stop=0.3):
    """Stops along tracking passes, each swept end to end in turn (serpentine), and the points each stop covers.

    Without `count` these are all the passes of the part's largest region, in order. With it they
    are a run of `count` neighbors whose passes all face the front, if there is one, where the
    contact steps most regularly (exactly one step from pass to pass); ties go to the most
    front-facing surface. A pass is swept along Y (the integral of the feed direction n x a) with
    a stop about every `stop` inches, and each stop covers the pass's points since the last one.
    """
    p, n, number = res["points"], res["normals"], res["pass"]
    idx = np.flatnonzero(number >= 0)
    remap = np.full(len(p), -1)
    remap[idx] = np.arange(idx.size)
    feed, link = integrate(p[idx], np.cross(n[idx], res["axis"][idx]), remap[res["pair_edges"]])
    along = np.full(len(p), np.nan)
    along[idx] = feed
    label = connected_components(link, directed=False)[1]
    main = idx[label == np.bincount(label).argmax()]
    if count:
        front = view_axes(shot)[0]
        facing = main[n[main] @ front > 0.2]
        sizes = np.bincount(number[facing], minlength=number.max() + 1)
        offset = np.full(sizes.size, np.nan)
        offset[number[idx]] = res["track"][idx]
        regular = np.abs(np.abs(np.diff(offset)) - res["track_stats"]["step"]) < 1e-6
        first = max((sizes[w:w + count].min() > 0, regular[w:w + count - 1].sum(), sizes[w:w + count].sum(), w)
                    for w in range(max(sizes.size - count, 0) + 1))[3]
        order = range(first, first + count)
    else:
        order = np.unique(number[main])
    trail, covered = [], []
    for r, k in enumerate(order):
        members = main[number[main] == k]
        if members.size == 0:
            continue
        w = (1.0 if r % 2 == 0 else -1.0) * along[members]
        sequence = np.argsort(w, kind="stable")
        members, w = members[sequence], w[sequence]
        marks = np.linspace(w[0], w[-1], max(2, int(np.ceil((w[-1] - w[0]) / stop)) + 1))
        cuts = np.searchsorted(w, marks, side="right")
        for mark, lo, hi in zip(marks, np.concatenate([[0], cuts[:-1]]), cuts):
            trail.append(members[np.argmin(np.abs(w - mark))])
            covered.append(members[lo:hi])
    return trail, covered


def track_images(res, name, out, preview=None, full=False):
    """Tracking (step 4): 5_track.png and 5_track.gif, or with `full` only 5_track_full.gif, every pass of
    the part. With `preview` (rollout frame numbers) only those frames are rendered, and returned instead."""
    scene = [mesh_layer(res["mesh"], hex_rgb(PART)), *holder_layers(res)]
    cam = overview_camera(res, name)
    if preview is None and not full:
        track_figure(res, out, scene, cam)
    return track_rollout(res, name, out, scene, cam, preview, full)


def track_figure(res, out, scene, cam):
    """5_track.png: the offset field, the safe range, and the drum's work without and with tracking."""
    p, n = res["points"], res["normals"]
    length, h, band = res["params"]["length"], res["params"]["spacing"], res["track_stats"]["band"]
    ok = np.isfinite(res["track"])
    area = 1 / np.abs(n).max(axis=1)
    lo_on, hi_on = np.nan_to_num(res["patch_lo"]), np.nan_to_num(res["patch_hi"])
    fig = plt.figure(figsize=(21, 6.9))
    grid = fig.add_gridspec(2, 3, width_ratios=[1.3, 1.3, 1], hspace=0.55, wspace=0.1)
    by_pass = res["track_stats"]["mode"] == "passes"
    low, high = res["track_lo"].copy(), res["track_hi"].copy()
    if by_pass:  # a pass keeps one offset, so it gets the overlap of its points' safe ranges
        number = res["pass"][ok]
        pass_lo, pass_hi = np.full(number.max() + 1, -np.inf), np.full(number.max() + 1, np.inf)
        np.maximum.at(pass_lo, number, low[ok])
        np.minimum.at(pass_hi, number, high[ok])
        low[ok], high[ok] = pass_lo[number], pass_hi[number]
    lost = 1 - (high - low) / band
    limited = ok & (lost > 0.01)  # the probe margin alone takes about 0.5%
    unit = "pass" if by_pass else "point"
    for col, (title, values, cmap, lo, hi, label) in enumerate((
            (f"Where along the drum each {unit} touches", res["track"], DIV, -band / 2, band / 2,
             "contact position along the drum from its middle (in)"),
            (f"Where tracking is limited ({limited.sum() / ok.sum():.0%} of the surface)", lost, SEQ, 0, 1,
             f"share of the {band:g} in band the {unit} may not use"))):
        cols = np.tile(to_rgb(HAIR), (len(p), 1))
        shown = ok if col == 0 else limited
        cols[shown] = colors(cmap, values[shown], lo, hi)
        ax = fig.add_subplot(grid[:, col])
        picture(ax, render(cam, (1100, 760), meshes=scene, points=[dots(p, n, cols, 0.75 * h)]), title)
        cax = ax.inset_axes([0.04, -0.035, 0.55, 0.03])
        bar = fig.colorbar(plt.cm.ScalarMappable(Normalize(lo, hi), cmap), cax=cax, orientation="horizontal")
        bar.set_label(label, color=INK2)
        bar.outline.set_visible(False)
        if col == 1:
            bar.set_ticks([0, 0.5, 1], labels=["0%", "50%", "100%"])
            ax.legend(handles=[Patch(color=HAIR, label="not limited, or unreachable")], loc="upper left", fontsize=10)
    step = 0.25
    for row, (track, title, color) in enumerate(((np.zeros(ok.sum()), "Without tracking", FAMILY[1]),
                                                 (res["track"][ok], "With tracking", FAMILY[0]))):
        edges, share = wear(track, lo_on[ok], hi_on[ok], area[ok], length, step)
        ax = fig.add_subplot(grid[row, 2])
        ax.bar(edges[:-1] + step / 2, 100 * share / step, width=0.8 * step, color=color, linewidth=0)
        ax.axhline(100 / band, color=INK2, lw=1, ls=(0, (4, 3)))
        top = busiest(track, lo_on[ok], hi_on[ok], area[ok], length)
        ax.set_title(f"{title}: busiest inch does {top:.0%} of the work", loc="left", fontsize=12)
        ax.set(xlim=(-length / 2, length / 2), ylim=(0, None), ylabel="% of the work per in")
        ax.grid(axis="x", visible=False)
        if row == 1:  # headroom above the bars for the legend
            ax.set_ylim(0, 1.45 * max(100 * share.max() / step, 100 / band))
            ax.set_xlabel("position along the drum from its middle (in)")
            ax.legend(handles=[Line2D([], [], color=INK2, lw=1, ls=(0, (4, 3)),
                                      label=f"even over the {band:g} in band ({100 / band:.0f}% per in)")],
                      loc="upper left", fontsize=10)
    fig.subplots_adjust(left=0.005, right=0.985, top=0.93, bottom=0.12)
    fig.savefig(out / "5_track.png", dpi=100)
    plt.close(fig)


def track_rollout(res, name, out, scene, cam, preview=None, full=False):
    """5_track.gif (six neighboring passes) or, with `full`, 5_track_full.gif (every pass): tracked, both ways.

    The drum slides along its axis over the fixed part, or the part slides along the fixed drum.
    The drum darkens where it has worked, and a strip compares its work so far without and with
    tracking, counting all of each pass's surface that the strokes have covered.
    """
    mesh, p, n = res["mesh"], res["points"], res["normals"]
    radius, length, band = res["params"]["radius"], res["params"]["length"], res["track_stats"]["band"]
    shot = shot_of(res, name)
    ok = np.isfinite(res["track"])
    area = 1 / np.abs(n).max(axis=1)
    lo_on, hi_on = np.nan_to_num(res["patch_lo"]), np.nan_to_num(res["patch_hi"])
    by_pass = res["track_stats"]["mode"] == "passes"
    trail, covered = pass_strokes(res, shot, None if full else 6) if by_pass else ([], [])
    if not trail:  # a swept field, or no pass faces the front: the rollout strokes, one point each
        by_pass, trail = False, [i for i in passes(res, shot) if ok[i]]
        covered = [np.array([i]) for i in trail]
    s = res["track"][trail]
    rot = tool_rotations(res["axis"][trail], n[trail])
    inv = rot.inv()
    pos = -inv.apply(p[trail]) + s[:, None] * [1.0, 0.0, 0.0]
    if len(mesh.faces) > 300_000:
        scene = [mesh_layer(mesh.simplify_quadric_decimation(face_count=250_000), hex_rgb(PART)), *scene[1:]]
    view = np.asarray(shot.get("track_eye", (0.35, -1.0, 0.75)), float)
    view /= np.linalg.norm(view)
    target = np.array([0.0, 0.0, -0.3 * max(mesh.extents)])
    hfov = 2 * np.arctan(np.tan(np.radians(34) / 2) * 720 / 456)
    drum_cam = camera(target + view * 1.1 * (length / 2 + 0.6) / np.tan(hfov / 2), target, fov=34)
    part_cam = camera(cam["eye"] + (cam["eye"] - (mesh.bounds[0] + mesh.bounds[1]) / 2) * 0.75,
                      (mesh.bounds[0] + mesh.bounds[1]) / 2 - [0, 0, 0.25], fov=34)  # far enough back for most of the drum
    along = np.linspace(-length / 2, length / 2, 401)  # stations every 0.025 in: every 10th is a strip bin edge
    total = area[np.concatenate(covered)].sum()
    scale = 2.2 * total / band  # an even band ends a little under half way up the shading
    work = {"Without tracking": np.zeros(along.size), "With tracking": np.zeros(along.size)}
    trail_col = [to_rgb("#0d366b")]
    frames, done_area = [], 0.0
    for k, i in enumerate(trail):
        cov = covered[k]
        work["Without tracking"] += drum_work(np.zeros(cov.size), lo_on[cov], hi_on[cov], area[cov], along)
        work["With tracking"] += drum_work(res["track"][cov], lo_on[cov], hi_on[cov], area[cov], along)
        done_area += area[cov].sum()
        if preview is not None and k not in preview:
            continue
        done = trail[:k + 1]
        center = res["center"][i]  # already shifted back along the axis by the offset
        tool = worn_drum(center, res["axis"][i], n[i], radius, length, work["With tracking"], scale)
        left = render(part_cam, (720, 456), meshes=[*scene, tool], outline=0.6,
                      tubes=[middle_ring(center, res["axis"][i], n[i], radius)],
                      points=[dots(p[done], n[done], trail_col * len(done), 0.018)])
        r, t = inv[k], pos[k]
        drum = worn_drum(np.array([0.0, 0.0, radius]), np.array([1.0, 0.0, 0.0]), np.array([0.0, 0.0, -1.0]), radius,
                         length, work["With tracking"], scale)
        right_img = render(drum_cam, (720, 456), meshes=[*(moved(m, r, t) for m in scene), drum], outline=0.6,
                           tubes=[middle_ring(np.array([0.0, 0.0, radius]), np.array([1.0, 0.0, 0.0]),
                                              np.array([0.0, 0.0, -1.0]), radius)],
                           points=[dots(r.apply(p[done]) + t, r.apply(n[done]), trail_col * len(done), 0.018)])
        rows = []
        for (label, so_far), color in zip(work.items(), (FAMILY[1], FAMILY[0])):
            top = (so_far[40:] - so_far[:-40]).max() / max(done_area, 1e-300)  # any 1 in (40 stations) of the drum
            rows.append((f"{label}: busiest inch {top:.0%} of the work", np.diff(so_far[::10]) / total, color))
        strip = wear_strip(1444, rows, length, band)
        frame = Image.new("RGB", (1444, 456 + strip.height), SURFACE)
        where = f"pass {res['pass'][i] + 1} of {res['track_stats']['passes']} · " if by_pass else ""
        frame.paste(caption(left, f"Cylinder moves, part fixed · {where}contact {s[k]:+.2f} in"), (0, 0))
        frame.paste(caption(right_img, "Part moves, cylinder fixed"), (724, 0))
        frame.paste(strip, (0, 456))
        frames.append(frame)
    if preview is not None:
        return frames
    sample = frames[::max(1, len(frames) // 10)]
    mosaic = Image.new("RGB", (frames[0].width, frames[0].height * len(sample)))
    for k, f in enumerate(sample):
        mosaic.paste(f, (0, k * f.height))
    palette = mosaic.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    gif = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    gif[0].save(out / ("5_track_full.gif" if full else "5_track.gif"), save_all=True, append_images=gif[1:],
                duration=60 if full else 90, loop=0, optimize=True)


def tracked(res):
    """`res` with step 4 planned at its defaults, unless the saved result already has it."""
    if res.get("track_stats") is None or "patch_lo" not in res or "pass" not in res:
        found = plan(res)
        res = dict(res, track_stats=found.pop("stats"), **found)
        res["center"] = res["points"] + res["params"]["radius"] * res["normals"] - \
            np.where(np.isfinite(res["track"]), res["track"], 0.0)[:, None] * np.nan_to_num(res["axis"])
    return res


def main(names=None):
    """Compute (or reuse) results for each part and write all assets."""
    names = names or [k for k, (src, _) in PARTS.items() if src.exists()]
    overview = {}
    for name in names:
        src, opts = PARTS[name]
        res_dir = ROOT / "results" / name
        if not (res_dir / "summary.json").exists():
            save(run(src, **opts), res_dir)
        res = load(res_dir)
        out = ROOT / "assets" / name
        out.mkdir(parents=True, exist_ok=True)
        overview[name] = asset_images(res, name, out)
        track_images(tracked(res), name, out)
        if name == HERO:
            track_images(tracked(res), name, out, full=True)
        print(f"{name}: assets written to {out}")
    if HERO in overview:
        shots = overview[HERO]
        fig, axes = plt.subplots(1, 4, figsize=(20, 4.6))
        for ax, key, title in zip(axes, ("model", "grid", "reach", "field"),
                                  ("Input solid", "1 · surface grid + normals", "2 · collision-free share", "3 · smooth axis field")):
            picture(ax, shots[key], title)
            ax.set_anchor("N")  # top-align panels of different aspect so the titles line up
        fig.subplots_adjust(left=0.005, right=0.995, top=0.9, bottom=0.01, wspace=0.02)
        fig.savefig(ROOT / "assets" / "overview.png", dpi=100)
        plt.close(fig)


if __name__ == "__main__":
    main(sys.argv[1:])
