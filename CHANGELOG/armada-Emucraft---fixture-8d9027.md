# Changelog: Emucraft - fixture

- Armada session: `Emucraft - fixture`
- Branch: `armada/Emucraft---fixture-8d9027`
- Base branch: `master`
- Started: 2026-10-30 02:23 UTC

Entries are appended newest last, each stamped with the UTC date and time.

## 2026-10-29 21:45 CDT
Fixture collision checks: finished the fixturing feature prototyped on `dev`. A
`[[fixtures]]` config section places STL models (binary or ASCII) or boxes into
program coordinates, with units, scale, rotation about Z and an offset. Each
check rasterizes them from +Z into a second kernel block (new
`ec_sim_add_mesh` in the C kernel) and runs every move over it as a move that
must not cut, with the same tools. Anything the cutter removes there, or the
shank or holder touches, is a new `fixture` issue naming the fixture and the
exact line. The block only covers what the tools can reach and is skipped when
no move goes below the fixture top. The viewer draws the fixture meshes (with a
Fixtures toggle) and lists them under Setup; the text report and MCP server
know about fixtures, and MCP fixture models must stay inside the programs
folder. Added `examples/vise/` (the Kurt vise from `dev`, drawn in cm, with a
demo program that profiles through both jaws and has a holder hit), and the
web program list now includes example subfolders. The committed WebAssembly
module was not rebuilt: the viewer does not call the new function, and no
clang with a wasm32 target was available here (`make -C kernel wasm` refreshes it).
