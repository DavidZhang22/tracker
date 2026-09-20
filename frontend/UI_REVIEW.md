# Frontend visual and performance review (2026-09-20)

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


## Follow-up: page startup and description placement

The item description now follows the Unread / New links / Read summary strip. Thirty-one browser states were checked at 1440, 390, and 320 px, with no document overflow or unexpected page errors; the failed-chunk fallback still recovers to Library. Desktop and mobile item screenshots confirm the description follows the summary.

A read-only timing of the owner's current library (16 items, 3,386 links) measured 15.2 ms for the warm database query and 0.35 ms for JSON encoding. The main avoidable delay was the browser waterfall: authentication, then preferences, then page data. Item route code also waited for preferences and the workspace chunk.

After authentication, initial Library or Item metadata now starts alongside preferences and the requested private route's code. Preferences still gate rendering, preserving the first displayed sort order. A one-use, session-scoped request context avoids duplicate GETs, handles early failures, and aborts/discards pending data on navigation or account disposal. Retry and refresh fetch again. No source scans, browser persistence, or extra runtime dependencies were added.

`profile-page-load.py` records HTML/JS/API waterfalls, first row paint, scripting/layout/style work, long tasks, and heap at readiness. Measurements use three cold Chromium contexts per case in the same offline 1 GiB/two-CPU environment and synthetic accounts. Fixed response delays model waiting; they are not measured server execution or bandwidth limits. The baseline is commit `3cb0f28`.

| Page and fixture | API delay | Asset delay | Before | After |
| --- | ---: | ---: | ---: | ---: |
| Library, 25 items | 80 ms | loopback | 404 ms | 335 ms |
| Library, 25 items | 200 ms | loopback | 763 ms | 557 ms |
| Library, 500 items | 80 ms | loopback | 743 ms | 660 ms |
| Library, 500 items | 200 ms | loopback | 1,113 ms | 903 ms |
| Item, 25-item account | 80 ms | loopback | 432 ms | 412 ms |
| Item, 25-item account | 200 ms | loopback | 786 ms | 765 ms |
| Library, 25 items | 80 ms | 80 ms | 558 ms | 502 ms |
| Item, 25-item account | 80 ms | 80 ms | 663 ms | 582 ms |

The largest improvement is removing one serial Library data wait: 17–27% in the small-library API-delay cases. Preloading route code improves item first-row paint by 12% with simulated asset latency; its sorted link query still follows preferences. At 500 items, rendering remains expensive: about 14,700 DOM nodes and 312–317 ms of long tasks. This change preserves all rows and existing selection behavior; it does not claim to solve large-list rendering. Main-page heap at readiness changed by roughly 40–110 KB, with no material memory reduction claim.

All 208 frontend tests, 5 tooling tests, ESLint, production build, profiler lint, and the offline final-image smoke test passed. New tests cover saved sorting, one-use requests, Trash, failure/retry, navigation cancellation, account changes, and StrictMode cleanup. See [compact measurements](../backend/ml/reports/page-loading.json); raw profiles remain ignored under `backend/data/frontend-review/load-*.json`.


## Footer and Library count

The Library heading now shows its current item count and limit together, for example `16/500`. The former limit sentence below the list is removed. Trash retains its own count, and the list's filtered-result count stays available.

The footer now belongs to the workspace on signed-in pages and a shared public layout on legal, sign-in, and recovery pages. It shares content gutters and widths instead of using hard-coded sidebar offsets. This corrects the clipped desktop sidebar and blank strip below it, and removes the unnecessary 61 px scroll on the empty desktop Library. Legal footer content matches the 720 px text column; sign-in footer content matches the 420 px form card. Bottom spacing, muted typography, 44 px link targets, keyboard focus, and current-page indicators provide a consistent secondary navigation area. Conflicting legacy footer styles were removed.

The reusable `scripts/footer-audit.py` captured 40 states across Library, empty Library, Item, Add, Settings, Privacy, Terms, Sign-in, Recovery, and email verification at 1440, 900, 390, and 320 px. Each has exactly one footer, no document overflow, and no unexpected page errors. Measurements confirm footer/content alignment at every size; desktop sidebar top remains at zero when scrolled to the footer. Before/after screenshots were visually inspected. The final build also adds `align-content: start` to the page-failure layout. Three additional failed-workspace screens at 1440, 390, and 320 px confirm one aligned footer and a normal 44 px Reload control, bringing visual coverage to 43 states. A final mobile header capture confirms `16/500` without clipping.

All 208 frontend tests, ESLint, production build, profiler lint, and the offline deployment-image smoke check passed. Loading tests now verify a single footer within the workspace. Screenshots and geometry reports remain ignored under `backend/data/frontend-review/footer-*-shots/`.


## Compact selection toolbar

Library and Item selection now share the search row. The closed Select control matches the 44 px search height; the selection count and bulk actions appear only during selection. Manual, page, all-matching, invert, pattern, range, merge, and restore operations remain available. The Smart/Quick search selector is removed, while semantic search remains the default. Empty Trash no longer repeats an explanatory sentence. Generated company/title labels use a middle dot; original source punctuation is preserved.

The isolated Chromium review uses 16 synthetic Library items and 200 links, checking 50 states across Library and Item toolbars, expanded filters, manual selection, selected rows, pattern controls, applied patterns, and empty Trash at 1440, 900, 390, and 320 px. Visual inspection caught a clipped mobile Select label and a squeezed search field at narrow desktop widths. The final layout reserves room for both controls and checks readable labels as well as alignment and overflow. Screenshots and the temporary harness remain ignored under `backend/data/frontend-review/select-release/`.

The final frontend suite passes 208 tests, with ESLint and the production build passing. The offline candidate-image smoke check also passes for accounts, reading progress, account export/deletion, disabled mail, and public assets. Backend coverage includes 43 import tests and 89 discovery/cache tests. HTTP failures now display the actual status code, and HTTP 406 receives the same shared cooldown protection as other access refusals.

A bounded check from the Azure host on September 20, 2026 received HTTP 406 with an empty body for both the supplied arXiv HTML search and one official API query. This proves rejection before parsing, but does not identify whether the cause is request filtering, IP reputation, or another upstream policy. No bypass or automatic alternate-query translation was added. arXiv-specific guidance points to its [documented machine interfaces](https://info.arxiv.org/help/robots.html) and file import if those are unavailable. A synthetic Atom regression fixture confirms abstract-page links and publication dates are parsed without fetching paper pages or PDFs; the incomplete-archive warning remains.
