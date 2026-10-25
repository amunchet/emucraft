"""Moonshot as an MCP server: upload a model, plan it, render its pictures and GIFs, write the robot program,
and download any of it.

    python -m moonshot.server                              # stdio, for a local MCP client
    python -m moonshot.server --http --port 8000           # streamable HTTP at /mcp, plus plain file routes

Everything lives under the data directory (--data, or $MOONSHOT_DATA, default ./serve): uploaded models in
models/<id>/, and each planned run in runs/<id>/ with its results, assets/, fanuc/ and shots/. Planning,
assets and robot programs are long, so their tools only queue the work and return; `status` follows it.
One task runs at a time, since each already uses every CPU thread.

Files move in base64 chunks through the tools (`upload_start` / `upload_chunk` / `upload_finish`,
`list_files` / `read_file`), which works over any transport. Over HTTP the same files also go up with
`PUT /files/models/<filename>` and come down with `GET /files/runs/<run>/<path>`. With $MOONSHOT_TOKEN set,
every HTTP request needs `Authorization: Bearer <token>`.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import numpy as np
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.utilities.types import Image
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from . import fanuc
from .geometry import UNITS, load_mesh
from .pipeline import load, run, save

DATA = Path(os.environ.get("MOONSHOT_DATA", "serve")).resolve()
CHUNK, MAX_CHUNK = 1 << 20, 8 << 20  # bytes of file per read_file / upload_chunk call
INLINE = 8 << 20  # largest image `view_image` returns inline; bigger ones download in chunks
MODELS = (".stl", ".obj", ".ply", ".3mf", ".glb", ".gltf", ".off", ".step", ".stp")
COLORS = ("model", "reach", "grain", "track", "pass")

server = MCPServer("moonshot", instructions=__doc__)
worker = ThreadPoolExecutor(max_workers=1)
lock = threading.Lock()


def new_id():
    return secrets.token_hex(6)


def folder(kind, ident):
    """runs/<id> or models/<id>, refusing anything that is not an id this server made."""
    if not re.fullmatch(r"[0-9a-f]{12}", ident):
        raise ToolError(f"not a {kind[:-1]} id: {ident!r}")
    path = DATA / kind / ident
    if not path.is_dir():
        raise ToolError(f"no such {kind[:-1]}: {ident}")
    return path


def inside(root, rel):
    """`rel` under `root`, refusing paths that climb out of it."""
    path = (root / rel).resolve()
    if root.resolve() not in path.parents:
        raise ToolError(f"not a file of this run: {rel!r}")
    return path


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(path)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def store_model(source, filename):
    """Move an uploaded file into models/<id>/, after checking that it loads as a mesh; returns its record."""
    try:
        mesh = load_mesh(source)
    except Exception as exc:
        source.unlink()
        raise ToolError(f"{filename} does not load as a mesh: {exc}") from exc
    if len(mesh.faces) == 0:
        source.unlink()
        raise ToolError(f"{filename} does not load as a mesh: no triangles")
    ident = new_id()
    home = DATA / "models" / ident
    home.mkdir(parents=True)
    path = home / filename
    shutil.move(source, path)
    record = {"model_id": ident, "filename": filename, "size": path.stat().st_size, "sha256": sha256(path),
              "triangles": len(mesh.faces), "watertight": bool(mesh.is_watertight),
              "bounds_file_units": mesh.bounds.round(4).tolist(), "uploaded": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    write_json(home / "model.json", record)
    return record


def model_name(filename):
    name = Path(filename).name
    if Path(name).suffix.lower() not in MODELS:
        raise ToolError(f"model files are {', '.join(MODELS)}: {filename!r}")
    return name


@server.tool()
def upload_start(filename: str, size: int, sha256: str | None = None) -> dict:
    """Begin uploading a model file (STL recommended; OBJ, PLY, 3MF, GLB, OFF, or STEP with cascadio).

    Then send its bytes in order with `upload_chunk` and close with `upload_finish`. `size` is the
    whole file in bytes; give `sha256` (hex) to have the finished file checked against it.
    """
    ident = new_id()
    home = DATA / "uploads" / ident
    home.mkdir(parents=True)
    name = model_name(filename)
    write_json(home / "upload.json", {"filename": name, "size": size, "sha256": sha256})
    (home / name).touch()  # named as uploaded: the suffix says how to load it
    return {"upload_id": ident, "chunk_size": CHUNK, "max_chunk_size": MAX_CHUNK}


@server.tool()
def upload_chunk(upload_id: str, offset: int, data: str) -> dict:
    """Write base64 `data` at byte `offset` of an upload. Chunks go in order; resending one is harmless."""
    home = folder("uploads", upload_id)
    blob = base64.b64decode(data, validate=True)
    if len(blob) > MAX_CHUNK:
        raise ToolError(f"chunks are at most {MAX_CHUNK} bytes, got {len(blob)}")
    meta = read_json(home / "upload.json")
    path, total = home / meta["filename"], meta["size"]
    have = path.stat().st_size
    if offset > have:
        raise ToolError(f"offset {offset} skips bytes: {have} received so far")
    if offset + len(blob) > total:
        raise ToolError(f"chunk ends at {offset + len(blob)}, past the declared size {total}")
    with open(path, "r+b") as fh:
        fh.seek(offset)
        fh.write(blob)
    return {"received": max(have, offset + len(blob)), "size": total}


@server.tool()
def upload_finish(upload_id: str) -> dict:
    """Close an upload: check its size (and sha256 if given), load it as a mesh and keep it as a model."""
    home = folder("uploads", upload_id)
    meta = read_json(home / "upload.json")
    path = home / meta["filename"]
    if path.stat().st_size != meta["size"]:
        raise ToolError(f"received {path.stat().st_size} of {meta['size']} bytes")
    if meta["sha256"] and sha256(path) != meta["sha256"].lower():
        shutil.rmtree(home)
        raise ToolError("sha256 does not match; the upload was discarded, start again")
    record = store_model(path, meta["filename"])
    shutil.rmtree(home)
    return record


@server.tool()
def list_models() -> list[dict]:
    """Every uploaded model, newest first."""
    found = [read_json(p) for p in (DATA / "models").glob("*/model.json")]
    return sorted(found, key=lambda r: r["uploaded"], reverse=True)


def queue(run_dir, kind, args, work):
    """Record a task on a run and hand it to the worker; `work(log)` does it."""
    with lock:
        meta = read_json(run_dir / "run.json")
        meta["tasks"].append({"kind": kind, "args": args, "status": "queued", "queued": time.time(), "log": []})
        number = len(meta["tasks"]) - 1
        write_json(run_dir / "run.json", meta)

    def update(**change):
        with lock:
            meta = read_json(run_dir / "run.json")
            meta["tasks"][number].update(change)
            write_json(run_dir / "run.json", meta)

    def log(line):
        with lock:
            meta = read_json(run_dir / "run.json")
            meta["tasks"][number]["log"].append(str(line))
            write_json(run_dir / "run.json", meta)

    def task():
        update(status="running", started=time.time())
        try:
            work(log)
            update(status="done", finished=time.time())
        except Exception as exc:
            update(status="failed", finished=time.time(), error=f"{type(exc).__name__}: {exc}",
                   traceback=traceback.format_exc(limit=8))

    worker.submit(task)
    return {"run_id": run_dir.name, "task": number, "kind": kind, "status": "queued"}


@lru_cache(maxsize=2)
def results(run_dir, stamp):
    """A run's saved results with its mesh, kept for the next call; `stamp` changes when they are rewritten."""
    return load(run_dir)


