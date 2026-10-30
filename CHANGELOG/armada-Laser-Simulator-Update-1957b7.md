# Changelog: Laser Simulator Update

- Armada session: `Laser Simulator Update`
- Branch: `armada/Laser-Simulator-Update-1957b7`
- Base branch: `master`
- Started: 2026-10-30 01:20 UTC

Entries are appended newest last, each stamped with the UTC date and time.

## 2026-10-29 20:25 CDT
Laser simulator templates: proprietary master parts (1911 frame, Colt Python, ...) stored server-side and engraved by
id. The MCP server gains `list_templates`, `upload_template` and `delete_template` tools, plus `PUT /templates/<id>.<stl|obj>`
for parts too big for a tool call; `render_glb` and `POST /api/laser/render` accept a template in place of the model.
Templates are write-only by design: nothing reads one back, listings show only id/format/bytes/upload time, renders of a
template are always the ±10 % stand-in (`true_scale` refused), files are 0600 under validated ids, and compose keeps
them in a `laser-templates` volume (read-write for laser-mcp, read-only for laser-api).
