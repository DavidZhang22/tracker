# Media format classifier review

The deployed change adds a small learned classifier for **what an item contains**: podcast episodes, research papers, comics, articles, and the other existing media types. It does not choose a scraper or change link inventories. Better types also improve the private descriptors used by library search.

## What changed

The previous classifier applied fixed weights to 45 keyword features. Its weakest cases included podcast archives tagged as blogs and research indexes tagged as generic websites. Keywords also confused a medium with articles discussing that medium.

The selected classifier learns weights over 86 bounded features: separate title, description, path and entry evidence; inventory URL segments; chapter/episode/version patterns; acquisition type; and whether a collection links across hosts. No domain names, user libraries, or label names are input features. It samples at most 32 entries and reuses the same normalized evidence for classification and search tags.

The runtime uses 1,044 parameters in NumPy float32 arrays: **4,176 bytes of numeric weights and a 12,314-byte JSON artifact**. Numeric JSON is shape-, size-, label-, threshold-, and finiteness-checked before use. No pickle, remote inference, additional scraping, tokenizer, or ONNX encoder is loaded. Missing or invalid artifacts fall back to the old classifier. Explicit user choices still take precedence; reliable comic/novel/YouTube acquisition evidence is retained.

A probability-score floor and score margin limit changes when evidence is uncertain. These scores are not claimed to be calibrated probabilities. Explicit news/blog inventories require actual inventory evidence before they can be reclassified as the medium being discussed. A collection needs observed external links before it can be downgraded to a generic website.

## Data and evaluation

The original corpus contained 47 public profiles from 46 host groups, with 23 blogs, only 1–3 examples for several other formats, and no jobs or music. This review made **25 bounded index URL requests**, obtained 23 pages, and left the two rejected sources alone. One music URL redirected to an article and was explicitly excluded. No detail pages were fetched, no private library data was used, and collection is opt-in.

The usable public corpus now has 69 profiles: 52 development/training profiles, four validation profiles, eight initial held-out source profiles, and five final source profiles. Training also uses 210 clearly marked authored examples, including topic-versus-format counterexamples. Authored variants stay in training; they are never counted as independent public evaluation observations. Sources sharing a domain are kept together in development cross-validation. The labels describe the intended inventory; some parser outputs contain missing or irrelevant entries, which are retained to expose acquisition weaknesses.

| Evaluation | Previous rules | Selected model with safeguards |
| --- | ---: | ---: |
| Development, five domain-grouped folds | 36/52 (69.2%) | 45/52 (86.5%) |
| Validation | 3/4 | 3/4 |
| Initial held-out source set | 3/8 | 4/8 |
| Fresh final source set | 2/5 | 4/5 |

**These are small, mostly English samples, not a general accuracy estimate.** Development folds informed feature and threshold selection. The initial holdout metadata was inspected before feature work, so it is source-disjoint but not a blind benchmark. Final sources were selected and collected after freezing the candidate; no tuning followed their results. The selected change introduced no newly wrong predictions in these comparisons.

Verified corrections include Python Bytes and Talk Python podcast archives, W3C research/standards, museum exhibitions, the Questionable Content comic archive, Darknet Diaries episodes, and NeurIPS papers. The final two corrections came from the fresh source set. Music catalogs remain a clear weakness: “releases” alone cannot reliably separate records from software. No successful real music inventory entered training in this iteration; authored examples do not replace that missing coverage. Sparse descriptions and missing listing context also leave Wandering Inn, Twenty Thousand Hertz, and arXiv unresolved.

Three regularized linear candidates and neural candidates with 32 or 64 hidden units were compared. The larger neural models did not improve the rare formats reliably; a 64-unit model with stronger regularization fell back toward common classes. Their weights are not deployed. The selected linear model gives better measured behavior with less computation. Future expansion should prioritize independent music/podcast/novel inventories, multilingual pages, and extraction of trustworthy format context before increasing capacity again.

Some legacy labels are inherently debatable: a software project's news feed may reasonably be a blog or software. Historical labels were retained rather than changed to improve scores. Such ambiguity and the absence of inter-annotator review limit interpretation.

## Cost and verification

On the local public-profile replay, median classification time was about **2.0–2.4 ms per item**, versus 1.3 ms for the prior rules; full tagging was about 3.2–4.3 ms. That small additional cost buys better classification and does not add network requests. These are warm CPU measurements, not end-to-end refresh latency or whole-service memory measurements.

The float32 implementation differs from exported float64 weights by less than 0.000001 in probability score, with **zero decision changes** across the runtime replay. Focused tests cover public failure corrections, topic confusion, manual settings and progress, source adapter preservation, corrupt artifacts, bounded input, read-only arrays, and semantic-search compatibility.

Reproduce the full development comparison with the ML dependencies installed:

```sh
python backend/ml/train_media.py --compare
```

`--export` is an explicit option and trains only on the development set and authored curriculum. Training never includes validation or either held-out source set.

For an isolated Linux deployment benchmark using only normal runtime dependencies:

```sh
python backend/ml/benchmark_media.py --app-root backend --corpus-root backend/ml/datasets --output /tmp/media-runtime.json
```

The corpus directory needs `media-profiles.json`, `media-public-profiles.json`, and `media-final-profiles.json`. `--app-root` can be `/app/backend` inside the app container. The benchmark performs no network requests or training, checks numeric parity and encoder isolation, and reports process peak RSS on Linux. Its training replay count is explicitly separate from held-out evaluation.

Raw listing captures stay in ignored `backend/data/media-strength`; committed fixtures retain only bounded titles, descriptions, sampled link/title pairs, source URLs, labels, partitions, and content hashes. Data collection and split declarations are in `collect_media_sources.py`; the reproducible authored curriculum is in `build_media_curriculum.py`.

The candidate comparison follows the practical distinction in scikit-learn's [linear model documentation](https://scikit-learn.org/stable/modules/linear_model.html) and [neural network documentation](https://scikit-learn.org/1.8/modules/neural_networks_supervised.html): capacity and regularization are tested against the available data rather than assumed to improve quality.


Production-image replay under two CPUs and a 1 GiB container limit confirmed **1.14 ms/item** learned classification versus **0.62 ms/item** for the old rules, with **36.6 MiB process peak RSS** for the isolated benchmark. Full metadata took 1.84 ms/item. There were no float32 decision changes and no encoder import. See [media-runtime-linux.json](media-runtime-linux.json); these are model-process measurements, not the total application's memory use.
