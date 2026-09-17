# Library search and descriptions

Trackify combines lexical matches, Damerau–Levenshtein typo correction, topic hints and cosine similarity from a quantized `all-MiniLM-L6-v2` sentence encoder. Exact names, URLs and filenames retain priority, including when spelling correction suggests a different word. This indexes library items, not every individual chapter or external page. Existing per-item link filtering remains text-based.

The CPU encoder produces 384-dimensional normalized vectors. It uses the upstream verified ONNX INT8 export, at most 192 wordpieces, batches of eight, and two inference threads. One lazy encoder serves the API process; parser workers do not load it. No GPU, external vector database, inference API key or additional scraping is needed. Weights are downloaded only during installation/build, pinned by revision and verified against checksums. ONNX telemetry is disabled.

Descriptions are extractive: the encoder scores sentences from the already-scanned HTML metadata and introductory paragraphs, removes common boilerplate and near-duplicates, and selects up to three sentences within 700 characters. This does not invent a synopsis when a source lacks descriptive text. Source errors, unusual markup and marketing language can still affect results. Users can edit up to 1,200 characters in Item settings, keep an intentionally empty description, or restore automatic descriptions. Overrides survive refreshes.

## Evaluation and choice

The reproducible development evaluation uses 47 saved public profiles and 34 hand-labelled queries: 16 semantic paraphrases, eight typos, six exact queries and four unrelated queries. These are development checks, not a held-out or universal accuracy estimate. The model weights were not trained on this corpus. Reports retain individual results, including failures.

| Candidate | Semantic top 1 / 16 | Semantic top 3 / 16 | Typos top 1 / 8 | Unrelated queries returning nothing / 4 | Local warm median |
| --- | ---: | ---: | ---: | ---: | ---: |
| Text + fuzzy baseline | 3 | 3 | 8 | 4 | 4.7 ms |
| MiniLM L3 INT8 | 13 | 13 | 8 | 4 | 8.4 ms |
| **MiniLM L6 INT8** | **14** | **15** | **8** | **4** | **9.6 ms** |
| BGE small INT8 | 14 | 16 | 8 | 0 | 11.9 ms |

MiniLM L6 balances recall, rejection of unrelated queries, latency and memory with the tested thresholds. BGE needs separate threshold calibration before promotion. L6 found all six exact queries first; one semantic query about code formatting returned no match. The English-focused model is less reliable for other languages, very short queries and items with little descriptive text. Future evaluation should add independently labelled multilingual queries and harder negative examples.

Reports: `reports/search-{none,minilm-l3,minilm-l6,bge-small}.json`. Run from the repository root:

```sh
backend/.venv/bin/python backend/ml/fetch_semantic_model.py
backend/.venv/bin/python backend/ml/benchmark_search.py --model minilm-l6 --output backend/ml/reports/search-minilm-l6.json
```

For Windows use `.venv/Scripts/python.exe`. Alternative candidate weights require `fetch_semantic_model.py --model NAME` first. Candidate inference never selects a runtime model automatically.

## Storage, refresh and failure behavior

- Each account's SQLite database stores its own vectors (1,536 bytes per item, about 300 KiB for 200 items), source excerpts and descriptions. No account text enters a global search cache. Account deletion removes these records; export includes the underlying text and description overrides rather than rebuildable binary vectors.
- A versioned fingerprint covers title, media type, summary, tags, filename and the user's description. An unchanged item skips inference. Text-only fallback preserves current profiles and rebuilds obsolete profile versions. Inference happens outside transactions; conditional writes reject stale results after a concurrent edit or deletion. Trash is excluded from indexing. Existing valid vectors can still search Trash.
- Search requests use POST, a 200-character limit, four active searches maximum, and per-account/global rate limits. Cancellation holds admission until the worker actually ends. Queries are not stored server-side. Production access logging is disabled.
- The browser debounces by 250 ms, cancels outdated requests, caches 24 completed queries in component memory and keeps results visible during updates. Exact/typo text search continues when model files or inference are unavailable. Database failures retain the app's explicit service-unavailable handling; they are never reported as a successful empty library.
- `TRACKER_SEMANTIC_SEARCH=0` disables the encoder. Restore it and restart after repairing model files. Local setup runs `uv run python ml/fetch_semantic_model.py`; Docker installs the verified model and license automatically.

## Runtime memory check

`verify_semantic_runtime.py` creates a temporary 200-item synthetic library using public profile text, builds the index, and runs 30 searches alongside two parser workers processing 8,000 synthetic listing rows. Run it in the production image with network disabled, a read-only root, 128 MiB temporary storage, two CPUs and a 1 GiB memory cap. It never modifies a live library or fetches source websites. Report timing includes index validation, SQLite reads, typo matching and inference; it is distinct from the smaller local encoder comparison. See `reports/search-runtime.json` for the deployment measurement.

## Primary references

- [MiniLM sentence encoder and pooling](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)
- [Sentence Transformers ONNX quantization](https://www.sbert.net/docs/sentence_transformer/usage/efficiency.html)
- [BGE retrieval conventions](https://huggingface.co/BAAI/bge-small-en-v1.5)
- [ONNX Runtime telemetry controls](https://onnxruntime.ai/docs/api/python/api_summary.html#telemetry-events)

The final Azure container run peaked at 304.2 MiB. Initial indexing of 200 items took 10.13 s; the unchanged-index check took 6.57 ms. Under concurrent parser load, full search was 125.66 ms median / 176.98 ms p95. These are bounded workload measurements, not a guarantee for all pages or traffic patterns.
