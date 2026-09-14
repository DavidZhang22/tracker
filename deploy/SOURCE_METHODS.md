# API and sitemap sources

Choose **Source method** on Add item, or under Settings → Item settings. Settings
also contains **Default source method** for new items. New items with the Automatic
default select a supported API from their URL. Existing items keep their saved
method. Changing an item’s method keeps its saved links and
progress; refresh it to collect results through the selected method.

Detection makes no requests to the source site. It recognizes WordPress.com
homepages, WordPress `/wp-json/` roots, DEV.to profiles, GitHub release lists,
Codeforces contests, MangaDex titles, and profiles on mastodon.social/online.
YouTube channels/playlists and Ghost homepages select their APIs only when the
server has the required key. Unknown custom domains, individual posts, filtered
archives, and bare GitHub repositories retain the page scanner. Other Mastodon
instances and self-hosted/custom-domain blogs can still select their API manually.

The detected method is saved with the item and reused on refresh. Choosing a
method manually (including Automatic), a saved non-Automatic default, or entering
a CSS selector overrides detection. Explicit Automatic retains the existing page,
feed, model, and site-adapter behavior. API clients may send `detect_api: false`
with `source_method: "auto"` to request that same behavior. `/api/source-method/detect`
returns a URL-only suggestion; `/api/scans` independently resolves and persists it.
Selecting an API or Sitemap explicitly never silently falls back to crawling.
CSS selectors only apply to Automatic. Keywords and URL substring filters remain
available in every method, using the metadata the chosen source actually supplies.
The preview shows exactly which entries will be saved.

| Method | Source URL | Setup / scope |
| --- | --- | --- |
| WordPress.com API | Site homepage, including supported custom domains | No key. Published posts and pages, with publication dates and stable IDs. External links inside posts are not listed. |
| WordPress API (self-hosted) | Homepage or installation’s `/wp-json/` URL | No key for public posts/pages unless the owner restricts access. `/wp-json/` supports subdirectory installations. |
| DEV.to API | `https://dev.to/username` | No key. Articles belonging to an author or organization. |
| GitHub releases API | `https://github.com/owner/repo/releases` | No key for public releases; GitHub’s unauthenticated quota applies. Use Automatic for README job/application tables. |
| Codeforces API | `https://codeforces.com/contests` | No key. Contests and scheduled start times. |
| Mastodon API | `https://server.example/@username` | Public profile posts, excluding boosts; servers may restrict API access. |
| YouTube API | Channel ID, @handle, legacy user URL, or playlist | Server API key required; Google project quota applies. Uses uploads playlists instead of expensive search calls. Dates are video publication times, not playlist addition times. |
| Ghost Content API | Site homepage over HTTPS | Requires a Content API key configured for that exact hostname. Lists published posts and pages. |
| MangaDex API | MangaDex title URL | No key. Chapter metadata, dates, numbers, and languages; recognized language keywords reduce requests. |
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
