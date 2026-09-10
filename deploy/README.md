# Hosting Catchup

The app now supports username/password accounts, separate libraries, revocable sessions, sign-out, and password changes. Registration is closed unless an invite code is configured. Passwords use Argon2id; neither passwords nor raw session tokens are stored in the database. No email service is required; the site owner can reset forgotten passwords through the private command line.

## Free hosting tradeoffs, checked September 9, 2026

For the current Python scanner and persistent SQLite storage, **an Oracle Cloud Always Free VM is the closest fit for continuously running hosting**. An eligible A1 VM in the home region can use the current free allowance of 2 OCPUs / 12 GB RAM; its boot disk counts toward the 200 GB combined storage allowance. Capacity may be unavailable and idle instances may be reclaimed. Free hosting is not a guarantee of uninterrupted service. Stay on eligible resources and verify the console's price before provisioning. Do not generate fake traffic or CPU load to defeat idle policies. [Oracle's current terms and resource limits](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm).

Alternatives require a tradeoff:

| Option | Availability and storage |
| --- | --- |
| Oracle Always Free VM + this deployment | Runs the complete existing scanner and persistent disk. Possible capacity shortages / idle reclamation. |
| Google Cloud Run + a managed database | Accessible on demand, normally scales to zero and can cold-start. Requires a billing account and can charge beyond free quotas. Needs a database migration before using this SQLite implementation. |
| Render Free | Sleeps after 15 minutes; wake-up can take about a minute. Local SQLite files are lost and its free Postgres expires after 30 days. This repository must not be deployed there with local SQLite. |
| Static hosting / JavaScript Workers | Does not run this Python backend and yt-dlp subprocess as packaged. |

