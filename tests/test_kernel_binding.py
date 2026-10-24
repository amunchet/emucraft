"""The ctypes binding of the C kernel (the kernel itself is unit-tested in C)."""

import math
from array import array

import pytest

from emucraft import kernel
from emucraft.gcode import NOCUT, RAPID, SPINDLE
from emucraft.kernel import EV_BODY, EV_FLUTE, EV_NOCUT_CUT, EV_RAPID_CUT, EV_SPINDLE_OFF_CUT, KernelError, Sim, simplify

FLAT, BALL, BULL, CONE = 0, 1, 2, 3


def path(points, flags=SPINDLE, tool=0):
    """Arrays for a polyline; flags / tool may be a value or a list per move."""
    pts = array("d", [c for p in points for c in p])
    n = len(points) - 1
    fl = array("i", flags if isinstance(flags, list) else [flags] * n)
    tl = array("i", tool if isinstance(tool, list) else [tool] * n)
    return pts, fl, tl


def slot(z=0.8, flags=SPINDLE):
    return path([(0.2, 0.5, z), (0.8, 0.5, z)], flags)


def unit_block(n=100, tolerance=None):
    return Sim(n, n, 0.0, 0.0, 1.0 / n, 1.0, 0.0, tolerance)


def zigzag(rows=12):
    """A raster over the unit square, stepping down 0.02 per row, then a rapid."""
    pts = [(0.1, 0.1, 0.9)]
    y = 0.1
    for row in range(rows):
        xs = [0.1 + 0.8 * i / 30 for i in range(31)]
        if row % 2:
            xs.reverse()
        z = 0.8 - 0.02 * row
        pts += [(x, y, z) for x in xs[1:]]
        y += 0.07
        pts.append((xs[-1], y, z))
    pts.append((0.5, 0.5, 0.7))
    n = len(pts) - 1
    return path(pts, [SPINDLE] * (n - 1) + [RAPID | SPINDLE])


def test_library_loads_and_is_consistent():
    lib = kernel.load()
    assert lib.ec_version() == 1
    assert lib.ec_tile_size() == 32
    assert kernel.find_library().exists()


def test_flat_slot_volume():
    with unit_block() as sim:
        sim.set_tool(0, FLAT, 0.1)
        before = sim.volume()
        res = sim.run(*slot())
        expect = (0.6 * 0.2 + math.pi * 0.01) * 0.2
        assert res.volume == pytest.approx(expect, rel=0.03)
        assert before - sim.volume() == pytest.approx(res.volume, abs=1e-9)
        assert res.events == []
        h = sim.heights()
        assert len(h) == 100 * 100
        assert h[50 * 100 + 50] == pytest.approx(0.8)  # on the slot
        assert h[10 * 100 + 50] == 1.0  # off the slot
        assert sim.marks()[50 * 100 + 50] == 1  # cut by move 0
        x0, y0, x1, y1 = res.dirty
        assert 9 <= x0 <= 11 and 89 <= x1 <= 91 and 39 <= y0 <= 41 and 59 <= y1 <= 61
        assert sim.min_height() == pytest.approx(0.8)
        sim.reset()
        assert sim.volume() == pytest.approx(1.0)


@pytest.mark.parametrize("flags, kind", [
    (RAPID | SPINDLE, EV_RAPID_CUT),
    (0, EV_SPINDLE_OFF_CUT),
    (SPINDLE | NOCUT, EV_NOCUT_CUT),
])
def test_cut_events(flags, kind):
    with unit_block() as sim:
        sim.set_tool(0, FLAT, 0.1)
        res = sim.run(*slot(flags=flags))
        assert [(e.kind, e.move, e.body) for e in res.events] == [(kind, 0, -1)]
        ev = res.events[0]
        assert ev.depth == pytest.approx(0.2)
        assert ev.volume == pytest.approx(res.volume)
        assert 0.1 <= ev.x <= 0.9 and 0.4 <= ev.y <= 0.6 and ev.z == 1.0  # inside the slot outline
        # nothing left to cut on the second pass: no event
        assert sim.run(*slot(flags=flags)).events == []


def test_flute_and_holder_events():
    with unit_block() as sim:
        sim.set_tool(0, FLAT, 0.1, flute=0.1, bodies=[(0.15, 0.3)])
        res = sim.run(*slot(z=0.5))
        kinds = {e.kind: e for e in res.events}
        assert kinds[EV_FLUTE].depth == pytest.approx(0.4)  # 0.5 deep cut, 0.1 flutes
        assert kinds[EV_BODY].body == 0
        assert kinds[EV_BODY].depth == pytest.approx(0.2)  # holder bottom at 0.8
    with unit_block() as sim:  # holder above the stock: nothing
        sim.set_tool(0, FLAT, 0.1, bodies=[(0.15, 0.6)])
        assert sim.run(*slot(z=0.5)).events == []


def test_tolerance_hides_shallow_events():
    with unit_block(tolerance=0.05) as sim:
        sim.set_tool(0, FLAT, 0.1)
        assert sim.run(*slot(z=0.97, flags=RAPID | SPINDLE)).events == []
        assert len(sim.run(*slot(z=0.9, flags=RAPID | SPINDLE)).events) == 1


def test_tool_shapes_remove_less_than_flat():
    volumes = {}
    for shape, corner, slope in [(FLAT, 0, 0), (BULL, 0.03, 0), (BALL, 0, 0), (CONE, 0, 1.0)]:
        with unit_block() as sim:
            sim.set_tool(0, shape, 0.1, corner, slope)
            volumes[shape] = sim.run(*slot(z=0.7)).volume
    assert volumes[FLAT] > volumes[BULL] > volumes[BALL] > volumes[CONE] > 0


