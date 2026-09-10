# Record keywords and selection, September 10, 2026

The link classifier below now works with a separate 32-unit neural network that
associates surrounding metadata with each link. See [RECORD_CONTEXT.md](RECORD_CONTEXT.md)
for its dataset, comparisons, limits, MangaDex API support, and reproducible training.

# Table-aware context classifier, September 10, 2026

The current model adds a job-table specialist to the existing media classifier.
It has 84 numeric inputs and two 120-tree branches, about 210 KB of numeric JSON.
Only one branch runs per anchor: semantic job tables use the trained specialist;
other layouts use the frozen media model. No hostnames, passwords or private
library data are training inputs. Production still needs no NumPy, scikit-learn,
GPU or model service. The strict JSON loader permits one fallback level only.

Eight new inputs describe table headers, application versus company/role columns,
image labels, and column position. Image-only Apply buttons now have accessible
labels for classification. A semantic application table also provides authoritative
scope and company/role/location metadata, like existing feed and API adapters.
The scanner recognizes this layout on any host; GitHub receives only a README
loading adapter. No job applications, company profiles, images or archived lists
are fetched. Direct application URLs and alternate Simplify URLs remain separate
links. Closed rows without application URLs produce no entries. Query job IDs
are retained. Relative ages are displayed as source metadata and used for ordering;
month/year ages are approximate for sorting and never become invented calendar dates.

`v3-dataset.jsonl` contains 11,837 rows: 8,429 training, 2,607 validation and 801
prior test rows retained as regression data. It adds 700 sampled public job-table
rows and 1,140 authored examples with reordered columns, image/text buttons and
profile/promotion negatives. Raw README HTML stays in ignored local data;
`tests/fixtures/jobs-table.html` keeps a small public excerpt. Labels are reviewed
source rules and authored contracts, not independent human gold annotation.

Larger 180- and 240-tree replacements reduced media validation accuracy. The
routed model preserves the previous branch and achieves 99.37% precision and
95.13% recall on the combined validation rows, with macro F1 0.9346. These include
synthetic tables and known development pages, so they do not prove universal
extraction. On the full supplied README capture, the classifier identifies all
895 application URLs (448 direct, 447 alternate) with zero unwanted URLs. The
complete scanner independently returns the same 895 URLs in three fetch calls
(HTTP redirects consume additional request budget). Reports are in `table-candidate/`.

Reproduce from the frozen dataset, from `backend`:

```sh
uv sync --group ml
uv run --group ml python ml/train_context.py --expanded --dataset ml/v3-dataset.jsonl --output-dir ml/table-candidate --fallback-model ml/context-candidate/link-context-model.json
uv run pytest -q
uv run python ml/benchmark_context.py
```

The previous media artifact is retained in `context-candidate/`. Training only
writes a candidate; promotion remains an explicit copy after review. Optional
rebuilding requires the ignored public snapshots described below, plus the
captured README at `data/jobs-readme.md`:

```sh
uv run python ml/build_v2_dataset.py --output ml/v3-base-dataset.jsonl
uv run python ml/build_table_dataset.py --base ml/v3-base-dataset.jsonl
```

The original v2 design and evaluation are retained below as historical context.

---

# Context link classifier, version 2

The tracker uses a local classifier to recover content and reject navigation, authors, archive indexes, and references inside excerpts. It runs on CPU without a model server, GPU, or production ML dependencies. Dates and sequence numbers still come from the existing evidence extractor.

## Architecture and preprocessing

The selected model has **120 boosted trees**, maximum depth 4 and minimum 10 training rows per leaf: **3,378 nodes** and **3,379 learned thresholds/leaf scores**, including the initial score. Its validated numeric JSON is **105,310 bytes**, compared with the old 393-parameter neural network. The old network remains available as a fallback.

The 76 bounded numeric inputs include the previous 47 features plus record context: enclosing/preceding headings, heading distance, a card's primary URL, sibling links, duplicate URL/date evidence, paragraph density, author/tag relations, repeated DOM templates, and date-index shapes. Collapsed HTML and nested article headers are supported. Generic labels such as Full Story can use a nearby heading. The model never executes scripts or expands the crawl.

The training pipeline also provides NFKC normalization, percent decoding, camel-case/Unicode tokenization, stop-word removal, number normalization, channel-prefixed unigram/bigram tokens, and binary TF-IDF with L2 normalization. Hostnames and query **values** are excluded. Vocabulary is fitted on training only, pruned to terms occurring at least five times across two source groups, and capped at 512 tokens.

