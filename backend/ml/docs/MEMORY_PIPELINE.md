# Response memory and refresh throughput

The refresh pipeline keeps listing responses compressed until a parser worker is ready. It preserves the entire source document, including hidden links, sibling context, language attributes, dates, and embedded metadata. No content pages are fetched to reduce or enrich that document.

## Processing and bounds

1. `SafeFetcher` admits at most four HTTP operations, after taking the host lock and respecting its request interval. Redirects still require validated public DNS and count toward the request budget. Each source keeps its two-second pacing and backoff.
2. Wire gzip/deflate is retained. Uncompressed responses use gzip level 1 only when it reduces size. Validation and hashing decode bounded 64 KiB chunks. Both wire and expanded source bodies remain capped at 8 MB; malformed compression and expansion bombs fail before parsing. UTF-8 conversion and rendered Markdown have a separate 24 MB text cap.
3. At most six scans run globally, four per library. Waiting scans hold compressed documents; two reusable analysis workers admit jobs before submitting to the process pool. GitHub HTML preparation and Markdown rendering share those workers. The bounded scan producers provide backpressure instead of filling an unbounded executor queue. Disconnected requests retain admission until active worker jobs finish.
4. Workers decode on admission, extract links and record context, then explicitly release DOM children. Initial recipe validation reuses the same read-only tree and still compares the entire result. Subsequent refreshes validate the recipe against a newly fetched document. Simple model feature lookups use direct DOM walks equivalent to the original Beautiful Soup queries.

Refresh scheduling favors hosts with fewer active items, allowing a fast source to complete while another host waits. Each item is committed and emitted immediately. Trash and muted items remain excluded from refresh-all. Existing API authorization, account quotas, request/byte limits, 4,999-link caps, and deletion safeguards remain in place.

Item and scan locks use exact keys, retaining only active holders and waiters, so
unrelated sources cannot block each other through hash collisions. Cached responses
bypass the network host gate; actual requests still keep the two-second interval.
The [library refresh profile](../reports/library-refresh.md) measures the effect
of these changes and avoiding browser scans for unrelated expansion controls.

Compressed HTTP responses use a new cache-key namespace. Existing cached responses migrate without refetching; conditional requests and 304 handling still work. The previous application can ignore this namespace during rollback. The text digest stays identical, so parsed-page caches and existing recipes do not need global invalidation. Cache writes continue to respect account-deletion generation checks.

## Verification

`benchmark_memory.py` exercises actual fetch, discovery, process-worker, refresh scheduling, and database-merge code using disposable storage and mocked network responses. It runs offline in fresh containers with `--memory 1g --cpus 2 --network none`. The mixed workload has four slow items sharing a host and four other hosts, twelve requests including redirects, two-second per-host spacing, and 400 ms simulated response latency. The stress workload adds 6 MB of script data to each 4,999-link page. A separate run checks two simultaneous libraries.

The profile mode replays nested records and saved public GitHub/NovelsHaven captures. It compares complete scan output and classifier feature/token hashes. Weights and thresholds are unchanged. These measurements describe the tested workloads; they are not live-site latency guarantees or a proof about every possible HTML layout.

Compared with revision `15b6d10` on September 15, 2026:

| Offline workload | Before | After |
| --- | ---: | ---: |
| Eight 500-link items: total | 19.96 s | 16.27 s |
| First result from another host | 15.00 s | 2.33 s |
| Eight 4,999-link items + 6 MB/page: total | 89.85 s | 79.23 s |
| Same stress workload: peak container memory | 526.6 MiB | 453.6 MiB |
| 1,000 nested records: median parsing | 1.99 s | 1.70 s |
| GitHub README, 911 jobs: median parsing | 5.02 s | 4.40 s |

The two-library stress run peaked at 437.5 MiB. Every run stayed within the 1 GiB limit with zero OOM events and identical extracted-entry hashes. Both versions made the same twelve simulated source requests. Small pages have little response memory to save: the 500-link workload used 189.4 MiB before and 185.5 MiB after. Increased concurrency is bounded even when compression saves little space.

Profiles showed repeated DOM queries, record context, and date correlation dominate CPU time. Direct feature walks reduced parsing time without changing any feature/token hashes or model output in the replayed cases. NovelsHaven's structured 221-chapter extraction remained about 0.11 s. Source pacing/latency and complex DOM/date traversal remain the largest remaining delays; this change does not lower the host interval or skip older archive pages.

Full timings, hashes, process diagnostics, and cProfile tables are in `reports/memory-benchmark.json`.

```sh
PYTHONPATH=backend python backend/ml/benchmark_memory.py --links 500
PYTHONPATH=backend python backend/ml/benchmark_memory.py --links 4999 --padding 6000000
PYTHONPATH=backend python backend/ml/benchmark_memory.py --links 4999 --padding 6000000 --libraries 2
PYTHONPATH=backend python backend/ml/benchmark_memory.py --mode profile --links 1000
```

Tests cover compressed/plain output equivalence, chunk boundaries and encodings, malformed caches, compression bombs, legacy/304 cache behavior, actual process preparation, admission and cancellation, immediate DOM cleanup, feature-walk equivalence, host fairness, and network concurrency.
