# Privacy and security operations

Public contact: **mediatrackify@gmail.com**. `/privacy`, `/terms`, and
`/.well-known/security.txt` are available before sign-in. Set the real controller
identity with `TRACKER_OPERATOR_NAME`, the public email with
`TRACKER_PRIVACY_EMAIL`, and the actual provider/region with
`TRACKER_HOSTING_DESCRIPTION`. Update these when migrating hosts.

These controls support GDPR obligations; code changes are not a certification of
legal compliance or a guarantee that exploitation is impossible.

## Account controls

Public signup is controlled by `TRACKER_PUBLIC_SIGNUP`. Successful registrations
are limited to three per IP and twenty across the server per hour, with a total
cap of 200 accounts. SQLite stores hashed IP addresses and timestamps for the
hourly quota, without a username or account ID; maintenance removes expired rows.
These counters survive restart and account deletion. Authentication and library
isolation remain required when registration is open.

Source-request limits also retain URL fingerprints, timestamps and aggregate
unknown-source probe counts by hashed hostname for a five-minute window. These
records contain no account ID, full URL or per-user association. They survive
cache erasure so repeated account deletion cannot reset the fetch or host
protection; expired records are removed during cache maintenance. Response
bodies, scan results, redirect aliases and cached error details are cleared.

Settings → Account offers JSON export, sign-out everywhere, and permanent account
deletion. Each requires the current password. Deletion also requires typing
`DELETE`. Export includes saved items/links, Trash, settings, suggestions, and
scan previews; it excludes password hashes, invite codes, and session secrets.
Exports are streamed with per-account/global limits and no cache storage.

Deletion commits account removal and session revocation first. It then securely
clears the account's library tables, temporary previews and preferences. A marker
rejects later requests and rolls back writes from a refresh already in progress.
The shared source cache is cleared, and results from scans begun before the
clear cannot repopulate it. Failed cleanup is retried every five minutes and on
restart; a pending response does not falsely claim erasure finished. The legacy
owner can delete their account without breaking health checks or allowing the
former library to be claimed again.

An erasure ledger retains only random account IDs, legacy-library routing and
cleanup dates/flags. It exists to prevent restoration from backups, not to
reconstruct the user. Review these records when retiring historical backups;
remove a completed record only after all copies predating that deletion are
gone, including independent/manual backups. Do not retain request emails or
identity evidence longer than justified by the request and applicable law.

## Requests arriving by email

Monitor the configured mailbox. The site does not send email or verify that this
mailbox is monitored. Respond to data-rights requests within one month, with any
lawful extension explained within that month. Verify identity proportionately;
never request a password over email. Do not disclose a library merely because
someone knows a username. Prefer the authenticated export/delete controls.

For a verified request when the person cannot sign in, run the private operator
CLI on the VM. Deletion prompts for the username again and is irreversible:

```sh
cd /home/azureuser/tracker
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml exec app \
  /app/backend/.venv/bin/python -m app.tracker.accounts delete USERNAME

# Correct a username; other library data stays intact and sessions are revoked.
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml exec app \
  /app/backend/.venv/bin/python -m app.tracker.accounts rename USERNAME --new-username CORRECTED_NAME
```

Restriction/objection requests need an operator response: suspend access or
processing as appropriate, rather than deleting data the person asked to retain.
The application has no automatic background source scans; ordinary scans require
user action. Keep a limited request log recording the decision and completion.

## Backups and restoration

The daily `catchup-backup.service` now scrubs prior managed snapshots using the
current deletion ledger and scrubs each newly created snapshot again before it
is published. It keeps at most seven snapshots and removes snapshots older than
seven days by manifest creation date. Failed jobs must be investigated:

```sh
sudo systemctl status catchup-backup.timer catchup-backup.service
sudo journalctl -u catchup-backup.service --since '2 days ago'
```

Never start an old backup directly. Keep the current live data/erasure ledger,
copy the selected backup to a separate staging directory, and run:

