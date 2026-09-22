# Website breadth evaluation

Recorded September 21, 2026. Runtime models and application behavior were not changed.

## Coverage

**113 distinct website families across 114 evaluated listing pages:** 62 historical families plus 51 new families. Subdomains are grouped with their parent website; different pages on Python.org count once. The machine-readable [site index and summary](website-breadth.json.gz) lists every historical and new source.

The expansion adds **16,809 labeled candidate rows** and **4,829 intended content URLs** across 39 subject-area labels. Candidate rows can contain repeated anchors; evaluation deduplicates by source and canonical URL. All new rows are marked `test`, and no model weights or thresholds were fitted on them.

New sources include FDA recalls, Supreme Court opinions, Federal Reserve releases, USGS publications, IANA registries, UCI datasets, NGINX advisories, Kattis exercises, Wolfram mathematical references, museum exhibitions, recipes, sewing patterns, botanical events, and walking trails. This spans tables, cards, nested lists, navigation-heavy indexes, download catalogs, and collapsed rows.

91 candidate sites were attempted. There were 70 saved HTML captures: 51 reviewed listing scopes and 19 acquisition-only captures, such as JavaScript shells, challenges, or pages without the intended inventory. Another 21 attempts were unavailable, robots-disallowed, or exceeded the collection pacing limit. Neither group counts toward evaluated website coverage.

## Extraction results on the 51 new pages

These are full-parser results from a single saved HTML page per website. They do not benchmark the live discovery pipeline, API adapters, browser rendering, pagination, complete archives, or date accuracy. "Out of scope" means outside the intended listing, not necessarily useless content.

| Cohort | Sites | Intended URLs | Recovered | Out of scope | Precision | Recall | Mean per-site F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Cultural | 17 | 721 | 592 | 42 | 93.4% | 82.1% | 0.872 |
| Public | 16 | 488 | 437 | 7 | 98.4% | 89.5% | 0.863 |
| Technical | 18 | 3,620 | 2,587 | 2,205 | 54.0% | 71.5% | 0.539 |
| All new pages | 51 | 4,829 | 3,616 | 2,254 | 61.6% | 74.9% | 0.752 |

The parser recovered every intended URL on 30 pages; 19 of those also contained no out-of-scope results. Five pages returned none of their intended URLs. Large catalogs dominate pooled URL counts, so the per-site score is also reported.

## Classifier comparison

All four bundled link classifiers were evaluated at their existing thresholds, with the native inference kernel available. These metrics apply only to generated candidates, which cover 4,591 of the 4,829 intended URLs. They must not be interpreted as end-to-end extraction recall.

| Classifier | Precision | Candidate recall | URL F1 | Mean per-site F1 |
| --- | ---: | ---: | ---: | ---: |
| legacy | 76.7% | 22.3% | 0.345 | 0.353 |
| context | 64.6% | 58.8% | 0.616 | 0.561 |
| deep alone | 82.6% | 24.4% | 0.377 | 0.328 |
| cascade | 64.9% | 59.8% | 0.622 | 0.594 |

The deployed cascade recovered 42 more relevant candidate URLs than the context classifier with the same 1,482 false positives. The standalone deep network had much lower recall at its current threshold. This result does not support replacing the cascade with the standalone network without further training and evaluation.

## What the broader sample exposes

1. **Candidate budgets can hide relevant links.** IANA has 637 intended URLs, but only 399 reach candidate classification under the current anchor cap. Other parser paths still recover those URLs, while also including 1,305 out-of-scope links. Prefer prioritizing repeated content records before truncation, while retaining the existing resource bounds.
2. **Scope and grouping need improvement.** OpenSSL, IANA, GNU, and BusyBox mix content with references, navigation, indexes, or alternate downloads. Better list-boundary and record-role features are more useful here than simply increasing network size.
3. **Non-media inventories remain difficult.** The saved cURL advisories, kernel releases, Unicode reports, USGS publications, and puzzle index have zero parser recall. Samba recovers 8 of 545 intended version entries; Git reference recovers 1 of 78. These are useful regression cases, not evidence of complete site support.
4. **Dense cards and sibling metadata need better grouping.** Allrecipes returns 14 of 72 intended recipe URLs, FDA 3 of 10 recall URLs, and BLS 21 of 50 stable release URLs. Follow-up work should inspect candidate construction, classifier scores, and parser selection separately.
5. **Acquisition remains a separate problem.** Saved shells from sites such as OpenML and LLVM cannot fairly measure classification of an absent inventory. Their captures remain available for future acquisition tests without inflating classifier coverage.

Any subsequent development on these new examples should reclassify them as development/regression data and reserve another source-disjoint holdout. The next evaluation should add independently reviewed labels, title/date association checks, and temporal recaptures before making stronger accuracy claims.

## Collection and label safeguards

