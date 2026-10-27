"""Step 4: tracking keeps the contact, stays clear of everything, and spreads the drum's work."""
import numpy as np
import pytest
import trimesh
from scipy.spatial.transform import Rotation

from moonshot.geometry import load_mesh, tangent_frames
from moonshot.grid import neighbor_graph, surface_grid
from moonshot.holder import fixture_probes, front_view
from moonshot.pipeline import run
from moonshot.reach import probe_points
from moonshot.track import busiest, limits, offsets, pass_wear, passes, stepped, sweep

R, L, TOL, GAP = 0.5, 10.0, 0.001, 0.01
BAND = 0.9 * L
NONE = np.empty((0, 3))


def brute_limits(points, normals, axes, probes, holder, band, gap):
    """`limits` without a margin or plane, by projecting every probe onto every drum axis."""
    out = []
    for p, n, a in zip(points, normals, axes):
        lo, hi = -band / 2, band / 2
        for cloud, near, keep in ((probes, R + gap, True), (holder, R - TOL, False)):
            d = cloud - (p + R * n)
            t = d @ a
            t = t[np.einsum("ij,ij->i", d, d) - t * t < near ** 2]
            on = t[np.abs(t) <= L / 2]
            if keep and on.size:  # touched material stays on the drum
                lo, hi = max(lo, -L / 2 - min(on.min(), 0)), min(hi, L / 2 - max(on.max(), 0))
            if (t > L / 2).any():  # material past an end stays past it
                lo = max(lo, L / 2 - t[t > L / 2].min())
            if (t < -L / 2).any():
                hi = min(hi, -L / 2 - t[t < -L / 2].max())
        out.append((lo, hi))
    return np.array(out).T


def test_limits_match_brute_force():
    mesh = load_mesh("samples/pocket_block.stl")
    pts, nrm, _, _ = surface_grid(mesh, 0.15)
    rng = np.random.default_rng(4)
    pick = rng.choice(len(pts), 150, replace=False)
    pts, nrm = pts[pick], nrm[pick]
    t1, t2 = tangent_frames(nrm)
    ang = rng.uniform(0, np.pi, len(pts))
    axes = np.cos(ang)[:, None] * t1 + np.sin(ang)[:, None] * t2
    probes = probe_points(mesh, 0.04)
    holder = fixture_probes(front_view(mesh, (0, 0, -1), 0.01), 0.05, 0.05, 9.6)
    lo, hi, _, _ = limits(pts, nrm, axes, probes, R, L, BAND, GAP, holder, tol=TOL)
    want_lo, want_hi = brute_limits(pts, nrm, axes, probes, holder, BAND, GAP)
    assert np.allclose(lo, want_lo, atol=1e-9) and np.allclose(hi, want_hi, atol=1e-9)
    assert (lo < -1).any() and (hi < BAND / 2 - 1e-6).any()  # both kinds of limit occur


def test_flat_top_keeps_the_drum_over_it():
    """A drum lying along a 4 in face may slide only as far as it still covers the whole face."""
    box = trimesh.creation.box(extents=(4, 2, 1))
    probes = probe_points(box, 0.02)
    pts = np.array([[0.0, 0.0, 0.5], [1.5, 0.0, 0.5], [0.0, 0.0, 0.5]])
    nrm = np.tile([0.0, 0.0, 1.0], (3, 1))
    axes = np.array([[1.0, 0, 0], [1.0, 0, 0], [0, 1.0, 0]])
    lo, hi, lo_on, hi_on = limits(pts, nrm, axes, probes, R, L, BAND, GAP)
    assert np.allclose(lo, [-3.0, -1.5, -4.0], atol=1e-6) and np.allclose(hi, [3.0, 4.5, 4.0], atol=1e-6)
    assert np.allclose(lo_on, [-2.0, -3.5, -1.0], atol=1e-6) and np.allclose(hi_on, [2.0, 0.5, 1.0], atol=1e-6)


def test_post_past_the_end_stops_the_drum():
    """A post 0.5 in past the drum's end allows 0.5 in of travel toward it, a holder the same."""
    plate = trimesh.creation.box(extents=(4, 2, 1))
    post = trimesh.creation.box(extents=(1, 1, 3), transform=trimesh.transformations.translation_matrix((6, 0, 1)))
    pts, nrm, axes = np.array([[0.0, 0.0, 0.5]]), np.array([[0.0, 0.0, 1.0]]), np.array([[1.0, 0.0, 0.0]])
    plate_probes, post_probes = probe_points(plate, 0.02), probe_points(post, 0.02)
    lo, hi, _, _ = limits(pts, nrm, axes, np.vstack([plate_probes, post_probes]), R, L, BAND, GAP)
    assert np.allclose([lo[0], hi[0]], [-0.5, 3.0], atol=1e-6)
    lo, hi, _, _ = limits(pts, nrm, axes, plate_probes, R, L, BAND, GAP, holder=post_probes)
    assert np.allclose([lo[0], hi[0]], [-0.5, 3.0], atol=1e-6)


