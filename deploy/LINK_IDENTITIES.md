# Stable links across refreshes

Asura changed the eight-character suffix on its series URLs from `53fc8424`
to `6f7fe6eb`. The old and new chapter URLs appeared together in saved items
because storage previously treated their entire URLs as distinct identities.

Discovery and storage now share normalized content identities. On Asura, the
identity retains the series slug and full chapter number, excluding only the
rotating suffix. Meaningful query parameters, different series, decimal chapters,
and other hosts remain distinct. General normalization follows the existing URL
policy for fragments, trailing slashes, query ordering, and tracking parameters.

Refresh updates the existing row with the incoming URL and metadata. The link ID,
reading progress, favorites, ignored status, and Trash status stay in place.
Publication dates retain the existing evidence-quality safeguards. The per-item
database uniqueness constraint also applies to concurrent refreshes.

Database version 8 repairs existing duplicates in a transaction on library open.
It keeps the original row ID and discovery time, uses the newest discovered URL,
and retains the best date evidence. When duplicate states differ, read, favorite,
ignored, and Trash flags are retained if set on either copy. Duplicate discoveries
do not make an old chapter new. Other saved items and account preferences are
unchanged. A failed migration rolls back both the cleanup and version change.

The discovery cache version changes so saved model parses and extraction recipes
are rebuilt with the same identity rules. HTTP cache reuse, request pacing, and
scraping limits remain in effect; repair does not fetch content pages.

Validation: 332 backend tests, including rotating URLs through lightweight and
deep refresh, concurrent commits, duplicate preview entries, exact URL duplicates
with legacy keys, meaningful distinct URLs, state retention, migration rollback,
and migration idempotence.

Run `python ml/verify_identity_runtime.py` from `backend` for an isolated runtime
check. Pass `--database /path/to/tracker.sqlite3` to validate a real library using
SQLite backup into a temporary copy. The source is opened read-only; the verifier
does not modify the original library or fetch any source pages.