- One listing per site, robots first, Trackify identification, at least two seconds between same-site requests, and stricter advertised crawl intervals up to 30 seconds. Longer requested intervals stop collection.
- At most six requests and 10 MB received per source, including redirects and robots. The existing per-response safety cap also applies. Cross-host redirects outside the reviewed host and its www alias are refused. Listing redirects are checked against the saved robots policy.
- No content-detail downloads, pagination crawls, browser execution, credentials, access-control workarounds, or retries after refusals. The run recorded **174 network requests and 16,545,311 received bytes**. Offline evaluation made no requests.
- A final [offline policy audit](breadth-robots-audit.json) checked original and final URLs for all 51 evaluated sites: 44 verified saved robots policies and seven original robots 404 responses, with no disallowed evaluated URLs.
- Positive scopes were annotated from saved DOM before predictions, using selectors and explicit expected URL sets. Captures and scopes are hashed, and the evaluator rejects changed labels or captures.
- A separate reviewer inspected six varied scopes without seeing predictions. After the initial run, 123 GNU submanual URLs, 62 NGINX CVE references, and one Netlib alternate scan were made neutral. Positive labels did not change. The [initial technical report](breadth-technical-initial.json.gz) and per-source review provenance are retained; this is not a pristine blind evaluation.
- Labels are assistant-reviewed weak scope annotations, not independent human gold. Website families can share publishers or templates. RHS has one target URL; excluding that sparse page still leaves 112 families.
- Historical coverage includes training, validation, test, and holdout pages. Historical results keep their original reports and are not pooled into a current accuracy score. No new family overlaps recorded source/page URLs in existing JSONL datasets; synthetic or missing origin metadata cannot prove full domain disjointness.

## Resource observations

The median per-page offline parser time was 0.273 seconds; the largest IANA listing took 12.834 seconds. Summed per-source median cascade inference time was 0.436 seconds across 16,809 rows. These are local Windows measurements, with some overlapping runs, not production latency or speedup benchmarks.

Peak evaluator process memory ranged from 72.5 to 224.8 MiB. The process loads all four models and accumulates feature rows, so these are not per-request production memory measurements.

## Reproduce

Raw HTML and robots files stay locally under ignored `backend/data/breadth-{cultural,technical,public}/`. They are not committed. Frozen numeric/token features, source scopes, hashes, and results are committed. Keep the exact captures to reproduce parser results; recollecting a live page creates a new snapshot and requires new annotations.

From the repository root, with backend dependencies installed:

```sh
python backend/ml/evaluate_breadth.py --manifest backend/ml/datasets/breadth-cultural-sources.json --output backend/ml/reports/breadth-cultural.json --dataset backend/ml/datasets/breadth-cultural-dataset.jsonl --repeats 3
```

Repeat with `technical` and `public`, then aggregate:

```sh
python backend/ml/summarize_breadth.py --manifest backend/ml/datasets/breadth-cultural-sources.json --manifest backend/ml/datasets/breadth-technical-sources.json --manifest backend/ml/datasets/breadth-public-sources.json --report backend/ml/reports/breadth-cultural.json --report backend/ml/reports/breadth-technical.json --report backend/ml/reports/breadth-public.json --output backend/ml/reports/website-breadth.json
```

The optional collector requires `--fetch` and a reviewed manifest. Terminal attempt states are retained, so rerunning a command does not automatically refetch refused or completed sites.

## New website inventory

