import asyncio
import json
import sqlite3
from collections import Counter
from contextlib import aclosing
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from .csv_import import parse_csv
from .keywords import terms
from .limits import MAX_ITEMS, MAX_LINKS, bounded_scan
from .media_metadata import MediaOverride, annotate
from .preferences import PreferencesPatch
from .source_methods import SourceMethod, detect_source_method
from .store import ItemAdditionCooldown
from .suggestions import collect_cached, ranked_suggestions
from .urls import DiscoveryError
from .workers import run_blocking

router = APIRouter(prefix="/api")
REFRESH_HEARTBEAT_SECONDS = 15


class SourceDetectionRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class ScanRequest(SourceDetectionRequest):
    selector: str = Field(default="", max_length=300)
    include_path: str = Field(default="", max_length=300)
    keywords: str = Field(default="", max_length=300)
    source_method: SourceMethod = "auto"
    detect_api: bool = True


class CreateRequest(BaseModel):
    kind_override: MediaOverride = ""
    scan_id: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=300)
    mark_read: bool = False
    auto_read: bool | None = None
    read_indices: list[Annotated[int, Field(strict=True, ge=0, lt=MAX_LINKS)]] = Field(
        default_factory=list, max_length=MAX_LINKS
    )


@router.get("/settings")
def settings(request: Request):
    return request.state.store.settings()


@router.patch("/settings")
def update_settings(body: PreferencesPatch, request: Request):
    return request.state.store.update_settings(
        body.model_dump(exclude_none=True, exclude={"apply_auto_read"}),
        body.apply_auto_read,
    )


class ItemPatch(BaseModel):
    description_override: str | None = Field(default=None, max_length=1200)
    kind_override: MediaOverride | None = None
    favorite: bool | None = None
    ignored: bool | None = None
    auto_read: bool | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    selector: str | None = Field(default=None, max_length=300)
    include_path: str | None = Field(default=None, max_length=300)
    keywords: str | None = Field(default=None, max_length=300)
    source_method: SourceMethod | None = None


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
    sort: Literal["auto", "date", "number", "source", "discovered", "title"] | None = (
        None
    )
    direction: Literal["asc", "desc"] | None = None


class ReadRange(LinkView):
    anchor_id: str = Field(min_length=1, max_length=64)
    side: Literal["before", "after"]


class SelectionPattern(BaseModel):
    every: int = Field(default=1, ge=1, le=MAX_LINKS)
    starting: int = Field(default=1, ge=1, le=MAX_LINKS)
    first: int = Field(default=1, ge=1, le=MAX_LINKS)
    last: int | None = Field(default=None, ge=1, le=MAX_LINKS)


class LinkSelection(LinkView):
    pattern: SelectionPattern | None = None


class LinkGrouping(LinkView):
    ids: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        min_length=1, max_length=MAX_LINKS
    )
    action: Literal["merge", "separate"]


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


@router.post("/source-method/detect")
def detect_method(body: SourceDetectionRequest):
    return detect_source_method(body.url)


@router.post("/scans")
async def scan(body: ScanRequest, request: Request):
    terms(body.keywords)
    selector = body.selector.strip()
    method = body.source_method
    if method == "auto" and body.detect_api and not selector:
        method = detect_source_method(body.url)["source_method"]
    with request.state.store.connection() as db:
        db.execute("SELECT id FROM scans LIMIT 1").fetchone()
    with request.app.state.scan_guard.operation(request.state.store.path):
        async with request.app.state.scan_semaphore:
            result = await request.app.state.discoverer.scan(
                body.url,
                selector,
                body.include_path,
                **({"keywords": body.keywords} if body.keywords else {}),
                **({"source_method": method} if method != "auto" else {}),
                deep=True,
            )
    payload = bounded_scan(result.to_dict()) | {
        "selector": selector,
        "include_path": body.include_path,
        "keywords": body.keywords,
        "source_method": method,
    }
    payload = await run_blocking(annotate, payload)
    payload = await run_blocking(request.app.state.semantic.preview, payload)
    sid = await run_blocking(request.state.store.save_scan, payload)
    return payload | {"scan_id": sid}


@router.get("/items")
def items(request: Request, trash: bool = False):
    return request.state.store.items(trash=trash)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    trash: bool = False


