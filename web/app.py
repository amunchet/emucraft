#!/usr/bin/env python3
"""
Emucraft web service

Authentication is expected to be handled by the reverse proxy in front of this
service (Caddy), so nothing here is access controlled.
"""
import logging
import os

from flask import Flask, jsonify, request, send_from_directory

from . import simulate

MAX_UPLOAD_MB = float(os.getenv("EMUCRAFT_MAX_UPLOAD_MB", "20"))
TIMEOUT = int(os.getenv("EMUCRAFT_TIMEOUT", "300"))
DEFAULT_RESOLUTION = int(os.getenv("EMUCRAFT_RESOLUTION", "5"))
MAX_CELLS = int(os.getenv("EMUCRAFT_MAX_CELLS", "2000"))

logger = logging.getLogger("emucraft")

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = int(MAX_UPLOAD_MB * 1024 * 1024)


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/healthz")
def healthz():
    return jsonify(status="ok")


@app.post("/api/simulate")
def api_simulate():
    upload = request.files.get("file")
    if upload is not None:
        raw = upload.read()
    elif request.mimetype in ("multipart/form-data", "application/x-www-form-urlencoded"):
        raw = request.form.get("gcode", "").encode()
    else:
        raw = request.get_data()

    gcode = raw.decode("utf-8", errors="replace")
    if not gcode.strip():
        return jsonify(error="No G-code provided"), 400

    try:
        resolution = int(request.args.get("resolution", DEFAULT_RESOLUTION))
    except ValueError:
        return jsonify(error="resolution must be an integer"), 400
    if resolution < 1:
        return jsonify(error="resolution must be at least 1"), 400

    try:
        result = simulate.run_isolated(gcode, timeout=TIMEOUT, resolution=resolution, max_cells=MAX_CELLS)
    except simulate.SimulationError as e:
        return jsonify(error=str(e)), 400
    except TimeoutError as e:
        logger.warning("Simulation timed out: %s", e)
        return jsonify(error=str(e)), 504
    except RuntimeError:
        logger.exception("Simulation failed")
        return jsonify(error="Simulation failed"), 500

    return jsonify(result)


@app.errorhandler(413)
def too_large(_e):
    return jsonify(error=f"Upload exceeds {MAX_UPLOAD_MB:g} MB"), 413
