# Optional hostname redirects

Caddy imports `*.caddy` files from this directory. An empty directory is valid.
These local files are ignored by Git and mounted read-only in the proxy.

After the new primary domain has a working HTTPS certificate, set
`TRACKER_DOMAIN` in `deploy/.env` to that domain and add a file such as
`legacy.caddy`:

```caddyfile
old.example.com {
    redir https://{$TRACKER_DOMAIN}{uri} 308
}
```

Keep the old hostname's DNS pointed at the server so its certificate can renew.
Do not include the primary domain in a redirect block. Only the primary
hostname serves the application, preserving exact-origin checks and host-only
session cookies. Users must sign in again on the new domain.

Validate the Caddyfile and recreate the app and web services after changing
the primary domain. Directory-mounted redirect edits can be applied with
`caddy reload` after validation.
