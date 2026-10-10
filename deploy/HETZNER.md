# Hetzner deployment

Prepared on October 10, 2026. The Azure data import and DNS cutover are pending.

| Setting | Value |
| --- | --- |
| Server | CX23, Helsinki, `ubuntu-4gb-hel1-2` |
| IPv4 | `204.168.136.246` |
| IPv6 | `2a01:4f9:c012:ead9::1` |
| OS | Ubuntu 26.04.1 LTS, x86-64 |
| SSH | `root`, public-key authentication |
| Application directory | `/opt/trackify` |
| Compose project | `catchup` |
| Data volume | `catchup_tracker-data` |
| Domain | `mediatrackify.duckdns.org`, still pointing to Azure |
| Application release | `b9b539d` |

## Prepared state

Docker and Compose are installed and enabled at boot. The app and renderer run
as non-root users with read-only filesystems and separate 1 GiB memory limits.
The renderer has no direct network access. Only Caddy publishes application ports;
port 8000 stays private.

SSH accepts keys only, including for root. UFW rate-limits TCP 22 and permits
TCP 80/443 and UDP 443 for both IPv4 and IPv6. Automatic security updates are
enabled.

The deployment uses `TRACKER_LINK_MODEL=cascade` and two analysis workers.
`deploy/.env` is private (mode 600). Registration is closed and email delivery
is disabled during staging. The hosting description identifies Hetzner Cloud
in Helsinki, Finland.

The deployed build comes from committed source. Unfinished local model
experiments, local databases and credentials were excluded. Native inference,
the cascade, 384-dimensional semantic embeddings, JavaScript pagination,
entry actions, authentication and frontend assets passed staging checks.
These checks used synthetic data and did not import any user accounts.
The server was rebooted into kernel 7.0.0-38-generic; the services recovered
automatically and the model/browser checks passed again.

## Staging operations

Run these commands from `/opt/trackify`. Keep the staging override until the
final import is verified:

```sh
docker compose -p catchup --env-file deploy/.env \
  -f deploy/compose.yaml -f deploy/compose.staging.yaml up -d --no-build --wait
docker compose -p catchup --env-file deploy/.env \
  -f deploy/compose.yaml -f deploy/compose.staging.yaml ps
docker compose -p catchup --env-file deploy/.env \
  -f deploy/compose.yaml -f deploy/compose.staging.yaml logs --tail=50 app browser web
```

The staging proxy returns HTTP 503 with a maintenance message on port 80. It
does not expose the empty staging library or request a TLS certificate. Check
the private application's readiness from the host:

```sh
docker exec catchup-app-1 /app/backend/.venv/bin/python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/ready', timeout=5).status)"
```

Images are also tagged `catchup-app:hetzner-b9b539d` and
`catchup-browser:hetzner-b9b539d`. Container restart policies restore the
running configuration after a host restart.

## Backups

`catchup-backup.timer` runs daily around 04:00 UTC, with up to five minutes of
jitter. Its host service uses these Hetzner paths:

```ini
[Unit]
Description=Verified online backups of Trackify databases
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
UMask=0077
ExecStart=/usr/bin/python3 /opt/trackify/deploy/backup.py /var/lib/docker/volumes/catchup_tracker-data/_data /var/backups/mediatrackify
TimeoutStartSec=300
Nice=10
```

The timer is installed from `deploy/catchup-backup.timer`. The backup script
uses SQLite's backup API, verifies integrity and retains at most seven
snapshots for seven days. The initial empty staging databases passed backup
verification.

```sh
systemctl start catchup-backup.service
journalctl -u catchup-backup.service -n 10 --no-pager
systemctl list-timers catchup-backup.timer --no-pager
```

These snapshots are on the same VM. Private off-server backup storage still
needs to be configured. Do not publish archives or commit them to Git.

## Remaining migration

1. Reactivate Azure and confirm its current accounts and libraries. Preserve
   the latest source data, configuration and account-erasure ledger.
2. Put Azure into maintenance mode, stop application writes and make a final
   verified snapshot. Include all account libraries and settings. Preserve
   shared scan caches and recipes when transferring the stopped data volume.
3. Transfer through SSH into a private staging directory on Hetzner. Keep
   source credentials out of terminal output. Copy the source environment
   settings, retaining the Hetzner hosting description and leaving email
   delivery disabled. Registration should stay closed until validation.
4. Validate all databases and compare account/item counts. Apply the current
   erasure ledger when restoring any snapshot using
   `deploy/prepare-restore.py`; never reintroduce deleted accounts or old
   recovery addresses. Stop the Hetzner app before replacing its data, and
   preserve the container user's ownership. Keep a copy for rollback.
5. Transfer the existing Caddy certificate state consistently or arrange
   certificate issuance after the DNS change. Verify the restored app and
   private library access before exposing it.
6. Update the domain's A record to `204.168.136.246`. If an AAAA record is used,
   it must point to `2a01:4f9:c012:ead9::1`. Start the production proxy without
   the staging override, recreating the web container so its bind mount
   attaches the production Caddyfile. Verify HTTPS, sign-in, library counts,
   refreshes and a new backup. Restore the intended registration mode.
7. Keep Azure available for rollback until verification passes. If users
   have already written data on Hetzner, reconcile those writes before any
   rollback. Remove unneeded Azure resources only after the migration is
   accepted.

Do not change the Compose project name: a different name selects new, empty
volumes. See [AZURE.md](AZURE.md) for the source server and
[PRIVACY_SECURITY.md](PRIVACY_SECURITY.md) for restoration safeguards.