def test_undefined_tool_slot_cuts_nothing():
    with unit_block() as sim:
        pts, fl, _ = slot()
        res = sim.run(pts, fl, array("i", [-1]))
        assert res.volume == 0 and sim.volume() == pytest.approx(1.0)
        sim.set_tool(3, FLAT, 0.1)
        sim.clear_tool(3)
        assert sim.run(pts, fl, array("i", [3])).volume == 0


def test_threads_give_identical_results():
    runs = {}
    for threads in (1, 4):
        with Sim(400, 400, 0.0, 0.0, 0.0025, 1.0, 0.0, 1e-4) as sim:
            sim.set_tool(0, BALL, 0.05, bodies=[(0.12, 0.3)])
            res = sim.run(*zigzag(), threads=threads, move_outputs=True)
            runs[threads] = (bytes(sim.heights()), bytes(sim.marks()), res)
    (h1, m1, r1), (h4, m4, r4) = runs[1], runs[4]
    assert h1 == h4 and m1 == m4
    assert r1.dirty == r4.dirty
    assert r1.volume == pytest.approx(r4.volume, rel=1e-12)

    def key(e):
        return e.kind, e.move, e.body, e.depth, e.x, e.y, e.z

    assert [key(e) for e in r1.events] == [key(e) for e in r4.events]
    assert {e.kind for e in r1.events} == {EV_RAPID_CUT, EV_BODY}
    # cut events count every cell; body events count a lower bound per band
    for a, b in zip(r1.events, r4.events):
        if a.kind != EV_BODY:
            assert a.cells == b.cells and a.volume == pytest.approx(b.volume, rel=1e-12)
    assert list(r1.move_depth) == list(r4.move_depth)
    assert all(a == pytest.approx(b, rel=1e-12, abs=1e-18) for a, b in zip(r1.move_volume, r4.move_volume))
    assert sum(r1.move_volume) == pytest.approx(r1.volume, rel=1e-9)


def test_move_ranges_add_up():
    pts, fl, tl = zigzag()
    with Sim(200, 200, 0.0, 0.0, 0.005, 1.0, 0.0) as whole:
        whole.set_tool(0, FLAT, 0.05)
        total = whole.run(pts, fl, tl).volume
        heights = bytes(whole.heights())
    with Sim(200, 200, 0.0, 0.0, 0.005, 1.0, 0.0) as parts:
        parts.set_tool(0, FLAT, 0.05)
        n = len(fl)
        volume = parts.run(pts, fl, tl, 0, n // 3).volume + parts.run(pts, fl, tl, n // 3, n).volume
        assert bytes(parts.heights()) == heights
    assert volume == pytest.approx(total, rel=1e-12)


def test_bands_are_tile_aligned():
    with Sim(10, 1000, 0.0, 0.0, 1.0, 1.0, 0.0) as sim:
        for count in (1, 3, 4, 16, 100):
            bands = sim.bands(count)
            assert bands[0][0] == 0 and bands[-1][1] == 1000
            assert all(a[1] == b[0] for a, b in zip(bands, bands[1:]))
            assert all(r0 % 32 == 0 for r0, _ in bands)
            assert len(bands) <= count


def test_simplify_merges_collinear_runs():
    pts, fl, tl = path(
        [(0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0), (3, 1, 0), (3, 2, 0), (0, 0, 1)],
        [SPINDLE] * 5 + [RAPID | SPINDLE],
    )
    out_p, out_f, out_t, first = simplify(pts, fl, tl, 1e-9)
    assert list(out_p) == [0, 0, 0, 3, 0, 0, 3, 2, 0, 0, 0, 1]
    assert list(out_f) == [SPINDLE, SPINDLE, RAPID | SPINDLE]
    assert list(first) == [0, 3, 5]
    # different tools are never merged
    _, _, _, first = simplify(pts, fl, array("i", [0, 0, 1, 1, 1, 1]), 1e-9)
    assert list(first) == [0, 2, 3, 5]
    # a bend larger than eps stops the merge
    bent, _, _ = path([(0, 0, 0), (1, 0.01, 0), (2, 0, 0)])
    assert len(simplify(bent, array("i", [2, 2]), array("i", [0, 0]), 1e-3)[1]) == 2
    assert len(simplify(bent, array("i", [2, 2]), array("i", [0, 0]), 0.02)[1]) == 1
    # empty input
    empty = simplify(array("d", [0, 0, 0]), array("i"), array("i"), 0.1)
    assert len(empty[1]) == 0


def test_bad_arguments():
    with pytest.raises(KernelError):
        Sim(0, 10, 0, 0, 1, 1, 0)
    with pytest.raises(KernelError):
        Sim(10, 10, 0, 0, -1, 1, 0)
    with pytest.raises(KernelError):
        Sim(10, 10, 0, 0, 1, 0, 1)  # top below bottom
    with Sim(10, 10, 0, 0, 0.1, 1, 0) as sim:
        with pytest.raises(KernelError):
            sim.set_tool(0, FLAT, 0.0)
        with pytest.raises(KernelError):
            sim.set_tool(0, 7, 0.1)
        with pytest.raises(KernelError):
            sim.set_tool(256, FLAT, 0.1)
        with pytest.raises(KernelError):
            sim.set_tool(0, CONE, 0.1)  # a cone needs a slope
        with pytest.raises(ValueError):
            sim.run(array("d", [0, 0, 0]), array("i", [2]), array("i", [0]))
        empty = sim.run(array("d", [0, 0, 0, 1, 1, 1]), array("i", [2]), array("i", [0]), 0, 0)
        assert empty.volume == 0 and empty.events == []


def test_close_is_idempotent():
    sim = Sim(10, 10, 0, 0, 0.1, 1, 0)
    sim.close()
    sim.close()
