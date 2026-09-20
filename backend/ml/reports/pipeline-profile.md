# Scraping pipeline profile - September 19, 2026

The changes target measured DOM, URL, and date preprocessing costs. Production
classifier weights, thresholds, candidate limits, scrape budgets, and source
pacing are unchanged. All measurements below use previously saved public HTML;
no new website requests or private training data were required.

## Controlled parser results

The baseline is commit `e6ce33f`. Both revisions ran sequentially in isolated,
network-disabled containers on the Azure VM, each limited to 1 GiB RAM and 2 CPUs.
The same existing native model kernel, Python environment, and 56 captured pages
were used, with three parser runs per page. The reported duration is the sum of
per-page medians, not one user's refresh latency. This isolated workload excludes
live HTTP, the browser sidecar, API account traffic, and the semantic encoder.

| Measurement | Before | After |
| --- | ---: | ---: |
| Sum of 56 parser medians | 42.870 s | 29.320 s |
| Container peak memory | 119.7 MiB | 115.5 MiB |
| Complete scan outputs changed | 0 | 0 |
| Extracted records | 11,517 | 11,517 |

Parser time fell **31.6%**, with **3.5% lower peak memory** in this workload.
All 56 complete outputs matched exactly: entries and their titles, dates, context,
order and provenance; scan metadata, warnings, suggestions, pagination and feeds.
The captures include media archives, application tables, news cards, nested
records, and long technical indexes. Existing weak-label/acquisition limitations
remain; unchanged output does not establish universal extraction accuracy.

Selected per-page medians:

| Saved source | Before | After |
| --- | ---: | ---: |
| Asura | 0.449 s | 0.306 s |
| Royal Road | 0.591 s | 0.316 s |
| GitHub applications | 1.407 s | 0.899 s |
| xkcd archive | 3.757 s | 2.438 s |
| Python Bytes | 1.984 s | 1.022 s |
| W3C standards | 8.652 s | 6.652 s |
| ACL Anthology | 3.588 s | 2.419 s |

## Full refresh and semantic deployment

A separate replay exercised fetching/cache behavior, host pacing, the analysis
queue and worker processes, merging, account item reads, and semantic enrichment
using a temporary copy of the existing 14-source library. It made no external
requests: saved responses simulated 50 ms source latency, with normal host
pacing. Browser fallback followed captured failure sequences; this does not
measure a fresh browser navigation or variable live anti-bot delays. Stage times
overlap, so their sums are not refresh wall-clock time.

| Full refresh measurement | Before | After |
| --- | ---: | ---: |
| Cold replay wall time | 27.381 s | 27.113 s |
| Warm replay wall time | 0.436 s | 0.479 s |
| Analysis worker CPU time | 13.059 s | 9.853 s |
| Longest analysis job, including queue | 2.273 s | 1.712 s |
| Full-workload peak memory | 299.8 MiB | 296.1 MiB |
| Cold / warm source requests | 25 / 0 | 25 / 0 |

All 2,597 records and request counts matched exactly. Worker CPU use fell
**24.5%**, but the roughly 27-second cold critical path remains dominated by
per-host pacing and acquisition waits. The observed cold difference is too small
to claim a sustained wall-time improvement; the 43 ms warm difference is ordinary
single-run variation. Peak analysis queue delay was 0.609 s afterward. Merge and
item-read medians were about 20 ms, and semantic enrichment about 1.3 ms, because
unchanged descriptions/vectors reuse saved work. No OOM or memory-limit events
occurred. Additional worker parallelism or more aggressive scraping would not
remove the paced source path and is not enabled.

`semantic_model.py` now hashes the 23 MB model file incrementally instead of
allocating a complete file-sized buffer. Masked mean pooling uses NumPy's compiled
`einsum` reduction with float64 accumulation, retaining the prior numeric result
while avoiding a batch-by-token-by-dimension temporary array. The existing CPU
ONNX model, token limits, batch limits, checksum checks and finite-value validation
remain in place.

| Semantic operation | Before | After |
| --- | ---: | ---: |
| Pooling microbenchmark | 4.054 ms | 1.225 ms |
| Pooling peak temporary allocation | 4,848,960 bytes | 155,115 bytes |
| Model checksum peak allocation | 23,055,455 bytes | 271,537 bytes |

