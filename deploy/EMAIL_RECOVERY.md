# Recovery email

Email delivery is optional and disabled until SMTP is configured. Accounts and password sign-in continue to work without it. The email field is only for recovery: verification, recovery links, and password-reset notifications. Trackify does not send marketing or release notifications.

## Enable delivery

Choose an SMTP provider and verify a sender address with it. Add these values to the VM's existing `deploy/.env`; do not commit that file or paste its contents into logs or chat:

```dotenv
TRACKER_SMTP_HOST=smtp.provider.example
TRACKER_SMTP_PORT=587
TRACKER_SMTP_TLS=starttls
TRACKER_SMTP_FROM=recovery@your-domain.example
TRACKER_SMTP_USERNAME=provider-issued-username
TRACKER_SMTP_PASSWORD=provider-issued-secret
```

Use `ssl` and port `465` if your provider requires implicit TLS. Plaintext SMTP is not supported. The sender must be a plain email address, not a display-name format. Use an app password or restricted sending credential, not your ordinary mailbox password. Configure SPF/DKIM and any other sender verification required by the provider. Confirm that outbound traffic to the provider's TLS port is permitted.

The app uses `TRACKER_ORIGIN` (set from `TRACKER_DOMAIN` by Compose) to construct links. It never trusts the request's Host header for recovery links. Keep that origin equal to the site's HTTPS address.

Recreate only the app after updating settings:

```sh
sudo docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --no-build --wait app
```

Confirm `/api/auth/status` reports `recovery_available: true`. This means settings are present, not that the provider has accepted a message. Complete a verification and recovery with your own test account to confirm delivery and inspect the inbox/spam folder. Never print tokens or credentials in diagnostic output. Delivery failures log a generic message without recipient, link, or provider exception text.

## Account behavior

- Account settings require the current password before adding, changing, or removing an email. The user must follow a verification link and press Continue before the address can recover an account. The previous address remains active until a replacement is verified.
- Each verified address belongs to one account. Requests do not reveal whether an address belongs to an account. Address matching is case-insensitive; delivery preserves the verified address exactly, including local-part case. Currently ASCII email addresses are supported.
- Recovery links expire after 30 minutes. Opening a link does not consume it. Pressing Continue exchanges it once for an HttpOnly, Secure, SameSite=Strict cookie lasting 15 minutes. That cookie permits password replacement only; it cannot read a library or manage the account.
- After password replacement, all sign-in sessions, recovery links, and recovery grants for that account are revoked. The user signs in with the new password. A notification is sent to the verified address.
- Regular password changes, email removal, and signing out all sessions also revoke outstanding recovery links and grants. Account deletion cascades to email and recovery records. Staged restoration revokes restored grants and links as well as normal sessions. It preserves current live password hashes and verified addresses for surviving accounts, so restoring a snapshot cannot undo a password reset or reactivate an old removed address. Stop writes while preparing and installing a restore, as described in the backup procedure.

## Operational bounds

Only SHA-256 hashes of 256-bit random tokens are stored. Email tokens appear in URL fragments, which are not sent in HTTP requests. Recovery pages use `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.

Mail requests share persistent hourly limits of 3 per email, 12 per client IP, and 60 across the server. Verification/exchange and password-reset operations have their own bounded counters. The account system also limits password confirmation attempts. Counters expire after one hour; expired tokens and grants are pruned during maintenance. At most two SMTP sends run concurrently, with 10-second socket timeouts. Excess concurrent mail work is dropped, and any undelivered link is revoked. Users can retry after checking the email and rate limits.

Email is sent after a generic HTTP response, outside the event loop. Delivery jobs are not durable; a process restart or provider failure may require the user to request another link. The system never falls back to displaying tokens in API responses or logs. If SMTP is unconfigured, adding an email is unavailable and recovery requests remain generic without claiming that a message was sent.

Recovery requirements follow the [OWASP Forgot Password Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html). The automated test suite uses an in-memory mail sink and sends no real email.
