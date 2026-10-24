"""Small pure-JavaScript helpers of the viewer, run in Node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UTIL = ROOT / "emucraft" / "web" / "js" / "util.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")

SAMPLES = [
    "", "G0 X1", "G0 X1\n", "a\nb\r\nc\rd", "a\r\r\nb", "a\n\n", "\n", "\r\n\r\n",
    "x\x0by\x0cz\x1cw\x85v u t",
]


def test_split_lines_matches_python():
    """Line numbers in the viewer must match the interpreter's (str.splitlines)."""
    script = (
        f"import {{ splitLines }} from {json.dumps(UTIL.as_uri())};\n"
        f"const samples = {json.dumps(SAMPLES)};\n"
        "console.log(JSON.stringify(samples.map(splitLines)));\n"
    )
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [s.splitlines() for s in SAMPLES]
