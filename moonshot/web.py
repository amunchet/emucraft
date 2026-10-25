"""The JSON API behind Moonshot's web page, which `emucraft serve` mounts at /api/moonshot (the page is /moonshot/).

    GET  /models                    uploaded models, newest first
    PUT  /models/<filename>         upload a model as the raw request body
    GET  /samples                   the bundled sample parts
    POST /samples/<name>            add a sample part as a model
    GET  /runs                      runs, newest first
    POST /runs                      {model_id, ...plan options}      -> queued plan
    GET  /runs/<id>                 the run's tasks and their logs
    GET  /runs/<id>/stats           its numbers (see `store.stats`)
    GET  /runs/<id>/scene           mesh, holder, grid points and per-point results for the 3D view
    GET  /runs/<id>/program         the robot program's contact path, once written
    POST /runs/<id>/program         {robot program options}          -> queued task
    POST /runs/<id>/assets          {steps, track, full_gif}         -> queued task
    GET  /runs/<id>/files           every file of the run
    GET  /files/<id>/<path>         one of them

Arrays travel as base64 of little-endian typed arrays, as in the G-code viewer's API; pass other answers
through `finite` before writing them as JSON.
"""
import base64
import csv
import json
import shutil
from pathlib import Path

import numpy as np

from . import fanuc, store
from .store import Refused, folder, inside, loaded

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def b64(values, dtype):
    return base64.b64encode(np.ascontiguousarray(values, dtype=dtype).tobytes()).decode()


def handle(method, path, body=b""):
    """Answer one request: (HTTP status, a JSON-ready value, or a Path to send as a file)."""
    parts = [p for p in path.split("/") if p]
    data = json.loads(body or b"{}") if method == "POST" else {}
    if parts == ["models"] and method == "GET":
        return 200, store.list_models()
    if len(parts) == 2 and parts[0] == "models" and method == "PUT":
        name = store.model_name(parts[1])
        home = store.DATA / "uploads" / store.new_id()
        home.mkdir(parents=True)
        try:
            (home / name).write_bytes(body)
            return 200, store.store_model(home / name, name)
        finally:
            shutil.rmtree(home)
    if parts == ["samples"] and method == "GET":
        return 200, sorted(p.stem for p in SAMPLES.glob("*.stl"))
    if len(parts) == 2 and parts[0] == "samples" and method == "POST":
        if parts[1] not in [p.stem for p in SAMPLES.glob("*.stl")]:
            raise Refused(f"no such sample: {parts[1]!r}")
        source = SAMPLES / f"{parts[1]}.stl"
        home = store.DATA / "uploads" / store.new_id()
        home.mkdir(parents=True)
        try:
            shutil.copy(source, home / source.name)
            return 200, store.store_model(home / source.name, source.name)
        finally:
            shutil.rmtree(home)
    if parts == ["runs"] and method == "GET":
        return 200, store.list_runs()
    if parts == ["runs"] and method == "POST":
        return 200, store.plan(**data)
    if len(parts) > 2 and parts[0] == "files" and method == "GET":
        file = inside(folder("runs", parts[1]), "/".join(parts[2:]))
        if not file.is_file():
            return 404, {"error": "no such file"}
        return 200, file
    if len(parts) < 2 or parts[0] != "runs":
        return 404, {"error": "unknown endpoint"}
    run_id, rest = parts[1], parts[2:]
    if rest == [] and method == "GET":
        return 200, store.status(run_id)
    if rest == ["stats"] and method == "GET":
        return 200, store.stats(run_id)
    if rest == ["scene"] and method == "GET":
        return 200, scene(run_id)
    if rest == ["program"] and method == "GET":
        return 200, program(run_id)
    if rest == ["program"] and method == "POST":
        return 200, store.robot_program(run_id, **data)
    if rest == ["assets"] and method == "POST":
        return 200, store.render_assets(run_id, **data)
    if rest == ["files"] and method == "GET":
        return 200, store.list_files(run_id, "/api/moonshot/files")
    return 404, {"error": "unknown endpoint"}


def finite(value):
    """`value` with every NaN and infinity as None, which JSON (and so the browser) can read."""
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite(v) for v in value]
    return value


def scene(run_id):
    """What the 3D view draws: the part's mesh and holder, and every grid point with its results."""
    from .assets import holder_layers, unreachable_by_holder  # matplotlib: only once a run is opened

    res = loaded(folder("runs", run_id))
    mesh, params = res["mesh"], res["params"]
    ok = res["feasible"].any(axis=1)
    reach = np.where(ok, 0, np.where(unreachable_by_holder(res), 2, 1))  # 0 reachable, 1 the part, 2 the holder
    holder = [layer["tri"].reshape(-1, 3) for layer in holder_layers(res)]
    track = res["track_stats"]
    return {"run_id": run_id, "model": Path(params["source"]).name, "params": params, "points": len(ok),
            "band": track["band"] if track else None, "passes": int(res["pass"].max()) + 1,
            "vertices": b64(mesh.vertices, "<f4"), "faces": b64(mesh.faces, "<u4"),
            "holder": b64(np.vstack(holder) if holder else np.zeros((0, 3)), "<f4"),
            "point": b64(res["points"], "<f4"), "normal": b64(res["normals"], "<f4"),
            "axis": b64(np.nan_to_num(res["axis"]), "<f4"), "center": b64(res["center"], "<f4"),
            "reach": b64(reach, "u1"), "feasible_pct": b64(100 * res["feasible"].mean(axis=1), "<f4"),
            "grain_deg": b64(res["grain_deg"], "<f4"), "track": b64(res["track"], "<f4"),
            "pass": b64(res["pass"], "<i4"), "change_deg": b64(res["change"], "<f4"),
            "euler": b64(res["euler"], "<f4")}


def program(run_id):
    """The robot program's points in the run's (planning) coordinates: where each one touches, and how."""
    run_dir = folder("runs", run_id)
    if not (run_dir / "fanuc" / "program.json").exists():
        raise Refused(f"run {run_id} has no robot program yet")
    meta = store.read_json(run_dir / "fanuc" / "program.json")
    part = fanuc.frame(meta["frame"], meta["model_units"], meta["model_rotate"])
    with open(run_dir / "fanuc" / "program.csv") as fh:
        rows = list(csv.DictReader(fh))
    num = lambda keys: np.array([[float(r[k]) if r[k] else np.nan for k in keys] for r in rows]).reshape(len(rows), -1)
    turn, kinds = part[:3, :3], sorted({r["kind"] for r in rows})
    contact = num(["contact_x", "contact_y", "contact_z"]) @ turn.T + part[:3, 3]
    return {"program": meta["program"], "stats": meta["stats"], "kinds": kinds, "count": len(rows),
            "contact": b64(contact, "<f4"), "normal": b64(num(["normal_x", "normal_y", "normal_z"]) @ turn.T, "<f4"),
            "axis": b64(num(["axis_x", "axis_y", "axis_z"]) @ turn.T, "<f4"), "track": b64(num(["track"]), "<f4"),
            "time_ms": b64(num(["time_ms"]), "<f4"), "pass": b64(num(["pass"]), "<f4"),
            "line": b64(num(["line"]), "<f4"), "kind": b64([kinds.index(r["kind"]) for r in rows], "u1")}