Larger MLPs and word vocabulary generalized worse on validation sites. The shipped artifact therefore **uses numeric context, with no TF-IDF vocabulary**. The reproducible pipeline retains text models for future datasets. Complexity is selected by measured quality rather than parameter count alone.

| Candidate | Validation macro F1 | Threshold |
| --- | ---: | ---: |
| Logistic + text | 0.7863 | 0.50 |
| MLP 32 + text | 0.7760 | 0.25 |
| MLP 64 to 16 + text | 0.7524 | 0.45 |
| Boosted trees, numeric | **0.9201** | **0.25** |
| Boosted trees + text | 0.6561 | 0.35 |

Selection first requires 95% aggregate precision, then maximizes site macro F1. Candidate order breaks ties toward smaller models. Export predictions match scikit-learn within **1.16e-8**. Scores are uncalibrated, not reliable probabilities of correctness. See `context-candidate/training-report.json` for all thresholds and per-site measurements.

## Dataset and provenance

`v2-dataset.jsonl` contains **9,999 anchor-context rows**, not independent pages:

| Origin | Rows | Label treatment |
| --- | ---: | --- |
| 26 public collection pages | 7,377 | Reviewed source-specific DOM/URL rules |
| 160 historical CleanEval pages on distinct hosts | 822 | Conservative auxiliary negatives |
| Authored layout variations | 1,800 | Explicit training-only contracts |

The split is **6,971 train / 2,227 validation / 801 test** rows, grouped by source host. Each training index is capped at 350 rows per class with class balancing. Index/corpus/authored examples receive 75%/10%/15% of training weight; source groups share their origin's allocation. Large archives cannot dominate the fit.

The original 21 sites are development data now. Five additional indexes were fetched once: Julia Evans, Real Python, Project Gutenberg, Smashing Magazine, and Talk Python. PostgreSQL's capture failed and was skipped, without repeated attempts or fabricated examples. No article or media links were followed.

