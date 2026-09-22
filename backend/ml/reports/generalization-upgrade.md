# Link-model generalization update

Recorded September 21, 2026. This release improves several unfamiliar listing formats, but does not establish reliable extraction from arbitrary websites.

## What changed

The deployed cascade retains its existing classifier and adds a **64 x 32 neural expert** trained on 21,830 weighted examples. The complete numeric JSON bundle is 532,664 bytes, about 160 KB larger than the prior bundle. Native C inference is unchanged; the server needs no training library or external inference service.

The expert can rescue a rejected link when similar entries on the same page provide supporting evidence. Groups use normalized URL structure, DOM context classes, and record roles. At least two distinct URLs must be confidently accepted by the original model. An otherwise unsupported group needs three distinct expert predictions at or above 0.995, and every rescued link in that group must meet that threshold itself. Repeated anchors do not create extra support. Hostnames and query values are not learned features.

The new expert does not demote previously accepted links. Validation showed that words such as “about” inside legitimate article titles made lexical utility flags unreliable grounds for rejection. Existing base-model filtering remains in place.

Two preprocessing corrections accompany the model:

- On crowded pages, spend the 4,000-candidate budget on distinct canonical targets, inspecting at most 20,000 anchors. Prefer content representatives over footer copies. Unscored aliases cannot bypass model decisions through generic fallback.
- In an unambiguous table, use its verified title column to identify the row's primary target and same-target aliases. Other columns cannot inherit title status merely from a CSS class. Ambiguous tables, unsafe targets, spans, nested-table links and job-application routing retain their prior behavior.

Extraction cache versions changed so old learned results are not mistaken for results from this pipeline. Request pacing, shared cooldowns and content-page fetching behavior are unchanged.

## Data and evaluation design

The reviewed inventory now spans **129 website families and 130 listing pages**. Coverage counts evaluated snapshots, not complete site support. Sixteen new families add 6,834 bounded feature rows, including courses, museums, government notices, standards, recipes, mathematics lessons and multilingual journals. Collection used at most one listing per candidate, robots checks and the existing request/byte budgets; no individual content pages were fetched.

The previous 51-site breadth corpus became explicit development data: 32 whole families for training, ten for validation and nine initially reserved for testing. The nine were disclosed after the first failed experiment and are now regression data. Original dataset files remain immutable; the experiment protocol supplies the split override. Historical splits are family-disjoint, exact duplicates are removed, and source/URL weighting prevents large sites and repeated anchors from dominating training.

Tree and neural candidates, numeric features and bounded text vocabularies were compared. The selected expert uses numeric structural features. The first additional eight-site cohort exposed category-link errors and became diagnostic regression data. A second eight-site cohort was frozen independently and evaluated once after all preceding gates passed. It was not used for fitting or selection.

Labels are assistant-reviewed source scopes, not independently adjudicated human gold. These are offline, single-page URL retrieval results. They do not measure complete archives, pagination, JavaScript acquisition, live HTTP reliability or date accuracy.

## Results

The following comparisons isolate the model change using the corrected preprocessing for both models. Cohorts overlap and must not be added together. Recall means intended unique URLs recovered; precision excludes neutral alternatives.

| Cohort | Intended URLs | Correct, old → new | Unwanted, old → new | Recall, old → new | Precision, old → new | Mean site F1, old → new |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| New validation, 10 sites | 478 | 370 → 441 | 17 → 17 | 77.4% → 92.3% | 95.6% → 96.3% | .792 → .861 |
| Historical validation, 13 pages | 1,646 | 1,565 → 1,569 | 27 → 27 | 95.1% → 95.3% | 98.3% → 98.3% | .925 → .925 |
| All breadth development/regression, 51 sites | 4,829 | 3,616 → 3,814 | 2,217 → 2,227 | 74.9% → 79.0% | 62.0% → 63.1% | .752 → .813 |
| Historical regression, 63 pages | 12,679 | 11,813 → 11,846 | 239 → 244 | 93.2% → 93.4% | 98.0% → 98.0% | .833 → .849 |
| First external regression, 8 sites | 226 | 154 → 154 | 0 → 0 | 68.1% → 68.1% | 100% → 100% | .676 → .676 |
| Final unseen holdout, 8 sites | 3,423 | 1,151 → 1,151 | 534 → 534 | 33.6% → 33.6% | 68.3% → 68.3% | .573 → .573 |

