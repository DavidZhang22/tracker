# API and sitemap sources

Choose **Source method** on Add item, or in the item page’s **Item settings** popup. Settings
also contains **Default source method** for new items. New items with the Automatic
default select a supported API from their URL. Existing items keep their saved
method. Changing an item’s method keeps its saved links and
progress; refresh it to collect results through the selected method.

Detection makes no requests to the source site. It recognizes WordPress.com
homepages, WordPress `/wp-json/` roots, DEV.to profiles, GitHub release lists,
Codeforces contests, Steam game news pages, MangaDex titles, and profiles on mastodon.social/online.
YouTube channels/playlists and Ghost homepages select their APIs only when the
server has the required key. Unknown custom domains, individual posts, filtered
archives, and bare GitHub repositories retain the page scanner. Other Mastodon
instances and self-hosted/custom-domain blogs can still select their API manually.

The detected method is saved with the item and reused on refresh. Choosing a
method manually (including Automatic), a saved non-Automatic default, or entering
a CSS selector overrides detection. Except for arXiv (which always uses metadata in Automatic mode), explicit Automatic retains the existing page,
feed, model, and site-adapter behavior. API clients may send `detect_api: false`
with `source_method: "auto"` to request that same behavior. `/api/source-method/detect`
returns a URL-only suggestion; `/api/scans` independently resolves and persists it.
Selecting an API or Sitemap explicitly never silently falls back to crawling.
CSS selectors only apply to Automatic. Keywords and URL substring filters remain
available in every method, using the metadata the chosen source actually supplies.
The preview shows exactly which entries will be saved.

| Method | Source URL | Setup / scope |
| --- | --- | --- |
| arXiv API | Search, category, paper, or API URL | No key. Atom metadata, dated paper links, subject filters, bounded pagination, and daily caching. Some valid queries may be refused by arXiv. |
| WordPress.com API | Site homepage, including supported custom domains | No key. Published posts and pages, with publication dates and stable IDs. External links inside posts are not listed. |
| WordPress API (self-hosted) | Homepage or installation’s `/wp-json/` URL | No key for public posts/pages unless the owner restricts access. `/wp-json/` supports subdirectory installations. |
| DEV.to API | `https://dev.to/username` | No key. Articles belonging to an author or organization. |
| GitHub releases API | `https://github.com/owner/repo/releases` | No key for public releases; GitHub’s unauthenticated quota applies. Use Automatic for README job/application tables. |
| Codeforces API | `https://codeforces.com/contests` | No key. Contests and scheduled start times. |
| Mastodon API | `https://server.example/@username` | Public profile posts, excluding boosts; servers may restrict API access. |
| YouTube API | Channel ID, @handle, legacy user URL, or playlist | Server API key required; Google project quota applies. Uses uploads playlists instead of expensive search calls. Dates are video publication times, not playlist addition times. |
| Ghost Content API | Site homepage over HTTPS | Requires a Content API key configured for that exact hostname. Lists published posts and pages. |
| MangaDex API | MangaDex title URL | No key. Chapter metadata, dates, numbers, and languages; recognized language keywords reduce requests. |
| Steam news API | `https://store.steampowered.com/news/app/1623730` | No key. Official game announcements with publication dates and stable IDs; uses a one-character content limit and never opens articles. |
| Browser (JavaScript) | Public listing URL | Executes JavaScript in the isolated worker. Checks up to four load-more/scroll steps, retaining earlier DOM windows. May report partial coverage. |
| Sitemap | Site URL or direct `.xml` / `.xml.gz` sitemap | No key. Reads declared XML sitemaps and indexes, including compressed files; only page URLs on the source host are included. |

Sitemap titles come from URL slugs. `lastmod` is stored as **Updated**, not
Published. Existing real titles, publication dates, and richer context survive
sitemap refreshes. API IDs supplement URL matching so renamed posts update saved
entries; switching methods preserves read, favorite, ignored, and Trash states.

Both lightweight and deep refresh use the selected API/sitemap reader. Deep skips
the complete-scan cache but still respects the HTTP cache and server backoff. API
and sitemap readers do not invoke the link classifier or request individual
content pages. HTTP responses retain their ten-minute cache, conditional
revalidation, two-second host pacing, 40-request/32-MB scan budget, and 8-MB
response limit. The 4,999-link storage cap and existing account quotas still apply.
Repeated pagination and partial failures stop safely and report partial coverage.
An empty/failed inventory never deletes saved records.

## Optional API keys

The deployed app runs without these keys: Automatic and public sitemap methods
remain available. Credentials stay on the server and are never returned in item
settings, preview payloads, or cache keys. Credentialed requests require HTTPS and
do not forward keys through redirects.

### YouTube

