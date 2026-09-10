import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeftIcon,
  RefreshIcon,
  StarIcon,
  EyeOffIcon,
  ExternalLinkIcon,
  CheckIcon,
  SearchIcon,
} from "@heroicons/react/outline";
import { Icon, Notice, TypeIcon, IconButton } from "../Tracker";
import { api, patch, post, checked } from "../api";
import {
  ActionMenu,
  FilterOptions,
  openRowMenu,
  SelectionBar,
  useSelection,
  LinkDate,
} from "../RowTools";

export default function ItemPage() {
  const { id } = useParams();
  const [item, setItem] = useState(null),
    [data, setData] = useState({ links: [], total: 0 }),
    [filter, setFilter] = useState("all"),
    [sort, setSort] = useState("auto"),
    [direction, setDirection] = useState("asc"),
    [search, setSearch] = useState(""),
    [offset, setOffset] = useState(0);
  const [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(true),
    [version, setVersion] = useState(0);
  const [selector, setSelector] = useState(""),
    [path, setPath] = useState("");
  const selection = useSelection(`${id}:${filter}:${search}:${offset}`);
  const reload = () => setVersion((v) => v + 1);
  useEffect(() => {
    let active = true;
    api(`/items/${id}`)
      .then((i) => {
        if (active) {
          setItem(i);
          setSelector(i.selector);
          setPath(i.include_path);
        }
      })
      .catch((e) => active && setError(e.message));
    return () => {
      active = false;
    };
  }, [id, version]);
  useEffect(() => {
    let active = true;
    setLoading(true);
    const q = new URLSearchParams({
      filter,
      sort,
      direction,
      search,
      offset,
      limit: 50,
    });
    api(`/items/${id}/links?${q}`)
      .then((d) => {
        if (active) {
          setData(d);
          if (offset && offset >= d.total) setOffset(0);
        }
      })
      .catch((e) => active && setError(e.message))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [id, filter, sort, direction, search, offset, version]);
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
  const refresh = async () => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const r = await post(`/items/${id}/refresh`);
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
      const r = await post("/links/bulk", { action, ids, item_id: id });
      selection.clear();
      reload();
      setMessage(
        action === "delete"
          ? `${r.updated} links moved to Trash. Refresh will keep them there.`
          : `${r.updated} links updated.`,
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const openLink = (e, link) => {
    if (item.auto_read && (e.type === "click" || e.button === 1))
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
            <a
              className="source-link"
              href={item.url}
              target="_blank"
              rel="noopener noreferrer"
            >
              {new URL(item.url).hostname}
              <Icon as={ExternalLinkIcon} />
            </a>
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
            label={item.ignored ? "Restore item" : "Ignore item"}
            active={item.ignored}
            onClick={() => update({ ignored: !item.ignored })}
          />
          <button
            className="button primary"
            onClick={refresh}
            disabled={busy || item.deleted}
          >
            <Icon
              as={RefreshIcon}
              className={`icon ${busy ? "spinning" : ""}`}
            />
            {busy ? "Working…" : "Refresh item"}
          </button>
        </div>
      </div>
      <Notice
        error
        resetKey={`${id}:${error}:${item.error}:${item.last_attempt_at}`}
      >
        {error || item.error}
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
          This item is ignored. It is excluded from Refresh all and your active
          library.{" "}
          <button
            className="text-button"
            onClick={() => update({ ignored: false })}
          >
            Restore item
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
        <label className="checkbox">
          <input
            type="checkbox"
            checked={item.auto_read}
            onChange={(e) => update({ auto_read: e.target.checked })}
          />
          Mark as read when opened
        </label>
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
            ["ignored", "Ignored"],
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
          <label className="search">
            <Icon as={SearchIcon} />
            <input
              aria-label="Search links"
              placeholder="Search links"
              value={search}
              onChange={(e) => choose(setSearch, e.target.value)}
            />
          </label>
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
              <option value="asc">Oldest / first</option>
              <option value="desc">Newest / last</option>
            </select>
          </FilterOptions>
        </div>
        <SelectionBar
          selection={selection}
          visible={data.links.map((l) => l.id)}
          onAction={selectedAction}
          busy={busy || loading}
          trash={filter === "trash"}
          links
        />
        {loading ? (
          <div className="empty" role="status">
            Loading links…
          </div>
        ) : data.links.length ? (
          data.links.map((l) => (
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
                    checked={selection.ids.includes(l.id)}
                    onChange={() => selection.toggle(l.id)}
                    disabled={busy}
                  />
                </label>
              </div>
              <div className="entry-content">
                <a
                  className="entry-title"
                  href={l.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => openLink(e, l)}
                  onAuxClick={(e) => openLink(e, l)}
                >
                  {l.title} <span aria-hidden="true">↗</span>
                </a>
                <div className="entry-subtitle">
                  {l.is_new && !l.read && !l.ignored && (
                    <span className="badge">New</span>
                  )}
                  {l.number != null && <span>No. {l.number}</span>}
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
                  label={`${l.ignored ? "Restore" : "Ignore"} link: ${l.title}`}
                  onClick={() => updateLink(l.id, { ignored: !l.ignored })}
                />
                <ActionMenu
                  record={l}
                  links
                  disabled={busy}
                  onAction={(action) => selectedAction(action, [l.id])}
                />
              </div>
            </article>
          ))
        ) : (
          <div className="empty">
            <h2>No links in this view</h2>
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
        <summary>Scan details</summary>
        <p>
          {item.pages_scanned} pages · {item.methods.join(", ")}
        </p>
        <p>
          {item.dated_count || 0} of {item.total_count} links have a source
          date.
        </p>
      </details>
      <details className="scan-details">
        <summary>Link detection settings</summary>
        <form
          className="form-panel"
          onSubmit={async (e) => {
            e.preventDefault();
            setMessage("");
            if (await update({ selector, include_path: path }))
              setMessage(
                "Detection settings saved. Refresh this item to apply them.",
              );
          }}
        >
          <div className="field-row">
            <label className="field">
              Link selector
              <input
                value={selector}
                onChange={(e) => setSelector(e.target.value)}
                placeholder="#chapters a"
              />
            </label>
            <label className="field">
              URL must contain
              <input
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder="/chapter/"
              />
            </label>
          </div>
          <button className="button" type="submit">
            Save settings
          </button>
        </form>
      </details>
    </>
  );
}
