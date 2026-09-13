import asyncio
import json
import sqlite3
from collections import deque
from contextlib import aclosing
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from .keywords import terms
from .limits import MAX_ITEMS, MAX_LINKS, bounded_scan
from .store import ItemAdditionCooldown
from .suggestions import collect_cached, ranked_suggestions
from .urls import DiscoveryError
from .workers import run_blocking

router = APIRouter(prefix="/api")
REFRESH_HEARTBEAT_SECONDS = 15


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


class SuggestionFeedback(BaseModel):
    dismissed: bool


@router.get("/suggestions")
def suggestions(request: Request):
    return ranked_suggestions(request.state.store)


@router.post("/suggestions/rebuild")
async def rebuild_suggestions(request: Request):
    store = request.state.store
    cache = getattr(
        getattr(request.app.state.discoverer, "fetcher", None), "cache", None
    )
    # Reuse bounded account/global work admission, including its hourly quota.
    with request.app.state.scan_guard.operation(store.path):
        info = await asyncio.to_thread(collect_cached, store, cache)
        return await asyncio.to_thread(ranked_suggestions, store) | info


@router.patch("/suggestions/{sid}")
def suggestion_feedback(sid: str, body: SuggestionFeedback, request: Request):
    with request.state.store.connection() as db:
        result = db.execute(
            "UPDATE suggestions SET dismissed=? WHERE id=?", (body.dismissed, sid)
        )
        if not result.rowcount:
            raise KeyError("Suggestion not found.")
    return {"updated": True}


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
        async with request.app.state.scan_semaphore:
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
    sid = await run_blocking(request.state.store.save_scan, payload)
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
    except ItemAdditionCooldown as exc:
        raise HTTPException(
            429, str(exc), headers={"Retry-After": str(exc.retry_after)}
        ) from exc
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


async def refresh_item(iid, app, store, skip_unavailable=False):
    # Fixed-size lock striping avoids both overlapping merges and unbounded lock storage.
    lock = app.state.refresh_locks[hash(iid) % 64]
    async with lock:
        item = await run_blocking(store.refresh_source, iid)
        if skip_unavailable and (item["deleted"] or item["ignored"]):
            return {"id": iid, "new_count": 0, "ok": True, "skipped": True}
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
            added = await run_blocking(lambda: store.merge(iid, result.to_dict()))
            return {
                "id": iid,
                "new_count": added,
                "ok": True,
                "cached": result.cached,
                "checked_at": result.checked_at,
                "requests_made": result.requests_made,
            }
        except DiscoveryError as exc:
            if (await run_blocking(store.refresh_source, iid))["deleted"]:
                return {"id": iid, "new_count": 0, "ok": True, "skipped": True}
            await run_blocking(store.failure, iid, str(exc))
            return {"id": iid, "new_count": 0, "ok": False, "error": str(exc)}


@router.post("/items/{iid}/refresh")
async def refresh_one(iid: str, request: Request):
    with request.app.state.scan_guard.operation(request.state.store.path):
        return await refresh_item(iid, request.app, request.state.store)


def refresh_summary(results, total):
    checked = [r for r in results if not r.get("skipped")]
    return {
        "checked": len(checked),
        "failed": sum(not r["ok"] for r in checked),
        "new_count": sum(r["new_count"] for r in checked),
        "cached": sum(r.get("cached", False) for r in checked),
        "skipped": len(results) - len(checked),
        "remaining": total - len(results),
    }


async def refresh_events(rows, app, store):
    """Two bounded workers per library; emit each committed result as it finishes."""
    pending, completed = deque(rows), []
    queue = asyncio.Queue()

    async def worker():
        try:
            while pending:
                row = pending.popleft()
                result = await refresh_item(
                    row["id"], app, store, skip_unavailable=True
                )
                await queue.put((result, await run_blocking(store.item, row["id"])))
        except Exception as exc:
            await queue.put(exc)
        finally:
            await queue.put(None)

    tasks = [asyncio.create_task(worker()) for _ in range(min(2, len(rows)))]
    try:
        yield {"type": "start", "total": len(rows)}
        try:
            async with asyncio.timeout(600):
                running = len(tasks)
                while running:
                    try:
                        event = await asyncio.wait_for(
                            queue.get(), REFRESH_HEARTBEAT_SECONDS
                        )
                    except TimeoutError:
                        yield {"type": "heartbeat"}
                        continue
                    if event is None:
                        running -= 1
                    elif isinstance(event, Exception):
                        raise event
                    else:
                        result, item = event
                        completed.append(result)
                        yield {
                            "type": "item",
                            "item": item,
                            "result": result,
                            **refresh_summary(completed, len(rows)),
                        }
        except TimeoutError:
            pass
        yield {
            "type": "complete",
            "results": completed,
            **refresh_summary(completed, len(rows)),
        }
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@router.post("/refresh")
async def refresh_all(request: Request, stream: bool = False):
    rows = await run_blocking(request.state.store.refresh_sources)
    if len(rows) > MAX_ITEMS:
        raise HTTPException(
            422,
            f"Refresh all supports up to {MAX_ITEMS} active items. Refresh individual items instead.",
        )
    admission = request.app.state.scan_guard.operation(
        request.state.store.path, cost=max(1, len(rows))
    )
    if not stream:
        with admission:
            async with aclosing(
                refresh_events(rows, request.app, request.state.store)
            ) as events:
                async for event in events:
                    if event["type"] == "complete":
                        return {k: v for k, v in event.items() if k != "type"}

    # Admit before headers so overlapping scans and quota errors remain HTTP 429.
    admission.__enter__()
    released = False

    def release():
        nonlocal released
        if not released:
            released = True
            admission.__exit__(None, None, None)

    async def generate():
        events = refresh_events(rows, request.app, request.state.store)
        try:
            async for event in events:
                yield json.dumps(event) + "\n"
        except sqlite3.DatabaseError:
            yield (
                json.dumps(
                    {
                        "type": "error",
                        "detail": "Storage is temporarily unavailable. Completed updates were kept. Try again shortly.",
                    }
                )
                + "\n"
            )
        except Exception:
            yield (
                json.dumps(
                    {
                        "type": "error",
                        "detail": "Refresh stopped. Completed updates were kept. Please try again.",
                    }
                )
                + "\n"
            )
        finally:
            try:
                await events.aclose()
            finally:
                release()

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no"},
        background=BackgroundTask(release),
    )
