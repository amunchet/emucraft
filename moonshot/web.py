"""Moonshot's web page and the JSON API behind it, on their own server.

    moonshot-web --port 8766 --data moonshot-data     # or python -m moonshot.web; page at /, API under /api

    GET  /api/models                    uploaded models, newest first
    PUT  /api/models/<filename>         upload a model as the raw request body
    GET  /api/samples                   the bundled sample parts
    POST /api/samples/<name>            add a sample part as a model
    GET  /api/runs                      runs, newest first
    POST /api/runs                      {model_id, ...plan options}      -> queued plan
    GET  /api/runs/<id>                 the run's tasks and their logs
    GET  /api/runs/<id>/stats           its numbers (see `store.stats`)
    GET  /api/runs/<id>/scene           mesh, holder, grid points and per-point results for the 3D view
    GET  /api/runs/<id>/program         the robot program's contact path, once written
    POST /api/runs/<id>/program         {robot program options}          -> queued task
    POST /api/runs/<id>/assets          {steps, track, full_gif}         -> queued task
    GET  /api/runs/<id>/files           every file of the run
    GET  /api/files/<id>/<path>         one of them

Arrays travel as base64 of little-endian typed arrays, as in the G-code viewer's API. The page (moonshot/page/)
borrows the G-code viewer's 3D viewer, helpers, stylesheet and three.js from emucraft/web/. There is no
authentication: deployed, put it behind a proxy that has some.
"""
import argparse
import base64
import csv
import gzip
import json
import mimetypes
import shutil
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import numpy as np

from . import fanuc, store
from .store import Refused, folder, inside, loaded

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
STATIC = (Path(__file__).resolve().parent / "page", ROOT / "emucraft" / "web")  # the page first, then shared assets
MAX_BODY = 512 << 20  # largest upload


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
        return 200, store.list_files(run_id, "/api/files")
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


class Handler(BaseHTTPRequestHandler):
    """The page and its assets for GET, and /api/... for everything."""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet: task logs are in each run
        pass

    def send(self, status, body, ctype):
        if len(body) > 1024 and "gzip" in (self.headers.get("Accept-Encoding") or "") and not ctype.startswith("image/"):
            body = gzip.compress(body, compresslevel=5)
            self.send_response(status)
            self.send_header("Content-Encoding", "gzip")
        else:
            self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def answer(self):
        path = unquote(urlparse(self.path).path)
        method = "GET" if self.command == "HEAD" else self.command
        if path.startswith("/api/"):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                if length > MAX_BODY:
                    raise ValueError(f"request too large: at most {MAX_BODY >> 20} MB")
                status, value = handle(method, path[len("/api"):], self.rfile.read(length))
            except (ValueError, TypeError, KeyError) as exc:
                status, value = 400, {"error": str(exc)}
            except Exception as exc:
                traceback.print_exc()
                status, value = 500, {"error": f"{type(exc).__name__}: {exc}"}
            if isinstance(value, Path):
                self.send(status, value.read_bytes(), mimetypes.guess_type(value.name)[0] or "application/octet-stream")
            else:
                self.send(status, json.dumps(finite(value)).encode(), "application/json")
            return
        rel = "index.html" if path == "/" else path.lstrip("/")
        for root in STATIC:
            file = (root / rel).resolve()
            if method == "GET" and root.resolve() in file.parents and file.is_file():
                ctype = {".js": "text/javascript", ".wasm": "application/wasm"}.get(file.suffix) or \
                    mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                self.send(200, file.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))
                return
        self.send(404, b'{"error": "not found"}', "application/json")

    do_GET = do_HEAD = do_POST = do_PUT = answer


def make_server(host="127.0.0.1", port=8766, data="moonshot-data"):
    """The web server, with `data` opened as the store's data directory."""
    store.open_data(data)
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main(argv=None):
    """Command-line entry point."""
    ap = argparse.ArgumentParser(prog="moonshot-web", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1", help="address (default 127.0.0.1; 0.0.0.0 to deploy)")
    ap.add_argument("--port", type=int, default=8766, help="port (default 8766)")
    ap.add_argument("--data", default="moonshot-data", help="data directory (default ./moonshot-data); give the MCP "
                    "server its own")
    args = ap.parse_args(argv)
    server = make_server(args.host, args.port, args.data)
    host = "localhost" if args.host in ("0.0.0.0", "::") else args.host
    print(f"Moonshot on http://{host}:{server.server_address[1]}/  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
