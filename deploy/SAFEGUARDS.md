# Application safeguards

These bounds apply on the server; hiding controls or changing browser requests
cannot raise them. No additional service or database is needed.

| Resource | Bound |
| --- | --- |
| Links per scan preview and saved item | 4,999 |
| Links per account library | 100,000 |
| Items per account library | 200 |
| Saved scan previews per account | Latest 5, valid for one hour, up to 8 MB each |
| Active scan operations | One per account, three across the process |
| Scan allowance | 200 items per account, 600 server-wide; tokens replenish over an hour |
| API allowance | 240 requests per IP, 1,200 server-wide; tokens replenish over a minute |
| In-flight API requests | 64 |
| API request body | 64 KiB, including chunked requests; 10-second arrival deadline |
| Collection scan | 40 fetch requests, 32 MB of response content total, three-minute deadline |
| Individual source response | 8 MB after decompression |
| Refresh all | Sequential per account, stops after ten minutes and reports remaining items |

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
ten-minute source caching, source pacing, refusal backoff, bounded cache storage,
and database failure handling. Application admission limits are in memory and
reset after restart. They assume the deployed single application process; use a
shared limiter before adding workers or replicas. They do not provide protection
against traffic that exhausts the VM's network connection.

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
