# Changelog: Updates

- Armada session: `Updates`
- Branch: `armada/Updates-ab339f`
- Base branch: `master`
- Started: 2026-11-07 07:47 UTC

Entries are appended newest last, each stamped with the UTC date and time.

## 2026-11-07 02:37 CST
Your own fixture models, placed in Setup and over MCP, and links from MCP to a viewable simulation.

G-code has no word for its fixturing, so a check now chooses, first found: fixtures set for that check (Setup tab,
`check_program`), the saved setup a `(FIXTURE: name)` comment names (other wordings via `comment_rules` with
`program.fixture`), the saved setup named after the program, then the config's `[[fixtures]]`. The report's
`fixture_source` says which. A named setup that is not saved is a warning and one that cannot be used is an error, because
a fixture the check cannot see must not pass quietly.

- **Library:** uploaded STLs and named setups live in `$EMUCRAFT_DATA` (`/store` in the image), shared by the viewer, the
  MCP server and the CLI through the new `emucraft-store` volume. The two containers shared nothing before, which is why
  neither feature could work without it. `emucraft-mcp`'s settings stay in their own volume, so nothing is lost on upgrade.
- **Placement:** a fixture can now take `at` (where the center of its bounding box goes, and what it turns about), so a
  CAD model's far-away origin stops mattering; `offset` still works. `rotate` also takes [x, y, z], for models exported
  Y-up, and `position_units` separates the position's units from the model's.
- **Viewer:** the Setup tab edits each fixture, uploads STLs, adds boxes, and saves, loads and deletes setups. With *Move* on,
  a fixture can be dragged in the 3D view, and each drop re-checks. The first view now takes in the fixtures, not just
  the stock. Writes are new `PUT`/`DELETE` routes, which the production Caddyfile already sends through the write permission.
- **MCP:** `upload_fixture`, `delete_fixture`, `list_fixtures`, `save_fixture_setup`, `delete_fixture_setup`, `PUT /fixtures/<name>`
  for files too big for a tool call, and `fixture_setup` and `name` on `check_program`. Models may be in the library or the
  programs folder, so a server without a programs folder can now use STL fixtures at all.
- **Links:** every `check_program` saves the check and returns `simulation.url`, `<EMUCRAFT_URL>/?sim=<id>`, which opens it in
  the viewer. It goes through the viewer, not the MCP server, because `/emucraft-mcp/*` takes bearer tokens only, so a
  browser could not open it; the viewer is behind the normal sign-in, and viewing only takes read. Saved checks keep
  their config, so Re-check works from a link. The oldest go once they pass 2 GB.
- Fixed along the way: the STL cache could serve the old mesh when a same-size file replaced another within one kernel
  clock tick, since mtimes only move with that tick (a test caught it; the key now has the inode too).

`EMUCRAFT_URL` has to be set on `emucraft-mcp` for absolute links; without it, an HTTP server returns `url: null`.

## 2026-11-07 02:53 CST
Fixes from running the whole flow: a real MCP client through a Caddy with the production prefix, into separate viewer and
MCP containers sharing the volume, and the viewer in headless Chromium.
- A setup's bare model names now always mean the library. `fixture_setup` used to look in the programs folder first, so a
  same-named file there could silently stand in for the setup's model.
- Saving under the program's own name now hands over to the saved setup, so the note says where the fixtures come from
  rather than "save a setup to keep it", and the "Saved setup" toast shows instead of being cleared by the re-check.
- MCP uploads are parsed off the event loop, so a big STL no longer holds up other calls.
- A refused or unknown `PUT` now reads its body first, so it can't be taken for the next request on a keep-alive connection.

Checked in that setup: raw upload, placing the vise with `at` (same bounds and same 3 collisions as the example's `offset`),
the link opening through `/emucraft/`, playback, clicking an issue, lowering the vise and re-checking from the link, saving
there, and the next MCP check finding that setup with no fixtures argument. Links survive a container restart, a viewer
with read permission only loads one without a single write, and a removed one says it is gone. Also clicked through in
the browser: boxes, junk and repeated uploads, tilting a Y-up model, dragging from above, with Shift and from the front,
loading a setup on another program, and deleting from the library. Real WebGL here came from Mesa's software renderer
(`apk add mesa-dri-gallium mesa-egl mesa-gles`, Chromium with `--use-angle=gl-egl`), which earlier sessions lacked.
