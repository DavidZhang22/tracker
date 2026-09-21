import { ImportDetails } from "../Components/ImportInput";
import EntryLink from "../Components/EntryLink";
import ContinueLink from "../Components/ContinueLink";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Link,
  useLocation,
  useParams,
  useSearchParams,
} from "react-router-dom";
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
import Description from "../Components/Description";
import ItemSettingsDialog from "../Components/ItemSettingsDialog";
import { patch, post, checked } from "../api";
import { linkSortOptions, usePreferences } from "../Contexts/Preferences";
import { useStartupData } from "../Contexts/StartupData";
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
  const { id } = useParams();
  return <ItemDetail key={id} id={id} />;
}

function ItemDetail({ id }) {
  const loadStartupData = useStartupData();
  const { preferences } = usePreferences();
  const location = useLocation();
  const lastLanding = useRef(null);
  const [params, setParams] = useSearchParams();
  const [focusDescription, setFocusDescription] = useState(false);
  const showSettings = (open, description = false) => {
    setFocusDescription(description);
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        if (open) next.set("settings", "1");
        else next.delete("settings");
        return next;
      },
      { replace: true },
    );
  };
  const setParamsRef = useRef(setParams);
  setParamsRef.current = setParams;
  const filters = [
    "all",
    "unread",
    "new",
    "favorites",
    "read",
    "ignored",
    "upcoming",
    "trash",
  ];
  const filter = filters.includes(params.get("filter"))
    ? params.get("filter")
    : "all";
  const sort = linkSortOptions.some(([value]) => value === params.get("sort"))
    ? params.get("sort")
    : preferences.link_sort;
  const direction = ["asc", "desc"].includes(params.get("direction"))
    ? params.get("direction")
    : preferences.link_direction;
  const search = (params.get("search") || "").slice(0, 300);
  const position = Number(params.get("offset"));
  const offset =
    Number.isInteger(position) &&
    position >= 0 &&
    position <= 4950 &&
    position % 50 === 0
      ? position
      : 0;
  const setOffset = useCallback(
    (value) =>
      setParamsRef.current(
        (previous) => {
          const next = new URLSearchParams(previous);
          const raw = Number(previous.get("offset"));
          const current =
            Number.isInteger(raw) && raw >= 0 && raw <= 4950 && raw % 50 === 0
              ? raw
              : 0;
          const result = typeof value === "function" ? value(current) : value;
          if (result) next.set("offset", String(result));
          else next.delete("offset");
          return next;
        },
        { replace: true },
      ),
    [],
  );
  const [item, setItem] = useState(null);
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
    loadStartupData(`/items/${id}`)
      .then((i) => {
        if (active) {
          setItem(i);
        }
      })
      .catch((e) => active && setError(e.message));
    return () => {
      active = false;
    };
  }, [id, version, loadStartupData]);
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
  useEffect(() => {
    const landing = `${location.key}:${location.hash}`;
    if (initialLoading || loading || !item || lastLanding.current === landing)
      return;
    const target = data.links.find(
      (link) => location.hash === `#entry-${encodeURIComponent(link.id)}`,
    );
    if (!target) return;
    const row = document.getElementById(`entry-${target.id}`);
    if (!row) return;
    row.scrollIntoView?.({ block: "center" });
    lastLanding.current = landing;
  }, [data.links, initialLoading, loading, item, location.key, location.hash]);
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
            ? `Using the recent scan from ${checked(r.checked_at)}. Scans are shared across libraries for five minutes.`
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
  const choose = (key, value) => {
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        if (value) next.set(key, value);
        else next.delete(key);
        next.delete("offset");
        return next;
      },
      { replace: key === "search" },
    );
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
            {["csv", "document"].includes(item.source_type) ? (
              <span className="source-link">
                {item.source_name || "File import"}
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
          {filter !== "trash" && (
            <ContinueLink
              item={item}
              onRead={reload}
              onError={setError}
              className="button"
            />
          )}
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
          {["csv", "document"].includes(item.source_type) ? (
            !item.deleted && (
              <Link className="button primary" to={`/add?import=${id}`}>
                Update import
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
        resetKey={`${id}:${error}:${searchError}:${item.error}:${item.last_attempt_at}`}
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
        <button onClick={() => choose("filter", "unread")}>
          <span className="summary-number">{item.unread_count}</span>
          <span>Unread</span>
        </button>
        <button onClick={() => choose("filter", "new")}>
          <span className="summary-number teal">{item.new_count}</span>
          <span>New links</span>
        </button>
        <button onClick={() => choose("filter", "read")}>
          <span className="summary-number">{item.read_count}</span>
          <span>Read of {item.total_count - item.ignored_count}</span>
        </button>
      </div>
      <Description
        item={item}
        onEdit={() => showSettings(true, true)}
        disabled={busy}
      />
      <div className="detail-controls">
        <button
          className="text-button"
          onClick={() => showSettings(true)}
          disabled={busy || item.deleted}
          aria-haspopup="dialog"
        >
          Item settings
        </button>
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
              onClick={() => choose("filter", key)}
            >
              {label}
            </button>
          ))}
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
        >
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
                placeholder="Search"
                value={search}
                onChange={(e) => choose("search", e.target.value)}
              />
            </label>
          </form>
          <FilterOptions label="Sort">
            <select
              aria-label="Order links by"
              value={sort}
              onChange={(e) => choose("sort", e.target.value)}
            >
              {linkSortOptions.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <select
              aria-label="Order direction"
              value={direction}
              onChange={(e) => choose("direction", e.target.value)}
            >
              <option value="desc">Newest to oldest</option>
              <option value="asc">Oldest to newest</option>
            </select>
          </FilterOptions>
        </SelectionBar>

        {initialLoading ? (
          <div className="empty" role="status">
            Loading links…
          </div>
        ) : data.links.length ? (
          data.links.map((l, index) => (
            <article
              key={l.id}
              id={`entry-${l.id}`}
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
                </div>
                {l.summary_suppressed ? (
                  <p className="entry-summary">Automatic summary hidden.</p>
                ) : (
                  l.summary && <p className="entry-summary">{l.summary}</p>
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
              {(l.members?.length > 1 ||
                (["csv", "document"].includes(item.source_type) &&
                  l.context?.trim())) && (
                <div className="entry-details">
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
                            {member.id !== l.id &&
                              (member.summary_suppressed ? (
                                <p className="entry-summary">
                                  Automatic summary hidden.
                                </p>
                              ) : (
                                member.summary && (
                                  <p className="entry-summary">
                                    {member.summary}
                                  </p>
                                )
                              ))}
                            {member.id !== l.id &&
                              ["csv", "document"].includes(
                                item.source_type,
                              ) && (
                                <ImportDetails
                                  context={member.context}
                                  title={member.title}
                                />
                              )}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                  {["csv", "document"].includes(item.source_type) && (
                    <ImportDetails context={l.context} title={l.title} />
                  )}
                </div>
              )}
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
          {["csv", "document"].includes(item.source_type)
            ? "Import details"
            : "Scan details"}
        </summary>
        <p>
          {["csv", "document"].includes(item.source_type)
            ? item.source_name
            : `${item.pages_scanned} pages · ${item.methods.join(", ")}`}
        </p>
        <p>
          {item.dated_count || 0} of {item.total_count} links have a source
          date.
        </p>
      </details>
      {params.get("settings") === "1" && !item.deleted && (
        <ItemSettingsDialog
          key={item.id}
          item={item}
          focusDescription={focusDescription}
          onClose={() => showSettings(false)}
          onSaved={(saved) => {
            setItem(saved);
            setMessage("Item settings saved.");
            showSettings(false);
          }}
        />
      )}
    </>
  );
}
