# General input and source evaluation

September 15, 2026. Decision: ship the shared document pipeline and retain the production website models. No website adapters or additional requests were added to refresh.

## New sources and dataset

Fetched each accessible index once through the existing bounded fetcher, then worked offline. No article, chapter, release, image or specification detail pages were opened. The Library of Congress podcast index returned 403 and remained blocked; the RFC index exceeded the response limit and was left bounded.

| Source | Partition | Intended records |
| --- | --- | --- |
| [nasa-index](https://www.nasa.gov/news/recently-published/) | train | 25 |
| [pmlr-volumes](https://proceedings.mlr.press/) | train | 327 |
| [python-releases](https://www.python.org/downloads/) | validation | 260 |
| [esa-gallery](https://www.esa.int/ESA_Multimedia/Images) | validation | 58 |
| [w3c-standards](https://www.w3.org/TR/) | test | 1233 |

The new dataset contains 7,018 anchor feature rows. Combined with the existing 11,837 rows, training used 9,950 rows, validation used 4,106, and the remaining 4,799 were excluded from fitting. The source manifest records capture hashes, annotation scopes and partitions. Public features, tokens and URLs are stored; raw HTML and personal imports are not committed.

Authored records cover eight layouts across recipes, courses, exhibitions, datasets, lectures and reports. Whole authored families are assigned to one partition; these are structural checks, not independent evidence of real-world quality. Real validation sources are Python releases and ESA images. W3C was excluded from fitting and threshold selection. An initial candidate revealed regressions; after broadening authored layouts, the final comparison below still rejects promotion. W3C was already observed during that exploratory round, so this is a regression holdout, not a pristine blind test.

Labels represent links within each chosen index scope. Ancillary articles, translations, navigation and contributor links are outside that scope; these labels are not a claim that every excluded page is universally irrelevant. The 4,000-anchor model budget covers 3,998 valid W3C anchors. Full-parser counts below evaluate every expected URL in the captured index, including links found by existing structural rules outside that model budget.

## Model comparison

Trained 120-, 180- and 240-tree numeric candidates over the same 84 context features. The 120-tree model won validation selection; larger trees lost recall. Its numeric JSON is 103,592 bytes, and export predictions match training inference within the recorded tolerance. No larger neural network or runtime training dependency was added.

The full-parser candidate replaces the generic fallback branch only, preserving the job-table branch and existing 0.25 acceptance threshold. The training report also records standalone candidate thresholds. Different thresholds are intentionally reported separately; the candidate cannot be treated as a drop-in replacement based on validation F1 alone.

| Index | Production correct / outside scope | Candidate correct / outside scope |
| --- | --- | --- |
| nasa-index | 19 / 24 | 25 / 3 |
| pmlr-volumes | 206 / 3 | 327 / 3 |
| python-releases | 260 / 0 | 260 / 0 |
| esa-gallery | 58 / 0 | 58 / 0 |
| w3c-standards | 1233 / 178 | 1233 / 575 |

The final candidate keeps all Python and ESA records and improves NASA/PMLR coverage. However, W3C links outside the intended scope rise from 178 to 575. Across the frozen test features, precision falls from 90.83% to 75.50%, despite nearly unchanged recall. Production weights stay unchanged.

On the development machine, median inference over 18,855 feature rows took 2.903 seconds for production and 3.220 seconds for the candidate (three repetitions). Full W3C page parsing rose from 16.640 to 19.724 seconds. These are offline wall-clock measurements, not network latency or a service-level guarantee. There is no website refresh performance change in this release.

## Document pipeline and runtime

CSV retains its structured fast path. Excel, slides, Word, PDF, HTML, Markdown and text normalize to bounded records; the existing small record-context network associates nearby titles/dates. Optional content filtering uses the unchanged context classifier. All links is the default to preserve explicit user input, with deduplication, safe URL checks, keywords and preview before saving.

Modern Office XML preserves hyperlink relationships, sheet/slide order and Excel dates; formulas/macros/external parts do not execute. PDF annotations use text coordinates to associate a nearby line when available. This is approximate for complex rotated or multi-column PDFs. Image-only text requires external OCR, and legacy Office formats must be exported first. There is no claim of universal format or layout coverage.

The deployment-image check ran with Linux/Python 3.12, two CPUs, a 1 GiB container cap, no network, a read-only filesystem and a non-root user. Each document child has a 384 MiB address-space cap, 12-second CPU cap and 20-second wall timeout. The upload cap is 4 MB; decompressed Office parts are separately bounded.

| Synthetic import | Median processing time |
| --- | --- |
| 50 text records | 0.633 s |
| 1,000 text records | 1.124 s |
| 1,000 Excel records | 1.090 s |
| 1,000 HTML records with model filtering | 1.523 s |
| 4,999 text records | 3.177 s |
| 4,999 Excel records | 3.065 s |

Each document measurement includes cold worker startup and model loading, three repetitions, with all expected links and dates verified. A separate direct 4,999-row CSV parse took 0.550 seconds. Maximum child RSS across these cases was 76.61 MiB; parent peak RSS was 69.03 MiB. These are per-process peaks, not total app memory under concurrent refreshes, and exclude browser upload and database writes. See [raw runtime results](document-runtime.json).

The final backend suite passes 689 tests; the frontend suite passes 107, with lint and a production build also passing. Automated checks cover all accepted formats, source dates, unsafe URLs, duplicate removal, pasted tables, PDF annotations, line breaks, archive expansion, forbidden XML entities, invalid files, timeout/cancellation recovery, concurrency, account ownership, CSRF, progress-preserving reimport and refresh exclusions. Browser checks cover pasted text, Excel, CSV mapping, reading progress, selection patterns and desktop/mobile layout.

## Reproduce

From backend, with the ML dependency group installed and captures matching the source manifest in data/generalization:

```sh
python ml/build_generalization_dataset.py
python ml/train_context.py --expanded --dataset data/generalization/training.jsonl --output-dir data/generalization/candidate
python ml/evaluate_generalization.py --candidate data/generalization/candidate/link-context-model.json --output data/generalization/evaluation.json
python ml/verify_document_runtime.py
```

The last command is intended for Linux in the bounded production image. See [raw model comparison](generalization-evaluation.json.gz) and [candidate training report](../experiments/generalization/training-report.json.gz).

## Deployment verification

Application release `ef9f069` is deployed with required authentication and public
signup. Its JavaScript and CSS hashes match the verified local build. The proxy
was recreated to attach the current bind-mounted Caddyfile; a 300,000-byte
unauthenticated upload returns 401 and a 4,000,001-byte upload returns 413. A
database backup and the previous application image were retained for rollback.