def test_holder_only_has_to_stay_clear():
    """Holder material near the drum but outside it never limits tracking; part material does."""
    t = np.linspace(5.3, 8.0, 50)
    pts, nrm, axes = np.array([[0.0, 0.0, 0.0]]), np.array([[0.0, 0.0, 1.0]]), np.array([[1.0, 0.0, 0.0]])
    rail = np.column_stack([t, np.zeros_like(t), np.full_like(t, R + R + GAP / 2)])  # grazing the drum's top
    assert np.allclose(limits(pts, nrm, axes, NONE, R, L, BAND, GAP, holder=rail)[0], -BAND / 2)
    assert np.allclose(limits(pts, nrm, axes, rail, R, L, BAND, GAP)[0], L / 2 - 5.3)
    inside = rail - [0.0, 0.0, R]  # on the axis: a collision once the drum reaches it
    assert np.allclose(limits(pts, nrm, axes, NONE, R, L, BAND, GAP, holder=inside)[0], L / 2 - 5.3)


def test_table_limits_a_tilted_drum():
    """A drum on a wall, tilted 10 deg down: its low end reaches the table after 0.81 in of travel."""
    alpha = np.radians(10)
    pts, nrm = np.array([[1.0, 0.0, 1.5]]), np.array([[1.0, 0.0, 0.0]])
    axes = np.array([[0.0, np.cos(alpha), -np.sin(alpha)]])
    lo, hi, _, _ = limits(pts, nrm, axes, NONE, R, L, BAND, GAP, plane=((0, 0, -1), 0.0), tol=TOL)
    slack = TOL + 1.5 - L / 2 * np.sin(alpha) - R * np.cos(alpha)
    assert np.isclose(lo[0], -slack / np.sin(alpha)) and np.isclose(hi[0], BAND / 2)


def test_offsets_are_safe_smooth_and_even():
    """On a plate with a strip that can barely slide, offsets stay in range, change slowly and use the band evenly."""
    g = (np.arange(150) + 0.5) * 0.02
    x, y = np.meshgrid(g, g, indexing="ij")
    pts = np.column_stack([x.ravel(), y.ravel(), np.zeros(x.size)])
    nrm = np.tile([0.0, 0.0, 1.0], (len(pts), 1))
    edges = neighbor_graph(pts, nrm, 0.02)
    lo, hi = np.full(len(pts), -BAND / 2), np.full(len(pts), BAND / 2)
    strip = np.abs(pts[:, 0] - 1.5) < 0.05
    lo[strip], hi[strip] = -0.3, 0.2
    area = np.ones(len(pts))
    phase = sweep(pts, nrm, edges, (0, 0, 1), 1)  # flat across the sweep direction: rings from one corner
    track = offsets(lo, hi, phase, BAND, edges, pts, area, 8.0)
    assert (track >= lo).all() and (track <= hi).all()
    step = np.linalg.norm(pts[edges[:, 0]] - pts[edges[:, 1]], axis=1)
    assert (np.abs(track[edges[:, 0]] - track[edges[:, 1]]) <= 8.0 * step + 1e-9).all()
    flat = np.zeros(len(pts))
    assert busiest(track, flat, flat, area, L) < 1.25 / BAND  # within 25% of even; untracked it is all of it
    assert np.isclose(busiest(flat, flat, flat, area, L), 1.0)


def test_passes_follow_the_surface_around_a_bend():
    """On a half-cylinder wall with level axes wrapping round it, passes are vertical strips 0.1 in apart along the arc."""
    radius, h = 1.0, 0.01
    ang = np.arange(0.5, np.pi * radius / h) * h / radius  # one column of points every 0.01 in of arc
    z = (np.arange(200) + 0.5) * h
    ang_g, z_g = np.meshgrid(ang, z, indexing="ij")
    pts = np.column_stack([radius * np.cos(ang_g.ravel()), radius * np.sin(ang_g.ravel()), z_g.ravel()])
    nrm = pts * [1.0, 1.0, 0.0] / radius
    axes = np.column_stack([-nrm[:, 1], nrm[:, 0], np.zeros(len(pts))])  # horizontal, along the arc
    number = passes(pts, axes, neighbor_graph(pts, nrm, h), 0.1)
    arc = ang_g.ravel() * radius
    assert number.max() + 1 == int(np.ceil(arc.max() / 0.1))  # pi in of arc, cut every 0.1 in
    for k in np.unique(number):  # each pass is one vertical strip, 0.1 in of arc wide
        strip = arc[number == k]
        assert strip.max() - strip.min() < 0.1 and np.ptp(pts[number == k, 2]) > 1.9


