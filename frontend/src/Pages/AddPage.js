import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  RefreshIcon,
  ExternalLinkIcon,
} from "@heroicons/react/outline";
import { Icon, Notice, TypeIcon } from "../Tracker";
import { post, examples } from "../api";
import { LinkDate } from "../RowTools";
import { orderedPreview, usePreferences } from "../Preferences";
import SourceMethod from "../SourceMethod";
import useSourceMethod from "../useSourceMethod";

export default function AddPage() {
  const { preferences } = usePreferences();
  const [params] = useSearchParams(),
    navigate = useNavigate();
  const [url, setUrl] = useState(params.get("url") || ""),
    [selector, setSelector] = useState(""),
    [path, setPath] = useState(""),
    [keywords, setKeywords] = useState("");
  const [result, setResult] = useState(null),
    [busy, setBusy] = useState(false),
    [saving, setSaving] = useState(false),
    [error, setError] = useState("");
  const source = useSourceMethod(
    url,
    preferences.source_method,
    selector,
    busy || saving || Boolean(result),
  );
  const sourceMethod = source.method;
  const [title, setTitle] = useState(""),
    [readMode, setReadMode] = useState("unread"),
    [selectedRead, setSelectedRead] = useState(new Set()),
    [page, setPage] = useState(0);
  const [retryAt, setRetryAt] = useState(0),
    [remaining, setRemaining] = useState(0);
  const preview = useMemo(
    () =>
      result
        ? orderedPreview(result.entries, preferences, result.order_hint)
        : [],
    [result, preferences],
  );
  const readCount =
    readMode === "all"
      ? preview.length
      : readMode === "choose"
        ? selectedRead.size
        : 0;
  useEffect(() => {
    if (!retryAt) return;
    const update = () => {
      const seconds = Math.max(0, Math.ceil((retryAt - Date.now()) / 1000));
      setRemaining(seconds);
      if (!seconds) setRetryAt(0);
    };
    update();
    const timer = setInterval(update, 250);
    return () => clearInterval(timer);
  }, [retryAt]);
  useEffect(() => {
    setUrl(params.get("url") || "");
    setResult(null);
  }, [params]);
  const scan = async (e) => {
    e.preventDefault();
    setBusy(true);
    setResult(null);
    setError("");
    setPage(0);
    setReadMode("unread");
    setSelectedRead(new Set());
    try {
      const r = await post("/scans", {
        url,
        selector: sourceMethod === "auto" ? selector : "",
        include_path: path,
        keywords,
        ...source.request,
      });
      source.accept(r);
      setResult(r);
      setTitle(r.title);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const save = async () => {
    if (saving || remaining > 0) return;
    setSaving(true);
    setError("");
    try {
      const item = await post("/items", {
        scan_id: result.scan_id,
        title: title.trim() || result.title,
        mark_read: readMode === "all",
        read_indices:
          readMode === "choose" ? [...selectedRead].sort((a, b) => a - b) : [],
      });
      navigate(`/items/${item.id}`);
    } catch (e) {
      if (e.status === 429 && e.retryAfter) {
        setRemaining(e.retryAfter);
        setRetryAt(Date.now() + e.retryAfter * 1000);
      } else {
        setError(e.message);
      }
    } finally {
      setSaving(false);
    }
  };
  return (
    <>
      <Link className="back-link" to="/">
        <Icon as={ArrowLeftIcon} />
        Library
      </Link>
      <div className="page-heading">
        <h1>Add item</h1>
      </div>
      <Notice error>{error}</Notice>
      <div className="add-layout">
        <div>
          <form className="form-panel" onSubmit={scan}>
            <label className="field">
              Source URL
              <input
                type="url"
                inputMode="url"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                enterKeyHint="go"
                required
                placeholder="https://example.com/series"
                value={url}
                disabled={busy || saving}
                onChange={(e) => {
                  setUrl(e.target.value);
                  setResult(null);
                }}
              />
              <span className="hint">
                Use a series page, channel, blog archive, or RSS / Atom feed.
              </span>
            </label>
            <SourceMethod
              value={sourceMethod}
              disabled={busy || saving}
              onChange={(method) => {
                source.select(method);
                setResult(null);
              }}
            />
            {source.note && (
              <p className="hint" role="status">
                {source.note}
              </p>
            )}
            <label className="field">
              Keywords
              <input
                disabled={busy || saving}
                value={keywords}
                maxLength={300}
                placeholder="English, official"
                onChange={(e) => {
                  setKeywords(e.target.value);
                  setResult(null);
                }}
              />
              <span className="hint">
                Optional. Match every comma-separated keyword or phrase in a
                link’s title or nearby details.
              </span>
            </label>
            <details>
              <summary>Refine link detection</summary>
              {sourceMethod === "auto" && (
                <label className="field">
                  Link selector
                  <input
                    disabled={busy || saving}
                    value={selector}
                    placeholder="#chapters a, article h2 a"
                    onChange={(e) => {
                      setSelector(e.target.value);
                      setResult(null);
                    }}
                  />
                  <span className="hint">
                    Optional CSS selector for content links. Useful for pages
                    with several lists.
                  </span>
                </label>
              )}
              <label className="field">
                URL must contain
                <input
                  disabled={busy || saving}
                  value={path}
                  placeholder="/my-series/chapter/"
                  onChange={(e) => {
                    setPath(e.target.value);
                    setResult(null);
                  }}
                />
                <span className="hint">
                  Optional text that every content URL must include.
                </span>
              </label>
            </details>
            <button
              className="button primary"
              disabled={busy || saving}
              type="submit"
            >
              <Icon
                as={busy ? RefreshIcon : ArrowRightIcon}
                className={`icon ${busy ? "spinning" : ""}`}
              />
              {busy ? "Scanning source…" : "Scan links"}
            </button>
            {busy && (
              <p role="status" className="hint" style={{ marginTop: 15 }}>
                Checking the selected source. Large listings can take up to
                three minutes.
              </p>
            )}
          </form>
          {result && (
            <section className="form-panel scan-results">
              <div className="preview-save-actions preview-sticky">
                <span className="badge">Review &amp; save</span>
                <button
                  className="button primary"
                  onClick={save}
                  disabled={busy || saving || remaining > 0}
                  title={
                    remaining > 0
                      ? "You can add one item every 8 seconds."
                      : undefined
                  }
                >
                  {saving
                    ? "Saving…"
                    : remaining > 0
                      ? `Add in ${remaining}s`
                      : "Add to library"}
                  <Icon as={ArrowRightIcon} />
                </button>
              </div>
              <div className="preview-head">
                <div className="item-identity">
                  <TypeIcon kind={result.kind} />
                  <div>
                    <h2>{result.entries.length} links found</h2>
                    <div className="scan-meta">
                      <span>{result.pages_scanned} pages scanned</span>
                      <span>
                        {result.entries.filter((e) => e.published_at).length}{" "}
                        with dates
                      </span>
                      {result.keywords && (
                        <span>Matching: {result.keywords}</span>
                      )}
                      {result.unfiltered_count != null && (
                        <span>
                          {result.unfiltered_count} checked for keywords
                        </span>
                      )}
                      {result.cached && <span>Recent scan reused</span>}
                      {result.expected_count != null && (
                        <span>{result.expected_count} entries reported</span>
                      )}
                    </div>
                  </div>
                </div>
              </div>
              {result.warnings.length > 0 && (
                <Notice resetKey={result.scan_id}>
                  <ul className="notices-list">
                    {result.warnings.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </ul>
                </Notice>
              )}
              <label className="field">
                Item name
                <input
                  value={title}
                  maxLength={300}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </label>
              <div>
                <div className="preview-reading">
                  <label className="field">
                    Reading progress
                    <select
                      value={readMode}
                      disabled={saving}
                      onChange={(e) => {
                        const next = e.target.value;
                        if (next === "choose" && readMode === "all")
                          setSelectedRead(
                            new Set(preview.map(({ index }) => index)),
                          );
                        if (next === "unread") setSelectedRead(new Set());
                        setReadMode(next);
                      }}
                    >
                      <option value="unread">Not started</option>
                      <option value="choose">Choose read links</option>
                      <option value="all">Caught up</option>
                    </select>
                  </label>
                  <span className="hint" role="status">
                    {readCount} read · {preview.length - readCount} unread
                  </span>
                  {readMode === "choose" && (
                    <button
                      className="text-button"
                      disabled={
                        saving ||
                        preview
                          .slice(page * 25, (page + 1) * 25)
                          .every(({ index }) => selectedRead.has(index))
                      }
                      onClick={() =>
                        setSelectedRead(
                          (old) =>
                            new Set([
                              ...old,
                              ...preview
                                .slice(page * 25, (page + 1) * 25)
                                .map(({ index }) => index),
                            ]),
                        )
                      }
                    >
                      Mark this page read
                    </button>
                  )}
                  {readMode === "choose" && (
                    <button
                      className="text-button"
                      disabled={saving || !selectedRead.size}
                      onClick={() => setSelectedRead(new Set())}
                    >
                      Clear read selection
                    </button>
                  )}
                </div>
                {preview
                  .slice(page * 25, (page + 1) * 25)
                  .map(({ entry: e, index }) => (
                    <div className="entry-preview" key={e.url}>
                      {readMode === "choose" && (
                        <label className="checkbox preview-read">
                          <input
                            type="checkbox"
                            aria-label={`Read: ${e.title}`}
                            disabled={saving}
                            checked={selectedRead.has(index)}
                            onChange={(event) => {
                              const checked = event.target.checked;
                              setSelectedRead((old) => {
                                const next = new Set(old);
                                if (checked) next.add(index);
                                else next.delete(index);
                                return next;
                              });
                            }}
                          />
                          Read
                        </label>
                      )}
                      <a href={e.url} target="_blank" rel="noopener noreferrer">
                        {e.title} ↗
                      </a>
                      {e.summary && (
                        <span className="preview-context">{e.summary}</span>
                      )}
                      <LinkDate entry={e} />
                    </div>
                  ))}
              </div>
              {result.entries.length > 25 && (
                <div className="pagination">
                  <button
                    className="button"
                    disabled={page === 0}
                    onClick={() => setPage((p) => p - 1)}
                  >
                    Previous
                  </button>
                  <span>
                    {page + 1} / {Math.ceil(result.entries.length / 25)}
                  </span>
                  <button
                    className="button"
                    disabled={(page + 1) * 25 >= result.entries.length}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    Next
                  </button>
                </div>
              )}
              <p className="hint" style={{ marginTop: 15 }}>
                Read-on-open is {preferences.auto_read ? "on" : "off"} for new
                items. Change defaults in <Link to="/settings">Settings</Link>.
              </p>
            </section>
          )}
        </div>
        <aside className="help-panel">
          <h2>Example sources</h2>
          <div className="example-grid">
            {examples.map((e) => (
              <Link key={e.url} to={`/add?url=${encodeURIComponent(e.url)}`}>
                <span className="muted">{e.kind}</span>
                <strong>{e.title}</strong>
                <Icon as={ExternalLinkIcon} />
              </Link>
            ))}
          </div>
        </aside>
      </div>
    </>
  );
}
