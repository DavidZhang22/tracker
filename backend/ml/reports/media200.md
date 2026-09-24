# Ranked 200-site audit and shared source guidance

Audit date: September 24, 2026. Requests originated from the production Azure region in a separate container limited to 512 MiB and one CPU. User libraries were not accessed or refreshed.

## Scope and selection

The inventory contains 200 distinct, reviewed domain families. It combines the public [News and Media top 100](https://onelittleweb.com/digital-market-intelligence/popular-websites/news-and-media/), the five public leaders in [Similarweb Arts and Entertainment](https://www.similarweb.com/top-websites/arts-and-entertainment/), and category-ranked sources from [Detailed](https://detailed.com/50/): entertainment, technology, gaming, music, books and food. Overlapping domains are merged; remaining categories are interleaved in category-rank order.

This is a category-stratified sample of leading websites, **not an absolute global traffic top 200**. Public rankings use different definitions. The inventory preserves each source's category rank, ranking URL, displayed update date and capture hash. Some rankings display older update dates despite their page title saying 2026. Broad rankings also include portals, shopping and subscription homepages; returning a page does not prove that page offers a useful release listing.

`datasets/media200-inventory.json` freezes the selection and splits before collection. `datasets/media200-audit.json` records observations, errors, request counts and capture hashes. Seven source families already appeared in recorded datasets; their assignments were retained. BBC's two main domains are grouped together. Different publisher brands and shared CMS templates are not guaranteed independent.

## Access results

| Observation | Sites |
| --- | ---: |
| Ordinary HTML returned | 143 |
| Explicit refusal or robot challenge | 16 |
| Crawl policy could not be verified | 18 |
| Listing disallowed by robots.txt | 9 |
| JavaScript shell with no usable static listing | 4 |
| Redirect outside the reviewed host | 5 |
| Temporary failure, including rate limit | 2 |
| Crawl delay exceeds this audit's window | 3 |
| **Total** | **200** |

148 requests initially returned page bodies. Offline inspection identified four script-only pages and one HTTP-200 robot challenge. One otherwise accessible source had lossy character decoding and was excluded from training. A refused robots request is recorded separately from a refused listing request. These observations apply to the exact checked URLs and Azure egress at the recorded time, not every page on each domain.

Collection took 152.7 seconds, making 534 requests including robots and redirects and receiving 121.6 MB. No articles, chapters, media files, scripts or images were downloaded. Four independent sites ran concurrently; each site allowed at most six requests and 10 MB, with a 90-second timeout, at least two seconds of host spacing, and longer published crawl delays respected. Delays above 30 seconds were deferred. Finished attempts are retained on resume, including failures. The identified Trackify user agent, public-address validation, request cache and host backoff were used throughout.

## Dataset additions

`datasets/media200-links.jsonl.gz` contains **9,074 partially labelled examples from 135 new families** (1.84 MB compressed): 6,380 train, 1,037 validation and 1,657 test rows. There are 4,783 positive and 4,291 negative examples. Unambiguous linked headlines and navigation/utility controls supply weak labels; ambiguous links abstain. Rules operate on the captured DOM, never current model predictions. Features use the unchanged production representation.

These are **weak labels, not exhaustive human gold annotations**. Headline rules have selection bias and can confuse long category headings or promotions with content. Negative-only sources help evaluate navigation rejection but do not demonstrate content recall. Sample review found these limitations; partial scopes are retained rather than claiming all links were annotated. The compressed report preserves samples and per-site exclusions for further review.

New source families received deterministic 70/10/20 hash buckets before acquisition. Five rows overlapping historical data and 176 duplicate rows were removed using exact target URLs or identical feature/token fingerprints. Held-out examples take precedence over new training duplicates. Existing source families, robot pages and corrupt text are excluded. Historical unrecorded origins, publisher relationships and similar templates remain possible leakage sources.

A further compressed feed replay dataset contains 244 dated/title/URL records from eight official alternatives. Feed parser verification extracted every record from each original downloaded feed. Full article bodies are not committed. Raw HTML/XML and robots captures remain local, outside Git; source hashes allow later capture verification.

## Model diagnostics

Unchanged models were evaluated on the 1,657 held-out weak-labelled rows, without tuning or training:

| Model | Precision | Recall | F1 | Single batch time |
| --- | ---: | ---: | ---: | ---: |
| Legacy | 1.0000 | 0.6922 | 0.8181 | 30.6 ms |
| Context | 0.9877 | 0.9568 | 0.9720 | 48.9 ms |
| Deep alone | 1.0000 | 0.2948 | 0.4554 | 50.4 ms |
| Production cascade | 0.9879 | 0.9719 | 0.9799 | 109.1 ms |

These are partial-scope diagnostics on an easier rule-selected subset, **not overall website accuracy or complete-page recall**. Times exclude HTML parsing and feature extraction. No production weights or thresholds changed. Weak-label training now requires `--allow-weak-labels`; this is an explicit experiment opt-in, not a recommendation to deploy those results.

## Shared source registry and alternatives

`app/tracker/source_status.json` seeds a global SQLite registry alongside the main database: `source-status.sqlite3`. It contains public source observations only. Account libraries remain separate. Startup imports are idempotent; older observations cannot overwrite newer ones. No user-facing write endpoint exists.

A lookup performs no network requests. It matches an exact host, with a www alias, and does not blacklist unrelated subdomains. A past homepage failure never prevents a requested scan or silently switches its URL. Database failures omit this optional guidance while preserving the normal scan path. Observations older than one day are explicitly marked stale. Alternatives expire from recommendations after 30 days without verification.

Of 24 candidate official feeds, eight passed robots, access and complete parser verification from Azure:

- New York Times headlines
- NPR news
- Washington Post world news
- TechRepublic articles
- Fast Company articles
- Le Parisien headlines
- Parade articles
- Hollywood Life articles

The other candidates remain recorded as unavailable, not recommended. Publisher documentation and publisher-owned feed URLs provide provenance; extracted article URLs were checked against the publisher domain. No third-party mirror, proxy, CAPTCHA solution, browser impersonation or origin spoofing was used. Searches did not establish a usable official alternative for every unavailable site. The absence of an alternative means none was verified in this audit, not that none exists.

Users see a dismissible notice when source detection or a failed item scan finds a registry entry. Selecting an alternative opens the ordinary preview flow. The notice states that feed coverage may differ from the original page; filters and paths are never silently transferred. Known working alternative URLs do not receive their own homepage-failure warning.

A local Windows profile of 1,000 lookups against 200 observations measured median **0.465 ms**, p95 **0.559 ms**, maximum **0.700 ms**, with a **57,344-byte** database and zero source requests. This is lookup overhead only, not end-to-end refresh latency.

## Reproduce and maintain

Run from `backend` with the existing development environment. Offline diagnostics require no network:

```sh
python -m ml.media_audit.evaluate
pytest tests/test_media200_audit.py tests/test_source_registry.py
```

The committed corpus works without original HTML. Rebuilding examples requires the ignored captures at paths recorded in the audit, with matching SHA-256 hashes:

```sh
python -m ml.media_audit.corpus --manifest data/media200/audit.json
python -m ml.media_audit.publish
python -m ml.media_audit.evaluate
```

For a deliberate new audit, copy the frozen inventory to a new manifest under `data`, preserve the previous evidence, and first run the collector without `--fetch`. Add `--fetch` only when ready to make the bounded requests. Reusing a completed manifest never repeats finished checks. Production does not automatically recrawl this inventory.

```sh
python -m ml.media_audit.collect --manifest data/new-audit/audit.json --directory data/new-audit/captures
```

Review source statuses and publisher provenance before regenerating the seed and deploying. Reverify alternatives in the same server environment; a browser or different network can have different access. The training commands accept compressed JSONL through `ml.artifacts`. An explicit offline experiment can add `--extra ml/datasets/media200-links.jsonl.gz --allow-weak-labels` to `ml/train_cascade.py`; retain the existing regression suite and untouched test partitions before considering model promotion.

## Release verification

Backend lint and 1,333 tests passed. Frontend lint, 223 tests and the production build passed. Browser checks at 1440, 390 and 320 pixels verified no horizontal overflow, dismissible guidance, explicit alternative navigation, and no automatic scan on selection. Mobile add-item and desktop item-page screenshots were visually inspected.
