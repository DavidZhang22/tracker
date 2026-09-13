# Refresh performance

Measured on September 13, 2026 against baseline `9a77788`, on the Azure VM in
isolated containers limited to **one CPU and 512 MiB RAM**. All libraries were
disposable. Raw results and cProfile summaries are in `refresh-benchmark.json`.

| Operation | Before | After |
| --- | ---: | ---: |
| Parse 1,000 nested chapter records | 6.00 s | 2.05 s |
| Parse Royal Road fixture, 109 chapters | 443 ms | 211 ms |
| Refresh unchanged 1,000-record HTML after complete-scan cache expiry, excluding network | 6.25 s | 31 ms |
| Longest event-loop delay during that scan replay | 6.20 s | 10 ms |
| Merge 4,999 unchanged saved links | 348 ms | 109 ms |
| Write a small cache entry with 55 MB already cached | 46 ms | 13 ms |
| Peak RSS across the offline benchmark process | 221 MiB | 67 MiB |

Parsing/database numbers use the median of three runs; cache writes use ten.
Event-loop delay and RSS are single-run observations, not latency guarantees.
The cProfile pass is separate from wall-time samples because profiling adds overhead.
The 31 ms unchanged result still checks the source in the replay: it is not a longer
HTTP TTL. Real DNS, network, rate limiting and pagination must be added to it.

## Real source check

A single paced scan captured SimplifyJobs/New-Grad-Positions and the supplied
NovelsHaven series. There were five HTTP requests total, including one redirect;
no job applications, chapter bodies or images were fetched. The candidate replayed
those exact responses offline, so comparisons do not require scraping them again.

* GitHub: **911 links**, parsing **10.23 s → 4.78 s**. The original live scan also
  spent 4.28 s fetching, for 14.74 s overall. The candidate's 5.04 s offline replay
  excludes network; it must not be reported as a live end-to-end speedup.
* NovelsHaven: **221 chapters**, parsing **142 ms → 107 ms**. Its live request took
  1.37 s, making source response time the main remaining cost for this example.

All five benchmark fixtures (nested records, jobs, Royal Road, Hacker News and xkcd)
and both real captures produced identical output hashes. These hashes include
URLs, titles, dates, ordering, context, languages, suggestions, and scan warnings.
This verifies these cases, not universal extraction accuracy or source availability.

## What changed

1. **HTML traversal and model preprocessing.** Sibling statistics are computed once
   per parent within the same 80-child window. Accessible IDs are indexed once.
   Suggestion extraction rejects ineligible URL shapes before examining ancestors,
   and caches repeated ancestor markers. Simple date-tag traversal avoids repeated
   CSS selector evaluation. Both link classifiers reuse their common feature vector
   and table context. Identical record vectors reuse exact neural predictions in a
   page-local cache capped at 2,048 entries. Weights, thresholds and input features
   are unchanged; production still imports no NumPy, sklearn or torch.
2. **Reuse after revalidation.** A bounded parsed-page cache keys on the response
   body hash, final URL, selector, path restriction and parser/model versions.
   Unchanged HTML can reuse extraction after a fresh HTTP check or 304. Changed
   content or extraction options trigger parsing. Keyword filtering remains after
   parsing and each hit reconstructs independent results. Increment
   `DISCOVERY_VERSION` when changing parser semantics; model IDs invalidate their
   own cached results.
3. **Responsive requests.** Parsing, cache I/O and refresh database work run in
   bounded scan workers, allowing HTTP requests and completed item events to proceed.
   Cancellation retains admission and locks until the worker actually finishes,
   including Starlette's cancellation scopes. A disconnected client cannot start
   unlimited abandoned workers. This improves responsiveness; threads do not promise
   parallel speedups for Python CPU work.
4. **Less database work.** Refresh selection reads item preferences without counting
   every saved link. One snapshot supplies existing links for a merge; unchanged rows
   are not rewritten. Missing entries retain their source neighbors, better dates
   survive weaker metadata, and read/favorite/ignored/trash state stays intact.
5. **Cache storage.** Eviction reads a covering index of timestamps and stored byte
   counts instead of scanning every HTML/JSON body on each write. Connections close
   promptly. In-memory eviction tracks its total size incrementally. SQLite WAL
   allows readers while writes occur. The combined HTTP/scan/parsed cache remains
   capped at 64 MB and 1,000 records on disk, with seven-day pruning on writes.

Two library workers and three global scan slots remain. Preview scans now share
the same global slots. Source pacing remains two seconds; HTTP/complete scan cache
freshness remains ten minutes. Conditional requests, source backoff, SSRF protection,
request/byte/time limits, storage caps, the eight-second addition cooldown, and
database failure responses remain in force. Ignored items and Trash remain outside
bulk refresh. No frontend layout changes or external services were needed.

## Reproduce

From `backend`, with the existing environment activated:

```sh
python ml/benchmark_refresh.py --output data/refresh.json --profile
python ml/verify_refresh_runtime.py
python -m pytest -q
```

The runtime verifier uses real HTTP and a blocked parser worker. It checks that
library reads and a fast item's update arrive while the slow parser runs, that
disconnects retain admission until work stops, that cancelled items are not merged,
and that another refresh works afterward. It makes no source requests.

For an explicit, occasional public source profile:

```sh
python ml/profile_sources.py --capture data/captures.json --output data/live.json https://github.com/SimplifyJobs/New-Grad-Positions
python ml/profile_sources.py --replay data/captures.json --output data/replay.json https://github.com/SimplifyJobs/New-Grad-Positions
```

Keep raw captures untracked. Run the replay on the baseline and candidate with the
same runtime and resource limits. Inspect `fetch_s` separately from `parse_s` before
changing concurrency. Long paginated archives and YouTube continuations can still
spend most of their time waiting for source requests; no speedup is claimed for
uncaptured sites. Connection pooling or incremental archive scans should follow
source-specific measurements and coverage tests, not a reduction in pacing or history.

## Deployment and rollback

No account/library schema change or new dependency setup is required. Cache startup
adds a byte-size column and covering index, preserving existing response bodies.
Back up account/library databases before deployment. The previous image expects
the old three-column cache, so a rollback must also restore its pre-release cache
snapshot (or recreate only the disposable fetch cache) after stopping the app.
Never restore or delete user libraries just to invalidate a cache.

The release verification also covers the existing storage outage, quotas,
pagination, ordering, date, language, context and mobile frontend contracts.
