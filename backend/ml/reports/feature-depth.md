# Feature depth and model budget

## Plan and scope

Prior profiling found link-model inference used about 1.6% of parser CPU after optimization; source pacing still controlled refresh wall time. Spend the available model budget where evaluation identifies mistakes. Do not increase scraping or load a transformer into each analysis worker simply because RAM is available.

| Area | Implemented depth | Remaining useful work |
| --- | --- | --- |
| Media classification | Compare field-aware linear and neural classifiers on public, domain-separated data; deploy only improvements that preserve adapter evidence and manual overrides. | Expand independent, rare-format and sparse-metadata evaluation before increasing complexity. |
| Library search | Stable metadata-only index, packed posting lists, bounded query caches, background worker for 50+ items, immediate local matches, and Quick search without semantic API requests. | Evaluate multilingual semantic relevance on a larger independent query set. |
| Library organization | Save up to 12 private views combining query, status, media type, order, and search mode; edit/delete them and restore controls with browser back/forward. | Consider user-defined tags after observing whether saved views cover recurring tasks. |
| Reading progress | Continue opens the next unread entry by detected number/date/source order; skips muted, trashed and future scheduled content. Grouped or linkless entries open the unread list. | Add optional per-item continuation order if non-serial sources need a different workflow. |
| Refresh | Preserve full-source refresh, cache and pacing safeguards; speed up the summary reads used by refresh notifications. | Network/host wait improvements must preserve request limits and coverage. |
| Import and account data | Imported entries participate in Continue and views; export and account erasure include saved views. | Keep email recovery delivery disabled until the operator configures SMTP. |

Suggestions remain hidden, as requested previously. No additional hosted service, database or GPU is required.

## Browser and database work

Browser lexical ranking uses saved library metadata in memory. It makes no cross-site requests, downloads no model, and writes no private search index to browser storage. The worker is terminated on teardown; failures fall back to inline ranking. Smart search retains the existing server semantic encoder. Quick search performs local lexical/typo search only, so it is a clear performance/relevance choice rather than silently removing semantic results.

Saved-view writes are authenticated, same-origin checked, parameterized and transactional. Input lengths, valid filter values and a concurrent-safe 12-view cap are enforced server-side. Views are per-library and included in account exports, erasure and backup scrubbing. A database error reports failure without pretending a save succeeded.

Continue uses read-on-open only for a single destination URL. A merged entry can contain unread members even if its root is read; opening its unread list avoids marking unseen members read. Existing Latest remains available; merged Latest entries also open the item without marking unseen group members read. Continue returns an explicit sort, page offset and row target so muted metadata or earlier future releases do not redirect it to the wrong entry. Continued entries are picked independently of the current library search and link-page order.

Library summaries read a consistent SQLite snapshot. Libraries without display groups use the underlying links table, avoiding an unnecessary self-join/aggregation. Latest and Continue share one bounded, per-item materialization for grouped and ungrouped libraries.

## Verification

See [browser search measurements](browser-search.md) and the [media classifier report](media-strength.md) for model/search evidence. Database timing uses synthetic 25-item/2,500-link and 200-item/100,000-link libraries, both ungrouped and pairwise merged. The latter is the configured account cap, not a typical user's workload. Seven timed repeats follow one warm-up. Previous fields are compared exactly and continuation identity is checked.

Reproduce the storage comparison from the repository:

```sh
cd backend
uv run python ml/profile_library_views.py --baseline-ref b5ab2b9
```

`--baseline-file` accepts a local copy of that trusted baseline store module for image testing without Git. These are library-read timings, not total network refresh times.

The browser implementation follows the [Web Workers lifecycle and message-passing API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Workers_API/Using_web_workers). Source-separated evaluation avoids training and testing on records from the same website, following [grouped cross-validation guidance](https://scikit-learn.org/stable/modules/cross_validation.html#cross-validation-iterators-for-grouped-data).

## Candidate image measurements and checks

The final image was checked on the Azure VM with networking disabled, two CPUs, and a 1 GiB memory cap. Synthetic libraries used disposable storage; no user account was modified for profiling.

| Library | Previous summary | Summary with Latest + Continue |
| --- | ---: | ---: |
| 25 items / 2,500 links, ungrouped | 22.71 ms | 8.49 ms |
| 200 items / 100,000 links, ungrouped | 905.38 ms | 269.16 ms |
| 25 items / 2,500 links, paired groups | 15.76 ms | 17.84 ms |
| 200 items / 100,000 links, paired groups | 610.56 ms | 681.91 ms |

Ordinary libraries benefit from avoiding unused group aggregation. Grouped libraries pay about 2 ms in the smaller fixture and 71 ms at the full account cap for the additional unread ranking and exact landing position. This is a documented feature cost, not a claim that every query became faster. Raw timings: [library-runtime-linux.json](library-runtime-linux.json).

The media benchmark reported 0.62 ms/item for the previous rules, 1.14 ms for learned classification, and 1.84 ms for complete metadata generation. Process peak RSS was 36.6 MiB for that isolated benchmark; this is not whole-service peak RAM. No ONNX/tokenizer loaded, and float32 versus float64 decisions were unchanged across 69 profiles (maximum probability-score error 1.79e-7). Raw results: [media-runtime-linux.json](media-runtime-linux.json).

Validation: full backend suite 990 passed before the final landing-position refinement, followed by 52 storage/grouping/continuation regressions passed on final code; full frontend suite 176 passed, with 65 focused component tests covering subsequent landing refinements; Ruff, ESLint and production build passed. The isolated candidate additionally passed private saved-view CRUD, account isolation, export/deletion, next-unread progression, public routes and disabled-mail checks. Semantic requests have an eight-second deadline; failed workers and invalid replies fall back without leaving selection permanently disabled.

Interactive visual QA could not run because the Windows browser helper failed while applying sandbox ACLs. Layout and navigation were checked through component tests and production bundling; no visual-browser verification is claimed.
