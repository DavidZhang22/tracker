# Catchup

A personal media tracker for chapters, videos, podcasts, and blog posts. Built on this repo's React 18 frontend and FastAPI backend, with a SQLite database created automatically. Local use requires no account, API key, external database, or paid service. Public hosting can enable username/password accounts with separate libraries. See [deployment instructions](deploy/README.md).

## Run locally

Install **Node.js 22 or 24 LTS** and **uv**. uv installs Python 3.12 if needed.

From the repository root in PowerShell:

```powershell
.\start.ps1
```

This installs locked dependencies, builds the frontend, and runs the complete app at **http://127.0.0.1:8000**. Keep the terminal running; Ctrl+C stops it. The first installation needs internet access. Subsequent starts reuse installed packages.

For development, use two terminals:

```powershell
# Terminal 1
cd backend
uv sync
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

```powershell
# Terminal 2
cd frontend
npm ci
npm start
```

Open **http://localhost:3000**. The development proxy forwards `/api` to the backend. Interactive API documentation is at **http://127.0.0.1:8000/docs**.

## Use the tracker

1. **Add item:** paste a source URL, scan it, review the actual discovered links, and save. Optional selectors narrow detection for unusual pages. Existing content starts as unread backlog, or you can mark it read during import.
2. **Library:** search and filter by new content, unread content, favorites, ignored items, and media type. Refresh one item or all active items.
3. **Item:** open links, mark read/unread, favorite or ignore individual links, and change ordering. “Mark as read when opened” is a saved preference for each item; ordinary clicks, keyboard activation, and middle clicks are supported. Right-click a row, or use its actions button, to favorite, ignore, mark read/unread, or delete. Shift + right click retains the browser menu; opening from the native browser menu cannot automatically mark read.
4. **New badges:** only links first discovered after initial import are new. They survive later refreshes until read or explicitly cleared. Clearing badges does not mark links read.
5. **Ignored content:** ignored items are skipped by Refresh all. Ignored links are excluded from unread/new counts and bulk read actions. Restore either from its Ignored view. Refresh never erases saved links or user state.

6. **Bulk actions:** check rows or select the visible page. Favorite, ignore, or delete the selection; More actions offers the inverse actions and read/unread for links. Changing page, filter, or search clears the selection. Each request supports up to 1,000 selected records and is atomic.
7. **Trash:** deletion moves items or links into Trash. They stay there through refreshes, are excluded from active counts, and can be restored with their progress and favorites intact. Deleted items are skipped by Refresh all. Trash retains records until restored; there is no automatic permanent purge.
8. **Events and dates:** Codeforces contests include start times, duration, and phase. Its Upcoming filter uses the event time. Dates remain visible on narrow screens; hover over a date to see its source and time zone.

## Link discovery and ordering

The scanner combines source adapters with a small local link classifier. The classifier uses URL and layout context to recover content and reject navigation, author links, archive indexes, and references inside excerpts. It adds no network requests. Feeds, APIs, and explicit selectors retain authority. [Model design, datasets, evaluation, and retraining](backend/ml/README.md).

| Source | Detection |
| --- | --- |
| Asura Scans | Series-scoped chapter paths; parses every chapter already in the HTML, including collapsed lists; compares the result to the reported chapter count. |
| Royal Road | Fiction-scoped table of contents; numbered chapters and unnumbered entries; RSS supplements metadata; short and full chapter URLs share a stable identity. |
| YouTube | Page data and Atom feed, plus yt-dlp for paginated archives. Channel root URLs use the unified uploads playlist, including shorts and streams, to preserve upload order across types. Explicit tab and playlist URLs retain their scope. No media is downloaded. |
| We Tried TLS | Identifies the series from serialized Next.js data; uses the public chapter metadata API and its explicit pagination. Includes free and paid chapter links (marked in the UI), without requesting chapter bodies or bypassing access. |
| GitHub job lists | Loads the advertised README when missing from the repository HTML, then reads application columns with company, role, location and source age. Direct and alternate applications are included; profiles and promotions are excluded. |
| Fenrir Realm | Uses the public chapter index once, including chapter parts, source order, dates and paid-link metadata. |
| Codeforces | Uses the public `contest.list?gym=false` API in one request instead of crawling contest archive pages. Contest IDs identify URLs; UTC start times provide ordering. No API key is required. |
| Blogs and podcasts | RSS, Atom, JSON Feed, article links, dated permalinks, query-based posts, JSON-LD, and repeated content link patterns. Repeated rows/cards correlate dates with individual links; headline links may point to external sites. Podcasts can use enclosure URLs. |
| Other pages | Relative/root-relative URLs, hidden/collapsed markup, embedded JSON, explicit pagination, numbered pagination, older/next links, and embedded next-page URLs. Optional CSS selector and URL text filter are saved with the item. |

URLs are normalized, fragments and common tracking parameters removed, and content-identifying query parameters retained. Number extraction handles decimal chapters and reads human titles before URL hints. Royal Road's opaque chapter IDs and semantic software versions are never treated as chapter numbers. Duplicate navigation labels do not establish a content collection. Forward pagination is preferred over fetching every numbered button.

Each link stores its date, meaning (published, listed, updated, scheduled, or inferred from a dated URL), origin, and precision. Extraction checks the anchor's attributes, semantic time elements, the enclosing record, paired title/metadata table rows, and embedded/feed/API metadata. Neighboring cards and ambiguous multiple dates are not used as substitutes. Publication/start metadata takes precedence over modification dates or URL guesses. Exact timestamps are normalized to UTC and displayed in your time zone with the year; calendar-only dates stay on the source's calendar day. Unzoned clocks are reduced to a calendar date. Relative phrases and incomplete dates remain unknown. The date a tracker discovered a link stays separate from its source date.

Automatic ordering uses numeric order when every entry has a number, content dates when every entry has a date, then preserved source order. This keeps batch-released chapters in numeric order even when they share the same date. Unnumbered epilogues/interludes keep their place. Manual content-date, number, source, discovery-date, and title sorts are available in either direction. Unknown values sort last for explicit date/number sorts. Missing entries retain their source neighbors during partial refreshes.

**Coverage is observable, not guaranteed for arbitrary websites.** Scan previews show counts and warnings. Failed checks remain dismissible; routine scan details are omitted from saved items. A site can require authentication, a browser challenge, or an undocumented JavaScript API; the generic scanner does not execute browser scripts, bypass access checks, click arbitrary load-more buttons, or follow every linked article. Use an archive/feed URL or a custom selector when possible. A selector cannot expose content absent from the downloaded page. Feeds often contain only recent entries. YouTube publication dates may be absent, so the unified upload sequence is used; deleted/private videos cannot be recovered.

To avoid overscraping, full scan results and fetched indexes are reused for **10 minutes**, with the original check time preserved. Concurrent scans of the same source share that work. Host requests are spaced at least **2 seconds** apart; HTTP redirects also spend the request budget. Expired responses use ETag/Last-Modified conditional requests when supported. HTTP 401/403/429/503 responses pause that host, honoring `Retry-After` (up to 24 hours). Equivalent feed formats are deduplicated; feeds are skipped when the index already supplies all dates. Chapter bodies and individual article/video pages are not crawled to fill missing dates. A first archive scan is bounded; subsequent scans revalidate collection indexes rather than walking content bodies.

Limits: **40 HTTP index/feed/API requests per scan**, 4,999 links, 8 MB per response, 20 seconds per HTTP request, 120 seconds for the YouTube archive subprocess, and three minutes per item scan. The separate yt-dlp archive process is bounded by its 120-second timeout, flat metadata mode, 4,999-link cap, and two-second request pacing. Its internal request count is not included in the HTTP request statistic; its entire result shares the ten-minute scan cache. Refresh work is limited to three concurrent source scans. Failed and empty refreshes preserve the last successful check and all saved content.

## Storage and hosting

Data lives in **`backend/data/tracker.sqlite3`**, with SQLite WAL files while running. No database setup is needed. Existing libraries migrate additively on startup. The disposable `fetch-cache.sqlite3` beside the database stores at most 64 MB of payloads and 1,000 entries, retaining stale validators for up to seven days; deleting that cache with the server stopped does not delete your library. To back up, stop the server and copy the database file. To relocate it, set `TRACKER_DB` to an absolute path before starting. Keep the database on a persistent local disk; don't use a shared network filesystem for WAL.

By default, localhost uses the original personal library without sign-in. For public hosting, use `deploy/compose.yaml`: it enables **account authentication and separate user libraries**, configures an exact HTTPS origin, keeps the API port private, and places Caddy in front for TLS. Passwords are hashed with Argon2id; sessions use revocable, HttpOnly cookies. Signup is closed unless an invitation code is configured. Each user has a separate SQLite library, including their private scan previews; your original library is assigned only through the private owner command. [Deploy, create accounts, import data, and back up](deploy/README.md).

The prepared free-hosting route is an Oracle Always Free Linux VM with a free DNS subdomain. This has no application sleep timer, but Oracle may reclaim idle VMs and capacity is not guaranteed. Cloud Run or Render need external durable storage, and their free tiers have availability/cost tradeoffs. No remote deployment is claimed until a provider account, VM, and domain are configured. Local authentication stays disabled unless you explicitly enable it.

The included Docker configuration serves the React build and API in one Python container:

```sh
docker compose up --build -d
```

Open **http://127.0.0.1:8000**. The `tracker-data` volume persists your library across container replacements. Docker files are supplied as a deployment option; Docker was not available in the implementation environment, so that image build was not executed here.

Sites hosting runs JavaScript Workers and cannot run this existing Python backend or yt-dlp subprocess. This repository is delivered as a working local/self-hosted application, not a disconnected static deployment. The database and application have no subscription costs; remote hosting, if chosen, is your responsibility.

SQLite, indexed queries, server-side link pagination, and bounded scan concurrency fit a personal or small-server library. For larger multi-user deployments or multiple application workers, migrate account and library storage to PostgreSQL and move shared scan coordination into a worker queue. The current account mode targets a single VM and keeps each user's SQLite library separate. The interfaces separate parsing, fetching, persistence, and HTTP routes for that migration.

## Tests

```powershell
cd backend
uv sync
uv run pytest -q
uv run ruff check app/tracker app/main.py tests
```

```powershell
cd frontend
npm ci
npm test -- --watchAll=false --runInBand
npm run build
```

Offline fixtures contain reduced link metadata from Asura, Royal Road, We Tried TLS, Codeforces, Hacker News, xkcd, and GitHub releases. Tests cover all 140 and 109 fixture entries respectively, decimal/opaque numbering, feed namespaces and encoding, URL aliases, pagination cycles and partial failures, duplicate scans, persistence, refresh semantics, read/favorite/ignore controls, input validation, and private-network protections. Additional tests cover timestamp provenance, neighboring-card isolation, semantic versions, API pagination, caching/304 responses, request budgets, pacing/backoff, bulk-action ownership and atomicity, migration, Trash retention, and right-click/selection flows. Frontend tests exercise the main user flows using React Testing Library.

Optional live verification through the real API, using a **temporary database**:

```powershell
cd backend
uv run python -m tests.verify_live
```

It writes `backend/tests/live-report.json`. Live totals can change. Latest measured results and coverage notes are in that report. Normal tests never depend on the internet or mutate your library.

Additional collection verification:

```powershell
cd backend
uv run python -m tests.verify_collections --live
```

This uses a temporary library and the normal fetch cache/limits. It checks the We Tried TLS and Codeforces API collections, one xkcd archive, and one page each of Hacker News and GitHub releases. It does not claim complete historical coverage for those two paginated indexes. Omit `--live` to replay previously captured responses in the ignored `tests/live` folder. The checked-in `tests/collections-report.json` records the September 8, 2026 observations, replayed through the app's scan/save/sort API without repeating network requests: **169 We Tried TLS chapters, 2,146 contests, 3,276 xkcd links, 30 news stories, and 10 releases**, all with correlated dates. Live totals will change.

When YouTube changes its format, update its maintained extractor:

```powershell
cd backend
uv lock --upgrade-package yt-dlp
uv sync
```

## Design and code

Design seed **`0xD81EDED8F`** is recorded on the application root and in the shared stylesheet. The theme uses dark navigation, white content surfaces, teal actions/new indicators, and compact rows. Counts navigate to their filters, progress shows remaining content, source icons distinguish media types, and every action has an accessible label. Layouts adapt to narrow screens and reduced-motion preferences.

The existing package manager, React Router, Heroicons, and React Scripts flow are retained. The production main JavaScript bundle is approximately 68 KB gzip; add/detail routes are loaded separately. Existing prototype modules remain in the repository but are not used by the new app. User changes present before implementation were preserved.

Compatible security updates reduced the inherited frontend audit from 63 advisories (including 3 critical) to 30 advisories (9 low, 10 moderate, 11 high). Remaining advisories include React Scripts' build/test dependencies and React Router. Forced audit fixes propose a breaking React Scripts replacement and were not applied. Use the localhost production server; review and replace the inherited toolchain before a public deployment. No clean dependency audit is claimed.

Primary references: [FastAPI deployment](https://fastapi.tiangolo.com/deployment/), [yt-dlp](https://github.com/yt-dlp/yt-dlp), and [YouTube upload playlists](https://developers.google.com/youtube/v3/docs/channels#contentDetails.relatedPlaylists.uploads).

Public collection reference: [Codeforces contest.list](https://codeforces.com/apiHelp/methods#contest.list). Site adapters use only publicly exposed index metadata and may need updates if those sites change.

Account tests cover cookie security, invitation requirements, logout/expiry, password changes, rate limits, private preview IDs, cross-account reads/writes/bulk/refresh isolation, and assigning the existing library to an owner. The deployment files have not been container-built here because Docker is unavailable locally.
