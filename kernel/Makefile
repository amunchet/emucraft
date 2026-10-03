# Emucraft kernel
#
#   make            native shared library + WebAssembly module
#   make native     build/libemucraft.so (loaded by the Python package)
#   make wasm       ../emucraft/web/wasm/emucraft.wasm (loaded by the browser viewer)
#   make test       C unit tests
#
# The WebAssembly module is committed so the viewer works without clang;
# rebuild it whenever src/emucraft.c changes.

CC ?= cc
CLANG ?= clang
BUILD := build

UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Darwin)
	LIB := $(BUILD)/libemucraft.dylib
	SHARED := -dynamiclib
else
	LIB := $(BUILD)/libemucraft.so
	SHARED := -shared
endif

# -ffp-contract=off keeps native and WebAssembly results bit-identical.
COMMON := -O3 -std=c99 -fno-math-errno -ffp-contract=off -fvisibility=hidden -Wall -Wextra
CFLAGS ?= $(COMMON)
WASM := ../emucraft/web/wasm/emucraft.wasm
WASMFLAGS := --target=wasm32 $(COMMON) -nostdlib -mbulk-memory \
	-Wl,--no-entry -Wl,--export-dynamic -Wl,--strip-all \
	-Wl,--max-memory=4294967296 -Wl,-z,stack-size=1048576

.PHONY: all native wasm test clean

all: native wasm

native: $(LIB)

$(LIB): src/emucraft.c src/emucraft.h | $(BUILD)
	$(CC) $(CFLAGS) -fPIC $(SHARED) -o $@ src/emucraft.c -lm

wasm: $(WASM)

$(WASM): src/emucraft.c src/emucraft.h
	@mkdir -p $(dir $@)
	$(CLANG) $(WASMFLAGS) -o $@ src/emucraft.c

$(BUILD)/test_kernel: tests/test_kernel.c src/emucraft.c src/emucraft.h | $(BUILD)
	$(CC) $(COMMON) -g -Isrc -o $@ tests/test_kernel.c src/emucraft.c -lm

test: $(BUILD)/test_kernel
	./$(BUILD)/test_kernel

$(BUILD):
	mkdir -p $(BUILD)

clean:
	rm -rf $(BUILD)