def test_stepped_offsets_hold_through_each_pass():
    """The contact steps once per pass, back and forth across the band, and a restricted pass takes its nearest safe offset."""
    index = np.repeat(np.arange(40), 25)
    lo, hi = np.full(index.size, -BAND / 2), np.full(index.size, BAND / 2)
    lo[index == 10], hi[index == 10] = -0.3, 0.2  # its turn would be +0.5; one point would be enough to hold it back
    track, per_pass = stepped(lo, hi, index, BAND, 0.5)
    assert np.array_equal(track, per_pass[index])
    assert np.allclose(per_pass[:10], -BAND / 2 + 0.5 * np.arange(10)) and np.isclose(per_pass[10], 0.2)
    assert np.allclose(per_pass[11:19], -BAND / 2 + 0.5 * np.arange(11, 19))
    assert np.isclose(per_pass[18], BAND / 2) and np.allclose(per_pass[19:22], BAND / 2 - 0.5 * np.arange(1, 4))  # turns back
    # Scored by the drum's busiest inch, the wave starts where the band is used most evenly, and still only moves
    # between passes.
    flat = np.zeros(index.size)
    score = pass_wear(index, flat, flat, np.ones(index.size), L)
    track, per_pass = stepped(lo, hi, index, BAND, 0.5, score)
    assert np.array_equal(track, per_pass[index]) and (track >= lo).all() and (track <= hi).all()
    assert score(per_pass) <= score(stepped(lo, hi, index, BAND, 0.5)[1]) + 1e-12
    assert np.isclose(score(np.zeros(40)), 1.0)  # everything at the middle


@pytest.mark.parametrize("mode", ["passes", "sweep"])
def test_tracking_leaves_the_contact_alone(mode):
    """Same orientations as without tracking, poses shifted along the drum, and the drum touches the same probes."""
    base = run("samples/s_channel.stl", spacing=0.1, log=lambda *_: None)
    res = run("samples/s_channel.stl", spacing=0.1, track=True, mode=mode, log=lambda *_: None)
    for key in ("feasible", "theta", "euler", "quat", "part_euler", "part_quat", "axis"):
        assert np.array_equal(base[key], res[key], equal_nan=True), key
    ok = np.isfinite(res["axis"][:, 0])
    s, a, p = res["track"][ok], res["axis"][ok], res["points"][ok]
    assert np.abs(s).max() > 1.0 and np.abs(s).max() <= BAND / 2
    assert np.array_equal(base["track"][ok], np.zeros(ok.sum())) and (base["pass"] == -1).all()
    if mode == "passes":  # one offset per pass
        number = res["pass"][ok]
        assert (number >= 0).all() and (res["pass"][~ok] == -1).all()
        first = np.full(number.max() + 1, np.nan)
        first[number] = s
        assert np.array_equal(s, first[number])
    assert np.allclose(res["tool"][ok], p - s[:, None] * a) and np.allclose(res["center"][ok], base["center"][ok] - s[:, None] * a)
    # In the drum's frame each contact now lands at (track, 0, 0).
    rot = Rotation.from_quat(res["part_quat"][ok], scalar_first=True)
    assert np.allclose(rot.apply(p) + res["part_pos"][ok], np.column_stack([s, np.zeros((len(s), 2))]), atol=1e-9)
    # Brute force at the chosen offsets: no collision, and the same part probes on the drum within the gap.
    probes = probe_points(res["mesh"], res["params"]["probe_spacing"])
    holder = fixture_probes(front_view(res["mesh"], (0, 0, -1), 0.005), 0.05, 0.05, 9.6)
    n = res["normals"][ok]
    for i in np.random.default_rng(5).choice(len(s), 60, replace=False):
        d = probes - (p[i] + R * n[i])
        t = d @ a[i]
        radial = np.sqrt(np.maximum(np.einsum("ij,ij->i", d, d) - t * t, 0))
        on_middle, on_tracked = np.abs(t) <= L / 2, (t >= -L / 2 - s[i]) & (t <= L / 2 - s[i])
        assert not (on_tracked & (radial < R - TOL)).any()
        assert np.array_equal(on_middle & (radial < R + GAP), on_tracked & (radial < R + GAP))
        d = holder - (p[i] + R * n[i])
        t = d @ a[i]
        radial = np.sqrt(np.maximum(np.einsum("ij,ij->i", d, d) - t * t, 0))
        assert not ((t >= -L / 2 - s[i]) & (t <= L / 2 - s[i]) & (radial < R - TOL)).any()
