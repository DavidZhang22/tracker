# Catchup API

See the root README.MD for complete setup and design notes.

```powershell
uv sync
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
uv run pytest -q
```

`app/tracker/parser.py` contains pure source parsing, `discovery.py` orchestrates bounded scans, `urls.py` validates and pins network targets, `store.py` owns SQLite transactions, and `api.py` exposes the application routes. The earlier prototype services are retained but are not used by Catchup.
