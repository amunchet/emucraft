# Changelog: Emucraft Drilling

- Armada session: `Emucraft Drilling`
- Branch: `armada/Emucraft-Drilling-fe8ff4`
- Base branch: `master`
- Started: 2026-10-22 22:06 UTC

Entries are appended newest last, each stamped with the UTC date and time.

## 2026-10-23 01:36 UTC
Reset the branch onto master. Its earlier work (step-by-step canned-cycle expansion and collision checks in the old `gcode/` parser and `kernel/src/functions.c`) targeted code that master's restructure deleted, and master already covers it: the `emucraft` interpreter expands G81/G82/G83 into rapid-to-R, feed, and G98/G99 retract moves, and the kernel sweeps every move continuously for rapid-cut, holder, shank and table collisions. Added `test_peck_cycle_is_checked_along_the_whole_hole` to lock that in for G83: a holder buried partway down the hole and a rapid to an R plane inside the stock are both reported on the cycle line.
