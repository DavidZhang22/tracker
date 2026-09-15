# Link classifier: design, data and results

This is a small supervised **relevance assistant**, integrated into the tracker.
It helps decide whether an HTML anchor belongs to the source's content collection.
It does not summarize articles, predict your interests, infer publication dates,
or replace the feed and site adapters. The bundled model runs entirely on CPU.

## Architecture and resource budget

The selected model is a multilayer perceptron: **47 inputs → 8 ReLU units → 1
sigmoid output**, with **393 trained parameters**. Its numeric JSON artifact is
**8,986 bytes**. A logistic classifier and a 16-unit MLP were also trained; the
8-unit network achieved the highest macro F1 on the validation websites under
the selection rule in `train.py`.

Features describe URL relationships, path depth and date/index shapes, heading
and record markup, navigation/body/metadata context, link density, repeated URL
shapes, label length, and coarse text cues. Hostnames and individual title words
are not learned vocabulary. This makes the model cheap and reduces direct site
memorization, although layout and language biases remain.

Inference is plain Python. Production installs neither NumPy nor scikit-learn.
Weights load once and are validated for schema, finite values, dimensions and
size. There is no pickle loading, GPU, model server or remote inference call.

Measured on the Azure VM in a temporary **128 MiB / 1 CPU / no-network** container:

| Measurement | Result |
| --- | ---: |
| Entire benchmark process peak RSS, including HTML/BeautifulSoup | 43.45 MiB |
| Python allocation peak while loading the model | 31.43 KiB |
| Scoring 1,000 already-extracted feature vectors, median of five runs | 44.81 ms |
| Extracting features from 1,000 authored article cards | 164.87 ms |

These are separate measurements: model loading is not the memory cost of parsing
a page. The 128 MiB check covers the benchmark, not the whole website under load.
The VM reported 14,905 MiB available before this work. Retraining was also run
successfully on the VM in a separate **512 MiB / 1 CPU** container, with the input
project mounted read-only and outputs written separately. All three candidates
trained in 0.72 seconds after imports; dependency installation takes longer.
The container's memory high-water mark reached its 512 MiB limit during the
installation/training job, including file cache; this is not a measurement of
model-only training RAM. The live app remained available and used about 70 MiB
when idle after deployment. Training uses one BLAS thread.

## How it participates in extraction

1. Fetch an index using the existing cache, request budget, pacing, response-size
   limit and public-address checks.
2. Extract bounded features from at most **4,000 HTML anchors per page**. Anchors
   beyond that limit continue through the normal rules. The classifier follows
   no links and issues no network requests.
3. Scores of at least **0.85** can rescue candidates excluded by a loose path
   cluster, including an external article link. Existing URL, source-kind,
   pagination, navigation and user path restrictions still apply.
4. Scores below **0.03** reject only weak generic candidates. Strong content
   evidence is protected. In the middle, use the existing rules.
5. Extract dates and sequence numbers from actual evidence, then deduplicate and
   order using the existing code. The model never generates a date or number.

Scores are **uncalibrated model scores**, not guaranteed probabilities. The
conservative thresholds and protected rules are deliberate: the classifier alone
misses too much content to replace the extractor. Feed/JSON/API results, explicit
CSS selectors, and specialized novel/comic/YouTube paths are not filtered by it.
API scan methods identify `link classifier` and assisted entries use
`page + classifier`; no additional UI controls are needed.

An absent/corrupt model falls back to rules. `TRACKER_LINK_MODEL=off` disables it.
Cache identity includes model version and enabled state so switching models does
not reuse another model's parsed scan. Existing library IDs and read/favorite/
ignore state are unchanged; refreshing merges discoveries through the same store.

## Dataset and annotation

The frozen files contain **8,507 anchor-context rows** from **21 public websites**
plus authored training examples. These are not 8,507 independent websites or
unique links. Duplicate URLs can appear in different contexts, such as a title
and its date; evaluation counts each source/URL once.

| Partition | Rows | Source groups |
| --- | ---: | --- |
| Training | 2,443 | 12 public websites plus 612 authored boundary rows |
| Validation | 1,100 | Hacker News, Django weblog, Python Bytes |
| Initial test | 4,275 | xkcd, Go blog, Planet Python |
| Fresh final holdout | 689 | Simon Willison, LWN, Schneier on Security |

Training includes Asura, Royal Road, GitHub releases, Codeforces, Python/Rust/
Cloudflare blogs, Paul Graham essays, Hackaday, Lobsters, Changelog and WordPress.
It uses at most 128 positives and 128 negatives per training website, then weights
websites equally. This prevents a large comic archive from controlling training.
The authored archive-vs-article and dateless-card cases form one additional group,
are explicitly labelled `synthetic`, and never appear in evaluation.

Labels come from **source-specific rules reviewed against captured markup** in
`datasets/sources.json`, independent of the tracker's current relevance rules. A positive
is an entry in that source's collection; navigation, month/year indexes, authors,
tags, moving release aliases, and in-body citations are negatives. Some sources
have an intentionally narrow native-article definition; see each annotation.
Labels are weak supervision, not independent double annotation or a gold standard.
Representative positives, negatives and boundary cases were reviewed, not every
row individually. Publication dates are not classification labels.

