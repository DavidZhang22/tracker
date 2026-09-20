# Extraction model audit — 2026-09-20

The deployed cascade is conservative on these new listings, but its aggregate score conceals large source-specific misses. The legacy network and standalone deep network should not replace the context classifier. No model weights or runtime extraction rules were changed for this audit.

## Data and collection

Seven additional public listing pages were captured once: [MIT Missing Semester](https://missing.csail.mit.edu/2020/), [CSES](https://cses.fi/problemset/), [Syntax](https://syntax.fm/), [Lex Fridman](https://lexfridman.com/podcast/), [Standard Ebooks](https://standardebooks.org/ebooks/), [Zig news](https://ziglang.org/news/), and [UNSONG](https://unsongbook.com/). An eighth attempted listing, Inria news, rejected the request and was not retried or bypassed. Existing SafeFetcher URL validation, response limits, request budgets and host pacing applied. Robots were checked first; a missing robots file was allowed, other robots errors were not. The round used 17 HTTP requests including robots/redirects and received 1,167,613 bytes. No content-detail pages, pagination, browser rendering or authenticated data were requested.

The source hosts were absent from the existing extraction source manifests (69 hosts) and source/source_url/page_url fields in existing extraction JSONL datasets (381 hosts). This is source-disjoint from those recorded extraction datasets, not proof of absence from every upstream corpus. Standard Ebooks already occurs in the separate media-classification corpus; it is new only to this extraction audit.

Labels were defined from reviewed listing scopes before model scores were inspected. They are assistant-authored, source-scoped weak labels, not independent human gold. The frozen manifest records selectors, intended scope and SHA-256 of each capture. Duplicate renderings count once per source URL. Lex's valid episode/transcript alternatives are excluded from binary classification, not mislabeled as irrelevant. This yields 2,202 anchor rows, 1,326 unique scored URLs, 1,058 relevant URLs and 268 negatives. All new rows remain test data; nothing was trained on them.

Seven small captured DOM excerpts are retained under `tests/fixtures/extraction-audit/`, with provenance. These serialized excerpts omit other page regions and are diagnostic fixtures, not substitutes for the complete-page benchmark. Frozen numeric features/tokens reproduce classifier and candidate-region metrics without refetching. Full captured HTML remains under the ignored `data/extraction-audit/`; rebuilding those exact features or full-page timings requires those captures or an archived copy. Running the collector against a later live page creates a new capture, not the same test set.

A separate **authored** job-table fixture covers three column orders, image/ARIA labels and company links: six relevant application URLs, eight negatives. It verifies a branch not exercised by these seven real sources; it is not evidence of fresh real-world job-board accuracy. The new corpus is predominantly English, has no negative-only source and does not establish performance on JavaScript pagination, authenticated content or multilingual listings.

## Active model roles

The deployed `TRACKER_LINK_MODEL=cascade` setting was checked read-only. Actual loaded artifacts, rather than historical docstrings, determine these roles:

| Artifact / ID | Structure | Runtime role |
| --- | --- | --- |
| `link-model.json`, `mlp-8-b223bb65103d` | 47 numeric inputs, 8-unit hidden layer, one output | Legacy fallback; acceptance 0.85. |
| `link-context-model.json`, `context-tables-gated-5cd445174fb3` | 84 numeric inputs; 120-tree table head, 76-input/120-tree fallback (`context-trees-numeric-2a106aa097f5`); empty vocabulary | Main contextual classifier; job_table routes the head; acceptance 0.25. |
| `link-cascade-model.json`, `cascade-text-structure-v1-067e036ceaaa` | Same light classifier plus deep `cascade-research-numeric64x32-edbe8e927ac4`, 84→64→32→1 numeric network | Rescue policy: only light probabilities between 0.20 and 0.25 reach the deep rescue decision (configured gate 0.20–0.30). Deep acceptance 0.90. It does not veto confident light false positives. |
| `record-context-model.json`, `record-mlp32-e4714e194795` | 27 inputs, 32 hidden units, one output | Chooses a DOM region around each link; distinct from deciding whether the link is relevant. |

Context/tree/deep batch inference uses the optional compiled C kernel; legacy and record networks use the existing scalar path with C-backed arithmetic. The separate semantic/media models are evaluated in `non-extraction-audit.md`.

## Classifier results

| Model | TP | FP | FN | Precision | Recall | Micro F1 | Source-macro F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Legacy | 107 | 0 | 951 | 1.000 | 0.1011 | 0.1837 | 0.2975 |
| Context | 944 | 0 | 114 | 1.000 | 0.8922 | 0.9431 | 0.6946 |
| Deep alone, diagnostic | 539 | 0 | 519 | 1.000 | 0.5095 | 0.6750 | 0.3991 |
| Deployed cascade | 944 | 0 | 114 | 1.000 | 0.8922 | 0.9431 | 0.6946 |

Observed zero false positives on 268 negatives does not establish universal precision. Lex and CSES contribute 901 of 1,058 positives, so source-macro performance is more informative for versatility. Only four rows entered the rescue gate, and none were rescued; the deep branch did not improve this corpus. The authored job fixture routes 12 table anchors through the job head: context, deep and cascade each identify all six applications with zero false positives; legacy identifies none.

| Public source | Relevant URLs | Context/cascade correct | Full parser correct | Full parser unwanted |
| --- | ---: | ---: | ---: | ---: |
| MIT course | 11 | 9 | 10 | 0 |
| CSES problems | 400 | 397 | 0 | 0 |
| Syntax episodes | 10 | 10 | 10 | 0 |
| Lex episodes | 501 | 501 | 501 | 0 |
| Standard Ebooks | 12 | 0 | 0 | 0 |
| Zig posts | 29 | 26 | 28 | 0 |
| UNSONG chapters/interludes | 95 | 1 | 4 | 0 |

The complete parser also includes legacy fallback and structured extraction. Its 727 total entries comprise 553 primary targets plus 174 valid Lex episode/transcript alternatives. Same-host HTTP targets promoted by the parser to HTTPS are treated as equivalent for this table; without that correction three valid UNSONG chapters would appear to be false positives. No query strings or different-host identities are merged by this evaluation rule. The parser records dates on all 10 Syntax entries and 28 Zig entries; this count alone is not a date-correlation accuracy claim.

Concrete failures:

- **CSES:** `navigation_links()` matches `body.with-sidebar` as navigation and excludes all 410 anchors, including the 400 problem URLs. This is a general region-exclusion bug, independent of model quality. The source also omits optional list-item closing tags, but that is not the cause of the complete exclusion.
- **Standard Ebooks:** author/title/cover catalog markup is confidently missed by the classifiers. Twelve book targets are lost even though they exist in static HTML. More fetching would not help.
- **UNSONG:** a chapter table of contents embedded in paragraphs with line breaks is largely missed. The cascade's narrow rescue gate cannot recover confidently rejected anchors.
- **MIT/Zig:** the parser still misses `/2020/potpourri` and `/news/welcoming-new-team-members`, respectively. Repeated-card expansion recovers some other links rejected by the classifier, so classifier recall and parser recall differ.

## Record context, evaluated independently

For each source, up to 24 records were sampled evenly across the listing, yielding 129 records and 1,481 candidate regions. Candidate labels require the annotated metadata DOM node to be inside the region and exclude regions containing another primary content target. Dates, guest names, author names and CSES submission-count spans were explicitly identified from their own record; an arbitrary enclosing div is not assumed correct. The annotations do not validate semantic authorship or the parser's final publication-date value.

At threshold 0.5, candidate-region precision is 0.6330, recall 0.2438 and F1 0.3520. Selection has a fallback, so these binary candidate scores are not end-user record accuracy:

- 81/129 selected regions contain the designated metadata and no neighboring primary target.
- 17/129 selected regions include another primary record.
- Of 105 records with designated metadata, 74 include its actual DOM node and 96 contain its normalized text somewhere in the returned context. These differ: Lex guest names often already appear in anchor text despite the guest metadata div being omitted.
- Syntax selects the complete anchor as fallback for all ten cards; those anchors contain their own time and no other primary target. Low region confidence is harmless there.

| Source | Sampled | Own metadata, no neighbor | Neighbor contamination | Own metadata text missing |
| --- | ---: | ---: | ---: | ---: |
| CSES | 24 | 23 | 0 | 1 |
| Lex | 24 | 0 | 0 | 2 |
| MIT | 11 | 5 | 6 | 0 |
| Standard Ebooks | 12 | 2 | 8 | 2 |
| Syntax | 10 | 10 | 0 | 0 |
| UNSONG | 24 | 23 | 1 | 0 |
| Zig | 24 | 18 | 2 | 4 |

Examples in the record report: MIT `/2020/course-shell` and `/2020/shell-tools` borrow a region with a second lesson. Standard Ebooks' `gustave-flaubert/sentimental-education/m-walter-dunne` region includes a neighboring book, while `edmund-burke/reflections-on-the-revolution-in-france` falls back to the anchor and omits its author text. Zig's `welcoming-new-team-members` region includes another post, whereas `core-team-spotlight-alexrp` omits its adjacent date. These are context-boundary weaknesses, not proof that the final stored date is wrong.

## Runtime and parity

Local Windows measurements, uninstrumented median of three runs over the frozen 2,202 rows: legacy 36.0 ms, context 47.7 ms, deep-alone 60.5 ms and cascade 53.1 ms. Loading the three classifier objects took 28.9 ms; scoring 1,481 record-region vectors took 33.2 ms. Peak process working set was 59.7 MiB during the model audit. The seven full-parser pages took a sum of per-page medians of 3.038 s, with a 69.7 MiB process peak. These are single-process offline parser measurements, not full deployed app memory or network latency.

The local native kernel was loaded. Comparing the same frozen rows with `TRACKER_NATIVE_MODEL=off` gives maximum absolute probability differences of 1.11e-16 (context), 2.04e-14 (deep) and 1.11e-16 (cascade), with **zero threshold decisions changed**. No quantization, retraining or threshold changes were made.

The Linux deployment-image runs used separate offline containers limited to **1 GiB / 2 CPUs**. Five-run classifier medians were 35.67 ms (legacy), 45.70 ms (context), 54.05 ms (deep alone), and 45.08 ms (cascade). Model loading took 28.62 ms; scoring the 1,481 record candidates took 38.13 ms. The seven-page parser replay, three repetitions per page, totaled 3.100 s as a sum of per-page medians; Lex alone contributed 2.407 s (77.7%). These use monotonic elapsed timing, not CPU-utilization counters, and include no network latency.

Linux model evaluation peaked at **60.570 MiB process RSS / 47.805 MiB cgroup charged memory**; parser replay peaked at **70.250 MiB process RSS / 57.707 MiB cgroup charged memory**. Process RSS is `getrusage(RUSAGE_SELF).ru_maxrss`, including resident shared mappings; cgroup `memory.peak` measures memory charged to that container, with different shared-page ownership and kernel/file-cache accounting. They are different scopes and must not be added or treated as contradictory estimates. Neither run measures the entire live app with its semantic encoder, database and concurrent requests.

All four classifier metric sets and the record candidate metrics match Windows exactly. The Linux native tree/cascade probabilities equal the Python reference on these rows; the deep network differs by at most 2.04e-14, with zero changed decisions. SHA-256 of every complete parser result (all scan fields, pagination and feed discovery) matches across Windows and the deployment image for all seven captures. Reports: `extraction-audit-linux.json` and `extraction-audit-parser-linux.json`.

The prior `pipeline-profile.md` and `deployment-pipeline.json` separately measure fetch pacing, queueing and complete refresh execution; they should not be interpreted as new speed gains from this evaluation-only change.

## Reproduction and next work

From the repository root:

```text
backend/.venv/Scripts/python.exe backend/ml/evaluate_extraction_audit.py evaluate
backend/.venv/Scripts/python.exe backend/ml/evaluate_extraction_audit.py parser --output backend/ml/reports/extraction-audit-parser.json
backend/.venv/Scripts/python.exe backend/ml/evaluate_extraction_audit.py build
```

`evaluate` uses committed frozen vectors plus the authored fixture and requires no network or full captures. `build` and `parser` require the original HTML captures and manifest hashes. The collector only makes requests with explicit `--fetch`; it reuses existing local captures. Linux replay can set `TRACKER_ML_APP_ROOT=/app/backend` while the audit data and scripts live under a separate mounted directory. The four audit-integrity tests verify primary/alternative separation, duplicate grouping, HTTP promotion and independent metadata/neighbor labels.

The evidence favors fixing generic navigation boundaries and collecting independently labeled catalog/paragraph TOC examples before increasing model size. Revisit region selection using explicit sibling boundaries; then measure both neighbor contamination and metadata retention. Calibrate or widen deep rescue only on a separate validation set with hard negatives, because the deep model's standalone recall is worse here. Keep this audit held out if training on related examples later. No candidate is promoted from these test results.