@router.post("/search")
async def search_library(body: SearchRequest, request: Request):
    with request.app.state.search_guard.operation(request.state.store.path):
        return await run_blocking(
            request.app.state.semantic.search,
            request.state.store,
            body.query,
            body.trash,
        )


@router.post("/scans/import")
@router.post("/scans/csv")
async def csv_preview(
    request: Request,
    filename: str = Query("links.csv", max_length=255),
    link_filter: Literal["all", "content"] = "all",
    url_column: int | None = Query(None, ge=0, le=63),
    title_column: int | None = Query(None, ge=-1, le=63),
    company_column: int | None = Query(None, ge=-1, le=63),
    date_column: int | None = Query(None, ge=-1, le=63),
    number_column: int | None = Query(None, ge=-1, le=63),
    delimiter: Literal["auto", ",", ";", "\t", "|"] = "auto",
    header: Literal["auto", "yes", "no"] = "auto",
    date_order: Literal["auto", "day_first", "month_first"] = "auto",
    keywords: str = Query("", max_length=300),
    item_id: str | None = Query(None, min_length=1, max_length=64),
):
    mime = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    general_import = request.url.path == "/api/scans/import"
    if not general_import and mime not in {
        "text/csv",
        "text/tab-separated-values",
        "text/plain",
        "application/octet-stream",
        "application/vnd.ms-excel",
    }:
        raise HTTPException(
            415, "Upload CSV text. Export Excel workbooks as CSV first."
        )
    store = request.state.store
    target = await run_blocking(store.refresh_source, item_id) if item_id else None
    if target and (
        target["source_type"] not in {"csv", "document"} or target["deleted"]
    ):
        raise HTTPException(422, "Choose an imported item outside Trash to update.")
    columns = {
        field: value
        for field, value in zip(
            ("url", "title", "company", "date", "number"),
            (url_column, title_column, company_column, date_column, number_column),
            strict=True,
        )
        if value is not None
    }
    with request.app.state.scan_guard.operation(store.path):
        options = dict(
            columns=columns,
            delimiter=delimiter,
            header=header,
            date_order=date_order,
            keywords=keywords,
        )
        data = await request.body()
        if general_import and not filename.lower().endswith((".csv", ".tsv")):
            payload = await request.app.state.importer.parse(
                data, filename, link_filter=link_filter, **options
            )
        else:
            payload = await run_blocking(parse_csv, data, filename, **options)
        if target:
            payload.update(url=target["url"], import_item_id=item_id)
        payload = await run_blocking(annotate, payload)
        payload = await run_blocking(request.app.state.semantic.preview, payload)
        sid = await run_blocking(store.save_scan, payload)
    return payload | {"scan_id": sid}


class CsvImportRequest(BaseModel):
    scan_id: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, min_length=1, max_length=300)


@router.post("/items/{iid}/import")
def import_csv(iid: str, body: CsvImportRequest, request: Request):
    request.state.store.import_csv(iid, body.scan_id, body.title)
    request.app.state.semantic.enrich(request.state.store, [iid])
    return request.state.store.item(iid)


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
        created = request.state.store.create(
            body.scan_id,
            body.title,
            body.mark_read,
            body.auto_read,
            body.read_indices,
            body.kind_override,
        )
        request.app.state.semantic.enrich(request.state.store, [created["id"]])
        return request.state.store.item(created["id"])
    except ItemAdditionCooldown as exc:
        raise HTTPException(
            429, str(exc), headers={"Retry-After": str(exc.retry_after)}
        ) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "This source is already in your library.") from exc


@router.get("/items/{iid}")
def item(iid: str, request: Request):
    request.state.store.refresh_source(iid)
    request.app.state.semantic.enrich(request.state.store, [iid])
    return request.state.store.item(iid)


@router.patch("/items/{iid}")
def update_item(iid: str, body: ItemPatch, request: Request):
    if body.keywords is not None:
        terms(body.keywords)
    store = request.state.store
    store.item(iid)
    values = body.model_dump(exclude_none=True)
    if "description_override" in body.model_fields_set:
        values["description_override"] = body.description_override
    store.update("items", iid, values)
    if {"title", "kind_override", "description_override", "keywords"} & values.keys():
        request.app.state.semantic.enrich(store, [iid])
    return store.item(iid)


