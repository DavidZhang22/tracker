import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Dialog } from "@headlessui/react";
import { PlusIcon, XIcon } from "@heroicons/react/outline";
import { post } from "../api";
import { Icon } from "./Icons";
import { Notice } from "./Notice";
import EntryLink from "./EntryLink";
import { ImportDetails } from "./ImportInput";
import { LinkDate } from "./RowTools";
import "../styles/item-settings.css";
import "../styles/manual-entry.css";

const emptyEntry = (key) => ({
  key,
  title: "",
  url: "",
  context: "",
  published_at: "",
  number: "",
});

export default function ManualEntryInput({
  disabled = false,
  itemId,
  onPreview,
  onBusy,
  onError,
  onInvalidate,
}) {
  const nextKey = useRef(1);
  const [entries, setEntries] = useState(() => [emptyEntry(0)]);
  const [busy, setBusy] = useState(false);
  const locked = disabled || busy;
  const change = (key, field, value) => {
    setEntries((old) =>
      old.map((entry) =>
        entry.key === key ? { ...entry, [field]: value } : entry,
      ),
    );
    onInvalidate();
  };
  const preview = async (event) => {
    event.preventDefault();
    if (locked || entries.some((entry) => !entry.title.trim())) return;
    setBusy(true);
    onBusy(true);
    onInvalidate();
    onError("");
    try {
      const result = await post("/scans/manual", {
        entries: entries.map(
          ({ title, url, context, published_at, number }) => ({
            title: title.trim(),
            url: url.trim(),
            context: context.trim(),
            published_at: published_at ? `${published_at}T00:00:00Z` : null,
            number: number === "" ? null : Number(number),
          }),
        ),
        ...(itemId ? { item_id: itemId } : {}),
      });
      onPreview(result);
    } catch (error) {
      onError(error.message);
    } finally {
      setBusy(false);
      onBusy(false);
    }
  };
  return (
    <form className="form-panel manual-entry-form" onSubmit={preview}>
      {entries.map((entry, index) => (
        <fieldset
          className="manual-entry-record"
          key={entry.key}
          disabled={locked}
        >
          <legend>Entry {index + 1}</legend>
          {entries.length > 1 && (
            <button
              type="button"
              className="text-button manual-entry-remove"
              aria-label={`Remove entry ${index + 1}`}
              onClick={() => {
                setEntries((old) => old.filter((row) => row.key !== entry.key));
                onInvalidate();
              }}
            >
              Remove
            </button>
          )}
          <label className="field">
            Title
            <input
              required
              maxLength={300}
              value={entry.title}
              aria-label={`Entry ${index + 1} title`}
              placeholder="Merge Intervals"
              onChange={(event) =>
                change(entry.key, "title", event.target.value)
              }
            />
          </label>
          <label className="field">
            Details (optional)
            <textarea
              rows={3}
              maxLength={8192}
              value={entry.context}
              aria-label={`Entry ${index + 1} details`}
              placeholder="Sort by start, then merge"
              onChange={(event) =>
                change(entry.key, "context", event.target.value)
              }
            />
          </label>
          <details className="manual-entry-options">
            <summary>Link, date, and number</summary>
            <label className="field">
              Link (optional)
              <input
                type="url"
                inputMode="url"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                maxLength={2048}
                value={entry.url}
                aria-label={`Entry ${index + 1} link`}
                placeholder="https://example.com/entry"
                onChange={(event) =>
                  change(entry.key, "url", event.target.value)
                }
              />
            </label>
            <div className="manual-entry-metadata">
              <label className="field">
                Date
                <input
                  type="date"
                  min="1000-01-01"
                  max="9999-12-31"
                  value={entry.published_at}
                  aria-label={`Entry ${index + 1} date`}
                  onChange={(event) =>
                    change(entry.key, "published_at", event.target.value)
                  }
                />
              </label>
              <label className="field">
                Number
                <input
                  type="number"
                  min="0"
                  max="1000000"
                  step="any"
                  value={entry.number}
                  aria-label={`Entry ${index + 1} number`}
                  onChange={(event) =>
                    change(entry.key, "number", event.target.value)
                  }
                />
              </label>
            </div>
          </details>
        </fieldset>
      ))}
      <div className="manual-entry-actions">
        <button
          type="button"
          className="button"
          disabled={locked || entries.length >= 100}
          onClick={() => {
            const key = nextKey.current++;
            setEntries((old) => [...old, emptyEntry(key)]);
            onInvalidate();
          }}
        >
          <Icon as={PlusIcon} />
          Another entry
        </button>
        <button
          className="button primary"
          type="submit"
          disabled={locked || entries.some((entry) => !entry.title.trim())}
        >
          {busy ? "Preparing preview…" : "Preview entries"}
        </button>
      </div>
    </form>
  );
}

