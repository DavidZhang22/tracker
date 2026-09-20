"""Hard bounds shared by discovery and durable storage."""

MAX_LINKS = 4_999  # Includes ignored and trashed records; refresh cannot bypass it.
MAX_CSV_BYTES = 4_000_000
MAX_LIBRARY_LINKS = 100_000
MAX_ITEMS = 500
ITEM_ADD_INTERVAL_SECONDS = 8
MAX_PREVIEWS = 5
MAX_PREVIEW_BYTES = 8_000_000


def bounded_scan(scan):
    entries = scan.get("entries", [])
    if len(entries) <= MAX_LINKS:
        return scan
    return scan | {
        "entries": entries[:MAX_LINKS],
        "coverage": "partial",
        "warnings": list(
            dict.fromkeys(
                [
                    *scan.get("warnings", []),
                    f"Kept {MAX_LINKS:,} links for this item. Additional links were not saved.",
                ]
            )
        ),
    }
