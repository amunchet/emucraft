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

## 2026-10-22 20:33 CDT
Self-review found that this branch was built on a stale `master`. Current `master` replaced `gcode_parser.py` and the prototype kernel with the `emucraft/` package, so the branch conflicts and is not shipped. The MCP server and fuzzy detection need porting onto `emucraft`, pending a decision.

## 2026-10-23 00:20 CDT
Merged current `master` and dropped the prototype-based work. Comment detection in `emucraft/gcode.py` now has a label-alias fallback. A `(label: value)` comment that no rule matched is read when its label is close to a known alias. This catches typos and variants for holder diameter and length, stickout, flute/cutting length, shank, diameter and stock. Short aliases must match exactly, and a loose match is reported as a `comment-label` info message. It is configurable through `[comment_aliases]` and `[check] fuzz_threshold`. Lengths written with `mm`/`in`/`"` are converted once the program's units are known. Detection on the bundled examples is unchanged.

## 2026-10-23 00:20 CDT
Added `emucraft mcp`, an MCP server (stdio) for the generative CAM pipeline, available through the optional `mcp` extra (Python 3.10+). Tools:
- `check_program`: a full collision check with stock, tool and resolution overrides.
- `program_info`: what was detected, with the line and text for each value.
- `get_settings`, `update_settings`, `reset_settings`: tune detection and tool defaults in a saved emucraft JSON config.

CI now installs the extra, so the MCP tests run on Python 3.12. The README documents the server and comment detection.

## 2026-10-23 00:21 CDT
Removed the reversed `Z MIN`-style stock aliases. Posts use those labels for toolpath extents, which would otherwise overwrite the stock box.
