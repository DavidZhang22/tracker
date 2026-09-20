# Trackify

[Use Trackify](https://mediatrackify.duckdns.org) · [Project description](PROJECT_SUBMISSION.md)

Track blogs, chapters, videos, game updates, job listings and other public sources in one library. Create an account, add a source URL, review the discovered links and save your progress. Public registration is open on the hosted site; libraries are private to each account.

## Features

- Dated and numbered links, unread filters, favorites, muted items and Trash.
- Mobile layouts, bulk actions, reading order preferences and optional mark-as-read on opening a link.
- Keyword filtering using nearby record context, including language and location.
- Automatic media types with manual overrides in item settings, plus hidden descriptors for library similarity search.
- Local semantic library search with typo tolerance, and editable descriptions extracted from scanned source text.
- Lightweight refreshes with a full model scan available when a source changes.
- Suggestions ranked locally using existing source metadata (currently hidden in the interface).
- Password-protected accounts, optional verified recovery emails, data export, session revocation and account deletion.
- File and pasted-text imports with link context, date detection, preview and updates to one item.

## Import files or text

Choose **Add item → File or text**, then upload a file or paste text. Supported formats are CSV, TSV, Excel (.xlsx), PowerPoint (.pptx), Word (.docx), PDF, HTML, Markdown and plain text. Preview the links, name the item, and choose reading progress before saving. Each import creates links within one item.

Files are limited to **4 MB** and pasted text to **200,000 characters**. Office documents support up to 50 worksheets or 200 slides; PDFs support up to 200 pages. Import up to 10,000 candidate records and save up to 4,999 unique links. Image-only PDF text needs OCR before importing; embedded PDF hyperlinks are included. Export older .xls and .ppt files to their modern formats first. Password-protected files are unsupported.

All links are kept by default. For noisy documents, **Import options → Content links (model filtered)** uses the shared website classifier; review this optional filter because unfamiliar layouts can lose relevant links. Nearby titles, dates and keywords use the same record-context model as website scans. No website-specific document adapters or external model service are required.

CSV and pasted spreadsheet rows offer column detection and manual correction. UTF-8, UTF-16 with a BOM, Windows-1252, comma, semicolon, tab and pipe exports are supported. Ambiguous numeric dates require an explicit day/month choice. Other cells stay searchable under each link's Details. Excel supports embedded hyperlinks, plain URLs, cached cell values and literal HYPERLINK formulas; it does not calculate formulas.

**Update import** merges a revised file or pasted text after preview. Matching links retain read, favorite, muted, Trash and grouping state; missing links remain saved. Both Refresh and Full Refresh skip imported items and muted sources, including muted favorites.

Uploads are processed on the Trackify server; the original file is not saved. Parsing does not visit imported URLs, execute scripts or macros, or load external document parts. CSV retains its bounded fast parser. Other formats use one isolated parser process at a time with a 20-second wall timeout; Linux adds a 384 MiB address-space cap and 12-second CPU cap. Office archive expansion is limited to 24 MB, and XML entity expansion is disabled. Existing authentication, origin checks, request limits, account isolation and safe URL validation also apply. No additional database or hosted parsing service needs setup.

The [generalization evaluation](backend/ml/reports/generalization.md) records the new sources, model comparison and runtime checks.

## Organizing links

Library search combines title and URL matches, typo tolerance and local sentence embeddings of saved metadata and descriptions. **Quick search** uses only browser-side text and typo matching; **Smart search** adds server semantic matching. Larger libraries move lexical ranking into a background worker. Indexes survive progress/favorite updates, remain in memory, and are discarded when the page closes. Item link search matches text within that item's entries. Selection waits for current results.

**Saved views** stores a search, status filter, media type, sort order and search mode together. Save up to 12 private views, rename/update them, or delete a view without deleting its items. Browser back/forward restores Library controls. Saved views are included in account exports and deletion.

**Continue** opens the next unread entry in detected number/date/source order, honoring the item's read-on-open preference. Muted/trashed entries and future scheduled releases are skipped. Merged or linkless entries open the unread list without changing their progress. **Latest** remains available for jumping to the newest entry.

See the [feature-depth plan and verification](backend/ml/reports/feature-depth.md) for scope, tradeoffs and performance measurements.

Use Selection → Choose a pattern to combine an interval with a position range. For example, every 2 links, starting with 1, within positions 1–49 selects positions 1, 3, 5 … 49 in the current filtered order. Selection can be replaced, extended, reduced or inverted; Shift-click selects a range on the current page.

Merge with above groups each selected row with its preceding row. Consecutive selections join the same group. Every original URL and its progress remain stored; Separate links removes the grouping. Group actions apply to all members, and a group is read or muted when all its members are. Favorites apply when any member is a favorite. Refresh preserves these groups. The 4,999-link storage cap still counts every original URL.

## How discovery works

Known sources use their public APIs where available. Feeds, sitemaps and HTML listing pages provide links without visiting every article or chapter. A small neural network identifies the surrounding record; compact tree classifiers distinguish content links from navigation and promotional links. Dates and sequence numbers come from source evidence.

Chapter and episode lists can also contain titles without links. Trackify stores these as entries with read, favorite and mute controls; it does not invent a destination URL. For other lists, use **Refine content detection → Content selector** (for example, `#updates li` or `table tbody tr`). Dates stay within each row, and relative ages remain context when no exact date is supplied. A URL filter excludes entries without links. Source IDs, volume/season boundaries and unambiguous chapter numbers keep refreshes stable; indistinguishable unnumbered titles cannot reliably represent separate records without source IDs.

Initial scans can learn validated extraction recipes for subsequent refreshes. A bounded, isolated Chromium worker handles JavaScript listings and can learn reusable public JSON listing requests. All browser network requests pass through the application's URL validation, cache and request limits. There is no hosted AI service or GPU requirement.

The link classifiers train offline and load numeric JSON weights without scikit-learn. Semantic library search uses a quantized MiniLM encoder through ONNX Runtime and NumPy, with text search as a fallback. Descriptions select sentences from the source rather than inventing information. In a saved four-page, two-CPU benchmark, reusable analysis processes reduced batch time from 9.21 to 6.70 seconds with identical scan output. This measures that fixture workload, not every website.

- [Model training and evaluation](backend/ml/docs/LINK_CLASSIFIER.md)
- [Record context network](backend/ml/docs/RECORD_CONTEXT.md)
- [Semantic search and descriptions](backend/ml/docs/SEMANTIC_SEARCH.md)
- [Parallel analysis benchmark](backend/ml/docs/PARALLEL_ANALYSIS.md)
- [Response memory and refresh throughput](backend/ml/docs/MEMORY_PIPELINE.md)
- [Lightweight refresh design](backend/ml/docs/LIGHTWEIGHT_REFRESH.md)

Sources can block access or change their layouts. Scans report incomplete coverage and preserve existing data on failure. Requests are paced and capped; each item stores at most 4,999 links, including muted links and Trash.

Enma watch URLs select its episode-list API, making one metadata request without loading the player. On September 15, 2026, Enma returned HTTP 403 to both local and deployed scans. Trackify reports that refusal and respects the fetch backoff; it cannot guarantee access. The adapter has synthetic contract tests based on the site's published client code, but successful live extraction remains unverified. Episode numbers come from listing metadata; missing publication dates remain unavailable.

A direct diagnostic request on September 15 identified Enma's response as `Forbidden: Origin not allowed`. Trackify leaves that access restriction in place. Webnovel's official [Shadow Slave catalogue](https://www.webnovel.com/book/22196546206090805/catalog) is the preferred source for its chapter inventory, but direct requests currently receive a Cloudflare challenge (`cf-mitigated: challenge`). This happens before extraction; neither a larger classifier nor list parsing resolves a 403. Use accessible publisher listings, authorized APIs or CSV imports when automated access is unavailable. No chapter bodies, login tokens, challenge cookies or player streams are needed for inventory tracking.

## Repository layout

`frontend/src` separates pages, shared controls, account code, hooks, preferences and styles. `backend/app/tracker` contains the active API, storage, source adapters and inference code. Tests live in `frontend/src/__tests__` and `backend/tests`; ML datasets, reports and design notes are grouped under `backend/ml`. `deploy` contains the production Compose configuration and backup tools. The root Compose file runs a local-only instance.

See the [frontend guide](frontend/README.md), [backend guide](backend/README.MD), and [model tools](backend/ml/README.md) for entry points. Local databases, credentials, captures and generated build files are ignored.

## Run locally

Requires Python 3.12, uv and Node.js 24. From two terminals:

```sh
cd backend
uv sync
uv run python ml/fetch_semantic_model.py
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

```sh
cd frontend
npm ci
npm start
```

Open http://127.0.0.1:3000. The Vite development server binds to `127.0.0.1:3000` and proxies `/api` requests to `127.0.0.1:8000`. Local mode opens a personal library without accounts; bind it to localhost. SQLite files are created automatically in `backend/data` and must stay out of Git. No external database is required. Browser rendering is available with the Docker deployment.

## Test

```sh
cd backend
uv run pytest -q
```

```sh
cd frontend
npm run lint
npm test
npm run build
```

`npm test` runs Vitest once; use `npm run test:watch` during development. Run the backend tests before the frontend production build; the build replaces the static directory used by backend route tests. Vite writes production files to `frontend/build`, with generated assets under `build/static`, for FastAPI and Docker.

## Deploy

The application uses React, FastAPI and SQLite behind Caddy HTTPS. Docker Compose runs the app and a separate network-isolated browser worker on a Linux VM. Keep one application worker: shared scan pacing and local storage target a single VM, not multiple replicas.

[Deployment instructions](deploy/README.md) cover public, invited and closed registration. [Azure operations](deploy/AZURE.md) cover the current server, backups and migration. [Privacy and security operations](deploy/PRIVACY_SECURITY.md) document data requests and restoration safeguards. [Email recovery setup](deploy/EMAIL_RECOVERY.md) explains how to enable optional SMTP delivery; it remains disabled until configured.

The existing Compose project, session cookies and backup service retain their internal `catchup` names to preserve installed volumes and active sessions. All visible branding is Trackify.
