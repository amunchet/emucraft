# Changelog: Laser Simulator - select template

- Armada session: `Laser Simulator - select template`
- Branch: `armada/Laser-Simulator---select-template-5778b9`
- Base branch: `master`
- Started: 2026-10-30 06:30 UTC

Entries are appended newest last, each stamped with the UTC date and time.

## 2026-10-30 01:41 CDT
The Laser Simulator's Inputs panel has an **Or a stored template** list: pick one of the stored template parts and it
loads like an uploaded model (true scale, full alignment, simulation and export), so trying a template no longer means
finding and uploading its file.
- **This ends write-only templates, by the user's choice.** The simulator runs in the browser and needs the real
  geometry, so `laser-api` gains `GET /api/laser/templates` (listing) and `GET /api/laser/templates/<id>` (the file),
  both behind `LASER_TOKEN`. Any user signed in to the site with read permission can now download a template's
  geometry. MCP is unchanged: no tool reads a template back, and renders of templates are still always the stand-in.
- The `laser` image's nginx (`laser/nginx.conf.template`) passes only those two routes on to `laser-api`, adding the
  bearer token, so the Caddyfile is unchanged and rendering is still not reachable from the browser. `npm run dev`
  proxies them to a local `node server.mjs`. Without the API, the list shows "Unavailable" and the app works as before.
- Checked: laser tests 44/44, including a new API test for listing, download, unknown id, path traversal, wrong method
  and token. Both images built and ran together: through nginx, the list and the 14 MB 1911 grip came back with the
  token injected, and `/api/laser/render` stayed 404. In Chromium, the page's fetches listed the template, downloaded
  it and parsed it (284,600 triangles). Clicking through the picker itself wasn't tested, because WebGL isn't available
  in this sandbox.
