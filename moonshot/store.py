"""Moonshot's job store, shared by its two front ends: the MCP server (`moonshot.server`) and the web page
(`emucraft serve`, at /moonshot/).

Everything lives under the data directory: uploaded models in models/<id>/ (the file and model.json), and each
planned run in runs/<id>/ with run.json (its tasks and their logs), the results, assets/, fanuc/ and shots/.
Planning, assets and robot programs are long, so they are queued and return at once. One task runs at a time,
since each already uses every CPU thread. Give each running server its own data directory: the queue and its
lock live in the process.
"""
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

from . import fanuc
from .geometry import UNITS, load_mesh
from .pipeline import load, run, save

DATA = Path(os.environ.get("MOONSHOT_DATA", "serve")).resolve()
MODELS = (".stl", ".obj", ".ply", ".3mf", ".glb", ".gltf", ".off", ".step", ".stp")

worker = ThreadPoolExecutor(max_workers=1)
lock = threading.Lock()


class Refused(ValueError):
    """A request the store turns down; the message is meant for whoever made it."""


def open_data(path):
    """Use `path` as the data directory; tasks a previous process left queued or running are marked failed."""
    global DATA
    DATA = Path(path).resolve()
    for kind in ("models", "runs", "uploads"):
        (DATA / kind).mkdir(parents=True, exist_ok=True)
    for meta_path in (DATA / "runs").glob("*/run.json"):
        meta = read_json(meta_path)
        for task in meta["tasks"]:
            if task["status"] in ("queued", "running"):
                task.update(status="failed", error="the server restarted before it finished")
        write_json(meta_path, meta)


def new_id():
    return secrets.token_hex(6)


def folder(kind, ident):
    """runs/<id> or models/<id>, refusing anything that is not an id this store made."""
    if not re.fullmatch(r"[0-9a-f]{12}", ident):
        raise Refused(f"not a {kind[:-1]} id: {ident!r}")
    path = DATA / kind / ident
    if not path.is_dir():
        raise Refused(f"no such {kind[:-1]}: {ident}")
    return path


def inside(root, rel):
    """`rel` under `root`, refusing paths that climb out of it."""
    path = (root / rel).resolve()
    if root.resolve() not in path.parents:
        raise Refused(f"not a file of this run: {rel!r}")
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


def model_name(filename):
    name = Path(filename).name
    if Path(name).suffix.lower() not in MODELS:
        raise Refused(f"model files are {', '.join(MODELS)}: {filename!r}")
    return name


def store_model(source, filename):
    """Move an uploaded file into models/<id>/, after checking that it loads as a mesh; returns its record."""
    try:
        mesh = load_mesh(source)
    except Exception as exc:
        source.unlink()
        raise Refused(f"{filename} does not load as a mesh: {exc}") from exc
    if len(mesh.faces) == 0:
        source.unlink()
        raise Refused(f"{filename} does not load as a mesh: no triangles")
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


def list_models():
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
        raise Refused(f"run {run_dir.name} has no results yet: see its status")
    return results(run_dir, summary.stat().st_mtime_ns)


def plan(model_id, units="in", rotate=(0.0, 0.0, 0.0), spacing=0.01, radius=0.5, length=10.0, tolerance=0.001,
         bins=180, margin=1, holder="fixture", mount=(0.0, 0.0, -1.0), inset=0.05, grain=(0.0, 0.0, 1.0),
         grain_weight=1e6, prefer=(1.0, 0.0, 0.0), track=True, track_step=None, pass_spacing=0.1, track_gap=0.01,
         track_sweep=False, track_sweeps=1, track_slope=8.0):
    """Queue steps 1-4 on an uploaded model in a new run, with the command line's options."""
    model_dir = folder("models", model_id)
    if units not in UNITS or holder not in ("fixture", "table", "none"):
        raise Refused(f"units are {sorted(UNITS)}, holder is fixture, table or none")
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


def render_assets(run_id, steps=True, track=True, full_gif=True):
    """Queue the pictures of a planned run into its assets/ folder."""
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


def robot_program(run_id, frame=(0.0, 0.0, 0.0), name="SAND", setup="part", rtcp=False, uframe=1, utool=1,
                  config="N U T, 0, 0, 0", feed=120.0, rapid=240.0, max_turn=90.0, swing=480.0, lift=0.25,
                  approach=2.0, tol=0.001, angle_tol=0.5, max_step=0.25):
    """Queue the FANUC program for a planned (tracked) run into its fanuc/ folder, with `python -m moonshot.fanuc`'s
    options."""
    run_dir = folder("runs", run_id)
    if setup not in ("part", "drum") or (rtcp and setup == "drum"):
        raise Refused("setup is part or drum, and rtcp is only for a robot holding the part")
    args = {k: v for k, v in locals().items() if k not in ("run_id", "run_dir")}

    def work(log):
        res = loaded(run_dir)
        params = res["params"]
        part = fanuc.frame(list(frame), params["units"], params["rotate"])
        program = fanuc.build(res, part, "drum" if setup == "drum" else "rtcp" if rtcp else "part", lift, approach,
                              0.1, feed / 60, rapid / 60, max_turn, tol, angle_tol, max_step, swing=swing, log=log)
        extra = {"frame": list(frame), "model_units": params["units"], "model_rotate": params["rotate"],
                 "feed_in_min": feed, "rapid_in_min": rapid, "max_turn_deg_s": max_turn, "swing_deg_in": swing}
        path = fanuc.write(program, part, run_dir / "fanuc", name, setup, rtcp, uframe, utool, config, extra)
        log(f"wrote fanuc/{path.name}, program.csv, program.json")

    return queue(run_dir, "fanuc", args, work)


def status(run_id):
    """A run's tasks (plan, assets, fanuc): queued, running, done or failed, with their progress log."""
    return read_json(folder("runs", run_id) / "run.json")


def list_runs():
    """Every run, newest first, with the state of its last task."""
    found = []
    for path in (DATA / "runs").glob("*/run.json"):
        meta = read_json(path)
        last = meta["tasks"][-1] if meta["tasks"] else {}
        found.append({k: meta[k] for k in ("run_id", "model_id", "model", "created")} |
                     {"last_task": last.get("kind"), "last_status": last.get("status")})
    return sorted(found, key=lambda r: r["created"], reverse=True)


def stats(run_id):
    """A run's numbers: parameters, timings, reachability, neighbor turn, grain tilt, tracking and drum wear, and the
    robot program's (cycle time, points, lifts) once written."""
    run_dir = folder("runs", run_id)
    if not (run_dir / "summary.json").exists():
        raise Refused(f"run {run_id} has no results yet: see its status")
    summary = read_json(run_dir / "summary.json")
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


def list_files(run_id, base_url=None):
    """Every file a run has written, with its size and sha256, and its download URL under `base_url` if given."""
    run_dir = folder("runs", run_id)
    found = []
    for path in sorted(p for p in run_dir.rglob("*") if p.is_file() and p.suffix != ".tmp"):
        rel = path.relative_to(run_dir).as_posix()
        found.append({"path": rel, "size": path.stat().st_size, "sha256": sha256(path)} |
                     ({"url": f"{base_url.rstrip('/')}/{run_id}/{rel}"} if base_url else {}))
    return found
