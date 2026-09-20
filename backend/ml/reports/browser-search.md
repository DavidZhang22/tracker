# Browser search, 2026-09-20

The Library can use **Quick search** for local title, URL, description, filename and hidden-tag matching, including one-character typos and transposed letters. **Smart search** keeps the existing server semantic model and displays current local matches while the semantic request is pending. Selection stays disabled until the chosen search mode has finished, so a moving result set cannot silently change a bulk operation.

Libraries with at least 50 items build and query their lexical index in a dedicated browser worker. Smaller libraries avoid worker startup overhead. The production worker bundle is about 2.2 kB uncompressed. It receives searchable metadata only; reading state is not copied. There is no model download, cross-origin fetching, disk cache or persistent browser storage. Each component owns its worker; leaving the Library or signing out terminates it. A blocked, failed or unresponsive worker falls back to the same bounded local index. Superseded requests and late responses cannot replace the current query. Semantic refinement has an eight-second deadline; if it stalls, search settles on the current local matches and re-enables selection. Malformed worker responses also fall back locally.

The index uses inverted token postings, word-length buckets for typo candidates, packed typed-array posting lists and numeric accumulators and a 24-query memory cache. Its metadata revision ignores progress, favorite and mute changes, so those updates neither rebuild the worker/index nor invalidate completed semantic searches. Equivalent Unicode, case and whitespace queries share a semantic request. Single-character prefixes stay local; two-character concepts such as AI still use semantic search. Quick search makes zero `/search` requests; Smart search remains the default.

## Measurements

Windows, Node 24.11.1, median of nine iterations. Synthetic public-style tracker metadata covers eight media topics. 144 queries include exact titles, descriptions, multiword concepts, typos and URL fragments. The 200-item row is the current account limit; 2,000 items is only a stress test. Timings measure the pure lexical implementation, excluding browser rendering, worker startup/transfer, HTTP and the semantic model.

| Library size | Build before → after | 144 distinct queries before → after | 400 repeated queries before → after |
| --- | --- | --- | --- |
| 50 | 2.82 → 1.90 ms | 3.57 → 2.15 ms | 5.85 → 0.40 ms |
| 200 | 7.50 → 5.74 ms | 8.77 → 5.59 ms | 18.66 → 0.38 ms |
| 2,000 (stress only) | 65.99 → 63.53 ms | 201.78 → 79.34 ms | 668.20 → 0.41 ms |

At the supported limit, building the index is about 23% faster, distinct-query ranking is about 36% faster, and repeated queries benefit substantially from caching. These small absolute times are not an end-to-end refresh or server-inference speedup. Worker isolation moves the index work off the browser UI thread. Search results had exactly equal scores and membership for all benchmark queries and sizes. The 200-item worker metadata payload is approximately 108 kB; this is not an estimate of total browser memory.

Regression tests cover query normalization, zero-request Quick search, current local results, stale semantic responses, unchanged-index reading updates, metadata invalidation, worker errors/timeouts, cancellation and termination. The production build emits the worker as a same-origin static asset compatible with the existing CSP.

Reproduce from `frontend`, supplying the previous revision's `frontend/src/media.js` as an `.mjs` file:

```sh
node scripts/benchmark-browser-search.mjs /path/to/baseline.mjs ../backend/ml/reports/browser-search.json
```

Raw measurements: [browser-search.json](browser-search.json).


