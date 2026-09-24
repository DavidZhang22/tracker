# Models and discovery tools

Library search and source descriptions use a local quantized sentence encoder. See [semantic search](docs/SEMANTIC_SEARCH.md) for installation, evaluation, privacy and resource limits.

Production loads bounded numeric JSON models in [`app/tracker`](../app/tracker). The optional light/deep cascade adds a small neural rescue pass to the existing link classifier. Docker builds compile a fixed C inference kernel; Python remains the fallback. Training and benchmarking are offline tools, excluded from the production image.

| Location | Purpose |
| --- | --- |
| [`datasets/`](datasets) | Frozen training rows, dataset cards and source manifests |
| [`reports/`](reports) | Recorded evaluations, profiles and memory measurements |
| [`experiments/`](experiments) | Candidate model artifacts and their reports |
| [`media_audit/`](media_audit) | Ranked 200-site acquisition, partial-label corpus, feed alternatives and registry profiling |
| [`context_expansion/`](context_expansion) | Reviewed record details, bounded DOM augmentation and model comparisons |
| [`semantic_decisions/`](semantic_decisions) | Contextual decision-model data, training, replay and CPU benchmarks |
| [`baselines/v1/`](baselines/v1) | Original model and parser for historical comparisons |
| [`docs/`](docs) | Model designs, evaluation limitations and benchmark instructions |
| `raw/` | Ignored local source captures; never private account data |

Shared tools retain their direct CLI entry points. The contextual decision experiment lives in the `semantic_decisions` package, with its focused tests in `backend/tests/ml`. `train_*` and `build_*` prepare candidates and datasets; `evaluate_*` and `benchmark_*` measure them; `verify_*` exercise isolated runtime behavior. Tools that collect live pages are opt-in and retain request limits.

Run from `backend`:

```sh
uv sync --group ml
uv run --group ml python ml/train_context.py
uv run --group ml python ml/evaluate_context.py --split validation
uv run python ml/benchmark_context.py
uv run python -m ml.verify_csv_runtime
```

Training uses frozen feature rows. Full page replay additionally needs the ignored captures described in the source manifests. Candidate artifacts are not promoted automatically; review per-source regressions before replacing a runtime model. Reports retain the paths and measurements from their original runs.

- [Link classifiers and dataset provenance](docs/LINK_CLASSIFIER.md)
- [Record context network](docs/RECORD_CONTEXT.md)
- [Lightweight refresh](docs/LIGHTWEIGHT_REFRESH.md)
- [Parallel analysis](docs/PARALLEL_ANALYSIS.md)
- [Response memory and throughput](docs/MEMORY_PIPELINE.md)
- [Refresh profiling](docs/REFRESH_PERFORMANCE.md)
- [Model improvement roadmap](docs/MODEL_ROADMAP.md)
- [Neural cascade, native inference, and measured release results](reports/model-pipeline.md)
- [Model review, expanded data, table features and inference optimization](reports/model-review.md)
- [Scraping and model deployment performance profile](reports/pipeline-profile.md)
- [Shared scan cooldown, redirect reuse, and cache profiles](reports/source-cache.md)
- [Hydrated listings and JavaScript pagination](reports/javascript-pagination.md)
- [General listing acquisition and source diversity evaluation](reports/acquisition-methodology.md)
- [Suggestions](docs/SUGGESTIONS.md)
- [Media classification, hidden descriptors and model comparison](docs/MEDIA_CLASSIFICATION.md)

- [Document imports and expanded source evaluation](reports/generalization.md)

- [Fresh extraction/media/search audit, UI and description screening](reports/ui-model-review.md)

- [113-website breadth evaluation, cross-domain corpus and extraction limitations](reports/website-breadth.md)

## Stored results

Large historical JSON reports and candidate models are stored as `.json.gz`. Small summaries, source manifests, datasets and active application models remain readable in their existing formats. [The storage inventory](artifact-storage.json) records original and compressed checksums for the archived files.

Offline tools accept either the original logical `.json` path or the stored `.json.gz` path through `ml.artifacts`. Checksums and model-size measurements use the original decompressed bytes. Regenerating a compressed result keeps its storage format. Prefer a fresh output directory under `backend/data` for new experiments so recorded comparisons remain available.

For external tools that require plain JSON, run from `backend`:

```sh
python -m ml.artifacts --restore ml/reports/semantic-decision-runtime-linux.json.gz
python -m ml.artifacts ml/reports/semantic-decision-runtime-linux.json
```

The second command compresses the restored file again. Both operations verify the original bytes before removing the other copy. Source captures, databases and backups are outside this archive workflow.

- [Contextual decision experiment and current commands](semantic_decisions/README.md)
- [Recorded CPU generalization results](reports/semantic-decision.md)

- [Context dataset expansion and neural/tree comparison](reports/context-expansion-models.md)
- [Listing details fixes and full-page measurements](reports/context-extraction.md)

- [Ranked 200-site audit, dataset expansion and source alternatives](reports/media200.md)
