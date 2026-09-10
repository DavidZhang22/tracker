import { useEffect, useState } from "react";
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

export default function AddPage() {
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
  const [title, setTitle] = useState(""),
    [read, setRead] = useState(false),
    [auto, setAuto] = useState(true),
    [page, setPage] = useState(0);
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
    try {
      const r = await post("/scans", {
        url,
        selector,
        include_path: path,
        keywords,
      });
      setResult(r);
      setTitle(r.title);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const item = await post("/items", {
        scan_id: result.scan_id,
        title: title.trim() || result.title,
        mark_read: read,
        auto_read: auto,
      });
      navigate(`/items/${item.id}`);
    } catch (e) {
      setError(e.message);
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
                disabled={busy}
                onChange={(e) => {
                  setUrl(e.target.value);
                  setResult(null);
                }}
              />
              <span className="hint">
                Use a series page, channel, blog archive, or RSS / Atom feed.
              </span>
            </label>
            <label className="field">
              Keywords
              <input
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
              <label className="field">
                Link selector
                <input
                  value={selector}
                  placeholder="#chapters a, article h2 a"
                  onChange={(e) => {
                    setSelector(e.target.value);
                    setResult(null);
                  }}
                />
                <span className="hint">
                  Optional CSS selector for content links. Useful for pages with
                  several lists.
                </span>
              </label>
              <label className="field">
                URL must contain
                <input
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
            <button className="button primary" disabled={busy} type="submit">
              <Icon
                as={busy ? RefreshIcon : ArrowRightIcon}
                className={`icon ${busy ? "spinning" : ""}`}
              />
              {busy ? "Scanning source…" : "Scan links"}
            </button>
            {busy && (
              <p role="status" className="hint" style={{ marginTop: 15 }}>
                Checking pages and feeds. Large YouTube archives can take up to
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
                  disabled={busy || saving}
                >
                  {saving ? "Saving…" : "Add to library"}
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
                {result.entries.slice(page * 25, (page + 1) * 25).map((e) => (
                  <div className="entry-preview" key={e.url}>
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
              <div className="save-actions">
                <div>
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={read}
                      onChange={(e) => setRead(e.target.checked)}
                    />
                    Mark existing links as read
                  </label>
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={auto}
                      onChange={(e) => setAuto(e.target.checked)}
                    />
                    Mark links as read when opened
                  </label>
                </div>
              </div>
              <p className="hint" style={{ marginTop: 15 }}>
                Future discoveries get a new badge. Existing links start as your
                backlog.
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
