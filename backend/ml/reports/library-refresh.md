# Library refresh profile — September 17, 2026

The deployed library workload contained 14 refreshable sources. Muted items and
document imports remained excluded. Measurements used the existing two-CPU,
1 GiB application limit, four library scan producers and two analysis workers.
No model weights, source request intervals or scan coverage limits changed.

## Findings and changes

- A bare **Show more** button expanding a synopsis was mistaken for pagination.
  Two fully available chapter inventories unnecessarily entered browser rendering
  and requested stylesheets and application scripts. Detection now checks the
  control's explicit target or a bounded local prose region. Explicit listing
  labels, ambiguous controls and missing inventory entries remain eligible.
- A changed source page exposed **Next page** for reader reviews beside an already
  available chapter table. Pagination in an explicit comments/reviews region is
  excluded only when the tracked linked entries are all available outside it.
  Controls remain eligible for explicit selectors, linkless entries and tracked
  entries that are only present inside the reviews region.
- Hash-striped item and scan locks sometimes serialized unrelated sources. Locks
  now use exact keys and retain only active holders/waiters. Cancellation,
  exception cleanup and cross-account isolation have regression coverage.
- Cached responses no longer wait behind another same-host download. Identical
  URLs still share work, actual requests retain the host gate and two-second
  spacing, and queued requests check newly established host backoff.

The frontend already commits each streamed item event immediately, with no
per-item library refetch. In the original live profile, merges took 7–68 ms and
semantic enrichment was usually about 1 ms. The analyzer had no material queue
wait. Network scheduling and unnecessary rendering were the useful targets.

## Controlled comparison

Both versions ran the same private, captured listing responses in separate
network-disabled containers with identical resource limits. Requests used the
normal fetch/cache/discovery/merge code, 50 ms simulated response latency and the
existing two-second host interval. Captured failed browser resource sequences
were replayed; sources without a renderer capture remained unavailable to the
renderer. This is a controlled workload, not a browser performance benchmark.

| Measurement | Before | After |
| --- | ---: | ---: |
| Cold complete refresh | 66.24 s | 27.40 s |
| Simulated source requests | 46 | 25 |
| Cached complete refresh | 0.52 s | 0.44 s |
| Cached source requests | 0 | 0 |
| Peak container memory | 302.7 MiB | 296.5 MiB |

All 2,597 extracted entries across the 14 sources matched exactly, including every
field, date and ordering position. Neither container recorded an OOM event. The
cached timing difference is small run-to-run variation, not an improvement claim.
The lock helper added about 4 microseconds per uncontended operation in a separate
local microbenchmark and retained no keys after 50,000 distinct operations.

The original live profile took 67.36 seconds and made 45 requests. The final
deployed version took **27.83 seconds with 24 requests**, a 59% reduction in elapsed
time. Both profiles used a temporary copy of the same account's library on the
VM, the production shared source cache and ordinary request limits. Reading
progress was not changed by the profiler. The final run checked all 14 eligible
items successfully, with no new links or failed scans. Royal Road's revised page
required one listing request and preserved all 109 chapter records.

Actual sites change content and response latency between measurements; the
controlled replay provides the stronger output-equivalence comparison. Raw
captures, source URLs, library snapshots and account identifiers stay in ignored
private operational storage rather than this report.

## Safeguards and remaining limits

Five-minute shared response reuse, verified redirect aliases, DNS pinning,
request/byte budgets, source backoff, full pagination coverage, immediate streamed
updates and read/favorite/mute/Trash state are preserved. Parser cache versioning
discards obsolete pagination decisions without bypassing the HTTP cooldown.

The remaining cold refresh time is dominated by multiple sources sharing a host
and saved URLs that redirect. Requests continue to observe the two-second host
interval. Increasing model workers would not remove that wait and would consume
more memory. Repeating a refresh inside the shared cooldown uses cached results
where the source permits caching. Responses marked `private` or `no-store` and
their derived scans remain unshared; immediate retries can therefore be deferred
until the five-minute cooldown expires. This existing restriction also affected
an immediate UI recheck after profiling; it is not included in the simulated
all-cacheable warm timing above.

Regression coverage includes synopsis controls, real load-more controls, partial
inventories, auxiliary review pagination, shared Python/browser control fixtures,
hash collisions, lock cancellation and cache hits during slow downloads.

The final backend suite passed 871 tests; the frontend suite passed 141 tests,
including the worker's pagination selector fixtures. Backend Ruff and frontend
ESLint passed. Public health checks returned 200, and anonymous library access
remained protected with HTTP 401.
