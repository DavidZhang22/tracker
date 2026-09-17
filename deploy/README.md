# Hosting Trackify

The Docker Compose stack runs FastAPI, a network-isolated Chromium worker and Caddy HTTPS on one Linux VM. SQLite uses a persistent volume; no managed database or paid inference service is needed. The current deployment is documented in [AZURE.md](AZURE.md).

## Setup

1. Install Docker Engine and its Compose plugin on a Linux VM. Enable Docker at boot.
2. Point a domain at the VM's public IP. Allow inbound TCP 80/443 and SSH from your administration address. Keep port 8000 private.
3. Copy the repository to the server, excluding local databases, credentials and dependencies.
4. Copy `deploy/.env.example` to `deploy/.env`, restrict it with `chmod 600 deploy/.env`, and set `TRACKER_DOMAIN`, the operator identity, public contact email and hosting region.
5. Choose a registration mode, then build and start:

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml build
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --wait
docker compose --env-file deploy/.env -f deploy/compose.yaml ps
```

Caddy obtains and renews certificates once DNS and ports are ready. Open `https://YOUR_DOMAIN`.

The frontend uses Vite to generate `frontend/build`, with JavaScript and CSS
under `build/static`. Both Dockerfiles copy that directory to
`/app/frontend/build`, where FastAPI serves `/static` and the application routes.
The image build uses Node.js 24; the production Python image contains the built
frontend files without the Node build tools. For the prebuilt image, run
`npm ci` and `npm run build` in `frontend/` before building
`deploy/Dockerfile.prebuilt`.

When a release changes `deploy/Caddyfile`, recreate the web service after validating
the new configuration. A single-file Docker bind mount can retain the old file
after Git or archive extraction replaces it; reloading that mount can silently use
old upload limits. Recreating attaches the current file and preserves the existing
certificate volumes:

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --no-build --no-deps --force-recreate web
docker compose --env-file deploy/.env -f deploy/compose.yaml exec web cat /etc/caddy/Caddyfile
```

Check `/api/ready`, the application HTML and its referenced `/static/` assets
after updating. Navigate to an item and settings to verify lazy page imports.
Both
`/api/scans/csv` and `/api/scans/import` must allow 4 MB at the proxy and application
guards. A permitted-size upload without a session should return 401; an oversized
upload should return 413. Use a client that can read an early rejection while
transmitting (for example, curl); sending only headers can wait for the body timeout.

## Registration

| Mode | Configuration |
| --- | --- |
| Public | `TRACKER_PUBLIC_SIGNUP=1`; no invite required |
| Invited | `TRACKER_PUBLIC_SIGNUP=0`; set a random `TRACKER_SIGNUP_CODE` of at least 20 characters |
| Closed | `TRACKER_PUBLIC_SIGNUP=0`; leave `TRACKER_SIGNUP_CODE` empty |

Recreate the app after changing configuration. Public signup allows three successful registrations per IP per hour and twenty across the server per hour, with a 200-account total cap. Successful signup counters persist in SQLite and expire after one hour. Duplicate usernames do not consume the success quota. Login/password attempts and API requests have separate limits. Closing registration preserves existing accounts and sessions.

All modes require sign-in to access library data. Each new account starts with an empty private library. Accounts use usernames and passwords of 10–128 characters; no email service is required. Forgotten passwords require the operator to verify ownership before using the private CLI:

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml exec app \
  /app/backend/.venv/bin/python -m app.tracker.accounts reset-password USERNAME
```

The same command supports `create USERNAME` for private account creation. When importing an existing personal library, close public signup first and create its owner with `create USERNAME --claim-existing`. Only the private CLI can claim that library.

## Storage and operations

Keep the Compose project name `catchup`: deployed data resides in `catchup_tracker-data`. Renaming the project would select new empty volumes. Session cookie names and backup filenames also retain their original names for compatibility.

Use SQLite's online backup API and verify snapshots. Do not copy a live database file without its WAL state. The current VM's daily backup, retention and restore process is documented in [AZURE.md](AZURE.md) and [PRIVACY_SECURITY.md](PRIVACY_SECURITY.md). Never restore a backup without applying the current account-erasure ledger. Keep private off-server backups; the default daily job stores snapshots on the VM.

`/api/ready` returns 503 when storage is unavailable. Writes fail visibly and existing data is preserved. Authentication, exact-origin checks and per-account authorization remain enabled in production. Models, scans and browser rendering have bounded concurrency and resource limits; see [SAFEGUARDS.md](SAFEGUARDS.md).

Run one Uvicorn application worker. The default two analysis processes speed up parsing without duplicating database or network orchestration. The browser container has no direct network access and receives validated responses through the app's request broker.

## Link model

`TRACKER_LINK_MODEL=cascade` enables the tested light/deep combination: the existing classifier handles every candidate and a small neural network can rescue borderline rejections. `on` selects the previous classifier; `off` selects the original rules and adapters. Recreate the app after changing this value. A missing or invalid cascade falls back to the previous classifier. Model and extraction versions invalidate cached scan results.

Both Dockerfiles compile a fixed, bounded C inference kernel in a separate build stage. The compiler and training dependencies are absent from the runtime image. Python inference remains available when the kernel cannot load. No model training or uploaded executable/model loading occurs in the web application. See the [benchmark and evaluation limits](../backend/ml/reports/model-pipeline.md).