@router.get("/items/{iid}/links")
def links(
    iid: str,
    request: Request,
    filter: Literal[
        "all", "new", "unread", "read", "favorites", "ignored", "trash", "upcoming"
    ] = "all",
    search: str = Query("", max_length=300),
    sort: Literal["auto", "date", "number", "source", "discovered", "title"]
    | None = None,
    direction: Literal["asc", "desc"] | None = None,
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
def link_selection(iid: str, body: LinkSelection, request: Request):
    return request.state.store.link_selection(iid, **body.model_dump())


@router.post("/items/{iid}/link-groups")
def group_links(iid: str, body: LinkGrouping, request: Request):
    return request.state.store.group_links(iid, **body.model_dump())


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


async def refresh_item(iid, app, store, skip_unavailable=False, deep=False):
    # Fixed-size lock striping avoids both overlapping merges and unbounded lock storage.
    lock = app.state.refresh_locks[hash(iid) % 64]
    async with lock:
        item = await run_blocking(store.refresh_source, iid)
        if skip_unavailable and (
            item["deleted"]
            or item["ignored"]
            or item.get("source_type") in {"csv", "document"}
        ):
            return {"id": iid, "new_count": 0, "ok": True, "skipped": True}
        if item["deleted"]:
            raise DiscoveryError("Restore this item from Trash before refreshing it.")
        if item.get("source_type") in {"csv", "document"}:
            raise DiscoveryError("Upload a file or paste text to update this item.")
        try:
            async with app.state.scan_semaphore:
                store.check_active()
                result = await app.state.discoverer.scan(
                    item["url"],
                    item["selector"],
                    item["include_path"],
                    **({"keywords": item["keywords"]} if item.get("keywords") else {}),
                    **({"deep": True} if deep else {}),
                    **(
                        {"source_method": item["source_method"]}
                        if item.get("source_method", "auto") != "auto"
                        else {}
                    ),
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
            await run_blocking(app.state.semantic.enrich, store, [iid])
            return {
                "id": iid,
                "new_count": added,
                "ok": True,
                "cached": result.cached,
                "checked_at": result.checked_at,
                "requests_made": result.requests_made,
                "analysis_mode": result.analysis_mode,
            }
        except DiscoveryError as exc:
            if (await run_blocking(store.refresh_source, iid))["deleted"]:
                return {"id": iid, "new_count": 0, "ok": True, "skipped": True}
            await run_blocking(store.failure, iid, str(exc))
            return {"id": iid, "new_count": 0, "ok": False, "error": str(exc)}


@router.post("/items/{iid}/refresh")
async def refresh_one(iid: str, request: Request, deep: bool | None = None):
    if deep is None:
        deep = (await run_blocking(request.state.store.settings))[
            "refresh_mode"
        ] == "deep"
    with request.app.state.scan_guard.operation(request.state.store.path):
        return await refresh_item(iid, request.app, request.state.store, deep=deep)


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


async def refresh_events(rows, app, store, deep=False):
    """Bounded scans, favoring hosts with fewer active items; emit committed results."""
    pending, completed, active_hosts = list(rows), [], Counter()
    queue = asyncio.Queue()

    async def worker():
        try:
            while pending:
                index = min(
                    range(len(pending)),
                    key=lambda i: active_hosts[
                        urlsplit(pending[i].get("url", "")).hostname
                    ],
                )
                row = pending.pop(index)
                host = urlsplit(row.get("url", "")).hostname
                active_hosts[host] += 1
                try:
                    result = await refresh_item(
                        row["id"], app, store, skip_unavailable=True, deep=deep
                    )
                    await queue.put((result, await run_blocking(store.item, row["id"])))
                finally:
                    active_hosts[host] -= 1
        except Exception as exc:
            await queue.put(exc)
        finally:
            await queue.put(None)

    tasks = [asyncio.create_task(worker()) for _ in range(min(4, len(rows)))]
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
async def refresh_all(request: Request, stream: bool = False, deep: bool | None = None):
    if deep is None:
        deep = (await run_blocking(request.state.store.settings))[
            "refresh_mode"
        ] == "deep"
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
                refresh_events(rows, request.app, request.state.store, deep=deep)
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
        events = refresh_events(rows, request.app, request.state.store, deep=deep)
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
