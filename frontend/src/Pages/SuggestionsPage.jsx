import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ExternalLinkIcon, RefreshIcon } from "@heroicons/react/outline";
import { api, patch, post } from "../api";
import { Icon, TypeIcon } from "../Components/Icons";
import { Notice } from "../Components/Notice";

export default function SuggestionsPage() {
  const [suggestions, setSuggestions] = useState([]),
    [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [dismissed, setDismissed] = useState(null);
  useEffect(() => {
    let current = true;
    api("/suggestions")
      .then((r) => {
        if (current) setSuggestions(r.suggestions);
      })
      .catch((e) => {
        if (current) setError(e.message);
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, []);
  const rebuild = async () => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const r = await post("/suggestions/rebuild");
      setSuggestions(r.suggestions);
      setMessage(
        r.pages_used
          ? `Updated from ${r.pages_used} saved pages. No extra web requests.`
          : "No new saved pages. Refresh a source in your library to discover more.",
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const hide = async (record) => {
    setBusy(true);
    setError("");
    try {
      await patch(`/suggestions/${record.id}`, { dismissed: true });
      setSuggestions((current) => current.filter((s) => s.id !== record.id));
      setDismissed(record);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const undo = async () => {
    setBusy(true);
    setError("");
    try {
      await patch(`/suggestions/${dismissed.id}`, { dismissed: false });
      const r = await api("/suggestions");
      setSuggestions(r.suggestions);
      setDismissed(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <div className="page-heading">
        <h1>Suggestions</h1>
        <button className="button" disabled={busy || loading} onClick={rebuild}>
          <Icon as={RefreshIcon} className={`icon ${busy ? "spinning" : ""}`} />
          {busy ? "Updating…" : "Update suggestions"}
        </button>
      </div>
      <p className="suggestions-intro">
        Related sources found during your scans, ranked using your favorites and
        reading activity. No extra crawling.
      </p>
      <Notice error>{error}</Notice>
      <Notice>{message}</Notice>
      {dismissed && (
        <Notice resetKey={dismissed.id}>
          Suggestion dismissed.{" "}
          <button className="text-button" disabled={busy} onClick={undo}>
            Undo
          </button>
        </Notice>
      )}
      {loading ? (
        <div className="empty" role="status">
          Loading suggestions…
        </div>
      ) : suggestions.length ? (
        <div className="suggestion-list">
          {suggestions.map((s) => (
            <article className="suggestion-card" key={s.id}>
              <div className="item-identity">
                <TypeIcon kind={s.kind} />
                <div>
                  <a
                    className="item-title"
                    href={s.url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {s.title} <Icon as={ExternalLinkIcon} />
                  </a>
                  <div className="item-meta">
                    {new URL(s.url).hostname.replace(/^www\./, "")}
                  </div>
                </div>
              </div>
              {s.summary && <p className="suggestion-summary">{s.summary}</p>}
              <p className="suggestion-reason">
                {s.reason}:{" "}
                <Link to={`/items/${s.source_id}`}>{s.source_title}</Link>
              </p>
              <div className="suggestion-actions">
                <Link
                  className="button primary"
                  to={`/add?url=${encodeURIComponent(s.url)}`}
                >
                  Review &amp; add
                </Link>
                <button
                  className="button"
                  onClick={() => hide(s)}
                  disabled={busy}
                  aria-label={`Not interested in ${s.title}`}
                >
                  Not interested
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <div className="empty">
          <h2>No suggestions yet</h2>
          <p>
            Use saved pages to check sources you already scanned. Future library
            refreshes also collect related sources when available.
          </p>
          <Link className="button" to="/">
            Back to library
          </Link>
        </div>
      )}
    </>
  );
}