| Website family | Subject | Intended | Recovered | Out of scope |
| --- | --- | ---: | ---: | ---: |
| [abs.gov.au](https://www.abs.gov.au/release-calendar/latest-releases) | official-statistics | 20 | 20 | 0 |
| [allrecipes.com](https://www.allrecipes.com/recipes/78/breakfast-and-brunch/) | recipes | 72 | 14 | 8 |
| [bankofcanada.ca](https://www.bankofcanada.ca/press/press-releases/) | monetary-policy | 10 | 10 | 0 |
| [bea.gov](https://bea.gov/news/current-releases) | economic-statistics | 12 | 12 | 0 |
| [bls.gov](https://www.bls.gov/bls/newsrels.htm) | labor-statistics | 50 | 21 | 5 |
| [boardgamegeek.com](https://boardgamegeek.com/browse/boardgame) | board games | 100 | 72 | 1 |
| [budgetbytes.com](https://www.budgetbytes.com/category/recipes/) | recipes | 47 | 47 | 0 |
| [busybox.net](https://busybox.net/downloads/) | embedded software releases | 131 | 131 | 195 |
| [clevelandart.org](https://www.clevelandart.org/exhibitions) | museum exhibitions | 28 | 28 | 0 |
| [consumerfinance.gov](https://www.consumerfinance.gov/about-us/newsroom/) | consumer-finance | 21 | 21 | 0 |
| [cp-algorithms.com](https://cp-algorithms.com/navigation.html) | algorithm reference | 163 | 148 | 4 |
| [curl.se](https://curl.se/docs/vulnerabilities.html) | security advisories | 69 | 0 | 0 |
| [data.gov](https://catalog.data.gov/dataset/) | government data catalogs | 20 | 20 | 0 |
| [fda.gov](https://www.fda.gov/safety/recalls-market-withdrawals-safety-alerts) | product-recalls | 10 | 3 | 0 |
| [fdic.gov](https://www.fdic.gov/news/press-releases) | banking-regulation | 20 | 20 | 0 |
| [federalreserve.gov](https://www.federalreserve.gov/newsevents/pressreleases/2026-press.htm) | monetary-policy | 101 | 100 | 0 |
| [ftc.gov](https://www.ftc.gov/news-events/news/press-releases) | consumer-regulation | 20 | 20 | 0 |
| [git-scm.com](https://git-scm.com/docs) | version control reference | 78 | 1 | 0 |
| [gnu.org](https://www.gnu.org/manual/manual.html) | software manuals | 387 | 154 | 235 |
| [greenend.org.uk](https://www.chiark.greenend.org.uk/~sgtatham/puzzles/) | puzzles | 40 | 0 | 3 |
| [iana.org](https://www.iana.org/protocols) | internet standards registries | 637 | 637 | 1305 |
| [kattis.com](https://open.kattis.com/problems) | programming exercises | 100 | 97 | 1 |
| [kernel.org](https://www.kernel.org/) | software releases | 8 | 0 | 0 |
| [kew.org](https://www.kew.org/science/read-and-watch) | botanical science | 12 | 12 | 0 |
| [kingarthurbaking.com](https://www.kingarthurbaking.com/recipes/collections/sourdough-bread-recipes) | recipes | 19 | 19 | 4 |
| [louvre.fr](https://www.louvre.fr/en/explore/exhibitions) | museum exhibitions | 7 | 7 | 0 |
| [moca.org](https://www.moca.org/exhibitions) | museum exhibitions | 6 | 6 | 0 |
| [moma.org](https://www.moma.org/calendar/exhibitions) | museum exhibitions | 27 | 27 | 0 |
| [moodfabrics.com](https://blog.moodfabrics.com/free-sewing-patterns/) | craft patterns | 217 | 217 | 19 |
| [nationaltrail.co.uk](https://www.nationaltrail.co.uk/en_GB/trails/) | outdoor trails | 16 | 16 | 0 |
| [netlib.org](https://www.netlib.org/lapack/lawnspdf/) | numerical research | 294 | 294 | 1 |
| [nginx.org](https://nginx.org/en/security_advisories.html) | security advisories | 47 | 3 | 0 |
| [nybg.org](https://www.nybg.org/visit/calendar/) | garden events | 25 | 25 | 4 |
| [ons.gov.uk](https://www.ons.gov.uk/releasecalendar?view=published) | official-statistics | 10 | 10 | 0 |
| [openbsd.org](https://www.openbsd.org/faq/) | operating-system documentation | 12 | 12 | 4 |
| [openlibrary.org](https://openlibrary.org/trending/daily) | book catalog | 20 | 20 | 2 |
| [openssl-library.org](https://openssl-library.org/news/vulnerabilities/) | security advisories | 106 | 93 | 426 |
| [rba.gov.au](https://www.rba.gov.au/media-releases/) | monetary-policy | 87 | 87 | 0 |
| [rhs.org.uk](https://www.rhs.org.uk/plants/types/perennials) | gardening | 1 | 1 | 0 |
| [samba.org](https://www.samba.org/samba/history/) | network file-sharing software | 545 | 8 | 5 |
| [sec.gov](https://www.sec.gov/newsroom/press-releases) | financial-regulation | 25 | 21 | 0 |
| [seriouseats.com](https://www.seriouseats.com/italian-recipes-5117472) | recipes | 63 | 60 | 0 |
| [sphinx-doc.org](https://www.sphinx-doc.org/en/master/changes/index.html) | documentation software releases | 48 | 47 | 0 |
| [supremecourt.gov](https://www.supremecourt.gov/opinions/slipopinion/25) | court-decisions | 74 | 70 | 0 |
| [treasury.gov](https://home.treasury.gov/news/press-releases) | fiscal-policy | 10 | 10 | 1 |
| [uci.edu](https://archive.ics.uci.edu/datasets) | machine learning datasets | 10 | 10 | 0 |
| [unicode.org](https://www.unicode.org/versions/enumeratedversions.html) | text encoding standards | 33 | 0 | 29 |
| [usgs.gov](https://pubs.usgs.gov/) | earth-science | 6 | 0 | 0 |
| [whitney.org](https://whitney.org/exhibitions) | museum exhibitions | 21 | 21 | 1 |
| [wmo.int](https://wmo.int/news) | climate-weather | 12 | 12 | 1 |
| [wolfram.com](https://mathworld.wolfram.com/letters/A.html) | mathematical reference | 932 | 932 | 0 |

Defensible project wording: **Evaluated link extraction across 100+ websites spanning media, public data, software, education, and cultural catalogs.**
