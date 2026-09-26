"""Builds the C kernel into the package so installs work without a compiler at
run time. Everything else lives in pyproject.toml."""

import shutil
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.dist import Distribution

ROOT = Path(__file__).resolve().parent
KERNEL = ROOT / "kernel" / "src"


class BuildPyWithKernel(build_py):
    def run(self):
        super().run()
        target = Path(self.build_lib) / "emucraft"
        sources = target / "_kernel"
        sources.mkdir(parents=True, exist_ok=True)
        for name in ("emucraft.c", "emucraft.h"):
            shutil.copy2(KERNEL / name, sources / name)
        sys.path.insert(0, str(ROOT))
        try:
            from emucraft.kernel import KernelError, build_library

            suffix = {"darwin": ".dylib", "win32": ".dll"}.get(sys.platform, ".so")
            build_library(target / f"_emucraft_kernel{suffix}", KERNEL / "emucraft.c")
        except KernelError as exc:  # no compiler: the kernel is built on first use instead
            print(f"warning: kernel not prebuilt ({exc})")
        finally:
            sys.path.pop(0)


class BinaryDistribution(Distribution):
    def has_ext_modules(self):
        return True


setup(cmdclass={"build_py": BuildPyWithKernel}, distclass=BinaryDistribution)
