# Shared scan cooldown and redirect reuse

September 16, 2026. Uses the existing `fetch-cache.sqlite3`; no additional service or database setup is required.

## Behavior

- Public responses and matching scan results are reused across libraries for **300 seconds**, including Full Refresh. Reading a cached result does not restart its timer. After the window, Full Refresh runs the full analysis again.
- Cache identity includes the normalized destination URL, meaningful query values, selection/filter options, source method, request budget and parser/model version. Different keywords or selectors keep separate results while sharing fetched public content. Reading progress, favorites, muted state and private library records are never placed in this cache.
- Verified HTTP redirects create source-to-destination aliases. Permanent redirects are remembered for at most one day; temporary redirects for five minutes. HTML canonical hints and guessed suffix/ID rules are not enough to merge sources.
- An unseen alias can require one redirect request. If the destination is already cached, its body and analysis are reused. Once the alias is known, it can reuse the scan without any network request.
- Durable SQLite leases coalesce simultaneous work, including separate cache/scanner instances using the same database. Redirect handoff releases the original scan lease, keeps the fetched document and request/byte budget, then coordinates on the destination. Responses are stored once under the destination rather than copied for every alias.
- Cooldown records are separate from evictable response bodies. Eviction, worker cancellation or restart cannot force an immediate refetch. If no reusable body/result remains, the caller gets the remaining retry delay. Cache database failure stops uncached work instead of fetching without coordination.
- Unknown source URL probes are limited to **20 per hostname per five minutes**, shared across accounts; `www.` and the bare hostname share this allowance. Random path suffixes and meaningful query mutations cannot create an unlimited redirect-probing workload. Known cached sources remain available. Existing account/IP/global admission limits and per-host pacing also apply.
- Private/no-store responses and their derived page/scan results are not cached. Credentials stay in a separate hashed request namespace and are never forwarded through redirects. Redirect destinations retain DNS pinning, public-address checks and the original request limits.

The cooldown, alias and host-probe tables each have a 10,000-record bound. Active cooldowns are not discarded to make space; admission fails when capacity is exhausted. Expired metadata is pruned. Expired owners cannot overwrite a new owner's lease. A source request has a 60-second wall-clock limit; collection work retains its three-minute limit and four concurrent outbound-request slots.

Account erasure clears response bodies, results, aliases and cached errors, and fences writes from scans started before erasure. Minimal URL/hostname hashes and timestamps for source-request limits survive for the cooldown window without account identifiers or full URLs; otherwise an attacker could reset protection through repeated deletion. The privacy notice documents this limited security retention. Application admission and host pacing still assume the documented single application process; these changes do not make the entire application ready for multiple hosts or protect the VM from network-level flooding.

## Why not use only Asura's suffix?

The requested two URLs were checked directly:

- `/comics/surviving-the-game-as-a-barbarian-6f7fe6e` redirects to `/comics/surviving-the-game-as-a-barbarian-6f7fe6eb`.
- `/comics/the-nebulas-civilization-6f7fe6eb` is a different valid series using **the same suffix**.
- `/comics/some-random-text-6f7fe6eb` did not resolve successfully.

Even a hostname-scoped `page:6f7fe6eb` would mix unrelated series. The implementation uses the user's alternative of a cleaned canonical URL, established by a verified redirect. It does not guess which arbitrary text can be stripped from an unfamiliar site's paths.

With the finished code, three separate scanners checked the real canonical URL, shortened URL and shortened URL again. They returned **the same 160 links and original checked timestamp**. There were exactly **two requests total: one destination fetch and one redirect probe**; the third scan made zero requests. This is one live observation, not a permanent promise about the publisher's layout. [Live result](source-cache-asura.json).

## Performance

The previous deployed image and candidate ran sequentially on the same VM, both with the native cascade enabled, in separate **2-CPU, 1-GiB, network-disabled containers**. HTTP used a mock transport with 10 ms per request; databases and actual discovery/model code were real. The primary fixture has 250 dated chapters. This isolates cache behavior; it is not an end-to-end latency prediction for a live publisher.

| Check | Previous | Candidate |
| --- | ---: | ---: |
| Repeated Full Refresh, median of four | 902.7 ms | 2.4 ms |
| Analyses across initial scan + four Full Refresh calls | 5 | 1 |
| Initial URL + three new aliases + three repeated aliases, destination fetches | 4 | 1 |
| Same alias scenario, total HTTP requests | 7 | 4 |
| Same alias scenario, analyses | 7 | 1 |
| Four simultaneous scanner instances, HTTP requests / analyses | 4 / 4 | 1 / 1 |
| Already-cached 4,999-entry result, median of 15 | 25.5 ms | 25.3 ms |
| Already-cached 4,999-entry result, observed maximum | 32.8 ms | 35.6 ms |
| Whole isolated benchmark container peak | 60.8 MiB | 51.6 MiB |

All compared entry lists were identical. The repeated-refresh improvement comes from avoiding redundant work, not a faster classifier. Ordinary warm-cache latency remained essentially unchanged. The single cold scan took 970 ms before and 988 ms after; the extra coordination is modest here, but one cold sample does not establish a precise regression percentage.

Profiles show that large cache hits still spend most of their CPU work decoding JSON and reconstructing independent entries. Alias lookup is a small indexed lookup and does not load a second copy of the page. Benchmark database files totaled 2.56 MB before and 2.38 MB after, since aliases no longer duplicate results. Peak figures describe this controlled benchmark, not total production application/database/browser memory.

Raw results and cumulative-time profiles: [baseline](source-cache-baseline.json), [candidate](source-cache-candidate.json). The [final image check](source-cache-release.json), including the erasure safeguard, retained the same request/analysis counts and returned the 4,999-link cache hit in 25.6 ms median, peaking at 54.2 MiB in this benchmark. The checked-in tool, `ml/benchmark_source_cache.py`, accepts `--app-root`, `--profile` and `--output`, allowing the same harness to run against either image. Frozen model weights and test HTML remain the same.

## Verification

The final full backend suite passed **732 tests**, including 24 cache tests. Interface coverage passed **42 tests**, and the final privacy tests and production frontend build succeeded. New tests cover concurrent libraries, restart persistence, non-sliding expiry, redirect alias reuse in either order, random-suffix admission, suffix collisions, distinct query/filter/credential namespaces, missing bodies, private responses, negative results, cancellation, crash recovery, bounded metadata, erasure and cache-database failure. Existing tests that deliberately expired only scan payloads now advance the cooldown clock as well.

For a local comparison from `backend`:

```sh
TRACKER_LINK_MODEL=cascade uv run python ml/benchmark_source_cache.py --profile --output data/source-cache-profile.json
TRACKER_LINK_MODEL=cascade uv run pytest -q
```

Use the deployment container with `--network none --memory 1g --cpus 2` for comparable memory measurements. No benchmark visits media content pages or changes account data.
