import { useEffect, useState } from "react";
import { Link, Navigate, useLocation, useSearchParams } from "react-router-dom";
import { ArrowLeftIcon } from "@heroicons/react/outline";
import { Icon } from "../Components/Icons";
import AccountPage from "../Auth/AccountPage";
import { Notice } from "../Components/Notice";
import { linkSortOptions, usePreferences } from "../Contexts/Preferences";
import SourceMethod from "../Components/SourceMethod";

export default function SettingsPage() {
  const { hash } = useLocation();
  const [params] = useSearchParams();
  const legacyItem = params.get("item");
  useEffect(() => {
    if (hash === "#account") {
      const section = document.getElementById(hash.slice(1));
      section?.scrollIntoView?.({ block: "start" });
      section?.focus({ preventScroll: true });
    }
  }, [hash]);
  const { preferences, savePreferences } = usePreferences();
  const [draft, setDraft] = useState(preferences);
  const [applyAuto, setApplyAuto] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState("");
  const change = (name, value) => {
    setDraft((old) => ({ ...old, [name]: value }));
    setMessage("");
  };
  const save = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await savePreferences({ ...draft, apply_auto_read: applyAuto });
      setApplyAuto(false);
      setMessage("Preferences saved.");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  if (legacyItem)
    return (
      <Navigate
        replace
        to={`/items/${encodeURIComponent(legacyItem)}?settings=1`}
      />
    );
  return (
    <>
      <Link className="back-link" to="/">
        <Icon as={ArrowLeftIcon} />
        Library
      </Link>
      <div className="page-heading">
        <h1>Settings</h1>
      </div>
      <div className="settings-layout">
        <form className="form-panel settings-card" onSubmit={save}>
          <h2>Reading &amp; library</h2>
          <p className="hint">
            Saved with your library and used across devices. Sort and filter
            controls still work for the current view.
          </p>
          <Notice error>{error}</Notice>
          <Notice>{message}</Notice>
          <fieldset disabled={busy}>
            <label className="field">
              Default link order
              <select
                value={draft.link_direction}
                onChange={(e) => change("link_direction", e.target.value)}
              >
                <option value="desc">Newest to oldest</option>
                <option value="asc">Oldest to newest</option>
              </select>
            </label>
            <label className="field">
              Order links by
              <select
                value={draft.link_sort}
                onChange={(e) => change("link_sort", e.target.value)}
              >
                {linkSortOptions.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
              <span className="hint">
                Automatic uses chapter numbers, dates, or the source sequence.
                Missing values stay last.
              </span>
            </label>
            <label className="field">
              Default library sort
              <select
                value={draft.library_sort}
                onChange={(e) => change("library_sort", e.target.value)}
              >
                <option value="recent">Recently added</option>
                <option value="unread">Most unread</option>
                <option value="title">Title A–Z</option>
              </select>
            </label>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={draft.auto_read}
                onChange={(e) => change("auto_read", e.target.checked)}
              />
              Mark links as read when opened in new items
            </label>
            <label className="checkbox settings-apply">
              <input
                type="checkbox"
                checked={applyAuto}
                onChange={(e) => setApplyAuto(e.target.checked)}
              />
              Also apply read-on-open to existing items
            </label>
            <p className="hint">
              Existing reading progress stays as it is. Items in Trash are
              excluded.
            </p>
            <label className="field">
              Default refresh
              <select
                value={draft.refresh_mode}
                onChange={(e) => change("refresh_mode", e.target.value)}
              >
                <option value="light">Lightweight</option>
                <option value="deep">Deep scan</option>
              </select>
              <span className="hint">
                Lightweight reuses learned rules and runs full detection when
                needed. API and sitemap sources use their selected method in
                either mode. All scans respect source request limits.
              </span>
            </label>
            <SourceMethod
              label="Default source method"
              value={draft.source_method || "auto"}
              onChange={(value) => change("source_method", value)}
            />
            <button className="button primary" type="submit">
              {busy ? "Saving…" : "Save preferences"}
            </button>
          </fieldset>
        </form>
        <section id="account" tabIndex={-1} className="settings-account">
          <AccountPage embedded />
        </section>
      </div>
    </>
  );
}
