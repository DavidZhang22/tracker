# Browser search, 2026-09-20

This remeasurement compares the previously shipped browser-search implementation with its historical pre-worker baseline at the expanded 500-item limit. It is not a new search implementation or an additional speedup delivered by this release.

The Library can use **Quick search** for local title, URL, description, filename and hidden-tag matching, including one-character typos and transposed letters. **Smart search** keeps the existing server semantic model and displays current local matches while the semantic request is pending. Selection stays disabled until the chosen search mode has finished, so a moving result set cannot silently change a bulk operation.

Libraries with at least 50 items build and query their lexical index in a dedicated browser worker. Smaller libraries avoid worker startup overhead. The production worker bundle is about 2.2 kB uncompressed. It receives searchable metadata only; reading state is not copied. There is no model download, cross-origin fetching, disk cache or persistent browser storage. Each component owns its worker; leaving the Library or signing out terminates it. A blocked, failed or unresponsive worker falls back to the same bounded local index. Superseded requests and late responses cannot replace the current query. Semantic refinement has an eight-second deadline; if it stalls, search settles on the current local matches and re-enables selection. Malformed worker responses also fall back locally.

The index uses inverted token postings, word-length buckets for typo candidates, packed typed-array posting lists and numeric accumulators and a 24-query memory cache. Its metadata revision ignores progress, favorite and mute changes, so those updates neither rebuild the worker/index nor invalidate completed semantic searches. Equivalent Unicode, case and whitespace queries share a semantic request. Single-character prefixes stay local; two-character concepts such as AI still use semantic search. Quick search makes zero `/search` requests; Smart search remains the default.

## Measurements

Windows, Node 24.11.1, median of nine iterations. Synthetic public-style tracker metadata covers eight media topics. 144 queries include exact titles, descriptions, multiword concepts, typos and URL fragments. The 500-item row is the current account limit; 2,000 items is only a stress test. Timings measure the pure lexical implementation, excluding browser rendering, worker startup/transfer, HTTP and the semantic model.

| Library size | Build before → after | 144 distinct queries before → after | 400 repeated queries before → after |
| --- | --- | --- | --- |
| 50 | 1.05 → 0.81 ms | 2.07 → 1.33 ms | 3.14 → 0.24 ms |
| 200 | 3.80 → 2.64 ms | 4.83 → 3.32 ms | 11.75 → 0.20 ms |
| 500 | 9.18 → 6.26 ms | 13.20 → 6.93 ms | 29.55 → 0.19 ms |
| 2000 (stress only) | 33.64 → 29.57 ms | 58.03 → 31.36 ms | 150.20 → 0.19 ms |

Against the historical baseline, the current implementation builds a 500-item index about 32% faster and ranks distinct queries about 48% faster. These differences were delivered by the earlier search change; this release only remeasures the expanded limit. Warm repeated queries benefit substantially from the bounded cache. These small absolute times are not an end-to-end refresh or server-inference speedup. Worker isolation moves the index work off the browser UI thread. Search results had exactly equal scores and membership for every benchmark query and size. The 500-item worker metadata payload is approximately 271 kB; this is not an estimate of total browser memory.

Regression tests cover query normalization, zero-request Quick search, current local results, stale semantic responses, unchanged-index reading updates, metadata invalidation, worker errors/timeouts, cancellation and termination. The production build emits the worker as a same-origin static asset compatible with the existing CSP.

Reproduce from `frontend`, supplying the previous revision's `frontend/src/media.js` as an `.mjs` file:

```sh
node scripts/benchmark-browser-search.mjs /path/to/baseline.mjs ../backend/ml/reports/browser-search.json
```

Raw measurements: [browser-search.json](browser-search.json).
