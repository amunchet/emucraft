"""Local web server: the 3D viewer plus a small JSON API.

    GET  /                        viewer
    GET  /api/info                version, optimizer presets
    GET  /api/programs            programs in the served folder (and examples)
    GET  /api/programs/<name>     program text
    POST /api/analyze             {name, text, settings}          -> report + path
    POST /api/optimize            {name, text, settings, options} -> new program

Lengths in ``settings`` are in program units. Binary arrays travel as base64
of little-endian typed arrays so the browser can hand them to its
WebAssembly copy of the kernel without conversion.
"""

from __future__ import annotations

import gzip
import json
import math
import mimetypes
import sys
import threading
import time
import traceback
import webbrowser
from array import array
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

from . import __version__
from .check import check_text, parse_program
from .config import ConfigError, find_config, load_config
from .kernel import KernelError
from .model import Config, StockSpec
from .optimize import MATERIALS, OptimizeSpec, optimize
from .payload import WEB, analysis_payload, b64

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
PROGRAM_SUFFIXES = {".nc", ".ngc", ".gcode", ".tap", ".cnc", ".txt", ".mpf", ".eia", ".min", ".h"}
MAX_BODY = 256 * 1024 * 1024


def _num(value, what: str) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number") from None
    if not math.isfinite(out):
        raise ValueError(f"{what} must be finite")
    return out


def apply_settings(config: Config, settings: Optional[dict]):
    """Browser settings -> (config, run_check keyword arguments)."""
    settings = settings or {}
    config = config.copy()
    kwargs: Dict[str, object] = {}
    rapid_mode = settings.get("rapid_mode")
    if rapid_mode:
        if rapid_mode not in ("linear", "dogleg"):
            raise ValueError("rapid_mode must be 'linear' or 'dogleg'")
        config.machine.rapid_mode = rapid_mode
    stock = settings.get("stock")
    if stock:
        if len(stock) != 6:
            raise ValueError("stock needs [xmin, ymin, zmin, xmax, ymax, zmax]")
        values = [_num(v, "stock") for v in stock]
        spec = StockSpec(*values, source="viewer")
        if not spec.valid():
            raise ValueError("the stock box is empty")
        kwargs["stock"] = spec
    for key in ("resolution", "table_z", "link_feed"):
        value = _num(settings.get(key), key)
        if value is not None:
            kwargs["cell" if key == "resolution" else key] = value
    overrides: Dict[object, Dict[str, object]] = {}
    for key, values in (settings.get("tools") or {}).items():
        number: object = "*" if key == "*" else int(str(key).lstrip("Tt"))
        clean = {}
        for field, value in (values or {}).items():
            if value is None or value == "":
                continue
            clean[field] = value if field in ("shape", "kind", "name") else _num(value, field)
        overrides[number] = clean
    if overrides:
        kwargs["overrides"] = overrides
    return config, kwargs


class EmucraftServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, programs: Optional[Path] = None, config: Optional[Config] = None):
        super().__init__(address, Handler)
        self.programs = programs
        self.config = config

    def program_dirs(self) -> List[Tuple[str, Path]]:
        dirs = []
        if self.programs:
            dirs.append(("programs", self.programs))
        if EXAMPLES.is_dir() and (not self.programs or EXAMPLES.resolve() != self.programs.resolve()):
            dirs.append(("examples", EXAMPLES))
        return dirs

    def find_program(self, name: str) -> Optional[Path]:
        for _, folder in self.program_dirs():
            root = folder.resolve()
            candidate = (root / name).resolve()
            if candidate.is_file() and root in candidate.parents:
                return candidate
        return None

    def config_for(self, path: Optional[Path]) -> Config:
        if self.config is not None:
            return self.config
        if path is not None:
            found = find_config(path)
            if found:
                return load_config(found)
        if self.programs:
            found = find_config(self.programs / "_")
            if found:
                return load_config(found)
        return Config()


