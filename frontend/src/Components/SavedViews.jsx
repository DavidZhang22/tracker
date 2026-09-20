import { useEffect, useRef, useState } from "react";
import { api, patch, post } from "../api";
import { sameLibraryView } from "../libraryViews";
import { Notice } from "./Notice";
import "../styles/saved-views.css";

export default function SavedViews({ current, onApply }) {
  const [open, setOpen] = useState(false);
  const [views, setViews] = useState(null);
  const [selectedId, setSelectedId] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [attempt, setAttempt] = useState(0);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    if (!open || views !== null) return;
    let active = true;
    setError("");
    api("/views")
      .then((result) => {
        if (!Array.isArray(result))
          throw new Error("Saved views could not be loaded.");
        if (active) setViews(result);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [open, views, attempt]);
  const selected = views?.find((view) => view.id === selectedId);
  const changed =
    selected &&
    (!sameLibraryView(selected, current) || name.trim() !== selected.name);
  const save = async (event, update = false) => {
    event.preventDefault();
    if (
      busy ||
      !name.trim() ||
      (update && !selected) ||
      (!update && views.length >= 12)
    )
      return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const body = {
        ...current,
        query: current.query.trim(),
        name: name.trim(),
      };
      const result = update
        ? await patch(`/views/${selected.id}`, body)
        : await post("/views", body);
      if (!alive.current) return;
      setViews((previous) =>
        update
          ? previous.map((view) => (view.id === result.id ? result : view))
          : [...previous, result],
      );
      setSelectedId(result.id);
      setName(result.name);
      setMessage(update ? "View updated." : "View saved.");
    } catch (e) {
      if (alive.current) setError(e.message);
    } finally {
      if (alive.current) setBusy(false);
    }
  };
  const remove = async () => {
    if (busy || !selected) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await api(`/views/${selected.id}`, { method: "DELETE" });
      if (!alive.current) return;
      setViews((previous) =>
        previous.filter((view) => view.id !== selected.id),
      );
      setSelectedId("");
      setName("");
      setMessage("View deleted. Your items were kept.");
    } catch (e) {
      if (alive.current) setError(e.message);
    } finally {
      if (alive.current) setBusy(false);
    }
  };
  return (
    <details
      className="saved-views"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>Saved views</summary>
      {open && (
        <div className="saved-views-panel">
          <Notice error>{error}</Notice>
          <Notice>{message}</Notice>
          {views === null ? (
            error ? (
              <button
                className="button"
                onClick={() => setAttempt((value) => value + 1)}
              >
                Retry saved views
              </button>
            ) : (
              <p role="status">Loading saved views…</p>
            )
          ) : (
            <form
              aria-label="Saved view editor"
              onSubmit={(event) => save(event, Boolean(selected))}
            >
              <label className="field">
                Saved view
                <select
                  value={selectedId}
                  disabled={busy}
                  onChange={(event) => {
                    const view = views.find(
                      (entry) => entry.id === event.target.value,
                    );
                    setSelectedId(view?.id || "");
                    setName(view?.name || "");
                    setMessage("");
                    if (view) onApply(view);
                  }}
                >
                  <option value="">Create a view</option>
                  {views.map((view) => (
                    <option key={view.id} value={view.id}>
                      {view.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                View name
                <input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  maxLength={40}
                  required
                  disabled={busy}
                  placeholder="e.g. Unread novels"
                />
              </label>
              <div className="actions">
                <button
                  type={selected ? "button" : "submit"}
                  className="button"
                  onClick={selected ? (event) => save(event) : undefined}
                  disabled={busy || !name.trim() || views.length >= 12}
                >
                  Save new
                </button>
                {selected && (
                  <>
                    <button
                      type="submit"
                      className="button"
                      disabled={busy || !name.trim() || !changed}
                    >
                      Update view
                    </button>
                    <button
                      type="button"
                      className="text-button danger-text"
                      disabled={busy}
                      onClick={remove}
                    >
                      Delete view
                    </button>
                  </>
                )}
              </div>
              <p className="hint">
                Saves this search, filters, and order.
                {views.length >= 12 ? " Limit: 12 views." : ""}
              </p>
            </form>
          )}
        </div>
      )}
    </details>
  );
}
