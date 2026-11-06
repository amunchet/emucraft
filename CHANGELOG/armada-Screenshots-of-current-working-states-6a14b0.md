# Changelog: Screenshots of current working states

- Armada session: `Screenshots of current working states`
- Branch: `armada/Screenshots-of-current-working-states-6a14b0`
- Base branch: `master`
- Started: 2026-11-06 01:45 UTC

Entries are appended newest last, each stamped with the UTC date and time.

## 2026-11-05 19:51 CST
Merged the open broken-tool branch (PR #20) into this branch so all three features can be exercised together: it was never on master and conflicted with fixtures and workholding. Conflicts were both-sides additions in the issue kinds, the README table, `CheckSpec`, `_issues_from_events` and the viewer's labels.

## 2026-11-05 20:23 CST
Walked fixtures, the broken-tool check and workholding through the real viewer (headless Chromium) and the MCP server (stdio and token-protected HTTP), and fixed what got in the way.

Workholding in the viewer: an incomplete setup used to blank the form back to Fixture: Off while the bad settings stayed active, Off and emptied fields were never sent, and a green "No collisions found" sat under a red "Program errors". The form now keeps what was entered, Off works (also over a config file's `[workholding]`), emptied fields return to their defaults, torque and force clear each other, malformed clamp entries are rejected instead of dropped, and an error card and a "Workholding not checked" badge explain the problem. A torque past the fixture's limit no longer shows a green card. The least torque is rounded up in the viewer and CLI, since 44 ft-lbf (shown for 44.41) failed; the README example is now 44.5. Drill failures name the cutting torque instead of "pushes 0 lbf sideways".

Viewer: the all-clear note no longer claims holder checks for tools without holder data, Setup has a broken-tool switch, fixture and workholding moves are marked in the By tool stock colouring, warnings are amber in the HUD, the Issues pill is red when a check failed with no issues, and optimized downloads have no folder in their name. CLI: `flutes 1.0000` was the flute length, it now reads `flute length` and the flute count is shown.

MCP: the tool text now covers `broken-tool`, `check.broken_tool` and fixtures. `check_program` takes `fixtures` for one call, needed by inline callers since the deployed programs folder is empty. `update_settings` refuses a model outside the programs folder (it used to save it and fail every later check), and relative models resolve against the programs folder, since they could never work from `/data` in Docker.

Added `examples/broken_tool_demo.nc` and `examples/workholding/`: no example showed either feature.

## 2026-11-05 21:12 CST
Corrected the `check_program` fixtures documentation: lengths follow the config's units, like the config's own fixtures, not always the program's. Found while preparing the pull request: an inch config with a mm program scaled a box 25.4 times against what the text promised.

## 2026-11-05 21:22 CST
`Sim.load_heights` copied the height field cell by cell, about 0.65 s per tool run on a 5 million cell grid, and the broken-tool check calls it for every run: crash_demo took 1.98 s instead of 0.09 s. It now copies the buffer directly, 0.14 s, which also halves the test suite.
