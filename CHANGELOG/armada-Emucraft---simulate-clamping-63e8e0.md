# Changelog: Emucraft - simulate clamping

- Armada session: `Emucraft - simulate clamping`
- Branch: `armada/Emucraft---simulate-clamping-63e8e0`
- Base branch: `master`
- Started: 2026-10-30 02:41 UTC

Entries are appended newest last, each stamped with the UTC date and time.

## 2026-10-29 22:19 CDT
Added a workholding check (`emucraft/workholding.py`, `[workholding]` in the config): for every short piece of the cutting path the simulation measures the axial depth and cross-section of the cut, Kienzle's law turns that and the feed per tooth into the peak force of a straight-fluted cutter (an upper bound for a helical one), plus the lift of a right-hand helix and the torque of drills, all scaled by an adjustable worn-tool factor. A vise (jaw friction, with the floor taking the rocking moment) or toe clamps (sliding and turning under the clamps, tipping over an edge) must hold that load an adjustable safety factor times over. The check reports the margin at the given torque, the least torque that holds every cut, and the fixture's limit (the vise maker's maximum, or 75 % of the bolt's proof load for clamps); cuts that are not held become `workholding` errors on their lines, so `emucraft check` fails in CI. Wired into `run_check`, the text and JSON reports, the MCP `check_program` tool (`workholding` argument) and the web API settings. The feed optimizer now shares the path-splitting helper (`check.fine_path`).

## 2026-10-30 01:30 CDT
The viewer shows the workholding result at the top of the Issues tab (margin, least torque that holds, fixture limit, weakest cut, which jumps to its line) and has a Workholding section in Setup (fixture, material, torque, safety and wear factors, friction, vise or clamp geometry) plus a Flutes field on each tool card, sent as `settings.workholding`. The status badge no longer counts workholding failures as collisions. The vise model now lets the floor take the rocking moment at the far end of the jaws (the part cannot turn about the middle of the jaws without pushing through the parallels). README documents the model, options, defaults and assumptions.

## 2026-10-30 02:31 CDT
Kurt 6" vise presets: `vise = "d688" | "dx6" | "3600v" | "hd690" | "hdl6" | "scd640"` (SCD640 is the self-centering model). Each uses the clamping-force-vs-handle-torque table from Kurt's operating manual (interpolated; the tables are not linear), 6" jaws, and the top of the table as the torque limit (80 ft-lbf, 70 for HDL6, 60 for SCD640). The required torque is now read back off the vise's curve. The viewer's Setup form has a vise model picker.
