# Changelog: Quick UI Change

- Armada session: `Quick UI Change`
- Branch: `armada/Quick-UI-Change-f2ce82`
- Base branch: `master`
- Started: 2026-10-27 18:14 UTC

Entries are appended newest last, each stamped with the UTC date and time.

## 2026-10-27 13:15 CDT
Fixed the missing app-switcher dropdown on the deployed Laser Simulator. The switcher code was already in
`laser/src/main.js`, but `laser/Dockerfile` copied only `index.html` and `src/` into the build, not `vite.config.js`.
Without the config's `base: './'`, the image's page loaded its script from the root (`/assets/...`), which 404s behind
Caddy's `/laser-simulator/` prefix, so none of the app's JavaScript ran. The Dockerfile now also copies
`vite.config.js` and `viewer.html` (the config lists it as a build input, which also puts the customer viewer page into
the image). Verified with a Docker build: the page now uses `./assets/...`, and served under `/laser-simulator/` the title
shows the dropdown with Emucraft, Moonshot and Laser Simulator links.