The 47-document and 34-query replay produced exactly equal vectors and unchanged
top-three search ordering. Complete document encoding was essentially unchanged
(2.883 vs 2.853 s), since ONNX inference dominates that operation. The recorded
load timings have different filesystem/import warmth and must not be used as a
startup speed claim. Allocation measurements are operation-specific, not total
process or model memory.

## Code changes and evidence

- `parser.py`, `dom.py`, `list_entries.py`, and `pagination.py` now use direct
  document-order tag/ancestor walks for fixed, simple predicates. Arbitrary
  user CSS selectors still use the existing selector engine. Numbered/directional
  pagination labels are checked before walking ancestors. Automatic list
  candidates stop at their existing limit instead of constructing a complete
  match list before slicing it. BeautifulSoup matching calls fell from 300,532
  to 123,369; CSS selection invocations fell from 11,263 to 3,007.
- `urls.py` memoizes pure canonicalization within one synchronous HTML analysis.
  Actual normalizations fell from 207,230 to 65,375. The cache has both a
  4,096-entry and 2,000,000-character budget, separates bases and slash policies,
  and is discarded even when parsing raises. Oversized inputs bypass cache-key
  hashing and retain the original rejection. It caches no DNS decisions, HTTP
  responses, or authorization results; network/SSRF checks remain independent.
- `list_entries.py` reuses the existing per-page date cache. Consumers receive
  copied evidence so assigning provenance cannot alter another record's date.
- `models.py` routes strictly recognized ISO dates through CPython's C-backed
  `datetime.fromisoformat`, retaining dateutil for other formats and fallback.
  Absolute timestamps, date-only values, timezone handling, invalid inputs, and
  the rejection of relative/bare-number dates are covered by parity tests.
- `keywords.py` precomputes the fixed language alias dictionary. Runtime Unicode
  normalizations fell from 658,511 to 8,130, preserving language-family matching,
  Unicode normalization, aliases and their original ordering.

Parser-only `cProfile` runs identify remaining local CPU work as record-boundary
context extraction and link-feature preparation. The link cascade itself used
about 1.1% of the baseline's instrumented parser time and 1.6% after optimization.
Cumulative function times overlap, and instrumentation inflates traversal costs;
use the uninstrumented VM medians above for speed comparisons. The existing native
C inference already addresses the small scoring stage. Rewriting it or changing
model weights would not resolve the dominant DOM work.

Native and Python scoring were also compared on all 25,550 frozen feature rows:
zero threshold decisions changed, with maximum probability error 2.22e-16.
No additional C dependency or HTML parser replacement was introduced.

## Verification and reproduction

Tests cover nearest-ancestor/class semantics, label document order, bounded URL
memoization and invalid inputs, cleanup after exceptions, shared date ownership,
ISO date equivalence, multilingual aliases, custom selectors, pagination, learned
recipes, identity/deduplication, cache safety and worker limits. Focused suites
passed, followed by the integrated project checks: **944 backend tests and 150
frontend tests**. Ruff also passed.

From `backend`, with the original saved public captures present:

```sh
python ml/verify_model_review.py --app-root /baseline/backend --repeats 3 --output data/before.json --outputs data/outputs-before.json
python ml/verify_model_review.py --repeats 3 --output data/after.json --outputs data/outputs-after.json
python ml/verify_model_review.py --repeats 1 --profile data/parser.prof --output data/profile.json
python ml/verify_model_review.py --inference-only --output data/model-parity.json
```

For container measurements, run the first two commands in separate containers
with `--network none --read-only --cpus 2 --memory 1g`, mounting captures and code
read-only and only the report directory writable. The reports include capture
hashes and full scan hashes; compare those before comparing timing. The script
records cgroup peak memory. Python profiles remain local; summarized function
counts are committed below. Re-fetching sources produces a different benchmark.

Reports: [VM before](pipeline-profile-before.json),
[VM after](pipeline-profile-after.json),
[CPU before](pipeline-profile-cpu-before.json),
[CPU after](pipeline-profile-cpu-after.json),
[model parity](pipeline-profile-model-parity.json),
[full deployment replay](deployment-pipeline.json),
[semantic runtime](semantic-runtime.json).
