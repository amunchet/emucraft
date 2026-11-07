# Changelog: Emucraft - broken tool

- Armada session: `Emucraft - broken tool`
- Branch: `armada/Emucraft---broken-tool-ff8014`
- Base branch: `master`
- Started: 2026-10-30 02:24 UTC

Entries are appended newest last, each stamped with the UTC date and time.

## 2026-10-29 21:31 CDT
Added a broken-tool holder check. Tool breakage is only probed at the start and
end of a tool's run, so a tool that snaps as its run begins keeps going with its
holder leading. For every tool run with a known holder, the check now sweeps the
holder over the stock as that run found it (a broken tool removes nothing) and
reports any hit as a `broken-tool` warning, one per run, skipping moves where the
holder already hits with an intact tool. On by default; `[check] broken_tool =
false` turns it off. Shown in the viewer's issue list and stock colouring.
Roughly doubles simulation time for programs whose tools have holder data.
