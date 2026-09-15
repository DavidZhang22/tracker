# Models and discovery tools

Production loads the three bounded JSON models in [`app/tracker`](../app/tracker). Training and benchmarking are offline tools; they are excluded from the production image.

| Location | Purpose |
| --- | --- |
| [`datasets/`](datasets) | Frozen training rows, dataset cards and source manifests |
| [`reports/`](reports) | Recorded evaluations, profiles and memory measurements |
| [`experiments/`](experiments) | Candidate model artifacts and their reports |
| [`baselines/v1/`](baselines/v1) | Original model and parser for historical comparisons |
| [`docs/`](docs) | Model designs, evaluation limitations and benchmark instructions |
| `raw/` | Ignored local source captures; never private account data |

The Python tools stay in this directory so existing direct CLI imports work. `train_*` and `build_*` prepare candidates and datasets; `evaluate_*` and `benchmark_*` measure them; `verify_*` exercise isolated runtime behavior. Tools that collect live pages are opt-in and retain request limits.

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
- [Suggestions](docs/SUGGESTIONS.md)