The final unseen cohort establishes **non-regression, not an accuracy improvement**. No result from it was used to retune this release.

Concrete improvements include Allrecipes 14/72 → 57/72, BoardGameGeek 72/100 → 100/100, BLS 21/50 → 49/50, NGINX 3/47 → 42/47, and Unicode 0/33 → 23/33. The table correction independently removes all twelve previously returned Yale department/category URLs while retaining the 27 recovered course URLs.

There are tradeoffs: the model adds nine unwanted URLs on IANA, one on BLS and five on Godot relative to the same-preprocessing baseline. Including the candidate-budget correction, IANA still improves from 1,305 unwanted URLs in the original pipeline to 1,277. Large mixed catalogs remain noisy.

The final holdout is particularly difficult: OASIS recovers only 16/1,408 document URLs with 523 unwanted URLs, Love and Lemons 775/1,625, and IRS/OpenStreetMap recover no expected entries. GIMP, Math Is Fun, Ruby and SFMOMA recover all intended URLs, with some extra links on the last three. OASIS counts resource URLs and format/citation aliases, not one record per standard. Future work should address list boundaries, table/layout distinctions, multilingual titles and parser vetoes; increasing neural size alone did not solve these cases.

## Performance and verification

On the Azure VM, original and candidate images ran sequentially in isolated containers with one CPU, a 768 MiB limit, no network and no database volumes. Three repetitions used the same saved HTML:

| Page | Original median | New median |
| --- | ---: | ---: |
| Allrecipes | 0.544 s | 0.635 s |
| IANA | 12.013 s | 7.581 s |
| Yale | 0.249 s | 0.229 s |

Peak worker-process RSS across these three cases fell from 143.1 to 130.3 MiB. This is not total service memory or a live refresh latency benchmark. Allrecipes takes longer while returning 43 more intended entries. IANA is about 37% faster because fewer duplicate anchors reach expensive analysis.

Independent inference verification covered 6,507 validation rows with exact runtime/reference decisions and page partitioning. Linux native inference matched a 991-row reference within 1.12e-16. The final backend suite passed **1,198 tests**; one existing Starlette/httpx deprecation warning remains. New tests cover group support, page isolation, malformed model policies, duplicate floods, table roles, ambiguous/unsafe table targets and immutable dataset provenance.

## Reproduce and audit

[Promotion gates and report hashes](generalization-upgrade-gates.json), [Linux measurements](generalization-upgrade-linux.json), and the [experiment ledger](../experiments/breadth-generalization-v4/experiment-summary.json.gz) retain successful and rejected approaches. Full per-page reports are linked from the gate file. The original baseline and selected production artifact are retained; redundant generated experts and expanded search grids are archived locally under ignored `backend/data/generalization-experiment-archive/` and can be regenerated.

Install the backend's optional `ml` dependencies for training. From the repository root:

```sh
python backend/ml/train_breadth_generalization.py
python backend/ml/train_breadth_generalization.py --refine
python backend/ml/train_group_generalization.py
python backend/ml/train_group_generalization.py --rejection-variants
python backend/ml/experiments/breadth-generalization-v3/verify_runtime.py --candidate backend/app/tracker/link-cascade-model.json --output backend/data/generalization-parity.json
```

The frozen baseline must stay intact. Dataset provenance permits only CRLF/LF differences during replay; changed labels, ordering, splits or other bytes are rejected. Do not overwrite frozen captures or label sets when collecting future snapshots.

Full parser replay additionally requires the original ignored HTML captures:

```sh
python backend/ml/evaluate_breadth_upgrade.py --baseline backend/ml/experiments/breadth-generalization/baseline-cascade.json --candidate backend/app/tracker/link-cascade-model.json --manifest backend/ml/datasets/generalization-final-sources.json --output backend/data/replayed-final.json
```

`--resume` verifies models, captures, manifests, runtime and options before continuing. Recollection creates a different snapshot and needs fresh annotations. The final feature export predates the V5 table correction and remains immutable; parser results use the corrected live feature extractor. This distinction prevents silently rewriting evaluation inputs after results are known.