def loaded(run_dir):
    summary = run_dir / "summary.json"
    if not summary.exists():
        raise ToolError(f"run {run_dir.name} has no results yet: see `status`")
    return results(run_dir, summary.stat().st_mtime_ns)


@server.tool()
def plan(model_id: str, units: str = "in", rotate: list[float] = [0.0, 0.0, 0.0], spacing: float = 0.01,
         radius: float = 0.5, length: float = 10.0, tolerance: float = 0.001, bins: int = 180, margin: int = 1,
         holder: str = "fixture", mount: list[float] = [0.0, 0.0, -1.0], inset: float = 0.05,
         grain: list[float] = [0.0, 0.0, 1.0], grain_weight: float = 1e6, prefer: list[float] = [1.0, 0.0, 0.0],
         track: bool | float = True, track_step: float | None = None, pass_spacing: float = 0.1,
         track_gap: float = 0.01, track_sweep: bool = False, track_sweeps: int = 1, track_slope: float = 8.0) -> dict:
    """Queue steps 1-4 on an uploaded model; returns a run id to follow with `status`.

    The options are the command line's (`python -m moonshot --help`): lengths in inches, angles
    in degrees. `holder` is fixture, table or none; `mount` points from the part toward it.
    `grain` [0, 0, 0] turns the grain term off. `track` is true (90% of the drum), a band width
    in inches, or false for no tracking. Takes from seconds to a few minutes (the 1M-triangle
    grip at 0.01 in: about 50 s on 4 threads).
    """
    model_dir = folder("models", model_id)
    if units not in UNITS or holder not in ("fixture", "table", "none"):
        raise ToolError(f"units are {sorted(UNITS)}, holder is fixture, table or none")
    source = model_dir / read_json(model_dir / "model.json")["filename"]
    options = dict(spacing=spacing, radius=radius, length=length, tolerance=tolerance, bins=bins, margin=margin,
                   holder=holder, mount=tuple(mount), inset=inset, grain=tuple(grain) if any(grain) else None,
                   grain_weight=grain_weight, prefer=tuple(prefer), rotate=tuple(rotate), units=units,
                   track=track or None, mode="sweep" if track_sweep else "passes", step=track_step,
                   pass_spacing=pass_spacing, sweeps=track_sweeps, gap=track_gap, slope=track_slope)
    run_dir = DATA / "runs" / new_id()
    run_dir.mkdir(parents=True)
    write_json(run_dir / "run.json", {"run_id": run_dir.name, "model_id": model_id, "model": source.name,
                                      "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tasks": []})
    return queue(run_dir, "plan", options, lambda log: save(run(source, **options, log=log), run_dir))


