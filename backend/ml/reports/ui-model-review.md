# UI, limits and model audit — 2026-09-20

## Shipped behavior

- Saved views is removed from the interface and API. Existing view records remain in private account exports and are erased by account deletion; this release does not destroy them.
- Link details use labeled rows with wrapping. The disclosure sits below the main link row, so dates and action controls stay aligned. Imported group members keep their own details. Summaries have a readable block instead of sharing the compact date/status strip.
- Each account can store 500 items, including Trash. The limit appears once on Library. The existing 4,999-links-per-item and 100,000-links-per-account limits are unchanged. The account scan allowance now admits one full 500-item library refresh; the global 600/hour allowance, host pacing, concurrent scan limits and eight-second item addition interval remain enforced.
- Authentication/preferences startup uses a responsive app-shell placeholder instead of a narrow account-form panel. Library refresh keeps current rows visible and updates them as results arrive. No account data or session status is cached in persistent browser storage to avoid the loading state.
- Optional automatic descriptions and link summaries are omitted when already-collected text raises a configured risk flag. Original imported fields, titles, URLs, reading state and user-written descriptions remain intact.

## Description screening

`content_safety.py` checks bounded title, URL and source/excerpt text. It normalizes HTML entities, Unicode compatibility forms, zero-width controls and URL encoding. A cheap native substring prefilter avoids regular expressions on ordinary text. Matched categories cover explicit/exploitative sexual material, graphic violence inventories, serious-harm instructions and promotion of illicit services. Narrow protective/research exceptions reduce false flags. Existing stored automatic descriptions are checked at the response boundary as well as during new description selection.

The filter is deliberately limited: English patterns can miss unfamiliar wording, obfuscation and other languages, and can over-suppress benign text. It cannot decide legality, inspect unseen page bodies, identify malware, or guarantee that a source is safe. It does not fetch content pages, contact a moderation service or block saved links. Minimal authored phrase tests use category labels rather than collecting harmful material. Seventy historical public profiles produced no flags; that small benign replay is not a general false-positive estimate.

Unchanged safe `profile-v1` descriptions and vectors migrate to the screened profile signature without embedding them again. The signature also includes the source URL. Flagged profiles cannot use that shortcut. Account-local raw data remains exportable; no new global plaintext moderation cache is introduced.

The scope follows the distinction between [output validation](https://genai.owasp.org/llmrisk/llm052025-improper-output-handling/) and source trust: HTML remains rendered as text, and source text is not executable instructions. [Google Safe Browsing](https://developers.google.com/safe-browsing/reference/rest) addresses unsafe URLs rather than content legality and has usage constraints; no reputation API or new external data processor is added here.

## Additional model evaluation

Weights and thresholds stayed frozen. The new data is diagnostic held-out evidence, not a retraining result. Seven new extraction hosts and six further public profile hosts were captured with bounded requests; unavailable/challenged sources were not bypassed. Three extraction captures were reused for profile evaluation. No private libraries were used, and evaluations after collection run offline.

| Component | Additional evidence and finding |
| --- | --- |
| Link context classifier and deployed cascade | 2,202 anchors; 1,058 primary target URLs. Both retrieved 944 targets with zero observed false positives: 89.2% recall, 94.3% micro F1, but only 69.5% source-macro F1. Large sources hide weak smaller sources. |
| Legacy neural fallback and deep neural rescue | Legacy recall 10.1%; deep used alone 50.9%. These are diagnostic standalone runs, not the deployed cascade's accuracy. The actual rescue gate ran on four rows and rescued none. |
| End-to-end HTML parser | Retrieved 553/1,058 primary targets plus 174 valid alternative episode/transcript URLs. A broad `with-sidebar` body-class navigation exclusion removes all CSES problems; ebook cards and UNSONG's mixed table of contents also expose generalization gaps. |
| Record-context neural model | 81/129 selected regions meet the labeled boundary rule; 17 include a neighboring primary record. Own metadata appears in text for 96/105 metadata-bearing records, but its corresponding DOM region is contained in only 74/105. These do not measure semantic date/author correctness. |
| Media classifier | 6/9 new profiles correct, equal to prior rules overall. It fixes a research collection but misclassifies a distributed podcast as a generic website. |
| Semantic search | Meaning-query top-one matches improve from text-only 1/9 to hybrid 4/9; top-three 6/9. Three typo queries pass; two multilingual queries fail. |
| Extractive descriptions | All nine outputs are source-grounded, but several include promotion or individual-entry fragments. Grounding alone does not establish a good collection description. |
| Hidden suggestions | Authored fixtures preserve exclusion and account-local behavior, but TF-IDF misses a paraphrase without shared words. Suggestions remains hidden. |

Details, source manifests, limitations and reproduction commands: [extraction audit](extraction-audit.md), [non-extraction audit](non-extraction-audit.md).

## Server performance

Each replay used a fresh network-disabled candidate container capped at **1 GiB and two CPUs**, with public fixtures and the existing native kernel/quantized encoder. These are isolated replay measurements, not total production memory or network refresh latency.

| Workload | Measured result |
| --- | --- |
| Native deployed cascade, 2,202 prepared anchor rows | 45.1 ms median; no changed decisions against Python; maximum deep probability difference about 2.1e-14 |
| Full parser on seven captured pages | Sum of per-page medians 3.10 s; 2.41 s is the large Lex Fridman listing |
| Media classification | 1.19 ms/profile median |
| MiniLM cold load | 177 ms |
| Initial descriptions + index, 56 profiles | 3.43 s |
| Warm search, 500 copied public-profile records | 199 ms median / 283 ms p95; preparation 82 ms, encoder 1.9 ms, ranking 115 ms |
| Non-extraction audit process peak | 181 MiB; excludes the web app and separate browser worker |
| New screening, 500 item records | 44.2 ms median |
| New screening, 4,999 job summaries | 52.4 ms median |

The 500-item fixture uses distinct metadata/vector buffers copied from 56 public profiles; it is a load test, not 500 independently labeled sources. It excludes initial generation of 500 descriptions, database queries, HTTP and rendering. The browser worker was remeasured at 500 items; its documented before/after comparison is for the previous optimization, not a new speedup in this release.

Raw reports: [extraction runtime](extraction-audit-linux.json), [parser runtime](extraction-audit-parser-linux.json), [search/media runtime](non-extraction-audit-linux.json), [screening](content-safety-linux.json).

## Verification and remaining work

The complete backend suite passed 1,013 tests before the final boundary test and audit tests were added. Final focused runs passed 48 safety/semantic tests and eight audit integrity tests. Frontend tests passed 185/185, tooling 5/5; Ruff, ESLint and the production build passed. An offline candidate smoke test verified retired routes, the configured 500-item cap, suppression, manual overrides, imported fields, read progression, account export/deletion, disabled recovery delivery and public page serving.

Live browser visual inspection could not run because the computer-use runtime failed at Windows sandbox ACL setup. DOM/component and responsive CSS checks passed, but this report does not claim screenshot validation.

The strongest next work is to narrow generic layout exclusions, improve record/overview provenance, collect permitted multilingual/music examples, and cache lexical query preparation by metadata revision. Retraining against this audit would make it development data, requiring another independent final set. A larger model alone will not repair the demonstrated preprocessing failures.
