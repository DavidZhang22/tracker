# Reading preferences and initial progress

Settings are stored in each account's existing SQLite library, so they follow
the account across browsers and devices. There is no new database service or
manual setup. The additive migration creates a one-row `preferences` table;
existing items, read flags, favorites, ignored links, and Trash are preserved.

Defaults are newest-to-oldest links, automatic link ordering, recently added
items, read-on-open enabled for new items, and lightweight refresh. Changing the
read-on-open default affects existing items only when the user selects the
explicit apply option; Trash is excluded and reading progress is untouched.
Individual item overrides and detection filters are editable under Item settings.
Account/password controls are also reachable from Settings. `/account` remains
available for existing bookmarks.

The add preview offers Not started, Choose read links, and Caught up. This borrows
the useful reading-status pattern from
[Goodreads' reading shelves](https://www.goodreads.com/blog/show/666-read-with-kindle-on-your-iphone-or-ipad-now-it-s-even-easier-to-share-w),
while using link-level progress for serial media. Selections persist across
preview pagination and failed saves. Mark this page read and Clear read selection
are shown only during manual selection. Rescanning clears selections because the
new preview may contain different content.

Selected entries are sent as bounded integer indices into the saved, account-owned
preview. The server validates them and saves initial progress in the same SQLite
transaction as item creation. Invalid selections and database failures preserve
the preview and do not consume the eight-second addition cooldown. Refresh still
preserves existing read flags. Explicit light/deep overrides take precedence over
the saved refresh default, without altering request pacing or crawl limits.

Validation: 314 backend tests, 69 frontend tests, production frontend build,
`backend/ml/verify_preferences_runtime.py` migration/storage checks, and browser
checks at desktop and 320/390-pixel mobile widths. Browser verification selected
seven links across two preview pages and confirmed the saved read count and
newest-first item ordering. All source responses in these checks were offline
fixtures; no extra scraping was needed.
