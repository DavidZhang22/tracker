import { useEffect, useRef, useState } from "react";
import { Dialog } from "@headlessui/react";
import { XIcon } from "@heroicons/react/outline";
import { patch } from "../api";
import { Icon } from "./Icons";
import { Notice } from "./Notice";
import MediaType from "./MediaType";
import SourceMethod from "./SourceMethod";
import "../styles/item-settings.css";

function values(item) {
  return {
    title: item.title.trim(),
    kind_override: item.kind_override || "",
    description_override: item.description_override ?? null,
    auto_read: item.auto_read,
    ...(!["csv", "document"].includes(item.source_type)
      ? {
          source_method: item.source_method || "auto",
          keywords: item.keywords || "",
          selector:
            (item.source_method || "auto") === "auto"
              ? item.selector || ""
              : "",
          include_path: item.include_path || "",
        }
      : {}),
  };
}

export default function ItemSettingsDialog({
  item,
  onClose,
  onSaved,
  focusDescription = false,
}) {
  const [draft, setDraft] = useState(item);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const titleRef = useRef(null),
    descriptionRef = useRef(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const change = (name, value) =>
    setDraft((old) => ({ ...old, [name]: value }));
  const original = values(item);
  const changes = Object.fromEntries(
    Object.entries(values(draft)).filter(
      ([key, value]) => original[key] !== value,
    ),
  );
  const close = () => {
    if (!busy) onClose();
  };
  const save = async (event) => {
    event.preventDefault();
    if (
      busy ||
      item.deleted ||
      !draft.title.trim() ||
      !Object.keys(changes).length
    )
      return;
    setBusy(true);
    setError("");
    try {
      const saved = await patch(`/items/${item.id}`, changes);
      if (mounted.current) onSaved(saved);
    } catch (e) {
      if (mounted.current) setError(e.message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  };
  return (
    <Dialog
      open
      onClose={close}
      initialFocus={focusDescription ? descriptionRef : titleRef}
      className="item-settings-modal"
    >
      <div className="item-settings-backdrop" aria-hidden="true" />
      <div className="item-settings-viewport">
        <Dialog.Panel
          as="form"
          className="item-settings-dialog"
          onSubmit={save}
          aria-busy={busy}
        >
          <header className="item-settings-header">
            <Dialog.Title as="h2">Item settings</Dialog.Title>
            <button
              type="button"
              className="icon-button"
              aria-label="Close item settings"
              onClick={close}
              disabled={busy}
            >
              <Icon as={XIcon} />
            </button>
          </header>
          {error && (
            <div className="item-settings-feedback">
              <Notice error>{error}</Notice>
            </div>
          )}
          <div className="item-settings-body form-panel settings-card">
            <fieldset disabled={busy || item.deleted}>
              <label className="field">
                Title
                <input
                  ref={titleRef}
                  value={draft.title}
                  required
                  maxLength={300}
                  onChange={(e) => change("title", e.target.value)}
                />
              </label>
              <MediaType
                value={draft.kind_override}
                detected={draft.detected_kind || draft.kind}
                onChange={(value) => change("kind_override", value)}
              />
              <label className="field">
                Description
                <textarea
                  ref={descriptionRef}
                  rows={5}
                  maxLength={1200}
                  value={
                    draft.description_override ??
                    draft.description_auto ??
                    draft.description ??
                    ""
                  }
                  onChange={(e) =>
                    change("description_override", e.target.value)
                  }
                />
              </label>
              <div className="description-source">
                <span className="hint">
                  {draft.description_override == null
                    ? "Selected from the scanned source. Updates on refresh."
                    : "Your description is kept when this item refreshes."}
                </span>
                {draft.description_override != null && (
                  <button
                    className="text-button"
                    type="button"
                    onClick={() => change("description_override", null)}
                  >
                    Use source description
                  </button>
                )}
              </div>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={draft.auto_read}
                  onChange={(e) => change("auto_read", e.target.checked)}
                />
                Mark as read when opened for this item
              </label>
              {!["csv", "document"].includes(draft.source_type) && (
                <>
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
                    <summary>Advanced content detection</summary>
                    {(draft.source_method || "auto") === "auto" && (
                      <label className="field">
                        Content selector
                        <input
                          aria-label="Content selector"
                          value={draft.selector || ""}
                          maxLength={300}
                          placeholder="#chapters li"
                          onChange={(e) => change("selector", e.target.value)}
                        />
                        <span className="hint">
                          Select links or list rows, including titles without
                          links.
                        </span>
                      </label>
                    )}
                    <label className="field">
                      URL must contain
                      <input
                        value={draft.include_path || ""}
                        maxLength={300}
                        placeholder="/chapter/"
                        onChange={(e) => change("include_path", e.target.value)}
                      />
                    </label>
                  </details>
                </>
              )}
            </fieldset>
          </div>
          <footer className="item-settings-actions">
            <button
              type="button"
              className="button"
              onClick={close}
              disabled={busy}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="button primary"
              disabled={
                busy ||
                item.deleted ||
                !draft.title.trim() ||
                !Object.keys(changes).length
              }
            >
              {busy ? "Saving…" : "Save changes"}
            </button>
          </footer>
        </Dialog.Panel>
      </div>
    </Dialog>
  );
}
