# Changelog: Emucraft MCP 

- Armada session: `Emucraft MCP `
- Branch: `armada/Emucraft-MCP-cc0f37`
- Base branch: `master`
- Started: 2026-10-22 20:33 UTC

Entries are appended newest last, each stamped with the UTC date and time.

## 2026-10-22 17:15 CDT
Added `mcp_server.py`, an MCP server for the CAM pipeline with tools to analyze a program, generate XYZ, simulate it with the C kernel (flags material removed by rapid or spindle-off moves) and tune comment detection.

Comment detection in `gcode_parser.py` is now configurable: per-field label aliases with fuzzy, typo-tolerant matching, regex patterns for macro variables, and mm/in suffix conversion. Settings persist to `detection.json`. New detected fields: `cutting_length`, `shank_diameter` and `gauge_length`.

Fixes found along the way:
- The block was never set for programs ending in `M30`, because the helpers were skipped.
- The number regex rejected values like `.375` and `5`.
- The kernel indexed rows by `DIM_X` instead of `DIM_Y`, which corrupted non-square blocks.
