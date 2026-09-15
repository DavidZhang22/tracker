# Trackify

[Use Trackify](https://mediatrackify.duckdns.org) · [Project description](PROJECT_SUBMISSION.md)

Track blogs, chapters, videos, game updates, job listings and other public sources in one library. Create an account, add a source URL, review the discovered links and save your progress. Public registration is open on the hosted site; libraries are private to each account.

## Features

- Dated and numbered links, unread filters, favorites, muted items and Trash.
- Mobile layouts, bulk actions, reading order preferences and optional mark-as-read on opening a link.
- Keyword filtering using nearby record context, including language and location.
- Lightweight refreshes with a full model scan available when a source changes.
- Suggestions ranked locally using existing source metadata.
- Password-protected accounts, data export, session revocation and account deletion.

## Organizing links

Search matches titles, URLs and saved metadata. Results stay visible while a search runs, with recent views cached briefly in the current tab.

Use Selection → Choose a pattern to combine an interval with a position range. For example, every 2 links, starting with 1, within positions 1–49 selects positions 1, 3, 5 … 49 in the current filtered order. Selection can be replaced, extended, reduced or inverted; Shift-click selects a range on the current page.

Merge with above groups each selected row with its preceding row. Consecutive selections join the same group. Every original URL and its progress remain stored; Separate links removes the grouping. Group actions apply to all members, and a group is read or muted when all its members are. Favorites apply when any member is a favorite. Refresh preserves these groups. The 4,999-link storage cap still counts every original URL.

## How discovery works

Known sources use their public APIs where available. Feeds, sitemaps and HTML listing pages provide links without visiting every article or chapter. A small neural network identifies the surrounding record; compact tree classifiers distinguish content links from navigation and promotional links. Dates and sequence numbers come from source evidence.

Initial scans can learn validated extraction recipes for subsequent refreshes. A bounded, isolated Chromium worker handles JavaScript listings and can learn reusable public JSON listing requests. All browser network requests pass through the application's URL validation, cache and request limits. There is no hosted AI service or GPU requirement.

The models train offline and load numeric JSON weights in production without NumPy or scikit-learn. In a saved four-page, two-CPU benchmark, reusable analysis processes reduced batch time from 9.21 to 6.70 seconds with identical scan output. This measures that fixture workload, not every website.

- [Model training and evaluation](backend/ml/README.md)
- [Record context network](backend/ml/RECORD_CONTEXT.md)
- [Parallel analysis benchmark](backend/ml/PARALLEL_ANALYSIS.md)
- [Lightweight refresh design](backend/ml/LIGHTWEIGHT_REFRESH.md)

Sources can block access or change their layouts. Scans report incomplete coverage and preserve existing data on failure. Requests are paced and capped; each item stores at most 4,999 links, including muted links and Trash.

Enma watch URLs select its episode-list API, making one metadata request without loading the player. On September 15, 2026, Enma returned HTTP 403 to both local and deployed scans. Trackify reports that refusal and respects the fetch backoff; it cannot guarantee access. The adapter has synthetic contract tests based on the site's published client code, but successful live extraction remains unverified. Episode numbers come from listing metadata; missing publication dates remain unavailable.

## Run locally

Requires Python 3.12, uv and Node.js. From two terminals:

```sh
cd backend
uv sync
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

```sh
cd frontend
npm ci
npm start
```

Open http://localhost:3000. The development frontend proxies API requests to the backend. Local mode opens a personal library without accounts; bind it to localhost. SQLite files are created automatically in `backend/data` and must stay out of Git. No external database is required. Browser rendering is available with the Docker deployment.

## Test

```sh
cd backend
uv run pytest -q
```

```sh
cd frontend
npm test -- --watchAll=false --runInBand
npm run build
```

Run the backend tests before the frontend production build; the build replaces the static directory used by backend route tests.

## Deploy

The application uses React, FastAPI and SQLite behind Caddy HTTPS. Docker Compose runs the app and a separate network-isolated browser worker on a Linux VM. Keep one application worker: shared scan pacing and local storage target a single VM, not multiple replicas.

[Deployment instructions](deploy/README.md) cover public, invited and closed registration. [Azure operations](deploy/AZURE.md) cover the current server, backups and migration. [Privacy and security operations](deploy/PRIVACY_SECURITY.md) document data requests and restoration safeguards.

The existing Compose project, session cookies and backup service retain their internal `catchup` names to preserve installed volumes and active sessions. All visible branding is Trackify.
