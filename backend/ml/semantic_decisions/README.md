# Contextual decision experiments

This offline package contains the family-disjoint CPU classification experiment. It is excluded from the production image. The [recorded results](../reports/semantic-decision.md) rejected every tested candidate for deployment; organizing these tools does not change that outcome.

| Module | Purpose |
| --- | --- |
| `data.py` | Bounded candidate context extraction, shared by evaluation and runtime |
| `experiment.py` | Dataset preparation, head training, validation and full-parser replay |
| `benchmark.py` | Isolated inference processes, cold/warm timing, memory and decision parity |
| `audit_batches.py` | Compare cached global batches with page-local inference |

Tests live in `backend/tests/ml`. Shared provenance, evaluation and storage helpers remain in `backend/ml`.

## Run

From `backend`, install offline dependencies with `uv sync --group ml`. The commands below require the original ignored HTML captures and locally verified encoder assets. Freshly downloaded pages cannot reproduce frozen labels.

Replay the retained finalist without fitting or fetching pages:

```sh
uv run --group ml python -m ml.semantic_decisions.experiment evaluate --manifest ml/datasets/semantic-decision-holdout-sources.json --output data/semantic-replay/holdout.json
uv run --group ml python -m ml.semantic_decisions.experiment evaluate --output data/semantic-replay/regression.json
uv run --group ml python -m ml.semantic_decisions.benchmark --repeats 5 --page-id breadth-semantic-holdout-walters --page-id breadth-semantic-holdout-wireshark --page-id breadth-semantic-holdout-nostarch --evaluation ml/reports/semantic-decision-holdout.json --output data/semantic-replay/runtime.json
uv run --group ml python -m ml.semantic_decisions.audit_batches --output data/semantic-replay/batch-parity.json
```

The benchmark needs the native kernel for native-comparison figures and is expected to fail its parity gate for this frozen finalist. CPU and memory limits must be imposed externally. The batch audit defaults to the original Linux runtime report, which the artifact reader opens transparently from gzip.

For a new training run, use a new experiment and cache directory. Source-code hashes are part of the frozen protocol, so a refit in the archived directory will correctly reject the reorganized source paths.

```sh
uv run --group ml python -m ml.semantic_decisions.experiment fit --directory data/semantic-new/models --cache data/semantic-new/cache
```

Pass the same `--directory` and `--cache` when evaluating or benchmarking that new experiment. Direct script paths also work, for example `python ml/semantic_decisions/experiment.py --help`.

## Archive provenance

The September 22 experiment was recorded at commit `c4a486333396bb6224c295ed621ce1bada36db2d`. Its gate report retains the source paths and document hashes from that run. The current package replaces `semantic_decision_data.py`, `semantic_decision_experiment.py`, `benchmark_semantic_decisions.py` and `audit_semantic_batches.py`; numerical model, protocol, validation and runtime-result bytes remain unchanged.

Large JSON artifacts use gzip storage. [`ml.artifacts`](../artifacts.py) resolves logical paths and hashes their original bytes. [The storage inventory](../artifact-storage.json) records the mapping and checksums. Restore an explicit file with `python -m ml.artifacts --restore path.json.gz` if an external tool needs plain JSON, and compress it again with `python -m ml.artifacts path.json`.