@server.tool()
def render_assets(run_id: str, steps: bool = True, track: bool = True, full_gif: bool = True) -> dict:
    """Queue the pictures of a planned run into its assets/ folder.

    `steps`: 0_model, 1_grid, 2_reach, 2_probes, 3_field, 3_grain, 3_angles, 3_stats (PNG) and
    4_rollout.gif. `track`: 5_track.png and 5_track.gif (six neighboring passes). `full_gif`:
    5_track_full.gif, every pass of the run. Slow: the full GIF of the grip takes about half an
    hour on 4 threads, the rest about 15 minutes; the samples take a few minutes.
    """
    run_dir = folder("runs", run_id)
    loaded(run_dir)

    def work(log):
        from .assets import asset_images, track_images, tracked  # matplotlib and friends: only when drawing
        res = loaded(run_dir)
        out = run_dir / "assets"
        out.mkdir(exist_ok=True)
        name = Path(res["params"]["source"]).stem
        if steps:
            asset_images(res, name, out)
            log("wrote the step pictures and 4_rollout.gif")
        if track or full_gif:
            res = tracked(res)
        if track:
            track_images(res, name, out)
            log("wrote 5_track.png and 5_track.gif")
        if full_gif:
            track_images(res, name, out, full=True)
            log("wrote 5_track_full.gif")

    return queue(run_dir, "assets", {"steps": steps, "track": track, "full_gif": full_gif}, work)