Historical HTML comes from the [Web2Text CleanEval archive](https://github.com/dalab/web2text), pinned to commit `0f9c7b787ff125ce5190784e741c5b453ddf0560`. One bounded archive download supplies the sample; **none of its historical links are fetched**. CleanEval labels article text, not tracker link relevance. Only utility labels absent from cleaned text and short citations inside long cleaned paragraphs enter the auxiliary negative set. These are imperfect weak labels, not gold annotations.

Raw HTML stays in ignored `ml/raw` and `tests/live` folders. Frozen rows contain public link metadata, features, tokens, labels and provenance. No private library, password, reading history or favorite is training data. The repository's MIT license does not grant rights to republish original publishers' pages. Capture hashes, archive hash, annotation rules and exclusions are in `v2-dataset-card.json`, `sources.json` and `v2_sources.json`.

## Runtime modes and safeguards

- `TRACKER_LINK_MODEL=on` (default): scores at least 0.25 recover anchors outside loose path clusters, including external headlines and nested headers. Lower scores can reject candidates with navigation/reference/metadata context, even when a heading or date previously protected them. Other uncertain candidates retain v1 and rule fallback.
- `primary` (experimental): every scored generic anchor below 0.25 is rejected. Available for controlled evaluation, **not the deployed default**.
- `legacy`: previous v1 assistance. `off`: rules only.

Cache identity includes mode and model version. Missing/corrupt v2 falls back to v1; missing v1 falls back to rules. JSON validation limits file size, dimensions, node count and finite values, and rejects tree cycles. No executable pickle is loaded.

Feeds, APIs, embedded data, explicit CSS selectors, specialized novel/comic/YouTube adapters, URL safety, user path restrictions, pagination and date evidence retain authority. At most **4,000 anchors per page** are scored. Unscored anchors retain rule fallback, even in primary mode. The classifier makes **zero requests**; existing pacing, HTTP budgets, caching and backoff remain intact. NumPy and scikit-learn are not production dependencies.

Refresh does not retroactively delete saved entries or change read/favorite/ignore state. Existing unwanted links can be bulk ignored or moved to Trash; model predictions do not erase user progress.

## Evaluation and limitations

The candidate and integration were frozen before evaluating the reserved hosts:

| Test page | Previous relevant / labeled | New relevant / labeled | New unwanted |
| --- | ---: | ---: | ---: |
| Project Gutenberg | 1 / 25 | **25 / 25** | 0 |
| Smashing Magazine | 10 / 10 | 10 / 10 | 0 |
| Talk Python | 560 / 560 | 560 / 560 | 0 |
| Total | 571 / 595 | **595 / 595** | **0** |

The classifier alone and primary mode also passed these pages. This is limited evidence: only three pages, 560 podcast episodes dominating the aggregate, and Talk Python sharing a publishing platform with validation source Python Bytes. No model or integration tuning followed this test. A later baseline-only replay used the frozen original parser for an exact comparison; candidate predictions were preserved.

On seven development validation pages, default recall improves from 49.23% to 95.57% and precision from 96.33% to 97.99%. It still includes 23 unwanted URLs (20 on Real Python) and misses 52 Julia Evans posts. Primary mode reduces unwanted URLs to 8 but misses 13 Hacker News headlines, which is why fallback remains enabled.

On full **training/development** snapshots, not independent tests, unwanted URLs fall from 686 to 14. Planet Python improves from 13/25 relevant with 46 unwanted to 25/25 with 1 unwanted; LWN from 8/20 with 7 unwanted to 20/20 with none. GitHub gains four unwanted links and Changelog gains two; reports retain these regressions. Codeforces figures concern HTML fallback; normal discovery uses its contest API.

Reports count unique source/URL pairs and list predictions outside the labeled universe. `context-train-report.json` evaluates uncapped snapshots while fitting uses capped rows. Baseline source is retained in `v1/parser.py`. All v1 test sites are development data now. The model cannot expose content absent from fetched HTML/API responses.

Next priorities: independently reviewed nontechnical indexes, sparse records, main versus related-content cards, multilingual titles, and more unrelated publishing templates. Favorites/ignores must not automatically become relevance labels because they can represent preference rather than extraction mistakes.

## Reproduce and operate

From `backend`:

```bash
uv sync --group ml
uv run --group ml python ml/train_context.py
uv run --group ml python ml/evaluate_context.py --split validation
uv run pytest -q
uv run python ml/benchmark_context.py
```

Training is offline from the frozen JSONL, uses one BLAS thread and seed 81, and writes a **candidate** under `ml/context-candidate`. It never replaces the bundled artifact automatically. Review per-site regressions and evaluate new untouched sites before promotion. Reusing the current test set makes it development data. All candidates trained locally in about 21 seconds. Peak Python allocations during model loading were about 0.7 MiB; VM measurements are in `context-benchmark.json`.

On the Azure VM, a **128 MiB / one CPU / no-network** benchmark container peaked at **47.75 MiB RSS**, including Python, BeautifulSoup, a 1,000-link page, and the model. Scoring 1,000 extracted vectors took **86.75 ms**; extracting their features took **424.57 ms**. This bounds the benchmark, not a loaded web server. The live app used about **50 MiB when idle** immediately after deployment.

Retraining also completed in a separate **1 GiB / one CPU** container, with inputs mounted read-only and output under `/home/azureuser/ml-context-training-output`. Fitting all candidates took 17.39 seconds after dependency installation. It selected the same tree architecture and validation outcomes. Neural candidate scores differed across Windows/Linux numerical libraries, so retraining does not imply byte-identical exports. The independently trained VM artifact was not substituted for the tested deployment artifact. See `context-vm-training-report.json`.

Optional capture/label rebuilding:

```bash
uv run python ml/collect.py
uv run python ml/collect.py --manifest v2_sources.json
uv run python ml/import_html_corpus.py
uv run python ml/build_v2_dataset.py
```

Captures change over time: review labels and hashes before rebuilding. Full parser replay needs raw snapshots; training needs only frozen feature rows. The original v1 design is retained in `v1/README.md`.

Production adds no dependency or database migration. Set the desired mode in `deploy/.env` and recreate the app with the existing Compose command. After deployment, `catchup-app:before-context-model` retains the previous image; the daily database backup remains enabled.

References: [gradient boosting](https://scikit-learn.org/1.8/modules/ensemble.html#gradient-boosting), [MLPClassifier](https://scikit-learn.org/1.8/modules/generated/sklearn.neural_network.MLPClassifier.html), [Web2Text paper](https://arxiv.org/abs/1801.02607).
