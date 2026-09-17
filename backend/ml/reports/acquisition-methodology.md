# Generalizing listing acquisition

September 16, 2026. Compared with deployed commit `dc2cf07`. No model weights,
dependencies, database migrations, or new services are needed.

## Findings from accessible sources

One public listing response was captured per source (NASA redirected from its
old news URL). Subsequent development and profiling replayed those files without
network access. No article, chapter, paper, or download body was requested.

| Source | Layout or failure | Relevant records before → after | Dated relevant records before → after |
| --- | --- | ---: | ---: |
| [arXiv AI submissions](https://arxiv.org/list/cs.AI/recent) | Links and titles are in paired `dt`/`dd` rows; date belongs to a group heading; pagination labels are ranges | 0 → 50 | 0 → 50 |
| [Python downloads](https://www.python.org/downloads/) | Release rows use abbreviated months with periods; a content list has the CSS class `menu` | 260 → 260 | 107 → 260 |
| [Django weblog](https://www.djangoproject.com/weblog/) | Publication metadata is beside the heading, while the headline/summary mentions a different date | 10 → 10 | 1 → 10 |
| [NASA recently published](https://www.nasa.gov/news/recently-published/) | Global menus use divs; cards have duplicate thumbnail/title links; JavaScript pager says “Goto Next Page” | 22 → 25 | 16 → 18 |
| [Hugging Face papers](https://huggingface.co/papers) | Metadata is serialized in an HTML attribute and keyed by paper ID; some anchors say “View paper” | 25 → 25 | 0 → 25 |
| [Project Gutenberg releases](https://www.gutenberg.org/ebooks/search/?sort_order=release_date) | Head and body advertise different Next URLs; the head URL loses the release-order filter | 25 → 25 | 25 → 25 |
| [Rust blog](https://blog.rust-lang.org/) | Large table archive; regression control | 391 → 391 | 391 → 391 |
| [Smashing Magazine articles](https://www.smashingmagazine.com/articles/) | Semantic article cards and numbered page paths; regression control | 10 → 10 | 10 → 10 |

Across these captured pages: **743/796 → 796/796 relevant records**, with
**23 → 0 unrelated records**. The unrelated records were on NASA's page.
The old Django date was also wrong: it used a September 22 deadline instead of
the September 16 publication label. Dates are evidence, not interchangeable
timestamps: arXiv's group dates are marked **listed**; Rust's existing dates
remain **inferred from URLs**; seven NASA records remain undated. This is a small
development set, not an estimate of accuracy across the web or complete archives.

## Implemented methodology

1. **Inspect the response before fetching more.** Keep existing public API/feed
   support, HTML records, and script hydration. Also read JSON from `data-props`
   and `data-page` attributes. Do not execute JavaScript in the parser. Join
   metadata to an already observed URL only when its ID resolves uniquely;
   never manufacture a route from an ID. Prefer a specific title over “View
   paper.” Parent curation timestamps do not replace a nested paper's own date.
2. **Infer records, not individual anchors.** Preserve table/card semantics and
   add paired definition-list rows. For repeated sibling cards, require the
   existing classifier to accept at least two records and at least half of the
   group before extending it. Card titles and dates stay within their record.
   Menu landmarks and navigation regions are excluded, while a main-content
   wrapper called `sidebar-right` or a content list called `menu` stays usable.
3. **Rank date evidence locally.** Structured timestamps and explicit
   publication labels outrank incidental dates in a heading or summary.
   Date-only metadata supports abbreviated months. Definition-list date headings
   apply only to their group, with listed-date provenance. Missing dates are
   left missing; content pages are not opened to fill them.
4. **Follow one observed forward chain.** Recognize numeric ranges and offset
   coordinates as well as Next links. Preserve source filters such as language,
   category, and sort order; do not guess URLs or choose an unbounded “all” link.
   Novel pagination stays within its collection. If the server returns the same
   records under changing page URLs, stop that branch and report partial coverage.
5. **Escalate only when evidence calls for it.** Existing browser fallback handles
   replaced or appended JavaScript lists; control recognition now includes
   “Goto Next Page.” Forms, carousels, disabled controls, non-GET requests,
   credentials, and private-network destinations remain disallowed. Reuse the
   shared five-minute source cache, validated lightweight recipes and observed
   API recipes. Keep the 4,999-entry cap and request/time/memory budgets.

The new production helpers do not check any of the above hostnames. Source-specific
selectors appear only in the evaluation tool to define the expected output.
Deduplication happens before linked/list-only records are reconciled, so duplicate
representations cannot discard language or nearby context.

## Regression and performance gates

- Minimized, renamed structural cases in `tests/test_listing_structure.py` cover
  title/date separation, ambiguous IDs, forbidden route invention, menu/content
  discrimination, missing-date neighbors, query preservation, review pagination,
  and repeated pages. Existing extraction, security, cache and recipe tests run
  alongside them.
- Replay real pages against a recorded baseline, comparing exact URL sets,
  titles/date provenance, unwanted records, pagination scope, latency and peak
  RSS. Use source-level controls, not just aggregate recall. Preserve publication
  and curation distinctions when labeling.
- Verify deep/light equivalence. Six of the eight current fixtures reuse a
  validated recipe; arXiv and NASA safely use full analysis. All eight produce
  identical output when a lightweight refresh is requested.
- Exercise JavaScript controls in the isolated real Chromium worker with
  appended lists, replaced lists, navigated pages and an unchanged-content loop.
  These fixtures request no article bodies. Detection of NASA's control does not
  establish full traversal of its thousands of archive pages; that remains
  bounded and dependent on its scripts and allowed requests.

The recorded JSON reports contain source hashes and five warm samples' median
parse times, using the same native cascade in separate network-disabled Linux
containers limited to 1 GiB and two CPUs. Peak RSS measures the benchmark parser
process, not the whole production service or Chromium. Extra structural checks
have a CPU cost; the report retains that cost rather than claiming a speedup.
Profiling removed repeated per-anchor selector calls from the new card pass.

Final native-container measurements: summed per-page medians increased from
3,988.92 ms to 4,455.06 ms (**11.7%**, about 58 ms more per source on average).
Peak parser RSS was **59.1 MiB**. This is an explicit accuracy/coverage tradeoff,
not a refresh-speed improvement. Network fetches, browser startup, concurrent
users and the app's worker pool are outside this microbenchmark.

Validation: **776 backend tests**, **107 UI tests**, frontend lint and production
build. The real isolated Chromium run recovered 12 dated entries for both append
and replaced-Next layouts, six for navigated pages, and stopped an unchanged list
after two steps; it made zero article requests. Desktop and 390-pixel mobile UI
checks covered file selection, filename/size display, CSV preview and footer links.

From the repository root, replay existing local captures:

```sh
backend/.venv/Scripts/python.exe backend/ml/evaluate_acquisition.py --check-recipes --output backend/data/acquisition.json
```

Add `--capture` only to intentionally refresh the eight public listing captures.
That path uses SafeFetcher, its shared cache, at most two concurrent sources,
and a four-request budget per source. Capture URLs/HTML stay under ignored
`backend/data/source-diversity`; the committed reports retain hashes and metrics.
`--app-root` selects an archived baseline. On Linux use the corresponding Python
environment executable.

## Extending coverage without multiplying adapters

For each new failure, label its cause first: missing acquisition channel,
incorrect record boundaries, noisy title/date extraction, or pagination scope.
Capture once, reduce to a structural regression, change a shared capability,
then replay both the failing source and unrelated controls. Add a hostname adapter
only when a documented source contract cannot be represented safely by these
shared capabilities. Future datasets should cover non-English controls/dates,
multiple competing lists, infinite-scroll cursor APIs, and virtualized tables;
these are not claimed as solved by this eight-source evaluation.

Always expose partial coverage when bounded retrieval cannot prove completeness.
Authentication, access checks, unsupported requests and ambiguous layouts remain
real limits; broader acquisition must not turn into uncontrolled crawling.