```sh
python3 deploy/prepare-restore.py CURRENT_LIVE_DATA STAGED_RESTORE_COPY
```

This removes deleted accounts and libraries, carries the current deletion ledger
forward, and revokes every restored session. Only then restore the staged copy
while the app is stopped. Also apply deletions to any independent snapshots,
exports or off-server backups the operator controls. A failed disk or backup job
can delay physical erasure; do not report completion without verifying cleanup.

SQLite `secure_delete`, WAL checkpointing and compaction remove database-level
residue; they cannot promise physical overwriting of cloud disks or provider
snapshots. Verify cloud retention and deletion terms with the provider.

## Security boundaries

- Passwords use Argon2id; session cookies are Secure/HttpOnly/SameSite. Writes
  require the exact browser origin. Account and library authorization stay
  server-side, including bulk actions, export, deletion and refresh.
- Request bodies, concurrent requests, account attempts, account count, scans,
  pages, response sizes and link counts are bounded. Quotas are process-local;
  keep one API process until a shared admission system is introduced.
- Source fetching pins validated DNS results and rechecks redirects. Private,
  loopback, link-local, multicast, cloud metadata and IP-tunnel destinations are
  rejected. Credentials require HTTPS and are never forwarded through redirects.
- Production disables the optional yt-dlp subprocess because it performs
  requests outside SafeFetcher. Select the official YouTube API for full
  archives; public feeds remain available without a key. Do not re-enable the
  extractor without independently enforced egress isolation.
- Models and extraction recipes are JSON/numeric data, not uploaded code or
  pickles. No API accepts a command, executable, model path or arbitrary regex.
- File imports accept up to 4 MB. Non-CSV formats run one at a time in a
  killable process with stripped environment variables, a 20-second wall timeout,
  and Linux limits of 384 MiB address space and 12 CPU seconds. Office archive
  expansion and XML entities are bounded or rejected; macros, document scripts,
  external parts and imported URLs are not executed or fetched. Original uploads
  are not written to disk; extracted metadata follows normal library retention.
- The app container has a read-only root filesystem, all Linux capabilities
  dropped, no privilege escalation, a bounded non-executable temporary mount,
  and CPU/memory/process limits. Only its data volume is persistent/writable.
  Port 8000 remains private to the reverse proxy. No Docker socket is mounted.
- Hosted request-access logs are disabled; error logs are rotated. HSTS, CSP,
  frame blocking, restrictive browser permissions and no-store API responses
  complement input validation and React's escaping.

Before accepting broader public use, verify the operator's legal identity,
provider processing agreement and EEA transfer safeguards for US hosting, the
lawful bases and retention schedule, security incident handling, and whether a
DPIA is needed for any expanded use. Keep OS/container dependencies patched and
monitor backup and privacy-maintenance failures. Review `security.txt` before
its March 2027 expiry. Investigate suspected breaches promptly; assess GDPR's
72-hour supervisory notification requirement and communication duties where
applicable. The contact mailbox alone does not implement those procedures.

## Verification

Regression tests cover account isolation, password/confirmation/CSRF checks,
export scope and throttling, revocation, deletion retries, legacy-owner erasure,
in-flight writes and caches, managed backups and guarded restores, metadata/IP
tunnels and unsafe URLs. Candidate images run `ml/verify_privacy_runtime.py`
offline against disposable data before deployment; real user accounts are never
deleted for testing.

The frontend uses Vite, Vitest and ESLint for builds, tests and linting. Node build
tools are not shipped in the production Python image; builds must use trusted
repository assets. Review both `npm audit` and `npm audit --omit=dev` when updating
frontend dependencies, along with Python and browser runtime advisories. Apply
compatible fixes and verify lint, tests and the production build before release.

References: [EDPB rights guidance](https://www.edpb.europa.eu/sme/be-compliant/respect-individuals-rights_en),
[GDPR text](https://eur-lex.europa.eu/eli/reg/2016/679/oj),
[OWASP SSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html),
[OWASP REST security](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html).
