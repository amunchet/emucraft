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

## 2026-10-27 01:54 CDT
All three apps now run on one host under path prefixes (`/emucraft/`, `/moonshot/`, `/laser-simulator/`) instead of separate
subdomains, and the app switcher links by path. Every URL the pages use is now relative:
- Emucraft's `/api` calls.
- Moonshot's stylesheets, scripts, import map and API calls, plus the root-relative file links its server returns, which are
  rewritten on the page.
- The laser build, which now sets `base: './'` in `laser/vite.config.js` (both the simulator and the customer viewer page).

This is still frontend only. The README's Caddy example uses `handle_path` to strip each prefix, plus a `redir` that adds the
trailing slash the relative URLs need. Run locally, each app is still served at the root of its own port, and the switcher
links to those ports. Tested behind a prefix-stripping proxy: all three pages load with no failed requests and switch to
each other.

## 2026-10-27 01:56 CDT
Merged `master` (the laser render API, #10). Master moved the laser presets out of `laser/src/main.js`, so the switcher block
now sits beside master's version of that file. Master also added `laser/vite.config.js`, so the relative build base moved
into it, where it covers the new customer viewer page too.

## 2026-10-27 02:24 CDT
Fixed the failing CI test `tests/moonshot/test_web.py::test_page_is_served`. It checked for the old root-relative
`/moonshot.js` on Moonshot's page. It now asserts the relative `src="moonshot.js"` and `'./js/viewer.js'` that serving
under `/moonshot/` depends on, so a regression back to root-relative URLs fails the test.
