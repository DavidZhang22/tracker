import asyncio
import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .keywords import terms
from .limits import MAX_ITEMS, MAX_LINKS, bounded_scan
from .urls import DiscoveryError

router = APIRouter(prefix="/api")


class ScanRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    selector: str = Field(default="", max_length=300)
    include_path: str = Field(default="", max_length=300)
    keywords: str = Field(default="", max_length=300)


class CreateRequest(BaseModel):
    scan_id: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=300)
    mark_read: bool = False
    auto_read: bool = True


class ItemPatch(BaseModel):
    favorite: bool | None = None
    ignored: bool | None = None
    auto_read: bool | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    selector: str | None = Field(default=None, max_length=300)
    include_path: str | None = Field(default=None, max_length=300)
    keywords: str | None = Field(default=None, max_length=300)


class LinkPatch(BaseModel):
    favorite: bool | None = None
    ignored: bool | None = None
    read: bool | None = None


class BulkRequest(BaseModel):
    ids: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        min_length=1, max_length=MAX_LINKS
    )
    action: Literal[
        "favorite",
        "unfavorite",
        "ignore",
        "unignore",
        "delete",
        "restore",
        "read",
        "unread",
    ]
    item_id: str | None = None


class LinkView(BaseModel):
    filter: Literal[
        "all", "new", "unread", "read", "favorites", "ignored", "trash", "upcoming"
    ] = "all"
    search: str = Field(default="", max_length=300)
    sort: Literal["auto", "date", "number", "source", "discovered", "title"] = "auto"
    direction: Literal["asc", "desc"] = "asc"


class ReadRange(LinkView):
    anchor_id: str = Field(min_length=1, max_length=64)
    side: Literal["before", "after"]


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request):
    with request.app.state.store.connection() as db:
        db.execute("SELECT id FROM items LIMIT 1").fetchone()
    if request.app.state.accounts:
        with request.app.state.accounts.connection() as db:
            db.execute("SELECT id FROM users LIMIT 1").fetchone()
    return {"status": "ready"}


@router.post("/scans")
async def scan(body: ScanRequest, request: Request):
    terms(body.keywords)
    with request.state.store.connection() as db:
        db.execute("SELECT id FROM scans LIMIT 1").fetchone()
    with request.app.state.scan_guard.operation(request.state.store.path):
        result = await request.app.state.discoverer.scan(
            body.url,
            body.selector,
            body.include_path,
            **({"keywords": body.keywords} if body.keywords else {}),
        )
    payload = bounded_scan(result.to_dict()) | {
        "selector": body.selector,
        "include_path": body.include_path,
        "keywords": body.keywords,
    }
    sid = request.state.store.save_scan(payload)
    return payload | {"scan_id": sid}


@router.get("/items")
def items(request: Request, trash: bool = False):
    return request.state.store.items(trash=trash)


@router.post("/items/bulk")
def item_bulk(body: BulkRequest, request: Request):
    return request.state.store.bulk_selected("items", body.ids, body.action)


@router.post("/links/bulk")
def link_bulk(body: BulkRequest, request: Request):
    return request.state.store.bulk_selected(
        "links", body.ids, body.action, body.item_id
    )


@router.post("/items", status_code=201)
def create(body: CreateRequest, request: Request):
    try:
        return request.state.store.create(
            body.scan_id, body.title, body.mark_read, body.auto_read
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "This source is already in your library.") from exc


@router.get("/items/{iid}")
def item(iid: str, request: Request):
    return request.state.store.item(iid)


@router.patch("/items/{iid}")
def update_item(iid: str, body: ItemPatch, request: Request):
    if body.keywords is not None:
        terms(body.keywords)
    store = request.state.store
    store.item(iid)
    store.update("items", iid, body.model_dump(exclude_none=True))
    return store.item(iid)


@router.get("/items/{iid}/links")
def links(
    iid: str,
    request: Request,
    filter: Literal[
        "all", "new", "unread", "read", "favorites", "ignored", "trash", "upcoming"
    ] = "all",
    search: str = Query("", max_length=300),
    sort: Literal["auto", "date", "number", "source", "discovered", "title"] = "auto",
    direction: Literal["asc", "desc"] = "asc",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return request.state.store.links(
        iid, filter, search, sort, direction, offset, limit
    )


@router.patch("/links/{lid}")
def update_link(lid: str, body: LinkPatch, request: Request):
    request.state.store.update("links", lid, body.model_dump(exclude_none=True))
    return {"ok": True}


@router.post("/items/{iid}/link-selection")
def link_selection(iid: str, body: LinkView, request: Request):
    return request.state.store.link_selection(iid, **body.model_dump())


@router.post("/items/{iid}/read-range")
def read_range(iid: str, body: ReadRange, request: Request):
    return request.state.store.read_range(iid, **body.model_dump())


@router.post("/items/{iid}/read")
def read_all(iid: str, request: Request):
    request.state.store.bulk(iid, "read")
    return request.state.store.item(iid)


@router.post("/items/{iid}/acknowledge")
def acknowledge(iid: str, request: Request):
    request.state.store.bulk(iid, "acknowledge")
    return request.state.store.item(iid)


async def refresh_item(iid, app, store):
    # Fixed-size lock striping avoids both overlapping merges and unbounded lock storage.
    lock = app.state.refresh_locks[hash(iid) % 64]
    async with lock:
        item = store.item(iid)
        if item["deleted"]:
            raise DiscoveryError("Restore this item from Trash before refreshing it.")
        try:
            async with app.state.scan_semaphore:
                result = await app.state.discoverer.scan(
                    item["url"],
                    item["selector"],
                    item["include_path"],
                    **({"keywords": item["keywords"]} if item.get("keywords") else {}),
                )
            if not result.entries and not (
                result.unfiltered_count
                and result.keywords
                or result.coverage == "complete"
                and result.expected_count == 0
            ):
                raise DiscoveryError(
                    "No content found during refresh. Saved links and progress were kept. "
                    + " ".join(result.warnings)
                )
            added = store.merge(iid, result.to_dict())
            return {
                "id": iid,
                "new_count": added,
                "ok": True,
                "cached": result.cached,
                "checked_at": result.checked_at,
                "requests_made": result.requests_made,
            }
        except DiscoveryError as exc:
            store.failure(iid, str(exc))
            return {"id": iid, "new_count": 0, "ok": False, "error": str(exc)}


@router.post("/items/{iid}/refresh")
async def refresh_one(iid: str, request: Request):
    with request.app.state.scan_guard.operation(request.state.store.path):
        return await refresh_item(iid, request.app, request.state.store)


@router.post("/refresh")
async def refresh_all(request: Request):
    rows = [i for i in request.state.store.items() if not i["ignored"]]
    if len(rows) > MAX_ITEMS:
        raise HTTPException(
            422,
            f"Refresh all supports up to {MAX_ITEMS} active items. Refresh individual items instead.",
        )
    results = []
    with request.app.state.scan_guard.operation(
        request.state.store.path, cost=max(1, len(rows))
    ):
        # Sequential within each account; other accounts retain their scan slots.
        try:
            async with asyncio.timeout(600):
                for row in rows:
                    results.append(
                        await refresh_item(row["id"], request.app, request.state.store)
                    )
        except TimeoutError:
            pass  # Completed merges remain saved; report the unprocessed remainder.
    return {
        "checked": len(results),
        "failed": sum(not r["ok"] for r in results),
        "new_count": sum(r["new_count"] for r in results),
        "cached": sum(r.get("cached", False) for r in results),
        "results": results,
        "remaining": len(rows) - len(results),
    }
