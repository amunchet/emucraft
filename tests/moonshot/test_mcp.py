"""The MCP server end to end, through a real MCP client: chunked upload, a planned run, stats, screenshots
and chunked download, plus the HTTP file routes and their token."""
import asyncio
import base64
import hashlib
import json
import time
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from mcp import Client

import moonshot.server as srv

SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "wave_plate.stl"


async def call(client, name, **args):
    """A tool's JSON result, raising with the server's message on a tool error."""
    result = await client.call_tool(name, args)
    if result.is_error:
        raise RuntimeError(result.content[0].text)
    return result.structured_content["result"] if result.structured_content else json.loads(result.content[0].text)


async def upload(client, blob, filename="plate.stl", chunk=50_000, digest=None):
    started = await call(client, "upload_start", filename=filename, size=len(blob),
                         sha256=digest or hashlib.sha256(blob).hexdigest())
    for offset in range(0, len(blob), chunk):
        await call(client, "upload_chunk", upload_id=started["upload_id"], offset=offset,
                   data=base64.b64encode(blob[offset:offset + chunk]).decode())
    return await call(client, "upload_finish", upload_id=started["upload_id"])


@pytest.fixture(scope="module")
def planned(tmp_path_factory):
    """A coarse run of the wave plate, uploaded in chunks and planned with tracking, waited for."""
    srv.DATA = tmp_path_factory.mktemp("serve")

    async def go():
        async with Client(srv.server) as client:
            model = await upload(client, SAMPLE.read_bytes())
            run = await call(client, "plan", model_id=model["model_id"], spacing=0.05)
            deadline = time.time() + 600
            while (state := await call(client, "status", run_id=run["run_id"]))["tasks"][0]["status"] in ("queued", "running"):
                assert time.time() < deadline
                await asyncio.sleep(0.5)
            return model, state

    return asyncio.run(go())


def test_upload_keeps_the_model_intact(planned):
    model, _ = planned
    blob = SAMPLE.read_bytes()
    assert model["size"] == len(blob) and model["sha256"] == hashlib.sha256(blob).hexdigest()
    assert model["triangles"] > 0 and model["watertight"]
    stored = srv.DATA / "models" / model["model_id"] / "plate.stl"
    assert stored.read_bytes() == blob


def test_bad_uploads_are_refused(planned):
    async def go():
        async with Client(srv.server) as client:
            blob = SAMPLE.read_bytes()
            with pytest.raises(RuntimeError, match="sha256 does not match"):
                await upload(client, blob, digest="0" * 64)
            with pytest.raises(RuntimeError, match="does not load as a mesh"):
                await upload(client, b"not a mesh at all", filename="junk.stl", digest=None)
            with pytest.raises(RuntimeError, match="model files are"):
                await call(client, "upload_start", filename="evil.sh", size=3)
            started = await call(client, "upload_start", filename="gap.stl", size=10)
            with pytest.raises(RuntimeError, match="skips bytes"):
                await call(client, "upload_chunk", upload_id=started["upload_id"], offset=4,
                           data=base64.b64encode(b"abc").decode())

    asyncio.run(go())


def test_plan_runs_and_reports_its_stats(planned):
    _, state = planned
    task = state["tasks"][0]
    assert task["status"] == "done", task.get("traceback")
    assert [line.split(":")[0] for line in task["log"]] == ["step 1", "step 2", "step 3", "step 4"]

    async def go():
        async with Client(srv.server) as client:
            return await call(client, "stats", run_id=state["run_id"])

    stats = asyncio.run(go())
    assert 0 < stats["reachable"] <= stats["points"]
    assert stats["tracking"]["passes"] > 1 and "energy_history" not in stats
    assert stats["grain_tilt_deg"]["mean"] >= 0


def test_files_download_in_chunks_exactly(planned):
    _, state = planned

    async def go():
        async with Client(srv.server) as client:
            files = await call(client, "list_files", run_id=state["run_id"])
            for f in files:
                got, offset = b"", 0
                while True:
                    part = await call(client, "read_file", run_id=state["run_id"], path=f["path"], offset=offset,
                                      length=400_000)
                    got += base64.b64decode(part["data"])
                    offset += part["length"]
                    if part["eof"]:
                        break
                assert hashlib.sha256(got).hexdigest() == f["sha256"], f["path"]
            with pytest.raises(RuntimeError, match="not a file of this run"):
                await call(client, "read_file", run_id=state["run_id"], path="../../models")
            with pytest.raises(RuntimeError, match="not a run id"):
                await call(client, "list_files", run_id="../models")
            return {f["path"] for f in files}

    assert {"orientations.csv", "result.npz", "summary.json", "run.json"} <= asyncio.run(go())


def test_screenshot_shows_and_saves_an_image(planned):
    _, state = planned

    async def go():
        async with Client(srv.server) as client:
            shot = await client.call_tool("screenshot", {"run_id": state["run_id"], "color": "track", "width": 400,
                                                         "height": 300})
            saved = shot.content[1].text.split()[-1]
            again = await client.call_tool("view_image", {"run_id": state["run_id"], "path": saved})
            return shot, again

    shot, again = asyncio.run(go())
    assert not shot.is_error and shot.content[0].mime_type == "image/png"
    assert base64.b64decode(shot.content[0].data)[:8] == b"\x89PNG\r\n\x1a\n"
    assert again.content[0].data == shot.content[0].data


def test_http_routes_need_the_token(planned, monkeypatch):
    from starlette.testclient import TestClient

    _, state = planned
    monkeypatch.setenv("MOONSHOT_TOKEN", "secret")
    with TestClient(srv.guarded(srv.server.streamable_http_app()), base_url="http://127.0.0.1:8000") as http:
        assert http.get("/health").json()["status"] == "ok"
        assert http.get(f"/files/runs/{state['run_id']}/summary.json").status_code == 401
        assert http.post("/mcp", json={}).status_code == 401
        auth = {"Authorization": "Bearer secret"}
        got = http.get(f"/files/runs/{state['run_id']}/summary.json", headers=auth)
        assert got.status_code == 200 and got.json()["params"]["spacing"] == 0.05
        assert http.get(f"/files/runs/{state['run_id']}/..%2F..%2Fmodels", headers=auth).status_code == 404
        put = http.put("/files/models/plate.stl", content=SAMPLE.read_bytes(), headers=auth)
        assert put.status_code == 200 and put.json()["sha256"] == hashlib.sha256(SAMPLE.read_bytes()).hexdigest()
