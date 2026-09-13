# Learned refresh extraction

Refresh now reuses a validated page recipe when one is available. The first scan
uses full detection and learns URL formats, DOM record boundaries, title fields,
and date locations. The adjacent refresh menu offers **Deep refresh** for one
item and **Deep refresh all** for the library.

## Behavior

- A recipe contains bounded JSON data, never executable code or generated regex.
  URL shapes retain the host and parent path; numeric IDs and terminal slugs can
  vary. Record structure, surrounding containers, field locations, and accepted
  versus rejected link groups come from the full parser's decisions.
- Learning includes a second pass over the same HTML. Its entire result must
  equal full detection: URLs, titles, order, dates and precision, languages,
  context, suggestions, pagination, warnings, and other scan metadata.
- Stable recipes bypass the generic link classifiers and reuse learned record
  boundaries. Values are extracted from the new HTML on every pass. Ambiguous
  table neighborhoods still use the existing record-context model; job titles,
  relative listing ages, language filters, and Asura's exact Astro dates retain
  their existing extraction logic.
- Any unfamiliar anchor shape or URL format, changed structural evidence,
  changed scripts/table headers, missing known link, or lost/changed date
  evidence causes a full scan of the **same fetched HTML**. The failed fast pass
  is discarded before persistence. The full scan may replace the recipe.
- Recipes expire after seven days or 20 applications to changed documents. They
  share the existing bounded cache, survive restarts, and invalidate with parser
  or model versions. Each recipe is capped at 600 KB and 2,048 layout policies;
  normal 4,999-link and request/admission limits still apply.
- Explicit CSS selectors/path restrictions and authoritative structured
  adapters retain their existing detection. No extra libraries or model weights
  are installed. Two bounded analysis processes remain the production default.

Both modes traverse the full discovered listing/pagination within existing crawl
limits. They do not stop at previously seen links, fetch individual content for
dates, or add verification requests. Deep refresh bypasses complete-scan and
parsed-result caches and rebuilds the recipe; it **retains the ten-minute HTTP
cache and request pacing** to prevent repeated clicks from overscraping.
Unchanged HTML can still reuse its exact parsed result in the default mode.

## Measured results

Measured September 13, 2026 on the Azure VM, Python 3.12, warm model weights,
isolated container limited to two CPUs and 512 MiB. Three paired analysis runs
per case; raw measurements and output hashes are in `recipe-benchmark.json`.
Public captures were already saved, so the benchmark made zero source requests.

| Listing | Links | Full analysis | Learned analysis | Speedup |
| --- | ---: | ---: | ---: | ---: |
| Current Asura / Dungeon Odyssey | 169 | 1.74 s | 0.34 s | 5.0× |
| Nested chapter cards | 1,000 | 1.93 s | 0.99 s | 2.0× |
| SimplifyJobs application table | 911 | 4.73 s | 3.33 s | 1.4× |
| Royal Road | 109 | 190 ms | 168 ms | 1.1× |
| Hacker News fixture | 4 | 32 ms | 18 ms | 1.8× |
| xkcd archive fixture | 4 | 5.3 ms | 3.2 ms | 1.7× |
| Simplified Asura fixture | 140 | 152 ms | 158 ms | 0.96× |
| NovelsHaven structured index | 221 | 103 ms | 104 ms | Existing adapter |

All outputs match exactly for these cases. The simple Asura fixture illustrates
the tradeoff: shape validation costs about 6 ms more where detection was already
cheap. Gains on expensive real pages justify the default; recipes do not promise
lower latency on every page. Network, pagination, rate limits, and database work
are excluded from these timings. The benchmark's peak container memory was
113 MiB, not a guarantee for simultaneous maximum-size scans.

Initial learning has a cost: approximately 2.2 s for current Asura, 4.0 s for
1,000 nested cards, and 9.0 s for the job table, including the validation pass.
Unsupported or conflicting layouts keep full detection. A layout can also
change semantically without changing its DOM; structural guards and periodic
full scans reduce this risk but cannot establish universal extraction accuracy.

## Verification

`tests/test_recipes.py` checks new URLs, changed dates/languages, collapsed and
paginated listings, layout/script/URL drift, missing links and fields, timestamp
precision changes, corrupt/expired recipes, persisted cache reuse, explicit
selectors, worker serialization, and API mode routing. Frontend tests cover
default/deep selection, busy controls, and both request paths.

The full suites pass: **293 backend tests and 61 frontend tests**. The production
frontend builds successfully. Manual browser checks at 320 px and 1280 px verify
the split refresh control and menu; narrow library actions wrap without page
overflow and the menu stays within the viewport.

`verify_recipe_runtime.py` exercises real HTTP requests through the production
process pool with a disposable library: full initial scan of 1,000 links, a
lightweight refresh adding link 1,001, and an explicit uncached deep refresh.
Only three listing responses are replayed; zero chapter URLs are opened.

Reproduce with the backend environment (captures are optional and stay untracked):

```sh
python ml/benchmark_recipes.py --output /tmp/recipe-benchmark.json
python ml/verify_recipe_runtime.py
python -m pytest -q
```

Existing items learn on their next uncached scan. There is no library migration
or bulk source prewarming, and saved reading progress is unchanged.
