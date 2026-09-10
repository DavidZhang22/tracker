# Source fixtures

Captured 2026-09-08 from the three user-supplied test sources. `asura.html` retains the 140 chapter anchors and dates; `royalroad.html` retains the 109 table-of-contents rows and dates. Body text, images, scripts, and styling are removed. These are observed snapshots, not promises about future live counts.

- https://asurascans.com/comics/the-nebulas-civilization-53fc8424
- https://www.royalroad.com/fiction/21220/mother-of-learning

`capture_fixtures.py` can reduce manually fetched HTML saved under ignored `tests/live/`. It never fetches or stores media itself. YouTube's parser and archive continuation interface use synthetic fixtures so offline tests do not store a large personalized page or depend on live tokens. The live API check also exercises the supplied YouTube channel.


Additional fixtures captured September 8, 2026:
- `wetried-chapters.json`, `wetried-paid.json`: public API metadata from the supplied series (series ID 82). Excerpts retain real chapter fields; pagination totals are reduced to the excerpt sizes for deterministic tests. Next.js flight structure is represented by a synthetic fixture in the tests.
- `codeforces.json`: first two and last two records from the public regular-contest API.
- `hn.html`: four title rows and their following metadata rows from https://news.ycombinator.com/.
- `xkcd.html`: four dated archive anchors from https://xkcd.com/archive/.
- `github-releases.html`: two release cards from https://github.com/astral-sh/ruff/releases, with SVG/images/body text removed; auxiliary metadata is retained to test false-positive rejection.

Complete HTTP observations used for manual verification stay in ignored `tests/live/`; normal unit tests use only these minimized fixtures and make no requests.

`fenrir-chapters.json` was captured September 9, 2026 from the public GET endpoint
`https://fenrirealm.com/api/new/v2/series/the-speedrun-manual-of-miss-witch/chapters`,
used by the site's JavaScript series page. It retains seven of 722 metadata records,
including chapter 154's four parts and the latest paid entry. Account/purchase
fields and chapter bodies are omitted. The adapter fetches this index once through
the normal cached, bounded fetcher; it does not execute JavaScript or visit chapters.
Unlock dates take precedence over creation dates, with modification dates used
only as a labeled fallback. Explicit group/chapter indexes preserve reading order
when parts or volume resets would make numeric sorting misleading. Synthetic
tests cover those cases and invalid paths without making network requests.

`jobs-table.html` is a minimized September 10, 2026 excerpt of the public
SimplifyJobs/New-Grad-Positions README. It contains open application links and
closed rows, role/company/location/age columns and image-only action labels.
Regression tests also use authored reordered-column and Markdown layouts,
blocked-source responses, and untrusted raw README URLs. No applications are submitted.

`novelshaven-chapters.json` retains the 221 public chapter-metadata records from
https://novelshaven.com/series/the-galgame-martial-saint, captured September 10,
2026. IDs, reviews, profiles, images and chapter bodies are omitted. The page
renders 90 anchors but carries all records in escaped Next.js Flight JSON.
Tests reconstruct the streamed envelope and its React-props reference, including
chapter zero, and exercise hidden anchors, partial indexes, selectors, scope and
cyclic references. No JavaScript is evaluated and no chapter pages are fetched.

`mangadex-chapters.json` retains one public chapter metadata record per language
from the first 500 records of the supplied One Punch-Man feed, captured September
10, 2026. The 16-record excerpt keeps chapter IDs, numbers, titles, translation
languages, publication dates, and manga/group relationships. User relationships,
images and chapter contents are excluded. Offline tests reduce the pagination
total to their fixture size and explicitly exercise empty English results,
pagination limits, other-title records, unavailable chapters, and invalid IDs.
