# Convenience targets; see README.md.

PYTHON ?= python3

.PHONY: all kernel wasm test serve demo clean

all: kernel

kernel:
	$(MAKE) -C kernel native

wasm:
	$(MAKE) -C kernel wasm

test: kernel
	$(MAKE) -C kernel test
	$(PYTHON) -m pytest -q

serve: kernel
	$(PYTHON) -m emucraft serve --open

demo: kernel
	-$(PYTHON) -m emucraft check examples/crash_demo.nc

clean:
	$(MAKE) -C kernel clean
	rm -rf build dist *.egg-info .pytest_cache
