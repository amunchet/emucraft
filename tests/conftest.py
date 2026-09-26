import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from helpers import EXAMPLES  # noqa: E402


@pytest.fixture(scope="session")
def examples() -> Path:
    return EXAMPLES


@pytest.fixture(scope="session")
def makino_text() -> str:
    return (EXAMPLES / "makino_roughing.nc").read_text()
