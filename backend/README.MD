# Trackify API

See the [project README](../README.MD) for setup and deployment.

```powershell
uv sync
uv run python ml/fetch_semantic_model.py
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
uv run pytest -q
```

| Location | Purpose |
| --- | --- |
| `app/main.py` | App lifecycle, authentication middleware and error handling |
| `app/tracker/api.py` | Library, link and import routes |
| `app/tracker/store.py` | SQLite transactions and state-preserving merges |
| `app/tracker/discovery.py` | Bounded scan orchestration |
| `app/tracker/parser.py` | HTML link extraction |
| `app/tracker/file_import.py`, `import_pool.py`, `import_worker.py` | Bounded document normalization and isolated parsing |
| `app/tracker/urls.py` | URL validation, request pacing and safe fetching |
| `app/tracker/*_model.py` and model JSON files | CPU inference |
| `tests/` | Offline tests and small source fixtures |
| `ml/` | [Training data, tools and evaluation evidence](ml/README.md) |
| `data/` | Ignored libraries, caches and local working files |

Production requires neither a separate database service nor the training dependencies. Keep database files and private captures out of source control.
