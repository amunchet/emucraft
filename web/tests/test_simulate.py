#!/usr/bin/env python3
"""
Tests the web simulation pipeline end to end (parser -> CFFI kernel -> result)

Needs the kernel extension built (`cd kernel/src && python cffi_builder.py`)
"""
import io
import math

import pytest

from web import simulate
from web.app import app


def program(size_x, size_y, moves, tool=".5"):
    """Builds a small program with a header for a size_x x size_y x 1 block"""
    return f"""
    N10( DIAMETER: {tool})
    N20( MIN X: 0)
    N30( MAX X: {size_x})
    N40( MIN Y: 0)
    N50( MAX Y: {size_y})
    N60( MIN Z: 0)
    N70( MAX Z: 1)
    M3S1000
    {moves}
    M30
    """


def height_at(result, x, y):
    """Stock Z at program coordinates (x, y)"""
    hm = result["heightmap"]
    i = int((x - result["block"]["x"][0]) / hm["cell_x"])
    j = int((y - result["block"]["y"][0]) / hm["cell_y"])
    return hm["z"][i * hm["ny"] + j]


@pytest.mark.parametrize("size, hole, untouched", [
    ((2, 1), (1.8, 0.5), (0.2, 0.5)),  # Wider than tall
    ((1, 2), (0.5, 1.8), (0.5, 0.2)),  # Taller than wide
])
def test_non_square_block(size, hole, untouched):
    """A plunge near the far edge of a non-square block lands where it should"""
    hx, hy = hole
    gcode = program(*size, f"G0 X{hx} Y{hy} Z2.\nG1 Z.5 F10")
    result = simulate.run(gcode, resolution=10, preview_size=10_000)

    assert result["grid"]["nx"] == size[0] * 100
    assert result["grid"]["ny"] == size[1] * 100
    assert result["heightmap"]["nx"] == result["grid"]["nx"]

    assert height_at(result, hx, hy) == pytest.approx(0.5)
    assert height_at(result, *untouched) == pytest.approx(1.0)

    # Only the plunge removed material: a 0.5 diameter cylinder, 0.5 deep.  The kernel
    # only cuts cells strictly inside the radius, so a 25 cell radius comes out ~7% small
    expected = math.pi * 0.25 ** 2 * 0.5
    assert result["stats"]["removed_volume"] == pytest.approx(expected, rel=0.1)

    # Every cell outside the plunge is untouched
    hm = result["heightmap"]
    cut_cells = sum(1 for z in hm["z"] if z < 1)
    assert cut_cells * hm["cell_x"] * hm["cell_y"] * 0.5 == pytest.approx(result["stats"]["removed_volume"])


def test_header_short_numbers():
    """Single digit header values are used for the stock, not the toolpath extents"""
    result = simulate.run(program(2, 1, "G0 X1. Y.5 Z2."), resolution=10)
    assert result["block"]["from_header"]
    assert result["block"]["x"] == [0, 2]
    assert result["block"]["y"] == [0, 1]
    assert result["block"]["z"] == [0, 1]


def test_rapid_into_stock():
    """A G0 that removes material is reported"""
    result = simulate.run(program(2, 2, "G0 X1. Y1. Z2.\nG0 Z.5\nG1 X1.5 F10"), resolution=10)
    assert result["stats"]["rapid_cut_count"] > 0
    assert result["rapid_cuts"][0]["x"] == 1.0
    assert result["rapid_cuts"][0]["y"] == 1.0


def test_no_rapid_into_stock():
    result = simulate.run(program(2, 2, "G0 X1. Y1. Z2.\nG1 Z.5 F10\nG1 X1.5"), resolution=10)
    assert result["stats"]["rapid_cut_count"] == 0
    assert result["stats"]["removed_volume"] > 0


@pytest.mark.parametrize("gcode", ["", "G91 X1"])
def test_bad_programs(gcode):
    with pytest.raises(simulate.SimulationError):
        simulate.run_isolated(gcode)


def test_isolated_matches_in_process():
    gcode = program(2, 1, "G0 X1.8 Y.5 Z2.\nG1 Z.5 F10")
    assert simulate.run_isolated(gcode, resolution=10) == simulate.run(gcode, resolution=10)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_healthz(client):
    assert client.get("/healthz").json == {"status": "ok"}


def test_index(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Emucraft" in response.data


def test_api_upload(client):
    gcode = program(2, 1, "G0 X1.8 Y.5 Z2.\nG1 Z.5 F10").encode()
    response = client.post(
        "/api/simulate?resolution=10",
        data={"file": (io.BytesIO(gcode), "part.nc")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.json["grid"]["nx"] == 200
    assert response.json["grid"]["ny"] == 100


def test_api_raw_body(client):
    gcode = program(2, 1, "G0 X1.8 Y.5 Z2.\nG1 Z.5 F10")
    response = client.post("/api/simulate?resolution=10", data=gcode, content_type="text/plain")
    assert response.status_code == 200


@pytest.mark.parametrize("query, body, error", [
    ("", "", "No G-code provided"),
    ("", "G91 X1", "Unsupported G-code"),
    ("?resolution=abc", "G0 X1", "resolution must be an integer"),
    ("?resolution=0", "G0 X1", "resolution must be at least 1"),
])
def test_api_errors(client, query, body, error):
    response = client.post(f"/api/simulate{query}", data=body, content_type="text/plain")
    assert response.status_code == 400
    assert error in response.json["error"]
