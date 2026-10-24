# Emucraft kernel

The C half of Emucraft: stock simulation and collision checks. One
freestanding C99 file, `src/emucraft.c`, with its API in `src/emucraft.h`.

```sh
make            # build/libemucraft.so and ../emucraft/web/wasm/emucraft.wasm
make test       # unit tests
```

- **Native**: loaded by `emucraft/kernel.py` through ctypes. The Python side
  runs several row bands at once on threads; ctypes releases the GIL, and
  bands never share cells.
- **WebAssembly**: built with `clang --target=wasm32 -nostdlib`, with no
  imports. The browser viewer (`emucraft/web/js/kernel.js`) loads it to
  replay programs locally.

## Model

The stock is a height field: `nx * ny` cells, each holding the top of
material at the cell center. A move is a straight segment of a
rotationally symmetric tool: flat, ball, bull nose, or cone for drills and
chamfer mills. For each cell inside the swept outline, the kernel finds
the interval of the move during which the cell is under the tool. It then
takes the lowest point of the tool surface over that interval: closed form
for flat and ball tools, a short golden-section search for the others.
The cost of a move therefore depends on the area it sweeps, not its length.

Checks happen during the same sweep:

- material removed by a move flagged rapid, spindle-off or no-cut;
- material above the flute length where the cutter meets it (shank rub);
- shank and holder cylinders. A body can only touch a cell before the cutter
  reaches it (old height) or after the cutter leaves (swept height), so both
  intervals are checked exactly. Per-tile maximum heights skip most of the
  stock when bodies are large.

Speed-ups that do not change the result:

- the part of the start disk the previous move already cut is skipped;
- cheap lower bounds reject most cells before any square root;
- `ec_simplify` merges nearly collinear moves (the Python checker uses a
  tolerance of half the event threshold).

Tests in `tests/test_kernel.c` compare every tool shape and move type
against a brute-force reference that stamps the tool at 20,000 positions.
They also check that row bands and the start-disk skip leave the results
bit-identical. `tests/test_wasm_parity.py` in the Python suite checks that
the committed WebAssembly module and the native library agree bit for bit.
