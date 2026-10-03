"""The self-contained HTML report."""

import base64
import json
import re
from pathlib import Path

from emucraft.check import check_file
from emucraft.cli import main
from emucraft.html import build_report_html

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def embedded(page):
    m = re.search(r"window\.EMUCRAFT_EMBED = (\{.*?\});</script>", page, re.S)
    assert m, "embedded data missing"
    return json.loads(m.group(1))


def test_report_is_self_contained():
    page = build_report_html(check_file(EXAMPLES / "crash_demo.nc"))
    assert "<title>Emucraft FAIL: crash_demo.nc (5 collisions)</title>" in page
    # nothing is fetched: no external scripts, styles or module paths
    assert 'src="js/' not in page and 'href="css/' not in page
    imports = json.loads(re.search(r'<script type="importmap">(.*?)</script>', page, re.S).group(1))["imports"]
    assert set(imports) >= {"three", "three/addons/controls/OrbitControls.js", "emucraft/main.js", "emucraft/kernel.js"}
    assert all(url.startswith("data:text/javascript;base64,") for url in imports.values())
    main_js = base64.b64decode(imports["emucraft/main.js"].split(",", 1)[1]).decode()
    assert "from 'emucraft/kernel.js'" in main_js and "from './" not in main_js
    data = embedded(page)
    assert data["name"] == "crash_demo.nc" and data["text"].startswith("%")
    assert base64.b64decode(data["wasm"])[:4] == b"\0asm"
    assert data["payload"]["report"]["status"] == "fail"


def test_script_breakouts_are_escaped(tmp_path):
    text = "(</script><script>alert(1)</script>)\nG0 X0 Y0 Z1\n"
    path = tmp_path / "evil.nc"
    path.write_text(text)
    page = build_report_html(check_file(path))
    assert "<script>alert(1)" not in page
    assert embedded(page)["text"] == text


def test_cli_writes_html(tmp_path):
    out = tmp_path / "report.html"
    code = main(["check", str(EXAMPLES / "pocket_corners.nc"), "-q", "--html", str(out)])
    assert code == 0
    page = out.read_text()
    assert "EMUCRAFT_EMBED" in page and 500_000 < len(page) < 5_000_000
