import importlib.util

# Moonshot needs its optional dependencies (pip install -e .[moonshot-dev]); without them only emucraft is tested.
if any(importlib.util.find_spec(m) is None for m in ("numpy", "scipy", "numba", "trimesh")):
    collect_ignore_glob = ["test_*.py"]