1. Create a Google Cloud project and enable **YouTube Data API v3**.
2. Create an API key and restrict it to YouTube Data API v3. Where practical,
   restrict server use to the VM’s outbound public IP.
3. On the VM, edit `/home/azureuser/tracker/deploy/.env` and set:

```dotenv
TRACKER_YOUTUBE_API_KEY=your-api-key
```

### Ghost

The site owner creates an integration in Ghost Admin and provides its **Content
API key**. Do not use an Admin API key. Set an exact-host JSON map in the same
server `.env` file:

```dotenv
TRACKER_GHOST_CONTENT_KEYS='{"blog.example.com":"content-api-key","another.example.com":"another-content-api-key"}'
```

Use the canonical site hostname; credentialed API redirects are rejected. If you
do not have a site’s Content API key, choose Sitemap or Automatic instead.

After editing `.env`, apply either key configuration with:

```bash
cd /home/azureuser/tracker
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --no-build --wait app
```

Keep the file private and out of Git. Existing deployments need no new database
service. Database version 9 adds `items.source_method` and `links.source_id` while
preserving existing library data and account preferences.

## Verification and references

Offline tests cover all provider response formats, pagination, repeated pages,
missing keys, API errors, source selection persistence, method-separated caches,
filters, ID-based renames, cross-method deduplication, migration, date evidence,
sitemap cycles and namespaces, XML entity rejection, decompression limits,
credential redaction, and redirect safety.

## JavaScript listings

Automatic uses the browser worker when the initial scan has no content links or
detects a load-more control. Browser mode can also be selected explicitly in Add
item or the item page’s **Item settings** popup. Known APIs take precedence; existing Automatic
Steam items also use the news API on their next refresh.

Rendered links pass through the existing link and context classifiers, including
dates and neighboring language labels. Snapshots are combined so virtualized
lists do not lose entries from earlier steps. Coverage stays partial unless a
listing API's full pagination chain can be validated. A browser cannot guarantee
complete results from login-only, protected, POST-only, or arbitrarily long lists.

The app can learn a same-host public GET/JSON API with URL/title records matching
the first rendered list and an explicit `next`/`next_url`/`next_page_url` chain.
It validates that chain before caching a data-only recipe. Lightweight refreshes
read every API page; deep refreshes rediscover it. Repeated pages, changed fields,
foreign endpoints, credentials, and resource limits invalidate the recipe and
fall back to normal discovery. This does not guess offsets or persist executable
code. Recipes expire with the seven-day scan cache. Scans with CSS selectors or
context keywords retain DOM analysis instead of assuming API metadata contains
the same neighboring context.

The worker has no network interface, account database, credentials, or public
port. All allowed browser GETs are fulfilled through the application's existing
DNS-pinned SafeFetcher, HTTP cache, host pacing, and per-scan limits. It blocks
forms/POSTs, popups, frames, content navigation, WebSockets, service workers,
credential-like query fields, images, media, and fonts. It uses a fresh Chromium
process per job with the Chromium sandbox enabled, non-root UID, read-only
filesystem, dropped capabilities, one CPU, 1 GB RAM, 256 processes, and temporary
memory-backed storage. A private Unix socket is its only app connection; the app
mounts that socket directory read-only. One job runs at a time, with a 50-second
render limit, four interaction steps, five 1-million-character snapshots, and at
most 40 resource requests (also subject to the shared 40-request/32-MB budget).

