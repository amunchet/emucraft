import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from helpers import EXAMPLES  # noqa: E402

# Fixtures and simulations never touch the real library, not even from a module-scoped pytest fixture.
os.environ["EMUCRAFT_DATA"] = tempfile.mkdtemp(prefix="emucraft-tests-")


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Every test starts with an empty fixture library."""
    path = tmp_path / "emucraft-data"
    monkeypatch.setenv("EMUCRAFT_DATA", str(path))
    return path


@pytest.fixture(scope="session")
def examples() -> Path:
    return EXAMPLES


@pytest.fixture(scope="session")
def makino_text() -> str:
    return (EXAMPLES / "makino_roughing.nc").read_text()