References: [Cloud Run pricing](https://cloud.google.com/run/pricing), [Google billing requirement](https://docs.cloud.google.com/free/docs/free-cloud-features), [Render free limitations](https://render.com/docs/free).

## Deploy to an eligible Linux VM

These steps require your own cloud account and VM. They do not create paid resources or assume an existing project is safe to bill. Account signup/card verification must be completed directly with the provider. The deployment has not been run on a remote VM yet.

1. Create an **Always Free eligible** Ubuntu A1 VM in the account's home region, within the current free allowance. Retain its boot disk when replacing the VM. Keep your SSH key private. Allow inbound TCP 80/443 (and optionally UDP 443) in the cloud network rules; allow SSH only from your address. Do not expose port 8000. Apply equivalent rules in the OS firewall, retaining SSH access.
2. Install Docker Engine with its Compose plugin using the [official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/). Enable Docker at boot (`sudo systemctl enable --now docker`). Do not disable the firewall to troubleshoot.
3. Obtain a free subdomain from [Duck DNS](https://www.duckdns.org/), point it to the VM's public IP, and keep it updated if that address changes. Any domain pointing at the VM works. This stack's Caddy proxy obtains and renews HTTPS certificates automatically once DNS and ports are ready.
4. Transfer the app's source to a private directory on the VM. Exclude `.git`, `.env` files, `node_modules`, local virtual environments, `backend/data`, and test captures. Transfer existing data separately as described below. Run the following from the repository root:

```sh
cp deploy/.env.example deploy/.env
chmod 600 deploy/.env
# Edit TRACKER_DOMAIN in deploy/.env to your real domain.
# Leave TRACKER_SIGNUP_CODE empty unless you want invited people to create accounts.
docker compose --env-file deploy/.env -f deploy/compose.yaml build
```

5. **Before opening the site**, create your owner account. This prompts privately for the password; use a unique password of at least 10 characters. `--claim-existing` assigns the original personal library to this account, so another account cannot obtain it:

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --no-deps app \
  /app/backend/.venv/bin/python -m app.tracker.accounts create YOUR_USERNAME --claim-existing
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d
docker compose --env-file deploy/.env -f deploy/compose.yaml ps
```

Open `https://YOUR_DOMAIN` and sign in. New users get their own empty library. The app's port is private to the Compose network; Caddy is its only public entry point. The deployment requires HTTPS and rejects mutations with a missing or mismatched browser origin. Session cookies are HttpOnly, Secure, and SameSite=Lax, expire after seven days, and are revoked on logout or password change. Account attempts are rate limited. This single-VM deployment uses one application process; shared scan pacing is not designed for multiple replicas.

To create another account without invitations:

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml exec app \
  /app/backend/.venv/bin/python -m app.tracker.accounts create ANOTHER_USERNAME
```

For invitations, generate a strong random value locally, set `TRACKER_SIGNUP_CODE` in `deploy/.env`, then rerun `up -d`. Share the code privately. Clearing or changing the code closes or rotates registration without disabling existing accounts. There is no open public signup, email verification, or email password recovery.

## Import the current library

Make a consistent SQLite backup while the local app is stopped, or use SQLite's backup API. The personal library is `backend/data/tracker.sqlite3`. Transfer that backup to the VM through SSH/SCP, keeping it private. Before the first `up -d` and before importing any new cloud data:

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml create app
docker compose --env-file deploy/.env -f deploy/compose.yaml cp ./tracker.sqlite3 app:/data/tracker.sqlite3
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --no-deps --user root app \
  sh -c 'chown tracker:tracker /data/tracker.sqlite3 && chmod 600 /data/tracker.sqlite3'
```

Then create the owner with `--claim-existing` and start the stack. Only one account can claim the original library. Public signup can never claim it. Do not overwrite a cloud database that already contains new progress; reconcile or back up that data first. Future accounts live in `/data/users/<server-generated-id>/tracker.sqlite3`, and account/session records live in `/data/accounts.sqlite3`.

## Back up and maintain

The named `tracker-data` volume survives restarts, redeployments, and ordinary `docker compose down`. **Do not use `down -v`**, which deletes volumes. Caddy's certificate data is persisted separately.

For a consistent complete backup of accounts and all libraries, briefly stop the app, copy `/data` from its stopped container to a private backup directory, and start it again:

```sh
umask 077
docker compose --env-file deploy/.env -f deploy/compose.yaml stop app
docker compose --env-file deploy/.env -f deploy/compose.yaml cp app:/data ./catchup-backup
docker compose --env-file deploy/.env -f deploy/compose.yaml start app
```

Keep a dated copy off the VM, encrypted or on trusted private storage. Database files contain password hashes and personal data, and backups should not be published. A disposable `fetch-cache.sqlite3` can be omitted from backups. Disk persistence does not replace backups if the provider reclaims or deletes a VM.

To reset a forgotten password, run `accounts reset-password YOUR_USERNAME` instead of `accounts create` in the private CLI command above. This revokes all sessions for that account. Updating the source and running `up -d --build` keeps the volume; back it up first. Reboots restart both services automatically through Docker's restart policy.

Local development remains available without accounts on localhost. To test sign-in locally, export `TRACKER_AUTH_REQUIRED=1` and `TRACKER_ORIGIN=http://127.0.0.1:8000`, create an account with the CLI, and restart. Plain HTTP cookies are allowed only for localhost/test origins; cloud deployment always uses HTTPS.

## Validation status

Authentication and cross-account isolation are exercised with temporary databases. Frontend sign-in/account flows have component tests. Docker is not installed in the development environment, so the container build and Caddy certificate issuance require validation on the chosen VM. No public deployment URL is claimed before that validation succeeds.

## Database outages

The API returns HTTP 503 with `Retry-After: 5` and a generic storage message when SQLite is unavailable, locked, full, or corrupt. Authentication fails closed; an outage never switches to a shared library or clears a session cookie. Transactions roll back on failure. Requests are not automatically replayed: a response can be lost after a successful commit, so inspect the current data before repeating a save.

`/api/health` checks whether the process responds. `/api/ready` checks the main library and account database; Compose uses readiness. Individual user library failures are checked when that library is requested. This is a read check, not a guarantee that every subsequent write will succeed. Existing content remains in the page if reloading fails, and an initial library failure displays Retry loading instead of an empty collection. Scans check storage before contacting sources.

Normal database connections cannot create a missing database. Adjacent `.initialized` marker files also prevent recreating a previously initialized database at startup or when reopening a user library. Include these markers in backups. These safeguards cannot detect the loss of an entire volume, including its markers: verify the correct persistent volume is mounted before starting a replacement server.

To recover, stop the app, check the persistent volume, free disk space or correct permissions, and restore the complete data directory from an off-server backup if necessary. Preserve database files and their WAL files together; do not delete a database to clear an error. Start the app and check `/api/ready`, then verify account access and saved items. Transient runtime failures recover on the next request once storage is available. Missing or corrupt databases during startup intentionally prevent startup until repaired. Automated off-server backups and failover are not configured by this deployment.

## Free hosting without idle sleep

No free option promises uninterrupted availability. Oracle Always Free can run this Python app continuously, but Oracle may reclaim idle instances. Cloudflare Workers avoids VM cold starts and D1 offers free persistent storage, but this application's Python scanner and subprocess dependencies need a redesign to fit Workers, especially its free CPU quota. D1 queries fail once daily free query quotas are exhausted. Koyeb Free sleeps after one hour without traffic; Render Free sleeps after fifteen minutes. Those two do not meet a strict no-sleep requirement.

Sources checked September 9, 2026: [Oracle policy](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), [Workers architecture](https://developers.cloudflare.com/workers/reference/how-workers-works/), [Workers limits](https://developers.cloudflare.com/workers/platform/limits/), [D1 limits](https://developers.cloudflare.com/d1/reference/faq/), [Koyeb sleep](https://www.koyeb.com/docs/run-and-scale/scale-to-zero), [Render Free](https://render.com/docs/free).
