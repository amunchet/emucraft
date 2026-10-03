# TODO

## Kernel
- [x] ~~**Non-square blocks index out of bounds.**~~ Fixed: `cut()`, `write_block()` and `print_block()` now index as `BLOCK[x * DIM_Y + y]`. Covered by `test_non_square` in `kernel/tests/test_emucraft.c` and `web/tests/test_simulate.py`.
- [x] ~~**`process_from_file` misreads parser output.**~~ Fixed: reads the 7 column format (6 column files still work). Covered by `kernel/tests/test_file.c`.
