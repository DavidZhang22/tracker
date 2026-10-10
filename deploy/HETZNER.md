# Hetzner deployment

Migration and public DNS cutover completed on October 10, 2026.

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
| Domain | `tracki.fyi`, pointing to `204.168.136.246` (Cloudflare DNS only) |
| Previous domain | `mediatrackify.duckdns.org`, redirects to `tracki.fyi` |
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
`deploy/.env` is private (mode 600). Public registration is restored to the source setting. Email delivery
remains disabled until an email sender is configured. The hosting description identifies Hetzner Cloud
in Helsinki, Finland.

The deployed build comes from committed source. Unfinished local model
experiments, local databases and credentials were excluded. Native inference,
the cascade, 384-dimensional semantic embeddings, JavaScript pagination,
entry actions, authentication and frontend assets passed staging checks.
The initial model and renderer checks used synthetic data. The final migration
then copied the stopped source's accounts, libraries, shared caches, environment
settings and Caddy certificate state. Every transferred file matched its SHA-256
checksum. Database integrity checks and logical comparisons confirmed that
accounts, password hashes, preferences, items, entries and the erasure ledger
matched the source after startup.
The server was rebooted into kernel 7.0.0-38-generic; the services recovered
automatically and the model/browser checks passed again.

## Operations

Run these commands from `/opt/trackify`. The running deployment uses the
production configuration:

```sh
docker compose -p catchup --env-file deploy/.env \
  -f deploy/compose.yaml up -d --no-build --wait
docker compose -p catchup --env-file deploy/.env \
  -f deploy/compose.yaml ps
docker compose -p catchup --env-file deploy/.env \
  -f deploy/compose.yaml logs --tail=50 app browser web
```

The production proxy serves `tracki.fyi` using a verified, automatically renewed
Let's Encrypt certificate. TLS validation, four application routes, eight initial
static assets, readiness, anonymous library rejection and exact-origin checks
passed after the domain cutover.
Check the private application's readiness from the host:

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
snapshots for seven days. The migrated account and library databases passed backup verification.

```sh
systemctl start catchup-backup.service
journalctl -u catchup-backup.service -n 10 --no-pager
systemctl list-timers catchup-backup.timer --no-pager
```

These snapshots are on the same VM. Private off-server backup storage still
needs to be configured. Do not publish archives or commit them to Git.

## DNS cutover and Azure retirement

The DuckDNS A record points to `204.168.136.246`; no AAAA record is set.
Cloudflare and Google resolvers both returned the new address. The normal
public URL connected to Hetzner with a valid HTTPS certificate and returned
200 for readiness and 401 for an anonymous library request. Public registration
is enabled, as it was on Azure. Existing credentials and session records were
preserved; an interactive password sign-in was not performed during migration.

The primary hostname is now `tracki.fyi`. Its Cloudflare A record is DNS only
(gray cloud), pointing to `204.168.136.246`. Keep it DNS only with the current
proxy configuration; enabling Cloudflare proxying requires Full (strict) TLS
and trusted Cloudflare client-IP forwarding for accurate per-client limits.
No AAAA or `www` record was configured for the new domain.

`deploy/.env` sets `TRACKER_DOMAIN=tracki.fyi`. The host-specific file
`deploy/redirects/legacy.caddy` redirects the old DuckDNS hostname to the new
hostname with HTTP 308, preserving paths and query strings. Keep the DuckDNS
record pointed at this server so old bookmarks and certificate renewals work.
Users sign in again at the new hostname because session cookies are host-only;
accounts, passwords and libraries stay in the same data volume.

If IPv6 is enabled later, use `2a01:4f9:c012:ead9::1`.

Temporary transfer archives and extracted copies were removed after validation.
Each server retains a private verification report at
`/root/trackify-migration-20261010/verification.json`. The new daily backup
was verified against the migrated account and library databases.

Azure's application and browser containers are stopped. Its Caddy container
serves maintenance mode, and its backup timer is disabled. The frozen source
volume and its verified final backup remain available for rollback. Azure
resources have not been deleted or deallocated.

Do not restart the Azure app or restore its old data after users have written
to Hetzner without reconciling those changes and the latest account-erasure
ledger. Use `deploy/prepare-restore.py` when restoring snapshots; it applies
current deletions and credentials and revokes restored sessions.

After the user verifies access, retire the
unused Azure VM, disks and public IP. Deleting those resources is a separate
step; stopping the guest alone does not remove all charges.

The optional `deploy/compose.staging.yaml` override serves an HTTP 503
maintenance page for future empty staging deployments. Do not add it to the
normal production command.

Do not change the Compose project name: a different name selects new, empty
volumes. See [AZURE.md](AZURE.md) for the frozen source and
[PRIVACY_SECURITY.md](PRIVACY_SECURITY.md) for restoration safeguards.
