"""Self-contained HTML report: the full 3D viewer in one file.

Every script (viewer modules, three.js, the WebAssembly kernel) and the check
result are inlined, so the report opens from a CI artifact, an e-mail or a
shop-floor PC without a server or network access. The viewer replays the
simulation locally from the embedded path.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Union

from .check import CheckResult
from .payload import WEB, analysis_payload

_RELATIVE_IMPORT = re.compile(r"""(from\s+|import\s+)(['"])\./([\w-]+\.js)\2""")


def _data_url(text: str) -> str:
    return "data:text/javascript;base64," + base64.b64encode(text.encode("utf-8")).decode("ascii")


def _script_json(value) -> str:
    """JSON that is safe inside a <script> element."""
    return json.dumps(value, separators=(",", ":")).replace("<", "\\u003c")


def build_report_html(result: CheckResult) -> str:
    program = result.program
    payload = analysis_payload(result)
    imports = {
        "three": _data_url((WEB / "vendor" / "three" / "three.module.min.js").read_text(encoding="utf-8")),
        "three/addons/controls/OrbitControls.js": _data_url(
            (WEB / "vendor" / "three" / "OrbitControls.min.js").read_text(encoding="utf-8")),
    }
    for module in sorted((WEB / "js").glob("*.js")):
        source = _RELATIVE_IMPORT.sub(lambda m: f"{m.group(1)}{m.group(2)}emucraft/{m.group(3)}{m.group(2)}",
                                      module.read_text(encoding="utf-8"))
        imports[f"emucraft/{module.name}"] = _data_url(source)
    embed = {
        "name": program.name,
        "text": program.text,
        "payload": payload,
        "wasm": base64.b64encode((WEB / "wasm" / "emucraft.wasm").read_bytes()).decode("ascii"),
    }
    page = (WEB / "index.html").read_text(encoding="utf-8")
    css = (WEB / "css" / "app.css").read_text(encoding="utf-8")
    page = page.replace('<link rel="stylesheet" href="css/app.css">', f"<style>\n{css}\n</style>")
    page = re.sub(r'<script type="importmap">.*?</script>',
                  lambda _: f'<script type="importmap">{_script_json({"imports": imports})}</script>',
                  page, count=1, flags=re.S)
    page = page.replace(
        '<script type="module" src="js/main.js"></script>',
        f"<script>window.EMUCRAFT_EMBED = {_script_json(embed)};</script>\n"
        '<script type="module">import "emucraft/main.js";</script>')
    status = result.status.upper()
    title = f"Emucraft {status}: {program.name} ({result.summary()})"
    page = page.replace("<title>Emucraft</title>", f"<title>{_escape(title)}</title>")
    return page


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def write_report(result: CheckResult, path: Union[str, Path]) -> Path:
    path = Path(path)
    path.write_text(build_report_html(result), encoding="utf-8")
    return path