`datasets/dataset.jsonl` contains frozen features, public URL/title metadata, labels and
split identifiers. `datasets/dataset-card.json` records capture hashes and the labeling
rules. `datasets/holdout-dataset.jsonl` is kept separate so it cannot accidentally enter
training. Raw HTML stays in ignored local folders; article bodies, private
libraries, account records and credentials are not part of training. Public
metadata remains attributable to its source; this dataset grants no rights to
republish the publishers' full pages.

Six public snapshots were reused and 15 additional indexes were fetched once.
No article bodies or media were followed. The opt-in collector allows three
requests per source including redirects, caches requests and skips existing
captures. There is no scheduled crawler or automatic retraining.

## Evaluation, including the unsuccessful first test

The fixed random seed is 81. Training uses scikit-learn MLP with L-BFGS and L2
regularization (`alpha=1`), plus a logistic baseline. Whole source hosts stay in
one partition. Threshold/model selection uses validation only; feature design
and additional training examples were iterated against that partition.
See the [MLP documentation](https://scikit-learn.org/stable/modules/generated/sklearn.neural_network.MLPClassifier.html)
and [grouped validation guidance](https://scikit-learn.org/stable/modules/cross_validation.html).

The first untouched test showed no combined-parser improvement. It revealed
that existing external-link checks discarded some model-approved articles.
Only the parser integration was then corrected; weights and thresholds stayed
frozen. `reports/initial-test-report.json` preserves that first result. Consequently,
`reports/test-report.json` is now a **development regression result**, not an unbiased
final holdout. Three fresh websites were collected after the integration fix
and evaluated once; no tuning followed their results.

| Final fresh holdout, unique URLs | Rules | Assisted |
| --- | ---: | ---: |
| Relevant links found / labelled relevant links | 71 / 101 | 76 / 101 |
| Unwanted links included | 13 | 13 |
| Precision | 84.52% | 85.39% |
| Recall | 70.30% | 75.25% |
| F1 | 0.7676 | 0.8000 |
| Macro F1 across the three websites | 0.7437 | 0.7584 |

All five additional links came from Simon Willison's site. LWN and Schneier did
not improve. On the development validation snapshot, the assistant found all 535
labelled entries and removed 240 calendar links, but that result was used during
development and is not a general accuracy claim. Correcting `March 2026` being
mistaken for a chapter number was also necessary for the parser integration.

The initial test is dominated by thousands of xkcd links; its 98–99% aggregate
metrics conceal poor Planet Python performance. Per-site results are included in
every report for that reason. The classifier by itself performs poorly on several
unseen layouts; it is shipped as an assistant with rules retained.

Limitations: only three fresh holdout websites and 101 positive URLs; mostly
English technology content; no time-based evaluation, independent annotator or
confidence intervals; incomplete coverage of paywalled/JS-only indexes and mixed
aggregators. This model cannot reveal content that was not fetched. Use feeds,
public APIs or an explicit selector for difficult sites. User favorites/ignores
are preferences, not automatically valid training labels.

## Reproduce, retrain and operate

From `backend`:

```bash
# Train from the checked-in frozen feature dataset, offline after dependency setup.
uv sync --group ml
uv run --group ml python ml/train.py
uv run --group ml python ml/evaluate.py --split validation
uv run --group ml python ml/evaluate.py --split holdout
uv run python -m pytest -q
uv run python ml/benchmark.py
```

`evaluate.py` replays local raw captures, so a fresh checkout must first opt in to
`python ml/collect.py`. New captures will differ from the recorded snapshot; review
labels before rebuilding with `build_dataset.py` (or `--split holdout`). Training
itself needs only frozen `datasets/dataset.jsonl`; exported predictions are checked against
scikit-learn with maximum absolute error below `1e-10`. Retraining must be followed
by reviewing per-source regressions and using new, untouched evaluation sites.
The source collection manifest now includes the extra holdout pages; the primary
dataset builder excludes them by default.

To reproduce the isolated VM training check without changing the deployed model:

```bash
cd /home/azureuser/tracker
mkdir -p /home/azureuser/ml-training-output
sudo docker run --rm --memory=512m --cpus=1 \
  -v "$PWD/backend:/work:ro" \
  -v /home/azureuser/ml-training-output:/output \
  -w /work -e UV_PROJECT_ENVIRONMENT=/tmp/training-venv \
  --entrypoint sh catchup-app \
  -c 'uv run --locked --group ml python ml/train.py --output-dir /output'
```

The Azure run selected the same 393-parameter architecture. Its separately saved
candidate is not silently promoted: small numerical differences between Windows
and Linux training are expected, so the already-tested artifact stays deployed.
Training refuses to overwrite an artifact if no converged candidate reaches the
validation precision requirement. Always review the combined-parser evaluation
before promoting a retrained candidate.

Production uses `uv sync --frozen --no-dev` and copies only the runtime code/model.
Training files are excluded from the Docker build context. To disable assistance,
set `TRACKER_LINK_MODEL=off` in the VM's `deploy/.env`, then:

```bash
cd /home/azureuser/tracker
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --no-build app
```

The previous app image is retained as `catchup-app:before-link-model`. No account
or database schema migration is required. Daily database backups remain enabled.
For future improvements, collect more independently reviewed sites and actual
irrelevant-link labels; do not silently train on reading history or favorites.
