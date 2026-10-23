"""Builds the C kernel into the package so installs work without a compiler at
run time. Everything else lives in pyproject.toml."""

import sys

# Checked first so an old interpreter gets this message instead of a confusing
# build failure (the build requirement in pyproject.toml is kept low enough for
# old pips to get this far). Keep this file valid Python 3.6 syntax.
if sys.version_info < (3, 9):
    sys.exit(
        "Emucraft needs Python 3.9 or newer, but {} is Python {}.{}.{}.\n"
        "Install a newer Python first; see 'Installing' in README.md.".format(
            sys.executable, *sys.version_info[:3]))

import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

from setuptools import setup  # noqa: E402
from setuptools.command.build_py import build_py  # noqa: E402
from setuptools.dist import Distribution  # noqa: E402

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
