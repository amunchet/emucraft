# Changelog: Admin page on laser simulator

- Armada session: `Admin page on laser simulator`
- Branch: `armada/Admin-page-on-laser-simulator-88cd64`
- Base branch: `master`
- Started: 2026-10-30 01:21 UTC

Entries are appended newest last, each stamped with the UTC date and time.

## 2026-10-29 20:22 CDT
The app switcher in Emucraft, Moonshot and the Laser Simulator now has an **MCP Admin** link to `/admin/`, the
caddy-auth0 page that mints the MCP tokens. Frontend only (`emucraft/web/js/util.js` and its copy in
`laser/src/main.js`). The admin page exists only on the deployed host, so when the apps run locally on their own ports
the link is left out rather than pointing at nothing.
