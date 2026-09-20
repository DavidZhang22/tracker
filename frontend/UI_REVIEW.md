# Frontend visual and performance review — 2026-09-20

The production builds were inspected in Chromium using synthetic account data, without accessing production libraries or remote source websites. The reusable harness is `scripts/visual-audit.py`; it runs inside the existing Playwright browser image with networking disabled, a read-only root filesystem, a 1 GiB memory limit, and two CPUs. Its local fixture server introduces 80 ms of latency for each API response.

## Visual coverage

Forty screenshots cover Library, item rows, expanded details, selection patterns, Add item, file-column mapping, import preview, Settings, account controls, and sign-in at 1440 × 1000, 900 × 900, 390 × 844, and 320 × 780. Screenshots were visually reviewed, including before/after comparisons of tablet link rows and narrow-screen controls. All captured states stayed within the document width; deliberately scrolling tab strips remain scrollable.

The final build keeps mobile search and its filter button on one row, aligns form control heights, wraps imported metadata and long application addresses, moves dates below link titles when tablet space is limited, and presents selection patterns as a responsive grid. Fine-pointer desktop scrollbars reserve space; mobile pages have no unused scrollbar gutter.

A separate 390 px check blocks the Settings JavaScript chunk. The page displays “Page unavailable” and “Reload page”; the Library navigation remains functional and restores the library. Ordinary routes produced no uncaught page errors.

Raw PNGs and reports are local, ignored artifacts under `backend/data/frontend-review/{baseline,final,release}-shots/`.

## Loading and retained data

Public entry JavaScript now defers private workspace, account, privacy and recovery routes. The complete static import closure, including shared chunks, is **353,779 → 289,376 bytes raw** and **95,783 → 77,382 bytes gzip** (19.2% less gzip). This describes initial/sign-in code, not the total signed-in Library download; the Library still needs its deferred workspace chunk. Workspace code loads while authenticated preferences are fetched.

Unchanged search metadata keeps a stable reference instead of being serialized and parsed repeatedly. On the 500-item long-context synthetic benchmark, retained duplicate metadata falls from about 8.43 MB to 64 KB above the original API rows. This is an isolated V8 allocation measurement, not an estimate of typical account memory. Query workers/indexes start only when needed. Link-result cache retention is bounded by eight pages and a conservative 1 MiB payload estimate; a larger page still renders but is not retained in the cache. Private data stays in memory and is discarded when its view/account unmounts.

Three lazily initialized date formatters replace per-row formatter creation. Formatting 500 dates in all three supported formats took 121.2 → 3.1 ms in the local microbenchmark, with identical output. Invalid source dates now display an unavailable state rather than crashing a row.

[Bundle/allocation measurements](../backend/ml/reports/frontend-loading-local.json) and [date measurements](../backend/ml/reports/frontend-dates.json) include scope and reproducible scripts. No runtime dependencies were added.

## Browser measurements

The same valid fixture contains 500 items with 200 links each (100,000 account links). Three fresh browser contexts were measured per build after visual checks. The baseline is the previous deployed build; the measured final build entry is `index-BgJ6N3ka.js`. A subsequent wording-only change shortens the search placeholder to “Search library”; its 320 px layout was rechecked separately on release entry `index-CqL6RlDS.js`, with the complete placeholder visible and no page overflow. Values below are medians. Heap values follow an explicit garbage collection, and represent the **main page JavaScript heap only**, excluding worker heaps, browser process memory, and native DOM storage.

| Metric | Before | After |
| --- | ---: | ---: |
| First library row ready | 885.7 ms | 876.5 ms |
| Initial decoded JavaScript, including worker resource | 356,013 bytes | 341,300 bytes |
| Initial main-page JavaScript heap | 17.49 MB | 17.10 MB |
| Initial scripting time | 259.9 ms | 232.8 ms |
| Initial active workers | 1 | 0 |
| Initial DOM elements | 14,707 | 14,713 |
| Heap after search/navigation exercise | 19.04 MB | 18.71 MB |
| Cumulative scripting after exercise | 2.813 s | 2.445 s |
| Active workers after returning to the unsearched library | 1 | 0 |

The exercise performs twelve search changes and six route navigations. A fixed settling interval is used between changes; it is a memory/lifecycle exercise, **not a search-latency benchmark**. The sample is too small and the loopback server too fast to claim a meaningful end-to-end loading speedup: first-ready time is effectively unchanged. The measured reductions are approximately 4.1% in initially fetched decoded JavaScript, 2.3% in main-page heap, and 10.4% in initial scripting. The visible library still renders about 14,700 DOM elements at the account limit; larger reductions would need a separate pagination or virtualization design.

Workers are now started only when a query needs one and are disposed when the page is left. This avoids idle worker allocation. These short runs do not establish long-term leak freedom.

The new immutable caching policy for fingerprinted assets is verified separately by backend HTTP tests. This harness serves static files without production cache headers, so its timings do not claim the benefit of browser caching on repeat visits.

## Reproduction

Build the frontend, then run the harness in an isolated environment with Playwright 1.62.0 and Chromium already available:

```sh
python frontend/scripts/visual-audit.py \
  --build frontend/build \
  --output backend/data/frontend-review/recheck \
  --repeats 3 --check-failure
```

Use `--sizes 390x844,320x780 --repeats 0` for a focused narrow-screen capture. Network isolation belongs to the surrounding container; the script binds only to loopback and uses synthetic fixture API routes. Do not point the fixture harness at a live application.

## Verification

The complete frontend suite passed 199 tests; tooling passed 5 tests, ESLint and the production build passed. The focused backend test verifies immutable headers on versioned JS/CSS and 304 responses, while HTML and API responses stay `no-store`; unversioned and missing assets are not marked immutable. An offline final-image smoke test covers every built JS/CSS asset, private API protection, accounts, reading progress, export/deletion, and disabled email delivery.

Browser screenshots were produced in the existing Linux Chromium image because the desktop computer-use runtime failed before opening the browser. This review uses actual rendered production bundles with synthetic API data; it is not a review of a user's private library and does not emulate every mobile browser.