class Handler(BaseHTTPRequestHandler):
    server: EmucraftServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter than the default
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    # ------------------------------------------------------------ helpers

    def _send(self, status: int, body: bytes, content_type: str, cache: bool = False) -> None:
        if len(body) > 1024 and "gzip" in (self.headers.get("Accept-Encoding") or "") and \
                not content_type.startswith(("image/", "application/wasm")):
            body = gzip.compress(body, compresslevel=5)
            self.send_response(status)
            self.send_header("Content-Encoding", "gzip")
        else:
            self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("request too large")
        raw = self.rfile.read(length) if length else b"{}"
        if self.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return data

    # ------------------------------------------------------------ routes

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/info":
                self._json(200, {"version": __version__, "materials": MATERIALS,
                                 "optimize_defaults": asdict(OptimizeSpec())})
            elif path == "/api/programs":
                self._json(200, self._list_programs())
            elif path.startswith("/api/programs/"):
                found = self.server.find_program(path[len("/api/programs/"):])
                if not found:
                    self._error(404, "no such program")
                else:
                    self._send(200, found.read_bytes(), "text/plain; charset=utf-8")
            elif path.startswith("/api/"):
                self._error(404, "unknown endpoint")
            else:
                self._static(path)
        except Exception as exc:  # pragma: no cover - defensive
            traceback.print_exc()
            self._error(500, str(exc))

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            data = self._body()
            if path == "/api/analyze":
                self._json(200, self._analyze(data))
            elif path == "/api/optimize":
                self._json(200, self._optimize(data))
            else:
                self._error(404, "unknown endpoint")
        except (ValueError, ConfigError, KeyError, TypeError, KernelError) as exc:
            self._error(400, str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            traceback.print_exc()
            self._error(500, f"{type(exc).__name__}: {exc}")

    def _list_programs(self) -> List[dict]:
        out = []
        for source, folder in self.server.program_dirs():
            for f in sorted(folder.iterdir()):
                if f.is_file() and f.suffix.lower() in PROGRAM_SUFFIXES:
                    out.append({"name": f.name, "size": f.stat().st_size, "source": source})
        return out

    def _static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        root = WEB.resolve()
        target = (root / path.lstrip("/")).resolve()
        if root not in target.parents or not target.is_file():
            self._error(404, "not found")
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".js":
            ctype = "text/javascript; charset=utf-8"
        elif target.suffix == ".wasm":
            ctype = "application/wasm"
        elif target.suffix in (".html", ".css"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype, cache="/vendor/" in path)

    def _source(self, data: dict) -> Tuple[str, str, Config]:
        name = str(data.get("name") or "program.nc")
        text = data.get("text")
        path = self.server.find_program(name) if data.get("sample") else None
        if text is None:
            if path is None:
                raise ValueError("no program text")
            text = path.read_text(encoding="utf-8", errors="replace")
        # e.g. an optimized copy of an example: use the original's config file
        config_path = path
        if config_path is None and data.get("configOf"):
            config_path = self.server.find_program(str(data["configOf"]))
        return name, text, self.server.config_for(config_path)

    def _analyze(self, data: dict) -> dict:
        name, text, config = self._source(data)
        config, kwargs = apply_settings(config, data.get("settings"))
        t0 = time.perf_counter()
        result = check_text(text, name, config, **kwargs)
        payload = analysis_payload(result)
        payload["server_seconds"] = time.perf_counter() - t0
        return payload

    def _optimize(self, data: dict) -> dict:
        name, text, config = self._source(data)
        config, kwargs = apply_settings(config, data.get("settings"))
        options = dict(config.optimize)
        options.update({k: v for k, v in (data.get("options") or {}).items() if v is not None and v != ""})
        spec = OptimizeSpec.from_dict(options)
        program = parse_program(text, name, config)
        result = optimize(program, config, spec, overrides=kwargs.get("overrides"), stock=kwargs.get("stock"))
        payload = result.to_dict(include_text=True)
        payload["move_feed"] = b64(array("f", result.move_feed))
        payload["move_load"] = b64(array("f", result.move_load))
        payload["name"] = _optimized_name(name)
        return payload


def _optimized_name(name: str) -> str:
    stem, dot, ext = name.rpartition(".")
    return f"{stem}.opt.{ext}" if dot else f"{name}.opt"


def make_server(host: str = "127.0.0.1", port: int = 8765, programs=None,
                config: Optional[Config] = None) -> EmucraftServer:
    folder = Path(programs).resolve() if programs else None
    if folder is not None and not folder.is_dir():
        raise ValueError(f"{programs} is not a folder")
    return EmucraftServer((host, port), folder, config)


def serve(host: str = "127.0.0.1", port: int = 8765, programs=None, config: Optional[Config] = None,
          open_browser: bool = False) -> int:
    try:
        server = make_server(host, port, programs, config)
    except (OSError, ValueError) as exc:
        print(f"emucraft: {exc}", file=sys.stderr)
        return 2
    url = f"http://{host if host not in ('0.0.0.0', '::') else 'localhost'}:{server.server_address[1]}/"
    print(f"Emucraft viewer on {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
