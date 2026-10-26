"""Step 3: the optimizer stays feasible, beats the per-point choice, and propagates constraints."""
import numpy as np

from moonshot.geometry import tangent_frames
from moonshot.grid import neighbor_graph
from moonshot.pipeline import run
from moonshot.smooth import line_change, optimize

K = 180


def plate(n=40, h=0.05):
    """A flat square grid of points with upward normals."""
    g = (np.arange(n) + 0.5) * h
    x, y = np.meshgrid(g, g, indexing="ij")
    pts = np.column_stack([x.ravel(), y.ravel(), np.zeros(n * n)])
    nrm = np.tile([0.0, 0.0, 1.0], (n * n, 1))
    return pts, nrm, h


def bins_of(theta):
    return ((theta % np.pi) / (np.pi / K)).astype(int) % K  # a level axis can sit a hair below 0, i.e. at pi


def test_constraint_strip_steers_the_free_region():
    """A strip that only allows ~60 deg pulls the free rest of the plate toward 60 deg, not the X prior."""
    pts, nrm, h = plate()
    t1, t2 = tangent_frames(nrm)
    feas = np.ones((len(pts), K), bool)
    strip = pts[:, 0] < 0.3
    feas[strip] = False
    feas[np.ix_(strip, np.arange(57, 63))] = True
    edges = neighbor_graph(pts, nrm, h)
    res = optimize(pts, nrm, t1, t2, feas, edges)
    b = bins_of(res["theta"])
    assert feas[np.arange(len(pts)), b].all()
    free = ~strip
    assert np.abs(np.degrees(res["theta"][free] % np.pi) - 60).max() < 3.5
    assert np.degrees(res["naive"][free]).max() < 1  # the naive choice ignores its neighbors
    assert line_change(res["theta"], res["delta"], res["edges"]).sum() < line_change(res["naive"], res["delta"], res["edges"]).sum()


def test_grain_term_levels_the_axis():
    """On a plane sloping 40 deg, a tie-break toward Y runs the axis down the slope; the grain term (Z) levels it."""
    pts, nrm, h = plate()
    turn = np.array([[1, 0, 0], [0, np.cos(0.7), -np.sin(0.7)], [0, np.sin(0.7), np.cos(0.7)]])
    pts, nrm = pts @ turn.T, nrm @ turn.T
    t1, t2 = tangent_frames(nrm)
    feas = np.ones((len(pts), K), bool)
    edges = neighbor_graph(pts, nrm, h)
    for grain, rise in ((None, np.sin(0.7)), ((0, 0, 1), 0.0)):
        theta = optimize(pts, nrm, t1, t2, feas, edges, prefer=(0, 1, 0), grain=grain)["theta"]
        axis = np.cos(theta)[:, None] * t1 + np.sin(theta)[:, None] * t2
        assert np.allclose(np.abs(axis[:, 2]), rise, atol=0.02)


def test_unreachable_points_are_excluded_and_signs_are_consistent():
    pts, nrm, h = plate()
    t1, t2 = tangent_frames(nrm)
    feas = np.ones((len(pts), K), bool)
    feas[:10] = False
    edges = neighbor_graph(pts, nrm, h)
    res = optimize(pts, nrm, t1, t2, feas, edges)
    assert np.isnan(res["theta"][:10]).all() and not np.isnan(res["theta"][10:]).any()
    axis = np.cos(res["theta"])[:, None] * t1 + np.sin(res["theta"])[:, None] * t2
    e = res["edges"]
    assert (np.einsum("ij,ij->i", axis[e[:, 0]], axis[e[:, 1]]) > 0).all()


def test_pipeline_end_to_end():
    res = run("samples/dome_fillet.stl", spacing=0.05, log=lambda *_: None)
    ok = res["feasible"].any(axis=1)
    assert 0.3 < ok.mean() < 0.95
    assert res["feasible"][np.flatnonzero(ok), bins_of(res["theta"][ok])].all()
    assert np.isfinite(res["euler"][ok]).all() and np.isnan(res["euler"][~ok]).all()
    assert res["stats"]["optimized"]["mean"] < res["stats"]["naive"]["mean"]
    # Contact geometry: the cylinder axis is tangent and its center sits one radius off the surface.
    assert np.abs(np.einsum("ij,ij->i", res["axis"][ok], res["normals"][ok])).max() < 1e-9
    assert np.allclose(np.linalg.norm(res["center"] - res["points"], axis=1), 0.5)
