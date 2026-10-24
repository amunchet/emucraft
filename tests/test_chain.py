"""Chained checks: operations of one job share the stock."""

import base64
import gzip
import json
import re
from array import array
from pathlib import Path

import pytest

from emucraft.check import check_file, check_text
from emucraft.cli import main
from emucraft.html import build_report_html

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
ROUGH = EXAMPLES / "pocket_corners.nc"
FINISH = EXAMPLES / "pocket_finish.nc"


def test_second_operation_alone_crashes():
    result = check_file(FINISH)
    assert result.status == "fail"
    issue = result.issues[0]
    assert issue.kind == "rapid-cut" and issue.first_line + 1 == 24
    assert abs(issue.depth - 0.35) < 1e-3


def test_chained_second_operation_passes_on_the_roughed_stock():
    rough = check_file(ROUGH, keep_sim=True)
    remaining = rough.sim.volume()
    finish = check_file(FINISH, previous=rough, keep_sim=True, keep_initial=True)
    try:
        assert rough.sim is None, "the simulation moves to the chained result"
        assert finish.status == "pass", [i.message for i in finish.issues]
        assert finish.grid == rough.grid and finish.stock == rough.stock
        # only the 0.01 wall skin (and the finishing tool's corners) is left to cut
        assert 0.02 < finish.stats["volume_removed"] < 0.08
        assert abs(finish.stats["remaining_volume"] - (remaining - finish.stats["volume_removed"])) < 1e-6
        assert finish.initial_heights is not None and len(finish.initial_heights) == rough.grid.nx * rough.grid.ny
    finally:
        finish.sim.close()


def test_chain_needs_a_kept_simulation_and_matching_units():
    rough = check_file(ROUGH)
    with pytest.raises(ValueError, match="keep_sim"):
        check_file(FINISH, previous=rough)
    rough = check_file(ROUGH, keep_sim=True)
    metric = FINISH.read_text().replace("G20", "G21")
    with pytest.raises(ValueError, match="units"):
        check_text(metric, "metric.nc", previous=rough)
    rough.sim.close()


def test_cli_chain(tmp_path, capsys):
    assert main(["check", str(FINISH), "-q"]) == 1
    assert main(["check", "--chain", str(ROUGH), str(FINISH), "-q", "--json", str(tmp_path / "r.json")]) == 0
    reports = json.loads((tmp_path / "r.json").read_text())
    assert [r["status"] for r in reports] == ["pass", "pass"]
    # without --chain the second program is checked on a fresh block again
    assert main(["check", str(ROUGH), str(FINISH), "-q"]) == 1


def test_chained_report_embeds_the_starting_stock():
    rough = check_file(ROUGH, keep_sim=True)
    finish = check_file(FINISH, previous=rough, keep_sim=True, keep_initial=True)
    page = build_report_html(finish)
    finish.sim.close()
    data = json.loads(re.search(r"window\.EMUCRAFT_EMBED = (\{.*?\});</script>", page, re.S).group(1))
    payload = data["payload"]
    g = payload["display"]
    heights = array("f")
    heights.frombytes(gzip.decompress(base64.b64decode(payload["initial_heights_gz"])))
    assert len(heights) == g["nx"] * g["ny"]
    # the pocket floor from the roughing program is there before the finish runs
    assert min(heights) == pytest.approx(-0.4, abs=1e-6) and max(heights) == 0.0
    assert len(page) < 3_000_000


def test_chained_program_never_cuts_with_the_previous_programs_tools(tmp_path):
    """A tool without geometry must not inherit the slot of the previous program's tool."""
    unknown = tmp_path / "unknown_tool.nc"
    unknown.write_text("%\nG20 G90\nT6 M6\nS5000 M3\nG0 X2. Y1.5 Z0.1\nG0 Z-0.1\nG1 X2.5 F20.\nG0 Z1.\nM30\n%\n")
    rough = check_file(ROUGH, keep_sim=True)
    chained = check_file(unknown, previous=rough, keep_sim=True)
    chained.sim.close()
    assert [i.kind for i in chained.issues] == ["tool"]
    assert chained.stats["volume_removed"] == 0.0


def test_chaining_after_a_program_without_stock_starts_fresh(tmp_path):
    nostock = tmp_path / "nostock.nc"
    nostock.write_text("%\nG20 G90\nG0 X0 Y0 Z1.\nM30\n%\n")
    first = check_file(nostock, keep_sim=True)
    assert first.sim is None and first.stock is None
    second = check_file(FINISH, previous=first)
    assert [m.code for m in second.messages if m.code == "chain"] == ["chain"]
    assert [i.kind for i in second.issues] == ["rapid-cut"]  # checked on a fresh block


def test_starting_stock_is_not_cut_through_at_the_far_edges():
    """Viewer cells inside the stock must never sample the check grid's trimmed
    overhang (the last check cell starts inside the stock but is centred past it)."""
    from emucraft.check import trim_to_stock
    from emucraft.kernel import Sim
    from emucraft.model import StockSpec

    stock = StockSpec(0, 0, -1, 1.0, 1.0, 0)
    with Sim(4, 4, 0, 0, 0.3, 0.0, -1.0) as sim:  # centres 0.15 .. 1.05: the last one overhangs
        trim_to_stock(sim, stock)
        # a 0.13 viewer grid has a centre at 0.975: inside the stock, over the overhang cell
        naive = sim.sample(8, 8, 0, 0, 0.13)
        clamped = sim.sample(8, 8, 0, 0, 0.13, stock.xmax, stock.ymax)
    assert naive[7] == -1.0  # what the viewer used to show: a cut-through strip
    assert min(clamped) == max(clamped) == 0.0
