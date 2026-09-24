# Listing details: LionDine and context expansion

The September 24, 2026 saved LionDine late-night page now yields all 55 food
labels: 21 for JJ's and 34 for Diana. The former parser recovered none of those
foods in its final entries. Closed halls retain their status. Hewitt and Diana
share a destination URL, so one entry retains both named sections rather than
creating a duplicate URL. Menu facts describe this snapshot only.

## Runtime changes

- Heading links work in both HTML directions: a heading wrapping an anchor and
  an anchor wrapping a heading. Accepted fallback URLs can reuse their visible
  listing context without following any content links.
- When the model accepts no region, a bounded structural check can recover a
  single-target heading card. It requires a complete DOM sample, one outgoing
  target, no navigation, and a semantic record or repeated record wrapper.
  Confident model decisions and missing-model fail-closed behavior are preserved.
- Visible headings, lists, table rows, and repeated food names retain line
  breaks. Optional tooltips cannot displace the main text. Tooltips attached to
  visible labels are omitted instead of becoming an unattributed appendix.
- The formatter has node, depth, text-work and output bounds and a per-page
  cache. Model feature snippets stay unchanged. Duplicate URL contexts retain
  separate lines. The discovery version invalidates stale scan/recipe results.
- Website entries now expose Details, including merged entries. Empty,
  title-only, and exact summary duplicates remain hidden. Paragraph spacing is
  compact; desktop, 390px and 320px layouts were visually checked.

No source-specific extraction adapter or extra content-page request was added.
A refresh updates saved context. A layout without a safe lightweight recipe
falls back to analysis of the same fetched listing.

## Offline measurements

[Full-page evidence](context-expansion-pipeline-v1.json.gz) compares the frozen
`3eebc05` parser and record model against the release implementation, using the
production cascade link classifier and eleven saved public listings. All runs
used saved HTML and made zero HTTP requests.

| Measurement | Before | After |
| --- | ---: | ---: |
| LionDine food labels in final entries | 0/55 | 55/55 |
| Cross-target food leakage | 0 | 0 |
| LionDine warm median, five runs | 78.50 ms | 73.09 ms |
| LionDine Python allocation peak | 1,282,910 bytes | 1,323,024 bytes |
| Annotated full-page required facts | 32/83 | 54/83 |
| Annotated neighboring-fact leaks | 0 | 0 |
| Sum of ten page warm medians | 2.466 s | 2.568 s |

These are local diagnostic timings, not a general speedup claim. Peak allocation
uses `tracemalloc`, not total process RSS. The large W3 listing was measured once
per parser in bounded subprocesses instead of repeating a long parse; it took
10.21 versus 9.99 seconds and remains an acquisition/analysis bottleneck.

Thirty of 33 annotated targets have unambiguous URLs; three SQLite anchors share
a JavaScript-rewritten href and are excluded from aggregate URL scoring. The
family-disjoint diagnostic full-page set still recovers zero of 23 required
facts. The context specialist can locate some records whose links the whole
parser excludes, so context-only measurements must not stand in for end-to-end
coverage. This release fixes the dining case, not general web discovery.

## Efficient data expansion

The [dataset card](../datasets/context-expansion-v1-card.json) describes eleven
real site families, 33 reviewed targets, 92 required facts and 69 neighboring
facts. Captures use robots/pacing checks and bounded requests; one source that
returned 403 was excluded without bypassing it. The compact fixtures occupy
55 KB versus 1.87 MB of full captures.

Annotate a record boundary and its facts once, then generate candidate regions
and label-preserving DOM variations locally. Three development-only mutations
produce ten unique additional pages without more requests. Entire source
families stay in one split, duplicate HTML is checked, and variants share their
original anchor's training weight. Exact repeated feature/label rows are folded
into weighted rows to keep training small.

[Neural/tree experiments](context-expansion-models.md) document two fixed
comparisons and a flaw found in the old synthetic split. Neither candidate
improved fresh context recovery safely, so production weights remain unchanged.
Archived experiment measurements preceded the final structural fallback and
record their source hashes; rerunning the tools uses the current runtime.

## Reproduction

From `backend`, run `python -m ml.context_expansion.pipeline_benchmark` with the
ignored full captures referenced by the source manifest. The default baseline
directory contains `parser.py`, `record_context.py`, and `record-context-model.json`
from commit `3eebc05`. The script checks capture hashes before replay, alternates
warm parser order, and isolates memory measurements from latency measurements.
Use `--only liondine.com` for a short repeat or `--output` for a fresh report.

Normal tests use the committed minimized dining fixture and synthetic isolation
cases. They cover all 55 foods, shared URLs, refresh updates, misleading neighbors
beyond the DOM limit, language metadata, hidden/script content, and web/merged
Details rendering without making network requests.
