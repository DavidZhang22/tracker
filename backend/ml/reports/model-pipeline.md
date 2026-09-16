# Neural cascade and context pipeline

September 16, 2026. Implementation of the first measured stages of the [model roadmap](../docs/MODEL_ROADMAP.md). The remaining research targets below are not represented as completed work.

## Release decision

The release combines the existing routed tree classifier with an **84 → 64 → 32 → 1 neural network** containing **7,553 parameters**. Trees classify every candidate. The neural model only examines borderline tree rejections and can rescue a link; it cannot veto an accepted tree decision. This is a conservative first use of the new network, not a replacement for collection detection or deterministic API/feed/document decoding.

The bundled model is `cascade-text-structure-v1-067e036ceaaa`, in `app/tracker/link-cascade-model.json` (373,059 bytes). Its light threshold is 0.25, gate is 0.2–0.3, and calibrated neural threshold is 0.9. Rescue policy restricts the effective gate to scores strictly between 0.2 and 0.25. On the threshold-selection subset, **3.13%** of candidates reached the neural stage. This is a measured development-set rate, not a promise for every website. Sigmoid calibration was fitted on source groups separate from threshold selection; its probabilities are not guaranteed to be calibrated on unseen domains.

`TRACKER_LINK_MODEL=cascade` enables it. `on` retains the previous classifier with the new execution optimizations; `off` retains the original discovery rules and adapters. Missing or invalid cascade files fall back to the previous classifier. Weights, preprocessing, thresholds, policy and gates have a shared content-derived model identity. The discovery cache version also changed.

## Speed and memory

Date evidence, heading counts, sibling boundaries, record snippets and language evidence now share page-local caches. Cached date dictionaries are copied before callers attach provenance. Heading and sibling traversal retains the previous record boundaries, including missing-date records. Snippet assembly uses a running length instead of repeatedly summing all preceding fragments.

A fixed C kernel executes bounded batches of 128 rows. It preserves float32 tree comparisons and float64 thresholds, uses fixed 64-element hidden-layer buffers, and only accepts validated numeric model structures. Both Dockerfiles compile it in a separate stage. Runtime images contain neither the compiler nor NumPy, scikit-learn, PyTorch or an inference service. Python is the fallback. Existing analysis processes remain; free-threaded Python is not required for this release.

On the Windows development machine, scoring the same **18,855 feature rows** took **1.908 s in Python** and **0.700 s with the native kernel**, using medians of three runs: **2.72× faster**. Maximum score difference was **2.22e-16**, with **zero changed acceptance decisions**. This measures batch feature-to-score execution, not network time or complete refresh speed. [Parity measurements](native-model-parity.json).

The following full-parser measurements ran sequentially on the same Azure VM in isolated containers limited to **2 CPUs and 1 GiB**, using frozen HTML, no network and three repetitions per page. Baseline used the previously deployed image; release used the optimized parser and rescue cascade. Timings include parsing, context preparation, model inference and metadata association, but exclude fetching and persistence.

| Page | Baseline median | Release median | Reduction |
| --- | ---: | ---: | ---: |
| NASA index | 0.884 s | 0.574 s | 35.1% |
| PMLR volumes | 0.836 s | 0.698 s | 16.5% |
| Python releases | 1.201 s | 0.960 s | 20.1% |
| ESA gallery | 0.271 s | 0.237 s | 12.5% |
| W3C standards | 9.604 s | 7.530 s | 21.6% |
| EFF updates | 0.295 s | 0.240 s | 18.9% |
| Planetary articles | 0.290 s | 0.267 s | 8.0% |
| freeCodeCamp news | 0.305 s | 0.251 s | 17.5% |

The optimized parser with unchanged classifier weights produced identical full-output hashes on all eight pages and took 7.480 s on W3C. Enabling the rescue cascade took 7.530 s there. These runs isolate the main speed gain in execution/context work, rather than attributing it to the new neural model.

The release container peaked at **354.5 MiB** while replaying the benchmark and processing four synthetic 4,999-entry pages with two spawned workers, each page including 6 MB of script padding. All four produced identical complete results in **19.78 s total**. This is the whole isolated benchmark container, not a bound for the live web application plus database, other requests and the separate browser service. The 1 GiB deployment limit remains enforced.

