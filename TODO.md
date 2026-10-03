# TODO

## Kernel
- [ ] **Non-square blocks index out of bounds.** `cut()` and `write_block()` in `kernel/src/functions.c` (and `print_block()`) index the block as `BLOCK[x * DIM_X + y]`. For a row-major `DIM_X` x `DIM_Y` array the row stride is `DIM_Y`, so it should be `BLOCK[x * DIM_Y + y]`. Only square blocks work today, and a non-square block reads/writes past the end of the buffer. The kernel tests only use square blocks, so they don't catch it. The web service (`web/simulate.py`) works around this by padding the grid to a square; drop the padding once this is fixed.