export function ManualEntryDialog({ item, onClose, onSaved }) {
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [page, setPage] = useState(0);
  const locked = busy || saving;
  const close = () => {
    if (!locked) onClose();
  };
  const save = async () => {
    if (locked || !preview?.entries.length) return;
    setSaving(true);
    setError("");
    try {
      const saved = await post(`/items/${item.id}/entries`, {
        scan_id: preview.scan_id,
      });
      onSaved(saved);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };
  return (
    <Dialog open onClose={close} className="item-settings-modal">
      <div className="item-settings-backdrop" aria-hidden="true" />
      <div className="item-settings-viewport">
        <Dialog.Panel
          className="item-settings-dialog manual-entry-dialog"
          aria-busy={locked}
        >
          <header className="item-settings-header">
            <Dialog.Title as="h2">Add entries</Dialog.Title>
            <button
              type="button"
              className="icon-button"
              aria-label="Close add entries"
              onClick={close}
              disabled={locked}
            >
              <Icon as={XIcon} />
            </button>
          </header>
          <div className="item-settings-body form-panel">
            <Notice error>{error}</Notice>
            <Link
              className={`button manual-import-link${locked ? " is-disabled" : ""}`}
              aria-disabled={locked}
              tabIndex={locked ? -1 : undefined}
              onClick={(event) => {
                if (locked) event.preventDefault();
              }}
              to={`/add?append=${item.id}`}
            >
              Import file or text
            </Link>
            <ManualEntryInput
              itemId={item.id}
              disabled={saving}
              onBusy={setBusy}
              onError={setError}
              onInvalidate={() => setPreview(null)}
              onPreview={(result) => {
                setPreview(result);
                setPage(0);
              }}
            />
            {preview && (
              <section
                className="manual-entry-preview"
                aria-label="Entry preview"
              >
                <h3>
                  Review {preview.entries.length}{" "}
                  {preview.entries.length === 1 ? "entry" : "entries"}
                </h3>
                {preview.warnings?.length > 0 && (
                  <Notice>
                    <ul className="notices-list">
                      {preview.warnings.map((warning) => (
                        <li key={warning}>{warning}</li>
                      ))}
                    </ul>
                  </Notice>
                )}
                {preview.entries
                  .slice(page * 25, (page + 1) * 25)
                  .map((entry, index) => (
                    <div
                      className="entry-preview"
                      key={entry.source_id || entry.url || index}
                    >
                      <div className="entry-preview-content">
                        <EntryLink entry={entry} className="preview-title" />
                        <ImportDetails
                          context={entry.context}
                          title={entry.title}
                          defaultOpen
                        />
                      </div>
                      <LinkDate entry={entry} />
                    </div>
                  ))}
                {preview.entries.length > 25 && (
                  <div className="pagination">
                    <button
                      className="button"
                      disabled={!page}
                      onClick={() => setPage((old) => old - 1)}
                    >
                      Previous
                    </button>
                    <span>
                      {page + 1} / {Math.ceil(preview.entries.length / 25)}
                    </span>
                    <button
                      className="button"
                      disabled={(page + 1) * 25 >= preview.entries.length}
                      onClick={() => setPage((old) => old + 1)}
                    >
                      Next
                    </button>
                  </div>
                )}
              </section>
            )}
          </div>
          <footer className="item-settings-actions">
            <button className="button" onClick={close} disabled={locked}>
              Cancel
            </button>
            <button
              className="button primary"
              onClick={save}
              disabled={locked || !preview?.entries.length}
            >
              {saving ? "Adding…" : "Add to item"}
            </button>
          </footer>
        </Dialog.Panel>
      </div>
    </Dialog>
  );
}
