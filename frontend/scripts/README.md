# Frontend maintenance scripts

Run these commands from `frontend/` with dependencies installed. Routine checks
are `npm run lint`, `npm test`, `npm run test:tooling`, and `npm run build`.
The Vitest suite lives in `src/__tests__/`; `tooling.test.mjs` separately checks
Vite's loopback server, API proxy and SPA routes using temporary local servers.

| Script | Purpose and invocation |
| --- | --- |
| `mobile-check.cjs` | Touch layouts and navigation: `node scripts/mobile-check.cjs` |
| `visual-audit.py` | Responsive screenshots and browser lifecycle measurements: `python scripts/visual-audit.py --build build --output .mobile-check/visual` |
| `footer-audit.py` | Footer layout across public and private routes: `python scripts/footer-audit.py --build build --output .mobile-check/footer` |
| `profile-page-load.py` | Cold-load request waterfalls and first-row paint: `python scripts/profile-page-load.py --build build --output .mobile-check/page-load.json` |
| `profile-loading.mjs` | Compare initial bundle size, metadata allocation and cache costs: `node --expose-gc scripts/profile-loading.mjs BASELINE_BUILD CANDIDATE_BUILD .mobile-check/loading.json` |
| `benchmark-browser-search.mjs` | Compare search scores and timings: `node scripts/benchmark-browser-search.mjs BASELINE_MODULE .mobile-check/search.json` |
| `benchmark-dates.mjs` | Compare date formatter output and timings: `node scripts/benchmark-dates.mjs .mobile-check/dates.json` |

Browser checks require a production build and Playwright with Chromium. The
Python checks use `visual-audit.py`'s shared synthetic API fixtures and static
server; keep these scripts together. Run them in the isolated browser environment
described in [the visual review](../UI_REVIEW.md). The Node mobile harness can
also use an installed browser via the settings in [mobile QA](../MOBILE_QA.md).

Create `.mobile-check/` before using it as a JSON output directory; it is ignored
by Git. Screenshots, raw reports and temporary comparison builds belong in ignored
output directories. Keep reviewed summaries in documentation.

The bundle profiler needs `.vite/manifest.json` in both builds; use
`npm run build -- --manifest` when preparing each comparison build. The search
benchmark accepts any JavaScript module exporting `librarySearchIndex`, including
historical `media.js` files and the current `src/Search/searchIndex.js`. Its score
comparison preserves the baseline's query membership and numerical error evidence.
Microbenchmarks exclude browser rendering and network timing.
