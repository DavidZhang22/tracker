# Application safeguards

These bounds apply on the server; hiding controls or changing browser requests
cannot raise them. No additional service or database is needed.

| Resource | Bound |
| --- | --- |
| Links per scan preview and saved item | 4,999 |
| Links per account library | 100,000 |
| Items per account library | 500 |
| Saved scan previews per account | Latest 5, valid for one hour, up to 8 MB each |
| Active scan operations | One per account, three across the process |
| Scan allowance | 500 items per account, 600 server-wide; tokens replenish over an hour |
| API allowance | 240 requests per IP, 1,200 server-wide; tokens replenish over a minute |
| In-flight API requests | 64 |
| API request body | 64 KiB, including chunked requests; 10-second arrival deadline |
| Collection scan | 40 fetch requests, 32 MB of response content total, three-minute deadline |
| Individual source response | 8 MB after decompression |
| Repeated source requests and matching scans | Reused for five minutes across libraries, including Full Refresh |
| Unknown source URL probes | 20 per hostname per five minutes, shared across accounts |
| Sandboxed JavaScript pagination | Up to 20 steps, 40 resources, 50 seconds, 5 MB total snapshots |
| Refresh all | Up to four workers per account with host pacing, stops after ten minutes and reports remaining items |

Ignored records and Trash count toward storage limits. Existing libraries that
already exceed a new limit are preserved; further growth is blocked. Refreshes
still update known links and preserve IDs, reading progress, favorites, and
deletion state. Source warnings explain when additional links were skipped.
Changing these limits is an operator action in `backend/app/tracker/limits.py`;
back up data before changing retention behavior. Dismissing a notice only hides
it in the current view; technical scan metadata remains in API responses. Routine notes are omitted from saved item views.

Every member of Refresh all consumes one scan token, even if cached. Excess work
returns HTTP 429 with `Retry-After`; it is not put into an unbounded queue.
An oversized request returns 413 before authentication/body parsing. Slow bodies
return 408. Preview pruning bounds stored payloads; SQLite reuses its allocated
pages rather than shrinking the file on each prune. Total response-size checks
also apply to cached pages. The YouTube archive adapter separately uses a
4,999-link output cap, a 120-second subprocess timeout and paced metadata requests;
its internal HTTP requests are not part of the fetcher's 40-request/32 MB budget.

Existing protections remain: rate-limited public or invite-only registration, login/password attempt
limits, isolated account databases, HTTPS cookies and origin checks, private
network/metadata-address rejection and DNS pinning on each fetch redirect,
five-minute shared source caching, verified redirect aliases, source pacing, refusal backoff, bounded cache storage,
and database failure handling. Application admission limits are in memory and
reset after restart. They assume the deployed single application process; use a
shared limiter before adding workers or replicas. They do not provide protection
against traffic that exhausts the VM's network connection.

Source cooldowns, in-flight leases, redirect aliases and unknown-source probe
counts use the existing SQLite cache and survive restart. A cached destination is
not downloaded again when a new alias redirects to it. Previously unseen aliases
can require one redirect probe, bounded by the hostname limit. A suffix alone is
not an identity: Asura serves different series with the same suffix. Query values
that may select different content are preserved. Full Refresh can run the model
again after the five-minute window; it cannot override network or scan cooldowns.
Changed filters have separate results but reuse the public response.

Evicting a response does not remove its cooldown. If the body is unavailable,
Trackify asks the user to retry after the remaining delay. Cache database failures
stop uncached work. Private/no-store responses are not shared or retained as
derived scans. Existing user progress and favorites remain in account libraries.
See [cache design and profiling](../backend/ml/reports/source-cache.md).

Source requests identify themselves as
`Trackify/1.0 (+https://mediatrackify.duckdns.org; contact: mediatrackify@gmail.com)`.
The same identity is used by HTTP fetching, browser contexts, and sitemap robots
matching. It identifies the service and its public contact without impersonating
another browser or crawler. The optional YouTube archive extractor receives this
as its default user agent; yt-dlp may override it for client-specific API requests.
That extractor is disabled by default.

JavaScript pagination uses public GETs through the same fetcher and cache. The
networkless browser cannot submit forms, fetch private addresses, or navigate
to arbitrary articles. Explicit Next links may authorize same-origin page/cursor
coordinates; unchanged link lists stop after two attempts. Hydrated JSON record
lists are first matched to visible titles and links, allowing hidden dated rows
to be read without running JavaScript. Published listings can still exceed the
scan limits; results retain a partial-coverage notice rather than claiming the
entire archive was found.

Latest-entry shortcuts honor an adapter's explicit reading order when available
(for example, Fenrir Realm's volume and chapter-part indexes). Otherwise they use
the highest chapter/episode number when every eligible link is numbered, then the
latest date when every link is dated, then the stored source order.
Ignored links and Trash are excluded. Opening the shortcut
uses the same automatic-read preference as opening an entry from its item page.

Tests cover the real 4,999 boundary, repeated refreshes, deletion-state retention,
library/item limits, preview pruning, rate recovery, concurrent scan rejection,
declared and chunked oversized requests, slow bodies, cached response budgets,
and the latest-link ordering rules. Tests use temporary databases and fixture
sources; they do not generate traffic to media sites.
