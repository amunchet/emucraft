# Changelog: Frontend between apps

- Armada session: `Frontend between apps`
- Branch: `armada/Frontend-between-apps-3837e7`
- Base branch: `master`
- Started: 2026-10-27 01:42 UTC

Entries are appended newest last, each stamped with the UTC date and time.

## 2026-10-26 20:49 CDT
Clicking the app name in the top bar of Emucraft, Moonshot, or the Laser Simulator now opens a menu that switches between
the three apps. The links come from the current host name: on a deployed host the first subdomain label is swapped
(`emucraft.example.com` -> `moonshot.example.com`), and on localhost or an IP address each app's default port is used.
The menu is frontend only. Emucraft and Moonshot share it through `emucraft/web/js/util.js`, and the separately built
laser app has a small copy. The menu is left out of Emucraft's standalone HTML report, and it is set up before the 3D
view, so it still works when WebGL fails.
