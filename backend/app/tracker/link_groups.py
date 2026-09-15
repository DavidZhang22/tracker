"""Reversible display groups; original URLs and source identities remain stored."""

from .limits import MAX_LINKS


def install_view(db):
    aggregates = {
        "read": "min",
        "ignored": "min",
        "deleted": "max",
        "favorite": "max",
        "is_new": "max",
    }
    columns = [r["name"] for r in db.execute("PRAGMA table_info(links)")]
    fields = [
        f"{aggregates[c]}(member.{c}) AS {c}" if c in aggregates else f"root.{c}"
        for c in columns
    ]
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_link_groups ON links(item_id,coalesce(merged_into,id))"
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_link_parent ON links(merged_into)")
    definition = (
        "CREATE VIEW link_entries AS SELECT "
        + ",".join(fields)
        + ",count(member.id) AS link_count FROM links root JOIN links member "
        "ON member.item_id=root.item_id AND coalesce(member.merged_into,member.id)=+root.id "
        "WHERE root.merged_into IS NULL GROUP BY root.item_id,root.id"
    )
    # Unary + removes column affinity so SQLite can use both expression-index keys.
    existing = db.execute(
        "SELECT sql FROM sqlite_master WHERE name='link_entries' AND type='view'"
    ).fetchone()
    if not existing or existing[0] != definition:
        db.execute("DROP VIEW IF EXISTS link_entries")
        db.execute(definition)


def expand_ids(db, ids, item_id=None):
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(
        f"SELECT id,item_id,coalesce(merged_into,id) AS root FROM links WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    if len(rows) != len(set(ids)) or any(
        item_id and row["item_id"] != item_id for row in rows
    ):
        raise KeyError(
            "Some selected links no longer exist in this item. Nothing was changed."
        )
    roots = list({row["root"] for row in rows})
    return [
        r[0]
        for r in db.execute(
            f"SELECT id FROM links WHERE coalesce(merged_into,id) IN ({','.join('?' for _ in roots)})",
            roots,
        )
    ]


def members(db, root):
    return db.execute(
        "SELECT * FROM links WHERE id=? OR merged_into=? ORDER BY id!=?,merge_order,id",
        (root, root, root),
    ).fetchall()


def move_group(db, source, target, end=None):
    if source == target:
        return
    if end is None:
        end = db.execute(
            "SELECT coalesce(max(merge_order),0) FROM links WHERE merged_into=?",
            (target,),
        ).fetchone()[0]
    for index, row in enumerate(members(db, source), end + 1):
        db.execute(
            "UPDATE links SET merged_into=?,merge_order=? WHERE id=?",
            (target, index, row["id"]),
        )
        end = index
    return end


def merge_above(db, ordered, selected):
    if not set(selected).issubset(ordered):
        raise KeyError(
            "Some selected links are no longer in this view. Reload and select them again."
        )
    chosen, targets, changed, ends = set(selected), {}, 0, {}
    for index, lid in enumerate(ordered):
        target = targets.get(ordered[index - 1], ordered[index - 1]) if index else lid
        if lid in chosen and index:
            ends[target] = move_group(db, lid, target, ends.get(target))
            targets[lid] = target
            changed += 1
        else:
            targets[lid] = lid
    return {"updated": changed, "skipped": int(bool(ordered and ordered[0] in chosen))}


def separate(db, ids, item_id):
    expanded = expand_ids(db, ids, item_id)
    placeholders = ",".join("?" for _ in expanded)
    changed = db.execute(
        f"UPDATE links SET merged_into=NULL,merge_order=0 WHERE id IN ({placeholders}) AND merged_into IS NOT NULL",
        expanded,
    ).rowcount
    return {"updated": changed}


def coalesce_groups(db, rows, survivor):
    """Keep group members reachable when refresh proves two saved URLs are aliases."""
    current = [
        dict(db.execute("SELECT * FROM links WHERE id=?", (r["id"],)).fetchone())
        for r in rows
    ]
    roots = list(dict.fromkeys(r["merged_into"] or r["id"] for r in current))
    original = next(r for r in current if r["id"] == survivor)
    removed = {r["id"] for r in current if r["id"] != survivor}
    target = original["merged_into"] or survivor
    if target in removed:
        target = survivor
    all_members = {r["id"]: r for root in roots for r in members(db, root)}
    for index, row in enumerate(all_members.values()):
        db.execute(
            "UPDATE links SET merged_into=?,merge_order=? WHERE id=?",
            (None if row["id"] == target else target, index, row["id"]),
        )
    return dict(db.execute("SELECT * FROM links WHERE id=?", (survivor,)).fetchone())


def pattern_ids(ids, every=1, starting=1, first=1, last=None):
    last = len(ids) if last is None else last
    if not (
        1 <= every <= MAX_LINKS
        and 1 <= starting <= every
        and 1 <= first <= MAX_LINKS
        and 1 <= last <= MAX_LINKS
        and first <= last
    ):
        if not ids and last == 0:
            return []
        raise ValueError("Choose a valid interval, starting position and range.")
    return [
        lid
        for position, lid in enumerate(ids, 1)
        if first <= position <= last and (position - starting) % every == 0
    ]
