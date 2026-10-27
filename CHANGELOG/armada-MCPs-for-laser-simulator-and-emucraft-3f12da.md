# Changelog: MCPs for laser simulator and emucraft

- Armada session: `MCPs for laser simulator and emucraft`
- Branch: `armada/MCPs-for-laser-simulator-and-emucraft-3f12da`
- Base branch: `master`
- Started: 2026-10-27 21:23 UTC

Entries are appended newest last, each stamped with the UTC date and time.

## 2026-10-27 16:33 CDT
MCP servers for emucraft and the laser simulator, served next to Moonshot's so the `/admin` token page covers all
three. The admin page lists every `auth0 { mcp }` route in the Caddyfile, so each server gets one:
`/emucraft-mcp/mcp` and `/laser-mcp/mcp`, kept out of the browser-login and write-permission matchers.
- `emucraft mcp --http` serves the existing tools over streamable HTTP with `GET /health` and an optional
  `EMUCRAFT_TOKEN`. `--programs DIR` keeps `path` / `config_path` inside that folder (new `list_programs` tool);
  over HTTP without it only inline G-code is accepted, so callers can't read the server's files.
- `laser/mcp.mjs`: `laser_info` and `render_glb` (same job and stand-in scaling as the API, GLB returned as an
  embedded resource). `server.mjs` now exports `run_job` and `info` so both share one worker-thread job runner.
- docker-compose: `emucraft-mcp` and `laser-mcp` services (own profiles, like `moonshot-mcp`); `laser-mcp` shares
  `LASER_SECRET` with `laser-api`. The emucraft image now includes the MCP SDK.
- Reference Caddyfile and README updated.
