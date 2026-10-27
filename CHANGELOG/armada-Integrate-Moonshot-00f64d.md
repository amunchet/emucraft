# Changelog: Integrate Moonshot

- Armada session: `Integrate Moonshot`
- Branch: `armada/Integrate-Moonshot-00f64d`
- Base branch: `master`
- Started: 2026-10-25 07:04 UTC

Entries are appended newest last, each stamped with the UTC date and time.

## 2026-10-25 02:52 CDT (2026-10-25 07:52 UTC)
Brought Moonshot (cylinder contact planning for robot sanding, amunchet/moonshot@db8eee0) into the emucraft
project, with a web interface, and its MCP server kept separate.
- Import: the `moonshot` package sits beside `emucraft`, with its sample parts in `samples/`, its README as
  `moonshot/README.md` and its tests in `tests/moonshot/` (skipped when its dependencies are missing). The upstream
  history (93 MB, mostly rendered pictures) is not carried over.
- Packaging: one distribution. Moonshot is optional (`pip install -e ".[moonshot]"`, Python 3.10+), so emucraft still
  installs and runs on 3.9 with no dependencies. `moonshot` and `moonshot-mcp` scripts; `.[moonshot-mcp]` and
  `.[moonshot-dev]` extras.
- Job store: uploads, runs, the one-at-a-time task queue, stats and file listings moved out of the MCP server into
  `moonshot/store.py`, which the MCP server and the web page share. The MCP tools and their behavior are unchanged
  (its tests only point at `store.DATA` now).
- Web interface: `emucraft serve` has a Moonshot page at `/moonshot/` (linked from the G-code viewer) and its API at
  `/api/moonshot/...` (`moonshot/web.py`). Upload a model or add a sample, plan it with every CLI option, follow the
  task log live, and see the part, holder and grid in 3D, colored by reach, grain tilt, neighbor turn, tracking offset
  or pass. Click a point for the drum touching it, write the FANUC program and play its contact path back with the
  drum, render the step pictures, and download every file. Without Moonshot installed the API answers 503 with the
  install command. Data goes to `--moonshot-data` (default `./moonshot-data`), separate from the MCP server's.
- Docker: the default image (web) now includes Moonshot, with its data in a `/data` volume; `--target moonshot-mcp`
  builds the MCP server's own image, a separate `moonshot-mcp` compose service behind the `moonshot-mcp` profile.
- CI: a Moonshot job on Python 3.12 (its tests, the server tests, and a sample planned and programmed from the CLI).
- Tests: the web API end to end over HTTP (upload, samples, plan, scene consistent with the results, a program in a
  moved and turned part frame whose contacts land back on the part, downloads, refused paths and options), and the
  viewer working without Moonshot. Full suite: 325 passed in a Debian Python 3.12 container. The page was also
  driven in headless Chromium against the built image: sample, plan, 3D view, picking, program playback, files.
- Found, not changed: `python -m moonshot.fanuc` fails on a 0.1 in grid of the wave plate ("no way off and back on
  the part ... clears the drum"); 0.05 in works.

## 2026-10-25 03:03 CDT (2026-10-25 08:03 UTC)
Merged master, which added emucraft's own G-code MCP server (`emucraft mcp`, `.[mcp]` extra). Both extras are kept in
`pyproject.toml` and `requirements.txt`. Moonshot's MCP test is now `tests/moonshot/test_moonshot_mcp.py`, since a
second `test_mcp.py` breaks pytest's collection, and its compose profile is `moonshot-mcp`, so the two MCP servers
are not confused. Full suite after the merge: 339 passed.

## 2026-10-25 03:25 CDT (2026-10-25 08:25 UTC)
At the user's request, Moonshot and the laser engraving simulator each get their own Docker image, apart from
emucraft's.
- The laser simulator (`laser/`, from `armada/Lasering-Simulator-9ff683`, which had no pull request) is merged into
  this branch. `laser/Dockerfile` builds it with Vite and serves the static bundle with nginx on port 80.
- Moonshot's page left `emucraft serve`: `moonshot-web` (`python -m moonshot.web`, port 8766 by default) serves it at
  `/` with its API under `/api/`. The page moved to `moonshot/page/` and still borrows the G-code viewer's 3D viewer,
  helpers, stylesheet and three.js from `emucraft/web/`. Emucraft's server, CLI, viewer, root `Dockerfile` and tests
  are back to exactly master's.
- `moonshot/Dockerfile` (built from the repository root) runs the web page; the MCP server runs from the same image as
  its own `moonshot-mcp` compose service, with its own data, behind the `moonshot-mcp` profile.
- `docker-compose.yml` has `emucraft`, `moonshot` and `laser` (plus `moonshot-mcp` on request); the README's Web
  Deployment lists them, with a Caddy example for each.
- Checked: full Python suite 338 passed, laser tests 20 passed; all three images built and loaded in headless
  Chromium without errors, a Moonshot sample planned and shown in its container, a laser simulation completed in its
  container, and the MCP server answered `/health` from the Moonshot image.
