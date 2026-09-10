# MediaTrackify deployment

Azure VM: `20.25.217.119`, Ubuntu 24.04, SSH user `azureuser`.
Domain: `mediatrackify.duckdns.org`; its A record must point to the Azure IP.
Application directory: `/home/azureuser/tracker`.

The stack uses Docker Compose, a private app container, and Caddy on ports 80/443.
Accounts and libraries live in the persistent `catchup_tracker-data` volume.
Registration is closed. The initial owner is `david`; set a new password on the
Account page after using the privately delivered temporary login. Password changes
revoke existing sessions. Never put credentials or database backups in Git.

The small link-classification model is enabled for generic HTML discovery. See
`backend/ml/README.md` for measured resource use, dataset, limitations and retraining.
To disable it, add `TRACKER_LINK_MODEL=off` to `deploy/.env` and recreate the app
with Compose. No separate ML service or database is needed.

## Operations

After connecting to the VM:

```bash
cd /home/azureuser/tracker
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml ps
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml logs --tail=50 app web
```

To reset a forgotten owner password privately:

```bash
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml exec app \
  /app/backend/.venv/bin/python -m app.tracker.accounts reset-password david
```

## Backups

`catchup-backup.timer` runs daily around 04:00 UTC. It keeps seven successful
snapshots under `/var/backups/mediatrackify`. Every SQLite snapshot is made through
the online backup API and checked for integrity. The website keeps running.
Each database is internally consistent; this isn't one transaction across all
account and library databases. For a final migration, briefly stop the app before
taking the final backup so there are no new registrations or library changes.

```bash
sudo systemctl start catchup-backup.service
sudo journalctl -u catchup-backup.service -n 10 --no-pager
sudo systemctl list-timers catchup-backup.timer --no-pager
```

One initial backup is downloaded to the local project's `backend/data` directory.
Daily snapshots remain on the VM; automatic off-server copying is not configured.
Copy fresh snapshots to private storage regularly and before migration. An archive
contains password hashes and personal state: keep it private.

The service file currently contains Azure-specific absolute paths. Change these
paths and the data volume location when moving to another server.

## Migration and billing

Build the application on the destination VM, stop writes on Azure, make and
transfer a final backup, restore it into the destination data volume with the
container's `tracker` user as owner, and verify login and library data before
switching DNS. Keep the source available for rollback until verification passes.
Do not overwrite a destination library that already contains newer progress.

The Azure trial ends 30 days after signup; leftover credit does not extend it.
Set a spending budget/alert in Azure and migrate before that date. No Azure budget
alerts or migration reminders were created by these scripts. After verifying the
replacement, delete the Azure resources you no longer need, including retained
disks and public IPs. Stopping the VM alone does not stop all resource charges.