The stress test also found an existing bug: chapters numbered 1900–2099 were treated as year-archive navigation. Explicit sequence labels now distinguish these from actual year archives. Baseline extracted 4,799 records; release extracted all 4,999. A regression test covers this correction.

Raw evidence: [old image](model-runtime-baseline.json), [optimized parser and experimental models](model-runtime-experiments.json), [release cascade](model-runtime-rescue.json).

## Quality, data and alternatives

Added **17 public index captures and 3,259 feature rows**: 14 pages contributed 2,656 rows to the development expansion; three later pages contributed 603 evaluation rows. The neural training split contains **11,944 rows** including existing data. New sources cover releases, conference talks, science writing, essays, policy updates, tutorials and exhibitions. They were collected with bounded index-only requests and no individual article crawling. Blocked or unavailable sources remain recorded; there is no access-control bypass or private account/upload training data.

Dataset manifests retain public URLs, scope selectors, expected URL sets, splits and capture hashes. Captures themselves are ignored local files. The added domains exclude existing domain/subdomain families, including JPL/NASA. Training uses page-balanced weights, training-only vocabularies, separate calibration and selection source groups, and downweights authored/CleanEval examples. This expansion is useful but is **not** the roadmap's proposed 100–200 independently reviewed gold pages or an exhaustive platform-family audit.

Compared a linear word/structure model, a 32-unit text network, a 64/32-unit word/subword network, two numeric networks, tree-to-neural distillation, and both replacement and rescue cascades. Text preprocessing includes bounded, field-specific word and character features without hostname or query-value identity. The selected production network uses numeric structural features: the current text training data did not justify promoting the text alternatives.

Feature-level regression holdout results, using each candidate's selected threshold:

| Candidate | Precision | Recall | Decision |
| --- | ---: | ---: | --- |
| Existing routed trees | 90.52% | 99.78% | Light stage |
| Linear word/structure | 53.24% | 98.94% | Withheld |
| Text network, 32 hidden units | 60.98% | 94.72% | Withheld |
| Text/subword network, 64/32 units | 54.42% | 97.50% | Withheld |
| Numeric network, 64/32 units alone | 93.19% | 66.94% | Withheld as standalone |
| Distilled numeric network | 45.78% | 74.11% | Withheld; optimizer did not converge |
| Trees + neural rescue | 90.54% | 99.94% | Selected combination |

The rescue model recovered three additional labeled positives with the same 188 false positives on this feature holdout. Selection-set precision/recall matched the old model, rather than improving. These are **scope-based weak labels and already-observed regression holdouts**, not independent universal accuracy estimates. Architecture decisions were informed by errors on these pages. Training reports retain per-source results, optimizer convergence, dataset hashes and timing: [text comparisons](../experiments/cascade/report.json), [structural comparisons](../experiments/structural-cascade/report.json), [rescue selection](../experiments/rescue-cascade/report.json).

Complete parser replay matters more than individual anchor decisions:

| Collection | Previous expected links found | Release expected links found | Previous → release outside-scope links |
| --- | ---: | ---: | ---: |
| NASA | 19 / 25 | 20 / 25 | 24 → 24 |
| PMLR | 206 / 327 | 325 / 327 | 3 → 3 |
| Python | 260 / 260 | 260 / 260 | 0 → 0 |
| ESA | 58 / 58 | 58 / 58 | 0 → 0 |
| W3C | 1,233 / 1,233 | 1,233 / 1,233 | 178 → 178 |
| EFF | 15 / 15 | 15 / 15 | 2 → 2 |
| Planetary | 12 / 20 | 12 / 20 | 2 → 2 |
| freeCodeCamp | 25 / 25 | 25 / 25 | 0 → 0 |

PMLR and NASA are previously observed development pages, so their gains demonstrate repaired coverage, not unseen-domain generalization. PMLR gains are amplified by the existing collection-grouping stage. Six of the eight release output hashes match the old image exactly; only NASA and PMLR change. An additional one-pass replay of the 14 expansion pages found no changes in expected or outside-scope URL counts. Quanta's hash changes only because three entries gain `classifier` provenance. [Expanded replay](model-expanded-replay.json).

