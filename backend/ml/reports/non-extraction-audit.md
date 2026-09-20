# Additional non-extraction model audit — 2026-09-20

This is an evaluation of the frozen media classifier, MiniLM library retrieval, extractive descriptions, and hidden content suggestions. No weights or runtime thresholds were changed based on these results. The media artifact hash is recorded in the source manifest and checked by the audit script. The current description safety changes are included as `profile-v2-safety`.

## Additional data and boundaries

Twelve new public source URLs were declared before classification. Six produced usable listing captures; five could not be collected through the bounded fetch/robots path, and one returned an AWS WAF challenge. None of the unavailable/challenged sources was retried or bypassed. The successful sources are Rusty Quill's Magnus Archives, Lightspeed fiction, Bandcamp Daily, Open Library trending books, 80.lv articles, and Hugging Face papers. Three already-collected pages from the concurrent extraction audit—Syntax, Lex Fridman and Unsong—were reused without more network requests.

The resulting **nine profiles come from nine hosts absent from the project's earlier media datasets**. This means project-level source separation, not a claim that the pretrained sentence encoder has never encountered these websites. No private libraries or actual user queries were used. Labels describe the intended media inventory; Lightspeed's short fiction is assigned to the existing Books & novels category. Real music catalogs remain missing in this new sample because the declared music sources were unavailable or challenged. Authored music examples are reported separately and do not fill that evidence gap.

Thirty relevance queries were authored before model outputs were inspected. Twelve queries whose intended sources could not be collected are explicitly excluded, leaving 18. The retrieval corpus contains the nine new profiles plus 47 historical profiles as distractors. Relevance labels identify intended sources, not exhaustive judgments of every possibly related result. These small, mostly English samples are diagnostic rather than a general accuracy estimate.

Committed fixtures retain bounded metadata, sampled title/URL pairs and hashes. Raw public HTML remains in ignored `backend/data/non-extraction-audit` and the shared extraction capture directory. Collection is opt-in, caches saved captures, never follows detail links, preserves rejected results, caps concurrency at three, and now enforces twenty declared sources, forty network requests and twenty megabytes per pass. These are collector limits, not a retrospective exact request count for the first capture run.

## Media classification

| Frozen comparison | Correct new public profiles |
| --- | --- |
| Prior rules | 6 / 9 |
| Deployed learned classifier with guards | 6 / 9 |

The learned classifier corrects Hugging Face papers from blog to research, but regresses Magnus Archives from podcast to generic website. Its website score is only 0.357 with a 0.133 margin. The page's externally distributed episode links satisfy the aggregator safeguard even though external hosting does not imply a generic directory. Syntax remains blog because mixed episode/sidebar text overwhelms its podcast evidence; removing its summary or entries in a diagnostic ablation restores podcast. Bandcamp Daily remains website instead of music journalism. All three new fiction/book sources are classified as novel.

The seven **authored stress cases** are separate from public accuracy. Explicit music, fiction, music-news and software-news evidence is handled correctly; information-poor music and podcast cases return website, which is a reasonable abstention given their missing format evidence. An explicitly described Spanish podcast still returns website. Increasing classifier capacity alone does not address missing evidence, English-only features, or confusion between distributed content and aggregation.

## Library retrieval

| Query group | Queries | Text-only top 1 / top 3 | MiniLM hybrid top 1 / top 3 |
| --- | --- | --- | --- |
| Meaning | 9 | 1 / 1 | 4 / 6 |
| Typo | 3 | 3 / 3 | 3 / 3 |
| Exact | 1 | 1 / 1 | 1 / 1 |
| Multilingual | 2 | 0 / 0 | 0 / 0 |
| Unrelated | 3 | All empty | All empty |

Semantic retrieval improves paraphrases, but misses Bandcamp Daily for music-journalism wording and Hugging Face papers for a general machine-learning query. Lex Fridman appears fourth for a scientist-interview query. Spanish queries demonstrate a remaining cross-language weakness. Replacing MiniLM with a larger English encoder would not, by itself, resolve that language gap or noisy item descriptions.

## Description quality

All nine generated descriptions consist only of eligible source sentences. **That is grounding, not a guarantee of relevance or correctness.** Several outputs mix source-level information with individual records or promotions: Magnus includes tabletop-game merchandise, Lightspeed combines story fragments, Hugging Face mixes descriptions of individual papers, and Lex Fridman includes a support appeal. Unsong's description is largely navigation and literary quotation.

The separate authored cases confirm that signup copy, repeated sentences, short fragments and instruction-like text are handled, while a request to buy supporting merchandise can still enter a description. The stronger next step is to retain overview/meta-description provenance separately from entry excerpts and give it priority. A larger sentence ranker cannot reliably recover that distinction after all text has been flattened into one summary.

## Suggestions

The suggestions interface remains hidden. Its runtime is bounded TF-IDF plus favorite/read affinity and diversity, not the neural encoder. Authored account-local fixtures confirm that dismissed, already-owned and muted-source-only candidates are excluded. However, a relevant paraphrase with no shared words ranks below an unrelated candidate from the same origin. This is evidence of the lexical limitation, not a user recommendation-quality study. No further recommendation crawling was introduced.

## CPU and memory

Windows runtime replay with existing local model files, no network or model downloads:

- Learned media classification: about **1.32 ms per profile**.
- MiniLM cold load: **264 ms**.
- Descriptions plus initial indexing for 56 profiles: **3.81 seconds**; this is initial/rebuilt profile work, not each warmed query.
- Warm hybrid query median across 56 profiles: **22.88 ms**, p95 **31.75 ms**.
- Median stage costs across 56 profiles: vocabulary/query correction **8.95 ms**, encoder **1.62 ms**, ranking **12.17 ms**. Independently computed stage medians need not sum exactly to the overall median.
- Process RSS: about **34 MiB** after media loading, **118 MiB** after the encoder, and **133 MiB** after the audit; peak working set **155 MiB**. These are isolated process measurements, not the whole deployed app or browser service.
- Hidden suggestions: **283 ms** for 1,000 authored candidates, returning the bounded 40 results.

A separate **500-item warm-load benchmark** copies the 56 public profiles into distinct synthetic records, including independent metadata and vector buffers. It is a load test, not 500 independently observed sources or an accuracy benchmark. Eight queries are repeated three times. Median query time is **178.17 ms**, p95 **260.94 ms**: query correction **71.95 ms**, encoder **1.83 ms**, and ranking **104.33 ms**. RSS changes from **121.5 MiB** before fixture creation to **128.5 MiB** after the copies and **129.2 MiB** after querying. Packed vectors account for 768,000 bytes. This excludes initial 500-item description generation/indexing, SQLite, HTTP and browser work.

The encoder is a small part of warmed query latency even at 500 items. Reusing normalized per-item text and the fuzzy vocabulary across unchanged library metadata has more immediate speed potential than reducing encoder computation. The browser report also remeasures the already-shipped implementation at 500 items against its historical baseline; it is not a new speed improvement delivered by this release.

## Linux deployment-limit replay

The candidate runtime was replayed offline in a Linux container limited to **two CPUs and 1 GiB RAM**, using the same frozen corpus and local model files. Classification and retrieval outcomes matched the Windows evaluation; there were no model or threshold changes.

| Measurement | Linux result |
| --- | --- |
| Media classification | 1.19 ms per public profile |
| Encoder cold load | 177 ms |
| Initial descriptions and indexing, 56 profiles | 3.43 s |
| Warm hybrid query, 56 profiles | 24.62 ms median / 33.76 ms p95 |
| Warm query, 500 synthetic records | **198.74 ms median / 282.84 ms p95** |
| 500-record query preparation | 81.61 ms median |
| 500-record encoder inference | 1.93 ms median |
| 500-record ranking | 115.12 ms median |
| Hidden suggestions, 1,000 authored candidates | 286.82 ms median |
| Isolated process peak RSS | **180.92 MiB** |

For the 500-record workload, RSS was 140.22 MiB before constructing the copied metadata/vector records, 146.45 MiB after construction, and 147.40 MiB after querying. Its records are synthetic replicas of the 56 public profiles, not 500 independent website observations. The query measurement excludes initial 500-item description generation/indexing, database aggregation, HTTP, browser work and other app processes. The 180.92 MiB peak therefore establishes the isolated audit's fit under the container limit; it is not the production application's full memory footprint.

**This release does not optimize whole-query ranking.** The measurement identifies repeated lexical preparation and ranking as the main remaining warm-search costs; encoder inference is roughly one percent of the 500-record median. Bounded metadata-revision caches and precomputed lexical documents are follow-up experiments, not improvements already delivered. Linux raw results: [non-extraction-audit-linux.json](non-extraction-audit-linux.json).

## Recommended next experiments

1. Preserve and label source overview versus entry, navigation, and promotional context; acquire permitted real music and multilingual profiles before retraining.
2. Add a conservative safeguard against generic-website downgrades when a specific medium has explicit evidence; distinguish external distribution from aggregation using general record context. Evaluate any change on a fresh final set after this diagnostic set becomes development data.
3. Cache bounded lexical preparation by metadata revision, and compare field-aware semantic representations for title/media type versus overview. Keep the existing lexical fallback and negative-query checks.
4. Test a compact multilingual encoder or distillation candidate against independent multilingual queries, under the same one-gigabyte deployment limit. The present sample is too small to select a replacement.
5. Before making suggestions visible, compare its lexical ranking with reuse of existing local semantic vectors and test independently labeled relevance. Preserve account isolation and the observed-source-only rule.

## Reproduction and checks

```sh
python backend/ml/collect_non_extraction_audit.py          # saved captures only
python backend/ml/collect_non_extraction_audit.py --fetch  # explicit bounded collection
python backend/ml/evaluate_non_extraction.py
```

For an isolated deployed-runtime replay, copy the evaluator and these four corpus files: `media-profiles.json`, `non-extraction-audit-sources.json`, `non-extraction-audit-profiles.json`, and `non-extraction-audit-queries.json`.

```sh
/app/backend/.venv/bin/python /tmp/audit/evaluate_non_extraction.py \
  --app-root /app/backend --corpus-root /tmp/audit/datasets \
  --output /tmp/audit/report.json
```

The evaluator includes the 500-item warm-load section by default; `--warm-library-size` can change its fixture count. The script uses only runtime dependencies; Linux memory measurement falls back to `/proc` when psutil is absent. Four focused tests verify source separation, bounded fixture shape, exclusion of unavailable query targets, verbatim sentence grounding, and preserving rejected collection results without a network request.

Raw results: [non-extraction-audit.json](non-extraction-audit.json). Browser measurements: [browser-search.md](browser-search.md).