@server.tool()
def robot_program(run_id: str, frame: list[float] = [0.0, 0.0, 0.0], name: str = "SAND", setup: str = "part",
                  rtcp: bool = False, uframe: int = 1, utool: int = 1, config: str = "N U T, 0, 0, 0",
                  feed: float = 120.0, rapid: float = 240.0, max_turn: float = 90.0, swing: float = 480.0,
                  lift: float = 0.25, approach: float = 2.0, tol: float = 0.001, angle_tol: float = 0.5,
                  max_step: float = 0.25) -> dict:
    """Queue the FANUC program for a planned (tracked) run into its fanuc/ folder: <name>.ls, program.csv,
    program.json. Options are `python -m moonshot.fanuc`'s: `frame` is the part frame in the model file's
    coordinates (X Y Z, X Y Z W P R, or three points), `feed` and `rapid` in inches per minute."""
    run_dir = folder("runs", run_id)
    if setup not in ("part", "drum") or (rtcp and setup == "drum"):
        raise ToolError("setup is part or drum, and rtcp is only for a robot holding the part")
    args = {k: v for k, v in locals().items() if k not in ("run_id", "run_dir")}

    def work(log):
        res = loaded(run_dir)
        params = res["params"]
        part = fanuc.frame(frame, params["units"], params["rotate"])
        program = fanuc.build(res, part, "drum" if setup == "drum" else "rtcp" if rtcp else "part", lift, approach,
                              0.1, feed / 60, rapid / 60, max_turn, tol, angle_tol, max_step, swing=swing, log=log)
        extra = {"frame": frame, "model_units": params["units"], "model_rotate": params["rotate"],
                 "feed_in_min": feed, "rapid_in_min": rapid, "max_turn_deg_s": max_turn, "swing_deg_in": swing}
        path = fanuc.write(program, part, run_dir / "fanuc", name, setup, rtcp, uframe, utool, config, extra)
        log(f"wrote fanuc/{path.name}, program.csv, program.json")

    return queue(run_dir, "fanuc", args, work)


@server.tool()
def status(run_id: str) -> dict:
    """A run's tasks (plan, assets, fanuc): queued, running, done or failed, with their progress log."""
    return read_json(folder("runs", run_id) / "run.json")


@server.tool()
def list_runs() -> list[dict]:
    """Every run, newest first, with the state of its last task."""
    found = []
    for path in (DATA / "runs").glob("*/run.json"):
        meta = read_json(path)
        last = meta["tasks"][-1] if meta["tasks"] else {}
        found.append({k: meta[k] for k in ("run_id", "model_id", "model", "created")} |
                     {"last_task": last.get("kind"), "last_status": last.get("status")})
    return sorted(found, key=lambda r: r["created"], reverse=True)


@server.tool()
def stats(run_id: str) -> dict:
    """A run's numbers: parameters, timings, reachability, neighbor turn, grain tilt, tracking and drum
    wear, and the robot program's (cycle time, points, lifts) once written."""
    run_dir = folder("runs", run_id)
    summary = read_json(run_dir / "summary.json") if (run_dir / "summary.json").exists() else None
    if summary is None:
        raise ToolError(f"run {run_id} has no results yet: see `status`")
    summary.pop("energy_history")
    summary["reachable_pct"] = round(100 * summary["reachable"] / max(summary["points"], 1), 2)
    with np.load(run_dir / "result.npz") as data:
        tilt = data["grain_deg"][np.isfinite(data["grain_deg"])]
    if tilt.size:
        summary["grain_tilt_deg"] = {"mean": float(tilt.mean()), "p95": float(np.percentile(tilt, 95)),
                                     "over_5_pct": float(100 * (tilt > 5).mean())}
    if (run_dir / "fanuc" / "program.json").exists():
        program = read_json(run_dir / "fanuc" / "program.json")
        summary["program"] = {k: program[k] for k in ("program", "setup", "rtcp", "lines", "stats")}
    return summary