The three later pages exposed annotation errors in the initial URL-only scope: EFF also lists press/events, Planetary includes newsletter paths, and freeCodeCamp footer guides are outside the news cards. Their scope was corrected after inspecting baseline outputs, before final rescue replay. The table above uses the corrected 15/20/25 expected sets for both models, whose outputs on these pages are identical. Earlier raw reports retain the original 14/11/64 expectations; those denominators must not be compared as model gains. These pages therefore are not a pristine blind test either.

A replacement cascade could veto tree decisions and lost two valid Planetary newsletter links in full-parser replay, despite good feature metrics. It was not promoted. The larger text network returned 1,466 unwanted W3C URLs versus 178 for the existing classifier. More parameters alone did not solve the task.

## Remaining work

Collection scope and candidate generation are still the largest quality gaps. Planetary misses 8 of 20 intended entries; the expansion replay also exposes poor Debian and Blender coverage. W3C retains 178 outside-scope links. Future work should label complete record boundaries, link roles and metadata associations, then test record/group-aware features against these errors. Rescue alone cannot remove a confidently accepted unwanted link.

Next gates are the larger independently reviewed, multilingual and document evaluation set; balanced candidate budgets across groups; and a shared record representation with role/metadata heads. LightGBM, pooled learned embeddings, larger document teachers and graph/sequence models remain experiments to evaluate, not shipped features. Quantization and pruning were not promoted: the selected network is already small, while data quality and context traversal dominate current tradeoffs. Continue measuring complete-page output and memory before increasing model capacity or relaxing the 4,999-entry cap.

## Reproduction and checks

From `backend`, with the existing `ml` dependency group installed:

```sh
uv run --group ml python ml/train_cascade.py --extra ml/datasets/cascade-dataset.jsonl --output ml/experiments/cascade
uv run --group ml python ml/train_cascade.py --extra ml/datasets/cascade-dataset.jsonl --structural --output ml/experiments/structural-cascade
uv run --group ml python ml/train_cascade.py --extra ml/datasets/cascade-dataset.jsonl --structural --policy rescue --output ml/experiments/rescue-cascade
```

Training exports bounded JSON candidates and never replaces the runtime model automatically. Frozen rows are committed. Page replay additionally needs local HTML matching the source manifests; recollecting changed websites does not reproduce a historical snapshot. `collect_cascade_sources.py --help` describes the explicit, opt-in collector.

Docker builds include the kernel automatically. For a local Linux benchmark:

```sh
gcc -O3 -ffp-contract=off -fPIC -shared app/tracker/native_model.c -o app/tracker/_model_native.so -lm
uv run python ml/benchmark_native_model.py --output ml/reports/native-model-parity.json
uv run python ml/verify_model_pipeline.py --rescue --output ml/reports/model-runtime-rescue.json
uv run python ml/verify_model_pipeline.py --manifest ml/datasets/cascade-sources.json --rescue --repeats 1 --output data/expanded-rescue.json
TRACKER_LINK_MODEL=cascade uv run pytest -q
```

For the memory gate, run the verifier with `--stress` inside a network-disabled container limited to `--memory 1g --cpus 2`, with `TRACKER_LINK_MODEL=cascade`. Mount only public captures and an output directory. `/sys/fs/cgroup/memory.peak` records the aggregate container peak; a local run without cgroups is not an equivalent memory measurement.

**708 backend tests passed** with cascade mode enabled. All 19 pipeline tests also passed with native inference disabled. A separate [release-cascade parity check](native-rescue-parity.json) over 18,855 rows found zero changed decisions and maximum error 2.22e-16; its single timing run is diagnostic only. New coverage includes batch/native parity, Python fallback, malformed features/calibration, uncertain-only routing, rescue monotonicity, missing-model fallback, cache identity, date boundary/mutation isolation, CSS boundary equivalence, tokenization and year-like chapter numbers. Ruff checks passed.