Deployment builds the separate `browser` image automatically with Compose. Its
seccomp profile is vendored from the official [Playwright Docker profile](https://github.com/microsoft/playwright/blob/main/utils/docker/seccomp_profile.json)
to permit Chromium's nested user namespaces. It additionally permits the `chroot`
syscall needed inside those namespaces; container capabilities remain dropped.
Do not disable the Chromium sandbox
or grant SYS_ADMIN to work around startup failures. Local development without a
worker still supports HTML/API scans; Browser mode returns a clear setup message.

Verified September 14, 2026: the supplied Palworld news archive returned 117 unique
dated announcements in two API requests, with no article requests. A real
networkless Chromium fixture retained six records across replaced DOM windows,
then a lightweight refresh used four listing API requests to find eight records
without launching Chromium. The browser container peaked at approximately
186 MiB of memory in that test; more complex pages may use more, up to its limit.

References: [Steam news API](https://partner.steamgames.com/doc/webapi/ISteamNews),
[Playwright network routing](https://playwright.dev/python/docs/network),
[Chromium sandbox in Docker](https://playwright.dev/python/docs/docker).

Live metadata checks on 2026-09-14: Galactoid Tetris returned 191 non-homepage
entries with dates in two WordPress API requests. Sitemap discovery returned 191
entries in three requests (robots plus two declared sitemaps). GitHub Requests
returned 19 releases in one request. DEV.to pagination was checked using public
article listings. No individual article, video, or chapter body was requested.

- [WordPress.com listing API](https://developer.wordpress.com/docs/api/1.1/get/sites/$site/posts/)
- [WordPress posts](https://developer.wordpress.org/rest-api/reference/posts/) and [response envelopes](https://developer.wordpress.org/rest-api/using-the-rest-api/global-parameters/)
- [DEV.to API](https://developers.forem.com/api/v0)
- [GitHub releases](https://docs.github.com/en/rest/releases/releases#list-releases)
- [Codeforces methods](https://codeforces.com/apiHelp/methods)
- [Mastodon account statuses](https://docs.joinmastodon.org/methods/accounts/#statuses)
- [YouTube playlist items](https://developers.google.com/youtube/v3/docs/playlistItems/list)
- [Ghost Content API](https://docs.ghost.org/content-api/)
- [Sitemap protocol](https://www.sitemaps.org/protocol.html)


## arXiv

arXiv searches automatically use `https://export.arxiv.org/api/query`. Supported inputs include simple and advanced searches, `/search/cs` and other supported subject scopes, direct API queries, paper abstract/PDF/HTML URLs, and recent category lists. Paper URLs request metadata only. RSS/Atom category feeds retain the feed reader. Existing Automatic arXiv items also use the API when refreshed; HTML selectors must be cleared.

Search conversion preserves unquoted terms, phrases, supported fields (all, title, author, abstract, comments, journal reference, report number, complete paper ID), Boolean operators, subject scope, pagination offset, and supported sorting. Advanced form rows are grouped using the website's NOT > AND > OR precedence before unsupported filters are removed. Subject checkboxes are ORed together; `physics_archives=all` is inactive unless Physics is selected. Current-version searches for an explicit paper version keep the paper ID and disclose that the version restriction was omitted.

Unsupported restrictions are omitted and named in the dismissible link-preview warnings. This includes DOI/ORCID/MSC/ACM fields, primary-only or cross-list-only restrictions, older-version search, unsupported sorts, unknown nonempty parameters, and unavailable date types. Dropping an OR branch can narrow a query, so notices do not promise broader results. Malformed supported input still needs correction. If no supported condition remains, the scan stops without requesting all arXiv papers. A negative condition never becomes positive when its preceding unsupported term is removed.

Original-submission date filters support specific years, closed date ranges, and the past-12-month option. The website's Eastern-time boundaries are converted to UTC for the API. The inclusive API upper bound is refined locally using returned publication dates to preserve the website's exclusive cutoff, without opening paper pages. Open-ended, latest-submission, and announcement-date filters are omitted with notices. Website `submitted_date` sorts by API `lastUpdatedDate`; `submitted_date_first` sorts by `submittedDate`. Unsupported announcement sorts use relevance with a notice. Recent/new category URLs track the category by submission date, including older papers, with a scope note. Website and API indexing/ranking can still differ.

`search_query.py` contains provider-neutral text, facet, date-range, and Boolean conditions with capability pruning and omission notices. `arxiv_search.py` parses the website's form and compiles those conditions into arXiv syntax. Other adapters can reuse the condition model while defining their own field mappings and query serializer. They are not automatically enabled for arbitrary undocumented APIs.

Requests use deterministic parameter order, literal colons, `%20` spaces, and encoded quotes. Canonical cache keys remain independent of this wire representation. The user-provided `all:"domain specific language"` request with 200 results succeeds from Azure in this format, while its equivalent reordered/re-encoded URL returned HTTP 406. The translated computer-science search `Hoffmann et al. 2022` still returns HTTP 406. These observations suggest URL-sensitive upstream/cache behavior, not a proven block on particular terms. Access is not guaranteed; the adapter never retries a refusal with progressively stripped filters or browser impersonation. Requested compatibility omissions are applied once, before the initial request.

The reader follows OpenSearch offsets, requesting at most 200 records per page and 25 pages per scan (also subject to the scan request budget and 4,999-link cap). It extracts publication dates, titles, authors, categories, abstracts, and stable unversioned abstract-page links. It never opens individual paper pages or downloads PDFs. Missing, duplicate, inconsistent, or refused later pages keep earlier records and report partial coverage with the API's total result count.

Successful API responses are shared for 24 hours across users and refresh modes. A durable gate serializes arXiv API/RSS requests across workers sharing the cache database and waits at least 3.1 seconds after each request. HTTP refusals and Retry-After pause all arXiv hosts; there are no automatic retries. Multiple independently deployed servers would need a shared external limiter before using this integration at the same time.

References: [API manual](https://info.arxiv.org/help/api/user-manual.html), [API access limits](https://info.arxiv.org/help/api/tou.html).