def file_url(run_id, rel):
    base = os.environ.get("MOONSHOT_URL")
    return f"{base.rstrip('/')}/files/runs/{run_id}/{rel}" if base else None


@server.tool()
def list_files(run_id: str) -> list[dict]:
    """Every file a run has written, with its size, sha256, and a download URL when the server has one."""
    run_dir = folder("runs", run_id)
    found = []
    for path in sorted(p for p in run_dir.rglob("*") if p.is_file() and p.suffix != ".tmp"):
        rel = path.relative_to(run_dir).as_posix()
        found.append({"path": rel, "size": path.stat().st_size, "sha256": sha256(path)} |
                     ({"url": file_url(run_id, rel)} if file_url(run_id, rel) else {}))
    return found


@server.tool()
def read_file(run_id: str, path: str, offset: int = 0, length: int = CHUNK) -> dict:
    """Bytes `offset` to `offset + length` of a run's file, base64. Read on until `eof`; check the whole
    file against `sha256` from `list_files`."""
    file = inside(folder("runs", run_id), path)
    size = file.stat().st_size
    with open(file, "rb") as fh:
        fh.seek(offset)
        blob = fh.read(min(length, MAX_CHUNK))
    return {"path": path, "offset": offset, "length": len(blob), "size": size, "eof": offset + len(blob) >= size,
            "data": base64.b64encode(blob).decode()}


@server.tool(structured_output=False)
def view_image(run_id: str, path: str) -> Image:
    """A run's PNG or GIF, shown inline (at most 8 MB; download bigger ones with `read_file`)."""
    file = inside(folder("runs", run_id), path)
    if file.suffix.lower() not in (".png", ".gif", ".jpg", ".jpeg", ".webp"):
        raise ToolError(f"not an image: {path}")
    if file.stat().st_size > INLINE:
        raise ToolError(f"{path} is {file.stat().st_size / 1e6:.1f} MB: download it with read_file or its URL")
    return Image(path=file)


@server.tool(structured_output=False)
def screenshot(run_id: str, color: str = "reach", eye: list[float] | None = None, target: list[float] | None = None,
               width: int = 1200, height: int = 800) -> list:
    """Render one view of a planned run now and show it; also saved as shots/<color>-<time>.png.

    `color`: model (the part and its holder), reach (share of collision-free axis angles; red is
    unreachable because of the part, gray because of the holder), grain (tilt toward the grain, 0-30
    degrees), track (contact position along the drum) or pass (tracking passes). `eye` and `target`
    (model coordinates, inches, after the run's rotation) move the camera from its overview.
    """
    from matplotlib.colors import to_rgb
    from .assets import (CRITICAL, DIV, HAIR, MUTED, PART, SEQ, colors, dots, holder_layers, overview_camera,
                         unreachable_by_holder)
    from .render import camera, hex_rgb, mesh_layer, render

    if color not in COLORS:
        raise ToolError(f"color is one of {COLORS}")
    run_dir = folder("runs", run_id)
    res = loaded(run_dir)
    p, n, h = res["points"], res["normals"], res["params"]["spacing"]
    cam = overview_camera(res, Path(res["params"]["source"]).stem)
    if eye is not None or target is not None:
        mid = res["mesh"].bounds.mean(axis=0)
        cam = camera(np.array(eye) if eye is not None else cam["eye"], np.array(target) if target is not None else mid,
                     fov=30)
    cols, ok = np.tile(to_rgb(HAIR), (len(p), 1)), res["feasible"].any(axis=1)
    if color == "reach":
        cols = colors(SEQ, res["feasible"].mean(axis=1), 0, 1)
        cols[~ok] = to_rgb(CRITICAL)
        cols[unreachable_by_holder(res)] = to_rgb(MUTED)
    elif color == "grain":
        cols[ok] = colors(SEQ, res["grain_deg"][ok], 0, 30)
    elif color in ("track", "pass"):
        if "pass" not in res:
            raise ToolError("this run was planned without tracking")
        on = np.isfinite(res["track"])
        band = res["track_stats"]["band"]
        cols[on] = (colors(DIV, res["track"][on], -band / 2, band / 2) if color == "track" else
                    colors(SEQ, res["pass"][on] % 8, 0, 7))
    points = [] if color == "model" else [dots(p, n, cols, 0.75 * h)]
    img = render(cam, (width, height), meshes=[mesh_layer(res["mesh"], hex_rgb(PART)), *holder_layers(res)],
                 points=points)
    shots = run_dir / "shots"
    shots.mkdir(exist_ok=True)
    name = f"{color}-{time.strftime('%Y%m%d-%H%M%S')}.png"
    img.save(shots / name)
    return [Image(path=shots / name), f"saved shots/{name}"]


