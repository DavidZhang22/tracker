import { useEffect, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { api, patch } from "../api";
import { AccountPage } from "../Auth";
import { Notice } from "../Notice";
import { linkSortOptions, usePreferences } from "../Preferences";
import SourceMethod from "../SourceMethod";

export default function SettingsPage() {
  const { hash } = useLocation();
  useEffect(() => {
    if (["#account", "#item-settings"].includes(hash)) {
      const section = document.getElementById(hash.slice(1));
      section?.scrollIntoView?.({ block: "start" });
      section?.focus({ preventScroll: true });
    }
  }, [hash]);
  const { preferences, savePreferences } = usePreferences();
  const [draft, setDraft] = useState(preferences);
  const [applyAuto, setApplyAuto] = useState(false);
  const [autoUpdate, setAutoUpdate] = useState(null),
    [itemBusy, setItemBusy] = useState(false);
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
      if (applyAuto) setAutoUpdate({ value: draft.auto_read });
      setApplyAuto(false);
      setMessage("Preferences saved.");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <Link className="back-link" to="/">
        ← Library
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
          <fieldset disabled={busy || itemBusy}>
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
        <ItemSettings
          autoUpdate={autoUpdate}
          settingsBusy={busy}
          onBusy={setItemBusy}
        />
        <section id="account" tabIndex={-1} className="settings-account">
          <AccountPage embedded />
        </section>
      </div>
    </>
  );
}

function ItemSettings({ autoUpdate, settingsBusy, onBusy }) {
  const [params, setParams] = useSearchParams(),
    id = params.get("item") || "";
  const [items, setItems] = useState([]),
    [draft, setDraft] = useState(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState("");
  useEffect(() => {
    if (autoUpdate)
      setDraft((old) =>
        old && !old.deleted ? { ...old, auto_read: autoUpdate.value } : old,
      );
  }, [autoUpdate]);
  useEffect(() => {
    let active = true;
    api("/items")
      .then((rows) => active && setItems(rows))
      .catch((e) => active && setError(e.message));
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    let active = true;
    setDraft(null);
    setError("");
    setMessage("");
    if (id)
      api(`/items/${id}`)
        .then((item) => active && setDraft(item))
        .catch((e) => active && setError(e.message));
    return () => {
      active = false;
    };
  }, [id]);
  const change = (name, value) => {
    setDraft((old) => ({ ...old, [name]: value }));
    setMessage("");
  };
  return (
    <section
      id="item-settings"
      tabIndex={-1}
      className="form-panel settings-card"
    >
      <h2>Item settings</h2>
      <label className="field">
        Choose an item
        <select
          value={id}
          disabled={busy || settingsBusy}
          onChange={(e) =>
            setParams(e.target.value ? { item: e.target.value } : {})
          }
        >
          <option value="">Select an item</option>
          {items.map((item) => (
            <option key={item.id} value={item.id}>
              {item.title}
            </option>
          ))}
        </select>
      </label>
      <Notice error>{error}</Notice>
      <Notice>{message}</Notice>
      {draft && (
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            onBusy(true);
            setError("");
            setMessage("");
            try {
              await patch(`/items/${id}`, {
                auto_read: draft.auto_read,
                keywords: draft.keywords || "",
                selector:
                  (draft.source_method || "auto") === "auto"
                    ? draft.selector
                    : "",
                include_path: draft.include_path,
                source_method: draft.source_method || "auto",
              });
              setMessage(
                "Item settings saved. Refresh the item to apply detection changes.",
              );
            } catch (e) {
              setError(e.message);
            } finally {
              setBusy(false);
              onBusy(false);
            }
          }}
        >
          <fieldset disabled={busy || settingsBusy || draft.deleted}>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={draft.auto_read}
                onChange={(e) => change("auto_read", e.target.checked)}
              />
              Mark as read when opened for this item
            </label>
            <SourceMethod
              value={draft.source_method || "auto"}
              onChange={(value) => change("source_method", value)}
            />
            <label className="field">
              Keywords
              <input
                value={draft.keywords || ""}
                maxLength={300}
                placeholder="English, official"
                onChange={(e) => change("keywords", e.target.value)}
              />
              <span className="hint">
                Match every comma-separated keyword in a title or nearby
                details. Saved links are kept.
              </span>
            </label>
            <details>
              <summary>Advanced link detection</summary>
              {(draft.source_method || "auto") === "auto" && (
                <label className="field">
                  Link selector
                  <input
                    value={draft.selector}
                    maxLength={300}
                    placeholder="#chapters a"
                    onChange={(e) => change("selector", e.target.value)}
                  />
                </label>
              )}
              <label className="field">
                URL must contain
                <input
                  value={draft.include_path}
                  maxLength={300}
                  placeholder="/chapter/"
                  onChange={(e) => change("include_path", e.target.value)}
                />
              </label>
            </details>
            <div className="actions">
              <button className="button" type="submit">
                {busy ? "Saving…" : "Save item settings"}
              </button>
              <Link to={`/items/${id}`}>Back to item</Link>
            </div>
          </fieldset>
        </form>
      )}
    </section>
  );
}
