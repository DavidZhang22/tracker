import { CsvDetails } from "../Components/CsvUpload";
import EntryLink from "../Components/EntryLink";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  ArrowLeftIcon,
  StarIcon,
  EyeOffIcon,
  ExternalLinkIcon,
  CheckIcon,
  SearchIcon,
} from "@heroicons/react/outline";
import { Icon, TypeIcon, IconButton } from "../Components/Icons";
import { Notice } from "../Components/Notice";
import { api, patch, post, checked } from "../api";
import { usePreferences } from "../Contexts/Preferences";
import { useLinkResults } from "../Hooks/useLinkResults";
import {
  ActionMenu,
  FilterOptions,
  openRowMenu,
  SelectionBar,
  useSelection,
  LinkDate,
  RefreshControl,
} from "../Components/RowTools";

export default function ItemPage() {
  const { preferences } = usePreferences();
  const { id } = useParams();
  const [params] = useSearchParams();
  const [item, setItem] = useState(null),
    [filter, setFilter] = useState("all"),
    [sort, setSort] = useState(preferences.link_sort),
    [direction, setDirection] = useState(preferences.link_direction),
    [search, setSearch] = useState(() =>
      (params.get("search") || "").slice(0, 300),
    ),
    [offset, setOffset] = useState(0);
  const [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false),
    [version, setVersion] = useState(0);
  const selection = useSelection(
    `${id}:${filter}:${search}:${sort}:${direction}`,
  );
  const viewRef = useRef();
  viewRef.current = `${id}:${filter}:${search}:${sort}:${direction}`;
  const reload = () => setVersion((v) => v + 1);
  useEffect(() => {
    let active = true;
    api(`/items/${id}`)
      .then((i) => {
        if (active) {
          setItem(i);
        }
      })
      .catch((e) => active && setError(e.message));
    return () => {
      active = false;
    };
  }, [id, version]);
  const {
    data,
    initialLoading,
    updating: loading,
    error: searchError,
    submitSearch,
  } = useLinkResults({
    id,
    filter,
    sort,
    direction,
    search,
    offset,
    version,
    setOffset,
  });
  const visibleIds = useMemo(
    () => data.links.map((link) => link.id),
    [data.links],
  );
  const update = async (body) => {
    setError("");
    try {
      const i = await patch(`/items/${id}`, body);
      setItem(i);
      return true;
    } catch (e) {
      setError(e.message);
      return false;
    }
  };
  const updateLink = async (lid, body) => {
    setError("");
    try {
      await patch(`/links/${lid}`, body);
      reload();
    } catch (e) {
      setError(e.message);
    }
  };
  const refresh = async (deep = false) => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const r = await post(`/items/${id}/refresh?deep=${deep}`);
      if (!r.ok) setError(r.error);
      else
        setMessage(
          r.cached
            ? `Using the recent scan from ${checked(r.checked_at)}. Sources are checked at most once every ten minutes.`
            : `${r.new_count} new links found.`,
        );
      reload();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const bulk = async (action) => {
    setBusy(true);
    try {
      await post(`/items/${id}/${action}`);
      reload();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const selectedAction = async (action, ids) => {
    setBusy(true);
    setError("");
    try {
      const range = action === "read-before" || action === "read-after";
      const grouping = action === "merge" || action === "separate";
      const r = grouping
        ? await post(`/items/${id}/link-groups`, {
            action,
            ids,
            filter,
            search: search.trim(),
            sort,
            direction,
          })
        : range
          ? await post(`/items/${id}/read-range`, {
              anchor_id: ids[0],
              side: action.slice(5),
              filter,
              search,
              sort,
              direction,
            })
          : await post("/links/bulk", { action, ids, item_id: id });
      selection.clear();
      reload();
      setMessage(
        grouping
          ? action === "merge"
            ? `${r.updated} rows merged with the row above.${r.skipped ? " The first row has no row above it." : ""}`
            : `${r.updated} links separated.`
          : action === "delete"
            ? `${r.updated} links moved to Trash. Refresh will keep them there.`
            : range
              ? `${r.updated} links marked read in the current order. The selected link was kept as it was.`
              : `${r.updated} links updated.`,
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const selectAllMatching = async (pattern, mode = "replace") => {
    const requestedView = viewRef.current;
    setBusy(true);
    setError("");
    try {
      const r = await post(`/items/${id}/link-selection`, {
        filter,
        search,
        sort,
        direction,
        ...(pattern ? { pattern } : {}),
      });
      if (viewRef.current === requestedView) selection.apply(r.ids, mode);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const openLink = (e, link) => {
    if (item.auto_read && !link.read && (e.type === "click" || e.button === 1))
      updateLink(link.id, { read: true });
  };
  const choose = (setter, value) => {
    setter(value);
    setOffset(0);
  };
  if (!item)
    return (
      <>
        <Link className="back-link" to="/">
          <Icon as={ArrowLeftIcon} />
          Library
        </Link>
        <Notice error>{error}</Notice>
        {error && (
          <button className="button" onClick={reload}>
            Retry loading
          </button>
        )}
        {!error && <p role="status">Loading item…</p>}
      </>
    );
  return (
    <>
      <Link className="back-link" to="/">
        <Icon as={ArrowLeftIcon} />
        Library
      </Link>
      <div className="page-heading detail-heading">
        <div className="detail-title">
          <TypeIcon kind={item.kind} />
          <div>
            <h1>{item.title}</h1>
            {item.source_type === "csv" ? (
              <span className="source-link">
                {item.source_name || "CSV import"}
              </span>
            ) : (
              <a
                className="source-link"
                href={item.url}
                target="_blank"
                rel="noopener noreferrer"
              >
                {new URL(item.url).hostname}
                <Icon as={ExternalLinkIcon} />
              </a>
            )}
          </div>
        </div>
        <div className="actions">
          <IconButton
            icon={StarIcon}
            label={item.favorite ? "Unfavorite item" : "Favorite item"}
            active={item.favorite}
            onClick={() => update({ favorite: !item.favorite })}
          />
          <IconButton
            icon={EyeOffIcon}
            label={item.ignored ? "Unmute item" : "Mute item"}
            active={item.ignored}
            onClick={() => update({ ignored: !item.ignored })}
          />
          {item.source_type === "csv" ? (
            !item.deleted && (
              <Link className="button primary" to={`/add?import=${id}`}>
                Upload CSV
              </Link>
            )
          ) : (
            <RefreshControl
              label="Refresh item"
              onRefresh={refresh}
              busy={busy}
              disabled={item.deleted}
              primary
            />
          )}
        </div>
      </div>
      <Notice
        error
        resetKey={`${id}:${error}:${item.error}:${item.last_attempt_at}`}
      >
        {error || searchError || item.error}
      </Notice>
      <Notice>{message}</Notice>
      {item.deleted && (
        <Notice>
          This item is in Trash.{" "}
          <button
            className="text-button"
            onClick={async () => {
              try {
                await post("/items/bulk", { ids: [id], action: "restore" });
                reload();
              } catch (e) {
                setError(e.message);
              }
            }}
          >
            Restore item
          </button>
        </Notice>
      )}
      {item.ignored && (
        <Notice>
          This item is muted. It is excluded from Refresh all and your active
          library.{" "}
          <button
            className="text-button"
            onClick={() => update({ ignored: false })}
          >
            Unmute item
          </button>
        </Notice>
      )}
      <div className="summary-strip">
        <button onClick={() => choose(setFilter, "unread")}>
          <span className="summary-number">{item.unread_count}</span>
          <span>Unread</span>
        </button>
        <button onClick={() => choose(setFilter, "new")}>
          <span className="summary-number teal">{item.new_count}</span>
          <span>New links</span>
        </button>
        <button onClick={() => choose(setFilter, "read")}>
          <span className="summary-number">{item.read_count}</span>
          <span>Read of {item.total_count - item.ignored_count}</span>
        </button>
      </div>
      <div className="detail-controls">
        <Link to={`/settings?item=${id}#item-settings`}>Item settings</Link>
        <div className="actions">
          {item.new_count > 0 && (
            <button
              className="text-button"
              onClick={() => bulk("acknowledge")}
              disabled={busy}
            >
              Clear new badges
            </button>
          )}
          <button
            className="button"
            onClick={() => bulk("read")}
            disabled={busy || !item.unread_count}
          >
            <Icon as={CheckIcon} />
            Mark all as read
          </button>
        </div>
      </div>
      <section
        className={`collection-panel ${selection.selecting ? "is-selecting" : ""}`}
      >
        <div className="tabs" aria-label="Link filters">
          {[
            ["all", "All links"],
            ["unread", "Unread"],
            ["new", "New"],
            ["favorites", "Favorites"],
            ["read", "Read"],
            ["ignored", "Muted"],
            ...(item.kind === "events" || item.upcoming_count > 0
              ? [["upcoming", "Upcoming"]]
              : []),
            ["trash", "Trash"],
          ].map(([key, label]) => (
            <button
              key={key}
              aria-pressed={filter === key}
              className={filter === key ? "selected" : ""}
              onClick={() => choose(setFilter, key)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="toolbar">
          <form
            className="search-form"
            role="search"
            aria-label="Link search"
            onSubmit={(event) => {
              event.preventDefault();
              submitSearch();
            }}
          >
            <label className="search">
              <Icon as={SearchIcon} />
              <input
                aria-label="Search links"
                type="search"
                maxLength={300}
                placeholder="Search links"
                value={search}
                onChange={(e) => choose(setSearch, e.target.value)}
              />
            </label>
            <button className="button" type="submit">
              Search
            </button>
          </form>
          <FilterOptions label="Sort">
            <select
              aria-label="Order links by"
              value={sort}
              onChange={(e) => choose(setSort, e.target.value)}
            >
              <option value="auto">Automatic order</option>
              <option value="number">Chapter / episode number</option>
              <option value="date">Content date</option>
              <option value="source">Source order</option>
              <option value="discovered">Date discovered</option>
              <option value="title">Title</option>
            </select>
            <select
              aria-label="Order direction"
              value={direction}
              onChange={(e) => choose(setDirection, e.target.value)}
            >
              <option value="desc">Newest to oldest</option>
              <option value="asc">Oldest to newest</option>
            </select>
          </FilterOptions>
        </div>
        <SelectionBar
          selection={selection}
          total={data.total}
          onSelectAll={selectAllMatching}
          rangeActions={filter !== "trash" && filter !== "ignored"}
          visible={visibleIds}
          patternSelection
          mergeActions={!item.deleted && filter !== "trash"}
          onAction={selectedAction}
          busy={busy || loading}
          trash={filter === "trash"}
          links
        />
        {initialLoading ? (
          <div className="empty" role="status">
            Loading links…
          </div>
        ) : data.links.length ? (
          data.links.map((l, index) => (
            <article
              key={l.id}
              onContextMenu={openRowMenu}
              className={`entry-row ${l.is_new && !l.read && !l.ignored ? "is-new" : ""} ${l.read ? "is-read" : ""}`}
            >
              <div className="entry-select">
                <label className="row-select">
                  <input
                    type="checkbox"
                    aria-label={`Select link: ${l.title}`}
                    checked={selection.has(l.id)}
                    onChange={(event) =>
                      selection.toggle(
                        l.id,
                        event.nativeEvent.shiftKey,
                        visibleIds,
                      )
                    }
                    disabled={busy || loading}
                  />
                </label>
              </div>
              <div className="entry-content">
                <EntryLink
                  entry={l}
                  className="entry-title"
                  onClick={(e) => openLink(e, l)}
                  onAuxClick={(e) => openLink(e, l)}
                />
                {l.members?.length > 1 && (
                  <details className="merged-links">
                    <summary>{l.members.length} links in this entry</summary>
                    <ul>
                      {l.members.map((member) => (
                        <li key={member.id}>
                          <EntryLink
                            entry={member}
                            onClick={(event) => openLink(event, l)}
                            onAuxClick={(event) => openLink(event, l)}
                          />
                          <span className="muted">
                            {member.url
                              ? new URL(member.url).hostname
                              : "No link"}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
                <div className="entry-subtitle">
                  {selection.selecting && (
                    <span>Position {offset + index + 1}</span>
                  )}
                  {l.is_new && !l.read && !l.ignored && (
                    <span className="badge">New</span>
                  )}
                  {l.number != null && <span>No. {l.number}</span>}
                  {!l.url && <span>No link</span>}
                  <button
                    className="read-status"
                    aria-label={`${l.read ? "Mark unread" : "Mark read"}: ${l.title}`}
                    title={l.read ? "Mark unread" : "Mark read"}
                    onClick={() => updateLink(l.id, { read: !l.read })}
                  >
                    {l.read ? "Read" : "Unread"}
                  </button>
                  {l.availability === "paid" && (
                    <span className="access-label">Paid access</span>
                  )}
                  {l.summary && <span>{l.summary}</span>}
                </div>
                {item.source_type === "csv" && (
                  <CsvDetails context={l.context} />
                )}
              </div>
              <div className="entry-date">
                <LinkDate entry={l} />
              </div>
              <div className="row-actions">
                <IconButton
                  icon={StarIcon}
                  active={l.favorite}
                  label={`${l.favorite ? "Unfavorite" : "Favorite"} link: ${l.title}`}
                  onClick={() => updateLink(l.id, { favorite: !l.favorite })}
                />
                <IconButton
                  icon={EyeOffIcon}
                  active={l.ignored}
                  label={`${l.ignored ? "Unmute" : "Mute"} link: ${l.title}`}
                  onClick={() => updateLink(l.id, { ignored: !l.ignored })}
                />
                <ActionMenu
                  record={l}
                  rangeActions={filter !== "trash" && filter !== "ignored"}
                  links
                  mergeActions={!item.deleted && filter !== "trash"}
                  disabled={busy || loading}
                  onAction={(action) => selectedAction(action, [l.id])}
                />
              </div>
            </article>
          ))
        ) : (
          <div className="empty">
            <h2>
              {searchError ? "Links unavailable" : "No links in this view"}
            </h2>
            <p>Change the filter, or refresh to check for content.</p>
          </div>
        )}
        <div className="pagination">
          <span>
            {data.total
              ? `${offset + 1}–${Math.min(offset + 50, data.total)} of ${data.total}`
              : "0 links"}{" "}
            · Ordered by{" "}
            {data.sort_used === "source"
              ? "source"
              : data.sort_used === "date"
                ? "content date"
                : data.sort_used === "number"
                  ? "number"
                  : data.sort_used || "source"}
          </span>
          <div className="actions">
            <button
              className="button"
              disabled={offset === 0 || loading}
              onClick={() => setOffset((v) => Math.max(0, v - 50))}
            >
              Previous
            </button>
            <button
              className="button"
              disabled={offset + 50 >= data.total || loading}
              onClick={() => setOffset((v) => v + 50)}
            >
              Next
            </button>
          </div>
        </div>
      </section>
      <details className="scan-details item-scan-summary">
        <summary>
          {item.source_type === "csv" ? "Import details" : "Scan details"}
        </summary>
        <p>
          {item.source_type === "csv"
            ? item.source_name
            : `${item.pages_scanned} pages · ${item.methods.join(", ")}`}
        </p>
        <p>
          {item.dated_count || 0} of {item.total_count} links have a source
          date.
        </p>
      </details>
    </>
  );
}