def authorized(request):
    token = os.environ.get("MOONSHOT_TOKEN")
    return not token or secrets.compare_digest(request.headers.get("authorization", ""), f"Bearer {token}")


@server.custom_route("/health", methods=["GET"])
async def health(request: Request) -> Response:
    """Liveness, and how many tasks are waiting."""
    return JSONResponse({"status": "ok", "queued": worker._work_queue.qsize()})


@server.custom_route("/files/models/{filename}", methods=["PUT"])
async def put_model(request: Request) -> Response:
    """Upload a model as the raw request body; answers with the model's record."""
    if not authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    import anyio
    filename = model_name(request.path_params["filename"])
    home = DATA / "uploads" / new_id()
    home.mkdir(parents=True)
    path = home / filename
    with open(path, "wb") as fh:
        async for block in request.stream():
            fh.write(block)
    try:
        record = await anyio.to_thread.run_sync(store_model, path, filename)
    except ToolError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    finally:
        shutil.rmtree(home)
    return JSONResponse(record)


@server.custom_route("/files/runs/{run_id}/{path:path}", methods=["GET"])
async def get_file(request: Request) -> Response:
    """Download any file of a run (range requests work)."""
    if not authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        file = inside(folder("runs", request.path_params["run_id"]), request.path_params["path"])
    except ToolError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    if not file.is_file():
        return JSONResponse({"error": "no such file"}, status_code=404)
    return FileResponse(file)


def guarded(app):
    """The HTTP app behind the bearer token, when one is set (the file routes check it themselves)."""
    token = os.environ.get("MOONSHOT_TOKEN")

    async def wrapper(scope, receive, send):
        if token and scope["type"] == "http" and scope["path"].startswith("/mcp"):
            headers = dict(scope["headers"])
            if not secrets.compare_digest(headers.get(b"authorization", b"").decode(), f"Bearer {token}"):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await app(scope, receive, send)

    return wrapper


def main(argv=None):
    """Command-line entry point."""
    global DATA
    ap = argparse.ArgumentParser(prog="python -m moonshot.server", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(DATA), help="data directory (default $MOONSHOT_DATA or ./serve)")
    ap.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    ap.add_argument("--host", default="127.0.0.1", help="HTTP address (default 127.0.0.1; 0.0.0.0 to deploy)")
    ap.add_argument("--port", type=int, default=8000, help="HTTP port (default 8000)")
    args = ap.parse_args(argv)
    DATA = Path(args.data).resolve()
    for kind in ("models", "runs", "uploads"):
        (DATA / kind).mkdir(parents=True, exist_ok=True)
    for path in (DATA / "runs").glob("*/run.json"):  # a restart drops whatever was queued or running
        meta = read_json(path)
        for task in meta["tasks"]:
            if task["status"] in ("queued", "running"):
                task.update(status="failed", error="the server restarted before it finished")
        write_json(path, meta)
    if not args.http:
        server.run()
        return
    import uvicorn
    # On localhost the SDK guards against DNS rebinding; deployed, the token is the guard.
    app = server.streamable_http_app(host=args.host, max_request_body_size=MAX_CHUNK * 2)
    uvicorn.run(guarded(app), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
