import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { mediaLabel, mediaTypes } from "../media";
import useLibrarySearch from "../useLibrarySearch";
import { Link, useSearchParams } from "react-router-dom";
import {
  CollectionIcon,
  StarIcon,
  EyeOffIcon,
  PlusIcon,
  SearchIcon,
  ExternalLinkIcon,
  ArrowRightIcon,
} from "@heroicons/react/outline";
import { api, patch, post, examples, checked, refreshLibrary } from "../api";
import { usePreferences } from "../Contexts/Preferences";
import { Notice, ScanNote } from "../Components/Notice";
import { Icon, TypeIcon, IconButton } from "../Components/Icons";
import {
  ActionMenu,
  FilterOptions,
  openRowMenu,
  SelectionBar,
  useSelection,
  RefreshControl,
} from "../Components/RowTools";
export default function LibraryPage() {
  const { preferences } = usePreferences();
  const [items, setItems] = useState([]),
    [loading, setLoading] = useState(true),
    [params, setParams] = useSearchParams();
  const filter = params.get("filter") || "all";
  const trash = filter === "trash";
  const [search, setSearch] = useState(""),
    [kind, setKind] = useState("all"),
    [sort, setSort] = useState(preferences.library_sort);
  const [busy, setBusy] = useState(false),
    [refreshing, setRefreshing] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState("");
  const refreshController = useRef(null),
    trashView = useRef(trash),
    loadVersion = useRef(0);
  trashView.current = trash;
  useEffect(
    () => () => {
      refreshController.current?.abort();
      loadVersion.current++;
    },
    [],
  );
  const selection = useSelection(`${filter}:${search}:${kind}`);
  const load = useCallback(async () => {
    const version = ++loadVersion.current;
    try {
      const next = trash
        ? (await Promise.all([api("/items"), api("/items?trash=true")])).flat()
        : await api("/items");
      if (version !== loadVersion.current) return;
      setItems([...new Map(next.map((item) => [item.id, item])).values()]);
      setError("");
    } catch (e) {
      if (version === loadVersion.current) setError(e.message);
    } finally {
      if (version === loadVersion.current) setLoading(false);
    }
  }, [trash]);
  useEffect(() => {
    load();
  }, [load]);
  const update = async (id, body) => {
    try {
      await patch(`/items/${id}`, body);
      await load();
    } catch (e) {
      setError(e.message);
    }
  };
  const refresh = async (deep = false) => {
    if (refreshController.current) return;
    const controller = new AbortController();
    refreshController.current = controller;
    setRefreshing(true);
    setError("");
    setMessage("");
    try {
      await refreshLibrary(
        (r) => {
          if (controller.signal.aborted) return;
          if (r.type === "item" && !trashView.current) {
            setItems((current) =>
              current
                .map((i) => (i.id === r.item.id ? r.item : i))
                .filter((i) => !i.deleted),
            );
          }
          if (r.type === "start") setMessage(`Checking ${r.total} items…`);
          else
            setMessage(
              `${r.new_count} new ${r.new_count === 1 ? "link" : "links"}. ${r.checked} ${r.checked === 1 ? "item" : "items"} checked.${r.type !== "complete" ? " Refreshing…" : ""}${r.failed ? ` ${r.failed} checks could not finish. Saved links were kept.` : ""}${r.type === "complete" && r.remaining ? ` Refresh paused; ${r.remaining} items remain.` : ""}`,
            );
        },
        controller.signal,
        deep,
      );
    } catch (e) {
      if (e.name !== "AbortError") {
        setError(e.message);
        setMessage("");
      }
    } finally {
      refreshController.current = null;
      if (!controller.signal.aborted) setRefreshing(false);
    }
  };
  const selectedAction = async (action, ids) => {
    setBusy(true);
    setError("");
    try {
      const r = await post("/items/bulk", { action, ids });
      selection.clear();
      await load();
      setMessage(
        action === "delete"
          ? `${r.updated} items moved to Trash. Restore them from the Trash filter.`
          : `${r.updated} items updated.`,
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const openLatest = async (event, item) => {
    if (
      !item.auto_read ||
      item.latest_link.read ||
      (event.type !== "click" && event.button !== 1)
    )
      return;
    try {
      await patch(`/links/${item.latest_link.id}`, { read: true });
      await load();
    } catch (e) {
      setError(e.message);
    }
  };
  const active = useMemo(
    () => items.filter((i) => !i.ignored && !i.deleted),
    [items],
  );
  const libraryItems = useMemo(() => items.filter((i) => !i.deleted), [items]);
  const viewItems = trash ? items.filter((i) => i.deleted) : libraryItems;
  const counts = useMemo(
    () => ({
      all: libraryItems.length,
      new: active.filter((i) => i.new_count > 0).length,
      unread: active.filter((i) => i.unread_count > 0).length,
      favorites: libraryItems.filter((i) => i.favorite).length,
      ignored: libraryItems.filter((i) => i.ignored).length,
    }),
    [libraryItems, active],
  );
  const searchScores = useLibrarySearch(items, search, trash);
  const visible = useMemo(
    () =>
      items
        .filter((i) =>
          trash
            ? i.deleted
            : !i.deleted &&
              (filter === "all" ||
                filter === "favorites" ||
                (filter === "ignored" ? i.ignored : !i.ignored)),
        )
        .filter((i) =>
          filter === "new"
            ? i.new_count > 0
            : filter === "unread"
              ? i.unread_count > 0
              : filter === "favorites"
                ? i.favorite
                : true,
        )
        .filter((i) => kind === "all" || i.kind === kind)
        .filter((i) => searchScores.get(i.id) > 0)
        .sort(
          (a, b) =>
            Number(b.favorite) - Number(a.favorite) ||
            Number(a.ignored) - Number(b.ignored) ||
            (search.trim()
              ? searchScores.get(b.id) - searchScores.get(a.id)
              : 0) ||
            (sort === "title"
              ? a.title.localeCompare(b.title)
              : sort === "unread"
                ? b.unread_count - a.unread_count
                : (b.latest_discovered_at || b.created_at).localeCompare(
                    a.latest_discovered_at || a.created_at,
                  )),
        ),
    [items, trash, filter, kind, search, sort, searchScores],
  );
  const visibleIds = useMemo(() => visible.map((i) => i.id), [visible]);
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>
            {trash ? "Trash" : "Library"}{" "}
            <span className="heading-count">
              {trash
                ? items.filter((i) => i.deleted).length
                : libraryItems.length}
            </span>
          </h1>
        </div>
        <div className="actions">
          {!trash && (
            <RefreshControl
              label="Refresh"
              onRefresh={refresh}
              busy={refreshing}
              disabled={
                busy ||
                !active.some(
                  (i) => !["csv", "document"].includes(i.source_type),
                )
              }
            />
          )}
          <Link className="button primary" to="/add">
            <Icon as={PlusIcon} />
            Add item
          </Link>
        </div>
      </div>
      <Notice error>{error}</Notice>
      <Notice>{message}</Notice>
      {!trash && (
        <div className="summary-strip">
          <button onClick={() => setParams({ filter: "unread" })}>
            <span className="summary-number">
              {active.reduce((s, i) => s + i.unread_count, 0)}
            </span>
            <span>Unread</span>
            <Icon as={ArrowRightIcon} />
          </button>
          <button onClick={() => setParams({ filter: "new" })}>
            <span className="summary-number teal">
              {active.reduce((s, i) => s + i.new_count, 0)}
            </span>
            <span>New</span>
            <Icon as={ArrowRightIcon} />
          </button>
          <button onClick={() => setParams({ filter: "favorites" })}>
            <span className="summary-number">{counts.favorites}</span>
            <span>Favorites</span>
            <Icon as={StarIcon} />
          </button>
        </div>
      )}
      <div
        className={`collection-panel ${selection.selecting ? "is-selecting" : ""}`}
      >
        <div className="tabs" aria-label="Library filters">
          {[
            ["all", "All items"],
            ["new", "New content"],
            ["unread", "Unread"],
            ["favorites", "Favorites"],
            ["ignored", "Muted"],
            ["trash", "Trash"],
          ].map(([key, label]) => (
            <button
              key={key}
              className={filter === key ? "selected" : ""}
              aria-pressed={filter === key}
              onClick={() => setParams(key === "all" ? {} : { filter: key })}
            >
              {label}
              {counts[key] != null && <span>{counts[key]}</span>}
            </button>
          ))}
        </div>
        <div className="toolbar">
          <form
            className="search-form"
            role="search"
            aria-label="Library search"
            onSubmit={(event) => {
              event.preventDefault();
              setSearch((value) => value.trim());
            }}
          >
            <label className="search">
              <Icon as={SearchIcon} />
              <input
                aria-label="Search library"
                maxLength={200}
                type="search"
                placeholder="Search your library"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </label>
          </form>
          <FilterOptions>
            <select
              aria-label="Media type"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            >
              <option value="all">All media</option>
              {mediaTypes.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <select
              aria-label="Sort items"
              value={sort}
              onChange={(e) => setSort(e.target.value)}
            >
              <option value="recent">Recently added</option>
              <option value="unread">Most unread</option>
              <option value="title">Title A–Z</option>
            </select>
          </FilterOptions>
        </div>
        <SelectionBar
          selection={selection}
          visible={visibleIds}
          patternSelection
          onSelectAll={(pattern, mode = "replace") => {
            const matches = visibleIds.filter((id, index) => {
              const position = index + 1;
              return (
                !pattern ||
                (position >= pattern.first &&
                  position <= (pattern.last ?? visibleIds.length) &&
                  (position - pattern.starting) % pattern.every === 0)
              );
            });
            selection.apply(matches, mode);
          }}
          onAction={selectedAction}
          busy={busy}
          trash={trash}
        />
        {error && !items.length && !loading ? (
          <div className="empty">
            <h2>Library unavailable</h2>
            <button className="button" onClick={load}>
              Retry loading
            </button>
          </div>
        ) : loading ? (
          <div className="empty" role="status">
            Loading your library…
          </div>
        ) : visible.length ? (
          <div className="item-list">
            <div className="list-heading">
              <span>ITEM</span>
              <span>PROGRESS</span>
              <span>LAST CHECKED</span>
              <span />
            </div>
            {visible.map((i) => (
              <article
                key={i.id}
                className="item-row"
                onContextMenu={openRowMenu}
              >
                <div className="item-identity">
                  <label className="row-select">
                    <input
                      type="checkbox"
                      aria-label={`Select ${i.title}`}
                      checked={selection.ids.includes(i.id)}
                      onChange={() => selection.toggle(i.id)}
                      disabled={busy}
                    />
                  </label>
                  <TypeIcon kind={i.kind} />
                  <div>
                    <Link className="item-title" to={`/items/${i.id}`}>
                      {i.title}
                    </Link>
                    <div className="item-meta">
                      <span>
                        {["csv", "document"].includes(i.source_type)
                          ? i.source_name || "File import"
                          : new URL(i.url).hostname.replace(/^www\./, "")}
                      </span>
                      <span className="kind-label">{mediaLabel(i.kind)}</span>
                      {i.new_count > 0 && (
                        <span className="badge">{i.new_count} new</span>
                      )}
                      {i.latest_link &&
                        (i.latest_link.url ? (
                          <a
                            className="latest-link"
                            href={i.latest_link.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            aria-label={`Latest entry for ${i.title}: ${i.latest_link.title}`}
                            title={i.latest_link.title}
                            onClick={(event) => openLatest(event, i)}
                            onAuxClick={(event) => openLatest(event, i)}
                          >
                            Latest <Icon as={ExternalLinkIcon} />
                          </a>
                        ) : (
                          <Link
                            className="latest-link"
                            to={`/items/${i.id}?search=${encodeURIComponent(i.latest_link.title.slice(0, 300))}`}
                            aria-label={`Latest entry for ${i.title}: ${i.latest_link.title}`}
                            title={i.latest_link.title}
                          >
                            Latest
                          </Link>
                        ))}
                    </div>
                    <ScanNote item={i} />
                  </div>
                </div>
                <div className="progress-cell">
                  <strong>
                    {i.unread_count ? `${i.unread_count} unread` : "Caught up"}
                  </strong>
                  <span>
                    {i.read_count} of {i.total_count - i.ignored_count} read
                  </span>
                  <progress
                    value={i.read_count}
                    max={Math.max(i.total_count - i.ignored_count, 1)}
                    aria-label={`Reading progress for ${i.title}`}
                  />
                </div>
                <div className="last-checked">{checked(i.last_checked_at)}</div>
                <div className="row-actions">
                  <IconButton
                    icon={StarIcon}
                    label={`${i.favorite ? "Unfavorite" : "Favorite"} ${i.title}`}
                    active={i.favorite}
                    onClick={() => update(i.id, { favorite: !i.favorite })}
                  />
                  <IconButton
                    icon={EyeOffIcon}
                    label={`${i.ignored ? "Unmute" : "Mute"} ${i.title}`}
                    active={i.ignored}
                    onClick={() => update(i.id, { ignored: !i.ignored })}
                  />
                  <ActionMenu
                    record={i}
                    disabled={busy}
                    onAction={(action) => selectedAction(action, [i.id])}
                  />
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="empty">
            <span className="empty-icon">
              <Icon as={CollectionIcon} />
            </span>
            <h2>
              {viewItems.length
                ? "No matching items"
                : trash
                  ? "Trash is empty"
                  : "Start your collection"}
            </h2>
            <p>
              {viewItems.length
                ? "Try another filter or search."
                : trash
                  ? "Deleted items appear here."
                  : "Add a series, channel, blog, or feed to keep track of new releases."}
            </p>
            {!trash && (
              <Link className="button primary" to="/add">
                <Icon as={PlusIcon} />
                Add item
              </Link>
            )}
          </div>
        )}
        <div className="panel-footer">
          <span>{visible.length} items</span>
        </div>
      </div>
      {!trash && !items.length && !loading && !error && (
        <div className="examples">
          <h2>Try a source</h2>
          <div className="example-grid">
            {examples.map((e) => (
              <Link key={e.url} to={`/add?url=${encodeURIComponent(e.url)}`}>
                <span className="muted">{e.kind}</span>
                <strong>{e.title}</strong>
                <Icon as={ExternalLinkIcon} />
              </Link>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
